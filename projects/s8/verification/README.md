# S8 downstream verification

Run the existing S8 verification suite against the artifacts of a completed
`chipflow` physical build. This project-specific runner preserves the immutable
Milestone 1 source snapshot and PDK. It does not implement the reusable
Milestone 4 verification adapters.

Use the configured WSL toolchain and the existing EDA Python environment:

```sh
/home/sushant/.venvs/presubmit-check/bin/python \
  projects/s8/verification/run.py \
  --physical-run projects/s8/builds/<run-id> \
  --toolchain toolchains/local.wsl.toml \
  --output work/s8-downstream/<new-run>
```

`--output` must be new. Inputs are verified before staging. The runner creates
a short `/tmp/chipflow-s8-check-*` symlink to its durable output directory so
Verilog filename fields remain within their 128-byte capacity. It never
overrides the operating system's home directory. Private Python helper copies
use `CHIPFLOW_RUNTIME_ROOT`; the RAM helper preserves the short alias. Source
transformations and hashes are recorded.

The phases are `prepare`, `layout`, `timing`, `regressions`, and `collect`.
Run subsequent phases with `--state work/s8-downstream/<run>/state.json`.
Layout and regression phases require fresh output locations; completed groups
are not silently reused. `retry-simulation-io` reruns only the seeded-SDF and
RAM matrices in new directories and retains all preceding command attempts.
The simulator audit wrapper adds Icarus's `-i` option to flush startup records,
preserving the cell models and SDF arcs. It records actual child exits and
hashes separate stdout/stderr logs.

Checks cover hierarchical/flat DRC, LVS, presubmit, geometry controls,
extraction coverage/units, three-corner STA, clock tracing, equivalence,
CPU proofs, RTL/mapped/routed simulation, nonzero SDF probes, seeded startup,
the external RAM model and pad DC polarity. The captured functional/RAM
harness is qualified for S8's 100 ns, 50% clock, zero interface delays and
30 pF output loads; other settings are rejected.

Outputs include `result.json`, `report.md`, `state.json`,
`verification-inputs.json`, per-command logs, staged helpers and an
`output-manifest.json` with file sizes and SHA-256 hashes. Runtime data remain
in the output directory even if the temporary alias disappears; resuming
recreates the alias.

The raw extraction failure remains **FAIL**. STA/SDF use the separately labelled,
electrically unvalidated routed-length/LEF estimate. Pad slew violations,
stand-in simulation models and manual release items remain explicit blockers.
Aggregate exit 1 denotes a completed run with failed design checks; exit 2
denotes missing or invalid verification evidence. `signoff_ready` remains false.

Run the independent coverage defect controls with:

```sh
python -B -m unittest discover -s projects/s8/verification -p test_evidence.py -v
```

See the [downstream report](../../../docs/s8-downstream-verification.md) for the
recorded run and its actual outcomes.
