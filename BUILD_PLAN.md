# Reusable chip automation — build plan

Prepared 7 November 2026 from inspection of the existing S8 physical flow and pre-submission checker. This is an implementation plan. Implementation has started; [docs/implementation-status.md](docs/implementation-status.md) distinguishes the runnable foundation from the still-pending physical workflow below. Keep this project and its PDK-derived material private.

Reviewed and revised 7 November 2026. [PLAN_REVIEW.md](PLAN_REVIEW.md) records the findings, source evidence and two isolated synthetic probes. Milestone 1 was completed 8 November 2026; [acceptance evidence](reference/milestone1/acceptance.json) distinguishes reference reproduction from the still-pending reusable-engine and release qualification.

## Objective and first-release boundary

Build an independent command-line tool that accepts a chip project and produces a reproducible build, GDS, netlists, verification results and a report. Adding a supported chip must require only that chip's RTL, configuration, constraints, pad mapping and tests. It must not require editing the shared engine.

First release: synchronous, single-clock digital standard-cell designs on SCL C1D 1.2 µm. Qualify WSL Ubuntu 24.04 on x86-64 with pinned tool versions first; portability to other Linux environments needs its own installation test. Initially qualify the existing 2×2 mm frame. Support multiple RTL files, contiguous one-dimensional packed buses, synchronous resets, configurable input/output pads from a tested allowlist, clock/reset port names, frequency and external loads. Reject unsupported constructs or configurations early, with an explanation.

The first release does not promise arbitrary analog design, embedded memory macros, multiple clock domains, internal generated/gated clocks, asynchronous-reset flops, latches, bidirectional I/O, another foundry or every die size. These become explicit additions after the reusable digital path works. S8's gated clock output is a project-specific exception with its own structure and timing checks; it does not qualify internal clock gating generally. Users supply and verify their circuit design; this tool automates its implementation and checks.

Completion of this software means that supported projects build through the same engine and results are trustworthy. Foundry acceptance and final chip signoff remain separately reported. In particular, packaging the existing extraction workaround does not validate its electrical accuracy.

The initial release is a development flow. The known raw-extraction failure must remain a failed check and a nonzero full-build result. A software qualification run can pass by demonstrating the correct expected failure and completing the other required evidence; it cannot relabel the chip build as fully passing. Resolving extraction and electrical-model limits is a separate prerequisite for a later signoff-capable release.

## Starting point

- `M:/My Projects/Tapeout Chip/flow/` already implements synthesis, placement/routing, assembly, DRC/LVS, timing, simulation and reports.
- `tools/presubmit-check/` is already an installable checker. Its 224 tests passed during this assessment, including the real-PDK integration tests.
- The existing flow has some parameters, but still embeds S8 names, a 2 mm frame, particular pad positions, `clk`/`sck` signals, RAM timing, CPU smoke-test output and local tool paths.
- The orchestrator currently exposes failures through reports without consistently turning every failed required stage into a failing process result. A reusable runner needs a defined result contract.
- Important S8 evidence lives outside `flow/`: `tb/` contains delayed simulations, RAM tests and clock tracing, and `formal/` contains equivalence checks. Inventory and migrate those dependencies too.
- The existing hand-written netlist/DEF/report parsers need validation before reuse. Synthetic review probes found that the DRC summarizer accepts a report with no rule categories and that the CDL converter loses a simple Verilog net alias.
- Preserve the existing S8 project as the reference implementation. Develop the reusable tool here and migrate a copy of the S8 inputs into a project adapter.

## Proposed organization

```text
Tapeout Automation/
  tools/
    chipflow/                 # Installable Python package and CLI
      chipflow/
        config/               # Schema, validation, resolved configuration
        runner/               # Stage dependencies, processes, result collection
        stages/               # Synthesis, P&R, assembly, checks, timing, simulation
        technologies/scl_c1d/  # Private process profile and documented workarounds
        reporting/            # JSON and readable reports
      tests/
  projects/
    s8/                       # S8 configuration, constraints and checks
    counter/                  # Small independent acceptance project
    pwm/                      # Multi-file design, different ports and constraints
  docs/
    getting-started.md
    project-format.md
    supported-configurations.md
    known-limitations.md
```

