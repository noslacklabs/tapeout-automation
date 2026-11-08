# Milestone 1 environment reproduction

This milestone establishes a repeatable **binary runtime** and a fresh Ubuntu
userspace startup/probe. It does not claim that the EDA binaries were rebuilt
from source or that a new WSL distribution was installed. Those release-level
installation checks remain in milestone 6.

The recipe and evidence are in [toolchains/milestone1](../toolchains/milestone1/README.md).
The captured artifact set contains:

| Input | Binding and provenance |
|---|---|
| Ubuntu Base 24.04.3, amd64 | Official 29,983,772-byte root filesystem; SHA-256 checked against Canonical's published checksum |
| System runtime | 46 exact-version Debian artifacts for Yosys 0.33, Icarus 12, Python 3.12.3 and their installed dependency closure |
| Nix EDA runtime | 398 store paths captured into a 968,246,617-byte archive; contains active OpenROAD 26Q3, KLayout batch 0.30.7, formal Yosys/SBY/Z3 and Ngspice 45 closures |
| Python runtime | 15 individually hash-pinned wheels, including the Python KLayout 0.30.12 binding, gdstk, NumPy, cocotb, its dependency, tests, packaging tools and the two private project packages |
| Technology data | Externally configured PDK files, verified and copied into the private startup root; the kit is not part of the runtime archive or wheel bundle |

The binary archive's SHA-256 is
`6010ad5b3f65c23bc6a4e90240ac96be741e1684ce9c930b46db8f2423494173`.
`capture.json`, `nix-runtime-info.json` and `active-derivations.json` retain the
artifact identities, per-store-path metadata and active derivation graph.

The Python wheels other than the two private packages are explicitly repacked
from the recorded installed binary distributions. Their original `METADATA`,
`WHEEL` and license files are retained and `RECORD` is regenerated. Their new
wheel SHA-256 values are local binary-artifact pins, not claims about upstream
PyPI wheel hashes. The `chipflow` wheel is the already-tested development wheel
with SHA-256 `10bb95eb5bea429054e3f2e8df13f4526f4749a3fe85efc99394dc6119b2b9eb`.
The separate checker wheel is built offline from a copied checker source tree.

The recorded `ca-certificates` version was unavailable from the configured apt
indexes. Its artifact is an explicitly marked repack of installed package-owned
data with file hashes in `capture.json`. The remaining 45 Debian artifacts came
from the configured Ubuntu repositories at their exact recorded versions. The
certificate-data repack is not labeled as a vendor-supplied `.deb`. No version
was silently substituted.

## Fresh startup boundary

`scripts/m1_environment.py isolate` verifies the captured artifact hashes before
creating a new `/tmp/chipflow-m1-fresh-*` root filesystem. It extracts the Ubuntu
base, exact Debian artifacts and archived Nix runtime into that new tree.
The active distribution and its store are not changed.

Startup commands execute as UID/GID 1000 inside a chroot with a private mount
and PID namespace. The namespace provides the real `/proc/self/exe` required by
Yosys. It is not a bind mount of the host process tree. A positive isolation
check requires only the namespace's own PID 1 to be visible, rejects host home
and `/mnt/m` visibility, verifies Python's search path, and requires the
`chipflow` import to come from the newly installed venv.

The environment is cleared with `env -i`. A new venv is created without pip;
pip is then bootstrapped from its captured wheel. Installation uses
`--no-index --no-deps --require-hashes` and the complete local wheel lock. There
are no host `site-packages` links, editable installs or `.pth` links to the old
environment. Captured Nix paths are real copied files inside the root, not
symlinks or mounts to the host store. The PID/mount namespaces share the host
kernel; a fresh kernel/WSL installation is outside this gate.

The probe is headless: configured tool startup/version checks, a tiny design
mapped through the real SCL Liberty library, batch KLayout GDS generation and
independent geometry/frame validation through the separately pinned Python
binding. The test run path contains a space and U+00B5. Ngspice startup is checked
separately for the wider reference regression dependency. PDK files are hashed
at staging and rechecked after consumption. Commands, stdout/stderr, path maps,
resolved toolchain configuration and generated evidence are retained.

## Result and acceptance

The fresh-userspace gate **passed** on 8 November 2026. The authoritative result is
[fresh-startup.json](../toolchains/milestone1/fresh-startup.json), with the detailed
[5ftzc883 result](../toolchains/milestone1/fresh-startup-5ftzc883/result.json) and
its `run-evidence/` logs. The configured doctor and real synthesis/geometry probe
passed in `/work/probe path µ/run-20261108T050123.677175Z-c0575d9132d8`.
The isolation assertion observed only PID `1`; host home, host mount paths and
host Python search paths were absent. Ngspice 45 startup exited 0 and all 20
configured external PDK file hashes were unchanged.

Acceptance requires
`exit_code = 0`, positive isolation checks, passing doctor and synthesis/geometry
probe, Ngspice startup and unchanged external PDK fingerprints. A completed
extraction or package install alone does not satisfy this gate.

The first attempt, `fresh-startup-a18axy5u`, is preserved as a failed result.
Its doctor checks passed, but Yosys synthesis exited because `/proc/self/exe`
was absent. That failure prompted the private PID/proc namespace in the current
recipe. It remains a failed environment test. The passing 5ftzc883 rerun is a
separate retained attempt.

## Remaining release evidence

The two previously recovered upstream flake checkouts do not reconstruct the
active OpenROAD store output: the inspected LibreLane flake resolves to a
2026-02-17 package and ORFS resolves to another output. Their recovered lockfiles
remain discovery evidence, not the active environment recipe. The captured
binary closure is the tested recipe for this milestone.

Independent source builds, verification of source-to-binary correspondence,
installation from a newly created WSL distribution, and the full three-project
installed-release qualification remain milestone 6 work. This startup gate does
not turn the known raw extraction FAIL or any manual signoff item into PASS.
