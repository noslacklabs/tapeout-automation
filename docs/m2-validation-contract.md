# Milestone 2 validation contract

This document extends `project-format.md`, `stage-contracts.md` and `result-contract.md`, which stay bound to milestone 1 and are not edited. Where they conflict with it, this document wins for chipflow 0.1.0.dev2 and later. It describes the code as it stands, including its limits.

## 1. What changed from milestone 1

Milestone 1 said full validation always exits nonzero. That no longer holds. `chipflow validate <chip.toml> --toolchain <toolchain.toml>` now runs three gates in one new run directory: `toolchain`, `elaboration` and `configuration`.

| Command | Exit |
|---|---|
| `validate --toolchain …` | 0 only when all three gates are SUCCEEDED and their checks PASS. 2 otherwise, including any NOT RUN row. 130 on cancellation. |
| `validate` without `--toolchain` | 2, "full validation needs --toolchain" |
| `validate --structure-only` | Unchanged: 0 after structural checks; claims nothing more |
| `build --toolchain …` | Runs the same gates, then an `implementation` stage. If a gate fails, implementation is BLOCKED with `failed_ancestors`. Otherwise it is FAILED/infrastructure, "physical adapters not implemented (milestone 3)". Every milestone 2 build exits 2. |

`validate` writes a `result.json` with `kind = "validation"` and `required_checks = ["toolchain", "elaboration", "configuration"]`. `build` writes a full `BuildResult`. In it, every other required check, including each `derived_outputs` check, appears exactly once as NOT RUN in the implementation stage. The console prints every check recorded by each stage, not a recomputed value, so a BLOCKED stage reads NOT RUN. Beneath a FAIL it prints the message, then the first failing or open configuration row.

## 2. Failure kinds

`StageResult.failure_kind` is `design`, `infrastructure`, `configuration` or empty.

- **configuration**: the inputs were rejected. Examples are an unsupported construct, a coverage gap, a bad pad map, a Yosys `ERROR:` line, or an input refused before Yosys runs. It needs at least one FAIL check and maps to exit 2.
- **infrastructure**: the tool or its evidence can't be trusted. Examples are a crash without an `ERROR:` line, a timeout, missing or malformed output, a failing `prepare` or `assess`, or an adapter calling its own result unusable. It maps to exit 2.
- **design**: a genuine design FAIL, exit 1. Milestone 2 produces none.

Exit precedence is 130, then 2, then 1, then 0. A stage with `failed_ancestors` but no FAIL check anywhere in the build is inconsistent provenance, and gives exit 2.

## 3. Stage adapter hook

`StageSpec` has three optional fields; existing callers are unaffected.

- `prepare(stage_dir)` runs in the fresh stage directory before the command. It must return a `StageEnvelope` (or a dict), which is written to `stage-input.json`. If it returns anything else, or raises, the stage is FAILED/infrastructure, the command doesn't run, and dependents are BLOCKED.
- `assess(stage_dir, command, artifacts)` returns `Assessment(checks, failure_kind, extra_artifacts)`. It is never called after a timeout or cancellation.
  - **Command succeeded**: the required outputs are validated and hashed first.
    - `failure_kind = "infrastructure"` always gives FAILED/infrastructure, with or without FAIL rows. It is never downgraded.
    - `design` or `configuration` without a FAIL check is inconsistent, and gives FAILED/infrastructure.
    - Any FAIL check gives FAILED with the stated kind, `design` if none is given. Artifacts are kept.
    - Otherwise the stage is SUCCEEDED.
  - **Command failed**: `assess` may return `configuration` with a FAIL check for an input error it positively recognises. Anything else is infrastructure.
  - **`assess` raises**: infrastructure.
- `diagnostic_inputs` must be a subset of `dependencies`. A stage may run after a dependency that FAILED with kind `design` and kept artifacts, but only if that dependency is listed here. The stage keeps the failure in `failed_ancestors`, and so do its descendants. Crashes, invalid outputs and every other kind block it. This continuation can never change the overall exit code.

The envelope has the fields in `stage-contracts.md`. Paths are relative and can't escape, hashes are 64 lowercase hex characters, `seed` is nonnegative and `threads` is at least 1.

## 4. `chip.toml` additions (schema 1, additive)

```toml
[clock]
port = "clk"
derived_outputs = [{ port = "sck", check = "s8.sck_gate" }]
```

