"""Read the declared run snapshot without consulting live project files."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

from .execution import sha256_file

if TYPE_CHECKING:
    from chipflow.config.project import ProjectConfig


def load_snapshot_manifest(run_dir: Path, project: ProjectConfig) -> dict[Path, dict[str, str]]:
    rows = json.loads((run_dir / "input-manifest.json").read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("input manifest must be an array")
    manifest: dict[Path, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ("original", "snapshot", "sha256")):
            raise RuntimeError("invalid input manifest row")
        original = Path(row["original"])
        if original not in project.dependencies or original in manifest:
            raise RuntimeError(f"unexpected or duplicate input manifest path: {original}")
        expected_path = Path("inputs/project") / original.relative_to(project.directory)
        if Path(row["snapshot"]) != expected_path or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
            raise RuntimeError(f"invalid snapshot path or hash for {original}")
        manifest[original] = row
    if set(manifest) != set(project.dependencies):
        raise RuntimeError("input manifest does not cover all declared dependencies")
    return manifest


def verified_snapshot_file(run_dir: Path, row: dict[str, str]) -> Path:
    path = run_dir / row["snapshot"]
    if not path.resolve().is_relative_to((run_dir / "inputs/project").resolve()):
        raise RuntimeError(f"snapshot path escapes run inputs: {path}")
    if sha256_file(path) != row["sha256"]:
        raise RuntimeError(f"snapshot hash mismatch: {path}")
    return path
