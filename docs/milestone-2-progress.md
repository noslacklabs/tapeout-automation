# Milestone 2 Progress

Date: 2026-11-08
Operator: gemini-3.8-flash (Antigravity harness)

## Baseline (Verified 2026-11-08)

| Check | Result |
|---|---|
| Windows tests without `PYTHONUTF8` | 26/27 (`ExecutionTests.test_arguments_logs_and_nonzero_exit` failed due to child process encoding cp1252 writing `µ`) |
| Windows tests with `PYTHONUTF8=1` | 27/27 OK |
| WSL tests (`/home/sushant/.venvs/presubmit-check/bin/python`) | 27/27 OK |
| Milestone 1 guard tests (`python tests/milestone1/test_evidence.py`) | 8/8 OK |
| Milestone 1 binding check (`reference/milestone1/contract-manifest.json`) | M1-bound files OK |

## Phases

- [x] Phase 0: Orient & Baseline
- [x] Phase 1: Fix Windows locale test issue
- [x] Phase 2: `configuration` failure kind (D2)
- [x] Phase 3: Stage adapters, envelope, and diagnostic continuation (G1, G2)
- [x] Phase 4: Design model & checks (pure Python) (G3, G4)
- [x] Phase 5: Yosys elaboration adapter (G3, G6, D7, D8)
- [x] Phase 6: Profile & pad map (G5, D3, D4, D6)
- [x] Phase 7: Semantic validation (G4, D1)
- [x] Phase 8: CLI and provenance (G6, G7, D9)
- [x] Phase 9: Mapped netlist checker (G8)
- [x] Phase 10: Real-input runs in WSL
- [x] Phase 11: Docs, version bump, wheel check, handback

## Completion Summary (Verified 2026-11-08)

- **Unit tests**: 81 tests passing on WSL2 Ubuntu (0 failures); 76 passing / 5 skipped on Windows (0 failures).
- **M1 contract manifest binding check**: 18/18 files verified (`M1-bound files OK`).
- **M1 evidence guard tests**: 8/8 tests pass.
- **Wheel build & verification**: `chipflow-0.1.0.dev2-py3-none-any.whl` built into `work/m2-dist/` and verified with `scripts/verify_wheel.py` (5/5 records PASS).
- **Real S8 validation run**: `projects/s8/builds/run-20261108T073506.325648Z-86dd80335af2` (Exit 0: toolchain, elaboration, configuration PASS).
- **Real S8 build preflight run**: `projects/s8/builds/run-20261108T073612.417603Z-974e15752cae` (Exit 2: preflight passed, physical adapters deferred to Milestone 3, downstream checks NOT RUN).
- **Negative test 1 (S8 without derived outputs)**: `work/m2-scratch/s8-no-derived/builds/run-20261108T073648.471984Z-71fc8cbe404a` (Exit 2: elaboration failed on clock cone branch at `rtl/s8.v:183`).
- **Negative test 2 (Counter with unused slots)**: `work/m2-scratch/counter/builds/run-20261108T073844.945998Z-024af058de77` (Exit 2: configuration failed on `pads.unused: no qualified unused-slot policy in profile 0.1.0`).

## Verification (Claude, 2026-11-08)

Not complete yet. The tests, the S8 exit codes, the negative runs and the wheel all reproduce. But real-Yosys probes found two false PASSes:

- a clock wired straight to an output passes `elab.clock_usage`;
- `elab.dependencies` accepts an undeclared file that shares a basename with a declared one.

Two CLI reporting bugs were also found. Details, fixes and evidence are in `docs/milestone-2-verification.md`.

Later the same day, findings 1–5 were fixed with regression tests and the contract doc was rewritten:

- Tests: WSL 94/94; Windows 85 pass and 9 skip.
- S8: validate exits 0, build exits 2.
- A rebuilt wheel is in `work/m2-dist-fixed/`.

Findings 6–9 are still open. They are listed in the report.

