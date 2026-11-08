# Milestone 1 completed — 8 November 2026

All seven acceptance criteria are recorded in
[acceptance.json](../reference/milestone1/acceptance.json). This milestone captures
the reference, contracts and reproducible startup environment. It does not qualify
the future reusable physical engine or declare the chip ready for submission.

## Current immutable reference

Current S8 RTL SHA-256:
`fa52f0fb87289df5dd2f1f5e6004538fb513956fc834ad78556c39021a86ca46`.
The harness captured 177 source/data dependencies, retained an independent source
copy, recorded the exact environment/toolchain and staged legacy scripts in a
private tool-compatible directory. Prior source/build/PDK files were preserved.
If `/tmp` is evicted, retained sources and hash-checked dependencies restore the
staging paths; no changed live file can silently replace a frozen input.

[baseline.json](../reference/milestone1/baseline.json) validates the actual artifacts:

| Evidence | Observed result |
|---|---|
| DRC / flat DRC | 72 expected categories each, zero markers |
| LVS | 22 matched circuits; expected top; 562 nets, 127 pins, 162 devices and 361 subcircuit pairs matched |
| Router / metal overlaps | Accepted attempt 0, zero required router violations and unexpected overlaps |
| Power | 92 metal-only points checked, zero problems |
| Presubmit | 38 PASS, 8 WARN, 7 MANUAL; no tool errors |
| Deliberate layout defects / controls | All 11 expected results detected |
| Routed equivalence / clock tracing | Proof PASS; all 45 DFF clocks reach CLK through four buffer levels |
| RTL / routed tests | 20 RTL and 20 at each of three routed SDF corners |
| Seeded routed startup | Five seeds × three corners × 20 cases = 300; distinct seed vectors and corner consistency verified |
| SDF annotation probe | Nonzero measured C-to-Q 1.308 / 2.539 / 3.027 ns, matching selected min/typ/max SDF arcs |
| RAM | 36 routed positive cases; four 25 MHz negatives positively detected; actual 40 simulator + 2 compiler exits all zero |
| Pad polarity | 72 DC rail measurements pass; does not validate load/slew/current/hysteresis limits |

The legacy wrapper returned 0 despite its extraction FAIL. That return is retained
as historical execution behavior, not accepted as an aggregate verification
result. The new evidence checker positively reproduces the extraction signature:
0.1374 pF raw versus 26.915 pF diagnostic estimate, 361 matched nets and zero
unmatched nets. The design-status command returns **1**. Separate reference
reproduction qualification passes because the exact known failure is preserved
and no unexpected failure is accepted. The estimate remains electrically unvalidated.

The complete wider manifest contains ten regression groups. CPU formal tasks and
earlier mapped campaigns remain explicitly inventoried prior evidence; the new
baseline does not silently claim that all later release campaigns ran. The
source snapshot is an engineering reference, not the final user-approved silicon
freeze or human signoff.

## Fresh environment and contracts

The [pinned binary recipe](../toolchains/milestone1/README.md) retains a checked
Ubuntu Base rootfs, 46 Debian artifacts, 398 Nix runtime paths and 15 wheel pins.
It installs offline in fresh userspace without a mounted host store/home or reused
site-packages. A private PID namespace exposes only PID 1 during the isolation
test and supplies `/proc/self/exe` to Yosys. Configured versions/hashes, real SCL
tiny synthesis, both KLayout geometry interfaces/frame reading and Ngspice startup
pass. The initial missing-proc failure is retained. A clean WSL release installation
and independent source rebuild are not claimed.

[Stage contracts](stage-contracts.md), [project format](project-format.md),
[result policy](result-contract.md) and [workaround register](m1-workarounds.md)
define ownership, exact artifact/evidence boundaries and minimum checks before
physical extraction. The current structural loader is a subset of the semantic
validation promised by these contracts; implementing that validation is milestone 2.

Eight guard tests reject empty/truncated/changed DRC reports, missing/failed/skipped
or duplicate tests, invalid proof results and invalid parasitics. A crash cannot
count as the expected extraction failure or deliberate-defect detection.

## Reproduction and retained limitations

From the workspace in WSL, using the configured reference Python:

```bash
python scripts/m1_reference.py prepare
python scripts/m1_reference.py physical --state <new-run>/state.json
python scripts/m1_reference.py regressions --state <new-run>/state.json
python scripts/m1_reference.py ram-audit --state <new-run>/state.json
python scripts/m1_collect.py --state <new-run>/state.json --outcome qualification
python scripts/m1_collect.py --state <new-run>/state.json --outcome design
```

`prepare` prints the new run path. Every process command, environment override,
source/artifact hash and original-to-staged map is retained. The two final
commands distinguish expected-reference qualification (0) from failed design
verification (1). Missing/malformed evidence gives 2. Fresh-runtime reconstruction
has its own documented `capture`/`isolate` commands and pinned artifact bundle.

Raw evidence is under `work/milestone1/reference-20261107T202316Z-f546a6c1/`.
Reviewable bindings are in [reference/milestone1](../reference/milestone1/acceptance.json),
including source inputs, all artifact hashes, checker-source hashes, contract
hashes and [scope-bound limitations](../reference/milestone1/limitations.json).
Known pad/model/electrical and foundry-policy blockers remain visible. PDK/model
contents remain private local inputs; they are not bundled into a public example.
