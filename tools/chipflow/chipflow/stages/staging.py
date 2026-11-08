"""Shared, hash-checked staging of declared project sources for tool stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import re
import shutil
from typing import Any, Mapping

from chipflow.config.project import ConfigError, ProjectConfig
from chipflow.runner.execution import sha256_file
from chipflow.runner.snapshot import load_snapshot_manifest, verified_snapshot_file


def validate_design_inputs(project: ProjectConfig) -> None:
    """Reject parameters and defines that cannot be passed safely to Yosys."""
    for key, value in project.parameters.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"parameter {key!r} must be a nonnegative integer, got {value!r}")
    for define in project.defines:
        if "=" in define:
            name, value = define.split("=", 1)
        else:
            name, value = define, ""
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ConfigError(f"define name has unsupported characters: {name!r}")
        if value and not re.fullmatch(r"[A-Za-z0-9_]+", value):
            raise ConfigError(f"define value has unsupported characters: {value!r}")


def _safe_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    if not relative or ".." in Path(relative).parts:
        raise ConfigError(f"unsafe staged path: {relative!r}")
    return relative


@dataclass(frozen=True)
class StagedSources:
    sources: tuple[str, ...]
    include_flags: tuple[str, ...]
    inputs: tuple[dict[str, Any], ...]
    path_map: tuple[dict[str, str], ...]
    snapshot: Mapping[Path, dict[str, str]]


def stage_project_sources(
    project: ProjectConfig,
    run_path: Path,
    stage_dir: Path,
    snapshot_by_orig: Mapping[Path, dict[str, str]],
) -> StagedSources:
    """Copy source and include files from the verified run snapshot.

    Only files listed in the run's input manifest are considered.  Every copy is
    rehashed against the manifest row before it is exposed to a tool.
    """
    staged_sources: list[str] = []
    inputs: list[dict[str, Any]] = []
    path_map: list[dict[str, str]] = []

    for index, source in enumerate(project.sources):
        row = snapshot_by_orig.get(source)
        if row is None:
            raise ConfigError(f"source is not in the run snapshot: {source}")
        snapshot_file = verified_snapshot_file(run_path, row)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", source.name)
        staged_rel = f"work/src/{index:04d}_{safe_name}"
        destination = stage_dir / staged_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot_file, destination)
        staged_hash = sha256_file(destination)
        if staged_hash != row["sha256"]:
            raise RuntimeError(f"staged copy hash mismatch for {source}")
        staged_sources.append(staged_rel)
        path_map.append({
            "original": str(source),
            "snapshot": row["snapshot"],
            "staged": staged_rel,
            "sha256": staged_hash,
        })
        inputs.append({
            "id": f"src_{index}",
            "path": staged_rel,
            "sha256": staged_hash,
            "bytes": destination.stat().st_size,
        })

    include_flags: list[str] = []
    include_files: dict[Path, list[Path]] = {}
    for include_dir in project.include_dirs:
        include_files[include_dir] = sorted(
            path for path in snapshot_by_orig if path.is_relative_to(include_dir)
        )
        for path in include_files[include_dir]:
            relative = _safe_relative(path, include_dir)
            if not re.fullmatch(r"[A-Za-z0-9_./+-]+", relative):
                raise ConfigError(f"include path has unsupported characters: {relative!r} in {path}")

    for index, include_dir in enumerate(project.include_dirs):
        staged_dir = f"work/inc/{index:04d}"
        include_flags.append(staged_dir)
        (stage_dir / staged_dir).mkdir(parents=True, exist_ok=True)
        for path in include_files[include_dir]:
            row = snapshot_by_orig[path]
            snapshot_file = verified_snapshot_file(run_path, row)
            relative = path.relative_to(include_dir)
            destination = stage_dir / staged_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot_file, destination)
            staged_hash = sha256_file(destination)
            if staged_hash != row["sha256"]:
                raise RuntimeError(f"staged include copy hash mismatch for {path}")
            staged_rel = f"{staged_dir}/{relative.as_posix()}"
            path_map.append({
                "original": str(path),
                "snapshot": row["snapshot"],
                "staged": staged_rel,
                "sha256": staged_hash,
            })
            inputs.append({
                "id": f"inc_{index}_{relative.as_posix()}",
                "path": staged_rel,
                "sha256": staged_hash,
                "bytes": destination.stat().st_size,
            })

    return StagedSources(
        sources=tuple(staged_sources),
        include_flags=tuple(include_flags),
        inputs=tuple(inputs),
        path_map=tuple(path_map),
        snapshot=snapshot_by_orig,
    )


def copy_verified_dependency(
    run_path: Path,
    stage_dir: Path,
    dependency: str,
    name: str,
    destination: str,
) -> dict[str, Any]:
    """Copy one declared dependency artifact and return its input row."""
    source = run_path / "stages" / dependency / name
    if not source.is_file():
        raise RuntimeError(f"missing dependency artifact: {dependency}/{name}")
    expected = sha256_file(source)
    target = stage_dir / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha256_file(target) != expected:
        raise RuntimeError(f"dependency copy hash mismatch: {dependency}/{name}")
    return {
        "id": f"{dependency}.{name}",
        "path": destination,
        "sha256": expected,
        "bytes": target.stat().st_size,
    }


def assert_snapshot_unchanged(
    project: ProjectConfig,
    run_path: Path,
    snapshot_by_orig: Mapping[Path, dict[str, str]],
) -> None:
    if load_snapshot_manifest(run_path, project) != dict(snapshot_by_orig):
        raise RuntimeError("input manifest changed during stage preparation")
