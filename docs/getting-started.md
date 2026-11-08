# Getting started with the foundation

Run EDA commands in WSL Ubuntu 24.04 on x86-64. The current local installation is
described by `toolchains/local.wsl.toml`. It contains exact paths and hashes and
is deliberately separate from chip projects. [The environment recipe](../toolchains/README.md)
records what was recovered and what still needs a clean installation test.

Milestone 1's fresh userspace startup has now passed. For the pinned offline
runtime reconstruction rather than reuse of the local setup below, follow
[the milestone 1 recipe](../toolchains/milestone1/README.md). A clean WSL release
installation remains a separate gate.

From this project directory in WSL, the existing reference Python can run the source package:

```bash
cd tools/chipflow
/home/sushant/.venvs/presubmit-check/bin/python -m chipflow doctor \
  --toolchain ../../toolchains/local.wsl.toml --probe --run-root ../../work/probes
```

`doctor` checks the configured OS, Python, package versions, executable hashes,
version banners and 20 external technology input fingerprints. `--probe` then
maps a tiny synchronous counter using the real Liberty library, writes a GDS
with batch KLayout, verifies its geometry with the separately versioned Python
binding, and reads/checks the external 2 mm frame. Inputs embedded in tool
scripts use relative staged names. Every call creates a new retained run.

The gate is an environment check; its success does not certify a chip build or
the unused-pad policy. `SBY --version` in the reference installation prints
`unknown SBY version`; that exact banner is recorded, with the executable hash
and Nix-store identity retained separately.

The source package is installable with `python -m pip install ./tools/chipflow`.
For offline installation of the built wheel, use:

```bash
python -m pip install --no-index --no-deps work/m2-dist/chipflow-0.1.0.dev2-py3-none-any.whl
chipflow --help
```

That interpreter must already have the pinned EDA Python dependencies for the
geometry probe. Packaging has no mandatory third-party Python dependency for
schema or runner use. The wheel verification script creates a local venv,
installs the wheel offline, confirms imports come from that venv, runs the
synthetic tests and exercises the environment gate:

```bash
/home/sushant/.venvs/presubmit-check/bin/python scripts/verify_wheel.py --workspace "$PWD" --wheel work/m2-dist/chipflow-0.1.0.dev2-py3-none-any.whl
```

Project commands currently have these boundaries:

```bash
# Fast structural and syntax validation
chipflow validate path/to/chip.toml --structure-only

# Full semantic preflight validation (toolchain, elaboration, configuration)
chipflow validate path/to/chip.toml --toolchain toolchains/local.wsl.toml

# Build preflight (validates configuration; physical implementation is deferred to milestone 3)
chipflow build path/to/chip.toml --toolchain toolchains/local.wsl.toml
```

Full validation returns 0 when toolchain, elaboration, and configuration checks pass,
and returns 2 on check failure. `build` executes preflight validation; if preflight passes,
it reports the implementation stage as FAILED with an infrastructure notice (physical adapters
deferred to milestone 3), records all physical checks as NOT RUN, and exits 2.
`init` and the chip reporting adapters arrive in milestones 3 and 4 respectively.

Unit tests require no kit and run on Windows or Linux (94 tests; the 9 real-Yosys tests skip unless `CHIPFLOW_TEST_TOOLCHAIN` names a toolchain file). Run them from `tools/chipflow`:

```bash
# Windows
python -m unittest discover -s tests -v

# Linux / WSL
CHIPFLOW_TEST_TOOLCHAIN="$PWD/../../toolchains/local.wsl.toml" /home/sushant/.venvs/presubmit-check/bin/python -m unittest discover -s tests -v
```