The existing pre-submission checker remains a separate package invoked by the flow. PDK files stay in their external installation; the process profile references and fingerprints them. Workarounds operate on recorded working copies.

Use Python for orchestration and validation, preserving the proven Python/Tcl geometry and EDA routines where practical. A small, explicit stage registry is sufficient; a general plugin marketplace, GUI and distributed job scheduler are outside the initial scope.

## Per-project interface

Use a versioned `chip.toml` file, with companion pad mapping and test files. The typed configuration is the source of truth for ordinary timing constraints; generate stage-specific SDC from it. Any additional reviewed SDC must be hashed, checked for conflicting definitions and included in coverage checks. It must specify:

- Project name, ordered RTL sources, include directories, defines, parameters and top module.
- Technology/frame profile, top-cell name and build mode; placeholder silicon identifiers are explicitly marked as development inputs.
- Clock port, period, waveform/duty cycle, transition and uncertainty; reset behavior; min/max input and output delays, per-output loads, timing corners and justified timing exceptions. External asynchronous inputs need an explicitly reviewed synchronization/reset protocol; do not insert blanket false paths.
- Pad-to-port mapping, bus bits, pad cell types and driver choices from the supported process profile. Every physical signal-pad slot must be explicitly active or unused under a validated profile policy; preserve required signal and power pad counts.
- Floorplan settings supported by that frame profile, routing limits and resource/time limits.
- Verification tests, expected test counts/results and any project-specific checks such as S8's RAM timing or SCK gate analysis.
- Required checks, optional checks and unresolved manual signoff items.

Store machine-specific executable and PDK locations in a separate local toolchain configuration. Resolve project-relative paths against the project directory, independent of the caller's working directory. Run external tools with argument arrays and preserve paths containing spaces and `µ`.

Argument arrays do not solve paths embedded inside Tcl, Yosys scripts or CDL includes. Stage inputs in a private tool-compatible working directory when needed, use proper language-specific quoting, and retain the original-to-staged path map. Resolve package resources from the installed package rather than the source checkout.

Proposed user workflow, to be implemented:

```text
chipflow doctor --toolchain local.toml
chipflow init projects/new-chip --template scl-c1d-2mm
chipflow validate projects/new-chip/chip.toml --toolchain local.toml
chipflow build projects/new-chip/chip.toml --toolchain local.toml
chipflow report <run-directory>
```

`init` supplies a documented example and marks required user inputs. It must not silently invent electrical constraints or report a generated template as a verified chip.

## Milestone 1 — Capture the reference and define contracts

Status: **complete, 8 November 2026**. [Milestone report](docs/milestone-1.md), [acceptance](reference/milestone1/acceptance.json) and [fresh-environment recipe](toolchains/milestone1/README.md). The reference design retains its extraction FAIL and nonzero design result; this status is completion of the reference/contracts milestone.

1. Inventory the scripts, environment variables, source files and existing results used by the S8 build.
2. Record exact tool versions, source hashes, PDK/deck hashes and configuration for a reproducible reference run.
3. Classify each assumption as engine behavior, process-specific behavior or S8-specific behavior. Document each PDK workaround and its verification evidence.
4. Define the project schema, stage inputs/outputs, result schema and required-check policy before moving the implementation.
5. Bring environment reproduction forward: recover or create a pinned recipe for the EDA binaries and Python packages, distinguishing the KLayout batch version from the Python binding version. Establish a fresh-environment startup and tiny synthesis/geometry probe now. Installation reproducibility is an early gate, not something discovered at release time.
6. Capture the wider S8 regression manifest: routed equivalence, clock tracing, delayed cocotb tests, RAM-model runs and seeded startup/SDF probes. Record external model file provenance and availability; do not embed third-party/PDK data in a distributable example by accident.
7. Define an explicit limitation manifest for the pinned process/model versions: check ID, evidence, expected FAIL/WARN/MANUAL state, affected configuration and required resolution. Include the extraction issue and unresolved pad limits. This manifest may explain a software regression's expected outcome; it cannot waive a design failure, accept an unexpected new failure or mark signoff complete.

