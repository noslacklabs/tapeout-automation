# Review of the reusable automation plan

Reviewed 7 November 2026. Private project material.

## Verdict

The architecture and bounded first-release scope are sound, but the initial plan had material omissions. `BUILD_PLAN.md` has been revised to address them. The revised plan is suitable to start implementation; it does not constitute validation of a new engine, a portable installation or chip signoff.

This review inspected the current scripts, checker configuration, S8 verification runners and status reports, and used the related project's existing graph as a documentation index. Two isolated synthetic probes exercised the existing DRC summary and CDL conversion scripts. No existing flow, RTL, PDK or build artifact was modified. The full physical flow was not rerun. The earlier 224-test checker run is prior evidence, not a test of the proposed engine.

## Findings and corrections

### R1 — High: a completed command or empty report could masquerade as verification

The plan required structured results but did not specify positive evidence that the expected checks actually ran. [drc_summary.py](<M:/My Projects/Tapeout Chip/flow/drc_summary.py:19>) accepts an XML report with no categories or items, produces a zero-marker summary and exits 0. The current [report generator](<M:/My Projects/Tapeout Chip/flow/report/make_report.py:87>) uses that zero count to select PASS. The runner also pipes stage output without propagating all underlying process failures: [run_all.sh](<M:/My Projects/Tapeout Chip/flow/run_all.sh:17>).

Correction: milestones 2 and 4 now require explicit process outcomes, report structure and coverage validation, expected rule categories, a nonempty LVS top-circuit comparison, checker error metadata, complete test counts and nonzero aggregate status for failures. NOT APPLICABLE requires evidence; it cannot substitute for missing results.

Probe performed: passed the following synthetic XML to the existing summary script with `--json`:

```xml
<report-database><categories/><items/></report-database>
```

Observed exit 0 and `{"total": 0, "items": []}`. The reusable adapter must reject this as incomplete verification. This probe does not allege that the recorded S8 DRC reports are empty.

### R2 — High: copying the existing converters would not preserve arbitrary supported connectivity

[make_cdl.py](<M:/My Projects/Tapeout Chip/flow/assemble/make_cdl.py:44>) extracts cell instances with a regular expression but does not resolve Verilog `assign` aliases. A synthetic example converted successfully while leaving an aliased input disconnected in the generated CDL.

Probe performed: used invented `INV`, `INPAD` and `OUTPAD` cells, two configured pads, and this netlist:

```verilog
module demo(input a, output y);
  wire alias_a;
  assign alias_a = a;
  INV u1 (.IN1(alias_a), .OUT1(y));
endmodule
```

Observed exit 0 and these generated connections:

```text
X0 alias_a y VDD VSS INV
XPAD1 PAD1 a VDD VSS INPAD
XPAD2 y PAD2 VDD VSS OUTPAD
```

The converter emitted no connection between `a` and `alias_a`. A later LVS run should expose this, but the converter must not silently alter the intended circuit. This does not establish an alias defect in the current S8 layout.

Correction: milestone 3 now requires structured connectivity, alias/constant handling, unique name mapping, parser completeness and unit/orientation checks. Milestone 5 includes independent connectivity and unsupported-record fixtures. Small parsers can remain only within a documented, checked format boundary.

### R3 — High: configuring final STA alone would leave implementation constraints inconsistent

[pnr.tcl](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:66>) fixes the clock to `clk`, the period to 100 ns and output loading to one common value. [make_report.py](<M:/My Projects/Tapeout Chip/flow/report/make_report.py:178>) also fixes its period to 100 ns. The initial plan emphasized configurable final timing but did not explicitly require the same constraint source across placement, buffering, CTS and reporting, or check coverage of all intended paths.

Correction: milestones 2–4 use one resolved configuration, translate chip and core timing boundaries, and require min/max I/O constraints, clock reachability, path/corner coverage and valid exceptions. Changing the clock or load must change the actual implementation/analysis inputs. Positive slack on a few reported paths is insufficient evidence.

### R4 — High: fewer active ports and alternative pads had no complete implementation policy

The physical [assembly](<M:/My Projects/Tapeout Chip/flow/assemble/assemble.py:136>) starts from a complete frame and changes the listed pads. The [CDL generator](<M:/My Projects/Tapeout Chip/flow/assemble/make_cdl.py:62>) describes only listed signal pads plus fixed power pads. Simply shortening the map for a counter can leave physical pads absent from the schematic. Wrapper generation also assumes particular pad polarities and pin names.

