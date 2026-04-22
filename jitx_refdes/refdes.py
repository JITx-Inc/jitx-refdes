import csv
import io
import json
import logging
import math
import re
import shutil
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

_NATURAL_SORT_RE = re.compile(r"(\d+)")
_REFDES_RE = re.compile(r"^([A-Za-z]+)(\d+)$")

# JITX package code identifying a ViaStructure. ViaStructures use the
# 'U' prefix but are renumbered as a separate subgroup that follows the
# regular 'U' ICs on each side.
TOPOLP_PACKAGE = "TOPOLP"
VIA_STRUCTURE_PREFIX = "U"

SIDES = ("Top", "Bottom")

# Maps starting corner to (y_descending, x_descending). At top-left,
# Y sorts large-to-small and X sorts small-to-large, so the component
# nearest the top-left corner gets the lowest number.
CORNER_DIRS = {
    "top-left": (True, False),
    "top-right": (True, True),
    "bottom-left": (False, False),
    "bottom-right": (False, True),
}

START_CORNERS = tuple(CORNER_DIRS.keys())
PRIMARY_AXES = ("y", "x")


def _natural_sort_key(value: str) -> list[str | int]:
    """Split a string into text and integer parts for natural sorting."""
    parts: list[str | int] = []
    for piece in _NATURAL_SORT_RE.split(value):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            parts.append(piece)
    return parts


def _safe_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _sanitize_csv_field(value: str) -> str:
    """Prevent CSV injection by escaping leading formula characters."""
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _normalize_side(side: str) -> str:
    lower = side.strip().lower()
    if lower == "top":
        return "Top"
    elif lower == "bottom":
        return "Bottom"
    else:
        log.warning("Unrecognized board side '%s', defaulting to 'Top'", side)
        return "Top"


def _split_refdes(designator: str) -> tuple[str, int] | None:
    match = _REFDES_RE.match(designator)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def _strip_package(package: str) -> str:
    return package.split("$")[0] if package else ""