Acceptance: a documented dependency map, toolchain recipe and baseline manifest explain how the current build and its wider verification are produced. The environment probe succeeds through configured paths. Every known warning, failed extraction check and manual item remains visible. Reference success means reproducing recorded behavior, including known limitations, rather than making every row green.

## Milestone 2 — Package configuration and reliable execution

Status: **complete, 8 November 2026**. [Milestone report](docs/milestone-2.md), [validation contract](docs/m2-validation-contract.md). All items implemented and tested: 111 tests (111/111 in WSL with the real toolchain; 101 pass and 10 real-tool integration tests skip on Windows), after the fixes in [the verification report](docs/milestone-2-verification.md), verified wheel `chipflow-0.1.0.dev4-py3-none-any.whl`, S8 real preflight validation passing (exit 0), S8 build preflight (exit 2, physical adapters deferred to Milestone 3), and negative test fixtures detecting violations.

1. Create the installable `chipflow` package with `doctor`, `validate` and the build runner. Add `init` with the profile template during milestone 3 and `report` with the reporting adapters during milestone 4.
2. Use a bounded Yosys elaboration to obtain structured port/net information with resolved includes, defines and parameters. Validate top-module existence, port and pad coverage, bus indices, supported pad types, clocks, constraints, frame capacity and output locations before expensive stages. Reject unsupported state elements, unresolved black boxes and unmapped technology cells; check relevant netlist properties again after synthesis.
3. Make tool/PDK locations configurable. Check tool versions and required libraries; explain unsupported or missing installations.
4. Give every run a new directory containing a resolved configuration, input manifest, stage logs, commands, versions and SHA-256 artifact hashes. Snapshot the complete project dependency set, including includes, constraints, test data and hooks. Record seeds, thread counts and relevant environment variables. Verify external PDK/deck fingerprints at consumption; reject drift instead of combining different inputs in one run.
5. Implement dependency-aware execution, timeouts and cancellation. A failed stage blocks dependent stages; independent diagnostics may still complete.
6. Use fresh stage directories and current-run manifests so old output cannot satisfy a new check. Do not implement cross-run caching in v1.
7. Validate the resolved build root against all source/PDK paths, give concurrent invocations disjoint directories and never recursively clear an arbitrary user-selected directory. Lock each active run, write final result files atomically and terminate the complete tool process group on cancellation or timeout. A stopped run is incomplete and retains its diagnostic evidence.

Result contract:

- Stage execution: succeeded, failed, blocked, skipped or cancelled.
- Check result: PASS, FAIL, WARN, MANUAL, NOT RUN or NOT APPLICABLE, with evidence and input artifact hashes. NOT APPLICABLE requires a validated capability/precondition, never an empty result or missing report.
- Build exit 0: all required automated checks passed, required artifacts were produced and no executed check failed. Exit 1: a genuine design/check failure, including the known extraction-validation failure. Exit 2: configuration, infrastructure, malformed/missing required output or tool execution failure; this takes precedence over exit 1. Cancellation returns 130 and marks the run incomplete.
- A failed or missing required result must never produce exit 0. Deliberately broken fixtures succeed only when the expected defect is positively detected; a tool crash is not a successful negative test.
- Report build completion, automated verification and signoff blockers separately. Required checks have a profile-defined minimum; optional checks can be explicitly skipped, but neither an optional executed failure nor a development setting can turn FAIL into PASS. Do not infer foundry approval from an exit code.
- Separate extraction command execution, artifact validity and extraction plausibility. Successful production of raw SPEF can feed explicitly requested diagnostic timing after a plausibility failure, provided structural/unit checks pass. Diagnostic descendants retain the failed-ancestor provenance and the full build remains nonzero. Tool crashes or invalid artifacts block all consumers.
- Full-build and software-release qualification are distinct: the known extraction FAIL is an expected nonzero build outcome in the v0.1 regression harness. New/unexpected failures fail software qualification too; no broad expected-failure exemption is allowed.

