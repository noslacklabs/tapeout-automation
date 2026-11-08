# presubmit-check

A read-only checker for a GDSII file going to the eChipHub-SCL MPW shuttle (SCL 1.2 µm C1D process). It covers programme rules that SCL's DRC deck doesn't check.

**This tool doesn't replace DRC or LVS.** Both must still be run separately with SCL's current deck. No result here, including a run with no FAILs, means the design is ready to submit.

**Keep this tool private.** The PDK is marked confidential, and `presubmit_check/config/default.toml`, the tests and every report contain rule values or PDK measurements. Don't put any of them in a public repo, issue or forum post.

## What it won't do

- Change the input GDS. It opens the file read-only, and compares its MD5 before and after the run.
- Run anything from the PDK. Scripts, installers and DRC/LVS decks are never run. GDS, LEF and text files from the kit are parsed as plain data.
- Copy PDK files into the tool. Their locations come from the config file.
- Contact the portal, or submit or upload anything.
- Write inside the PDK, or overwrite an input with `--json`. It refuses both.

## Install

In WSL Ubuntu, with its own venv:

```bash
python3 -m venv ~/.venvs/presubmit-check
```

```bash
~/.venvs/presubmit-check/bin/pip install -e "/mnt/m/Desktop Folders/My Projects/Tapeout Automation/tools/presubmit-check[test]"
```

This installs klayout 0.30.12, plus gdstk 1.0.2 and pytest for the tests. KLayout only does polygon operations in memory. It never opens a design or PDK file, because it hides `$$$CONTEXT_INFO$$$`.

## Usage

```bash
presubmit-check OS0001_MyChip_25072026_v0.gds --die 2 --silicon OS0001 --pdk-root "$PDK" --drc-report OS0001_MyChip_25072026_v0_drc.txt --md5-report md5.txt --json out.json
```

`--pdk-root` is the kit folder holding `PadFrame_C1D`, `Analog_C1D` and `Digital_C1D`, called `SCL_C1D_PDK_17092026` in this download. Its parent folder's name contains `µ` (U+00B5), so quote the path. The error message says so if a Greek `μ` (U+03BC) was typed instead.

| Option | Meaning |
|---|---|
| `--die` | 1, 2, 3, 4 or 5 (mm). Required |
| `--silicon` | Silicon number. Required |
| `--pdk-root` | Kit folder. Required |
| `--drc-report` | KLayout marker database from the DRC run |
| `--md5-report` | `md5sum` output to compare with |
| `--silicon-block` | Silicon number block GDS as issued |
| `--json` | Also write full results as JSON |
| `--config` | Another config file |
| `--frame-set` | `padframe` (default) or `analog` |
| `--verbose` | Print all evidence, not just the first 12 lines per result |

| Exit code | Meaning |
|---|---|
| 0 | No FAILs. WARN and MANUAL items still need reading |
| 1 | At least one FAIL. An invalid GDS is a FAIL |
| 2 | The tool itself had an error (missing file, wrong PDK path, a bug in a check). The report is incomplete |

Each result has an ID such as `C09.1`, a status, its evidence with coordinates in µm, and the rule's source. The statuses are:

- **PASS:** the check ran and found nothing wrong. It says only that about this one check.
- **FAIL:** a rule is broken.
- **WARN:** a person needs to look, usually because the PDK or the rule is unclear.
- **MANUAL:** this wasn't decided here. It covers rules needing a person, inputs you didn't supply, and anything that couldn't be evaluated because the GDS wouldn't parse.

Source short names such as `[Procedure]` and `[Analog doc]` are defined at the top of `PDK_GUIDE.md`.

## What's checked

