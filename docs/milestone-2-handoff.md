# Milestone 2 handoff — chipflow

Written 8 November 2026 by Claude (Opus 5.5) for the model that continues in this chat. Items marked *verified* were run or read on this machine that day. Keep your running notes in `docs/milestone-2-progress.md` (create it).

## 1. Context

- **What:** `chipflow` is a private, standard-library-only Python CLI in `tools/chipflow/`. Given a chip project (RTL, `chip.toml`, pad map and tests), it is meant to produce a reproducible build, GDS, netlists, verification results and a report for SCL C1D 1.2 µm on the 2×2 mm frame. It generalises the S8-specific legacy flow in `M:\My Projects\Tapeout Chip\flow\`, which is a read-only reference.
- **Why:** the user's first tapeout is S8, a bit-serial 8-bit CPU, on the eChipHub-SCL Shuttle I. The first submission (design plus DRC report) is due 5 December 2026. The S8 submission uses the legacy flow; BUILD_PLAN milestone 6 keeps it until the packaged flow passes full regression. Nothing you do may disturb it.
- **Plan:** `BUILD_PLAN.md` (milestones 1–6). `PLAN_REVIEW.md` has the review findings R1–R9 that shaped it. Current status: `docs/implementation-status.md`.
- **Milestone 1** (reference capture, contracts, pinned environment) finished on 8 November 2026: `docs/milestone-1.md`, `reference/milestone1/acceptance.json`.
- **Milestone 2** ("Package configuration and reliable execution") is partly done:
  - Built: the strict schema-1 loader, run directories, the runner and executor, the result policy, and the toolchain doctor and probe.
  - Not built: elaboration-based validation, pad/frame validation, stage adapters, diagnostic continuation and dependency reconciliation.
- **Your job:** finish milestone 2, then stop and hand back. Don't start milestone 3 (extracting synthesis, P&R and assembly).

## 2. Rules

1. **Confidential.** The SCL kit is marked CONFIDENTIAL/NDA. Never publish, upload or paste project or kit content outside this machine: no web services, artifacts, gists or issues. Don't download anything or `pip install` without asking the user.
2. **Read-only.** Never write, move or delete anything in:
   - `M:\My Projects\Tapeout Chip\`: the live S8 project, which other sessions are working in.
   - `scl-pdk-1.2um\`: the kit. Never run its scripts.
   - `reference\`, `toolchains\milestone1\`, `toolchains\pins\`, `work\milestone1\`, `work\installed-wheel-*`, `transfer\`.
   - `dist\chipflow-0.1.0.dev1-py3-none-any.whl`, which `toolchains/milestone1/requirements.lock` pins by SHA-256 `10bb95eb…`.
3. **Leave the 18 milestone 1-bound files alone.** `reference/milestone1/contract-manifest.json` binds them by hash, and with no git an edit destroys the milestone 1 version. Put milestone 2 contract text in new files. The files are:
   - Docs: `project-format.md`, `result-contract.md`, `stage-contracts.md`, `m1-workarounds.md`, `m1-environment.md`, `reference-inventory.md`, `known-limitations.md`, `milestone-1.md` (all in `docs/`).
   - `reference/s8/m1-contract-audit.json`.
   - In `toolchains/milestone1/`: `capture.json`, `requirements.lock`, `README.md`.
   - `scripts/m1_environment.py`, `m1_reference.py`, `m1_collect.py`, `m1_close.py`, `m1_process_wrapper.py`.
   - `tests/milestone1/test_evidence.py`.
4. **Report results honestly.**
   - NOT RUN stays NOT RUN.
   - Missing, empty or malformed evidence means exit 2.
   - The raw extraction FAIL (`SCL-C1D-RCX-001`) is never waived.
   - `signoff_ready` is always false.
   - If S8 or a fixture fails a check, report it. Never loosen a check to make it pass.
5. **Never clear or recursively delete a directory.** Every run gets a new directory. Old run outputs and legacy-flow outputs never count as evidence for a new run.
6. **External tools:** argument arrays only, never `shell=True`. Executables come only from the toolchain TOML and are SHA-256-checked just before use. No `PATH` lookup.
7. **Package constraints:** standard library only (`dependencies = []`), Python 3.11 or later. It must pass on Windows Python 3.13.5 and WSL Python 3.12.3. KLayout and gdstk run only in subprocess scripts under the configured interpreter. Always pass `encoding="utf-8"` for text I/O.
8. **Style:** match the existing code: frozen dataclasses, strict validation that raises `ConfigError` or `ValueError` with a clear message, and `atomic_write_json` for result files.
9. **Ask the user first** before adding a dependency or download, changing frozen S8 inputs, running any kit file, bumping the schema version, or deleting anything.
10. **Keep notes:** update `docs/milestone-2-progress.md` after each phase. The user watches context use, so save state there before the conversation gets long. Other agents can change files here, so check file state rather than trusting notes.

## 3. Environment and commands

- Windows root: `M:\Desktop Folders\My Projects\Tapeout Automation`. WSL path: `/mnt/m/Desktop Folders/My Projects/Tapeout Automation`. The WSL2 distro is `Ubuntu` (24.04.1). This folder isn't a git repository.
- Run WSL commands from the Bash tool with a heredoc, and strip `\r`:

```bash
wsl.exe -d Ubuntu --exec bash -s <<'EOF' 2>&1 | tr -d '\r'
cd "/mnt/m/Desktop Folders/My Projects/Tapeout Automation/tools/chipflow" || exit 9
/home/sushant/.venvs/presubmit-check/bin/python -m unittest discover -s tests -v
EOF
```

- The WSL reference Python is `/home/sushant/.venvs/presubmit-check/bin/python` (3.12.3, with klayout 0.30.12 and gdstk 1.0.2). Don't install anything into it.
- Windows tests, from the project root: `cd tools/chipflow && PYTHONUTF8=1 python -m unittest discover -s tests -v`. After phase 1 they must also pass without `PYTHONUTF8`.
- Milestone 1 guard tests, from the project root: `python tests/milestone1/test_evidence.py`. `unittest discover -s tests/milestone1` fails only because that folder has no `__init__.py`. Don't add one.
- The toolchain file is `toolchains/local.wsl.toml`. It is private, uses WSL absolute paths, and pins 8 tools and 20 kit files by SHA-256. Elaboration uses `[tools.yosys]`, which is `/usr/bin/yosys`, Yosys 0.33. Real tools only run in WSL.
- Doctor and probe, in WSL from `tools/chipflow`: `/home/sushant/.venvs/presubmit-check/bin/python -m chipflow doctor --toolchain ../../toolchains/local.wsl.toml --probe --run-root ../../work/probes`
- Milestone 1 binding check. Run it from the project root before every handback:

```bash
python - <<'EOF'
import hashlib, json, pathlib
rows = json.load(open('reference/milestone1/contract-manifest.json'))
bad = [r['path'] for r in rows if not pathlib.Path(r['path']).is_file()
       or hashlib.sha256(pathlib.Path(r['path']).read_bytes()).hexdigest() != r['sha256']]
