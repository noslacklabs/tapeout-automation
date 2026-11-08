# Milestone 2: Package Configuration and Reliable Execution

## 1. Overview

Milestone 2 delivers complete project configuration parsing, process profile definitions for SCL C1D 1.2 µm, stage execution with diagnostic continuation, bounded RTL elaboration via Yosys, comprehensive semantic and pad qualification validation, mapped netlist checking, and full CLI preflight workflows.

Closeout version: **0.1.0.dev4** (8 November 2026). All verification findings are resolved; current results and retained evidence are in [the verification report](milestone-2-verification.md#final-closeout-8-november-2026).

All code is implemented in standard Python (Python 3.11+) with zero external dependencies, running cross-platform on Windows and WSL2 Linux.

---

## 2. Capabilities Delivered

### Project & Pad Configuration (G4, G5)
- **`chipflow/config/project.py`**: Strict schema-1 TOML loader parsing clock constraints, synchronous resets, derived clock outputs (`[clock.derived_outputs]`), I/O delays, output loads, and toolchain configurations.
- **`chipflow/config/pads.py`**: Strict pad-map loader for `pads.toml` assigning the 12 peripheral frame slots to top-level ports and standard pad cells with appropriate buffer drivers.
- **`chipflow/technologies/scl_c1d/profile-0.1.0.toml`**: Frame specification for the 2×2 mm frame, capturing die dimensions (2000 µm × 2000 µm), 12 peripheral signal slots, 4 fixed power pads (PVDD01/VDD on 13 and 15, PVSS01/VSS on 14 and 16), allowed pad cells (`IPAD30`, `IPAD3D`, `IPAD2D`, `OPAD04`), allowed drivers (`INVR04`, `none`), and DROP/FORCE standard cell exclusion rules.

### Stage Execution & Provenance (G1, G2, G6)
- **`chipflow/runner/execution.py`**: Sequential DAG execution with isolated per-stage work directories (`stages/<id>/`), command execution with separate stdout/stderr logs, timeout enforcement, process tree cleanup, and diagnostic continuation (allowing downstream diagnostic stages to run when parent stages fail with valid evidence).
- **`run-manifest.json`**: Captures platform metadata, Python and toolchain versions, execution hashes, random seeds, worker thread counts, timeout limits, and allowlisted environment variables (`LANG`, `LC_ALL`, `PATH`, `PYTHONUTF8`, `PYTHONIOENCODING`, `TMPDIR`).
- **`input-manifest.json`**: Snapshots all project dependencies and records SHA-256 fingerprints at the point of use.

### Bounded Elaboration Adapter (G3)
- **`chipflow/stages/elaboration.py`**: Stages RTL sources and include paths into safe relative names (`work/src/...`) to handle spaces and unicode in workspace paths. Runs Yosys 0.33 elaboration, emits `design.json`, tracks state bit counts (e.g. 45 for S8), verifies module resolution (rejecting undefined blackboxes), and traces flip-flop clock pins.
- Flags clock net branching into combinational logic with exact source line citations (e.g. `rtl/s8.v:183`), while honoring declared derived outputs.

### Semantic Validation & Pad Planning (G4, G5)
- **`chipflow/config/project.py`** validates numeric clock constraints and supported reset declarations. **`chipflow/validation.py`** checks clock/reset port existence, direction and width, I/O coverage, and 10 pad checks (`pads.schema`, `pads.frame`, `pads.slot_coverage`, `pads.cell`, `pads.driver`, `pads.unused`, `pads.qualification`, `pads.direction`, `pads.port_coverage`, `pads.capacity`).
- Reads the hash-verified snapshot pad map, writes `validation.json`, and emits a hashed `pad-plan.json` only when all pad checks pass. Signal pads carry qualified side/offset data; the four power pads carry cell/net identities and explicitly unknown (`null`) geometry for Milestone 3.

### Mapped Netlist Checking (G8)
- **`chipflow/stages/netlist_checks.py`**: Checks mapped primitives, sequential clocks, constant ties and caller-supplied cell allowlists. Synthetic tests and a freshly mapped real probe exercise it; removing DROP/FORCE cells from the allowlist detects their presence. The raw-library environment probe contains excluded cells and is correctly rejected by the strict profile check. Integration with chip synthesis belongs to Milestone 3.

### CLI Commands (G7)
- **`chipflow validate`**: Runs full validation (`toolchain` -> `elaboration` -> `configuration`). Exits with **0** when all pass; exits with **2** when checks fail; exits with **130** on interruption.
- **`chipflow build`**: Runs preflight validation; if preflight passes, reports downstream implementation stage as FAILED with `failure_kind="infrastructure"` and error `"physical adapters not implemented (milestone 3)"`, reporting remaining checks as `NOT RUN` and exiting **2**.

---

## 3. How to Run

### Running Full Validation on S8
```bash
# In WSL2 Linux
PYTHONPATH=tools/chipflow /home/sushant/.venvs/presubmit-check/bin/python -m chipflow validate projects/s8/chip.toml --toolchain toolchains/local.wsl.toml
```

### Running Test Suite
```bash
# Windows, from tools/chipflow
python -m unittest discover -s tests -v

# WSL2 Linux, from tools/chipflow; set CHIPFLOW_TEST_TOOLCHAIN so the real-Yosys tests run
CHIPFLOW_TEST_TOOLCHAIN="$PWD/../../toolchains/local.wsl.toml" /home/sushant/.venvs/presubmit-check/bin/python -m unittest discover -s tests -v
```

### Building and Verifying the Wheel
```bash
# Build wheel
mkdir -p work/m2-dist-dev4
PYTHONPATH="$PWD/work/package-build" /home/sushant/.venvs/presubmit-check/bin/python -m pip wheel --no-deps --no-build-isolation --no-index -w work/m2-dist-dev4 tools/chipflow

# Verify wheel
/home/sushant/.venvs/presubmit-check/bin/python scripts/verify_wheel.py --workspace . --wheel work/m2-dist-dev4/chipflow-0.1.0.dev4-py3-none-any.whl
```

---

## 4. Evidence Runs

The original implementation runs below are retained for history. Final dev4 runs are `projects/s8/builds/run-20261108T100553.914641Z-5968b18008a4` (validate, exit 0) and `run-20261108T100556.730460Z-9bba6f8c992c` (build, expected exit 2). The complete final suite and negative-run records are in `work/m2-closeout-20261108T100510Z/`.

1. **S8 Full Validation (Pass):**
   - Run directory: `projects/s8/builds/run-20261108T073506.325648Z-86dd80335af2`
   - Exit code: `0`
   - Stages: `toolchain` (PASS), `elaboration` (PASS, 45 state bits), `configuration` (PASS, all 12 slots qualified).

2. **S8 Build Preflight (Infrastructure Exit):**
   - Run directory: `projects/s8/builds/run-20261108T073612.417603Z-974e15752cae`
   - Exit code: `2`
   - Preflight stages succeeded; `implementation` stage failed with infrastructure notice deferring physical adapters to Milestone 3; downstream checks reported `NOT RUN`.

3. **Negative Test: S8 Without Derived Clock Output:**
   - Run directory: `work/m2-scratch/s8-no-derived/builds/run-20261108T073648.471984Z-71fc8cbe404a`
   - Exit code: `2`
   - Stage `elaboration` failed with `clock net used outside flop CLK pins: $or$... at rtl/s8.v:183.18-183.28`.

4. **Negative Test: Counter with Unused Slots:**
   - Run directory: `work/m2-scratch/counter/builds/run-20261108T073844.945998Z-024af058de77`
   - Exit code: `2`
   - Stage `configuration` failed with `pads.unused: no qualified unused-slot policy in profile 0.1.0`.

---

## 5. Scope Boundaries and Handoff to Milestone 3

- **Milestone 2 completed**: All configuration, profile, elaboration, semantic check, and netlist check requirements are complete and tested.
- **Milestone 3 scope**:
  - Full synthesis script generation and execution (mapping to SCL C1D cells, inserting tie-offs, applying DROP/FORCE cell rules).
  - OpenROAD place-and-route flow integration (floorplan, tapcells, standard cell placement, CTS, routing, filler cells).
  - Physical signoff adapters: KLayout DRC/LVS, OpenROAD parasitic extraction (SPEF) and STA signoff across min/typ/max corners.
  - S8 behavior testbench and SPI clock gate verification (`s8.sck_gate`, `s8.behavior`).