def _parse_board_extent(root: ET.Element) -> tuple[float, float, float, float] | None:
    """Return (min_x, min_y, max_x, max_y) from BOARD-BOUNDARY elements.

    Handles LINE polyline points exactly and ARC bounds by evaluating
    endpoints plus any cardinal tangent angles (0°, 90°, 180°, 270°)
    that fall within the CCW sweep. Returns None if no boundary
    geometry is present.
    """
    xs: list[float] = []
    ys: list[float] = []

    def _in_ccw_sweep(deg: float, start: float, end: float) -> bool:
        s = start % 360
        e = end % 360
        d = deg % 360
        if math.isclose(s, e):
            return True
        return s <= d <= e if s < e else (d >= s or d <= e)

    for boundary in root.iter("BOARD-BOUNDARY"):
        for line in boundary.findall("LINE"):
            for pt in line.findall("POINT"):
                xs.append(_safe_float(pt.get("X")))
                ys.append(_safe_float(pt.get("Y")))
        for arc in boundary.findall("ARC"):
            cx = _safe_float(arc.get("X"))
            cy = _safe_float(arc.get("Y"))
            r = _safe_float(arc.get("RADIUS"))
            sa = _safe_float(arc.get("START_ANGLE"))
            ea = _safe_float(arc.get("END_ANGLE"))
            sample_degs = [sa, ea]
            for cardinal in (0.0, 90.0, 180.0, 270.0):
                if _in_ccw_sweep(cardinal, sa, ea):
                    sample_degs.append(cardinal)
            for deg in sample_degs:
                rad = math.radians(deg)
                xs.append(cx + r * math.cos(rad))
                ys.append(cy + r * math.sin(rad))

    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _parse_components(xml_path: Path) -> list[dict]:
    """Parse component instances from a JITX XML board export.

    Returns a list of component dicts with placement, prefix, and
    ViaStructure classification. Part numbers are joined in from the
    SCHEMATIC section by designator.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    board = root.find("BOARD")
    if board is None:
        raise ValueError(f"No <BOARD> element found in {xml_path}")

    extent = _parse_board_extent(board)
    if extent is not None:
        log.info(
            "Board extent: X [%.3f, %.3f]  Y [%.3f, %.3f]  size %.2fx%.2f mm",
            extent[0], extent[2], extent[1], extent[3],
            extent[2] - extent[0], extent[3] - extent[1],
        )

    # Only first occurrence per designator — multi-unit components may
    # emit multiple SCH-INST entries sharing the same designator.
    props_map: dict[str, str] = {}
    for sch_inst in root.iter("SCH-INST"):
        props = sch_inst.find("PROPS")
        if props is not None:
            desig = props.get("DESIGNATOR")
            if desig and desig not in props_map:
                props_map[desig] = props.get("MPN", "")

    components: list[dict] = []
    for inst in board.findall("INST"):
        designator = inst.get("DESIGNATOR")
        if designator is None:
            log.warning("Skipping INST with no DESIGNATOR attribute")
            continue

        side = _normalize_side(inst.get("SIDE", "Top"))
        package = _strip_package(inst.get("PACKAGE", ""))

        pose = inst.find("POSE")
        if pose is None:
            log.warning("Skipping %s: no POSE element", designator)
            continue

        x = _safe_float(pose.get("X"))
        y = _safe_float(pose.get("Y"))
        angle = _safe_float(pose.get("ANGLE"))

        split = _split_refdes(designator)
        prefix, number = split if split is not None else (None, None)

        components.append(
            {
                "RefDes": designator,
                "Prefix": prefix,
                "Number": number,
                "X": x,
                "Y": y,
                "Rotation": angle,
                "Side": side,
                "Package": package,
                "PN": props_map.get(designator, ""),
                "IsViaStructure": package == TOPOLP_PACKAGE,
            }
        )

    if not components:
        log.warning("No component instances found in %s", xml_path)

    return components


def _position_sort_key_fn(start_corner: str, primary_axis: str, bin_size: float):
    """Build a raster-scan sort key.

    The primary axis is binned (floor-divided by `bin_size`) so components
    with similar primary-axis coordinates fall into the same scan column
    (or row, if primary is y) and are ordered by the secondary axis
    within it. Directions are set by `start_corner`: the component
    nearest that corner receives the lowest number.

    With `bin_size <= 0`, no binning is applied — primary-axis values are
    used as-is, giving a strict lexicographic sort that degenerates to
    the primary axis when values are unique.
    """
    y_descending, x_descending = CORNER_DIRS[start_corner]
    primary_is_x = primary_axis == "x"

    def _bin(v: float) -> float:
        return math.floor(v / bin_size) if bin_size > 0 else v

    def key(c: dict) -> tuple[float, float]:
        if primary_is_x:
            primary = -_bin(c["X"]) if x_descending else _bin(c["X"])
            secondary = -c["Y"] if y_descending else c["Y"]
        else:
            primary = -_bin(c["Y"]) if y_descending else _bin(c["Y"])
            secondary = -c["X"] if x_descending else c["X"]
        return (primary, secondary)

    return key


def _next_free(start: int, used: set[int]) -> int:
    n = start
    while n in used:
        n += 1
    return n


def _assign_numbers(
    components: list[dict],
    start: int,
    reserved: set[int],
    sort_key,
) -> int:
    """Assign NewNumber to `components` starting at `start`.

    Mutates each component in place. Skips numbers already in `reserved`
    and adds assigned numbers to it. Returns the next unused number, so
    callers can chain counters (e.g. TOPOLP U's continuing after regular
    U's on the same side).
    """
    components.sort(key=sort_key)
    n = start
    for c in components:
        n = _next_free(n, reserved)
        c["NewNumber"] = n
        reserved.add(n)
        n += 1
    return n


def _mark_preserved(components: list[dict], preserve_refdes: set[str]) -> None:
    """Mark components whose refdes is preserved (kept unchanged).

    A component is preserved when its refdes appears in `preserve_refdes`
    or when the refdes cannot be split into (prefix, number).
    """
    for c in components:
        c["Preserved"] = c["RefDes"] in preserve_refdes or c["Prefix"] is None


def _renumber_components(
    components: list[dict],
    start_corner: str,
    primary_axis: str,
    top_start: int,
    bottom_start: int,
    bin_size: float,
) -> None:
    sort_key = _position_sort_key_fn(start_corner, primary_axis, bin_size)
    side_starts = {"Top": top_start, "Bottom": bottom_start}

    # Preserved numbers reserve space per (side, prefix) so that
    # non-preserved components don't get the same new refdes.
    reserved: dict[tuple[str, str], set[int]] = defaultdict(set)
    for c in components:
        if c["Preserved"] and c["Prefix"] is not None and c["Number"] is not None:
            reserved[(c["Side"], c["Prefix"])].add(c["Number"])

    for side in SIDES:
        by_prefix: dict[str, list[dict]] = defaultdict(list)
        for c in components:
            if c["Side"] == side and not c["Preserved"]:
                by_prefix[c["Prefix"]].append(c)

        start = side_starts[side]

        for prefix, group in by_prefix.items():
            res = reserved[(side, prefix)]

            if prefix == VIA_STRUCTURE_PREFIX:
                regular = [c for c in group if not c["IsViaStructure"]]
                topolp = [c for c in group if c["IsViaStructure"]]
                next_n = _assign_numbers(regular, start, res, sort_key)
                _assign_numbers(topolp, next_n, res, sort_key)
            else:
                _assign_numbers(group, start, res, sort_key)

    for c in components:
        if c["Preserved"]:
            c["NewRefDes"] = c["RefDes"]
        elif "NewNumber" in c:
            c["NewRefDes"] = f"{c['Prefix']}{c['NewNumber']}"
        else:
            # Non-preserved component with no assignment should not occur;
            # fall back to the original to avoid producing an empty refdes.
            log.warning("Component %s was not renumbered", c["RefDes"])
            c["NewRefDes"] = c["RefDes"]


def _warn_duplicates(components: list[dict]) -> None:
    seen: dict[str, str] = {}
    for c in components:
        new = c["NewRefDes"]
        if new in seen and seen[new] != c["RefDes"]:
            log.warning(
                "Duplicate new refdes '%s' assigned to both '%s' and '%s' — "
                "consider increasing --bottom-start to clear the top range",
                new,
                seen[new],
                c["RefDes"],
            )
        seen[new] = c["RefDes"]


def _build_output_rows(components: list[dict]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for c in components:
        rows.append(
            {
                "OldRefDes": c["RefDes"],
                "NewRefDes": c["NewRefDes"],
                "Prefix": c["Prefix"] or "",
                "X": f"{c['X']:.3f}",
                "Y": f"{c['Y']:.3f}",
                "Side": c["Side"],
                "Package": _sanitize_csv_field(c["Package"]),
                "Preserved": "yes" if c["Preserved"] else "",
            }
        )
    rows.sort(key=lambda r: _natural_sort_key(r["OldRefDes"]))
    return rows


MAPPING_FIELDS = [
    "OldRefDes",
    "NewRefDes",
    "Prefix",
    "X",
    "Y",
    "Side",
    "Package",
    "Preserved",
]

FIXED_WIDTH_HEADERS = {
    "OldRefDes": "OLD",
    "NewRefDes": "NEW",
    "Prefix": "PRE",
    "X": "X COORD",
    "Y": "Y COORD",
    "Side": "SIDE",
    "Package": "PACKAGE",
    "Preserved": "KEEP",
}


def _write_delimited(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    delimiter: str = ",",
) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=fieldnames,
        extrasaction="ignore",
        delimiter=delimiter,
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _write_fixed_width(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    headers: dict[str, str],
) -> str:
    widths: dict[str, int] = {}
    for f in fieldnames:
        col_max = len(headers.get(f, f))
        for r in rows:
            col_max = max(col_max, len(r.get(f, "")))
        widths[f] = col_max

    def fmt_row(values: dict[str, str]) -> str:
        parts = [values.get(f, "").ljust(widths[f]) for f in fieldnames]
        return "  ".join(parts)

    header_row = fmt_row(headers)
    sep_row = "  ".join("-" * widths[f] for f in fieldnames)

    lines = [header_row, sep_row]
    for r in rows:
        lines.append(fmt_row(r))
    return "\n".join(lines) + "\n"


def _run_renumbering(
    xml_path: Path,
    start_corner: str,
    primary_axis: str,
    top_start: int,
    bottom_start: int,
    preserve: Iterable[str] | None,
    bin_size: float,
) -> list[dict]:
    """Parse, preserve-mark, renumber, and return the component list."""
    if start_corner not in CORNER_DIRS:
        raise ValueError(
            f"start_corner must be one of {list(CORNER_DIRS)}, got {start_corner!r}"
        )
    if primary_axis not in PRIMARY_AXES:
        raise ValueError(
            f"primary_axis must be one of {list(PRIMARY_AXES)}, got {primary_axis!r}"
        )
    if bin_size < 0:
        raise ValueError(f"bin_size must be >= 0, got {bin_size}")

    preserve_set = set(preserve) if preserve else set()

    components = _parse_components(xml_path)

    found_refdes = {c["RefDes"] for c in components}
    for missing in sorted(preserve_set - found_refdes):
        log.warning("Preserved refdes '%s' not found in board", missing)

    _mark_preserved(components, preserve_set)
    _renumber_components(
        components, start_corner, primary_axis, top_start, bottom_start, bin_size
    )
    _warn_duplicates(components)
    return components


def build_mapping(
    xml_file: str | Path,
    start_corner: str = "top-left",
    primary_axis: str = "x",
    top_start: int = 1,
    bottom_start: int = 500,
    preserve: Iterable[str] | None = None,
    bin_size: float = 1.0,
) -> dict[str, str]:
    """Return a {old_refdes: new_refdes} mapping without writing files."""
    xml_path = Path(xml_file)
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    components = _run_renumbering(
        xml_path,
        start_corner,
        primary_axis,
        top_start,
        bottom_start,
        preserve,
        bin_size,
    )
    return {c["RefDes"]: c["NewRefDes"] for c in components}


def renumber(
    xml_file: str | Path,
    output_file: str | Path | None = None,
    fmt: str = "csv",
    start_corner: str = "top-left",
    primary_axis: str = "x",
    top_start: int = 1,
    bottom_start: int = 500,
    preserve: Iterable[str] | None = None,
    bin_size: float = 1.0,
) -> str:
    """Renumber reference designators by board position.

    Parses INST elements from a JITX XML board export and assigns each
    component a new refdes derived from its placement. Grouping rules:

    - Each refdes prefix (C, R, L, U, J, ...) is numbered independently.
    - Top side starts at `top_start`; bottom side starts at `bottom_start`.
    - Within a side, components are ordered by position relative to
      `start_corner` with `primary_axis` as the outer sort axis.
    - ViaStructures (PACKAGE "TOPOLP") share the 'U' prefix but are
      numbered as a separate subgroup continuing after the regular 'U'
      ICs on each side.
    - Refdes in `preserve` keep their original name; their numbers are
      reserved so no other component is assigned the same refdes.
    - Refdes that cannot be split into (prefix, number) are preserved
      automatically.

    Args:
        xml_file: Path to the JITX XML board export file.
        output_file: Path to write the mapping; if None, result is only
            returned as a string.
        fmt: Output format — "csv", "tsv", or "txt" (fixed-width).
        start_corner: Starting corner — "top-left", "top-right",
            "bottom-left", or "bottom-right".
        primary_axis: Outer sort axis — "y" or "x".
        top_start: First designator number on the top side.
        bottom_start: First designator number on the bottom side.
        preserve: Iterable of refdes strings to keep unchanged.

    Returns:
        Formatted mapping string (OldRefDes, NewRefDes, ...).
    """
    xml_path = Path(xml_file)
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    components = _run_renumbering(
        xml_path,
        start_corner,
        primary_axis,
        top_start,
        bottom_start,
        preserve,
        bin_size,
    )

    rows = _build_output_rows(components)

    if fmt == "txt":
        result = _write_fixed_width(rows, MAPPING_FIELDS, FIXED_WIDTH_HEADERS)
    elif fmt == "tsv":
        result = _write_delimited(rows, MAPPING_FIELDS, delimiter="\t")
    else:
        result = _write_delimited(rows, MAPPING_FIELDS)

    if output_file is not None:
        out = Path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result)

    return result


def update_reference_designators_table(
    xml_file: str | Path,
    table_file: str | Path,
    output_file: str | Path | None = None,
    start_corner: str = "top-left",
    primary_axis: str = "x",
    top_start: int = 1,
    bottom_start: int = 500,
    preserve: Iterable[str] | None = None,
    bin_size: float = 1.0,
) -> Path:
    """Rewrite a JITX reference-designators.table with renumbered refdes.

    The table maps opaque inst-ids to refdes strings. This reads the
    table, looks up each current refdes in the board mapping derived
    from the XML, and replaces it with the new refdes. Entries whose
    current refdes is not present in the XML are left unchanged.

    Args:
        xml_file: Path to the JITX XML board export.
        table_file: Path to design-info/reference-designators.table.
        output_file: When set, the updated table is written here and
            `table_file` is left untouched. When None, `table_file` is
            rewritten in place with a .bak backup of the original.
        start_corner, primary_axis, top_start, bottom_start, preserve:
            Same meaning as in `renumber`.

    Returns:
        Path of the written file.
    """
    table_path = Path(table_file)
    if not table_path.exists():
        raise FileNotFoundError(f"Table file not found: {table_path}")

    mapping = build_mapping(
        xml_file,
        start_corner=start_corner,
        primary_axis=primary_axis,
        top_start=top_start,
        bottom_start=bottom_start,
        preserve=preserve,
        bin_size=bin_size,
    )

    with table_path.open() as f:
        data = json.load(f)

    assigned = data.get("assigned")
    if not isinstance(assigned, dict):
        raise ValueError(
            f"Table {table_path} has no 'assigned' object at the top level"
        )

    unmatched: list[str] = []
    new_assigned: dict[str, str] = {}
    for inst_id, old_refdes in assigned.items():
        if old_refdes in mapping:
            new_assigned[inst_id] = mapping[old_refdes]
        else:
            new_assigned[inst_id] = old_refdes
            unmatched.append(old_refdes)

    for u in sorted(set(unmatched)):
        log.warning(
            "Table refdes '%s' not found in XML board export; left unchanged",
            u,
        )

    data["assigned"] = new_assigned

    if output_file is None:
        backup = table_path.with_suffix(table_path.suffix + ".bak")
        shutil.copy2(table_path, backup)
        out_path = table_path
    else:
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text(json.dumps(data, indent=2) + "\n")
    return out_path
