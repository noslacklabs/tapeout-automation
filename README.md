# Tapeout Automation (private)

`chipflow` is a private, provenance-first automation engine for digital standard-cell designs on the SCL C1D 1.2 µm, 2×2 mm frame. It validates project configuration and RTL, runs synthesis and physical implementation, and retains inputs, logs, artifact hashes and structured results for each run.

![SCL C1D 1.2 µm 2x2 mm Assembled Die](docs/images/layout-die.png)
*Figure 1: Full assembled die layout on the SCL C1D 1.2 µm 2×2 mm frame (OS0000.gds), featuring 16 peripheral pad sites and central standard-cell core.*

---

## Current status and roadmap

As of **8 November 2026**, Milestones **1, 2 and 3** of [BUILD_PLAN.md](BUILD_PLAN.md) are complete. The current development package is **0.1.0.dev4**.

```mermaid
flowchart LR
    M1["<b>Milestone 1</b><br/>Reference & Environment<br/><code>COMPLETE</code>"] --> M2["<b>Milestone 2</b><br/>Config & Validation<br/><code>COMPLETE</code>"]
    M2 --> M3["<b>Milestone 3</b><br/>Reusable Physical Flow<br/><code>COMPLETE</code>"]
    M3 -.-> M4["<b>Milestone 4</b><br/>Verification Adapters<br/>(DRC / LVS / STA)"]
    M4 -.-> M5["<b>Milestone 5</b><br/>Acceptance Testing<br/>(S8, Counter, PWM)"]
    M5 -.-> M6["<b>Milestone 6</b><br/>Clean-Install & v0.1"]

    classDef done fill:#d4edda,stroke:#28a745,stroke-width:2px;
    classDef pending fill:#fff3cd,stroke:#ffc107,stroke-width:2px;
    class M1,M2,M3 done;
    class M4,M5,M6 pending;
```

- **Milestone 1 — Reference and environment:** Immutable current-S8 engineering reference, contracts and inventories, plus a pinned binary runtime installed and probed in fresh isolated Ubuntu userspace.
- **Milestone 2 — Configuration and execution:** Strict schema-1 project and pad map loaders, bounded Yosys elaboration and clock cone analysis, 10 pad qualification checks, mapped netlist checking, isolated execution and provenance tracking. Full `chipflow validate` returns 0 on clean preflight.
- **Milestone 3 — Reusable physical flow:** Technology staging, synthesis, wrapper and constraint generation, OpenROAD placement/CTS/routing, GDS assembly, CDL generation and metal-only power connectivity checks. The S8 build produces all four canonical physical artifacts.

Milestone 3 verification records **114/114 tests passing in WSL2**, a passing S8 physical flow smoke run, a real CLI build producing the physical artifacts, and wheel verification in an isolated virtual environment. See the [Milestone 3 report](docs/milestone-3.md) and [implementation status](docs/implementation-status.md).

> [!WARNING]
> **Build completion is not yet signoff qualification.** DRC/LVS, timing and simulation verification adapters remain Milestone 4 work; broader S8/counter/PWM acceptance is Milestone 5, and clean-install qualification and the private v0.1 release are Milestone 6. The known raw parasitic extraction failure and unresolved manual items remain open; `signoff_ready` remains false.

---

## End-to-end automation architecture

The `chipflow` engine coordinates immutable run scheduling, deterministic tool invocations, and strict stage contracts from RTL to assembled layout.

```mermaid
flowchart TD
    subgraph Inputs ["1. Inputs & Toolchain Configuration"]
        RTL["RTL Sources<br/><code>*.v</code>"]
        CFG["Project Config<br/><code>chip.toml</code>"]
        PADS["Pad Mapping<br/><code>pads.toml</code>"]
        PDK["SCL C1D 1.2 µm PDK<br/>(LEF, LIB, GDS, CDL)"]
        TC["Pinned Toolchain<br/><code>toolchain.toml</code>"]
    end

    subgraph Preflight ["2. Preflight & Semantic Validation (M2)"]
        VAL_TC["Toolchain & PDK<br/>Fingerprint Verification"]
        VAL_SYNTAX["Project Syntax &<br/>Schema-1 Check"]
        ELAB["Bounded Yosys<br/>Elaboration"]
        PAD_QUAL["10-Rule Pad<br/>Qualification Gate"]
    end

    subgraph PhysicalFlow ["3. Physical Implementation Pipeline (M3)"]
        TECH["Technology Staging<br/>& Cell Allowlist (24 cells)"]
        WRAP["Core Wrapper & SDC<br/>Constraint Generation"]
        SYNTH["Synthesis (Yosys 0.33)<br/>& Clock Gate Verification"]
        FLOORPLAN["Floorplan Generation<br/>& Driver Cell Placement"]
        PNR["OpenROAD Placement,<br/>CTS & Detailed Routing"]
        ROUT_VERIFY["Wire-to-Cell Overlap &<br/>Routing Cleanliness Check"]
        ASM["Top Assembly (2×2 mm Frame)<br/>& Pad Substitution"]
        CDL["Hierarchical CDL<br/>Netlist Generation"]
        PWR_PROBE["KLayout Metal-Only<br/>Power Connectivity Probe"]
    end

    subgraph Outputs ["4. Release Artifacts & Evidence Manifest"]
        GDS["artifacts/chip.gds"]
        CDL_OUT["artifacts/chip.cdl"]
        MAP_V["artifacts/mapped.v"]
        ROUT_V["artifacts/routed.v"]
        RESULT["builds/<run-id>/result.json<br/>(Stage Outcomes & Hashes)"]
    end

    RTL & CFG & PADS --> VAL_SYNTAX
    TC & PDK --> VAL_TC
    VAL_TC & VAL_SYNTAX --> ELAB
    ELAB --> PAD_QUAL

    PAD_QUAL --> TECH
    TECH --> WRAP --> SYNTH --> FLOORPLAN --> PNR --> ROUT_VERIFY --> ASM --> CDL --> PWR_PROBE

    PWR_PROBE --> GDS & CDL_OUT & MAP_V & ROUT_V & RESULT
```