- `port` names a whole port, which must also have an `io.outputs` constraint. `check` matches `[a-z][a-z0-9_.-]*`, and is added to the required checks so that a build can't exit 0 until a hook supplies that check.
- Without the key, any use of the clock other than a `$dff` CLK pin fails `elab.clock_usage`. That includes the clock reaching an output with no cell in between, as with `assign out = clk;`.
- With the key, the forward combinational cone from the clock's non-CLK users must not reach any `$dff` pin. It must reach exactly the bits of the declared ports: every one of them, and nothing else. A declaration that the cone doesn't reach fails as stale.
- Known gap: a `check` ID may currently reuse a profile-minimum ID such as `drc`, and then no separate check is required.

`project.parameters` must be nonnegative integers, because Yosys 0.33 can't take a negative or string `-chparam`. Define names and values are limited to `[A-Za-z0-9_]`, and include-tree file names to `[A-Za-z0-9_./+-]`. Inputs that break these rules are refused before Yosys runs (§6).

## 5. Pad map (`pads.toml`)

Top-level keys are `schema_version = 1`, `frame` and `profile_version`, plus a `[[slot]]` array. Unknown keys are rejected at every level. The map is read from the run's snapshot.

| Slot form | Keys |
|---|---|
| Active | `slot`, `port`, `pad_cell`, `driver` |
| Unused | `slot`, `unused` |

- `driver` is `"none"` for input pads.
- `port` names one bit: `name` for an unranged 1-bit port, otherwise `name[i]` with the HDL index. For an unranged 1-bit port, `a` and `a[0]` are the same bit.
- Profile 0.1.0 has no qualified unused-slot policy. Any `unused` slot fails `pads.unused` with "no qualified unused-slot policy in profile 0.1.0". So only designs that use all 12 signal slots can fully validate in this milestone.

## 6. Profile (`chipflow/technologies/scl_c1d/profile-0.1.0.toml`)

The profile is loaded from package resources, and its SHA-256 is recorded in `run-manifest.json`. It holds names, IDs and positions only, never kit files or rule values.

- **Top-level keys:** `schema_version = 1`, `profile = "scl_c1d"`, `profile_version = "0.1.0"`, `frame = "scl-c1d-2mm"`, `frame_top_cell`, `die_um = 2000.0`, `dbu_um`, `grid_um`, `unused_slot_policies = []` and `qualification_evidence`.
- **`[[signal_slot]]`** has 12 entries `{slot, side, offset_um, shipped_cell}`. Slots 1–4 are on the bottom, 5–6 on the right, 7–10 on the top and 11–12 on the left, at the offsets of `ORIGIN` in the legacy `flow/assemble/assemble.py`.
- **`[[power_pad]]`** has 4 entries `{slot, cell, net}`: 13 PVDD01 VDD, 14 PVSS01 VSS, 15 PVDD01 VDD, 16 PVSS01 VSS.
- **`[pad_cell.<ID>]`** has `direction`, `core_pin`, `pad_pin`, `drivers`, `qualified_slots`, `inverting` and `limitations`:

  | Cell | Direction | Drivers | Qualified slots |
  |---|---|---|---|
  | OPAD04 | output | INVR04 | 1, 3, 5, 7, 9, 11 |
  | IPAD2D | input | — | 2, 8, 10, 12 |
  | IPAD30 | input | — | 4 |
  | IPAD3D | input | — | 6 |

- **`[[excluded_cell]]`** has `{cell, workaround, reason}`: the 13 DROP cells (P01) and 25 FORCE cells (P02) from `flow/synth.sh`. The mapped-netlist checker uses them only through the allowlist its caller passes in. No stage calls it until milestone 3.

## 7. Elaboration (`yosys.elaboration` adapter, version 0.1.0)

**Inputs refused before Yosys runs.** The rules in §4 raise `ConfigError` in `elaboration_stage()`. The CLI writes `<run>/elaboration-rejected.json` containing the message, then records a FAILED/configuration stage whose FAIL check cites that file. After that, configuration is BLOCKED and the run still writes `validation.json` and `run-manifest.json`.

**`prepare`.** It verifies the Yosys executable's SHA-256 and copies the run snapshot, never the live project:

- sources go to `work/src/NNNN_<name>`;
- each include tree goes to `work/inc/NNNN/`.

Each copy is re-hashed against `input-manifest.json`. It then writes `path-map.json` (original, snapshot and staged path, with SHA-256), `elab.ys` and `stage-input.json`.

**Fixed script.** Changing it requires a new adapter version.

```text
read_verilog -sv -I work/inc/0000 -DNAME=VALUE work/src/0000_a.v
hierarchy -check -top <top> -chparam NAME <n>
proc
flatten
memory
opt_clean
check -assert
write_json elab.json
```

The command is `yosys -q -E deps.txt -l elab.log -s elab.ys`, run in the stage directory, with the project's `build.timeout_seconds`.

**Outputs.**

