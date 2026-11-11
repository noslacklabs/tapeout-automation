# Milestone 4 progress — generalize verification and reporting

**Status, 10 November 2026: in progress. Item 2 of 7 is implemented and
verified; items 1 and 3–7 are open.** A full `chipflow build` still exits 2,
because the parasitic, timing and simulation checks are `NOT RUN`.
`signoff_ready` stays false.

The source tree is ahead of the verified 0.1.0.dev5 wheel. No new wheel has
been built or installed for this work yet; the version is unchanged until the
milestone closeout.

| Item in [BUILD_PLAN.md](../BUILD_PLAN.md#milestone-4--generalize-verification-and-reporting) | State |
|---|---|
| 1. Final STA from the shared constraints, timing coverage | Open |
| 2. DRC, LVS and presubmission adapters | **Done** |
| 3. RTL and routed simulation adapters | Open |
| 4. S8 SCK gate, RAM and smoke checks as project checks | Open |
| 5. Simulation-model limits and SDF annotation checks | Open |
| 6. Raw and estimated parasitics as distinct artifacts | Open |
| 7. Report, `chipflow report` and delivery bundle | Open |

## What item 2 adds

`chipflow build` now runs four stages after assembly. Each consumes the
assembled layout by its producer hash, runs in a fresh stage directory and
records the deck, tool and input fingerprints with its result.

| Stage | Check | Source |
|---|---|---|
| `drc` | `drc` | `stages/drc.py` |
| `drc_flat` | `drc_flat` | `stages/drc.py` |
| `lvs` | `lvs` | `stages/lvs.py` |
| `presubmit` | `presubmit`, `presubmit_warnings`, `presubmit_manual` | `stages/presubmit.py` |

The decks stay in the external kit. The profile's new
`[physical_verification]` table only names the deck variables, the hierarchy
mode line and the die size. No rule text is stored in this repository.

### Rules each adapter applies

Every adapter separates three outcomes. A complete report that shows a defect
is a design FAIL. A report that is missing, truncated, empty or inconsistent
supports no verdict: the check is `NOT RUN`, the stage is an infrastructure
failure and the build exits 2. Only complete evidence without a defect is PASS.

- **DRC.** The expected rule categories are derived from the staged deck's own
  active `output` calls, ignoring comments and string contents. The marker
  database must have the expected root and top cell and exactly that category
  set before markers are counted. A deck whose category names cannot be
  predicted is rejected. Flat DRC replaces the deck's single hierarchy-mode
  line in a recorded copy and is otherwise identical.
- **LVS.** The comparison must be nonempty, contain the top circuit once and
  match every circuit, net, pin, device and subcircuit pair. Any status other
  than `Match`, including `MatchWithWarning` and `Skipped`, is a FAIL. An empty
  comparison, a missing top circuit or a top with nothing matched is unusable.
- **Presubmission.** The checker's process exit, the exit code in its report,
  its tool-error list, the layout MD5 and its row inventory must all agree.
  The 53-row inventory is pinned for checker 0.1.0; another version is not
  interpreted. `presubmit` passes only with no FAIL row. WARN and MANUAL rows
  are reported as their own checks and never folded into that PASS.

### Engine change

`StageSpec.design_returncodes` lets an adapter declare the nonzero exits by
which its tool reports a completed verdict; the presubmission checker exits 1
when a rule fails. Outputs are still validated, and the assessment must then
contain a FAIL check. Without one, or for any other nonzero exit, the stage
remains an infrastructure failure.

### Choices made here

- `presubmit` is a required check, and a required check that is neither PASS
  nor FAIL makes the build exit 2. The checker always reports some WARN and
  MANUAL rows for this frame, so they are separate, non-required checks.
- `drc_flat` runs in every build but is not added to the required minimum in
  `runner/results.py`. A flat-DRC FAIL still fails the build.
- A DRC design failure blocks `presubmit`, because the checker reads the DRC
  report. The block is attributed to `drc` and the build exits 1.
- The staged layout gets a development file name,
  `<top cell>_<PROJECT>_<run date ddmmyyyy>_v0.gds`, to satisfy the checker's
  naming rule. These are not issued identifiers.

## Verification

| Check | Result |
|---|---|
| Linux, pinned tools, source tree | 168/168 tests pass |
| Windows without EDA dependencies | 156 pass, 12 skip with a stated reason |
| Milestone 1 evidence guards | 8/8 pass |
| Real S8 build | DRC and flat DRC: 72 categories, 0 markers. LVS: 22 circuits, 361 subcircuits. Presubmit: 38 PASS, 0 FAIL, 8 WARN, 7 MANUAL. Build exit 2 as expected |
| Real-tool defect probes | 13/13 behave as expected |

The S8 figures equal the Milestone 1 reference and the
[project-specific downstream run](s8-downstream-verification.md).

The 30 new tests in `tests/test_physical_verification.py` replace the tool
process with a script that writes planned files, so they need no kit. They
cover missing, extra, duplicate and empty rule categories, a wrong top cell,
truncated XML, markers outside declared categories, LVS mismatches and empty
comparisons, checker tool errors, exit-code disagreement, missing and unknown
rows, a report for another layout, and changed layout, deck or tool
fingerprints before the tool runs.

`scripts/m4_verification_probes.py` runs the real adapters, KLayout, decks and
checker on the retained S8 negative-control layouts:

| Defect | Adapter | Outcome |
|---|---|---|
| Unchanged copy | DRC, flat DRC, LVS, presubmit | PASS |
| Metal 1 near a row rail, core cell | DRC, flat DRC | FAIL, 2 and 1 markers |
| Metal 2 near the VSS ring, wires cell | DRC | FAIL, 1 marker |
| Flip-flop moved 1 µm | DRC | FAIL, 7 markers in 5 categories |
| Metal 2 merging with the ring (legal) | DRC | PASS |
| Routed via removed | LVS | FAIL |
| Signal shorted to VSS | LVS | FAIL |
| Schematic pins swapped | LVS | FAIL |
| DRC report with markers | Presubmit | FAIL on C19.2, checker exit 1 |

### Evidence

- S8 build: `projects/s8/builds/run-20261110T062259.689453Z-20baaa4ff8b1`
- Probes: `work/m4-dev/probes-01/probes.json`
- Linux test log: `work/m4-dev/tests-linux.log`
- Development environment: `work/m4-dev/venv`, a virtual environment whose
  path file puts this source tree ahead of the existing EDA site-packages. It
  is not a clean installation.

### Limits

- The probes reuse negative layouts made for the 8 November physical run. They
  were not regenerated from the 10 November build.
- The presubmission checker is identified by its version and a hash of its
  source files. That hash is recorded, not pinned in the toolchain file.
- `docs/stage-contracts.md` differs from its Milestone 1 fingerprint. This
  predates the work here and the file was not edited; the other 17 bound files
  match.
- Installed-wheel verification of these adapters is still to do.

## Next

Items 6 and 1 come next: raw extraction with unit and coverage checks, the
labelled estimate, then chip STA from the resolved constraints with a
timing-coverage check. The S8 run already shows what they must reproduce: raw
extraction plausibility FAIL and seven failing input hold paths.
