"""Shared helpers for verification adapters.

A verification adapter distinguishes three outcomes.  A complete, well-formed
report that shows a defect is a design FAIL.  A missing, truncated, empty or
otherwise unusable report is an infrastructure failure, never a clean result
and never a detected defect.  Only complete evidence with no defect is PASS.
"""

from __future__ import annotations

from pathlib import Path

from chipflow.runner.execution import Assessment, sha256_file
from chipflow.runner.results import Artifact, CheckResult, CheckStatus
from chipflow.toolchain import Tool, verify_file


class EvidenceError(ValueError):
    """The evidence cannot support either verdict."""


def artifact(run_path: Path, path: Path) -> Artifact:
    return Artifact(path.resolve().relative_to(run_path.resolve()).as_posix(), sha256_file(path))


def unusable(check_ids: tuple[str, ...], reason: object) -> Assessment:
    """Infrastructure outcome that keeps the reason on each affected check row."""
    return Assessment(
        checks=tuple(CheckResult(check_id, CheckStatus.NOT_RUN, message=str(reason)) for check_id in check_ids),
        failure_kind="infrastructure",
    )


def verified_tool(tool: Tool) -> dict[str, str]:
    """Check a configured executable's fingerprint and return its envelope row."""
    verify_file(tool.executable, tool.sha256)
    return {"id": tool.name, "executable_sha256": tool.sha256, "version_evidence": "doctor/doctor.json"}


def nonempty(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"required output {path.name} is missing or empty")
