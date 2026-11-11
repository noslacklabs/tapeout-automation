# Milestone 3 independent verification — 8 November 2026

## Closeout — 0.1.0.dev5

**The five verification findings are fixed. Milestone 3's documented S8 physical
acceptance passes.** Reusable verification and signoff qualification remain
incomplete until the milestone 4/5 adapters and qualification run.

The subsequent [S8 downstream execution](s8-downstream-verification.md) is
complete: DRC/LVS and functional checks pass, while input hold and raw extraction
remain FAIL. It preserves signoff_ready=false and does not close Milestone 4.

| Check | Final result |
|---|---|
| Linux, installed package, real toolchain enabled | 138/138 tests PASS |
| Windows without EDA dependencies | 126 PASS, 12 explicit integration/geometry skips; CLI help works |
| Installed wheel harness | 5/5 records PASS |
| Full S8 build from the installed wheel | All physical stages PASS; 4 canonical artifacts; expected exit 2 |
| Requested fresh S8 smoke rerun | All physical stages PASS, exit 0; 0 routing violations/overlaps and all 92 power probes pass |
| S8 layout against M1 | No polygon differences on any layer; one 2,000 × 2,000 µm top |
| S8 schematic against M1 | All 361 instances/types, complete net-to-pin incidence, external nets and emitted library models match |
| Changed-timing project from the installed wheel | Physical stages PASS; saved placement/routing SDC contains the changed waveform, uncertainty and I/O delays |
| Malformed DEF with a valid producer fingerprint | Assembly FAILED before GDS emission |
| Current package files against built wheel | 38/38 match, no missing files |

The Windows tests use a short workspace temporary directory. The installed
package environment reuses recorded EDA dependencies; this is not a fresh
operating-system/EDA installation. The original physical result retains its
downstream NOT RUN states. Subsequent project-specific DRC/LVS, timing and
functional results are recorded separately in the downstream report.

### Fixes and regression coverage

1. Placement and routing read the synthesis-produced, fingerprint-verified
   implementation SDC and the selected core/pad libraries. OpenROAD writes
   placement.sdc and route.sdc as evidence of the constraints it consumed.
2. A shared bounded DEF reader validates units, grid, supported orientation,
   section counts, all component/pin/net records and every routing token.
   Assembly also reconciles the complete DEF inventory/connectivity with Yosys
   JSON before emitting layout.
3. CDL uses Yosys-resolved bit identities, preserving aliases, constants,
   escaped identifiers and distinct net names. Unknown bits, missing inputs
   and inconsistent inventories fail. Qualified unused outputs get unique
   floating nets. Original PDK subcircuit definitions, including parameter
   defaults, remain intact; conflicting duplicate model definitions fail.
4. The executor checks producer artifact identities before consumers run;
   shared copy helpers verify the recorded hash and staged bytes. Technology
   records the raw PDK views used downstream. P&R consumes the verified pad plan
   without reloading live project files. The smoke script now runs the same
   preflight/configuration gates.
5. KLayout loads only when a geometry check executes. Nonphysical CLI/tests
   need no EDA dependencies, and the optional physical extra declares the
   pinned KLayout/gdstk requirements.

Coverage adds 24 tests to the original 114, including a real-Yosys alias case,
model-header preservation, malformed layout records, artifact drift and changed
constraints. Independent final comparisons also verified model preservation,
rather than relying only on the implementation's own checks.

### Final evidence

- S8 run: projects/s8/builds/run-20261108T145307.485099Z-502c9d6bea7e
- Fresh smoke rerun: work/m3-s8-smoke-20261108T180041Z/run; log work/m3-final-20261108/s8-smoke-retry.log
- Changed constraints: work/m3-final-20261108/constraint variant µ/builds/run-20261108T145328.398312Z-a0a8404c74b4
- Package verification: work/installed-wheel-2fd9e73250/verification.json
- Wheel: work/m3-final-20261108/wheel-dist/chipflow-0.1.0.dev5-py3-none-any.whl
- Wheel SHA-256: 301bba242c354ae64bc4dfaf0ccd094d54ba4947d3dd7a61307af7a11f221bbc
- Commands, logs, sealed-negative inputs and comparisons:
  work/m3-final-20261108/

The final comparisons are baseline-comparison.json and
schematic-comparison.json; closeout-probes.json records installed-build,
constraint-variant and malformed-layout outcomes. Earlier unsuccessful attempts
remain in work/m3-fixes-20261108/ and work/m3-closeout-20261108/.

The sections below retain the initial 0.1.0.dev4 review and its evidence.


## Initial verdict — 0.1.0.dev4

**Milestone 3 is not complete against `BUILD_PLAN.md`.** The S8 physical success
case reproduces, all 114 Linux tests pass, and the installed package passes its
existing verification harness. Five independently reproduced findings remain in
constraint consumption, DEF completeness, CDL aliases, artifact identity and
dependency-free CLI operation. Passing the existing suite does not cover these
failure paths.

