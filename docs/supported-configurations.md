# Current support and qualification boundary

`chipflow` is a generic engine for supported chip projects. Its current scope is
synchronous, single-clock digital standard-cell designs within the **SCL C1D
1.2 µm, 2×2 mm frame profile**, rather than reuse across technologies. A project
supplies RTL, configuration, constraints, pad mapping and tests without editing
the shared engine. S8, a bit-serial 8-bit CPU, is the reference project and the
only design exercised through the physical flow so far; all images in
`docs/images/` are S8 outputs. Broader S8/counter/PWM physical acceptance remains
Milestone 5 work.

| Capability | Current evidence |
|---|---|
| Schema 1, ordered RTL, includes/defines/parameters, constraints and declared dependency snapshot | Implemented, elaborated via Yosys 0.33, and tested on synthetic and real S8 designs |
| One clock and declared synchronous reset, explicit min/max I/O and three corners | Fully validated against elaborated design, clock cone tracing, and derived clock outputs |
| Bounded RTL elaboration, state bit accounting, clock cone tracing, and blackbox detection | Implemented in `chipflow.stages.elaboration` and verified on real S8 (45 state bits) and negative tests |
| SCL C1D 2 mm frame profile, pad mapping, and 10 pad qualification checks | Implemented in `profile-0.1.0.toml` and `chipflow.validation`; verified on S8 and counter fixtures |
| Isolated runner, argument arrays, logs/hashes, timeout, cancellation, failure blocking and concurrent runs | Tested on Windows Python 3.13.5 and installed wheel on WSL2 Ubuntu 24.04 |
| WSL Ubuntu 24.04 x86-64 configured reference environment | Version/hash checks, real Yosys elaboration, and tiny synthesis/geometry probe pass |
| Full preflight validation CLI (`chipflow validate`) | Implemented; exits 0 on valid S8 project, exits 2 on semantic/pad/elaboration violations |
| Post-synthesis mapped netlist verification | Library checker tested on synthetic netlists and a freshly mapped real probe, including detected clock/tie defects and strict DROP/FORCE rejection; integrated into synthesis in Milestone 3, with S8 physical-run evidence |
| Installed wheel with package resources and paths containing spaces and U+00B5 | `work/m2-dist-dev4/chipflow-0.1.0.dev4-py3-none-any.whl`; closeout evidence in `docs/milestone-2-verification.md` |
| Fresh Ubuntu userspace from pinned binary runtime | PASS; offline wheel install, private PID/mount namespaces, no host store/home/venv reuse and real SCL synthesis/geometry probe |
| Clean WSL release installation / independent source rebuild | NOT RUN / not claimed |
| SCL C1D 2 mm reference chip implementation | S8 preflight and physical implementation PASS in Milestone 3; four canonical artifacts produced. Full build exits 2 because downstream verification remains NOT RUN |
| Complete unused pads, alternative drivers, wrapper/CDL alias handling | Current profile includes the bounded `shipped-input-pad-open` unused-slot policy for AIPAD00/IPAD00 with frame-level evidence; full targeted LVS remains later work. Qualified drivers only; broader project acceptance remains pending |
| Current S8 reference / counter and multi-file PWM acceptance | S8 physical flow exercised; generic three-project physical qualification NOT RUN (Milestone 5) |
| Full DRC/LVS/STA/functional/SDF adapters and signoff | NOT RUN in the reusable engine; verification adapters remain Milestone 4 work, broader acceptance Milestone 5, and clean-install/private v0.1 qualification Milestone 6. `signoff_ready` remains false |

The intended release scope remains synchronous single-clock digital standard-cell
designs on the 2 mm SCL C1D frame. Unsupported clock/reset structures, buses or
technology choices cannot receive full validation from this foundation.

As defined in [the first-release boundary](../BUILD_PLAN.md#objective-and-first-release-boundary),
other foundries and die sizes, arbitrary analog design, embedded memory macros,
multiple clock domains, internal generated/gated clocks, asynchronous-reset flops,
latches and bidirectional I/O are outside scope. S8's externally gated SCK output
is a project-specific checked exception. Users supply and verify their design;
the engine automates implementation and checks.
