"""Tests for jitx_refdes.

Each test builds an XML board in tmp_path and calls the public API
directly. No external fixtures required.
"""

import json
from pathlib import Path

import pytest

from jitx_refdes import (
    build_mapping,
    renumber,
    update_reference_designators_table,
)


def _inst(desig: str, x: float, y: float, side: str = "Top", package: str = "Pkg0402") -> str:
    return (
        f'  <INST DESIGNATOR="{desig}" PACKAGE="{package}" SIDE="{side}" HEIGHT="0.0">\n'
        f'    <POSE X="{x}" Y="{y}" ANGLE="0.0"/>\n'
        f'  </INST>'
    )


def _board(tmp_path: Path, insts: list[str]) -> Path:
    body = "\n".join(insts)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<PROJECT NAME="Test" VERSION="2.0">\n'
        '  <BOARD>\n'
        f'{body}\n'
        '  </BOARD>\n'
        '</PROJECT>\n'
    )
    path = tmp_path / "board.xml"
    path.write_text(xml)
    return path


# ---------------------------------------------------------------------------
# Prefix grouping + position sort
# ---------------------------------------------------------------------------


def test_prefix_groups_numbered_independently(tmp_path):
    """Each refdes prefix gets its own sequence; C and R don't share numbers."""
    xml = _board(tmp_path, [
        _inst("C1", 10, 10),
        _inst("C2", 20, 20),
        _inst("R1", 30, 30),
        _inst("R2", 40, 40),
    ])
    m = build_mapping(xml)
    # Both groups start at 1 independently.
    assert sorted(m[k] for k in ("C1", "C2")) == ["C1", "C2"]
    assert sorted(m[k] for k in ("R1", "R2")) == ["R1", "R2"]


def test_top_left_column_major_default(tmp_path):
    """Default is column-major raster: primary-axis=x, start=top-left.

    Components are grouped into X-bins (width 1 mm by default) and
    ordered left-to-right; within each column, top-first (Y descending).
    """
    xml = _board(tmp_path, [
        _inst("C1", 90, 10),
        _inst("C2", 10, 90),
        _inst("C3", 50, 50),
        _inst("C4", 90, 90),
    ])
    m = build_mapping(xml)
    # Bins: x=10 -> one comp (C2); x=50 -> one (C3);
    # x=90 -> two (C4 top, C1 bottom). Scan order: C2, C3, C4, C1.
    assert m["C2"] == "C1"
    assert m["C3"] == "C2"
    assert m["C4"] == "C3"
    assert m["C1"] == "C4"


def test_top_right_differs_from_top_left(tmp_path):
    """Column-major default makes top-left and top-right produce
    different orderings on scattered boards (the originally-requested
    behavior)."""
    xml = _board(tmp_path, [
        _inst("C1", 90, 10),
        _inst("C2", 10, 90),
        _inst("C3", 50, 50),
        _inst("C4", 90, 90),
    ])
    left = build_mapping(xml, start_corner="top-left")
    right = build_mapping(xml, start_corner="top-right")
    assert left != right


def test_bin_size_groups_near_x_values(tmp_path):
    """Components with X within one bin get Y-ordered within the column."""
    xml = _board(tmp_path, [
        _inst("C1", 10.1, 10.0),
        _inst("C2", 10.4, 90.0),
        _inst("C3", 10.9, 50.0),
        _inst("C4", 50.0, 50.0),
    ])
    m = build_mapping(xml, bin_size=1.0)
    # C1, C2, C3 all in bin 10. Within bin, Y-desc: C2(90), C3(50), C1(10).
    # Then bin 50 -> C4.
    assert m["C2"] == "C1"
    assert m["C3"] == "C2"
    assert m["C1"] == "C3"
    assert m["C4"] == "C4"


def test_bin_size_zero_disables_binning(tmp_path):
    """With bin_size=0, close X values sort strictly by X; Y only breaks ties."""
    xml = _board(tmp_path, [
        _inst("C1", 10.1, 10.0),
        _inst("C2", 10.4, 90.0),
        _inst("C3", 10.9, 50.0),
    ])
    m = build_mapping(xml, bin_size=0)
    # Strict X order: C1(10.1) < C2(10.4) < C3(10.9)
    assert m["C1"] == "C1"
    assert m["C2"] == "C2"
    assert m["C3"] == "C3"


def test_start_corner_and_primary_axis(tmp_path):
    """bottom-right + primary-axis=x sorts by (-x, y)."""
    xml = _board(tmp_path, [
        _inst("C1", 10, 90),
        _inst("C2", 90, 90),
        _inst("C3", 10, 10),
        _inst("C4", 90, 10),
    ])
    m = build_mapping(xml, start_corner="bottom-right", primary_axis="x")
    # Sort keys: C1(-10,90), C2(-90,90), C3(-10,10), C4(-90,10)
    # Ascending: C4(-90,10), C2(-90,90), C3(-10,10), C1(-10,90)
    assert m["C4"] == "C1"
    assert m["C2"] == "C2"
    assert m["C3"] == "C3"
    assert m["C1"] == "C4"


