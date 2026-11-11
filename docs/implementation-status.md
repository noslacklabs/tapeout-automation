# Implementation checkpoint — 9 November 2026

**Milestones 1, 2 and the documented S8 physical-flow scope of 3 are complete. Milestone 4 is in progress: its DRC, LVS and
presubmission adapters are done ([progress](milestone-4.md), 10 November 2026); timing, simulation and reporting are open.** The v0.1 release
and reusable verification qualification remain future work.

`chipflow` is the reusable automation engine; SCL C1D 1.2 µm on the 2×2 mm frame
is its current technology profile. S8, a bit-serial 8-bit CPU, is the reference
project and the only design exercised through the physical flow so far. Other
supported designs supply their own RTL, configuration, constraints, pad mapping
and tests without shared-engine edits. This is reuse across designs within the
profile; other foundries and die sizes are outside the first-release boundary.
The [supported configurations](supported-configurations.md) distinguish that
scope from the still-pending multi-project physical acceptance.

## Milestone 1 completion

An immutable engineering snapshot of current S8 RTL has now been built and
verified: SHA-256 `fa52f0fb87289df5dd2f1f5e6004538fb513956fc834ad78556c39021a86ca46`.
It reproduces the recorded behavior, including known limitations. This is separate
from an approved final silicon source freeze.

- DRC and flat DRC execute all 72 expected categories with zero markers. LVS
  matches 22 circuits, including the expected top and nonempty matching coverage.
- Routing overlaps are zero; all 92 metal-only power probes pass; all 11 layout
  negative/control cases behave as expected. Presubmit retains 38 PASS, 8 WARN,
  7 MANUAL and no tool errors.
- Routed equivalence and all 45 clock traces pass. RTL/routed/SDF tests total 380
  passing cases, including 300 seeded cases. Nonzero SDF delays match all corners.
- The 36-case routed RAM matrix passes; all four intended 25 MHz negatives detect
  timing violations. Actual exits for 40 simulator and 2 compiler processes were
  captured and checked. Pad DC polarity passes 72 rail measurements.
- Raw extraction remains **FAIL**: 0.1374 pF against the 26.915 pF diagnostic
  routed-length/LEF estimate. Design-status validation returns **1**; reference
  reproduction qualifies separately. The estimate is not electrically validated.

The complete hash-pinned binary runtime was installed offline into fresh Ubuntu
userspace with private PID/mount namespaces, no mounted host store or home and no
reused Python site-packages. Configured tool checks, real SCL synthesis, KLayout
geometry/frame probes and Ngspice startup pass. The run path includes spaces and
U+00B5. The first missing-proc failure and logs remain retained. Clean WSL release
installation remains milestone 6 work; independent EDA source rebuilds are not claimed.

Project/stage/result/required-check contracts, assumption ownership, workaround
evidence, ten wider regression groups and model provenance/availability are
documented. Eight guard tests reject empty/malformed/missing reports, bad proof
status and invalid parasitics. See [milestone report](milestone-1.md) and
[acceptance](../reference/milestone1/acceptance.json).

## Existing milestone 2 foundation

- Installable `chipflow` 0.1.0.dev1, schema/result version 1 and profile version 0.1.0.
- Strict typed project contract, project-relative input resolution and declared
  dependency snapshots. Unknown/unsupported/unsafe inputs fail early.
- Dependency-aware runner with unique locked runs, fresh stage directories,
  argument arrays, logs, artifact hashes, structural validators, atomic results,
  Windows/POSIX process-tree cleanup, timeout and cancellation.
- Full-build minimum and result policy: design FAIL stays nonzero, infrastructure
  takes precedence, optional executed failure cannot pass and cancellation is 130.
- Configured toolchain doctor and real Liberty synthesis / batch-plus-Python
  KLayout geometry/frame probe. The engine contains no S8 names or local executable paths.
- Current source/evidence/PDK inventories, historical artifact comparison,
  wider S8 regression/model dependencies, limitations and recovered tool recipes.

## Milestone 2 completion