---

## Physical architecture and die frame

The SCL C1D 1.2 µm frame provides a fixed 2000 µm × 2000 µm boundary with 16 peripheral pad sites (12 configurable signal pads and 4 dedicated power pads: 2× `PVDD01`, 2× `PVSS01`). Standard cells are organized in horizontal rows (height 48.7 µm, site pitch 4.2 µm) with M1 power rails and M2 top/bottom power distribution rings.

```mermaid
flowchart TB
    subgraph Die ["SCL C1D 1.2 µm Die (2000 µm × 2000 µm Frame)"]
        direction TB
        subgraph NorthPads ["North Pads (Slots 1 - 4)"]
            direction LR
            N1["Pad 1<br/>Signal (IPAD3D)"] ~~~ N2["Pad 2<br/>VDD (PVDD01)"] ~~~ N3["Pad 3<br/>Signal (OPAD04)"] ~~~ N4["Pad 4<br/>Signal (OPAD04)"]
        end

        subgraph CoreArea ["Standard Cell Core & Routing Channels"]
            direction TB
            PWR_TOP["M2 Top Power Ring (VDD / VSS)"]
            ROW_N["Standard Cell Row N (M1 VDD / VSS Rails)"]
            LOGIC["Core Logic Gates (24 SCL Standard Cells) + INVR04 Drivers"]
            CTS_NET["Balanced Clock Tree (DELBUF Buffers & SCK Gate)"]
            ROW_1["Standard Cell Row 1 (M1 VDD / VSS Rails)"]
            PWR_BOT["M2 Bottom Power Ring (VDD / VSS)"]
            PWR_TOP --- ROW_N --- LOGIC --- CTS_NET --- ROW_1 --- PWR_BOT
        end

        subgraph WestPads ["West Pads (Slots 13 - 16)"]
            direction TB
            W16["Pad 16: Signal"]
            W15["Pad 15: VSS (PVSS01)"]
            W14["Pad 14: Signal"]
            W13["Pad 13: Signal"]
        end

        subgraph EastPads ["East Pads (Slots 5 - 8)"]
            direction TB
            E5["Pad 5: Signal"]
            E6["Pad 6: VDD (PVDD01)"]
            E7["Pad 7: Signal"]
            E8["Pad 8: Signal"]
        end

        subgraph SouthPads ["South Pads (Slots 9 - 12)"]
            direction LR
            S12["Pad 12<br/>Signal"] ~~~ S11["Pad 11<br/>VSS (PVSS01)"] ~~~ S10["Pad 10<br/>Signal"] ~~~ S9["Pad 9<br/>Signal"]
        end
    end

    NorthPads --- CoreArea
    WestPads --- CoreArea
    CoreArea --- EastPads
    CoreArea --- SouthPads
```

### Visual layout and physical evidence

| 2×2 mm Pad Frame Layout | Core Floorplan & Cell Rows |
|:---:|:---:|
| ![SCL C1D 2x2mm Pad Frame](docs/images/frame-light.png) | ![Core Floorplan](docs/images/floorplan-light.png) |
| *Fixed 2×2 mm perimeter frame with 16 pad slots* | *Standard cell placement rows and horizontal channels* |

| Clock Tree Distribution | Routing Density & Congestion |
|:---:|:---:|
| ![Clock Tree Synthesis](docs/images/clock-tree-light.png) | ![Routing Density](docs/images/routing-density-light.png) |
| *Balanced clock tree with clock-gate isolation hook* | *Global and detailed route congestion map* |

![Core Routing and Standard Cell Detail](docs/images/layout-zoom.png)
*Figure 2: Magnified view of core logic rows, standard-cell boundary alignment, and dual-layer routing (M1/M2).*

---

## Physical flow execution and retry loop

The physical flow implements automated retries during detailed routing (`TritonRoute`) by incrementally tuning global route layer adjustments (`0.04 × attempt`) to eliminate router DRC violations before layout assembly.

