"""Place and Route stage: floorplanning, OpenROAD placement, CTS and detailed routing with retries."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from typing import Any

from chipflow.config.pads import build_pad_plan, load_pad_map
from chipflow.config.project import ProjectConfig
from chipflow.runner.execution import (
    Artifact,
    Assessment,
    CommandResult,
    StageSpec,
    atomic_write_json,
    sha256_file,
)
from chipflow.runner.results import CheckResult, CheckStatus
from chipflow.stages.envelope import StageEnvelope
from chipflow.stages.floorplan import build_floorplan
from chipflow.technologies.profile import ProcessProfile
from chipflow.toolchain import Toolchain, verify_file


def _validate_nonempty(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"required output {path.name} is missing or empty")


PNR_RUNNER_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from chipflow.stages.wire_overlap import check_wire_vs_cells

config = json.loads(Path("pnr_config.json").read_text(encoding="utf-8"))
openroad = config["openroad_executable"]
threads = str(config["threads"])
attempts = config["route_attempts"]
drt_iters = str(config["detailed_route_iterations"])

# 1. Placement and CTS
env = os.environ.copy()
env.update({
    "T": ".",
    "FP": "floorplan.tcl",
    "NETLIST": "mapped.v",
    "TOP": config["top_core"],
    "OUT": ".",
    "THREADS": threads,
    "REPAIR": "1" if config["buffer_inputs"] else "0",
    "DENSITY": config["density"],
    "SLEW_MARGIN": str(config["slew_margin"]),
    "CAP_MARGIN": str(config["cap_margin"]),
    "CTS_ARGS": f"-distance_between_buffers {config['clock_buffer_distance_um']}",
    "CLOCK_PORT": config["clock_port"],
    "CLOCK_PERIOD": str(config["clock_period"]),
    "INPUT_TRANSITION": str(config["input_transition"]),
    "ISOLATE_CTS_GATES": "1" if config["cts_clock_gate_isolation"] else "0",
})

print("Running placement and CTS...")
with open("pnr.log", "w", encoding="utf-8") as log:
    p = subprocess.run([openroad, "-no_init", "-exit", "pnr.tcl"], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env)

if p.returncode != 0 or not Path("placed.odb").is_file() or Path("placed.odb").stat().st_size == 0:
    summary = {
        "succeeded": False,
        "phase": "placement",
        "returncode": p.returncode,
        "error": "OpenROAD placement failed or placed.odb missing",
        "attempts": 0,
    }
    Path("route_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    sys.exit(1)

print("Placement complete.")

# 2. Detailed routing retry loop
best_violations = None
best_overlaps = None
succeeded = False
successful_try = None

for try_idx in range(attempts):
    print(f"Routing attempt {try_idx} of {attempts}...")
    for f in ("routed.def", "routed.v", "routed.odb"):
        Path(f).unlink(missing_ok=True)
    env["TRY"] = str(try_idx)
    env["DRT_ITERS"] = drt_iters
    with open(f"route_try{try_idx}.log", "w", encoding="utf-8") as log:
        p = subprocess.run([openroad, "-no_init", "-exit", "route.tcl"], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env)
    shutil.copyfile(f"route_try{try_idx}.log", "route.log")

    log_txt = Path("route.log").read_text(encoding="utf-8", errors="ignore")
    m = re.findall(r"Number of violations\s*[=:]\s*(\d+)", log_txt)
    violations = int(m[-1]) if m else (0 if Path("route_drc.rpt").is_file() and Path("route_drc.rpt").stat().st_size == 0 else 999999)
    best_violations = violations

    overlaps_count = 999999
    overlap_result = {"nets": 0, "cells": 0, "problems": [{"error": "routed.def missing"}]}
    if Path("routed.def").is_file() and Path("routed.def").stat().st_size > 0:
        try:
            overlap_result = check_wire_vs_cells("routed.def", "core.gds", "cells.lef")
            overlaps_count = len(overlap_result.get("problems", []))
        except Exception as e:
            print(f"Overlap check error: {e}")
            overlap_result = {"problems": [{"error": str(e)}]}
            overlaps_count = 1

    Path(f"overlaps_try{try_idx}.json").write_text(json.dumps(overlap_result, indent=2), encoding="utf-8")
    shutil.copyfile(f"overlaps_try{try_idx}.json", "overlaps.json")
    best_overlaps = overlaps_count

    print(f"Attempt {try_idx}: rc={p.returncode}, router violations={violations}, unexpected overlaps={overlaps_count}")
    if p.returncode == 0 and violations == 0 and overlaps_count == 0:
        succeeded = True
        successful_try = try_idx
        break

summary = {
    "succeeded": succeeded,
    "phase": "routing",
    "attempts_run": try_idx + 1,
    "successful_attempt": successful_try,
    "router_violations": best_violations,
    "unexpected_overlaps": best_overlaps,
}
Path("route_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
sys.exit(0 if succeeded else 1)
"""


