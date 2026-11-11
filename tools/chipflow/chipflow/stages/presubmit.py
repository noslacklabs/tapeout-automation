"""Automatic presubmission adapter around the separate `presubmit-check` package.

The checker's exit code, tool-error list, input identity and complete row
inventory are validated together.  A zero-FAIL summary from an incomplete or
erroring run is not a clean result.  WARN and MANUAL rows stay visible as
their own checks; they are never folded into the automatic PASS.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any

from chipflow.config.project import ProjectConfig
from chipflow.runner.execution import (
    Artifact, Assessment, CommandResult, StageSpec, atomic_write_json, sha256_file,
)
from chipflow.runner.results import CheckResult, CheckStatus
from chipflow.stages.envelope import StageEnvelope
from chipflow.stages.evidence import EvidenceError, artifact, nonempty, unusable
from chipflow.stages.staging import copy_verified_dependency
from chipflow.technologies.profile import ProcessProfile
from chipflow.toolchain import Toolchain

ADAPTER = {"id": "chipflow.presubmit", "version": "0.1.0"}
ROW_STATUSES = ("PASS", "FAIL", "WARN", "MANUAL")


def _rows(groups: dict[int, int]) -> frozenset[str]:
    return frozenset(f"C{group:02d}.{index}" for group, count in groups.items() for index in range(1, count + 1))


# Row inventory of each qualified checker version.  An unlisted version is not
# interpreted: its rows could have been renumbered, removed or added.  The
# checker stops evaluating some later rows of a rule after that rule fails, so
# the complete inventory is demanded of a run without a FAIL row.
EXPECTED_ROWS = {
    "0.1.0": _rows({1: 2, 2: 4, 3: 2, 4: 5, 5: 4, 6: 1, 7: 2, 8: 3, 9: 3, 10: 4,
                    11: 2, 12: 1, 13: 1, 14: 3, 15: 1, 16: 4, 17: 2, 18: 4, 19: 4, 20: 1}),
}
# Rows a version emits only to warn that its own reference data looks unusual.
CONDITIONAL_ROWS = {"0.1.0": frozenset({"C11.3", "C11.4"})}


def checker_identity() -> dict[str, Any]:
    """Version and source fingerprint of the checker this interpreter would run."""
    spec = importlib.util.find_spec("presubmit_check")
    if spec is None or not spec.submodule_search_locations:
        raise EvidenceError("the presubmit-check package is not installed in this Python environment")
    try:
        version = importlib.metadata.version("presubmit-check")
    except importlib.metadata.PackageNotFoundError as error:
        raise EvidenceError("presubmit-check has no installed distribution metadata") from error
    if version not in EXPECTED_ROWS:
        raise EvidenceError(f"presubmit-check {version} is not a qualified checker version")
    root = Path(next(iter(spec.submodule_search_locations))).resolve()
    digest = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(sha256_file(path).encode("ascii") + b"\n")
    return {"version": version, "files": len(files), "source_sha256": digest.hexdigest()}


def submission_name(top_cell: str, project_name: str, run_path: Path) -> str:
    """Checker-conforming development file stem: <silicon>_<design>_<ddmmyyyy>_v0."""
    design = re.sub(r"[^A-Za-z0-9]", "", project_name).upper()
    if not design:
        raise EvidenceError("project name has no characters usable in a submission file name")
    stamp = re.match(r"run-(\d{4})(\d{2})(\d{2})T", run_path.name)
    year, month, day = stamp.groups() if stamp else datetime.now(timezone.utc).strftime("%Y %m %d").split()
    return f"{top_cell}_{design}_{day}{month}{year}_v0"


def presubmit_verdict(report: dict[str, Any], returncode: int | None, *, top_cell: str, die_mm: int,
                      gds_md5: str) -> dict[str, Any]:
    """Validate a checker JSON report against its process exit and input."""
    if report.get("tool") != "presubmit-check":
        raise EvidenceError("report was not written by presubmit-check")
    expected = EXPECTED_ROWS.get(report.get("version"))
    if expected is None:
        raise EvidenceError(f"report is from unqualified checker version {report.get('version')!r}")
    meta, rows, errors = report.get("meta"), report.get("results"), report.get("tool_errors")
    if not isinstance(meta, dict) or not isinstance(rows, list) or not isinstance(errors, list):
        raise EvidenceError("report lacks its meta, results or tool_errors section")
    if errors:
        raise EvidenceError(f"checker reported {len(errors)} tool error(s): {errors[0]}")
    if meta.get("exit_code") != returncode:
        raise EvidenceError(f"checker process exited {returncode} but its report records {meta.get('exit_code')}")
    if meta.get("input_unchanged") is not True:
        raise EvidenceError("checker did not confirm its input stayed unchanged")
    if meta.get("silicon") != top_cell or meta.get("die") != die_mm:
        raise EvidenceError("checker ran for a different silicon number or die size")
    if meta.get("md5") != gds_md5:
        raise EvidenceError("checker report is not for the staged layout")
    if any(not isinstance(row, dict) for row in rows):
        raise EvidenceError("report has a malformed result row")
    identifiers = [row.get("id") for row in rows]
    missing = expected - set(identifiers)
    extra = set(identifiers) - expected - CONDITIONAL_ROWS[report["version"]]
    has_fail = any(row.get("status") == "FAIL" for row in rows)
    if len(set(identifiers)) != len(identifiers) or extra or (missing and not has_fail):
        raise EvidenceError(f"result rows differ from the checker's inventory: {len(missing)} missing, "
                            f"{len(extra)} unexpected, {len(identifiers) - len(set(identifiers))} duplicated")
    by_status: dict[str, list[str]] = {status: [] for status in ROW_STATUSES}
    for row in rows:
        if row.get("status") not in by_status or not isinstance(row.get("title"), str) or not row["title"].strip():
            raise EvidenceError(f"result row {row.get('id')} has no valid status or title")
        by_status[row["status"]].append(row["id"])
    counts = {status: len(ids) for status, ids in by_status.items()}
    if {k: v for k, v in counts.items() if v} != {k: v for k, v in (report.get("summary") or {}).items() if v}:
        raise EvidenceError("report summary disagrees with its result rows")
    if returncode not in (0, 1) or (returncode == 1) != bool(by_status["FAIL"]):
        raise EvidenceError(f"checker exit {returncode} disagrees with {counts['FAIL']} FAIL row(s)")
    titles = {row["id"]: row["title"] for row in rows}
    return {"checker_version": report["version"], "rows": len(rows), "counts": counts,
            "not_reported_after_failure": sorted(missing),
            **{status.lower(): [{"id": i, "title": titles[i]} for i in by_status[status]]
               for status in ("FAIL", "WARN", "MANUAL")}}


def presubmit_stage(project: ProjectConfig, profile: ProcessProfile, toolchain: Toolchain,
                    run_dir: Path | str) -> StageSpec:
    run_path = Path(run_dir).resolve()
    top_cell = project.top_cell
    settings = profile.physical_verification
    if settings is None:
        raise ValueError("process profile does not describe physical verification")
    stem = submission_name(top_cell, project.name, run_path)
    gds_name, drc_name = stem + ".gds", stem + "_drc.txt"

    def prepare(stage_dir: Path) -> StageEnvelope:
        identity = checker_identity()
        layout = copy_verified_dependency(run_path, stage_dir, "assembly", f"{top_cell}.gds", gds_name)
        drc = copy_verified_dependency(run_path, stage_dir, "drc", "report.xml", drc_name)
        atomic_write_json(stage_dir / "presubmit-manifest.json", {
            "schema_version": 1, "checker": identity, "layout": gds_name, "layout_sha256": layout["sha256"],
            "drc_report": drc_name, "drc_report_sha256": drc["sha256"], "silicon": top_cell,
            "die_mm": settings.presubmit_die_mm, "pdk_root": str(toolchain.pdk_root),
            "development_identifiers": project.development_identifiers,
            "file_name_note": "Development file name; the silicon number, date and version are not issued identifiers.",
        })
        return StageEnvelope(
            stage_id="presubmit", adapter=ADAPTER, inputs=(layout, drc), tools=(),
            configuration_sha256=sha256_file(run_path / "resolved-project.json"),
            seed=project.resources.seed, threads=project.resources.threads,
            outputs=({"id": "presubmit_report", "path": "presubmit.json", "validator": "presubmit-json-v1"},),
        )

    def assess(stage_dir: Path, result: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        ids = ("presubmit",)
        if not artifacts:
            return unusable(ids, f"presubmission checker did not complete: exit {result.returncode}")
        try:
            manifest = json.loads((stage_dir / "presubmit-manifest.json").read_text(encoding="utf-8"))
            layout = stage_dir / gds_name
            if sha256_file(layout) != manifest["layout_sha256"]:
                raise EvidenceError("staged layout changed after preparation")
            if checker_identity() != manifest["checker"]:
                raise EvidenceError("presubmission checker changed during the stage")
            report = json.loads((stage_dir / "presubmit.json").read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise EvidenceError("checker report is not a JSON object")
            verdict = presubmit_verdict(report, result.returncode, top_cell=top_cell,
                                        die_mm=settings.presubmit_die_mm,
                                        gds_md5=hashlib.md5(layout.read_bytes()).hexdigest())
        except (EvidenceError, OSError, KeyError, ValueError) as error:
            return unusable(ids, error)
        atomic_write_json(stage_dir / "presubmit-summary.json", verdict)
        extra = (artifact(run_path, stage_dir / "presubmit-summary.json"),
                 artifact(run_path, stage_dir / "presubmit-manifest.json"))
        evidence = (*artifacts, *extra)
        hashes = {"layout_gds": manifest["layout_sha256"], "drc_report": manifest["drc_report_sha256"],
                  "presubmit_checker": manifest["checker"]["source_sha256"]}
        counts = verdict["counts"]

        def listed(rows: list[dict[str, str]]) -> str:
            return "; ".join(f"{row['id']} {row['title']}" for row in rows)

        failed = bool(verdict["fail"])
        checks = [CheckResult(
            "presubmit", CheckStatus.FAIL if failed else CheckStatus.PASS,
            message=(f"{counts['FAIL']} automatic rule(s) failed: {listed(verdict['fail'])}" if failed else
                     f"all {verdict['rows']} checker rows present; {counts['PASS']} PASS, 0 FAIL, "
                     f"{counts['WARN']} WARN and {counts['MANUAL']} MANUAL reported separately"),
            evidence=evidence, input_hashes=hashes)]
        if verdict["warn"]:
            checks.append(CheckResult("presubmit_warnings", CheckStatus.WARN,
                                      message=listed(verdict["warn"]), evidence=evidence, input_hashes=hashes))
        if verdict["manual"]:
            checks.append(CheckResult("presubmit_manual", CheckStatus.MANUAL,
                                      message=listed(verdict["manual"]), evidence=evidence, input_hashes=hashes))
        return Assessment(checks=tuple(checks), failure_kind="design" if failed else None, extra_artifacts=extra)

    return StageSpec(
        id="presubmit",
        command=(sys.executable, "-I", "-m", "presubmit_check", gds_name, "--die", str(settings.presubmit_die_mm),
                 "--silicon", top_cell, "--pdk-root", str(toolchain.pdk_root),
                 "--drc-report", drc_name, "--json", "presubmit.json"),
        dependencies=("assembly", "drc"),
        timeout_s=float(max(project.resources.timeout_seconds, 900.0)),
        required_outputs=("presubmit.json",),
        validators={"presubmit.json": nonempty},
        prepare=prepare,
        assess=assess,
        design_returncodes=(1,),
    )
