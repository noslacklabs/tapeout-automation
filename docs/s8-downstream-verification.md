# S8 downstream verification — 9 November 2026

**Downstream execution completed with design failures: exit 1, signoff_ready
false.** Physical and functional checks pass. Full-chip input hold timing fails,
and the known raw extraction defect remains FAIL. Pad electrical/model issues
remain unresolved.

This run verifies the repaired 0.1.0.dev5 S8 physical outputs using the pinned
tools and private copies of the immutable reference helpers. It does not
complete the generic Milestone 4 adapters or multi-project qualification.

## Inputs and retained evidence

- Physical run:
  `projects/s8/builds/run-20261108T145307.485099Z-502c9d6bea7e`.
- Downstream run:
  `work/s8-downstream-20261108-02/`.
- Machine-readable result: [result.json](../work/s8-downstream-20261108-02/result.json).
- Commands, attempts and fingerprints: [state.json](../work/s8-downstream-20261108-02/state.json),
  [verification-inputs.json](../work/s8-downstream-20261108-02/verification-inputs.json),
  [output-manifest.json](../work/s8-downstream-20261108-02/output-manifest.json).
- Reproducible project runner: [instructions](../projects/s8/verification/README.md).

All 61 emitted physical artifacts are checked against their producer hashes.
The copied reference sources, simulation models and all 254 PDK files are
checked again before collecting the final result. Physical outputs, the
immutable reference and PDK remain unchanged.

## Results

| Check | Result |
|---|---|
| Hierarchical and flat DRC | PASS: 72 expected categories each, zero markers |
| LVS | PASS: 22 matching circuits, including OS0000; 361 top-level subcircuits |
| Independent layout checks | PASS: expected top/extents and routed geometry; 11 defect/control cases behave as expected |
| Presubmit | 38 PASS, 0 FAIL, 8 WARN, 7 MANUAL; no tool errors |
| Routed equivalence | PASS: synthesis and proof subprocesses succeed, positive proof status/completion |
| Routed clocks and SCK structure | PASS: all 45 sequential elements clocked; expected SCK gate/cone/truth table |
| CPU formal | PASS: reset_hold, reset_release, bus, commands, isa, isa_cover, halt |
| Functional simulation | PASS: 500 tests across RTL, mapped startup seeds, routed zero-delay and three-corner SDF |
| Nonzero SDF probe | PASS: measured delays 1.308 / 2.539 / 3.027 ns match min / typ / max expectations |
| Routed external-RAM tests | PASS: 36 cases over corners, programs and 40/50/60% duty cycles |
| RAM timing negatives | PASS: all four deliberate 25 MHz cases detect timing violations |
| RAM process audit | PASS: 2 compiler and 40 simulator processes all exit 0; negative harness failure is intentional |
| Pad DC polarity | PASS: all 72 model measurements |
| Raw extraction units/connectivity | PASS: 361 routed nets, 985 pin connections; 32 omitted nets are isolated single-output pins |
| Raw extraction plausibility | **FAIL**: 0.1374 pF versus 26.915 pF diagnostic estimate, ratio 195.888 |
| STA setup | PASS under the declared diagnostic timing/model assumptions |
| STA hold | **FAIL**: seven input-path/corner reports have negative slack |
| Slew/capacitance/pulse checks | Core and pulse checks pass; six pad slew violations per corner remain WARN/MANUAL |

The 500 functional tests comprise 20 RTL, 100 mapped tests over five startup
seeds, 20 routed zero-delay, 60 routed SDF and 300 seeded routed SDF tests. Each
of the 15 seeded SDF runs checks all 45 startup bits, distinct vectors across
seeds and consistent vectors across corners. SDF covers all 357 expected
instances, including all 45 DFFs. No missing-annotation diagnostic is accepted.

Ten independent downstream evidence controls pass, including empty extraction,
missing/duplicate pins and units, missing timing paths, and a negative input
hold path alongside positive register hold timing. The eight existing
Milestone 1 evidence guards also pass.

## Timing failures and limits

The timing constraints come from the physical run's snapshotted `chip.toml` at
the board pads: 100 ns clock, 50% duty cycle, 2.5 ns input transition, zero
input/output delay and 30 pF output loads. Every corner includes 45 registers,
all five data inputs and all six outputs. All 66 input/output max/min report
groups contain constrained paths.

| Corner | Register setup | Register hold | Full-chip setup | Full-chip hold |
|---|---:|---:|---:|---:|
| min | 82.80 ns | 1.06 ns | 40.90 ns | **−1.10 ns** |
| typ | 78.75 ns | 2.17 ns | 37.43 ns | **−2.02 ns** |
| max | 75.74 ns | 2.60 ns | 34.76 ns | **−1.99 ns** |

Failing board-input hold paths are:

| Input | min | typ | max |
|---|---:|---:|---:|
| rst / PAD6 | −1.10 ns | −2.02 ns | −1.99 ns |
| gpi[0] / PAD8 | no violation | no violation | −0.12 ns |
| gpi[1] / PAD10 | no violation | −0.49 ns | −0.86 ns |
| gpi[2] / PAD12 | no violation | no violation | −0.31 ns |

These input/output margins exactly match the immutable M1 reports; see
[comparison](../work/s8-downstream-20261108-02/timing-io-reference-comparison.json).
The complete I/O review exposes a pre-existing failure. Register-only hold
results cannot qualify the full chip. The initial core-only summary is
superseded by the final result and complete `build/sta/timing.json`.

Review actual board minimum input delays and the intended timing relationship
for these inputs. If zero-delay synchronous inputs remain the requirement,
implementation changes must close the input hold paths before qualification.
The verification does not relax constraints or insert false paths to hide them.

Raw OpenRCX data are retained unchanged and remain electrically unvalidated.
STA/SDF use the separately labelled routed-length/LEF diagnostic estimate;
their successful tests do not validate extracted parasitics. The six output
pad slew violations per corner report 5.74 / 8.07 / 11.68 ns against the
modelled 2.50 ns limit. Their interpretation and the IPAD30 simulation stand-in
remain signoff blockers. Icarus SDF simulation does not enforce DFF setup/hold
checks and cannot override the static hold failures.

## Verification setup corrections

The first RAM attempt could not open program/expected-memory files: resolving
the short runtime symlink produced paths longer than the bench's 128-byte
filename fields. A private helper copy now preserves the short path. All 36
RAM positives and four intended timing negatives were rerun with raw process
logs retained.

The first seeded attempt passed its 300 functional tests but interleaved one
startup record with cocotb output. Icarus's unbuffered `-i` option preserves all
45 records. The rerun retains the original UDP/specify models, SDF arcs and
test expectations. The failed attempts, exact source transformation and
before/after fingerprints remain in the run directory.

A separate reference-contract audit finds 17/18 current files byte-identical
to the original M1 contract inventory. `docs/stage-contracts.md` already differs
in the existing committed version and was not edited during this verification;
see [audit](../work/s8-downstream-20261108-02/reference-contract-audit.json).
The frozen executable reference helpers used by this run match their source
fingerprints.

Final issued identifiers, approved source freeze, board assumptions, model
qualification and release review remain pending. The result preserves the
nonzero design status and reports generic Milestone 4 implementation as open.