def test_invalid_options_raise(tmp_path):
    xml = _board(tmp_path, [_inst("C1", 0, 0)])
    with pytest.raises(ValueError):
        build_mapping(xml, start_corner="middle")
    with pytest.raises(ValueError):
        build_mapping(xml, primary_axis="z")
    with pytest.raises(ValueError):
        build_mapping(xml, bin_size=-1.0)


# ---------------------------------------------------------------------------
# Top/bottom start split and overrides
# ---------------------------------------------------------------------------


def test_top_bottom_default_starts(tmp_path):
    """Top defaults to 1, bottom defaults to 500."""
    xml = _board(tmp_path, [
        _inst("C1", 10, 10, side="Top"),
        _inst("C2", 20, 20, side="Top"),
        _inst("C3", 10, 10, side="Bottom"),
        _inst("C4", 20, 20, side="Bottom"),
    ])
    m = build_mapping(xml)
    # Column-major default: bin x=10 first, then x=20.
    # Top:    C1(x=10) -> C1, C2(x=20) -> C2
    assert m["C1"] == "C1"
    assert m["C2"] == "C2"
    # Bottom: C3(x=10) -> C500, C4(x=20) -> C501
    assert m["C3"] == "C500"
    assert m["C4"] == "C501"


def test_custom_top_and_bottom_starts(tmp_path):
    xml = _board(tmp_path, [
        _inst("C1", 10, 10, side="Top"),
        _inst("C2", 10, 10, side="Bottom"),
    ])
    m = build_mapping(xml, top_start=100, bottom_start=1000)
    assert m["C1"] == "C100"
    assert m["C2"] == "C1000"


# ---------------------------------------------------------------------------
# TOPOLP continues after regular U
# ---------------------------------------------------------------------------


def test_topolp_continues_after_regular_u_top(tmp_path):
    xml = _board(tmp_path, [
        _inst("U1", 10, 90, package="QFN32"),
        _inst("U2", 90, 90, package="QFN32"),
        _inst("U5", 10, 10, package="TOPOLP"),
        _inst("U6", 90, 10, package="TOPOLP"),
    ])
    m = build_mapping(xml)
    # Column-major: bin x=10 first (U1), then x=90 (U2) -> U1, U2
    assert m["U1"] == "U1"
    assert m["U2"] == "U2"
    # TOPOLP continues at 3: bin x=10 (U5), then x=90 (U6) -> U3, U4
    assert m["U5"] == "U3"
    assert m["U6"] == "U4"


def test_topolp_uses_bottom_start(tmp_path):
    xml = _board(tmp_path, [
        _inst("U1", 10, 10, side="Bottom", package="QFN32"),
        _inst("U2", 20, 20, side="Bottom", package="TOPOLP"),
    ])
    m = build_mapping(xml)
    # Regular U first, then TOPOLP continues
    assert m["U1"] == "U500"
    assert m["U2"] == "U501"


def test_topolp_with_only_via_structures(tmp_path):
    """If there are no regular U's, TOPOLP starts at side_start itself."""
    xml = _board(tmp_path, [
        _inst("U1", 10, 10, package="TOPOLP"),
        _inst("U2", 20, 20, package="TOPOLP"),
    ])
    m = build_mapping(xml)
    # Column-major: bin x=10 first, then x=20 -> U1, U2
    assert m["U1"] == "U1"
    assert m["U2"] == "U2"


# ---------------------------------------------------------------------------
# Preserve skips reserved numbers
# ---------------------------------------------------------------------------


def test_preserve_holds_refdes_unchanged(tmp_path):
    xml = _board(tmp_path, [
        _inst("C1", 90, 10),
        _inst("C2", 10, 90),
        _inst("C3", 50, 50),
        _inst("C4", 90, 90),
    ])
    m = build_mapping(xml, preserve={"C2"})
    # Column-major natural order: C2, C3, C4, C1 (x=10, 50, 90(top), 90(bot))
    # With C2 preserved (reserves 2):
    # Non-preserved sorted: C3, C4, C1
    # C3: next_free(1, {2}) = 1 -> C1
    # C4: next_free(2, {1,2}) = 3 -> C3
    # C1: next_free(4, {1,2,3}) = 4 -> C4
    assert m["C2"] == "C2"
    assert m["C3"] == "C1"
    assert m["C4"] == "C3"
    assert m["C1"] == "C4"


def test_preserve_accepts_iterable(tmp_path):
    """Preserve set can be any iterable (list, tuple, set)."""
    xml = _board(tmp_path, [_inst("J1", 0, 0), _inst("J2", 10, 10)])
    m = build_mapping(xml, preserve=["J1"])
    assert m["J1"] == "J1"