Reviewed source: `5dc54ddcfbeb095270d2283b8367cdfb18c124ea`, package
`0.1.0.dev4`, initially clean working tree. At the initial review only documentation changed;
production implementation fixes were still open. Probe inputs and mutated artifacts
are confined to fresh directories under `work/m3-verification-20261108/`.

## Results that reproduced

| Check | Fresh result |
|---|---|
| Linux source suite with `CHIPFLOW_TEST_TOOLCHAIN` | 114/114 PASS, Python 3.12.3, Ubuntu/WSL |
| Real S8 CLI build | Technology, synthesis, routing, assembly and power checks PASS; process exits 2 |
| Synthesis | 245 mapped cells, 45 sequential cells, 0 direct constant ties; configured derived-output check PASS |
| Routing | 0 router violations, 0 unexpected overlaps, 1 attempt |
| Assembly | 345 cells, 1,386 fillers, 12 pad substitutions; 92 power points, 0 problems |
| Canonical artifacts | `chip.gds`, `chip.cdl`, `mapped.v`, `routed.v` all produced |
| Baseline geometry | No polygon differences on any layer against the immutable M1 GDS; both have one `OS0000` top, DBU 0.001 µm and `(0,0;2000,2000)` extent |
| Baseline CDL inventory | Top-level master histograms match: 345 core instances, 12 signal pads, 4 power pads |
| Fresh wheel | Built successfully; all 36 packaged source/resource files match current checkout bytes |
| Installed wheel harness | 5/5 records PASS, including 114/114 tests and the real doctor probe |
| Windows without EDA Python dependencies | FAIL: unittest reports 65 entries, 7 errors and 1 skip; `python -m chipflow --help` exits 1 |

Fresh build:
`projects/s8/builds/run-20261108T142135.864630Z-9f317905fc33`.
It correctly records `exit_code: 2`, `complete: false`,
`automated_verification: INCOMPLETE` and `signoff_ready: false`. DRC, LVS,
parasitics, timing and functional verification remain `NOT RUN`. Their absence
at this milestone is expected and is not one of the findings below.

The geometry comparison excludes text labels. CDL inventory agreement does not
establish connectivity equivalence; fresh DRC/LVS and timing acceptance were not
run. Counter/PWM physical qualification remains milestone 5 work.

## Findings

### 1. P1 — Placement and CTS ignore the resolved implementation constraints

`tools/chipflow/chipflow/stages/pnr.py:198` creates a new clock directly instead
of reading the synthesis stage's `impl.sdc`. Line 200 applies a fixed
`set_load 1.0 [all_outputs]`. The stage neither copies nor reads `impl.sdc`.
Clock waveform, clock uncertainty, min/max input/output delays and the resolved
pad models therefore do not reach the OpenROAD placement/CTS invocation.

An isolated preparation probe changed the S8 waveform from `[0,50]` to `[0,35]`,
uncertainty from 0 to 5 ns, input delays from `[0,0]` to `[2,12]` ns and output
delays from `[0,0]` to `[3,17]` ns. It also changed board loads from 30 to 20 pF.
The generated SDC changed, but `pnr.tcl`, `route.tcl`, `pnr_config.json`,
`floorplan.json` and `floorplan.tcl` remained byte-identical. Board load changes
can legitimately leave a pad's core-pin capacitance unchanged; the ignored
waveform, uncertainty and I/O delays independently establish this failure.

This fails milestone 3 item 8. Consume the same resolved constraints throughout
physical implementation, retain their identity, and verify their effect in
OpenROAD. Do not substitute a fresh partial constraint set.

Evidence: `work/m3-verification-20261108/probe-results.json` (`constraints`) and
the `constraints-baseline/` and `constraints-variant/` copies.

### 2. P1 — DEF records can disappear while assembly reports PASS

`tools/chipflow/chipflow/stages/assembly.py:58` appends only component records
that match its placement regular expression. Nonmatching records are skipped;
the declared `COMPONENTS` count is not reconciled. The wire-overlap parser has
the same omission at `stages/wire_overlap.py:37` and ignores unsupported route
tokens at line 164. Neither parser validates the DEF unit declaration.

In a fresh copy of the real routed S8 inputs, the probe increased the declared
component count from 345 to 346 and added:

```text
- must_be_rejected NO_SUCH_CELL + UNPLACED ;
```

The real assembly adapter completed with `stage_status: succeeded`,
`assembly: PASS` reporting **345** cells, and `power_connectivity: PASS`
reporting all 92 probes. It emitted GDS/CDL despite silently dropping the
additional component.

Smaller overlap-check probes also returned no problems for a missing declared
component, a missing declared net, an unplaced unknown component, `BADVIA`, and
a changed DBU declaration. These are parser-acceptance results; they are not
claims that every such input would pass all later stages.

This fails milestone 3 items 5 and 7. Reject every unsupported component, pin
and routing record; reconcile complete inventories and validate units,
orientations and grid before consuming geometry.