Milestone 2 ("Package configuration and reliable execution") is complete and verified at 0.1.0.dev4. The [final closeout](milestone-2-verification.md#final-closeout-8-november-2026) resolves all remaining findings, with retained evidence in `work/m2-closeout-20261108T100510Z/`.
- **Project & pad configuration**: Strict schema-1 TOML loaders for `chip.toml` and `pads.toml` (`chipflow.config.project`, `chipflow.config.pads`), supporting derived clock outputs (`[clock.derived_outputs]`), synchronous resets, and I/O delay/load specs.
- **Process profile**: SCL C1D 1.2 µm 2×2 mm frame profile (`profile-0.1.0.toml`) with 12 peripheral signal slots, 4 power pads, allowed pad cell and driver pairings, and DROP/FORCE standard cell exclusion rules.
- **Stage execution & provenance**: Dependency-aware runner with isolated stage directories (`stages/<id>/`), command logs, timeout enforcement, process tree cleanup, diagnostic continuation, and full provenance tracking in `run-manifest.json` and `input-manifest.json`.
- **Bounded elaboration**: Yosys 0.33 elaboration adapter (`chipflow.stages.elaboration`) with safe path staging, AST/JSON inspection, state bit accounting, blackbox rejection, and clock net usage cone tracing with source line citations.
- **Semantic validation**: 10 pad qualification checks (`pads.schema`, `pads.frame`, `pads.slot_coverage`, `pads.cell`, `pads.driver`, `pads.unused`, `pads.qualification`, `pads.direction`, `pads.port_coverage`, `pads.capacity`), port/bus cross-validation, and `pad-plan.json` generation.
- **Mapped netlist checks**: Post-synthesis gate-level netlist checker (`chipflow.stages.netlist_checks`) checking primitives, sequential clocks, tie usage and caller-supplied allowlists. Fresh real-probe tests also detect excluded cells when the strict profile allowlist is applied; chip synthesis integration is Milestone 3.
- **CLI commands**: Full `chipflow validate` exiting 0 on clean preflight and 2 on semantic failure; `chipflow build` running preflight and exiting 2 reporting physical adapters pending Milestone 3.
- **Validation evidence**:
  - Real S8 validation run (`projects/s8/builds/run-20261108T100553.914641Z-5968b18008a4`): Exit 0 (toolchain, elaboration, configuration PASS).
  - S8 build preflight run (`projects/s8/builds/run-20261108T100556.730460Z-9bba6f8c992c`): Exit 2 (infrastructure notice, downstream checks NOT RUN).
  - Negative test (S8 without derived outputs): Exit 2 citing `clock net used outside flop CLK pins` at `rtl/s8.v:183`.
  - Negative test (Counter with unused slots): Exit 2 citing `pads.unused: no qualified unused-slot policy in profile 0.1.0`.

## Package verification

- Test suite: 111 tests after final closeout (`docs/milestone-2-verification.md`):
  - 111/111 pass on WSL2 Ubuntu 24.04, Python 3.12.3, with `CHIPFLOW_TEST_TOOLCHAIN` set.
  - 101 pass and 10 real-tool integration tests skip, with the reason printed, on Windows Python 3.13.5.
- Wheel `chipflow-0.1.0.dev4-py3-none-any.whl` built in `work/m2-dist-dev4/` and verified via `scripts/verify_wheel.py`:
  - 5/5 verification records passed (`create-venv`, `install`, `import-location`, `tests`, `doctor-probe`).
  - Result: `installed_package_verification: PASS`.
- Milestone 1 contract manifest binding check: 18/18 bound files verified with zero drift (`M1-bound files OK`).
- Milestone 1 evidence guard tests: 8/8 tests pass.

## Milestone gates still open

| Milestone | Status | Remaining gate |
|---|---|---|
| 1 | Complete | Current reference, pinned fresh-userspace startup, contracts and scope-bound limitations |
| 2 | Complete | Bounded Yosys elaboration/coverage, pad/frame validation, full CLI validation, provenance tracking, and mapped netlist checks |
| 3 | Complete: S8 physical scope | Five findings closed at 0.1.0.dev5; 138 Linux tests, installed S8/constraint-variant builds and baseline geometry/connectivity comparisons pass |
| 4 | In progress | Item 2 of 7 done: DRC, flat DRC, LVS and presubmission adapters run in `chipflow build`. Parasitic, timing, simulation and report adapters remain open; project-specific S8 downstream execution is complete with input hold/extraction FAIL |
| 5 | Open | Freeze engine/profile and run S8, counter and multi-file PWM acceptance plus deliberate defect fixtures |
| 6 | Open | Clean-install wheel qualification and v0.1 release; migrate active S8 only after regression comparison |

## Milestone 3 verification

Milestone 3 ("Extract the reusable physical flow") passes its documented S8 acceptance at 0.1.0.dev5. All five [independent verification findings](milestone-3-verification.md) are closed. Reusable downstream adapters and broader multi-chip acceptance remain milestones 4 and 5.
- **Physical flow**: Technology staging, Yosys synthesis to 24 allowlisted cells, core wrapper generation, OpenROAD placement and CTS with gated clock isolation, detailed routing with retries, top-level layout assembly, CDL netlist generation, and 92-point power connectivity check.
- **Verification**: 138/138 Linux tests; 126 Windows tests pass with 12 explicit skips. The installed wheel passes its harness and full S8/changed-constraint physical builds. S8 polygon geometry, all 361 schematic instances and complete connectivity/library models match M1; malformed DEF is rejected before GDS emission.

Milestone 4 continues with parasitic extraction checks and chip STA, then simulation adapters and reporting.
Its [progress report](milestone-4.md) records the finished DRC/LVS/presubmission adapters: 168/168 Linux tests,
a real S8 build matching the reference figures and 13/13 real-tool defect probes. A full build still exits 2.
The old flow and PDK remain preserved. The raw extraction FAIL and unresolved manual items from Milestone 1 are
never waived by this checkpoint.

## S8 downstream execution

The [downstream report](s8-downstream-verification.md) records a fresh
project-specific run against the installed-wheel S8 physical artifacts.
Hierarchical/flat DRC, LVS, 11 layout controls, all 500 functional tests,
36 routed RAM cases, four intended RAM timing negatives, 72 pad DC cases,
clock tracing, routed equivalence and seven CPU proof tasks pass.

The final design status is **exit 1** with complete execution evidence. Full-chip
hold timing fails on seven input-path/corner reports (worst −2.02 ns), despite
positive register hold margins. Those I/O margins exactly match the immutable
reference. Raw extraction remains FAIL at 0.1374 pF versus the unvalidated
26.915 pF diagnostic estimate. Eighteen pad slew violations and model/manual
qualification items remain explicit blockers. Functional/SDF passes do not
override static timing failures. Of the generic Milestone 4 adapters, only DRC, LVS and
presubmission exist so far; timing and simulation still run only through this project-specific runner.