def test_unparseable_refdes_auto_preserved(tmp_path):
    """Refdes without trailing digits can't be renumbered; keep as-is."""
    xml = _board(tmp_path, [
        _inst("TP", 10, 10),   # no digits
        _inst("FID", 20, 20),  # no digits
        _inst("C1", 30, 30),
    ])
    m = build_mapping(xml)
    assert m["TP"] == "TP"
    assert m["FID"] == "FID"
    assert m["C1"] == "C1"


# ---------------------------------------------------------------------------
# reference-designators.table round-trip
# ---------------------------------------------------------------------------


def test_table_roundtrip_keys_preserved_values_updated(tmp_path):
    xml = _board(tmp_path, [
        _inst("C1", 90, 10),
        _inst("C2", 10, 90),
        _inst("C3", 50, 50),
    ])
    table_data = {
        "assigned": {
            "aaa111": "C1",
            "bbb222": "C2",
            "ccc333": "C3",
            "ddd444": "NotOnBoard",
        }
    }
    table = tmp_path / "reference-designators.table"
    table.write_text(json.dumps(table_data, indent=2))

    out = tmp_path / "new.table"
    update_reference_designators_table(xml, table, output_file=out)

    result = json.loads(out.read_text())
    assigned = result["assigned"]

    # Same set of inst-ids — no additions, no deletions.
    assert set(assigned.keys()) == set(table_data["assigned"].keys())

    # Column-major default: C2(x=10), C3(x=50), C1(x=90) -> C1, C2, C3
    assert assigned["aaa111"] == "C3"  # was C1 -> now C3
    assert assigned["bbb222"] == "C1"  # was C2 -> now C1
    assert assigned["ccc333"] == "C2"  # was C3 -> now C2

    # Unmatched entries pass through unchanged.
    assert assigned["ddd444"] == "NotOnBoard"


def test_table_inplace_creates_bak(tmp_path):
    """In-place update writes a .bak of the original."""
    xml = _board(tmp_path, [_inst("C1", 10, 10)])
    table = tmp_path / "reference-designators.table"
    original_text = json.dumps({"assigned": {"aaa": "C1"}}, indent=2)
    table.write_text(original_text)

    update_reference_designators_table(xml, table)

    bak = table.with_suffix(table.suffix + ".bak")
    assert bak.exists()
    assert bak.read_text() == original_text


def test_table_without_assigned_key_raises(tmp_path):
    xml = _board(tmp_path, [_inst("C1", 10, 10)])
    table = tmp_path / "reference-designators.table"
    table.write_text(json.dumps({"something_else": {}}))
    with pytest.raises(ValueError, match="assigned"):
        update_reference_designators_table(xml, table, output_file=tmp_path / "out")


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Board-extent parser
# ---------------------------------------------------------------------------


def test_board_extent_from_lines(tmp_path):
    """Extent is computed from BOARD-BOUNDARY LINE points."""
    from jitx_refdes.refdes import _parse_board_extent
    import xml.etree.ElementTree as ET

    xml = (
        '<BOARD>'
        '<BOARD-BOUNDARY><LINE WIDTH="0.001">'
        '<POINT X="-15.5" Y="-10.0"/><POINT X="15.5" Y="10.0"/>'
        '</LINE></BOARD-BOUNDARY>'
        '</BOARD>'
    )
    root = ET.fromstring(xml)
    assert _parse_board_extent(root) == (-15.5, -10.0, 15.5, 10.0)


def test_board_extent_handles_arcs(tmp_path):
    """Arc bounds include cardinal tangent points within the sweep."""
    from jitx_refdes.refdes import _parse_board_extent
    import xml.etree.ElementTree as ET

    # 90-degree arc from 0 to 90 centered at (0,0), radius 5.
    # Should include tangent at 90 (0, 5) and endpoints.
    xml = (
        '<BOARD>'
        '<BOARD-BOUNDARY>'
        '<ARC X="0" Y="0" RADIUS="5" START_ANGLE="0" END_ANGLE="90"/>'
        '</BOARD-BOUNDARY>'
        '</BOARD>'
    )
    root = ET.fromstring(xml)
    min_x, min_y, max_x, max_y = _parse_board_extent(root)
    assert max_x == pytest.approx(5.0)
    assert max_y == pytest.approx(5.0)


def test_board_extent_absent_returns_none(tmp_path):
    from jitx_refdes.refdes import _parse_board_extent
    import xml.etree.ElementTree as ET

    root = ET.fromstring('<BOARD/>')
    assert _parse_board_extent(root) is None


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------


def test_renumber_csv_report_columns(tmp_path):
    xml = _board(tmp_path, [_inst("C1", 10, 10)])
    result = renumber(xml, fmt="csv")
    header = result.splitlines()[0]
    assert header == "OldRefDes,NewRefDes,Prefix,X,Y,Side,Package,Preserved"


def test_renumber_writes_file(tmp_path):
    xml = _board(tmp_path, [_inst("C1", 10, 10)])
    out = tmp_path / "report.csv"
    renumber(xml, output_file=out)
    assert out.exists()
    assert "OldRefDes" in out.read_text()
