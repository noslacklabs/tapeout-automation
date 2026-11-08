# Tapeout Automation (private)

Milestones 1 and 2 of [BUILD_PLAN.md](BUILD_PLAN.md) are complete:
- **Milestone 1**: An immutable current-S8 reference, complete contracts/inventories, and a pinned binary environment installed and probed in fresh isolated Ubuntu userspace.
- **Milestone 2**: Package configuration and reliable execution foundation — strict schema-1 project and pad map loaders, SCL C1D 2 mm frame profile, bounded Yosys elaboration and clock cone analysis, 10 pad qualification checks, post-synthesis mapped netlist checking, provenance tracking, and full CLI validation (`chipflow validate` returning 0 on clean preflight).

Reusable physical-flow adapters and signoff integration remain Milestone 3–4 work.

Milestone 2's final verified package is **0.1.0.dev4**. All review findings are closed; see the [closeout evidence](docs/milestone-2-verification.md#final-closeout-8-november-2026).

See [getting started](docs/getting-started.md), [implementation status](docs/implementation-status.md),
[milestone 2 report](docs/milestone-2.md), [validation contract](docs/m2-validation-contract.md),
[project format](docs/project-format.md) and [result contract](docs/result-contract.md).

No publication, uploads, source-project migration or PDK modification is performed.
