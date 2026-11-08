# Pinned environment recipe and reproduction gates

Milestone 1 now has an exercised [pinned binary-runtime recipe](milestone1/README.md):
fresh Ubuntu userspace, offline installation, no mounted host store/home or reused
venv, and passing configured synthesis/geometry probes. The recovery notes below
describe the initial inventory and remain as provenance. The copied upstream
flakes were evaluated and **do not resolve to the active OpenROAD binary**; they
are not the active reproduction recipe. The M1 bundle retains the actual runtime
closure, exact dependency artifacts and hashes instead. Clean WSL release
installation remains a milestone 6 gate; independent source rebuilds are not claimed.

The captured reference is WSL Ubuntu 24.04.1, x86-64, Python 3.12.3 and Nix 2.35.4.
`local.wsl.toml` is generated local configuration. Exact executable/technology
SHA-256 values reject drift; changing a pin requires a new reviewed reference.

| Component | Captured pin |
|---|---|
| Yosys synthesis / Liberty conversion | Debian `0.33-5build2`, banner 0.33 / `2584903a060` |
| Icarus compiler and vvp | Debian `12.0-2build2`, banner 12.0 |
| Python / venv | Debian `3.12.3-1ubuntu0.17` |
| OpenROAD | Nix `26Q3`, store output and derivation in `pins/reference-20261107/provenance.json` |
| KLayout batch | Nix `0.30.7`, separately recorded derivation |
| KLayout Python | `0.30.12` |
| gdstk / numpy | `1.0.2` / `2.5.4` |
| Formal Yosys / SBY / Z3 | Captured store identities 0.70 / 0.70 / 5.3.2 and binary hashes; SBY's actual banner is `unknown SBY version` |
| Wider simulation | Separate Python environment with cocotb 2.1.0; see `reference/s8/verification-environment.json` |
| Packaging tools used here | setuptools 80.9.0, wheel 0.45.1 |

`pins/reference-20261107/` contains the recovered lockfiles, flake entry points,
derivations and transitive store closure paths. The upstream checkouts were clean:

- LibreLane `8494d4fea0ddd9bee12ea5e42a38b59438c2b128`.
- OpenROAD-flow-scripts `11aa833a11488c3a540a1d8eb4caa23fd22df703`.

These are historical recovery evidence. Their copied flake entry points reference
other upstream files, and their evaluated OpenROAD outputs differ from the active
26Q3 binary. The small copied directories are not standalone build recipes. Do
not use them as proof of active-tool reproduction. See `milestone1/capture.json`
and its retained binary archive for the environment that actually passed startup.

For Ubuntu, install the exact recorded Debian versions, not unpinned tool names:

```bash
sudo apt-get install yosys=0.33-5build2 iverilog=12.0-2build2 \
  python3.12=3.12.3-1ubuntu0.17 python3.12-venv=3.12.3-1ubuntu0.17
```

Milestone 1 captured the exact installed dependency closure: 45 Ubuntu repository
packages and one explicitly labeled repack of installed certificate data whose
exact repository package was unavailable. All pins were imported into the fresh
rootfs; no version was silently substituted and no global system installation
command above was run. The retained artifacts make startup reproducible offline;
a new WSL distro and independent source rebuild were not exercised.

Readable Python version lists remain in `python-eda-requirements.txt` and
`python-test-requirements.txt`. The complete M1 offline wheel hash lock is
`milestone1/requirements.lock`; its third-party wheels are explicit repacks of
the captured installed binary distributions, with provenance recorded. The
private checker remains a separate wheel; the PDK and RAM model stay external.

Recovery commands used from this workspace in WSL:

```bash
python scripts/capture_environment.py --output reference/s8/environment.json
python scripts/capture_environment.py --output reference/s8/verification-environment.json
python scripts/finalize_reference.py --workspace "$PWD"
```

Run the two environment captures with their respective recorded interpreters.
`recover_recipe.py --output <new-directory>` reads the local upstream metadata and
refuses to overwrite an existing recovery. It never fetches sources or executes
kit installers. `finalize_reference.py` reopens current files, compares recorded
hashes, fingerprints the kit and emits local configuration; it does not certify
an altered input or rerun a chip build.
