"""LVS adapter: compare the assembled layout with its generated schematic.

A comparison database is clean only when it is nonempty, contains the expected
top circuit on both sides and every circuit, net, pin, device and subcircuit
pair matches.  An empty or unreadable database supports no verdict.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import shutil
from typing import Any

from chipflow.config.project import ProjectConfig
from chipflow.runner.execution import (
    Artifact, Assessment, CommandResult, StageSpec, atomic_write_json, sha256_file,
)
from chipflow.runner.results import CheckResult, CheckStatus
from chipflow.stages.envelope import StageEnvelope
from chipflow.stages.evidence import EvidenceError, artifact, nonempty, unusable, verified_tool
from chipflow.stages.staging import copy_verified_dependency
from chipflow.technologies.profile import ProcessProfile
from chipflow.toolchain import Toolchain, verify_file

ADAPTER = {"id": "chipflow.lvs", "version": "0.1.0"}
PAIR_KINDS = ("nets", "pins", "devices", "subcircuits")


def read_lvs_database(path: Path) -> dict[str, Any]:
    """Reduce a KLayout LVS database to plain circuit-pair records.

    KLayout's Python module is loaded only here, so configuration and
    nonphysical commands keep working without the physical runtime.
    """
    import klayout.db as db

    comparison = db.LayoutVsSchematic()
    try:
        comparison.read(str(path))
    except RuntimeError as error:
        raise EvidenceError(f"LVS database is not readable: {error}") from error
    xref = comparison.xref()
    if xref is None:
        raise EvidenceError("LVS database has no comparison section")
    methods = {"nets": xref.each_net_pair, "pins": xref.each_pin_pair,
               "devices": xref.each_device_pair, "subcircuits": xref.each_subcircuit_pair}
    circuits = []
    for pair in xref.each_circuit_pair():
        circuits.append({
            "layout": pair.first().name if pair.first() is not None else None,
            "schematic": pair.second().name if pair.second() is not None else None,
            "status": str(pair.status()),
            **{kind: dict(Counter(str(item.status()) for item in method(pair))) for kind, method in methods.items()},
        })
    return {"circuits": circuits}


def lvs_verdict(summary: dict[str, Any], top_cell: str) -> dict[str, Any]:
    """Judge plain circuit-pair records; raise when they support no verdict."""
    circuits = summary.get("circuits")
    if not isinstance(circuits, list) or not circuits:
        raise EvidenceError("LVS comparison is empty")
    tops = [c for c in circuits if top_cell in (c.get("layout"), c.get("schematic"))]
    if not tops:
        raise EvidenceError(f"LVS comparison lacks the top circuit {top_cell}")
    if len(tops) != 1:
        raise EvidenceError(f"LVS comparison has {len(tops)} records for the top circuit {top_cell}")
    problems: list[str] = []
    totals: Counter[str] = Counter()
    for circuit in circuits:
        label = circuit.get("layout") or circuit.get("schematic") or "?"
        if circuit.get("layout") is None or circuit.get("schematic") is None:
            problems.append(f"{label}: present on one side only")
        if circuit.get("status") != "Match":
            problems.append(f"{label}: circuit {circuit.get('status')}")
        for kind in PAIR_KINDS:
            counts = circuit.get(kind)
            if not isinstance(counts, dict) or any(type(v) is not int or v < 0 for v in counts.values()):
                raise EvidenceError(f"LVS comparison record for {label} lacks valid {kind} counts")
            totals[kind] += counts.get("Match", 0)
            unmatched = {status: count for status, count in counts.items() if status != "Match" and count}
            if unmatched:
                detail = ", ".join(f"{count} {status}" for status, count in sorted(unmatched.items()))
                problems.append(f"{label}: {kind} {detail}")
    top = tops[0]
    if not problems and not all(top[kind].get("Match", 0) > 0 for kind in ("nets", "pins", "subcircuits")):
        raise EvidenceError("LVS top circuit comparison has no matched nets, pins or subcircuits")
    return {"top_cell": top_cell, "circuits": len(circuits), "matched": dict(totals),
            "top_matched": {kind: top[kind].get("Match", 0) for kind in PAIR_KINDS},
            "problems": problems, "match": not problems}


def lvs_stage(project: ProjectConfig, profile: ProcessProfile, toolchain: Toolchain,
              run_dir: Path | str) -> StageSpec:
    run_path = Path(run_dir).resolve()
    top_cell = project.top_cell
    settings = profile.physical_verification
    if settings is None:
        raise ValueError("process profile does not describe physical verification")
    klayout = toolchain.tool("klayout")

    def prepare(stage_dir: Path) -> StageEnvelope:
        tool_row = verified_tool(klayout)
        layout = copy_verified_dependency(run_path, stage_dir, "assembly", f"{top_cell}.gds", "layout.gds")
        schematic = copy_verified_dependency(run_path, stage_dir, "assembly", f"{top_cell}.cdl", "schematic.cdl")
        deck = toolchain.pdk_file("lvs_deck")
        verify_file(deck.path, deck.sha256)
        shutil.copyfile(deck.path, stage_dir / "deck.lvs")
        verify_file(stage_dir / "deck.lvs", deck.sha256)
        return StageEnvelope(
            stage_id="lvs", adapter=ADAPTER,
            inputs=(layout, schematic, {"id": "lvs_deck", "path": "deck.lvs", "sha256": deck.sha256,
                                        "bytes": (stage_dir / "deck.lvs").stat().st_size}),
            tools=(tool_row,),
            configuration_sha256=sha256_file(run_path / "resolved-project.json"),
            seed=project.resources.seed, threads=project.resources.threads,
            outputs=({"id": "lvs_database", "path": "report.lvsdb", "validator": "klayout-lvsdb-v1"},
                     {"id": "extracted_netlist", "path": "extracted.cir", "validator": "nonempty"}),
        )

    def assess(stage_dir: Path, result: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        if not result.succeeded or not artifacts:
            return unusable(("lvs",), f"LVS did not complete: exit {result.returncode}")
        try:
            verify_file(stage_dir / "deck.lvs", toolchain.pdk_file("lvs_deck").sha256)
            verdict = lvs_verdict(read_lvs_database(stage_dir / "report.lvsdb"), top_cell)
        except (EvidenceError, ImportError, OSError, ValueError) as error:
            return unusable(("lvs",), f"{type(error).__name__}: {error}")
        atomic_write_json(stage_dir / "lvs-summary.json", verdict)
        summary = artifact(run_path, stage_dir / "lvs-summary.json")
        matched = verdict["matched"]
        message = (f"{verdict['circuits']} circuits including {top_cell}; matched {matched.get('nets', 0)} nets, "
                   f"{matched.get('pins', 0)} pins, {matched.get('devices', 0)} devices, "
                   f"{matched.get('subcircuits', 0)} subcircuits")
        if verdict["problems"]:
            shown = "; ".join(verdict["problems"][:5])
            message = f"{len(verdict['problems'])} mismatch record(s): {shown}"
        check = CheckResult(
            "lvs", CheckStatus.PASS if verdict["match"] else CheckStatus.FAIL, message=message,
            evidence=(*artifacts, summary),
            input_hashes={"layout_gds": sha256_file(stage_dir / "layout.gds"),
                          "schematic_cdl": sha256_file(stage_dir / "schematic.cdl"),
                          "lvs_deck": toolchain.pdk_file("lvs_deck").sha256, "klayout": klayout.sha256},
        )
        return Assessment(checks=(check,), failure_kind=None if verdict["match"] else "design",
                          extra_artifacts=(summary,))

    return StageSpec(
        id="lvs",
        command=(str(klayout.executable), "-b", "-rd", "input=layout.gds", "-rd", f"top_cell={top_cell}",
                 "-rd", f"{settings.lvs_schematic_variable}=schematic.cdl",
                 "-rd", f"{settings.lvs_netlist_variable}=extracted.cir",
                 "-rd", f"{settings.lvs_report_variable}=report.lvsdb", "-r", "deck.lvs"),
        dependencies=("assembly",),
        timeout_s=float(max(project.resources.timeout_seconds, 1800.0)),
        required_outputs=("report.lvsdb", "extracted.cir"),
        validators={"report.lvsdb": nonempty, "extracted.cir": nonempty},
        prepare=prepare,
        assess=assess,
    )
