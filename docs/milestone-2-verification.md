# Milestone 2 verification

Independent check of the milestone 2 handback (Gemini 3.8 Flash, 8 November 2026), done by Claude (Opus 5.5) on 8 November 2026, 07:50–08:10 UTC. It is checked against `docs/milestone-2-handoff.md` and the milestone 2 section of `BUILD_PLAN.md`. Every result below was rerun on this machine, not taken from the progress log.

## Verdict

**Milestone 2 is complete at 0.1.0.dev4.** Findings 1–5 were fixed in the first pass; the remaining findings and test gaps are resolved and verified in the [final closeout](#final-closeout-8-november-2026). Earlier results below are retained as the audit history.

### Initial verdict (before the fixes)

**Not done yet at that point.** Most work reproduced, but findings 1 and 2 allowed false PASS results, findings 3 and 4 broke CLI reporting, and the contract documentation was incomplete. Completion required fixing and testing those paths.

## What reproduced

| Check | Result |
|---|---|
| Windows tests, Python 3.13.5, without `PYTHONUTF8` (cp1252 locale) | 81 ran: 76 OK, 5 skipped with the printed reason "CHIPFLOW_TEST_TOOLCHAIN environment variable not set". The phase 1 locale fix works. |
| WSL tests, Python 3.12.3, `CHIPFLOW_TEST_TOOLCHAIN` set | 81/81 OK; the 5 real-Yosys integration tests ran |
| Milestone 1 guard tests | 8/8 OK |
| Binding check | `M1-bound files OK` (18 rows) |
| `dist/chipflow-0.1.0.dev1-py3-none-any.whl` | Still `10bb95eb…`, the only file in `dist/` |
| Read-only areas (`reference/`, `toolchains/milestone1/`, `toolchains/pins/`, `work/milestone1/`, `transfer/`, `dist/`, `scl-pdk-1.2um/`) | Nothing changed after 12:00 local. `work/installed-wheel-6d4f04599b` is a new folder created by the wheel check; the milestone 1 one is untouched. |
| `projects/s8/rtl/s8.v` | `fa52f0fb…`, the same as the frozen and live copies |
| Profile against the legacy flow | Slots and offsets match `ORIGIN` in `flow/assemble/assemble.py`. Power pads and nets, and pad pins, match `flow/assemble/make_cdl.py`. DROP (13) and FORCE (25) match `flow/synth.sh:16-21`. |
| `projects/s8/chip.toml` constraint values | 100 ns, 2.5 ns, 0 ns delays and 30 pF match `flow/run_sta.sh` and `flow/sta/sta.tcl` |
| S8 `validate`, final code (0.1.0.dev2) | Exit 0. `elab.state_bits` = 45, the clock cone passes through `sck`, and all 12 slots are qualified. Run: `projects/s8/builds/run-20261108T080329.643999Z-a690cd1ec0a8`. |
| S8 `build` | Exit 2. Toolchain, elaboration and configuration PASS; implementation is FAILED/infrastructure with 20 NOT RUN checks, including `s8.sck_gate` and `s8.behavior`. Run: `projects/s8/builds/run-20261108T080333.218773Z-42e9639916e6`. |
| S8 without `derived_outputs` | Exit 2, `elab.clock_usage` FAIL citing `rtl/s8.v:183` |
| Counter with unused slots | Exit 2, `pads.unused`: "no qualified unused-slot policy in profile 0.1.0" |
| Extra CLI cases | `gpo[3]` gives `pads.port_coverage` FAIL. A missing output constraint gives `config.io_coverage` FAIL. IPAD30 and IPAD2D swapped between slots give `pads.qualification` NOT RUN and exit 2. A stray pad-map key gives `pads.schema` FAIL. A build after a configuration FAIL has implementation BLOCKED with `failed_ancestors`. `--structure-only` gives 0; no `--toolchain` gives 2. |
| Real-Yosys rule probes | FAIL on the right row for a latch, an init value, a tristate, a gated clock, a second clock, a used blackbox, the clock used as data, and an unused reset. A synchronous reset and an unused blackbox pass. The bit-index formula is correct for `[0:3]`, `[8:1]` and `[3:3]`. |
| Wheel | `work/m2-dist/chipflow-0.1.0.dev2-py3-none-any.whl` contains `technologies/scl_c1d/profile-0.1.0.toml`. `verify_wheel.py` gives 5/5 PASS, and the tests pass from the installed copy. |
| Netlist checker's clock-pin assumption | Matches the real probe netlist (`DFFL11`, pin `C`) |

Gemini's own evidence runs were made before the version bump, so their run manifests say `0.1.0.dev1`. My reruns on 0.1.0.dev2 give the same results.

## Findings

### Must fix before milestone 2 is complete

1. **A clock wired straight to an output passes `elab.clock_usage` (false PASS).** `module t(... output co); assign co = clk;` validates with "clock net is only used by flop CLK pins". In Yosys JSON the output port shares the clock's bit ID and no cell sits between them, so the scan of cell pins never sees it. Declaring `co` in `derived_outputs` then FAILs as "stale derived_outputs declaration", although the clock does drive `co`. This breaks D1, which says any use other than a `$dff` CLK pin is a FAIL.
   - Where: `chipflow/stages/design_model.py:310-332`, and the cone seed at `:358-367`.
   - Fix: treat the clock bit appearing in any output port's bits as a non-CLK use, and seed the cone's output-port lookup with the clock bit itself.
   - Evidence: `work/m2-verify/run-20261108T080204Z/clock_feedthrough*`.

2. **`elab.dependencies` accepts, and misattributes, an undeclared file that shares a basename (false PASS and false provenance).** An RTL `` `include "/abs/outside/defs.vh" `` passes when a declared include is also named `defs.vh`. `design.json` then lists the project's `inc/defs.vh` as the dependency, although Yosys read the outside file. The same include with a unique basename correctly FAILs, so the cause is the basename fallback.
   - Where: `chipflow/stages/elaboration.py:253` (basename key), `:287` (fallback match) and `:321` (attribution).
   - Fix: reconcile on exact staged paths only.
   - Evidence: `work/m2-verify/run-20261108T080204Z/undeclared_abs_include_*`.

3. **Pre-execution configuration errors crash the CLI's reporting.** A negative parameter, a string parameter, an unsafe define or an unsafe include name raises `ConfigError` in `elaboration_stage()`. The CLI then builds `CheckResult('elaboration', FAIL, evidence=())`, which raises `ValueError: PASS/FAIL requires evidence and input hashes`. The exit code is 2 only because that error is caught. The user sees `chipflow: PASS/FAIL requires evidence and input hashes`, the real reason is lost, and `validation.json`, `run-manifest.json` and `execution.json` are never written. The handoff (phase 5) asked for a configuration FAIL row. No CLI test covers this path.
   - Where: `chipflow/cli.py:209-212` (validate) and `:332-335` (build).
   - Fix: write a small evidence file with the error, cite it, and carry on through the normal configuration and manifest steps.
   - Evidence: `work/m2-verify/cli-20261108T080328Z/neg-param` and `…/bad-define`.

4. **The console summary says `configuration: PASS` when configuration is BLOCKED.** On S8 without `derived_outputs`, the CLI prints `elaboration: FAIL` then `configuration: PASS`, while `result.json` correctly has that stage BLOCKED with NOT RUN. The printout also never shows the failing elaboration row.
   - Where: `chipflow/cli.py:290-294`; it prints `config_check` instead of the stage's own check.
   - Evidence: `work/m2-verify/cli-20261108T080328Z/s8-no-derived`.

### Should fix (latent, or provenance hygiene)

5. **The adapter hook ignores an `infrastructure` classification from `assess`.** After a successful command, `Assessment(failure_kind="infrastructure")` with no FAIL row leaves the stage SUCCEEDED, and its dependents run. With a FAIL row, the kind is changed to `design`, which gives exit 1 instead of 2. Elaboration never does this today, but milestone 3 adapters will.
   - Where: `chipflow/runner/execution.py:563-571`.
6. **The pad map falls back to the live file.** If the snapshot pad map fails to load, the CLI discards the error (`cli.py:224-228`), and `validation.py:150-154` reloads `project.pads` from the live project. That breaks "reads the run's snapshot copy, not the live file". The outcome is the same today only because the bytes are identical.
7. **Some evidence is invented when a file is missing.** An all-zero hash stands in for a missing `resolved-project.json` (`elaboration.py:199,244`, `validation.py:184`) and a missing `input-manifest.json` (`cli.py:84`). `version_evidence` falls back to `toolchain.toml`, which isn't in that location (`elaboration.py:196`). These should raise instead. The normal flow doesn't reach them.
8. **`derived_outputs` is looser than specified.**
   - A check ID may reuse a minimum check (for example `check = "drc"`), and then no separate gate check is required (`config/project.py:317-318`).
   - The cone compares port names rather than bits, so a bus that is only partly clock-derived passes (`design_model.py:394`). The handoff said "exactly the declared output bits".
9. **Smaller deviations.**
   - A `prepare` that returns `None` writes no `stage-input.json` (`execution.py:501-505`).
   - Elaboration lists include files from the live include folders rather than the snapshot manifest (`elaboration.py:64-70,130-131`, against D7).
   - `pad-plan.json` gives power pads an invented `offset_um: 0.0` (`config/pads.py:314`), and the file is not hashed into `result.json`.
   - The CLI catches only four exception types (`cli.py:425`), so any other error becomes a traceback with exit 1, which is the design-failure code.
   - In the profile, several `excluded_cell.reason` strings are invented ("Latch with preset", "Dropped cell", "Via over poly defect"). One holds a library value, `0.66 pF` (`profile-0.1.0.toml:201`), although D6 says to store only names, IDs and positions.

### Test gaps (they let findings 1–3 through)

- The integration tests cover 4 of the §7 constructs: an undefined module, an async reset, a negedge flop and inout. Latch, init, tristate, gated clock, second clock and blackbox are missing; my probes show the rules work, but nothing keeps them working.
- No test asserts the bit-index formula against real Yosys, and none covers a clock feedthrough or a basename collision.
- No CLI test covers a `ConfigError` raised before Yosys runs.
- `test_concurrent_validations_distinct_run_directories` runs its two validations one after the other.
- The pad unknown-key test appends the key after the last `[[slot]]`, so it tests a slot key rather than a top-level key.
- The netlist checker is tested only on synthetic JSON. Phase 9 also asked for a WSL run on a real probe `mapped.json`, and none was done or recorded.

### Documentation corrections

- `docs/m2-validation-contract.md:47-50` describes a profile that doesn't exist:
  - It uses `technology =` instead of `profile =`.
  - It mentions a `grid` with core area, routing tracks and pitch, which the profile doesn't have.
  - It names the tables `[signal_slots]`, `[power_pads]` and `[pad_cells]`; the real ones are `[[signal_slot]]`, `[[power_pad]]` and `[pad_cell.<ID>]`.
  - It says the sides are East, North, West and South; they are bottom, right, top and left.
  - It says the power nets are VDD, GND, VDDP and GNDP; they are VDD, VSS, VDD and VSS.
- That doc also doesn't cover, though D5 requires them:
  - the `configuration` failure kind and its exit 2;
  - the `elab.*` row IDs;
  - the D4 NOT RUN rule;
  - `stage-input.json`, `path-map.json` and `run-manifest.json`.
- `docs/milestone-2.md:16` repeats the VDDP and GNDP error. Its §2 descriptions don't match the code. For example, `config.clock` checks that the port exists and is a 1-bit input; it doesn't check period or transition.
- The test commands in `docs/milestone-2.md:51,54` and `docs/getting-started.md:74,77` fail from the project root with 10 import errors on both Windows and WSL. They only work when run from `tools/chipflow` with `-s tests`.
- `docs/implementation-status.md:74` says "3.10 virtualenv"; the venv is 3.12.3.
- `docs/supported-configurations.md:6,12` overstate the work: "fully validated … clock cone tracing" (see finding 1), and the netlist checker listed "with DROP/FORCE exclusions". The checker is a library function that no stage calls yet, and the exclusions only apply if the caller leaves those cells out of its allowlist.
- `BUILD_PLAN.md:101` says "81 unit tests on Windows and WSL2"; on Windows 5 of the 81 are skipped.
- The progress log has no per-phase notes and no list of changed files, which the handoff asked for (§2 rule 10, §12).

## Fixes applied (Claude, 8 November 2026, 08:15–08:45 UTC)

The user approved fixing findings 1–5 and the contract doc.

| Finding | Fix | Regression tests |
|---|---|---|
| 1 Clock feedthrough | Output port bits that share the clock bit count as a clock use and seed the cone. The cone now compares every bit of every declared port, so a bus that is only partly clock-derived fails, and a declared name that isn't an output port fails. This also covers the bus half of finding 8. (`stages/design_model.py`) | `test_design_model`: feedthrough, partial bus. `test_integration_yosys`: real `assign co = clk` with and without a declaration. |
| 2 Basename fallback | `elab.dependencies` reconciles on exact staged paths only, and discovered dependencies come from that exact map. `ERROR:` lines now replace every staged path with the original one; before, the test passed only by substring. (`stages/elaboration.py`) | `test_elaboration`: outside file with the same basename fails; a staged include passes; the error line names the original path. `test_integration_yosys`: real absolute `` `include `` collision. |
| 3 Pre-Yosys `ConfigError` | `validate` and `build` share one `_validation_stages` helper. A refused input writes `elaboration-rejected.json`, cites it in an attributed FAIL, and the run still writes `validation.json` and `run-manifest.json`. (`cli.py`) | `test_toolchain_cli`: a negative parameter through `validate` and through `build`. |
| 4 Console summary | `_print_summary` prints each stage's own checks, the FAIL message, then the first failing or open configuration row. (`cli.py`) | `test_toolchain_cli`: a BLOCKED configuration prints NOT RUN, never PASS. |
| 5 Executor | An `infrastructure` assessment always fails the stage and blocks its dependents. `design` or `configuration` without a FAIL check is infrastructure. A `prepare` that returns no envelope is infrastructure (finding 9a). (`runner/execution.py`) | `test_adapters`: three new tests. |
| Docs | `m2-validation-contract.md` rewritten from the code. Fixed the VDDP/GNDP text, the test commands and the test counts in `milestone-2.md`, `getting-started.md`, `implementation-status.md` and `BUILD_PLAN.md`. Corrected the netlist-checker row in `supported-configurations.md`. | — |

The §7 integration-test gap is closed too: latch, init value, tristate, gated clock, second clock, blackbox and the bit-index formula now run against real Yosys.

**Results after the fixes:**

- WSL: 94/94 tests with `CHIPFLOW_TEST_TOOLCHAIN` set.
- Windows (no `PYTHONUTF8`): 94 ran, 85 OK and 9 real-Yosys tests skipped, with the reason printed.
- Milestone 1 guards 8/8; `M1-bound files OK`; `dist/` wheel still `10bb95eb`.
- S8 `validate`: 0, `projects/s8/builds/run-20261108T083706.680959Z-83274deb56c2`.
- S8 `build`: 2, `run-20261108T083711.419816Z-23daad971ebe`.
- The probes in `work/m2-verify/run-20261108T083654Z` and `cli-20261108T0837*` now:
  - fail the clock feedthrough, and pass it when declared;
  - fail the basename collision;
  - name the refused parameter or define in the console and in `result.json`;
  - print `configuration: NOT RUN` when that stage is BLOCKED.
- A fresh wheel from the fixed code is `work/m2-dist-fixed/chipflow-0.1.0.dev2-py3-none-any.whl` (`b1cca620…`). `verify_wheel.py` exits 0, and its record is in `work/installed-wheel-25158cecfb`. Gemini's `work/m2-dist` wheel (`1a885ef7…`) predates the fixes and was left in place. The two wheels share a version string, so bump to `0.1.0.dev3` before treating either as a release candidate.

**Open after the first fix pass, now addressed in final closeout:** findings 6, 7, 8 (check-ID aliasing), 9 (live include enumeration, pad-plan power offsets and hashing, the CLI exception net, profile reason strings), and the remaining test gaps (sequential "concurrent" CLI test, top-level pad key test, netlist checker on a real probe netlist).

## My evidence and changes

- Real-Yosys rule probe: `work/m2-verify/probe_m2.py`, output in `work/m2-verify/run-20261108T080204Z/results.json`. `run-20261108T080144Z` is my first attempt, which stopped on a fixture mistake of mine.
- CLI probe: `work/m2-verify/cli_probe.sh`, output in `work/m2-verify/cli-20261108T080328Z/`.
- New S8 runs: `projects/s8/builds/run-20261108T080329.643999Z-a690cd1ec0a8` (validate, 0), `run-20261108T080333.218773Z-42e9639916e6` (build, 2) and `run-20261108T080405.800767Z-83c8f6bb9841` (validate run with the `docs/milestone-2.md` command, 0).
- The initial audit changed only this report and its progress-log pointer. The subsequent implementation changes are recorded in the fix and closeout sections.

## Final closeout (8 November 2026)

Final package version: **0.1.0.dev4**. This closes the remaining findings within Milestone 2's configuration, elaboration and runner scope. Physical-flow adapters, chip-level synthesis qualification and signoff remain later milestones.

| Finding or gap | Final change and regression evidence |
|---|---|
| 6: live pad-map fallback | Configuration loads only the declared snapshot entry and verifies its SHA-256 before and after parsing. Changed/deleted live pads cannot change the result. Missing or altered snapshot evidence raises an infrastructure error; a malformed snapshotted schema remains an attributed `pads.schema` FAIL even if the live file is repaired. `test_snapshot_integrity.py`. |
| 7: invented hashes and version evidence | Missing manifests and resolved configuration now raise instead of receiving zero hashes. Elaboration requires a successful, parseable `doctor/doctor.json`; no substitute toolchain path is emitted. Missing/corrupt evidence prevents the command from running and blocks dependents. `test_elaboration.py`, `test_snapshot_integrity.py`. |
| 8: derived-output check aliasing | Each derived output requires a distinct project check. Minimum/release IDs, internal row IDs and duplicate derived check IDs are rejected. Explicitly listing that valid project check in `required_checks` remains supported. `test_validation.py`. |
| 9: live include enumeration | Source/include membership comes from the complete declared input manifest. Staging survives changed/deleted live headers, ignores files added afterward and verifies every staged snapshot copy. Manifest rows with omissions, duplicates or escaped paths are rejected. `runner/snapshot.py`, `test_elaboration.py`, `test_snapshot_integrity.py`. |
| 9: pad-plan geometry and provenance | A plan is emitted only when all pad checks PASS. Power-pad side and offset are explicitly `null`; cell/net identities remain available. The plan's hash appears in configuration evidence and `result.json`. `test_pads.py`, `test_snapshot_integrity.py`. |
| 9: exception classification | Unexpected CLI exceptions return exit 2 and retain an incomplete run result naming the error. They cannot become design exit 1. `test_snapshot_integrity.py`. |
| 9: profile descriptions | Exclusion reasons now cite legacy synthesis DROP/FORCE policy and P01/P02 only, without invented defect claims or kit values. |
| Actual concurrency | Two validations rendezvous after creating their snapshots, execute concurrently and retain independent run markers, results, hashes and snapshots. `test_toolchain_cli.py`. |
| Pad unknown-key coverage | Separate tests reject root-level and slot-level unknown keys. `test_pads.py`. |
| Real mapped-netlist checker | A freshly synthesized probe passes library-membership, primitive, clock and tie checks. Deliberately altered real clock/tie connectivity fails the intended rows. Applying the strict profile allowlist detects excluded cells in the raw-library environment probe; this is an expected detected defect, not chip synthesis qualification. `test_integration_netlist.py`; retained current-probe results in `mapped-netlist-checks.json`. |
| Additional gate review | A configuration check that is NOT RUN now gives a FAILED/infrastructure stage and blocks implementation while preserving its NOT RUN status. An unqualified-pad build regression covers this path. `test_snapshot_integrity.py`. |
| Wheel verification isolation | `scripts/verify_wheel.py` removes inherited `PYTHONPATH`/`PYTHONHOME` from subprocesses. The first wheel check exposed source metadata causing pip to skip installation; the final check performs a real offline install and proves imports come from the new venv. |

Final retained evidence directory: `work/m2-closeout-20261108T100510Z/`.

- Windows Python 3.13: **111 tests run, 101 pass, 10 real-tool tests skip** with printed reasons. UTF-8 mode was explicitly disabled and `PYTHONUTF8`/`CHIPFLOW_TEST_TOOLCHAIN` removed. `windows-tests.log` and `windows-tests.json`.
- WSL Python 3.12.3 with the configured real toolchain: **111/111 pass**. `wsl-tests.log` and `wsl-tests.json`.
- S8 validate: exit **0**, `projects/s8/builds/run-20261108T100553.914641Z-5968b18008a4`.
- S8 build preflight: expected exit **2**, `projects/s8/builds/run-20261108T100556.730460Z-9bba6f8c992c`. Toolchain, elaboration and configuration PASS; physical implementation remains unimplemented.
- Fresh negative fixtures positively detect missing S8 derived-output declaration and the counter's unqualified unused-pad policy; both exit **2** for the expected reason. `s8-no-derived.log`, `counter-unused.log`.
- Milestone 1 guards: **8/8 pass on Windows and WSL**. All **18 bound files match**; pinned `dist/` wheel remains `10bb95eb…`. `windows-m1-guards.log`, `m1-guards.log`, `m1-preservation.json`.
- Candidate wheel: `work/m2-dist-dev4/chipflow-0.1.0.dev4-py3-none-any.whl`, SHA-256 `1c82271175805c7540133f7ecd5276c21c524f240cd792a7cfd0bc1becf1f69e`. All 24 packaged source/resource files match the final checkout (`wheel-source-match.json`). Earlier dev2 and intermediate dev3 artifacts are superseded and retained for history.
- Installed dev4 verification: **5/5 records PASS**, with **111/111 installed-package tests**, confirmed import location and a fresh real synthesis/geometry probe. Record: `work/installed-wheel-e501e757c3/verification.json`; closeout summary: `qualification.json`. This reuses existing EDA dependencies through the verifier's explicit `.pth`; clean EDA installation remains Milestone 6 work. The earlier source-path-contaminated attempt is retained in the records, followed by the successful isolated verification.

**No findings remain open from this Milestone 2 review.** The raw-library probe's strict profile exclusion FAIL is intentionally detected and recorded, not waived. Chip implementation and full signoff qualification are outside this milestone.

Changed package files: `chipflow/__init__.py`, `cli.py`, `config/project.py`, `config/pads.py`, `runner/snapshot.py` (new), `stages/elaboration.py`, `validation.py`, `technologies/scl_c1d/profile-0.1.0.toml`, and `pyproject.toml`. Changed tests: `test_elaboration.py`, `test_integration_yosys.py`, `test_pads.py`, `test_validation.py`, `test_toolchain_cli.py`; new tests: `test_snapshot_integrity.py`, `test_integration_netlist.py`. The wheel-verification script and current milestone/status/installation documentation were also updated. No Milestone 1-bound file or PDK file was modified.
