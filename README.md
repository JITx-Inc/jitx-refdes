# jitx-refdes

Renumber PCB reference designators by position on the board from a
JITX XML board export.

## Overview

Parses the XML that JITX exports from a board design and produces a
report mapping each existing reference designator to a new one derived
from the component's placement. Optionally rewrites the design's
`design-info/reference-designators.table` so the new refdes values
stick when you re-open the design in JITX.

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
pip install -e ".[dev]"
pytest
```

## Exporting XML from JITX

In the JITX IDE, use the export menu to generate an XML file for your
board design. The resulting file will have a `<PROJECT>` root element
containing a `<BOARD>` section with component instances and a
`<SCHEMATIC>` section with part number data.

The exporter writes the XML to `designs/<design>/xml/<design>.xml` (or
`designs/<design>/altium/<design>.xml` on older exports).

## Quickstart

Given an XML export of a 30 × 20 mm board with six components (five on
top, one on bottom), the tool produces a report like this:

```console
$ jitx-refdes designs/my-design/xml/my-design.xml -f txt
INFO Board extent: X [0.000, 30.000]  Y [0.000, 20.000]  size 30.00x20.00 mm

OLD  NEW   PRE  X COORD  Y COORD  SIDE    PACKAGE  KEEP
---  ----  ---  -------  -------  ------  -------  ----
C1   C3    C    15.000   18.000   Top     Pkg0402
C3   C1    C    5.000    18.000   Top     Pkg0402
C5   C2    C    5.000    12.000   Top     Pkg0402
J1   J500  J    15.000   5.000    Bottom  USB-C
R2   R1    R    15.000   10.000   Top     Pkg0402
U1   U1    U    25.000   15.000   Top     QFN32
```

Reading the report:

- `C3` and `C5` share the leftmost column (X ≈ 5 mm); `C3` is higher
  (Y = 18) so it gets `C1`, `C5` gets `C2`.
- `C1` is in the middle column and becomes `C3`.
- `R2` is the only `R` so it becomes `R1`.
- `U1` is the only `U` so it stays `U1`.
- `J1` is on the Bottom side so its counter starts at 500.
- The `Board extent` line comes from parsing `BOARD-BOUNDARY` and is
  emitted at INFO level — useful for spotting off-center boards.

The default output format is CSV; pass `-f txt` for the fixed-width
layout shown above, or `-f tsv` for tab-separated. CSV/TSV use the
full field names (`OldRefDes`, `NewRefDes`, `Prefix`, `Preserved`);
the `txt` format abbreviates them to `OLD`, `NEW`, `PRE`, `KEEP` for
readability.

## Workflow

The typical loop when renumbering a real board:

1. **Generate the report** and review the proposed mapping:

   ```bash
   jitx-refdes designs/my-design/xml/my-design.xml -o refdes_map.csv
   ```

2. **Identify spec-fixed designators** that must keep their original
   names — usually a handful of connectors whose refdes is dictated by
   a spec, mechanical ground points, fiducials, etc. Collect them in a
   text file:

   ```text
   # preserved.txt — one refdes per line, '#' starts a comment
   J1     # USB connector — refdes fixed by host-interface spec
   J7     # debug header
   MH1
   MH2
   ```

3. **Re-run** with the preserve list and inspect the new report:

   ```bash
   jitx-refdes designs/my-design/xml/my-design.xml \
     --preserve-file preserved.txt \
     -o refdes_map.csv
   ```

   Preserved rows show `yes` in the `Preserved` column and keep their
   original `NewRefDes`. Their numbers are reserved so no other
   component collides with them.

4. **Apply the mapping** by rewriting the design's
   `reference-designators.table`. This file is JSON that JITX reads
   when re-opening the design — it maps each component's internal
   inst-id to a refdes. Rewriting it with new refdes values is what
   makes the renumbering stick:

   ```bash
   jitx-refdes designs/my-design/xml/my-design.xml \
     --preserve-file preserved.txt \
     --table designs/my-design/design-info/reference-designators.table
   ```

   By default, in-place updates save a `.bak` copy of the original
   next to the file. Use `--table-output FILE` to write the updated
   table elsewhere and leave the original untouched — useful for
   dry-runs.

   **Refdes prefix changes.** If you renamed a component's prefix in
   the design (e.g. `U1` → `J1`) the table on disk still holds the
   old refdes for that inst-id. When the XML export carries a stable
   `<INST INST-ID="...">` per instance, the join uses inst-id and
   reconciles the rename automatically. When it does not, those
   entries cannot be matched and are left unchanged with a WARNING.
   Pass `--strict` to make the CLI exit non-zero in that case (the
   updated table is still written so you can inspect it).

5. **Re-open the design in JITX** to pick up the new refdes values.

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
| `--strict` | Exit non-zero if any table entry cannot be reconciled (requires `--table`) |
| `-h`, `--help` | Show help and exit |

## How it works

1. Parses `BOARD-BOUNDARY` to compute the board's extent and logs it.
2. Parses `BOARD/INST` elements for placement data (position, side,
   package name).
3. Parses `SCHEMATIC/.../SCH-INST/PROPS` for part numbers.
4. Groups non-preserved components by `(side, prefix)`, splits `U`
   into regular + TOPOLP subgroups, and assigns numbers by position.
5. Writes the old-to-new mapping in the requested format.
6. If `--table` is set, reads the JITX `reference-designators.table`
   (inst-id → refdes JSON) and rewrites each entry's refdes. The join
   prefers a stable inst-id match between the XML and the table — so
   prefix changes (e.g. `U1` → `J1`) reconcile correctly when the XML
   exposes `<INST INST-ID="…">`. When it does not, the join falls back
   to the old refdes (the historical behavior). Entries that match by
   neither are left unchanged and a WARNING is logged for each;
   `--strict` upgrades that to a non-zero exit code.

## License

MIT