Correction: every physical slot is now explicitly active or unused under a validated profile policy, with complete GDS/CDL agreement and defined unused-input treatment. The pad allowlist records polarity, power and model pin mappings and compatible drivers. The counter must exercise fewer active ports than the frame provides. The exact unused-pad treatment remains an implementation validation task; the review does not invent a foundry-approved treatment.

### R5 — High: extracting only `flow/` would omit important S8 verification

The [continuation report](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:30>) records formal equivalence, clock tracing, delayed tests and RAM-model runs. Their runners live under `tb/` and `formal/`, outside the main build script. [run_sta.sh](<M:/My Projects/Tapeout Chip/flow/run_sta.sh:34>) runs a simpler smoke test and substitutes a behavioral flip-flop. It does not provide all the recorded delayed verification. The existing [SDF probe](<M:/My Projects/Tapeout Chip/tb/check_random_start_sdf.py:37>) measures an actual nonzero delay and checks annotation errors.

Correction: milestone 1 inventories the wider dependency set; milestone 4 preserves it as required S8 regression work. Delayed verification requires matched models, real annotation, correct corner/timescale selection and a nonzero-delay probe. Model substitutions must preserve timing arcs. Optional formal support for other projects cannot downgrade S8's existing equivalence requirement.

### R6 — High: software qualification and the known extraction failure conflicted

The initial definition of done required all required automated checks to pass while also preserving a known extraction FAIL. It did not specify whether that failure should affect the build exit code, block estimated diagnostics or block release of the reusable software. The [signoff checklist](<M:/My Projects/Tapeout Chip/reports/signoff-checklist.md>) also records unresolved pad/model limits.

Correction: the first release is explicitly a development tool. Actual failed checks remain FAIL and produce a nonzero full-build result. Diagnostic estimated timing can continue only from valid artifacts and retains its limitations. A separate software qualification harness checks the exact expected outcomes, including documented process/model failures. A versioned limitation manifest is required; unexpected failures and design defects cannot be broadly exempted. Independent extraction/model validation is still needed before claiming signoff capability.

### R7 — Medium: the milestone acceptance order had a dependency error

Milestone 3 originally required DRC/LVS and functional regression results, but the adapters producing those results were scheduled for milestone 4. That made the stated sequential acceptance inconsistent.

Correction: milestone 3 accepts physical-stage artifacts and structural invariants. Milestones 4–5 accept the complete verification results. The plan now includes a stage dependency outline and incremental integration runs, followed by a frozen engine/profile qualification across all three designs.

### R8 — Medium: environment reproduction and safe run isolation were under-specified

[env.sh](<M:/My Projects/Tapeout Chip/flow/env.sh:9>) depends on absolute PDK, Nix-store and venv paths. A fresh Python venv alone does not reproduce the EDA environment. [pnr.sh](<M:/My Projects/Tapeout Chip/flow/pnr.sh:12>) documents a tool problem with script paths containing spaces, and the existing runner clears previous stage directories under the configured build path.

Correction: pinned EDA reproduction starts at milestone 1 and is verified with an installed wheel in a clean WSL environment at milestone 6. The plan specifies tool-language path handling, complete input snapshots, external-file fingerprints, distinct run directories, process-group cancellation and atomic results. Tool setup and input provenance are release requirements, rather than implicit properties of the current machine.

### R9 — Medium: advertised support exceeded the tested interface boundary

The original plan allowed configurable resets, buses and pads without narrowing what the first release must support. The current [synthesis cell filter](<M:/My Projects/Tapeout Chip/flow/synth.sh:16>) removes asynchronous and latch cells; the wrapper and clock-tree routines also have a narrow interface.

Correction: v0.1 explicitly targets one external clock, synchronous reset, contiguous one-dimensional packed buses and a tested pad allowlist on the existing frame. Unsupported state/clock structures fail elaboration or mapping checks. S8's clock-derived output remains a specifically verified project feature. Counter and parameterized multi-file PWM acceptance exercise materially different interfaces before support is advertised.

## Remaining implementation decisions

- Select and prove the unused-pad policy for this kit.
- Establish the pinned toolchain recipe and complete process/model limitation manifest.
- Choose a structured connectivity/DEF implementation within the installed tools' verified capabilities.
- Validate core-boundary constraints and pad models for every supported pad combination.
- Implement and run the three-project qualification matrix, including the deliberate-failure cases.

These are now explicit, ordered tasks with acceptance criteria in [BUILD_PLAN.md](BUILD_PLAN.md). No tool installation, generalized flow implementation or new physical qualification was performed by this review.