The full-build minimum covers configuration/toolchain validation, elaboration and mapped-netlist checks, routing and overlap checks, assembly integrity, DRC/LVS, automatic pre-submission rules, metal-only power connectivity, parasitic artifact/unit/coverage/validation checks, complete timing analysis and the required RTL/routed functional tests. Project requirements can add checks. They cannot silently remove these minimums. SDF runs at the specified corners are mandatory in release qualification. Manual or unresolved foundry-policy items remain visible independently of this automated minimum.

Acceptance: tests cover subprocess failures, absent/truncated reports, stale artifacts, timeouts, cancellation, concurrent runs, invalid mappings, unsafe output roots and unusual paths. Each produces the correct result and blocks dependent work. Diagnostic continuation cannot change the overall failure result.

## Milestone 3 — Extract the reusable physical flow

Status: **complete for the documented S8 physical-flow scope, 8 November 2026**. [Independent closeout](docs/milestone-3-verification.md) verifies all five findings fixed at 0.1.0.dev5: 138/138 Linux tests, 126 Windows tests with 12 explicit skips, installed-wheel checks and full installed-package S8/changed-constraint physical builds. Baseline layout and schematic connectivity/models match. Full builds correctly exit 2 with downstream verification pending milestones 4 and 5.

1. Port synthesis with ordered multi-file RTL and configurable top/parameters.
2. Move cell filtering, library fixes, routing obstructions and process units into the SCL profile.
3. Replace fixed frame names, pad slots and floorplan coordinates with a validated frame profile. Qualify the existing 2 mm profile first; derive geometry where reliable and check it against the actual library.
4. Generalize wrapper generation, pad wiring, CDL generation and GDS assembly to the configured supported ports and cells.
5. Retain the proven router overlap check, power connectivity check, bounded route retries and geometry checks. Exhausted retries must fail the stage.
6. Give the pad profile explicit direction, polarity, power pins, CDL/Liberty/Verilog pin mappings and compatible drivers. Select and validate an unused-slot treatment against the real frame and LVS. Never omit a physical pad from the schematic or leave an unspecified unused CMOS input. Limit v0.1's pad allowlist to combinations actually exercised.
7. Use structured netlist connectivity for CDL/wrapper generation, resolving aliases and constants without name collisions. Prefer tool APIs for DEF/netlist interpretation; where a bounded parser remains, reject every unsupported record and reconcile parsed instance/net counts. Validate DBU, grid, orientation and units; silently skipped connectivity or geometry is a hard error.
8. Feed placement, buffering, CTS and final STA from the same resolved clock and interface constraints. Translate chip-level loads/delays to the core boundary using the selected pad models rather than applying a board load directly to core ports. Move the CTS workaround into an explicitly enabled, structurally checked S8 project hook.

Stage dependency outline: validate/snapshot → technology preparation and synthesis → P&R → assembly. DRC/LVS, geometry and power checks consume the assembled layout/schematic. Extraction consumes the routed database; chip STA consumes the pad wrapper, routed netlist, constraints and the selected parasitic artifact. RTL tests can run independently after elaboration; routed tests depend on the routed models/netlist, and SDF tests also depend on complete STA delay output. The final reporter always runs and includes failed, blocked and skipped stages.

Acceptance at this milestone: S8 reaches synthesis, P&R and assembly through the new engine with its inputs and special rules in `projects/s8/`; structural inventories and geometry invariants are checked against the baseline. Full DRC/LVS, timing and functional regression acceptance follows milestones 4 and 5, when their adapters exist. Require identical GDS bytes only where deterministic output has been established. The shared engine contains no S8 port names, smoke-test expectations or absolute project paths.