print('M1-bound files OK' if not bad else 'DRIFT: %s' % bad)
EOF
```

- Keep scratch files in `work/m2-scratch/`, which is private and ignored. Don't use WSL `/tmp`, which a WSL restart clears.

## 4. Baseline, verified 8 November 2026

| Check | Result |
|---|---|
| chipflow tests, WSL Python 3.12.3 | 27/27 OK |
| chipflow tests, Windows Python 3.13.5 | 26/27. `test_execution.ExecutionTests.test_arguments_logs_and_nonzero_exit` fails with `UnicodeDecodeError`, because the child Python writes `µ` as cp1252 into a file. All 27 pass with `PYTHONUTF8=1`. This is a fault in the test, not the engine. |
| Milestone 1 guard tests | 8/8 OK |
| The 18 milestone 1-bound files | All match |
| S8 RTL | The live `rtl/s8.v` and the frozen `work/milestone1/reference-20261107T202316Z-f546a6c1/source/rtl/s8.v` both have SHA-256 `fa52f0fb87289df5dd2f1f5e6004538fb513956fc834ad78556c39021a86ca46`. The frozen pad map is `…/source/flow/pads.tsv` (`f57c2826…`). Always copy from the frozen copies. |
| `projects/` folder | Doesn't exist yet |

## 5. Code map

`tools/chipflow/` has about 1,450 lines of package code and 450 lines of tests.

| File | What it does now |
|---|---|
| `chipflow/__init__.py` | Sets `__version__ = "0.1.0.dev1"`, `SCHEMA_VERSION = 1`, `RESULT_SCHEMA_VERSION = 1` and `PROFILE_VERSION = "0.1.0"`. |
| `chipflow/cli.py` | Commands:<br>• `doctor`.<br>• `validate`: `--structure-only` exits 0; anything else always exits 2.<br>• `build`: snapshot and doctor, then an `implementation` stage that is FAILED/infrastructure, so it exits 2.<br><br>Helpers:<br>• `active_run()` writes an incomplete `result.json` first.<br>• `snapshot_project()` copies each dependency to `<run>/inputs/project/<relative path>` and writes `input-manifest.json` and `resolved-project.json`. |
| `chipflow/config/project.py` | Strict schema-1 loader that returns a frozen `ProjectConfig`. It rejects unknown keys, unsafe paths, async resets, nonfinite numbers and build roots that overlap inputs. `remaining_checks` lists what it doesn't validate. `pads.mapping` is captured but not interpreted. |
| `chipflow/runner/results.py` | Defines:<br>• `StageStatus` and `CheckStatus`.<br>• `MINIMUM_REQUIRED_CHECKS` (21 IDs), `RELEASE_REQUIRED_CHECKS` and `MINIMUM_REQUIRED_ARTIFACTS` (4).<br>• `Artifact`, `CheckResult`, and `StageResult` (with `failure_kind` design or infrastructure).<br>• `BuildResult.exit_code`, where 130 takes precedence over 2, 2 over 1, and 1 over 0. |
| `chipflow/runner/execution.py` | Provides:<br>• `sha256_file`, `atomic_write_json` and `validate_build_root`.<br>• The locked `RunDirectory` and `create_run`.<br>• `run_command`: argument array, separate logs, timeout, cancellation, and killing the whole process tree through a Windows job or POSIX session.<br>• `StageSpec`.<br>• `execute_stages`: a sequential DAG with a fresh `stages/<id>/` per stage, where a failure blocks all descendants; it writes `execution.json`. |
| `chipflow/toolchain.py` | `load_toolchain` (strict); `verify_file`, which raises `ToolchainError` on fingerprint drift; `doctor`. |
| `chipflow/probe.py`, `chipflow/resources/` | A tiny synthesis and KLayout geometry probe of the environment. Copy its pattern: stage package resources, record `path-map.json`, and verify kit files before and after use. |
| `tests/` | • `test_contracts.py`: the loader and result policy; `write_project()` builds the synthetic project fixture.<br>• `test_execution.py`: the runner.<br>• `test_toolchain_cli.py`: the toolchain and CLI; `toolchain_file()` builds a synthetic toolchain that uses `sys.executable` for every tool. |

## 6. What milestone 2 still needs

These come from the milestone 2 section of `BUILD_PLAN.md` and from `docs/implementation-status.md`.

- **G1** A stage adapter hook, and the stage input envelope from "Stage envelope and artifact identity" in `docs/stage-contracts.md`.
- **G2** Diagnostic continuation: a design-failed stage with valid artifacts may feed a consumer explicitly declared as diagnostic. Provenance is kept and the build stays nonzero.
- **G3** A bounded Yosys elaboration adapter that reports ports, nets, state elements and clock usage in structured form.
- **G4** Semantic validation against the elaborated design: top, ports, bus indices, clock and reset, I/O constraint coverage, unsupported constructs and frame capacity.
- **G5** The SCL C1D 2 mm frame profile, a pad-map format, and validation of slots, allowlist, drivers, power pads and unused slots.
- **G6** Reconciling discovered dependencies; recording provenance (seed, threads, environment, versions); checking fingerprints at the point of use.
- **G7** CLI: a real full `validate`, and `build` reporting real configuration and elaboration results.
- **G8** A reusable checker for the mapped netlist after synthesis (plan item M2.2). Lowest priority; it can move to milestone 3 if you record that.
- **G9** Tests for the milestone 2 acceptance list, plus docs, status, a version bump and a wheel check.

## 7. Yosys 0.33 behaviour, verified 8 November 2026

I probed `/usr/bin/yosys` in WSL on synthetic files in a folder whose path contains spaces and `µ`, and on a scratch copy of S8.

1. A quoted `-I "dir with space"` inside a script fails with `ERROR: Can't open include file`. A quoted source file name containing a space works.
   **Do:** stage includes as `work/inc/0000/…` and sources as `work/src/0000_<safe>.v`, and write only safe relative names into scripts.
