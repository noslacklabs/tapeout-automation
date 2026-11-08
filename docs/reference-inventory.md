# S8 reference inventory

The initial inventory below is retained as historical capture evidence. Milestone 1
now also has a freshly reproduced current-source baseline:
[milestone report](milestone-1.md), [baseline](../reference/milestone1/baseline.json)
and [acceptance](../reference/milestone1/acceptance.json). The new immutable
engineering reference resolves the earlier source-drift gap for development
comparison; final silicon freeze/review and signoff remain separate.

This is a read-only capture of `M:/My Projects/Tapeout Chip`. Source scripts,
RTL, prior build directories and the kit are preserved. The existing graph was a
historical navigation index only; current source hashes were checked separately.

The source repository HEAD is `f01d8574d020dfeef13632a15aa8891c8552e3fb` with
pre-existing working-tree changes. `rtl/s8.v` SHA-256 is
`fa52f0fb87289df5dd2f1f5e6004538fb513956fc834ad78556c39021a86ca46`.
Its MD5 `1526c6ba36f68e67d6769f75eef4e41d` differs from the historical
physical source MD5 `ced63f7ea8bcad407a151f427743815f`. A new reference build
against frozen inputs remains necessary. The inventory does not certify current RTL.

[dependencies.json](../reference/s8/dependencies.json) identifies 89 source files,
14 historical evidence files, 44 selected technology assets, environment variables,
external models and 10 regression groups. [recheck.json](../reference/s8/recheck.json)
reopens those 147 files and the 23 recorded physical-build artifacts; all recorded
hashes matched at capture. [pdk-files.json](../reference/s8/pdk-files.json) fingerprints
all 254 files in the kit installation, without copying their contents into the package.

## Dependency boundaries

| Classification | Existing material and treatment |
|---|---|
| Engine | Dependency scheduling, argument-array process execution, result policy, unique run directories, snapshots, hashing, report structure and completeness validation |
| Process/profile | Cell filters, Liberty/LEF working-copy fixes, DBU/grid/orientation, routing obstructions, track alignment, filler geometry, power connectivity, kit decks, 2 mm frame, pad pin/polarity/driver mappings and extraction limitations |
| S8 project | `clk`/`rst`/`sck`, RAM protocol and output expectations, CPU program/ISS tests, gated output and CTS hook, pad positions, clock tracing, routed equivalence and seeded start model transformations |
| Local installation | WSL, Nix store paths, two Python environments, executable paths, model locations and PDK root; kept in a separate toolchain configuration |

The current `flow/run_all.sh` runs synthesis, routing, assembly/decks, additional
checks, negatives, flat DRC, STA/smoke and reporting. Its shell pipeline masks
some failures and its exit status enforces only the SCK gate. It cannot provide
the new runner's aggregate result contract. Its fixed output cleanup must not be
used for new engine runs.

`flow/tech/make_tech.py` and Liberty filtering modify working copies. The router's
track adjustments and overlap checks, bounded retries, assembly grid checks and
metal-only power check are candidates for reuse with configuration separated.
`make_cdl.py` and the existing DEF/DRC parsers require replacement or bounded,
validated interpretation: the review probes found alias loss and empty-report
acceptance. Direct copying would not qualify arbitrary supported projects.

## Wider S8 regression manifest

| Group | Required historical evidence to preserve |
|---|---|
| Physical flow | GDS/CDL inventories, expected DRC categories, complete LVS, 11 deliberate defects detected, presubmit rows, 92-point power connectivity and raw extraction FAIL |
| Clock tracing | All retained DFF clocks reach the external clock; historical count 45, reconciled against the new mapped inventory |
| Routed equivalence | Nonempty proof PASS, matched gold/gate/Liberty/shim hashes and actual post-reset comparison |
| Routed SDF tests | 20 tests at each min/typ/max corner, correct models/annotation, no failures/errors/skips |
| Seeded startup | Five seeds at three corners, 15 runs/300 tests, original UDP/specify arcs retained and complete startup records |
| SDF probe | Measured nonzero C-to-Q at all corners, matching instance paths, timescale and SDF values |
| RAM model | 36 positive demo/corner/duty cases and four positively detected 25 MHz negatives; independent expected RAM/event results |
| Pad polarity | 72 DC rail cases with model hashes; does not establish load/slew/current ratings |
| CPU formal | All seven named task statuses after freeze; historical continuation did not rerun these tasks |
| Mapped campaigns | Seeded mapped simulation and independent CPU model expectations, separately identified from routed/SDF tests |

Exact runners, commands, inputs and expected counts are in `dependencies.json`.
The digital simulation model copy and Microchip RAM model remain external.
Their observed availability/hashes are captured; redistribution permission for
the RAM model remains unresolved. No new download or model bundling occurred.

Historical `presubmit` evidence records 38 PASS, 8 WARN and 7 MANUAL. The new
baseline reproduces those states and the raw extraction FAIL. Current source
reproduction and the fresh binary-userspace probe are now complete. Full new-engine
timing coverage, three-design qualification, unused pads and clean WSL release
installation remain NOT RUN.
