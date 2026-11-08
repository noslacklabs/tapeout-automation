# Milestone 1 pinned binary environment

This directory is the local, private recipe for reconstructing the captured
EDA runtime in a fresh Ubuntu userspace. It preserves actual binary artifacts
so startup can be reproduced without the old Nix store or Python environments.

The gate passed on 8 November 2026 in
[fresh-startup-5ftzc883/result.json](fresh-startup-5ftzc883/result.json): a new
userspace/venv, positive private PID/path isolation checks, the configured doctor
and real synthesis/geometry probe, Ngspice startup, and unchanged external PDK
fingerprints. [fresh-startup.json](fresh-startup.json) is the canonical result.
The original failed attempt `fresh-startup-a18axy5u` is also retained.

## Files

| Path | Purpose |
|---|---|
| `capture.json` | Exact package/runtime/wheel versions, SHA-256 values, provenance and capture commands |
| `requirements.lock` | All 15 Python artifacts pinned by version and wheel hash |
| `artifacts/ubuntu-base-24.04.3-base-amd64.tar.gz` | SHA-verified official Ubuntu Base |
| `artifacts/debs/` | 45 exact-version Ubuntu `.deb` files and the explicit installed-data certificate repack |
| `artifacts/nix-runtime.tar.gz` | Complete active 398-path EDA runtime closure; 968,246,617 bytes |
| `artifacts/wheels/` | Captured/repacked Python binary distributions and private engine/checker wheels |
| `artifacts/checker-source/` | Copied private checker source used to build its wheel |
| `nix-runtime-paths.txt` / `nix-runtime-info.json` | Store path set and Nix content metadata |
| `active-derivations.json` | Active derivation graph as provenance, distinct from a completed source rebuild |
| `inspection.json` | Prerequisite/flake discovery; records that earlier recovered flakes produce different OpenROAD outputs |
| `capture-logs/` | Package/archive build/download command logs |
| `fresh-startup-*/` | Separate retained startup attempts, including failures |
| `fresh-startup.json` | Canonical successful fresh startup result after all required checks pass |

The toolchain retains separate identities for KLayout batch 0.30.7 and the
KLayout Python binding 0.30.12. SBY's observed banner is `unknown SBY version`;
its exact script hash and Nix package identity are retained instead of inventing
a version banner. Ngspice 45 is included for the DC polarity reference dependency.

## Reconstruct from the existing artifact bundle

Run from WSL Ubuntu on x86-64 with this project as the current directory. The
host needs Python 3.12, GNU tar, `dpkg-deb`, `chroot` and Linux namespace support.
The reconstruction uses local captured artifacts; it does not install packages
into the active WSL distribution. The target gets an empty venv and offline
hash-checked wheel installation.

Ensure `toolchains/local.wsl.toml` points to the external, authorized PDK on this
machine. Its version, executable and PDK hashes must match the captured recipe.
Nix executable paths remain the captured canonical paths because those paths
are restored inside the new filesystem. The original PDK is read and verified;
its configured files are staged in the private root and are not bundled in the
runtime archive.

From Windows PowerShell, the reproducible startup command is:

```powershell
wsl -d Ubuntu -u root -- /usr/bin/python3 `
  '/mnt/m/Desktop Folders/My Projects/Tapeout Automation/scripts/m1_environment.py' `
  --workspace '/mnt/m/Desktop Folders/My Projects/Tapeout Automation' isolate
```

From a WSL shell, the equivalent is:

```bash
sudo /usr/bin/python3 scripts/m1_environment.py --workspace "$PWD" isolate
```

Root is used to assemble the fresh root filesystem, create its device nodes and
enter isolated namespaces/chroot. Tool commands run as UID/GID 1000. Each attempt
gets a distinct `/tmp/chipflow-m1-fresh-*` root and evidence directory. No source
project, old run or installed tool directory is cleared. Process namespace mounts
are private to the command and do not persist in the active distribution.

On success, inspect `fresh-startup.json`, the attempt's `isolation-check.log`,
`doctor-probe.log`, `ngspice-startup.log`, resolved `toolchain.toml`,
`pdk-path-map.json` and copied `run-evidence/`. The result must report a passing
fresh startup and exit 0. Failed attempts remain in place and never qualify the
environment. `python scripts/m1_environment.py --workspace "$PWD" diagnose`
copies available failed-run diagnostics and prints failed doctor checks.

## Capture a new recipe

Capture is a separate operation. It depends on the recorded installed reference
tools/environments and may download exact Ubuntu artifacts. Reconstruction from
an existing bundle does not require that reference installation.

```bash
/home/sushant/.venvs/presubmit-check/bin/python scripts/m1_environment.py \
  --workspace "$PWD" inspect
/home/sushant/.venvs/presubmit-check/bin/python scripts/m1_environment.py \
  --workspace "$PWD" capture
```

Use a new reviewed output location/version for a different reference rather
than treating a changed binary as the old pin. `capture.json` distinguishes
vendor downloads from locally repacked installed data. The certificate artifact
is explicitly the latter because its installed version was unavailable from
the configured package indexes. Python wheels captured from installed
distributions retain original package metadata/licenses and regenerate wheel
`RECORD`; their SHA-256 values refer to these local repacks.

Ubuntu Base origin and published checksum are recorded in the manifest:
`https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/`.
The selected rootfs SHA-256 is
`6bc2cde3930ad088b3bb46fa45279e96d25bc3810f209850ecbe4722711874f9`.
The active Nix runtime archive SHA-256 is
`6010ad5b3f65c23bc6a4e90240ac96be741e1684ce9c930b46db8f2423494173`.

Keep this bundle private: it includes the private engine/checker packages and
local provenance. It contains no PDK or third-party RAM-model payload. Preserve
the complete artifact set and `capture.json` together; the recipe intentionally
does not depend on future package-index retention or access to the old store.

See [the environment evidence boundary](../../docs/m1-environment.md) for the
initial failed startup, the private `/proc` requirement and the remaining
milestone 6 source-build/clean-WSL qualification.