def _pnr_tcl_script(top_cell: str, clock_port: str) -> str:
    return f"""# Place and route script generated by chipflow. PRIVATE.
set T $::env(T)
set OUT $::env(OUT)
set_thread_count $::env(THREADS)
source $::env(FP)

read_lef $T/tech.lef
read_lef $T/cells.lef
define_corners tt ss ff
read_liberty -corner tt $T/core_typ.lib
read_liberty -corner ss $T/core_max.lib
read_liberty -corner ff $T/core_min.lib
read_verilog $::env(NETLIST)
link_design $::env(TOP)

initialize_floorplan -site CORE -die_area $BLOCK -core_area $BLOCK
set block [ord::get_db_block]
set dbu [$block getDbUnitsPerMicron]
proc to_dbu {{v}} {{ return [expr {{int(round($v * 1000))}}] }}

foreach row [$block getRows] {{ odb::dbRow_destroy $row }}
set site [[ord::get_db] findMaster INVR01]
set site [$site getSite]
set i 0
foreach y $ROWS {{
  odb::dbRow_create $block ROW_$i $site [to_dbu $BLOCK_LO] [to_dbu $y] R0 HORIZONTAL $N_SITES [to_dbu 4.2]
  incr i
}}

make_tracks metal1 -x_offset $X_OFF -x_pitch $PITCH -y_offset $Y_OFF -y_pitch $PITCH
make_tracks metal2 -x_offset $X_OFF -x_pitch $PITCH -y_offset $Y_OFF -y_pitch $PITCH

foreach p $PINS {{
  lassign $p name layer x y
  place_pin -pin_name $name -layer $layer -location [list $x $y] -pin_size {{2.5 2.5}}
}}

set tech [ord::get_db_tech]
set m1 [$tech findLayer metal1]
foreach y $ROWS {{
  foreach {{a b}} [list 0 9.0 39.7 48.7] {{
    odb::dbObstruction_create $block $m1 [to_dbu $BLOCK_LO] [to_dbu [expr {{$y + $a}}]] \
      [to_dbu [lindex $BLOCK 2]] [to_dbu [expr {{$y + $b}}]]
  }}
}}

foreach d $DRIVERS {{
  lassign $d name x y
  set inst [$block findInst $name]
  if {{$inst == "NULL"}} {{ error "driver $name not in netlist" }}
  $inst setOrient R0
  $inst setLocation [to_dbu $x] [to_dbu $y]
  $inst setPlacementStatus LOCKED
}}

create_clock -name $::env(CLOCK_PORT) -period $::env(CLOCK_PERIOD) [get_ports $::env(CLOCK_PORT)]
set_input_transition $::env(INPUT_TRANSITION) [all_inputs]
set_load 1.0 [all_outputs]
set_wire_rc -signal -resistance 2e-5 -capacitance 2.5e-4
set_wire_rc -clock -resistance 2e-5 -capacitance 2.5e-4

set_placement_padding -masters NND301 -left 1

global_placement -density $::env(DENSITY)
if {{$::env(REPAIR)}} {{
  estimate_parasitics -placement
  buffer_ports -inputs -buffer_cell DELBUF
  repair_design -slew_margin $::env(SLEW_MARGIN) -cap_margin $::env(CAP_MARGIN)
}}
detailed_placement
check_placement

if {{$::env(ISOLATE_CTS_GATES)}} {{
  set clknet [$block findNet $::env(CLOCK_PORT)]
  set gated {{}}
  foreach it [$clknet getITerms] {{
    if {{[$it isInputSignal] && ![string match DFF* [[[$it getInst] getMaster] getName]]}} {{ lappend gated $it }}
  }}
  foreach it $gated {{ puts "clock gate pin kept off tree: [[$it getInst] getName]/[[$it getMTerm] getName]"; $it disconnect }}
}}
clock_tree_synthesis -root_buf CDRI01 -buf_list CDRI01 -sink_clustering_enable {{*}}$::env(CTS_ARGS)
if {{$::env(ISOLATE_CTS_GATES)}} {{
  foreach it $gated {{ $it connect $clknet }}
}}
set_propagated_clock [all_clocks]
detailed_placement
check_placement
puts "after CTS:"
report_design_area
write_db $OUT/placed.odb
"""


