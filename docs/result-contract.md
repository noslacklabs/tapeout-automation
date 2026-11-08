# Execution and result contract, schema 1

`runner.results` defines stage states `succeeded`, `failed`, `blocked`, `skipped`
and `cancelled`, independently of check states `PASS`, `FAIL`, `WARN`, `MANUAL`,
`NOT RUN` and `NOT APPLICABLE`. A successful command alone does not create a PASS.
PASS and FAIL need evidence artifacts and input SHA-256 hashes. NOT APPLICABLE
needs a validated precondition explanation and evidence.

`BuildResult.exit_code` uses these rules:

| Code | Meaning |
|---|---|
| 0 | All profile/project required checks passed, required artifacts exist in the result inventory, no executed check failed, and execution is complete |
| 1 | A positively detected design/check failure with otherwise complete required evidence; the known raw extraction defect stays here when reproduced |
| 2 | Configuration/infrastructure/tool failure or missing, malformed, duplicate or incomplete required evidence; takes precedence over code 1 |
| 130 | Cancellation, with incomplete work and preserved evidence |

The profile minimum includes configuration, toolchain, elaboration, mapped
connectivity, routing/overlap, assembly, DRC/LVS, automatic presubmission,
metal-only power connectivity, parasitic artifact/unit/coverage/plausibility,
timing coverage/setup/hold/limits and RTL/routed functional tests. Release
qualification additionally requires min/typ/max SDF and the annotation probe.
Canonical required outputs are `artifacts/chip.gds`, `artifacts/chip.cdl`,
`artifacts/mapped.v` and `artifacts/routed.v`; adapters can require more.
Projects cannot remove the check/artifact minimums. Optional executed failures
remain failures. Missing outputs remain conservative infrastructure failures.

`to_dict()` keeps completion, automated verification and signoff separate.
`signoff_ready` is always false in this development foundation. The versioned
limitation manifest explains expected software-regression outcomes; it does not
alter a check or the build exit code.

`runner.execution` exposes:

- `create_run(root, protected_paths)` returns a locked context with a unique
  `.path`. It never clears another run. Roots overlapping protected inputs fail.
- `run_command(argv, cwd, timeout_s, env, cancellation, log_prefix)` executes
  argument arrays with separate stdout/stderr, command metadata and atomic result
  files. Reusing a command evidence prefix fails. POSIX sessions and Windows jobs
  terminate descendants on timeout/cancellation and clean up orphaned children.
- `StageSpec` names dependencies, an argument array, timeout, environment overrides,
  required outputs and per-output structural validators. `execute_stages` checks
  the graph, uses fresh stage directories, hashes validated outputs, blocks failed
  descendants and continues independent work. Output existence/nonzero size is
  the base requirement; verification adapters must supply positive content checks.

The current executor conservatively blocks all failed dependencies. Explicit
diagnostic extraction continuation, failed-ancestor propagation into diagnostic
timing, and automated check-specific adapters are future work. It does not treat
an unvalidated raw artifact as permission to continue. CLI runs write an incomplete
result immediately, then atomically replace it on completion or cancellation.

Environment doctor/probe results use separate `kind` values and cannot substitute
for the full-build result. Only `validate --structure-only` limits success to the
structural contract. Full validation and preflight-only builds are nonzero.