- `elab.json`: `modules[top]` must have `ports`, `cells` and `netnames`.
- `deps.txt`: exactly one make rule, with target `elab.json`.
- `elab.log`: nonempty.
- `design.json`: the rows, the discovered dependencies as original paths, and each port's direction, width and canonical bits.

If Yosys fails and `elab.log` has an `ERROR:` line, the result is a configuration FAIL quoting that line, with staged paths replaced by original ones. If there's no such line, it is infrastructure.

**Rows.** `elaboration` is PASS only if every row passes.

| Row | Rule |
|---|---|
| `elab.top` | The top module is present |
| `elab.inout` | No `inout` ports |
| `elab.tristate` | No `"z"` bits |
| `elab.blackbox` | Every instantiated cell type starts with `$`; unused blackbox modules are ignored |
| `elab.async_or_latch` | No `$adff*`, `$aldff*`, `$dffsr*`, `$dlatch*` or `$sr` |
| `elab.cell_types` | Only the combinational allowlist plus `$dff`; anything else is "unsupported in yosys.elaboration 0.1.0" |
| `elab.clock_polarity` | `CLK_POLARITY` is 1 |
| `elab.single_clock` | Every `$dff` CLK is the declared clock bit |
| `elab.init_values` | No `init` attributes |
| `elab.clock_usage` | The §4 rule |
| `elab.reset_used` | The reset port has at least one user |
| `elab.state_bits` | Number of state bits; informational, always PASS |
| `elab.dependencies` | Every prerequisite apart from `elab.ys` is, exactly, a staged path in `path-map.json`, and every declared source appears. Basenames are never matched, so an outside file that happens to share a declared file's name still fails as "undeclared dependency". |

## 8. Configuration check

The rows are written to `<run>/validation.json`, which the `configuration` check cites. The check is FAIL if any row fails, NOT RUN if any row is NOT RUN, and PASS otherwise. Rows that need the design are NOT RUN when there is no `elab.json`.

| Row | Rule |
|---|---|
| `config.clock`, `config.reset` | The port exists in the design and is a 1-bit input |
| `config.io_coverage` | Every input bit apart from the clock has exactly one input constraint. Every output bit has exactly one output constraint with a positive load. No constraint names a missing bit, the wrong direction or an out-of-range index. |
| `pads.schema` | The pad map loads under the strict schema |
| `pads.frame` | Frame and profile version match the profile |
| `pads.slot_coverage` | Slots 1–12 each appear exactly once; 13–16 and anything else fail |
| `pads.cell` | The pad cell is in the profile |
| `pads.driver` | Inputs use `"none"`; outputs use one of the cell's drivers |
| `pads.unused` | §5 |
| `pads.qualification` | A known cell in a slot outside its `qualified_slots` is **NOT RUN**, "not yet physically qualified". The configuration therefore can't PASS, and validation exits 2. |
| `pads.direction` | Pad direction matches port direction |
| `pads.port_coverage` | Every port bit is mapped exactly once; no unknown ports or out-of-range bits |
| `pads.capacity` | The design's port bits fit in 12 signal slots |

If the schema, frame and slot coverage rows pass, `<run>/pad-plan.json` holds 16 entries for milestone 3: the 12 signal slots with side, offset, port, direction, cell and driver, and the 4 power pads. The power pads' `offset_um` of 0.0 is a placeholder, not a position. The plan isn't yet hashed into `result.json`.

## 9. Run evidence

Each run directory contains:

| File | Contents |
|---|---|
| `input-manifest.json` | Original path, snapshot path and SHA-256 for every dependency |
| `inputs/project/…` | The snapshot itself |
| `resolved-project.json` | The resolved configuration |
| `toolchain.toml`, `resolved-toolchain.json`, `doctor/doctor.json` | The toolchain and its doctor results |
| `run-manifest.json` | chipflow, schema and profile versions; profile SHA-256; argv; start and end times (UTC); platform; Python; toolchain and input-manifest SHA-256; seed, threads and timeout; environment overrides passed to tools (none so far); and a snapshot of `LANG`, `LC_ALL`, `PATH`, `PYTHONUTF8`, `PYTHONIOENCODING` and `TMPDIR` |
| `execution.json`, `stages/elaboration/…` | `command.json`, logs, `stage-input.json`, `path-map.json`, `elab.ys`, `deps.txt`, `elab.json`, `elab.log` and `design.json` |
| `validation.json`, `pad-plan.json`, `result.json` | Configuration rows, the pad plan and the overall result; plus `elaboration-rejected.json` when an input is refused before Yosys |

Remaining provenance gaps are listed in `docs/milestone-2-verification.md`. One example is the pad-map fallback to the live file when the snapshot copy won't load.