| Check | Results | Rule |
|---|---|---|
| 1 | C01.1–2 | Valid GDSII stream, checked record by record with byte offsets. Database unit |
| 2 | C02.1–4 | Silicon number format, one top cell, exact case-sensitive name, no `$$$CONTEXT_INFO$$$` |
| 3 | C03.1–2 | Extent is exactly (0,0) to the die size. Labels inside the die |
| 4 | C04.1–5 | Every coordinate on the 0.005 µm grid. User cells FAIL; unmodified SCL pad cells, standard cells and frame elements WARN |
| 5 | C05.1–4 | File name pattern, real calendar date, agreement with `--silicon`, version like `v0` |
| 6 | C06.1 | DRC report named `<layout>_drc.txt` |
| 7 | C07.1–2 | MD5 in `md5sum` format, compared with `--md5-report` |
| 8 | C08.1–3 | Pad, VDD and VSS counts for the die |
| 9 | C09.1–3 | `PVDD01` and `PVSS01` positions and orientations against the reference frame. Which frame set was used |
| 10 | C10.1–4 | Pads only from `io_pad_list.txt`, signal pad changes reported, exact 90 × 90 µm windows, stray shapes on layer 11 |
| 11 | C11.1–4 | Library-named cells match reference geometry. Label differences warn |
| 12 | C12.1 | Same geometry as a library cell under another name |
| 13 | C13.1 | Every layer/datatype is in the `PDK_GUIDE.md` table, including labels and paths |
| 14 | C14.1–3 | Layer 999:0 limit. Polygons outside the logo or a declared block. User shapes under SCL's cover |
| 15 | C15.1 | Silicon number block present, unchanged and unscaled, if given. Otherwise MANUAL |
| 16 | C16.1–4 | Metal 1 and 2 maximum. User metal FAILs. Pad, other SCL and joined metal WARN separately |
| 17 | C17.1–2 | Via over contact or poly. User geometry FAILs; overlaps inside SCL's own cells WARN |
| 18 | C18.1–4 | MANUAL: metal necking, 12 µm bus, ERC guidelines, all electrical checks |
| 19 | C19.1–4 | KLayout marker database: validity, markers by category, `@GUIDELINE` items, whether it names this layout |
| 20 | C20.1 | The statement that DRC and LVS still have to be run |

The library check uses a hash of each cell's flattened polygons, merged layer by layer. It doesn't depend on shape order, start vertex, winding direction or how an area is split into shapes.

## What isn't checked

- **DRC and LVS.** This tool has no design-rule or connectivity checks beyond those listed above.
- **Whether a DRC report is current or from these exact bytes.** A marker database holds no checksum of the GDS. C19.4 compares only names.
- **Whether `$PDK` is the latest kit or deck.** Check the portal yourself.
- **Rules in the "manual" list:** metal necking, the 12 µm bus on a single contact, ERC guidelines, current density, supply and operating limits, floating gates, parasitics and simulation.
- **Legal devices.** Nothing checks that only the 10 listed devices are used, or that a `.cdl` matches a swapped pad.
- **Density and antenna rules.** They aren't in the deck, [Procedure] or the reference documents, so whether any apply is unknown.
- **Delivery option, signed form and upload.** These are outside the GDS.
- **Some renamed copies.** C12 finds translated copies only. A rotated, mirrored or edited copy under a new name won't be found, and neither will a library name in a different case.
- **The logo's presence.** Removing the logo isn't reported in its own right.

## Rules that are assumptions

These are my readings where the documents don't settle the point. Each also appears on its result as `assumption:`.

| Result | Assumption |
|---|---|
| C03.2 | A label outside the die fails, because KLayout counts labels in a cell's extent. SCL doesn't say how it measures the window |
| C04 | SCL library cells are "unmodified" if their polygons match the reference. Labels and geometry that exactly match the reference frame are treated as SCL's, even inside a designer's cell |
| C05.1 | A design name may contain underscores. The last two fields are read as date and version |
| C05.4 | `v` followed by digits is treated as the normal version format, from the single example `v0` |
| C07.2 | A matching hash with a different file name or in upper case warns rather than fails |
| C10.2 | A swap to another listed pad warns rather than fails, because [Pad doc] step 4 permits it |
| C11.1 | Two cells match if their merged mask geometry matches, however it's split into shapes |
| C11.2 | Label differences warn because labels aren't on mask layers. A pad's labels may match either the pad library or the chosen frame, because SCL's two copies differ |
| C13.1 | Marker layers listed without a datatype are datatype 0, as the deck reads them |
| C14.1 | The limit counts polygons flattened under the top cell, though the deck's message says "top cell". This is my reading of the deck; the deck hasn't been run. On made-up data, a KLayout deep region's `size` does give the flat count |
| C14.2 | The logo is recognised by name plus a match to a variant shipped in the kit. An edited logo gets no exemption |
| C15.1 | A block flattened into the layout is compared exactly on mask layers inside its outline. Rotation, mirroring, renaming or several copies warn; scaling fails |
| C16 | "Maximum width in two directions" is read as: no merged shape may contain a 25 × 25 µm square, so a long narrow bus is fine. It's exact for right-angled shapes; angled edges can over-report but not under-report |
| C16.2–4 | Pad metal warns because the PDK doesn't say whether pads are exempt. SCL's frame and logo metal is treated the same way, and so is user metal that's only over the limit where it joins SCL metal |
| C17.2 | Via overlaps inside an unmodified SCL cell warn because they can't be edited. Overlaps between placed cells count as the designer's and fail |
| Pad frame | The default reference is the `padframe` set, as the brief named it. SCL hasn't confirmed this |

