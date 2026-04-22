"""CLI entry point for jitx_refdes.

Usage:
    python -m jitx_refdes <xml_file> [options]
"""

import argparse
import sys

from jitx_refdes.refdes import (
    PRIMARY_AXES,
    START_CORNERS,
    renumber,
    update_reference_designators_table,
)


def _parse_preserve(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    result: set[str] = set()
    for v in values:
        for piece in v.split(","):
            piece = piece.strip()
            if piece:
                result.add(piece)
    return result


def _read_preserve_file(path: str) -> set[str]:
    result: set[str] = set()
    with open(path) as f:
        for line in f:
            # Allow '#' comments and blank lines.
            line = line.split("#", 1)[0].strip()
            if line:
                result.add(line)
    return result


def main():
    parser = argparse.ArgumentParser(
        prog="jitx_refdes",
        description=(
            "Renumber reference designators by board position from a JITX "
            "XML board export. Each refdes prefix (C, R, L, U, ...) is "
            "numbered independently; ViaStructures (PACKAGE 'TOPOLP') "
            "share the 'U' prefix and continue after the regular U ICs "
            "on each side."
        ),
    )
    parser.add_argument(
        "xml_file",
        help="Path to the JITX XML board export file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Write the renumbering report to FILE instead of stdout.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["csv", "tsv", "txt"],
        default="csv",
        help="Report format (default: csv).",
    )
    parser.add_argument(
        "--start-corner",
        choices=START_CORNERS,
        default="top-left",
        help=(
            "Corner the component nearest which receives the lowest "
            "number (default: top-left)."
        ),
    )
    parser.add_argument(
        "--primary-axis",
        choices=PRIMARY_AXES,
        default="x",
        help=(
            "Outer (raster-scan) sort axis (default: x). With x, "
            "components are grouped into columns by X and ordered by Y "
            "within each column. With y, the scan is row-major."
        ),
    )
    parser.add_argument(
        "--bin-size",
        type=float,
        default=1.0,
        metavar="MM",
        help=(
            "Primary-axis bin width in mm used to group components into "
            "raster-scan columns/rows (default: 1.0). Use 0 to disable "
            "binning — the sort then degenerates to a strict primary-axis "
            "order with the secondary axis only breaking ties."
        ),
    )
    parser.add_argument(
        "--top-start",
        type=int,
        default=1,
        help="First designator number on the top side (default: 1).",
    )
    parser.add_argument(
        "--bottom-start",
        type=int,
        default=500,
        help="First designator number on the bottom side (default: 500).",
    )
    parser.add_argument(
        "--preserve",
        action="append",
        default=[],
        metavar="REFDES",
        help=(
            "Refdes to preserve (keep unchanged). Repeatable and accepts "
            "comma-separated lists, e.g. --preserve J1,J2."
        ),
    )
    parser.add_argument(
        "--preserve-file",
        metavar="FILE",
        help=(
            "Text file listing refdes to preserve (one per line, '#' "
            "starts a comment)."
        ),
    )
    parser.add_argument(
        "--table",
        metavar="FILE",
        help=(
            "Path to a JITX design-info/reference-designators.table. "
            "When set, the table is rewritten using the new refdes values "
            "(joined through the old refdes). The original is saved to "
            "<FILE>.bak unless --table-output is also given."
        ),
    )
    parser.add_argument(
        "--table-output",
        metavar="FILE",
        help=(
            "When writing the updated reference-designators.table, write "
            "it here instead of in place. Requires --table."
        ),
    )

    args = parser.parse_args()

    if args.table_output and not args.table:
        parser.error("--table-output requires --table")

    preserve = _parse_preserve(args.preserve)
    if args.preserve_file:
        preserve |= _read_preserve_file(args.preserve_file)

    result = renumber(
        args.xml_file,
        output_file=args.output,
        fmt=args.format,
        start_corner=args.start_corner,
        primary_axis=args.primary_axis,
        top_start=args.top_start,
        bottom_start=args.bottom_start,
        preserve=preserve,
        bin_size=args.bin_size,
    )

    if args.output is None:
        sys.stdout.write(result)

    if args.table:
        update_reference_designators_table(
            args.xml_file,
            table_file=args.table,
            output_file=args.table_output,
            start_corner=args.start_corner,
            primary_axis=args.primary_axis,
            top_start=args.top_start,
            bottom_start=args.bottom_start,
            preserve=preserve,
            bin_size=args.bin_size,
        )


if __name__ == "__main__":
    main()
