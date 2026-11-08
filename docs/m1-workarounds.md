# Reference workaround and assumption register

This register covers the transformations and tool/model accommodations actually present in the pinned reference flow, plus explicitly identified historical/disabled variants. The source project and kit were read only. Exact source SHA-256 values, line spans, applicability, limitations and current regression metadata are in [m1-contract-audit.json](../reference/s8/m1-contract-audit.json). Source comments and historical results are evidence with limits, not independent electrical validation.

## Ownership

The engine owns scheduling, immutable evidence, process execution, completeness checks and result aggregation. The process profile owns library/geometry/model transformations, frames/pads and process limitations. The S8 adapter owns CPU/RAM expectations, gated SCK, its CTS exception and seeded simulation variants. Local executable/model/PDK paths stay in toolchain configuration. Kit originals remain unchanged.

## Workarounds

### P01 — Filtered cells

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Exclude asynchronous/latch/tristate and high-risk cells including MX2101 from working Liberty copies.

Implementation: [flow/synth.sh:14–25](<M:/My Projects/Tapeout Chip/flow/synth.sh:14>), [flow/tech/filt.py:6–23](<M:/My Projects/Tapeout Chip/flow/tech/filt.py:6>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:91–102](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:91>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P02 — Poly-pin cell exclusions

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Default FORCE exclusions avoid cells whose pins require vias over poly. ALLOW_VIA_OVER_POLY is a reviewed opt-out, not a waiver.

Implementation: [flow/synth.sh:17–22](<M:/My Projects/Tapeout Chip/flow/synth.sh:17>), [flow/libcheck/via_poly_table.py:1–42](<M:/My Projects/Tapeout Chip/flow/libcheck/via_poly_table.py:1>).
Historical evidence: [reports/flow-status.md:158](<M:/My Projects/Tapeout Chip/reports/flow-status.md:158>), [scl-pdk-1.2um/TOOL_EVALUATION.md:79](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:79>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-C17.2`.

### P03 — ABC buffer and timing option

Owner: **process/tool**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Keep DELBUF so ABC has a buffer. Avoid incompatible abc -D constraint timing on pinned Yosys0.33.

Implementation: [flow/synth.sh:14–16](<M:/My Projects/Tapeout Chip/flow/synth.sh:14>), [flow/synth.sh:32–40](<M:/My Projects/Tapeout Chip/flow/synth.sh:32>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:91–99](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:91>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P04 — Physical constant ties

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Map constants through VDDCON/TIEHI and VSSCON/TIELO before routing.

Implementation: [flow/synth.sh:41–42](<M:/My Projects/Tapeout Chip/flow/synth.sh:41>), [tb/gate_build.sh:22–25](<M:/My Projects/Tapeout Chip/tb/gate_build.sh:22>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:103–104](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:103>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P05 — LEF width utilization

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Use LEF widths and row budgets instead of interpreting Liberty area as square micrometres.

Implementation: [flow/tech/width.py:1–23](<M:/My Projects/Tapeout Chip/flow/tech/width.py:1>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:99](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:99>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P06 — Working abstract grid

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Change CORE site and metal1 pitch to4.2um and snap RECT coordinates to0.005um in working LEF only.

Implementation: [flow/tech/make_tech.py:7–14](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:7>), [flow/tech/make_tech.py:48–54](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:48>), [flow/tech/make_tech.py:173–200](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:173>).
Historical evidence: [reports/flow-status.md:136–143](<M:/My Projects/Tapeout Chip/reports/flow-status.md:136>), [scl-pdk-1.2um/TOOL_EVALUATION.md:95–96](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:95>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P07 — Abstract width rounding

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Round non-filler widths upward to4.2um sites and add a site when a remainder would leave an illegal small gap.

Implementation: [flow/tech/make_tech.py:192–200](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:192>).
Historical evidence: [reports/flow-status.md:138–143](<M:/My Projects/Tapeout Chip/reports/flow-status.md:138>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P08 — Missing metal1 obstruction

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Read real GDS metal1 and add geometry absent from the shipped LEF to working obstruction views.

Implementation: [flow/tech/make_tech.py:157–164](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:157>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:74](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:74>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P09 — Metal2 pin straps

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Trace connected GDS metal2 through cell vias into signal pins and rebuild remaining metal2 as obstruction.

Implementation: [flow/tech/make_tech.py:107–134](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:107>), [flow/tech/make_tech.py:165–169](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:165>), [flow/tech/make_tech.py:205–212](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:205>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:74](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:74>), [reports/flow-status.md:141–143](<M:/My Projects/Tapeout Chip/reports/flow-status.md:141>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P10 — Hidden material via keepouts

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Add via1 obstructions for contact, shrunk poly and diffusion edges with defined signal-pin access openings.

Implementation: [flow/tech/make_tech.py:138–156](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:138>).
Historical evidence: [reports/flow-status.md:140–141](<M:/My Projects/Tapeout Chip/reports/flow-status.md:140>), [scl-pdk-1.2um/TOOL_EVALUATION.md:100–101](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:100>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P11 — MX201B matched offset

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Shift MX201B5.2um within50.4um abstract and use the identical GDS placement/checker offset to put its pins on tracks.

Implementation: [flow/tech/make_tech.py:228–241](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:228>), [flow/assemble/assemble.py:199–216](<M:/My Projects/Tapeout Chip/flow/assemble/assemble.py:199>), [flow/checks/wire_vs_cells.py:48–61](<M:/My Projects/Tapeout Chip/flow/checks/wire_vs_cells.py:48>).
Historical evidence: [reports/flow-status.md:141–143](<M:/My Projects/Tapeout Chip/reports/flow-status.md:141>), [scl-pdk-1.2um/TOOL_EVALUATION.md:84](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:84>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P12 — Separated unmirrored rows

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Replace default placement rows with spaced R0 rows because Y-only site symmetry does not permit alternating abutted supply rails.

Implementation: [flow/pnr/pnr.tcl:25–34](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:25>).
Historical evidence: [reports/flow-status.md:178–183](<M:/My Projects/Tapeout Chip/reports/flow-status.md:178>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P13 — Track and pin alignment

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Use4.2um metal1/metal2 grids with profile offsets and assembly-matched pin positions.

Implementation: [flow/floorplan.py:25–42](<M:/My Projects/Tapeout Chip/flow/floorplan.py:25>), [flow/pnr/pnr.tcl:36–44](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:36>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:75](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:75>), [reports/flow-status.md:178–183](<M:/My Projects/Tapeout Chip/reports/flow-status.md:178>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P14 — Reserve rail metal

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Block routing over row rail regions that assembly draws continuously through cell gaps.

Implementation: [flow/pnr/pnr.tcl:46–53](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:46>), [flow/assemble/assemble.py:244–247](<M:/My Projects/Tapeout Chip/flow/assemble/assemble.py:244>).
Historical evidence: [reports/flow-continuation.md:18–25](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:18>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P15 — NND301 left padding

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Reserve one left site on NND301 to avoid the known AND201-to-NND301 boron-width failure.

Implementation: [flow/pnr/pnr.tcl:75–76](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:75>).
Historical evidence: [reports/flow-status.md:139–143](<M:/My Projects/Tapeout Chip/reports/flow-status.md:139>), [scl-pdk-1.2um/TOOL_EVALUATION.md:72–73](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:72>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P16 — Tested filler pattern

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Use FILLER2 every4.2um plus continuous rails and FILLER2/FILLER4 in the MX201B left gap. Do not substitute arbitrary shipped fillers.

Implementation: [flow/assemble/assemble.py:221–247](<M:/My Projects/Tapeout Chip/flow/assemble/assemble.py:221>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:72–73](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:72>), [reports/flow-continuation.md:18–25](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:18>). Historical/reference evidence only; not generic electrical or foundry qualification.

### P17 — Explicit stronger output drivers

Owner: **process/S8**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Keep and lock INVR04 near OPAD04 rather than use the document INVR02 suggestion. Double inversion preserves port polarity; modeled capacitance motivates the change.

Implementation: [flow/gen_core.py:4–7](<M:/My Projects/Tapeout Chip/flow/gen_core.py:4>), [flow/gen_core.py:48–54](<M:/My Projects/Tapeout Chip/flow/gen_core.py:48>), [flow/pnr/pnr.tcl:56–64](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:56>), [flow/pads.tsv:1–13](<M:/My Projects/Tapeout Chip/flow/pads.tsv:1>).
Historical evidence: [reports/flow-status.md:147–150](<M:/My Projects/Tapeout Chip/reports/flow-status.md:147>), [reports/flow-continuation.md:37–40](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:37>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-PAD-001`.

### P18 — Input buffering

Owner: **process/S8**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

When REPAIR is enabled insert DELBUF on data inputs and repair slew/capacitance; preserve clock exception and check actual pad loading.

Implementation: [flow/pnr/pnr.tcl:78–87](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:78>).
Historical evidence: [reports/flow-status.md:111–115](<M:/My Projects/Tapeout Chip/reports/flow-status.md:111>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-PAD-001`.

### P19 — Clock buffer choice

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Use noninverting CDRI01 instead of the supplied template inverter. Clock-buffer capacitance/fanout interpretations remain explicit limitations.

Implementation: [flow/pnr/pnr.tcl:99–101](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:99>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:81](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:81>), [scl-pdk-1.2um/TOOL_EVALUATION.md:97](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:97>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-CLK-001`.

### T01 — Tool-compatible script names

Owner: **tool**. State: **applied**. Scope: OpenROAD26Q3 script loader.

Copy OpenROAD Tcl entries into working directories and invoke relative names. Embedded Yosys/Tcl path text still needs staging.

Implementation: [flow/pnr.sh:11–15](<M:/My Projects/Tapeout Chip/flow/pnr.sh:11>), [flow/pnr.sh:23–27](<M:/My Projects/Tapeout Chip/flow/pnr.sh:23>), [flow/run_sta.sh:7–8](<M:/My Projects/Tapeout Chip/flow/run_sta.sh:7>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:86–87](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:86>). Historical/reference evidence only; not generic electrical or foundry qualification.

### T02 — CTS gate disconnect

Owner: **S8/tool**. State: **applied**. Scope: S8 externally gated SCK exception on pinned OpenROAD26Q3.

Disconnect non-DFF loads from clk during CTS then reconnect to the root. Future use requires an S8-only structural before/after check.

Implementation: [flow/pnr/pnr.tcl:90–101](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:90>).
Historical evidence: [reports/flow-status.md:111–115](<M:/My Projects/Tapeout Chip/reports/flow-status.md:111>), [scl-pdk-1.2um/TOOL_EVALUATION.md:85](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:85>). Historical/reference evidence only; not generic electrical or foundry qualification.

### T03 — Overlap check and route retries

Owner: **tool/process**. State: **applied with legacy aggregation limitation**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Independently compare routed nets/cell metal and preserve attempts. Legacy retry exhaustion is not a hard failure; require a fresh positively accepted attempt.

Implementation: [flow/pnr.sh:20–40](<M:/My Projects/Tapeout Chip/flow/pnr.sh:20>), [flow/checks/wire_vs_cells.py:1–10](<M:/My Projects/Tapeout Chip/flow/checks/wire_vs_cells.py:1>).
Historical evidence: [reports/flow-status.md:141–143](<M:/My Projects/Tapeout Chip/reports/flow-status.md:141>), [scl-pdk-1.2um/TOOL_EVALUATION.md:84](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:84>). Historical/reference evidence only; not generic electrical or foundry qualification.

### T04 — Metal-only power check

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Trace metal1/via1/metal2 from supply pads to every row independently of LVS substrate/well connectivity.

Implementation: [flow/checks/power_connect.py:2–7](<M:/My Projects/Tapeout Chip/flow/checks/power_connect.py:2>), [flow/checks/power_connect.py:19–28](<M:/My Projects/Tapeout Chip/flow/checks/power_connect.py:19>), [flow/checks/power_connect.py:35–70](<M:/My Projects/Tapeout Chip/flow/checks/power_connect.py:35>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:82](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:82>), [reports/flow-continuation.md:18–25](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:18>). Historical/reference evidence only; not generic electrical or foundry qualification.

### T05 — Supplement deck omissions

Owner: **process**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Check routed vias over poly/contact, drawn metal width, notches, grid and top extent separately. Preserve inherited kit warnings and manual policy items.

Implementation: [flow/checks/own_checks.py:2–9](<M:/My Projects/Tapeout Chip/flow/checks/own_checks.py:2>), [flow/checks/own_checks.py:93–120](<M:/My Projects/Tapeout Chip/flow/checks/own_checks.py:93>).
Historical evidence: [reports/flow-continuation.md:18–25](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:18>), [reports/signoff-checklist.md:8–24](<M:/My Projects/Tapeout Chip/reports/signoff-checklist.md:8>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-C17.2`.

### T06 — Flat DRC crosscheck

Owner: **process/tool**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Replace only deep with flat in a separately hashed deck working copy and compare positive rule coverage/results.

Implementation: [flow/run_all.sh:51–60](<M:/My Projects/Tapeout Chip/flow/run_all.sh:51>).
Historical evidence: [reports/flow-continuation.md:18–25](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:18>). Historical/reference evidence only; not generic electrical or foundry qualification.

### T07 — Local inline CDL conversion

Owner: **tool/process**. State: **applied in reference only; generic reuse blocked**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Inline selected subcircuits instead of executing the kit converter. Current regex parser ignores assign aliases and is unqualified for generic conversion.

Implementation: [flow/assemble/make_cdl.py:4–7](<M:/My Projects/Tapeout Chip/flow/assemble/make_cdl.py:4>), [flow/assemble/make_cdl.py:44–54](<M:/My Projects/Tapeout Chip/flow/assemble/make_cdl.py:44>), [flow/assemble/make_cdl.py:89–96](<M:/My Projects/Tapeout Chip/flow/assemble/make_cdl.py:89>).
Historical evidence: [PLAN_REVIEW.md:30–62](<M:/Desktop Folders/My Projects/Tapeout Automation/PLAN_REVIEW.md:30>). Historical/reference evidence only; not generic electrical or foundry qualification.

### T08 — Distinct parasitic estimate

Owner: **process/model**. State: **applied diagnostic estimate**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Preserve raw SPEF and generate a separate per-net routed-length/LEF capacitance estimate retaining network/resistance. It does not validate extraction or bound real parasitics.

Implementation: [flow/run_sta.sh:15–32](<M:/My Projects/Tapeout Chip/flow/run_sta.sh:15>), [flow/sta/scale_spef.py:2–17](<M:/My Projects/Tapeout Chip/flow/sta/scale_spef.py:2>), [flow/sta/scale_spef.py:74–107](<M:/My Projects/Tapeout Chip/flow/sta/scale_spef.py:74>).
Historical evidence: [reports/flow-continuation.md:44–55](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:44>), [reports/flow-status.md:186–189](<M:/My Projects/Tapeout Chip/reports/flow-status.md:186>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-RCX-001`.

### T09 — Hierarchical SPEF annotation

Owner: **tool**. State: **applied**. Scope: Pinned OpenROAD/OpenSTA hierarchy behavior.

Use link_design -hier before read_spef -path u_core to preserve wrapper/core timing identity.

Implementation: [flow/sta/sta.tcl:12–15](<M:/My Projects/Tapeout Chip/flow/sta/sta.tcl:12>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:87](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:87>). Historical/reference evidence only; not generic electrical or foundry qualification.

### T10 — Full SDF triples

Owner: **tool**. State: **applied**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Use write_sdf -include_typ and divider / so Icarus does not silently use zero for an empty typical field.

Implementation: [flow/sta/sta.tcl:98](<M:/My Projects/Tapeout Chip/flow/sta/sta.tcl:98>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:90](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:90>), [reports/routed-random-start-20261107.md:31–41](<M:/My Projects/Tapeout Chip/reports/routed-random-start-20261107.md:31>). Historical/reference evidence only; not generic electrical or foundry qualification.

### S01 — Behavioral random-start smoke flop

Owner: **S8/simulation**. State: **applied only to named zero-delay/mapped checks**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Rename kit DFFL11 and append a behavioral initialized replacement for zero-delay smoke/mapped campaigns. The replacement lacks specify arcs and cannot qualify SDF.

Implementation: [flow/run_sta.sh:34–43](<M:/My Projects/Tapeout Chip/flow/run_sta.sh:34>), [tb/gate_build.sh:29–43](<M:/My Projects/Tapeout Chip/tb/gate_build.sh:29>).
Historical evidence: [reports/flow-continuation.md:50–55](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:50>), [reports/routed-random-start-20261107.md:9–19](<M:/My Projects/Tapeout Chip/reports/routed-random-start-20261107.md:9>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-SIM-001`.

### S02 — Seeded original UDP startup

Owner: **S8/simulation**. State: **applied in seeded routed regression**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Pulse original DFFL11 startup set/reset on a private model copy with deterministic seed/instance hash; retain output buffers and entire specify block and verify initialized bits.

Implementation: [tb/routed_util.py:23–70](<M:/My Projects/Tapeout Chip/tb/routed_util.py:23>).
Historical evidence: [reports/routed-random-start-20261107.md:9–41](<M:/My Projects/Tapeout Chip/reports/routed-random-start-20261107.md:9>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-SIM-001`.

### S03 — Legacy SDF copy repair

Owner: **tool/simulation**. State: **legacy compatibility path only**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Rewrite old min::max triples to min:min:max on a working copy only. Newly written full triples remain unchanged; no independent typical model is created.

Implementation: [tb/routed_util.py:73–86](<M:/My Projects/Tapeout Chip/tb/routed_util.py:73>).
Historical evidence: [reports/flow-status.md:189](<M:/My Projects/Tapeout Chip/reports/flow-status.md:189>), [reports/routed-random-start-20261107.md:31–41](<M:/My Projects/Tapeout Chip/reports/routed-random-start-20261107.md:31>). Historical/reference evidence only; not generic electrical or foundry qualification.

### S04 — Missing IPAD30 model stand-in

Owner: **process/simulation**. State: **applied in routed model**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

Provide buffer plus specify arc for IPAD30. Annotated delay is retained but Schmitt thresholds/hysteresis and analog behavior are not modeled.

Implementation: [tb/routed_top.v:12–18](<M:/My Projects/Tapeout Chip/tb/routed_top.v:12>).
Historical evidence: [reports/flow-continuation.md:50–55](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:50>), [scl-pdk-1.2um/TOOL_EVALUATION.md:89–90](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:89>). Historical/reference evidence only; not generic electrical or foundry qualification.
Limitation IDs: `SCL-C1D-MODEL-001`.

### T11 — Passive model-data copies

Owner: **process/model**. State: **applied**. Scope: 72 rail-level DC measurements only.

Build selected MOS/diode/resistor model libraries locally instead of environment-dependent include headers; reject executable directives and hash consumed data.

Implementation: [flow/libcheck/pad_polarity.py:36–65](<M:/My Projects/Tapeout Chip/flow/libcheck/pad_polarity.py:36>), [flow/libcheck/pad_polarity.py:84–108](<M:/My Projects/Tapeout Chip/flow/libcheck/pad_polarity.py:84>).
Historical evidence: [reports/flow-continuation.md:37–40](<M:/My Projects/Tapeout Chip/reports/flow-continuation.md:37>). Historical/reference evidence only; not generic electrical or foundry qualification.

### L01 — Historical next_state trim

Owner: **tool**. State: **historical experiment; not current baseline**. Scope: Yosys0.62 experiment only.

Yosys0.62 needed next_state whitespace trimmed in a working Liberty. Pinned Yosys0.33 reads the original data; do not silently add that transformation.

Implementation: [scl-pdk-1.2um/TOOL_EVALUATION.md:55–57](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:55>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:14–16](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:14>). Historical/reference evidence only; not generic electrical or foundry qualification.

### L02 — Closed poly-pin access experiment

Owner: **process/experiment**. State: **disabled experimental option**. Scope: Pinned SCL_C1D_PDK_17092026 reference toolchain.

NO_POLY_PIN_VIAS removes access openings at poly-overlapping pins. The experiment had pin-access failures and is not the supported default.

Implementation: [flow/tech/make_tech.py:149–155](<M:/My Projects/Tapeout Chip/flow/tech/make_tech.py:149>).
Historical evidence: [scl-pdk-1.2um/TOOL_EVALUATION.md:79](<M:/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/TOOL_EVALUATION.md:79>). Historical/reference evidence only; not generic electrical or foundry qualification.

## Baseline-running hazards

- **LEGACY-EXIT:** Wrappers print child tool exits without propagating them; run_all.sh pipes stages without pipefail. Validate positive outputs and every child process. Sources: [flow/run_all.sh:17–22](<M:/My Projects/Tapeout Chip/flow/run_all.sh:17>), [flow/run_decks.sh:13–24](<M:/My Projects/Tapeout Chip/flow/run_decks.sh:13>), [formal/run.sh:19–25](<M:/My Projects/Tapeout Chip/formal/run.sh:19>), [formal/lec.sh:65–66](<M:/My Projects/Tapeout Chip/formal/lec.sh:65>).
- **LEGACY-ROUTE:** Exhausted route retries lack a hard failure. Require a current accepted route_try.txt, successful last attempt, complete zero-violation report and zero validated overlaps. Sources: [flow/pnr.sh:24–40](<M:/My Projects/Tapeout Chip/flow/pnr.sh:24>).
- **LEGACY-HOME:** Simulation output/model paths and shell interpreter/checker paths derive from HOME. Use explicit recorded private-HOME/model/tool links or a copy-only path adapter, preserving existing results. Sources: [tb/run.py:20–21](<M:/My Projects/Tapeout Chip/tb/run.py:20>), [tb/routed_random.py:22](<M:/My Projects/Tapeout Chip/tb/routed_random.py:22>), [tb/run_ram_model.py:30–33](<M:/My Projects/Tapeout Chip/tb/run_ram_model.py:30>), [flow/env.sh:14–18](<M:/My Projects/Tapeout Chip/flow/env.sh:14>), [flow/run_all.sh:39–40](<M:/My Projects/Tapeout Chip/flow/run_all.sh:39>).
- **LEGACY-SPACE:** Embedded Yosys, SBY and SPICE path text is not made safe by argument arrays. Use a retained original-to-staged map and tool-compatible private paths. Sources: [flow/synth.sh:33–40](<M:/My Projects/Tapeout Chip/flow/synth.sh:33>), [formal/lec.sh:19–27](<M:/My Projects/Tapeout Chip/formal/lec.sh:19>), [flow/libcheck/pad_polarity.py:76–80](<M:/My Projects/Tapeout Chip/flow/libcheck/pad_polarity.py:76>).
- **LEGACY-DRC:** Empty marker XML categories can currently be summarized as zero violations. Require expected nonempty executed-rule coverage. Sources: [PLAN_REVIEW.md:11–28](<M:/Desktop Folders/My Projects/Tapeout Automation/PLAN_REVIEW.md:11>).
- **LEGACY-ALIAS:** Current CDL regex conversion ignores continuous assign aliases. Generic reuse needs structured connectivity or a rigorously validated input boundary. Sources: [flow/assemble/make_cdl.py:44–54](<M:/My Projects/Tapeout Chip/flow/assemble/make_cdl.py:44>), [PLAN_REVIEW.md:30–62](<M:/Desktop Folders/My Projects/Tapeout Automation/PLAN_REVIEW.md:30>).
- **LEGACY-STA:** P&R hard-codes clk/100ns/1pF core loads while chip STA uses separate period/external loads. Record baseline assumptions; unified constraint propagation is a later implementation gate. Sources: [flow/pnr/pnr.tcl:66–73](<M:/My Projects/Tapeout Chip/flow/pnr/pnr.tcl:66>), [flow/sta/sta.tcl:17–25](<M:/My Projects/Tapeout Chip/flow/sta/sta.tcl:17>).
- **LEGACY-RAM-RC:** RAM run_one parses vvp stdout but discards vvp returncode. Positive case summaries and specifically detected timing violations remain mandatory; later adapter must retain process codes. Sources: [tb/run_ram_model.py:100–141](<M:/My Projects/Tapeout Chip/tb/run_ram_model.py:100>).

## Current wider-regression inventory

All 89 source files still match the earlier inventory. Current RTL SHA-256 is `fa52f0fb87289df5dd2f1f5e6004538fb513956fc834ad78556c39021a86ca46`; the earlier physical source differs and remains separate historical evidence. The source currently declares 16 cocotb cases in `test_s8` and 4 in `test_demos`, preserving the expected 20 per run. The routed DFF count must come from the frozen current netlist, rather than assuming historical 45.

The existing five-seed campaign is 15 runs/300 tests, with 50 programs and 100 instructions per run. The RAM positive matrix is 36 cases: four demos, three corners and three duty cycles. The historical four-case 25 MHz negative is `--period 40` without `--routed`; combining both requests 36 cases. The pad-polarity runner performs 36 SPICE sweeps and validates 72 rail measurements. CPU formal has seven tasks, but reset/ISA tasks are bounded and `isa_cover` is coverage; retain these proof limits.

Routed equivalence additionally depends on the exact `G/du.lib`; the routed netlist and shim alone do not recreate its conversion input. Private-HOME or path adaptation must be recorded in a copied baseline environment, without silent upstream/model edits.

Milestone 1 requires a complete provenance-bound manifest of wider verification and honest frozen reference evidence. It does not constitute the later three-design engine qualification. Inventoried campaigns remain NOT RUN until actual logs/results are bound to the frozen inputs.