```mermaid
flowchart TD
    START(["Start Physical Flow"]) --> TECH["Stage Technology<br/>Verify 15 PDK SHA-256 fingerprints & allowed cells"]
    TECH --> WRAP["Generate Core Wrapper<br/>Map pad pins to ports & insert INVR04 drivers"]
    WRAP --> SYNTH["Synthesis (Yosys 0.33)<br/>Verify 0 unmapped primitives & no direct P04 ties"]
    SYNTH --> FP["Floorplan Generation<br/>Derive core bounding box, tracks & pad terminals"]
    FP --> PLACE["Standard Cell Placement<br/>OpenROAD global & detailed placement"]
    PLACE --> CTS_ISO["Clock Gate Isolation<br/>Disconnect non-DFF clock pins during CTS"]
    CTS_ISO --> CTS["Clock Tree Synthesis<br/>TritonCTS buffer insertion"]
    CTS --> CTS_RECONN["Clock Gate Reconnection<br/>Restore gated clock connections"]
    CTS_RECONN --> ROUTE_INIT["Initialize Router<br/>Attempt = 0, layer adjustment = 0.0"]

    ROUTE_INIT --> ROUTE_EXEC["TritonRoute Detailed Route"]
    ROUTE_EXEC --> ROUTE_EVAL{"DRC Violations == 0?"}

    ROUTE_EVAL -- "Yes (Clean)" --> WIRE_CHECK["Wire Overlap Verification<br/>Verify routed DEF against cell layouts"]
    ROUTE_EVAL -- "No (Violations > 0)" --> RETRY_CHECK{"Attempt < Max Retries?"}

    RETRY_CHECK -- "Yes" --> RETRY_INC["Attempt = Attempt + 1<br/>Adjust layer margin (0.04 × Attempt)"] --> ROUTE_EXEC
    RETRY_CHECK -- "No" --> FAIL(["Flow Error: Router DRC unresolved"])

    WIRE_CHECK --> ASM["Top-Level GDS Assembly<br/>Instantiate 2×2 mm frame, core & pad cells"]
    ASM --> CDL_GEN["Generate CDL Schematic<br/>Hierarchical pin and transistor network"]
    CDL_GEN --> PWR_CHECK["KLayout Metal Power Probe<br/>92-point VDD/VSS continuity check"]
    PWR_CHECK --> EMIT(["Emit Physical Artifacts<br/>Copy to artifacts/"])
```

---

## Quick start

Use **Python 3.11 or newer**. Run EDA commands in **WSL Ubuntu 24.04, x86-64**, with the pinned tools and PDK inputs configured in `toolchains/local.wsl.toml`. That file contains machine-specific paths and hashes; use the [toolchain recipe](toolchains/README.md) and [pinned runtime reconstruction](toolchains/milestone1/README.md) for environment details. Installing the Python package alone does not install the EDA tools or PDK.

From the repository root, using the Python environment configured for those tools:

```bash
python -m pip install ./tools/chipflow
chipflow --version

# Check the pinned environment and run synthesis/geometry probes.
chipflow doctor --toolchain toolchains/local.wsl.toml --probe --run-root work/probes

# Check project structure without running EDA tools.
chipflow validate projects/s8/chip.toml --structure-only

# Run full semantic preflight: toolchain, elaboration and configuration.
chipflow validate projects/s8/chip.toml --toolchain toolchains/local.wsl.toml

# Run preflight and physical implementation.
chipflow build projects/s8/chip.toml --toolchain toolchains/local.wsl.toml
```

Each full validation or build creates a retained run under `projects/s8/builds/`. Runs include `result.json`, input and run manifests, and stage evidence. A successful physical implementation produces:

```text
artifacts/chip.gds
artifacts/chip.cdl
artifacts/mapped.v
artifacts/routed.v
```

The current full build **exits 2 even when physical implementation succeeds**, because required downstream verification checks remain `NOT RUN`. Inspect `result.json` for individual stage and check outcomes. Exit 0 from validation covers preflight only; it does not certify a chip. See the [result contract](docs/result-contract.md) for exit-code and evidence rules.

---

## Tests

From `tools/chipflow`, run the suite on Windows or Linux:

```bash
python -m unittest discover -s tests -v
```

Real-tool integration tests skip unless `CHIPFLOW_TEST_TOOLCHAIN` points to a configured toolchain file. To include them in the configured WSL environment:

```bash
CHIPFLOW_TEST_TOOLCHAIN="$PWD/../../toolchains/local.wsl.toml" python -m unittest discover -s tests -v
```

---

## Documentation

- [Build plan](BUILD_PLAN.md) and [implementation status](docs/implementation-status.md)
- [Milestone 1 reference](docs/milestone-1.md), [Milestone 2 report](docs/milestone-2.md) and [Milestone 2 closeout evidence](docs/milestone-2-verification.md#final-closeout-8-november-2026)
- [Milestone 3 physical flow and verification evidence](docs/milestone-3.md)
- [Getting started and environment details](docs/getting-started.md)
- [Project format](docs/project-format.md), [validation contract](docs/m2-validation-contract.md), [stage contracts](docs/stage-contracts.md) and [result contract](docs/result-contract.md)
- [Reference limitations](reference/milestone1/limitations.json)

This repository remains private. The source project and PDK are preserved; publication, uploads and source-project migration are outside the current workflow.