ROUTE_TCL_SCRIPT = """# Detailed route script generated by chipflow. PRIVATE.
set OUT $::env(OUT)
set_thread_count $::env(THREADS)
read_db $OUT/placed.odb
set_routing_layers -signal metal1-metal2 -clock metal1-metal2
if {$::env(TRY) != 0} {
  set_global_routing_layer_adjustment metal1 [expr {0.04 * $::env(TRY)}]
  set_global_routing_layer_adjustment metal2 [expr {0.04 * $::env(TRY)}]
}
global_route -congestion_iterations 30 -allow_congestion
detailed_route -droute_end_iter $::env(DRT_ITERS) -verbose 1 -output_drc $OUT/route_drc.rpt
puts "routing finished"
write_def $OUT/routed.def
write_verilog $OUT/routed.v
write_db $OUT/routed.odb
"""


def pnr_stage(
    project: ProjectConfig,
    profile: ProcessProfile,
    toolchain: Toolchain,
    run_dir: Path | str,
) -> StageSpec:
    run_path = Path(run_dir).resolve()
    openroad_tool = toolchain.tool("openroad")
    verify_file(openroad_tool.executable, openroad_tool.sha256)

    def prepare(stage_dir: Path) -> StageEnvelope:
        tech_stage = run_path / "stages/technology"
        synth_stage = run_path / "stages/synthesis"

        # Copy verified LEF, Liberty, GDS and netlist
        for item in ("tech.lef", "cells.lef", "core_typ.lib", "core_min.lib", "core_max.lib"):
            shutil.copyfile(tech_stage / item, stage_dir / item)
        shutil.copyfile(tech_stage / "work/core.gds", stage_dir / "core.gds")
        shutil.copyfile(synth_stage / "mapped.v", stage_dir / "mapped.v")

        # Load pad plan and technology metadata
        pad_plan_file = run_path / "pad-plan.json"
        if pad_plan_file.is_file():
            pad_plan = json.loads(pad_plan_file.read_text(encoding="utf-8"))
        else:
            snap_pads = run_path / "inputs/project" / project.pads.relative_to(project.directory)
            pad_config = load_pad_map(snap_pads if snap_pads.is_file() else project.pads)
            pad_plan = build_pad_plan(pad_config, profile)
            atomic_write_json(pad_plan_file, pad_plan)
        tech_meta = json.loads((tech_stage / "technology.json").read_text(encoding="utf-8"))
        pad_terminals = tech_meta.get("pad_terminals_um", {})

        # 1. Build floorplan
        fp_dict, fp_tcl = build_floorplan(pad_plan, profile, pad_terminals, stage_dir / "cells.lef")
        (stage_dir / "floorplan.json").write_text(json.dumps(fp_dict, indent=2), encoding="utf-8")
        (stage_dir / "floorplan.tcl").write_text(fp_tcl, encoding="utf-8")

        # 2. Write OpenROAD scripts
        top_core = f"{project.top}_core"
        (stage_dir / "pnr.tcl").write_text(_pnr_tcl_script(top_core, project.clock.port), encoding="utf-8")
        (stage_dir / "route.tcl").write_text(ROUTE_TCL_SCRIPT, encoding="utf-8")

        # 3. Write PNR runner config and script
        pnr_config = {
            "openroad_executable": str(openroad_tool.executable),
            "threads": project.resources.threads,
            "route_attempts": project.physical.route_attempts,
            "detailed_route_iterations": project.physical.detailed_route_iterations,
            "top_core": top_core,
            "density": project.physical.density,
            "slew_margin": project.physical.slew_margin,
            "cap_margin": project.physical.cap_margin,
            "clock_buffer_distance_um": project.physical.clock_buffer_distance_um,
            "buffer_inputs": project.physical.buffer_inputs,
            "cts_clock_gate_isolation": project.hooks.cts_clock_gate_isolation,
            "clock_port": project.clock.port,
            "clock_period": project.clock.period_ns,
            "input_transition": project.clock.transition_ns,
        }
        (stage_dir / "pnr_config.json").write_text(json.dumps(pnr_config, indent=2), encoding="utf-8")
        (stage_dir / "pnr_runner.py").write_text(PNR_RUNNER_SCRIPT, encoding="utf-8")

        config_hash = sha256_file(run_path / "resolved-project.json")
        envelope_inputs = [
            {"id": name, "path": name, "sha256": sha256_file(stage_dir / name), "bytes": (stage_dir / name).stat().st_size}
            for name in ("mapped.v", "tech.lef", "cells.lef", "core.gds", "floorplan.json", "floorplan.tcl")
        ]
        return StageEnvelope(
            stage_id="pnr",
            adapter={"id": "chipflow.pnr", "version": "0.1.0"},
            inputs=tuple(envelope_inputs),
            tools=(
                {
                    "id": "openroad",
                    "executable_sha256": openroad_tool.sha256,
                    "version_evidence": "doctor/doctor.json",
                },
            ),
            configuration_sha256=config_hash,
            seed=project.resources.seed,
            threads=project.resources.threads,
            outputs=tuple(
                {"id": name, "path": name, "validator": "pnr-v1"}
                for name in ("floorplan.json", "floorplan.tcl", "placed.odb", "routed.def", "routed.v", "routed.odb", "route_summary.json")
            ),
        )

    def assess(stage_dir: Path, result: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        summary_path = stage_dir / "route_summary.json"
        if not result.succeeded and not summary_path.is_file():
            return Assessment(failure_kind="infrastructure")

        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}

        evidence = tuple(
            Artifact(str((stage_dir / name).relative_to(run_path)), sha256_file(stage_dir / name))
            for name in ("floorplan.json", "placed.odb", "routed.def", "routed.v", "routed.odb", "route_summary.json")
            if (stage_dir / name).is_file()
        )
        input_hashes = {
            "mapped_verilog": sha256_file(stage_dir / "mapped.v"),
            "tech_lef": sha256_file(stage_dir / "tech.lef"),
            "cells_lef": sha256_file(stage_dir / "cells.lef"),
            "floorplan_json": sha256_file(stage_dir / "floorplan.json"),
        }
        succeeded = summary.get("succeeded", False)
        violations = summary.get("router_violations", -1)
        overlaps = summary.get("unexpected_overlaps", -1)
        attempts = summary.get("attempts_run", 0)

        routing_status = CheckStatus.PASS if (succeeded and violations == 0) else CheckStatus.FAIL
        overlap_status = CheckStatus.PASS if (succeeded and overlaps == 0) else CheckStatus.FAIL
        evidence_artifacts = tuple(artifacts) if artifacts else evidence

        checks = (
            CheckResult(
                "routing",
                routing_status,
                message=f"{violations} router violations after {attempts} attempt(s)",
                evidence=evidence_artifacts,
                input_hashes=input_hashes,
            ),
            CheckResult(
                "routing_overlap",
                overlap_status,
                message=f"{overlaps} unexpected overlaps across routed nets",
                evidence=evidence_artifacts,
                input_hashes=input_hashes,
            ),
        )
        if not succeeded:
            return Assessment(checks=checks, failure_kind="design", extra_artifacts=evidence_artifacts)
        return Assessment(checks=checks, extra_artifacts=evidence_artifacts)

    return StageSpec(
        id="pnr",
        command=(sys.executable, "-I", "pnr_runner.py"),
        dependencies=("technology", "synthesis"),
        timeout_s=float(project.physical.pnr_timeout_seconds),
        required_outputs=("floorplan.json", "floorplan.tcl", "placed.odb", "routed.def", "routed.v", "routed.odb", "route_summary.json"),
        validators={name: _validate_nonempty for name in ("floorplan.json", "floorplan.tcl", "placed.odb", "routed.def", "routed.v", "routed.odb", "route_summary.json")},
        prepare=prepare,
        assess=assess,
    )