## Milestone 4 — Generalize verification and reporting

Status: **in progress**. Item 2 is implemented and verified, 10 November 2026; items 1 and 3–7 are open
([progress report](docs/milestone-4.md)). The [project-specific S8 downstream execution](docs/s8-downstream-verification.md)
has completed against the repaired Milestone 3 artifacts. DRC/LVS and functional
checks pass; full-chip input hold timing and raw extraction remain FAIL. This
execution does not implement the generic adapters below or qualify signoff.

1. Consume the shared resolved constraints in final STA and remove hard-coded 10 MHz and RAM assumptions from common reports. Require a timing-coverage check: every retained sequential clock pin belongs to the declared clock, every expected path group and min/max I/O constraint is accounted for, and exceptions resolve to intended nonempty objects. Missing clock propagation, unexplained unconstrained endpoints, missing corners or report sections cannot pass. Check setup/hold and modeled slew, capacitance, fanout and pulse-width limits; unresolved pad-limit interpretations stay explicit blockers.
2. Run DRC/LVS and the existing pre-submission checker through adapters with structured results tied to the exact inputs, decks and output artifacts. DRC needs successful execution, a valid expected report structure and evidence that the expected rule categories ran. LVS needs a nonempty comparison containing the expected top circuit and complete matching coverage. Validate the checker's exit code and tool-error metadata as well as its result rows. Empty categories, empty comparisons and zero expected tests are not clean results.
3. Add RTL and routed simulation adapters with explicit test expectations. Separate zero-delay simulation, SDF simulation and static timing; report exactly which ran. Formal is optional for generic projects, but preserve the established S8 equivalence and wider tests as required S8 regressions. Import their actual runners from `tb/` and `formal/`, including external model dependencies, instead of assuming `flow/run_all.sh` already runs them.
4. Keep S8's SCK gate, RAM protocol checks and smoke-test parser in the S8 project. Designs without `sck` must not enter those checks.
5. Record pad stand-ins, initialization overrides and other simulation-model limitations explicitly; do not apply S8's random-start flip-flop override to every new design.
   For SDF runs, verify matching hierarchy/cell models, min/typ/max selection, timescales and annotation coverage. Run a nonzero known-delay probe to prove delays are applied. Initialization variants must preserve timing arcs. Missing specify support, ignored annotations, zero executed cases and unexplained skipped tests must fail the requested verification. Simulator delay application is not a substitute for static setup/hold analysis.
6. Preserve raw parasitics and any estimated alternative as distinct artifacts. Label which feeds timing. Check SPEF units, matched nets and pin coverage; a close total-capacitance comparison alone cannot validate the extraction model. Use the process limitation manifest until independent validation exists. An estimated mode can support development, but unresolved extraction validation blocks signoff readiness.
7. Produce a concise report plus machine-readable JSON listing failures, blocked stages, warnings, manual work and artifact provenance. Package a local delivery directory and checksums; submission remains outside the runner.
   Mark development bundles as such. A later submission-ready bundle requires the issued identifier/block, final naming and verification rerun on the exact final GDS. v0.1 may list that work as blocked; it must not label a placeholder bundle submission-ready.

Acceptance: a new design's report contains only applicable checks, every claimed pass has complete evidence, an empty report or missing expected test fails verification, and no raw extraction failure disappears behind successful estimated timing. The release harness verifies both the intended passing stages and the exact documented nonzero full-build outcome.

## Milestone 5 — Prove reuse on different chips

Build all three projects through the same installed engine:

1. **S8:** regression case with its existing special checks and models.
2. **Counter:** small design with a non-`clk` clock name, synchronous reset, output bus and no SCK/RAM interface. Use fewer active ports than the frame capacity to exercise unused-pad policy. Supply an independent expected-output test.
3. **PWM controller:** multi-file, parameterized RTL with an include file, different pad positions, clock target, min/max I/O delays and output loading. Supply tests for reset, duty cycle and period, including a configuration variant that changes the elaborated width.

