"""Bounded Yosys elaboration stage adapter."""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
import posixpath
import re
import shutil
from typing import Any, Mapping

from chipflow.config.project import ConfigError, ProjectConfig
from chipflow.runner.execution import (
    Artifact,
    Assessment,
    CommandResult,
    StageSpec,
    atomic_write_json,
    sha256_file,
)
from chipflow.runner.results import CheckResult, CheckStatus
from chipflow.runner.snapshot import load_snapshot_manifest, verified_snapshot_file
from chipflow.stages.design_model import (
    check_design,
    load_design,
    parse_make_deps,
)
from chipflow.stages.envelope import StageEnvelope
from chipflow.toolchain import Toolchain, verify_file


def _validate_safe_path(p: str) -> bool:
    path = Path(p)
    windows = PureWindowsPath(p)
    if path.is_absolute() or path.drive or windows.drive or ".." in path.parts or ".." in windows.parts:
        return False
    return True


def elaboration_stage(
    project: ProjectConfig,
    toolchain: Toolchain,
    run_dir: Path | str,
) -> StageSpec:
    run_path = Path(run_dir).resolve()

    # Pre-execution validation
    # 1. Parameter validation: must be nonnegative integers
    for pk, pv in project.parameters.items():
        if isinstance(pv, bool) or not isinstance(pv, int) or pv < 0:
            raise ConfigError(f"parameter {pk!r} must be a nonnegative integer, got {pv!r}")

    # 2. Defines validation: names and values must be [A-Za-z0-9_]
    for d in project.defines:
        if "=" in d:
            dk, dv = d.split("=", 1)
        else:
            dk, dv = d, ""
        if not re.fullmatch(r"[A-Za-z0-9_]+", dk):
            raise ConfigError(f"define name has unsupported characters: {dk!r}")
        if dv and not re.fullmatch(r"[A-Za-z0-9_]+", dv):
            raise ConfigError(f"define value has unsupported characters: {dv!r}")

    # 3. Include-tree names come only from the run's declared snapshot.
    snapshot_by_orig = load_snapshot_manifest(run_path, project)
    include_files = {}
    for inc_dir in project.include_dirs:
        include_files[inc_dir] = sorted(f for f in snapshot_by_orig if f.is_relative_to(inc_dir))
        for f in include_files[inc_dir]:
            rel = f.relative_to(inc_dir).as_posix()
            if not re.fullmatch(r"[A-Za-z0-9_./+-]+", rel):
                raise ConfigError(f"include path has unsupported characters: {rel!r} in {f}")

    try:
        yosys_tool = toolchain.tool("yosys")
    except StopIteration:
        raise ConfigError("toolchain missing required 'yosys' tool")

    def prepare(stage_dir: Path) -> StageEnvelope:
        # Re-verify yosys executable
        verify_file(yosys_tool.executable, yosys_tool.sha256)

        if load_snapshot_manifest(run_path, project) != snapshot_by_orig:
            raise RuntimeError("input manifest changed before elaboration")
        conf_hash = sha256_file(run_path / "resolved-project.json")
        doc_json = run_path / "doctor/doctor.json"
        doctor_data = json.loads(doc_json.read_text(encoding="utf-8"))
        if not isinstance(doctor_data, dict) or doctor_data.get("exit_code") != 0:
            raise RuntimeError("elaboration requires successful doctor version evidence")

        path_map_rows: list[dict[str, str]] = []
        staged_inputs: list[dict[str, Any]] = []

        # 1. Stage sources
        staged_sources: list[str] = []
        for idx, src_path in enumerate(project.sources):
            snap_row = snapshot_by_orig[src_path]
            snap_file = verified_snapshot_file(run_path, snap_row)
            expected_hash = snap_row["sha256"]

            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", src_path.name)
            staged_rel = f"work/src/{idx:04d}_{safe_name}"
            dest = stage_dir / staged_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snap_file, dest)
            staged_hash = sha256_file(dest)
            if staged_hash != expected_hash:
                raise RuntimeError(f"staged copy hash mismatch for {src_path}: expected {expected_hash}, got {staged_hash}")

            staged_sources.append(staged_rel)
            path_map_rows.append({
                "original": str(src_path),
                "snapshot": snap_row["snapshot"],
                "staged": staged_rel,
                "sha256": staged_hash,
            })
            staged_inputs.append({
                "id": f"src_{idx}",
                "path": str(dest.relative_to(stage_dir)),
                "sha256": staged_hash,
                "bytes": dest.stat().st_size,
            })

        # 2. Stage include directories
        staged_inc_flags: list[str] = []
        for inc_idx, inc_dir in enumerate(project.include_dirs):
            staged_inc_dir_rel = f"work/inc/{inc_idx:04d}"
            staged_inc_flags.append(f"-I {staged_inc_dir_rel}")
            (stage_dir / staged_inc_dir_rel).mkdir(parents=True, exist_ok=True)
            for f in include_files[inc_dir]:
                snap_row = snapshot_by_orig[f]
                snap_file = verified_snapshot_file(run_path, snap_row)
                expected_hash = snap_row["sha256"]

                rel = f.relative_to(inc_dir)
                dest = stage_dir / staged_inc_dir_rel / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(snap_file, dest)
                staged_hash = sha256_file(dest)
                if staged_hash != expected_hash:
                    raise RuntimeError(f"staged copy hash mismatch for {f}")

                staged_rel = f"{staged_inc_dir_rel}/{rel.as_posix()}"
                path_map_rows.append({
                    "original": str(f),
                    "snapshot": snap_row["snapshot"],
                    "staged": staged_rel,
                    "sha256": staged_hash,
                })
                staged_inputs.append({
                    "id": f"inc_{inc_idx}_{rel.as_posix()}",
                    "path": str(dest.relative_to(stage_dir)),
                    "sha256": staged_hash,
                    "bytes": dest.stat().st_size,
                })

        # Write path-map.json
        atomic_write_json(stage_dir / "path-map.json", path_map_rows)

        # 3. Write elab.ys
        # Build commands according to Decision D8
        read_parts = ["read_verilog", "-sv"]
        read_parts.extend(staged_inc_flags)
        for d in sorted(project.defines):
            read_parts.append(f"-D{d}")
        read_parts.extend(staged_sources)
        read_line = " ".join(read_parts)

        hier_parts = ["hierarchy", "-check", "-top", project.top]
        for pk, pv in sorted(project.parameters.items()):
            hier_parts.extend(["-chparam", pk, str(pv)])
        hier_line = " ".join(hier_parts)

        script_content = "\n".join([
            read_line,
            hier_line,
            "proc",
            "flatten",
            "memory",
            "opt_clean",
            "check -assert",
            "write_json elab.json",
            "",
        ])
        (stage_dir / "elab.ys").write_text(script_content, encoding="utf-8")

        envelope = StageEnvelope(
            stage_id="elaboration",
            adapter={"id": "yosys.elaboration", "version": "0.1.0"},
            inputs=tuple(staged_inputs),
            tools=({
                "id": "yosys",
                "executable_sha256": yosys_tool.sha256,
                "version_evidence": "doctor/doctor.json",
            },),
            configuration_sha256=conf_hash,
            seed=project.resources.seed,
            threads=project.resources.threads,
            outputs=(
                {"id": "elab.json", "path": "elab.json", "validator": "yosys-json-v1"},
                {"id": "deps.txt", "path": "deps.txt", "validator": "make-deps-v1"},
                {"id": "elab.log", "path": "elab.log", "validator": "log-v1"},
            ),
        )
        return envelope

    def validate_elab_json(path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("elab.json is not a valid JSON object")
        modules = data.get("modules", {})
        if project.top not in modules:
            raise ValueError(f"top module {project.top!r} missing from elab.json modules")
        top_mod = modules[project.top]
        for req in ("ports", "cells", "netnames"):
            if req not in top_mod:
                raise ValueError(f"elab.json top module missing {req!r}")

    def validate_deps_txt(path: Path) -> None:
        target, prereqs = parse_make_deps(path.read_text(encoding="utf-8"))
        if target != "elab.json":
            raise ValueError(f"deps.txt target must be 'elab.json', got {target!r}")

    def validate_elab_log(path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("elab.log is missing or empty")

    def assess(stage_dir: Path, cmd: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        conf_file = run_path / "resolved-project.json"
        conf_hash = sha256_file(conf_file)

        # Read path-map.json for reverse lookup. Keys are exact staged paths only:
        # a basename key would let an undeclared file with the same name pass.
        path_map_file = stage_dir / "path-map.json"
        path_map_by_staged: dict[str, str] = {}
        if path_map_file.is_file():
            rows = json.loads(path_map_file.read_text(encoding="utf-8"))
            for r in rows:
                path_map_by_staged[r["staged"]] = r["original"]

        log_file = stage_dir / "elab.log"
        if not cmd.succeeded:
            if log_file.is_file():
                log_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                error_line = None
                for line in log_lines:
                    if "ERROR:" in line:
                        error_line = line.strip()
                        break
                if error_line:
                    mapped_msg = error_line
                    for staged in sorted(path_map_by_staged, key=len, reverse=True):
                        mapped_msg = mapped_msg.replace(staged, path_map_by_staged[staged])
                    log_artifact = Artifact(str(log_file.relative_to(run_path)), sha256_file(log_file))
                    check = CheckResult(
                        "elaboration",
                        CheckStatus.FAIL,
                        message=mapped_msg,
                        evidence=(log_artifact,),
                        input_hashes={"configuration": conf_hash},
                    )
                    return Assessment(checks=(check,), failure_kind="configuration", extra_artifacts=(log_artifact,))
            return Assessment(failure_kind="infrastructure")

        # Command succeeded: parse deps.txt and elab.json
        deps_file = stage_dir / "deps.txt"
        target, prereqs = parse_make_deps(deps_file.read_text(encoding="utf-8"))

        dep_checks: list[str] = []
        undeclared: list[str] = []
        for p in prereqs:
            norm_p = posixpath.normpath(p.replace("\\", "/"))
            if norm_p == "elab.ys":
                continue
            if norm_p not in path_map_by_staged:
                undeclared.append(norm_p)
            else:
                dep_checks.append(norm_p)

        # Check all declared sources were consumed
        missing_sources = []
        staged_prereq_set = set(dep_checks)
        for r_staged, r_orig in path_map_by_staged.items():
            if r_staged.startswith("work/src/") and r_staged not in staged_prereq_set:
                missing_sources.append(r_orig)

        if undeclared:
            dep_row_status = CheckStatus.FAIL
            dep_row_msg = f"undeclared dependency: {', '.join(undeclared)}"
        elif missing_sources:
            dep_row_status = CheckStatus.FAIL
            dep_row_msg = f"declared source not in dependencies: {', '.join(missing_sources)}"
        else:
            dep_row_status = CheckStatus.PASS
            dep_row_msg = "all dependencies declared and verified"

        # Load design model
        elab_json_file = stage_dir / "elab.json"
        design_data = json.loads(elab_json_file.read_text(encoding="utf-8"))
        design = load_design(design_data, project.top)

        derived = getattr(project.clock, "derived_outputs", ())
        design_rows = check_design(design, project.clock, project.reset, derived, path_map_by_staged)

        from chipflow.stages.design_model import DesignCheckRow
        all_rows = list(design_rows) + [DesignCheckRow("elab.dependencies", dep_row_status, dep_row_msg)]

        discovered_deps = [path_map_by_staged[p] for p in dep_checks]

        # Write design.json
        design_summary = {
            "schema_version": 1,
            "top": project.top,
            "rows": [{"id": r.id, "status": r.status.value, "message": r.message} for r in all_rows],
            "discovered_dependencies": sorted(set(discovered_deps)),
            "ports": {
                p.name: {
                    "direction": p.direction,
                    "width": p.width,
                    "canonical_bits": list(p.canonical_bits),
                }
                for p in design.ports.values()
            },
        }
        design_file = stage_dir / "design.json"
        atomic_write_json(design_file, design_summary)
        design_artifact = Artifact(str(design_file.relative_to(run_path)), sha256_file(design_file))

        has_fail = any(r.status == CheckStatus.FAIL for r in all_rows)
        if has_fail:
            failing = [r for r in all_rows if r.status == CheckStatus.FAIL]
            check = CheckResult(
                "elaboration",
                CheckStatus.FAIL,
                message=failing[0].message,
                evidence=(design_artifact,),
                input_hashes={"configuration": conf_hash},
            )
            return Assessment(checks=(check,), failure_kind="configuration", extra_artifacts=(design_artifact,))
        else:
            check = CheckResult(
                "elaboration",
                CheckStatus.PASS,
                message="elaboration checks passed",
                evidence=(design_artifact,),
                input_hashes={"configuration": conf_hash},
            )
            return Assessment(checks=(check,), extra_artifacts=(design_artifact,))

    return StageSpec(
        id="elaboration",
        command=(
            str(yosys_tool.executable),
            "-q",
            "-E",
            "deps.txt",
            "-l",
            "elab.log",
            "-s",
            "elab.ys",
        ),
        timeout_s=float(project.resources.timeout_seconds),
        required_outputs=("elab.json", "deps.txt", "elab.log"),
        validators={
            "elab.json": validate_elab_json,
            "deps.txt": validate_deps_txt,
            "elab.log": validate_elab_log,
        },
        prepare=prepare,
        assess=assess,
    )
