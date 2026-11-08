#!/usr/bin/env python3
"""Milestone 3 S8 physical flow smoke run: elaboration -> technology -> synthesis -> pnr -> assembly."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

# Add chipflow to path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "tools/chipflow"))

from chipflow.cli import snapshot_project
from chipflow.config import load_project
from chipflow.runner.execution import atomic_write_json, execute_stages
from chipflow.stages.assembly import assembly_stage
from chipflow.stages.elaboration import elaboration_stage
from chipflow.stages.pnr import pnr_stage
from chipflow.stages.synthesis import synthesis_stage
from chipflow.stages.technology import technology_stage
from chipflow.technologies.profile import load_profile
from chipflow.toolchain import doctor, load_toolchain


def main() -> int:
    project_file = repo_root / "projects/s8/chip.toml"
    toolchain_file = repo_root / "toolchains/local.wsl.toml"

    project = load_project(project_file)
    toolchain = load_toolchain(toolchain_file)
    profile = load_profile()

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work_dir = repo_root / "work" / f"m3-s8-smoke-{run_stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)
    run_dir = work_dir / "run"
    run_dir.mkdir()

    print(f"Starting Milestone 3 physical run in: {run_dir}")
    snapshot_project(project, run_dir)
    shutil.copyfile(toolchain.path, run_dir / "toolchain.toml")
    atomic_write_json(run_dir / "resolved-toolchain.json", toolchain.to_dict())
    from chipflow.config.pads import build_pad_plan, load_pad_map
    atomic_write_json(run_dir / "pad-plan.json", build_pad_plan(load_pad_map(project.pads), profile))

    doctor_report = doctor(toolchain, run_dir / "doctor")
    print(f"Doctor exit code: {doctor_report['exit_code']}")
    if doctor_report["exit_code"] != 0:
        print("Doctor failed!")
        return 2

    specs = [
        elaboration_stage(project, toolchain, run_dir),
        technology_stage(project, profile, toolchain, run_dir),
        synthesis_stage(project, profile, toolchain, run_dir),
        pnr_stage(project, profile, toolchain, run_dir),
        assembly_stage(project, profile, toolchain, run_dir),
    ]

    print("Executing stages: elaboration, technology, synthesis, pnr, assembly...")
    summary = execute_stages(run_dir, specs)

    for stage in summary.stages:
        print(f"\n--- STAGE {stage.id}: {stage.status.value} (failure_kind={stage.failure_kind}) ---")
        if stage.error:
            print(f"  Error: {stage.error}")
        for check in stage.checks:
            print(f"  [{check.status.value}] {check.id}: {check.message}")

    print(f"\nSummary: cancelled={summary.cancelled}")
    all_succeeded = all(s.status.value == "succeeded" for s in summary.stages)
    print(f"All stages succeeded: {all_succeeded}")

    # Inspect produced GDS and CDL
    gds_path = run_dir / f"stages/assembly/{project.top_cell}.gds"
    cdl_path = run_dir / f"stages/assembly/{project.top_cell}.cdl"
    if gds_path.is_file():
        print(f"GDS produced: {gds_path} ({gds_path.stat().st_size} bytes)")
    else:
        print("GDS MISSING!")
    if cdl_path.is_file():
        print(f"CDL produced: {cdl_path} ({cdl_path.stat().st_size} bytes)")
    else:
        print("CDL MISSING!")

    return 0 if all_succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