For each, require synthesis, routing, assembly, clean DRC, matching LVS, complete timing coverage, configured functional checks and traceable reports. Check both RTL and routed behavior against independent expectations and verify configured delayed runs with the annotation probe. Compare timing under the explicitly declared estimate only as development evidence. The known raw-extraction check must remain a specific recorded FAIL with exit 1 when reproduced; any additional unqualified failure rejects the software release. Test designs do not remove process/model limitations.

Add deliberate failures: wrong/duplicate/omitted pad mapping, unspecified unused pad, unsupported reset/clock structure, failing testbench, impossible timing constraint, unclocked sequential element, missing I/O constraint, broken layout/netlist, missing tool, empty/truncated report, missing SDF annotation and a stale artifact. Include connectivity fixtures with aliases, constants and name collisions, plus a DEF fixture with an unsupported routing record. Each must produce the intended failure, without a false PASS or reuse of older evidence. The old S8-specific negative tests must be ported by defect type; do not assume their chosen pad or cell exists on another chip.

Acceptance: counter and PWM build from project files alone, with **no edits to the engine or SCL profile**. A non-`clk` clock name and absence of `sck` work. Repeated builds preserve their evidence and do not overwrite one another. Changes to RTL, constraints or the PDK are reflected in run manifests.

Freeze the candidate engine/profile before final acceptance builds. Earlier integration may reveal needed engine fixes; after any such fix, restart the full three-project qualification suite against one version. A clock/load change must reach P&R, STA and the report, not just change displayed text.

## Milestone 6 — Release and migration

1. Document installation, supported versions, project creation, build execution, failure recovery and report interpretation.
2. Test the built wheel, not just an editable checkout, in a clean WSL installation using the pinned EDA recipe, with tool and PDK paths supplied through configuration and a project path containing spaces and `µ`.
3. Complete the environment work started in milestone 1. Verify that the documented recipe resolves the EDA binaries and Python packages without depending on this machine's pre-existing Nix store or venvs. A fresh Python venv alone is insufficient evidence.
4. Version the engine, schema and process profile. Keep synthetic runner tests usable without the confidential kit; run private integration checks locally with the kit.
5. Deliver a private local v0.1 development release with the example projects, exact expected results and qualification evidence. Switch the active S8 project to the packaged flow only after its complete regression comparison is complete; retain the existing flow and baseline artifacts for rollback.

Acceptance: the documented instructions reproduce the examples in a clean environment, and a user can start a supported chip without copying or modifying engine scripts. Publication, uploads and new external services are not part of this plan.

## Definition of done

- One installable tool with a stable, validated per-chip interface.
- S8 plus two independent designs complete the same engine and pass the software qualification suite: all supported implementation/verification checks meet their criteria, and any documented process-model failure is reproduced with the expected nonzero build status. No unexplained failure is accepted.
- No hard-coded S8 behavior or machine-specific paths in the engine.
- New runs preserve prior results; all reported evidence has provenance.
- Failed, missing and unsupported work is reported accurately and affects the overall result appropriately.
- PDK files remain external and unchanged; every transformed working copy is traceable.
- Installation and new-project instructions are exercised, not merely written.
- Known parasitic/model limitations and manual signoff items remain explicit.
- Installed-package, unused-pad, parser-completeness, timing-coverage and nonzero-SDF tests pass; changes to clock/load constraints affect every relevant stage.

## Work after v0.1

Qualify additional SCL frame sizes one at a time with real integration runs. Then consider bidirectional pads, multiple clocks, macros, another PDK, analog workflows, and a GUI. Each addition needs a capability definition and at least one independent acceptance design.

Implement milestones in dependency order, with an integration build after each stage is extracted. Start with the reference manifest, early environment probe and configuration/result contracts: they establish what must be preserved and make the later extraction measurable. Estimate the implementation schedule after that inventory, especially the amount of geometry and verification code that can be retained unchanged. Keep stage internals small until the second design demonstrates which abstractions are actually shared.
