"""Real-tool defect probes for the DRC, LVS and presubmission adapters.

Each case seals one layout/schematic pair as if it were an assembly result and
runs the engine's own stage adapters with the pinned KLayout, decks and checker.
A case passes only when the adapter reaches the expected verdict for the
expected reason; a tool crash or unusable report never counts as a detected
defect.  The defective layouts are the retained S8 negative controls.

    python scripts/m4_verification_probes.py --physical-run projects/s8/builds/<run> \
        --negatives work/<s8 downstream run>/build/chip/negative \
        --toolchain toolchains/local.wsl.toml --output work/<new directory>
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/chipflow"))
from chipflow.config import load_project
from chipflow.runner.execution import atomic_write_json, execute_stages, sha256_file
from chipflow.runner.results import Artifact, StageResult, StageStatus
from chipflow.stages.drc import drc_stage
from chipflow.stages.lvs import lvs_stage
from chipflow.stages.presubmit import presubmit_stage
from chipflow.technologies.profile import load_profile
from chipflow.toolchain import load_toolchain

# name: (layout, schematic or None for the build's own, adapter, expected check status, failure kind)
CASES = {
    "control-drc": ("control.gds", None, "drc", "PASS", None),
    "control-drc-flat": ("control.gds", None, "drc_flat", "PASS", None),
    "control-lvs": ("control.gds", None, "lvs", "PASS", None),
    "m1-near-rail-in-core-cell": ("drc_core_m1.gds", None, "drc", "FAIL", "design"),
    "m1-near-rail-in-core-cell-flat": ("drc_core_m1.gds", None, "drc_flat", "FAIL", "design"),
    "m2-near-ring-in-wires-cell": ("drc_wires_m2.gds", None, "drc", "FAIL", "design"),
    "flip-flop-moved": ("drc_cell_moved.gds", None, "drc", "FAIL", "design"),
    "m2-merging-with-ring-is-legal": ("drc_top_m2.gds", None, "drc", "PASS", None),
    "routed-via-removed": ("lvs_open.gds", None, "lvs", "FAIL", "design"),
    "signal-shorted-to-vss": ("lvs_short.gds", None, "lvs", "FAIL", "design"),
    "schematic-pins-swapped": ("control.gds", "bad.cdl", "lvs", "FAIL", "design"),
    "presubmit-clean": ("control.gds", None, "presubmit", "PASS", None),
    "presubmit-drc-markers": ("drc_core_m1.gds", None, "presubmit", "FAIL", "design"),
}


def seal(run: Path, stage: str, files: dict[str, Path]) -> StageResult:
    directory = run / "stages" / stage
    directory.mkdir(parents=True)
    artifacts = []
    for name, source in files.items():
        shutil.copyfile(source, directory / name)
        artifacts.append(Artifact(f"stages/{stage}/{name}", sha256_file(directory / name)))
    return StageResult(stage, StageStatus.SUCCEEDED, artifacts=tuple(artifacts))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--physical-run", type=Path, required=True)
    parser.add_argument("--negatives", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    physical = args.physical_run.resolve(strict=True)
    negatives = args.negatives.resolve(strict=True)
    project = load_project(physical / "inputs/project/chip.toml")
    toolchain, profile = load_toolchain(args.toolchain), load_profile()
    top = project.top_cell
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    rows = []
    for name, (layout, schematic, adapter, expected_status, expected_kind) in CASES.items():
        run = output / name / f"run-{stamp}-probe"
        run.mkdir(parents=True)
        shutil.copyfile(physical / "resolved-project.json", run / "resolved-project.json")
        cdl = negatives / schematic if schematic else physical / f"stages/assembly/{top}.cdl"
        done = [seal(run, "assembly", {f"{top}.gds": negatives / layout, f"{top}.cdl": cdl})]
        if adapter == "presubmit":
            # The checker reads the marker database made for this exact layout.
            done.append(seal(run, "drc", {"report.xml": negatives / (Path(layout).stem + "_drc.txt")}))
        atomic_write_json(run / "execution.json", {"stages": [
            {"id": s.id, "status": "succeeded", "artifacts": [{"path": a.path, "sha256": a.sha256} for a in s.artifacts]}
            for s in done]})
        spec = {"drc": lambda: drc_stage(project, profile, toolchain, run),
                "drc_flat": lambda: drc_stage(project, profile, toolchain, run, flat=True),
                "lvs": lambda: lvs_stage(project, profile, toolchain, run),
                "presubmit": lambda: presubmit_stage(project, profile, toolchain, run)}[adapter]()
        stage = execute_stages(run, [spec], existing_stages=done).stages[0]
        check = next((c for c in stage.checks if c.id == adapter), None)
        observed = check.status.value if check else None
        ok = (observed == expected_status and stage.failure_kind == expected_kind
              and stage.status == (StageStatus.SUCCEEDED if expected_status == "PASS" else StageStatus.FAILED)
              and bool(check.evidence) and bool(check.input_hashes))
        rows.append({"case": name, "layout": layout, "schematic": schematic or "build", "adapter": adapter,
                     "expected": expected_status, "observed": observed, "stage_status": stage.status.value,
                     "failure_kind": stage.failure_kind, "error": stage.error,
                     "message": check.message if check else None, "ok": ok,
                     "layout_sha256": sha256_file(negatives / layout), "run": str(run.relative_to(output))})
        print(f"{'ok  ' if ok else 'BAD '} {name}: {adapter} {observed} ({stage.status.value}) "
              f"{(check.message if check else stage.error or '')[:150]}", flush=True)
    passed = all(row["ok"] for row in rows)
    atomic_write_json(output / "probes.json", {
        "schema_version": 1, "kind": "m4-physical-verification-probes", "physical_run": str(physical),
        "negatives": str(negatives), "toolchain_sha256": sha256_file(toolchain.path),
        "cases": rows, "passed": passed, "signoff_ready": False})
    print(f"{sum(r['ok'] for r in rows)}/{len(rows)} probes behaved as expected: {output / 'probes.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