## Open questions for SCL

None of these has been guessed. The tool warns where they apply.

1. **Which pad frame set is correct?** `PDK/PadFrame_C1D` or `ANATECH/libs.ref/PadFrame_C1D`? Pad positions are the same in both for all five dies. They differ in the metal 1 ring shapes and in whether layer 999 covers the logo.
2. **Are off-grid library cells accepted as shipped?** Pad cells, frame labels, and also many standard cells have off-grid points, and you may not edit them.
3. **Is the metal 25 × 25 µm limit waived for SCL's own metal?** Every pad exceeds it, and so does the frame's ring (a 48 µm square fits in the outer ring and 90 µm in the inner one on the 2 mm frame).
4. **Is via over poly in SCL's cells acceptable?** The rule is mandatory, but `NOR201`, `PDDR02`, `PDDR04`, `DEC24H`, `DEC24L`, `DFCL11`, `DFPC11`, `LTPR11`, `MX4122` and the logo all have via over poly. The logo also has via over contact.
5. **Is the rule on via over contact checked by DRC?** [Analog doc] Level 9 says no check is defined, but the deck's rule 9.3.1 includes the overlap.
6. **What's the version format** in the file name? Only `v0` is shown.
7. **What's the silicon number block's format?** Layers, size, cell name, and whether it may be flattened. If its top cell is also called `OS####`, it clashes with the top-cell rule.
8. **Does the MD5 report have a required name and format?** And the [Procedure] example `OS0001_25072026_v0.gds` omits the design name its own naming rule requires.
9. **Is an XML marker database acceptable** as the `_drc.txt` report?
10. **Must `@GUIDELINE` items be cleared?** The documents say ERC isn't checked under DRC, yet the deck reports some of them.
11. **Why do pad labels differ between copies?** `PVDD01` and `PVSS01` have an extra metal 2 label in `PF/klayout/gds/io_pad_c1d.gds` that the frames and other libraries don't. `IPAD00` and `AIPAD00` labels differ in `scl_io_lib`. Their polygons match everywhere.
12. **What's the licence status?** "Open source", or "CONFIDENTIAL" under NDA?
13. **Do density or antenna rules apply?**

## Config

`presubmit_check/config/default.toml` holds the PDK locations, relative to `--pdk-root`, and every rule value: frame sets, cell names, the die table, layer tables and limits. To point at a differently unpacked kit or a new frame set, copy it and pass `--config`.

## Tests

```bash
cd "/mnt/m/Desktop Folders/My Projects/Tapeout Automation/tools/presubmit-check" && ~/.venvs/presubmit-check/bin/python -m pytest
```

The tests generate small synthetic GDS files with invented geometry and invented cell names, in a temp "kit" laid out like the real one. They use the rule values from the config and no PDK data.

`tests/test_integration_pdk.py` runs against the real kit read-only, and is skipped if it isn't found. Set `PRESUBMIT_PDK_ROOT` if the kit is somewhere else. It checks:

- The expected results on `frame2mmx2mm.gds`.
- That all ten shipped frames fail only on naming and top-cell rules.
- That the raw parser agrees with gdstk.