2. Relative paths work when the working directory contains spaces and `µ`.
3. `yosys -q -E deps.txt -l elab.log -s elab.ys` writes one make rule listing every file read, including nested includes, with spaces escaped as `\ `. For example: `top.json: elab.ys inc0/nested.vh inc0/width.vh top\ file.v`
4. `hierarchy -check -top T -chparam W 6` works. Two forms fail:
   - `-chparam OFF -3` gives `ERROR: Can't decode value '-3'!`
   - `-chparam MODE "slow"` gives `ERROR: Can't decode value '"slow"'!`

   **Do:** support only nonnegative integer parameters, and reject anything else with a clear message.
5. `-DNAME=VALUE` works.
   **Do:** restrict names and values to `[A-Za-z0-9_]`.
6. An undefined module makes Yosys exit 1 with ``ERROR: Module `\missing_cell' referenced in module `\t_undef' in cell `\u' is not part of the design.``
7. `(* blackbox *)` modules pass `hierarchy -check`. Their instances survive `flatten` as cells whose type doesn't start with `$`. Unused blackbox modules stay in the `modules` list.
   **Do:** check the cell types actually instantiated, not the module list.
8. After `proc; flatten; memory`, each construct appears like this:

   | RTL construct | What the JSON shows |
   |---|---|
   | Synchronous reset | `$mux` before `$dff` (no `$sdff`, because `opt_dff` isn't run) |
   | Asynchronous reset | `$adff` |
   | Latch | `$dlatch` |
   | Negative-edge flop | `$dff` with `CLK_POLARITY` `'0'` |
   | `reg r = 1'b1` | Netname attribute `init` |
   | Inferred RAM | `$dff`, `$mux`, `$eq`, `$and`; no `$mem*` left |
   | `inout` port | Port direction `inout` |
   | `1'bz` | `"z"` entries in connection bit lists |
   | Gated clock | `$dff` CLK driven by an `$and` output |
   | Second clock | `$dff` CLK on another port's bit |

9. JSON shape:
   - **Ports** have `direction` and `bits`. `offset` appears only when nonzero, `upto: 1` only for `[0:N]`, and `signed` only when signed. Bit `i` of `bits` (LSB first) has HDL index `offset + (width-1-i if upto else i)`. Confirm this with a `[0:3]` and an `[8:1]` port in an integration test.
   - **Cells** have:
     - `type`.
     - `parameters`, which are binary strings, either one bit such as `'1'` or 32 bits such as `'00…0100'`; always parse with `int(v, 2)`.
     - `attributes.src`, for example `s8.v:105.16-105.45`.
     - `port_directions`.
     - `connections`, made of integer bit IDs or the strings `"0"`, `"1"`, `"x"` and `"z"`.
10. The S8 scratch copy:
    - Exits 0 in well under a second.
    - Inputs: `clk`, `rst`, `miso`, `gpi[2:0]`. Outputs: `sck`, `cs_n`, `mosi`, `gpo[2:0]`.
    - Has 14 `$dff` cells, 45 bits in all, every one on `clk`. That matches milestone 1's 45 clock traces.
    - The clock net feeds 14 `$dff.CLK` pins and one `$or.A`. The `$or` is `assign sck = ~(clk | idle);` (`rtl/s8.v:183`).
    - Has no `init`, no `z` and no memory, and `check -assert` passes.

## 8. Decisions made for you

Use these defaults. If one proves wrong, write down why in the progress log and ask the user.

- **D1 Clock-derived outputs (S8's `sck`).** Add one optional key to `[clock]`: `derived_outputs = [{ port = "sck", check = "s8.sck_gate" }]`.
  - Without the key, any use of the clock net other than a `$dff` CLK pin is a configuration FAIL.
  - With the key, compute the forward combinational cone from the clock net's non-CLK users. It must reach exactly the declared output bits and must not reach any `$dff` D or CLK pin. Every declared port must be in the cone, so a stale declaration fails.
  - Each `check` ID is added to the project's required checks, so a build can't exit 0 until milestone 4 supplies the S8 hook. That is intended.
  - The schema stays at version 1 because the key is optional and additive. Document it in the new contract doc (D5).
- **D2 A new failure kind, `configuration`.**
  - In `runner/results.py`, allow `failure_kind="configuration"`. It needs at least one FAIL check and maps to exit 2, as infrastructure does.
  - Use it for rejected inputs: unsupported constructs, coverage gaps, bad pad maps, and Yosys input errors (an `ERROR:` line in `elab.log`).
  - Crashes, timeouts and malformed tool output stay `infrastructure`.
- **D3 No unused-slot policy exists yet.**
  - Profile 0.1.0 defines no qualified unused-slot policy; review finding R4 deliberately refused to invent one.
  - Any `unused` entry in a pad map is a configuration FAIL: "no qualified unused-slot policy in profile 0.1.0".
  - So in milestone 2 only designs that use all 12 signal slots, such as S8, can fully validate. A counter with fewer ports gets exit 2 with that message, which is correct.
- **D4 The pad allowlist is what milestone 1's S8 reference used.**

  | Pad cell | Driver | Qualified slots |
  |---|---|---|
  | OPAD04 | INVR04 | 1, 3, 5, 7, 9, 11 |
  | IPAD2D | none | 2, 8, 10, 12 |
  | IPAD30 | none | 4 |
  | IPAD3D | none | 6 |
  | PVDD01 (power) | — | 13, 15 |
  | PVSS01 (power) | — | 14, 16 |

  - A known cell in a slot it wasn't used in gets a NOT RUN row saying "not yet physically qualified", so `configuration` can't PASS and validation exits 2.
  - An unknown cell or an incompatible driver is a FAIL.
- **D5 Contract text goes in a new doc.** Write `docs/m2-validation-contract.md` and say that it extends `project-format.md`, `stage-contracts.md` and `result-contract.md`. Cover the pad-map schema, the profile format, the elaboration rules, the `configuration` kind and `derived_outputs`. Say that it replaces the milestone 1 statement that full validation is always nonzero.
- **D6 Profile file.** Put it at `tools/chipflow/chipflow/technologies/scl_c1d/profile-0.1.0.toml` and load it with `importlib.resources.files("chipflow").joinpath("technologies/scl_c1d/profile-0.1.0.toml")`.
  - `pyproject.toml` already ships `technologies/**/*.toml`. No `__init__.py` is needed, just as for `resources/`.
  - Store only the names, IDs and positions that validation needs. Never copy kit files or rule values into it.
- **D7 Elaboration reads the run snapshot** (`<run>/inputs/project/…`), never the live project. Re-hash each staged copy against `input-manifest.json` before Yosys runs.
- **D8 A fixed elaboration script.** This is adapter `yosys.elaboration`, version 0.1.0. Changing the script means a new adapter version.

```text
read_verilog -sv -I work/inc/0000 -DNAME=VALUE work/src/0000_a.v work/src/0001_b.v
hierarchy -check -top <top> -chparam NAME 6
proc
flatten
memory
opt_clean
check -assert
write_json elab.json
```

  - Command: `[yosys, "-q", "-E", "deps.txt", "-l", "elab.log", "-s", "elab.ys"]`, with the stage directory as the working directory.
  - Timeout: the project's `build.timeout_seconds`. A timeout is an infrastructure failure.
- **D9 Exit codes for `validate`.**
  - 0 only when `toolchain`, `elaboration` and `configuration` all PASS.
  - 2 otherwise, including when any row is NOT RUN.
  - 130 on cancellation.
  - `--structure-only` keeps today's behaviour.
  - Full validation without `--toolchain` exits 2 with "full validation needs --toolchain".
- **D10 Versions.** When the milestone 2 code is finished, set `0.1.0.dev2` in both `pyproject.toml` and `chipflow/__init__.py`. Keep `SCHEMA_VERSION = 1` and `PROFILE_VERSION = "0.1.0"`.

## 9. Checklist

Work through the phases in order, and run the relevant tests after each one. Don't move on while a test is failing.

### Phase 0: orient (no code changes)

- [ ] Read:
  - `BUILD_PLAN.md`: the objective, the per-project interface, milestone 2 and the result contract.
  - `docs/implementation-status.md`, `stage-contracts.md`, `project-format.md`, `result-contract.md` and `known-limitations.md`.
  - All of `tools/chipflow/`.
- [ ] Run the baseline: Windows tests with `PYTHONUTF8=1`, WSL tests, the milestone 1 guard tests and the binding check. Expect the results in §4.
- [ ] Create `docs/milestone-2-progress.md` with the date, the baseline results and a short list of phases.

### Phase 1: fix the Windows locale fault in the test

- [ ] In `tests/test_execution.py::test_arguments_logs_and_nonzero_exit`, pass `env={"PYTHONUTF8": "1"}` to `run_command`, so the child writes UTF-8 whatever the host locale. Don't change `run_command`; its logs are raw bytes by design.
- [ ] Done when all 27 tests pass on Windows **without** `PYTHONUTF8`, and all 27 pass in WSL.

### Phase 2: the `configuration` failure kind (D2)

- [ ] In `runner/results.py`:
  - Accept `"configuration"`, require at least one FAIL check for it, and have `exit_code` return 2 for it. Keep 130 checked first.
  - Also return 2 when any stage has `failed_ancestors` but there is no FAIL check anywhere, since that provenance is inconsistent.
- [ ] Tests in `tests/test_contracts.py`:
  - A configuration failure gives 2.
  - A configuration failure together with a design FAIL gives 2.
  - Cancellation still gives 130.
  - A configuration failure without a FAIL check raises `ValueError`.

### Phase 3: stage adapters, the envelope and diagnostic continuation (G1, G2)

- [ ] Create `chipflow/stages/__init__.py` and `chipflow/stages/envelope.py`.
  - Define a frozen `StageEnvelope` matching `docs/stage-contracts.md`, with these fields:
    - `schema_version`, `stage_id` and `adapter {id, version}`.
    - `inputs [{id, path, sha256, bytes}]` and `tools [{id, executable_sha256, version_evidence}]`.
    - `configuration_sha256`: the SHA-256 of `<run>/resolved-project.json`.
    - `seed`, `threads`, and `outputs [{id, path, validator}]`.
  - Validate relative paths that can't escape, lowercase 64-character hex hashes, and nonnegative integers.
  - `write(path)` uses `atomic_write_json`.
- [ ] Add optional fields to `StageSpec` that default to off, so existing callers keep working:
  - `prepare: Callable[[Path], StageEnvelope] | None`
    - Runs in the fresh stage directory before the command; the executor writes its return value to `stage-input.json`.
    - If it raises, the stage is FAILED/infrastructure, the command doesn't run, and dependents are BLOCKED.
  - `assess: Callable[[Path, CommandResult, tuple[Artifact, ...]], Assessment] | None`, with `Assessment(checks, failure_kind, extra_artifacts)`.
    - The executor calls it after the command finishes, but never after a timeout or cancellation.
    - If the command succeeded: validate and hash the outputs, then call `assess`. Any FAIL makes the stage FAILED with the given kind (`design` or `configuration`), and the artifacts are kept.
    - If the command failed: `assess` may classify an input error it positively recognises as `configuration`; otherwise the failure is infrastructure.
    - If `assess` itself raises, the failure is infrastructure.
  - `diagnostic_inputs: tuple[str, ...] = ()`. It must be a subset of `dependencies`; check this in `_validate_stages`.
- [ ] Dependency rule in `execute_stages`:

```python
inherited, blocked_by = [], []
for dep in stage.dependencies:
    r = by_id[dep]
    inherited.extend(r.failed_ancestors)
    if r.status == StageStatus.SUCCEEDED:
        continue
    if (dep in stage.diagnostic_inputs and r.status == StageStatus.FAILED
            and r.failure_kind == "design" and r.artifacts):
        inherited.append(dep)      # runs, carrying the failure
        continue
    blocked_by.append(dep)
ancestors = tuple(dict.fromkeys(inherited + blocked_by))
# If blocked_by is nonempty: BLOCKED(failed_ancestors=ancestors). Otherwise run, and the result keeps ancestors.
```

- [ ] Write `tests/test_adapters.py` with these cases:
  - `prepare` raises: the stage is FAILED/infrastructure, there's no command log, and dependents are BLOCKED.
  - `stage-input.json` is written before the command runs and is valid.
  - `assess` returns a FAIL: the stage is FAILED/design with its artifacts kept, and a normal dependent is BLOCKED with `failed_ancestors`.
  - A diagnostic consumer runs after a design FAIL and carries `failed_ancestors`, and its descendants inherit them.
  - A `BuildResult` holding a design FAIL plus a fully passing diagnostic stage still exits 1.
  - A producer that crashes or writes invalid output leaves the diagnostic consumer BLOCKED.
  - A consumer that doesn't declare the design-failed stage as diagnostic is BLOCKED.
  - `diagnostic_inputs` outside `dependencies` raises `ValueError` before any command runs.
  - `assess` raising gives an infrastructure failure.
- [ ] Done when the old and new tests pass on Windows and in WSL.

### Phase 4: design model and checks, pure Python (G3 analysis, G4 structure)

- [ ] Write `chipflow/stages/design_model.py`. It makes no tool calls.
  - `parse_make_deps(text) -> (target, prerequisites)`:
    - Handle `\ ` escapes and backslash-newline continuations.
    - Require exactly one rule.
    - Treat `$`, `#` or a stray `:` as an infrastructure error, "unexpected dependency syntax".
  - `load_design(data, top) -> Design`:
    - **Ports:** name, direction, width, and the declared index range from `offset` and `upto`.
    - **Canonical bit names:** `name` for an unranged 1-bit port, otherwise `name[i]`. For a 1-bit port at offset 0, `a` and `a[0]` are the same bit, so constraining both is a duplicate.
    - **Cells:** type, parameters parsed with `int(v, 2)`, connections, `port_directions` and `src`.
    - **Netnames** that carry `init`.
- [ ] Add `check_design(design, clock, reset, derived_outputs, path_map) -> rows`.
  - It returns one row per rule, each with a stable ID.
  - In messages, map `src` from staged names back to the original project paths.

  | Row ID | Rule |
  |---|---|
  | `elab.top` | The top module is present |
  | `elab.inout` | No `inout` ports |
  | `elab.tristate` | No `"z"` bits |
  | `elab.blackbox` | Every cell type starts with `$` |
  | `elab.cell_types` | Allowlist of the combinational cells `$not $pos $neg $and $or $xor $xnor $reduce_and $reduce_or $reduce_xor $reduce_xnor $reduce_bool $logic_not $logic_and $logic_or $shl $shr $sshl $sshr $shift $shiftx $lt $le $eq $ne $eqx $nex $ge $gt $add $sub $mul $div $mod $divfloor $modfloor $pow $mux $pmux $bmux $demux`, plus `$dff`. Any other `$` type fails as "unsupported in yosys.elaboration 0.1.0". |
  | `elab.async_or_latch` | None of `$adff*`, `$aldff*`, `$dffsr*`, `$dlatch*`, `$sr` |
  | `elab.clock_polarity` | `CLK_POLARITY` is 1 |
  | `elab.single_clock` | Every `$dff` CLK bit is the declared clock bit |
  | `elab.init_values` | No `init` attributes |
  | `elab.clock_usage` | The D1 cone rule |
  | `elab.reset_used` | The reset port has at least one user |
  | `elab.state_bits` | Number of state bits (informational) |

- [ ] Clock cone:
  - Walk forward from the clock bits using `port_directions`.
  - Stop at `$dff` CLK pins, which are legal.
  - A `$dff` D pin inside the cone is a FAIL.
  - Collect the top-level output bits reached and compare them with `derived_outputs`.
- [ ] Write `tests/test_design_model.py` using small hand-written JSON fixtures shaped like those in §7, plus deps-parser cases: spaces, continuation, two rules, garbage. Include a fixture shaped like S8, with one `$or` and one `$not` from `clk` to `sck`; it must pass only when `derived_outputs` declares `sck`.

### Phase 5: the Yosys elaboration adapter (G3, G6, D7, D8)

- [ ] Write `chipflow/stages/elaboration.py` with `elaboration_stage(project, toolchain, run_dir) -> StageSpec`.
  - **Before execution:** `elaboration_stage()` checks file names, parameters and defines. It raises `ConfigError` for:
    - include-tree paths outside `[A-Za-z0-9_./+-]`, naming the file;
    - negative or string parameters;
    - unsafe define text.

    The CLI turns that into a `configuration` FAIL row. `prepare` raising is reserved for infrastructure problems such as I/O errors or hash mismatches.
  - **`prepare`:**
    - Copy the snapshot sources, in order, to `work/src/NNNN_<name>`, with the name reduced to `[A-Za-z0-9_.-]`.
    - Copy each include directory's snapshot tree to `work/inc/NNNN/`, keeping relative names.
    - Re-hash every copy against `input-manifest.json`.
    - Write `path-map.json` (original, snapshot and staged path, plus SHA-256) and `elab.ys`.
    - Run `verify_file()` on the Yosys executable.
    - Return the envelope, recording the tool hash. Use the run-relative `doctor/doctor.json` as `version_evidence`.
  - **Outputs and their validators:**
    - `elab.json`: valid JSON whose `modules[top]` has `ports`, `cells` and `netnames`.
    - `deps.txt`: exactly one rule, with target `elab.json`.
    - `elab.log`.
  - **`assess`:**
    - If Yosys failed: an `ERROR:` line in `elab.log` makes it a configuration FAIL whose message is that line with staged paths mapped back. Without an `ERROR:` line, it is an infrastructure failure.
    - If Yosys succeeded: write `design.json` with the normalised model, the rows and the discovered dependencies (as original paths), hash it, and return it as an extra artifact. Then return the `elaboration` check, which is PASS only if every row passes.
    - Add the `elab.dependencies` row (G6): every prerequisite apart from `elab.ys` must be a staged file listed in `path-map.json`, and every declared source must appear. Otherwise the row is FAIL, "undeclared dependency".
  - **Evidence order:** write an evidence file before creating the `CheckResult` that cites its hash, because PASS and FAIL need both evidence and input hashes.
- [ ] Windows-safe tests. Build the real `StageSpec`, then fake Yosys with `dataclasses.replace(spec, command=(sys.executable, "-c", <code that writes fixture outputs>))`. Cover:
  - Staging when the project path contains spaces and `µ`.
  - An unsafe include name.
  - Negative and string parameters.
  - A fixture log with an `ERROR:` line, which must give a configuration failure.
  - A crash, which must give an infrastructure failure.
  - A missing or garbled `elab.json`, which must give an infrastructure failure.
  - A dependency outside `path-map.json`, which must give a FAIL.
- [ ] Write `tests/test_integration_yosys.py`, which uses the real Yosys.
  - Skip it unless the environment variable `CHIPFLOW_TEST_TOOLCHAIN` points to a toolchain TOML, and print the reason for the skip.
  - Generate the negative designs from §7 in a temporary directory and assert the exact failing row for each.
  - Add one passing counter-style design with an include and a parameter, plus ports declared `[0:3]` and `[8:1]`.
  - Run it in WSL with `CHIPFLOW_TEST_TOOLCHAIN="/mnt/m/Desktop Folders/My Projects/Tapeout Automation/toolchains/local.wsl.toml"`.

### Phase 6: profile and pad map (G5, D3, D4, D6)

- [ ] Write the profile, `technologies/scl_c1d/profile-0.1.0.toml`. In TOML, top-level keys must come before the first `[table]`.
  - **Top-level keys:**
    - `schema_version = 1`, `profile = "scl_c1d"`, `profile_version = "0.1.0"`.
    - `frame = "scl-c1d-2mm"`, `frame_top_cell = "frame2mmx2mm"`.
    - `die_um = 2000.0`, `dbu_um = 0.001`, `grid_um = 0.005`.
    - `unused_slot_policies = []`, `qualification_evidence = "reference/milestone1/acceptance.json"`.
  - **12 `[[signal_slot]]` entries** `{slot, side, offset_um, shipped_cell}`, taken from `ORIGIN` in `flow/assemble/assemble.py:162-167`. The shipped cell is AIPAD00 for odd slots and IPAD00 for even slots, per the `flow/pads.tsv` header.

    | Side | Slot: offset (µm) |
    |---|---|
    | Bottom | 1: 350.95, 2: 700.95, 3: 1050.95, 4: 1400.95 |
    | Right | 5: 350.95, 6: 1605.8 |
    | Top | 7: 1400.95, 8: 1050.95, 9: 700.95, 10: 350.95 |
    | Left | 11: 1605.8, 12: 350.95 |

  - **4 `[[power_pad]]` entries** `{slot, cell, net}`: 13 PVDD01 VDD, 14 PVSS01 VSS, 15 PVDD01 VDD, 16 PVSS01 VSS (`flow/assemble/make_cdl.py:83-86`).
  - **`[pad_cell.<ID>]` tables** with `direction`, `core_pin`, `pad_pin`, `drivers`, `qualified_slots` and `limitations`. Pin names come from `flow/assemble/make_cdl.py:79`.

    | Cell | Direction | Core pin | Pad pin | Drivers | Other |
    |---|---|---|---|---|---|
    | OPAD04 | output | `IN1` | `PAD` | `["INVR04"]` | `inverting = true`; limitation `SCL-C1D-PAD-001` |
    | IPAD2D | input | `OUT1` | `IN1` | `[]` | |
    | IPAD30 | input | `OUT1` | `IN1` | `[]` | limitation `SCL-C1D-MODEL-001` |
    | IPAD3D | input | `OUT1` | `IN1` | `[]` | |

    Leave input-pad polarity out; milestone 3 fills it in from milestone 1's pad-polarity evidence.
- [ ] Write `chipflow/technologies/profile.py`.
  - A strict loader that rejects unknown keys.
  - Cross-check that there are 12 unique signal slots and 4 power pads, and that every qualified slot exists and isn't a power slot.
  - Record the profile file's SHA-256 in the run evidence.
- [ ] Write `chipflow/config/pads.py`, a strict loader for the pad map.
  - Top-level keys: `schema_version = 1`, `frame` and `profile_version`.
  - Each `[[slot]]` entry is either `{slot, port, pad_cell, driver}` (with `driver = "none"` for inputs) or `{slot, unused = "<policy>"}`.
  - It reads the run's snapshot copy, not the live file.
- [ ] Pad rows. Rows that don't depend on the design still run when elaboration fails; rows that need ports become NOT RUN.

  | Row ID | Rule |
  |---|---|
  | `pads.schema` | Strict pad-map schema |
  | `pads.frame` | Frame and profile version match |
  | `pads.slot_coverage` | Each of slots 1–12 appears exactly once. Duplicates, gaps and out-of-range slots (including 13–16) fail. |
  | `pads.cell` | The pad cell is in the allowlist |
  | `pads.driver` | Outputs need an allowlisted driver; inputs need `"none"` |
  | `pads.direction` | The pad direction matches the port direction |
  | `pads.port_coverage` | Every top-level port bit is mapped exactly once; no unknown ports or out-of-range bus bits |
  | `pads.unused` | D3 |
  | `pads.qualification` | D4; NOT RUN for slots never used with that cell |
  | `pads.capacity` | The design's port bits fit in the 12 signal slots |

- [ ] Write the resolved 16-entry `pad-plan.json` (12 signal pads plus 4 power pads) for milestone 3.
- [ ] Write `tests/test_pads.py` with at least one case per row:
  - A duplicate slot, a missing slot, slot 13 and an unknown cell.
  - An input pad with a driver, an output pad without one, and a wrong driver.
  - A direction mismatch, a port bit mapped twice, and a port bit not mapped.
  - A nonexistent port, and `gpo[3]` on a 3-bit bus.
  - An unused slot, a wrong profile version and an unknown key.
  - The exact S8 mapping, which must pass.
  - The same cells in other slots, which must give NOT RUN.
  - The profile loading from package resources.

### Phase 7: semantic validation (G4, D1)

- [ ] In `config/project.py`, accept `clock.derived_outputs`, an array of `{port, check}`.
  - The port must be one of the `io.outputs` ports.
  - The check ID must match `[a-z][a-z0-9_.-]*`.
  - Add the check IDs to `verification.required_checks`.
  - Keep error messages explicit.
- [ ] Write `chipflow/validation.py` to build the `configuration` check. Its rows:
  - `config.clock` and `config.reset`: each is present and is a 1-bit input.
  - `config.io_coverage`:
    - Every input bit apart from the clock has exactly one input constraint.
    - Every output bit has exactly one output constraint with a load.
    - No constraint names a missing bit, the wrong direction or an out-of-range bus index.
  - The pad rows from phase 6.

  The check is FAIL if any row fails, NOT RUN if any row is NOT RUN, and PASS otherwise. Write every row to `validation.json` and cite that file as evidence.
- [ ] Tests: a passing and a failing case for each coverage rule, plus validation of `derived_outputs`.

### Phase 8: CLI and provenance (G6, G7, D9)

- [ ] Full mode, `chipflow validate <chip.toml> --toolchain <toml>`:
  - Steps, in order:
    1. `active_run(project.build_root, protected, "validation")`.
    2. Snapshot the inputs.
    3. Doctor, giving the `toolchain` check.
    4. The elaboration stage, through `execute_stages`. Call it once per run, because it creates `<run>/stages/` with `exist_ok=False`.
    5. The configuration check.
    6. Write `validation.json` and `result.json`.
  - Print a short summary: the run path, each check's status and the first failing row.
  - Exit codes follow D9.
- [ ] `chipflow build`:
  - Run the same validation stages first. If validation fails, everything after it is BLOCKED with `failed_ancestors`. Otherwise keep the `implementation` stage's message "physical adapters not implemented (milestone 3)", so every milestone 2 build exits 2.
  - List each required check exactly once. `toolchain`, `elaboration` and `configuration` come from validation. Everything else, including `s8.sck_gate`, appears as NOT RUN in the implementation stage. Duplicate check IDs force exit 2 and hide the real cause.
- [ ] Write a new `<run>/run-manifest.json` containing:
  - The chipflow, schema and profile versions, and the profile's SHA-256.
  - argv, start and end times (UTC), platform and Python version.
  - The SHA-256 of the toolchain file and of the input manifest.
  - Seed, threads and timeout.
  - Every environment override passed to a tool, plus an allowlisted snapshot of `LANG`, `LC_ALL`, `PATH`, `PYTHONUTF8`, `PYTHONIOENCODING` and `TMPDIR`.
- [ ] Windows-safe tests. Patch `chipflow.cli.doctor` as `test_build_preflight_never_passes_unimplemented_flow` does, and fake the elaboration command. Cover:
  - A passing full validation gives 0.
  - Each failure class gives 2.
  - Cancellation gives 130.
  - Two concurrent validations get distinct run directories.
  - `--structure-only` is unchanged.
  - Full validation without `--toolchain` gives 2.
  - A build with a valid configuration reports `configuration` and `elaboration` as PASS and the physical checks as NOT RUN, and exits 2.

### Phase 9: mapped-netlist checker (G8, lowest priority)

- [ ] Write `chipflow/stages/netlist_checks.py` with `check_mapped_netlist(data, top, allowed_cells, sequential_cells, clock_port)`:
  - No `$` cells remain, and every cell is in the allowed list.
  - Every sequential cell's clock pin is on the clock port bit.
  - Constants come only from the tie cells VDDCON (pin TIEHI) and VSSCON (pin TIELO), per workaround P04.
- [ ] Add the excluded cells to the profile as `excluded_cells`, tagged with workaround IDs P01 and P02. Take the `DROP` and `FORCE` lists from `flow/synth.sh:16-22`. The `ALLOW_VIA_OVER_POLY` opt-out isn't supported in v0.1.
- [ ] Test with synthetic JSON, and in WSL with the `mapped.json` from a fresh `doctor --probe` run (`work/probes/run-*/probe/mapped.json`). If time runs short, record in the progress log and the status doc that G8 has moved to milestone 3.

### Phase 10: real-input runs in WSL

- [ ] Create `projects/s8/` with three things:
  - **`rtl/s8.v`:** copied from the frozen copy. Check its SHA-256 is `fa52f0fb…`.
  - **`pads.toml`:** converted from the frozen `pads.tsv`.

    | Slot | Port | Direction | Pad cell | Driver |
    |---|---|---|---|---|
    | 1 | `cs_n` | out | OPAD04 | INVR04 |
    | 2 | `miso` | in | IPAD2D | none |
    | 3 | `mosi` | out | OPAD04 | INVR04 |
    | 4 | `clk` | in | IPAD30 | none |
    | 5 | `sck` | out | OPAD04 | INVR04 |
    | 6 | `rst` | in | IPAD3D | none |
    | 7, 9, 11 | `gpo[0]`, `gpo[1]`, `gpo[2]` | out | OPAD04 | INVR04 |
    | 8, 10, 12 | `gpi[0]`, `gpi[1]`, `gpi[2]` | in | IPAD2D | none |

  - **`chip.toml`:**
    - Top `s8`, with `top_cell = "OS0000"` and `development_identifiers = true`, because no silicon number exists yet.
    - Clock `clk`: `period_ns = 100.0`, `waveform_ns = [0.0, 50.0]`, `transition_ns = 2.5`, `uncertainty_ns = 0.0`, and `derived_outputs = [{ port = "sck", check = "s8.sck_gate" }]`.
    - Reset `rst`, synchronous, active high.
    - I/O delays of `min_ns = max_ns = 0.0` for every data input and output, and `load_pf = 30.0` for each output.
    - `build.root = "builds"`.
    - A comment saying that the period, transition, delays and loads are the legacy flow's unreviewed assumptions. Cite `flow/run_sta.sh:26-27`, `flow/sta/sta.tcl:17-25` and the `environment_variables` in `reference/s8/dependencies.json`.
    - A `manual_items` entry saying the same. Don't invent any other values.
- [ ] Validate S8 in WSL, from `tools/chipflow`: `/home/sushant/.venvs/presubmit-check/bin/python -m chipflow validate ../../projects/s8/chip.toml --toolchain ../../toolchains/local.wsl.toml`
  - Expect exit 0, with `elab.state_bits` = 45, `elab.clock_usage` passing through the declared `sck`, and all 12 slots qualified.
  - If you get anything else, stop, record the run path and rows, and report. Don't edit checks to force a pass.
- [ ] Run `chipflow build` on the same project. Expect exit 2, with `configuration` and `elaboration` passing and the physical checks and `s8.sck_gate` NOT RUN.
- [ ] Copy S8 to `work/m2-scratch/s8-no-derived/` and remove `derived_outputs`. Expect `elab.clock_usage` to FAIL, naming `rtl/s8.v:183`, and exit 2.
- [ ] Build a counter fixture in `work/m2-scratch/counter/`: a 4-bit output, clock `tick`, reset `reset_high`, and unused slots. Expect exit 2 with the D3 message.
- [ ] Record every run path, exit code and key row in the progress log.

### Phase 11: docs, version, wheel and handback

- [ ] Write the new `docs/m2-validation-contract.md` (D5) and `docs/milestone-2.md`. The latter covers what was built, how to run it, evidence paths, limits, and anything moved to milestone 3.
- [ ] Update the docs that aren't bound: `docs/implementation-status.md`, `docs/getting-started.md`, `docs/supported-configurations.md`, `README.md`, and the milestone 2 status line in `BUILD_PLAN.md`.
- [ ] Bump the version to `0.1.0.dev2` (D10).
- [ ] Check the wheel:
  - Build it offline into `work/m2-dist/`, never into `dist/`. First try this from the project root in WSL: `PYTHONPATH="$PWD/work/package-build" /home/sushant/.venvs/presubmit-check/bin/python -m pip wheel --no-deps --no-build-isolation --no-index -w work/m2-dist tools/chipflow`. `work/package-build` contains setuptools 80.9.0 and wheel 0.45.1.
  - If that fails, ask the user; don't download anything.
  - Add a `--wheel PATH` option to `scripts/verify_wheel.py` and run it on the new wheel. That script isn't bound, and today it expects exactly one wheel in `dist/`.
  - Confirm that the installed wheel contains `technologies/scl_c1d/profile-0.1.0.toml` and that the tests pass from it.
- [ ] Final checks:
  - Windows tests pass without `PYTHONUTF8`.
  - WSL tests pass with `CHIPFLOW_TEST_TOOLCHAIN` set, with no integration tests skipped.
  - The milestone 1 guard tests pass 8/8.
  - The binding check prints "M1-bound files OK".

## 10. Definition of done for milestone 2

| Milestone 2 requirement in BUILD_PLAN | Evidence needed |
|---|---|
| Subprocess failures, absent or truncated reports, stale artifacts, timeouts, cancellation, concurrent runs, unsafe output roots and unusual paths | The existing tests, plus elaboration-adapter equivalents from phases 3, 5 and 8 |
| Invalid mappings | `tests/test_pads.py` |
| Each case gives the correct result and blocks dependent work | Phase 3 tests and the CLI build tests |
| Diagnostic continuation can't change the overall failure result | The phase 3 test where the exit code stays 1 |
| Bounded elaboration; validation of the top, ports, buses, pads, clock, constraints and capacity; rejection of unsupported state and black boxes | Tests from phases 4–7, the WSL integration tests, and the S8 and negative runs |
| Configurable tools with checked versions | The existing doctor, plus the tool fingerprint check at the point of use |
| Run provenance: resolved config, input manifest, logs, commands, versions, hashes, seeds, threads and environment, with fingerprints checked at the point of use | `run-manifest.json`, `stage-input.json`, `path-map.json`, and the phase 8 tests |

Also required:

- S8 full validation exits 0 in WSL, and the S8 build exits 2 with the expected rows.
- All tests pass on both platforms, and the milestone 1 guard tests and binding check are unchanged.
- The docs are updated, and nothing in the read-only list has changed.

## 11. Traps

- Yosys 0.33 can't take a quoted `-I` path that contains spaces, or a negative or string `-chparam` value (§7).
- Parameter widths in the Yosys JSON vary (`'1'` versus 32-bit strings). Always parse with `int(v, 2)`.
- `offset` and `upto` appear only when they aren't the default. A missing key means 0, or a downto range.
- Unused `(* blackbox *)` modules stay in the module list. Only the cells actually instantiated matter.
- Running `opt_dff` would turn synchronous-reset flops into `$sdff` or `$dffe`. Keep the D8 script fixed, or change the allowlist and the adapter version together.
- Windows text I/O defaults to cp1252. Pass `encoding="utf-8"` everywhere, and pass `PYTHONUTF8=1` to child Python processes that print non-ASCII text.
- `toolchains/local.wsl.toml` uses WSL paths, so Windows tests must fake the tools. Never skip a test silently; always print the reason.
- Duplicate check IDs in a `BuildResult` force exit 2.
- `execution.json` serialises `StageResult` with `asdict`, so any new fields must be JSON-safe. Callables belong on `StageSpec`, which isn't serialised.
- `scripts/verify_wheel.py` expects exactly one wheel in `dist/`.
- The legacy shell scripts hide failures (`LEGACY-EXIT` in `docs/m1-workarounds.md`), so never treat their output as evidence.
- `tests/milestone1/` has no `__init__.py`, so run that test file directly.

## 12. Handback

When milestone 2 is done, or when you stop, update `docs/milestone-2-progress.md`. Then report in the chat:

1. The paths of the files you created or changed.
2. Test counts for Windows, WSL and the integration tests, with the reason for every skip.
3. The output line of the milestone 1 binding check.
4. The run paths and exit codes for S8 `validate` and `build`, and the results of the negative runs.
5. Anything deferred to milestone 3, and any rule you couldn't follow, with the reason.
