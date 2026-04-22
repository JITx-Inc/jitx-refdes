# jitx-refdes

Renumber PCB reference designators by position on the board from a
JITX XML board export.

## Overview

Parses the XML that JITX exports from a board design and produces a
report mapping each existing reference designator to a new one derived
from the component's placement. Optionally rewrites the design's
`design-info/reference-designators.table` so the new refdes values
stick.

Renumbering rules:

- Each refdes prefix is numbered independently — all capacitors as a
  group, all resistors as a group, etc.
- Top side starts at `1`; bottom side starts at `500`. Both are
  overridable.
- Within a side, components are ordered as a raster scan relative to
  the starting corner (default **top-left**). Default scan is
  column-major: walk down the leftmost X-column top-to-bottom, then
  move right to the next column, and so on. Columns are formed by
  binning the primary axis on a configurable width (default 1 mm) so
  components with near-identical X values land in the same column.
- Pass `--primary-axis y` to switch to row-major scanning, or
  `--bin-size 0` to disable binning (strict primary-axis sort).
- The board extent is parsed from `BOARD-BOUNDARY` elements (supports
  rectangles, rounded-corner rectangles, and circular boards that are
  not centered at the origin) and logged for context.
- ViaStructures (PACKAGE `TOPOLP`) share the `U` prefix but are
  renumbered as a separate subgroup continuing after the regular `U`
  ICs on each side.
- Spec-fixed designators (e.g. certain connectors whose name is
  dictated by a spec) can be listed with `--preserve` to hold them
  unchanged. Their numbers are reserved so other components don't
  collide with them.
- Refdes that don't split into `<prefix><number>` are preserved
  automatically.

## Installation

Requires Python 3.12+.

```bash
pip install git+https://github.com/JITx-Inc/jitx-refdes.git
```

For development:

```bash
git clone https://github.com/JITx-Inc/jitx-refdes.git
cd jitx-refdes
pip install -e .
```

## Exporting XML from JITX

In the JITX IDE, use the export menu to generate an XML file for your
board design. The resulting file will have a `<PROJECT>` root element
containing a `<BOARD>` section with component instances and a
`<SCHEMATIC>` section with part number data.

## Usage

### Command line

```
jitx-refdes <xml_file> [options]
```

**Report to stdout:**

```bash
jitx-refdes board.xml
```

**Write the report to a file:**

```bash
jitx-refdes board.xml -o refdes_map.csv
```

**Also update the design's reference-designators.table (in place, with
a `.bak` backup):**

```bash
jitx-refdes board.xml \
  -o refdes_map.csv \
  --table designs/my-design/design-info/reference-designators.table
```

**Update the table to a new file, leaving the original untouched:**

```bash
jitx-refdes board.xml \
  --table designs/my-design/design-info/reference-designators.table \
  --table-output reference-designators.new.table
```

**Hold spec-fixed refdes unchanged:**

```bash
jitx-refdes board.xml --preserve J1,J2 --preserve MOUNT1
# or via a file (one refdes per line, '#' for comments)
jitx-refdes board.xml --preserve-file preserved.txt
```

**Override side starting numbers:**

```bash
jitx-refdes board.xml --top-start 1 --bottom-start 1000
```

**Use a different starting corner or axis priority:**

```bash
jitx-refdes board.xml --start-corner bottom-right --primary-axis x
```

The `python -m jitx_refdes` invocation also works as an alternative to
the `jitx-refdes` command.

### Python API

```python
from jitx_refdes import (
    build_mapping,
    renumber,
    update_reference_designators_table,
)

# old -> new refdes dict
mapping = build_mapping("board.xml", preserve={"J1", "J2"})

# Write a CSV report
renumber("board.xml", output_file="refdes_map.csv")

# Rewrite the design's reference-designators.table in place (with .bak)
update_reference_designators_table(
    "board.xml",
    "designs/my-design/design-info/reference-designators.table",
)
```

## Options reference

| Option | Description |
|---|---|
| `xml_file` | Path to the JITX XML board export (required) |
| `-o`, `--output FILE` | Write report to FILE instead of stdout |
| `-f`, `--format {csv,tsv,txt}` | Report format (default: `csv`) |
| `--start-corner {top-left,top-right,bottom-left,bottom-right}` | Corner nearest which gets the lowest number (default: `top-left`) |
| `--primary-axis {x,y}` | Raster scan axis — `x` is column-major (default), `y` is row-major |
| `--bin-size MM` | Primary-axis bin width for grouping into raster columns/rows (default: `1.0`; use `0` to disable) |
| `--top-start N` | First number on the top side (default: `1`) |
| `--bottom-start N` | First number on the bottom side (default: `500`) |
| `--preserve REFDES` | Refdes to preserve (repeatable; comma-lists ok) |
| `--preserve-file FILE` | Text file of refdes to preserve (one per line) |
| `--table FILE` | Read & rewrite this `reference-designators.table` |
| `--table-output FILE` | Write updated table here instead of in place |
| `-h`, `--help` | Show help and exit |

## How it works

1. Parses `BOARD/INST` elements for placement data (position, side,
   package name).
2. Parses `SCHEMATIC/.../SCH-INST/PROPS` for part numbers.
3. Groups non-preserved components by `(side, prefix)`, splits `U`
   into regular + TOPOLP subgroups, and assigns numbers by position.
4. Writes the old-to-new mapping in the requested format.
5. If `--table` is set, reads the JITX `reference-designators.table`
   (inst-id → refdes), joins through the old refdes to replace each
   value with its new refdes, and writes the updated table.

## License

MIT