Evidence: `work/m3-verification-20261108/assembly-negative-result.json`,
`assembly-dropped-component/execution.json`, its retained inputs/outputs,
and `probe-results.json` (`def_rejections`).

### 3. P1 — CDL generation loses valid Verilog net aliases

`tools/chipflow/chipflow/stages/assembly.py:379` reparses `routed.v` with regular
expressions and emits nets from their textual names. It never resolves
`assign` aliases through a structured connectivity model. The structured model
used during synthesis does not feed this CDL conversion.

Running the production CDL-generation block against a synthetic mapped netlist
with `INVR04 u0 (.IN1(clk), .OUT1(n)); assign y_b = n;` produces:

```text
X0 clk n VDD VSS INVR04
XPAD1 y_b PAD1 VDD VSS OPAD04
```

The inverter output and pad input remain different CDL nets even though Verilog
connects them. This is the alias defect explicitly identified in the original
build plan, reproduced in the new engine.

This fails milestone 3 items 4 and 7. Generate CDL from reconciled structured
connectivity, including aliases, constants, escaped/bus names and collision-safe
net allocation. Check instance and pin coverage against the routed design.

Evidence: `work/m3-verification-20261108/cdl-alias/`, containing the synthetic
libraries, Verilog, exact extracted production conversion block and output CDL;
`probe-results.json` (`cdl_alias`).

### 4. P1 — Physical consumers accept artifacts that differ from producer evidence

`tools/chipflow/chipflow/stages/pnr.py:272` copies the mapped netlist directly;
assembly similarly copies predecessor files starting at `assembly.py:532`.
The execution runner checks predecessor success status but does not compare
consumed bytes with the producer's recorded artifact hashes. The shared
`stages/staging.py:138` helper also hashes the current source at copy time rather
than looking up the producer's emitted hash.

The probe retained the successful build's `result.json` and `execution.json`,
changed only the copied producer `mapped.v` by appending a comment, and prepared
P&R. Preparation accepted the changed bytes and recorded their new hash as the
consumer input. The recorded producer hash began `bc865a07`; the consumed hash
began `19408744`. No mismatch was raised.

This violates the existing stage contract: "A consumer must use the exact
artifact hash emitted by its producer". Rehashing arbitrary current bytes
establishes copy integrity, not producer identity. Check each consumed artifact
against its successful producer evidence before staging and reject drift.

Evidence: `work/m3-verification-20261108/probe-results.json`
(`dependency_drift`) and `dependency-drift/`.

### 5. P2 — Nonphysical CLI commands now require an undeclared EDA dependency

`tools/chipflow/chipflow/cli.py:23` imports assembly unconditionally, and
`stages/assembly.py:12` imports `klayout.db` at module load. The physical test
module likewise imports the overlap checker unconditionally. Meanwhile
`tools/chipflow/pyproject.toml` declares `dependencies = []`.

On the available Windows Python 3.13.5 environment without KLayout,
`python -m chipflow --help` raises `ModuleNotFoundError: No module named
'klayout'`. The documented dependency-free test invocation cannot load most
CLI-dependent test modules. This contradicts the milestone report's assertion
that pure-Python tests pass on Windows/Linux without dependencies.

Keep configuration, help and nonphysical tests usable without the physical
runtime, or explicitly define and enforce the changed dependency contract.
Physical requirements should be detected and reported by the appropriate
toolchain/doctor path, with supported installation instructions and test skips.

Evidence: `work/m3-verification-20261108/windows-cli-help.log`,
`windows-unit-tests.log` and `windows-summary.json`.

## Package verification and limits

Wheel: `work/m3-verification-20261108/wheel-dist/chipflow-0.1.0.dev4-py3-none-any.whl`.
SHA-256: `bbf8fe4b70a7acdadeb62fa5ef81e73558c57b1d42397540cc8ac357d59d6dd5`.
Installed verification: `work/installed-wheel-e7d0f12a3e/verification.json`.

The initial offline build attempt using the EDA venv lacked `setuptools`.
The wheel was then built successfully with the already installed Ubuntu
`setuptools 68.1.2` / `wheel 0.42.0`; no global packages were installed.

The installed verification uses a fresh package venv and explicitly reuses
existing EDA dependencies via a `.pth` file. Its 114 tests, import-location
assertion and real doctor probe pass. This is **not** a clean EDA installation
or a full chip build from the installed wheel; the harness records both limits.

## Retained evidence and closeout

All verification scripts, command records, logs and isolated fixtures are in
`work/m3-verification-20261108/`. `baseline-comparison.json` records geometry,
inventory, release artifact hashes and packaged-source comparisons.
`linux-summary.json` and `package-summary.json` retain initial failures as well
as the successful packaging route. `run_checks.py`, `run_probes.py`,
`run_assembly_negative.py` and `verify_baseline.py` record the exact methods.

The initial requirement was to close the five findings and add coverage of
their observed failure paths. The closeout above records those fixes. The working S8 success case and the passing
114-test suite remain valid evidence within their stated limits.
