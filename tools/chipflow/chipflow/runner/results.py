"""Build evidence and exit policy. Expected limitations never waive failed checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
from typing import Any

from chipflow import RESULT_SCHEMA_VERSION


class StageStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    MANUAL = "MANUAL"
    NOT_RUN = "NOT RUN"
    NOT_APPLICABLE = "NOT APPLICABLE"


MINIMUM_REQUIRED_CHECKS = (
    "configuration", "toolchain", "elaboration", "mapped_netlist",
    "routing", "routing_overlap", "assembly", "drc", "lvs", "presubmit",
    "power_connectivity", "parasitic_artifact", "parasitic_units",
    "parasitic_coverage", "parasitic_validation", "timing_coverage",
    "timing_setup", "timing_hold", "timing_limits", "rtl_tests", "routed_tests",
)
RELEASE_REQUIRED_CHECKS = (*MINIMUM_REQUIRED_CHECKS, "sdf_min", "sdf_typ", "sdf_max", "sdf_annotation_probe")
MINIMUM_REQUIRED_ARTIFACTS = ("artifacts/chip.gds", "artifacts/chip.cdl", "artifacts/mapped.v", "artifacts/routed.v")


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("artifact/input SHA-256 must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _nonempty(self.path, "artifact.path")
        _hash(self.sha256)


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: CheckStatus
    message: str = ""
    evidence: tuple[Artifact, ...] = ()
    input_hashes: dict[str, str] = field(default_factory=dict)
    applicability_reason: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.id, "check.id")
        object.__setattr__(self, "status", CheckStatus(self.status))
        for value in self.input_hashes.values():
            _hash(value)
        if self.status == CheckStatus.NOT_APPLICABLE:
            if not self.applicability_reason or not self.evidence:
                raise ValueError("NOT APPLICABLE requires a validated precondition reason and evidence")
        if self.status in {CheckStatus.PASS, CheckStatus.FAIL} and (not self.evidence or not self.input_hashes):
            raise ValueError("PASS/FAIL requires evidence and input hashes")


@dataclass(frozen=True)
class StageResult:
    id: str
    status: StageStatus
    checks: tuple[CheckResult, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    error: str | None = None
    failure_kind: str | None = None
    failed_ancestors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.id, "stage.id")
        object.__setattr__(self, "status", StageStatus(self.status))
        if self.failure_kind not in (None, "design", "infrastructure", "configuration"):
            raise ValueError("failure_kind must be design, infrastructure, configuration or None")
        if self.status == StageStatus.FAILED and not self.failure_kind:
            raise ValueError("a failed stage requires a failure_kind")
        if self.failure_kind in ("design", "configuration") and not any(c.status == CheckStatus.FAIL for c in self.checks):
            raise ValueError(f"a {self.failure_kind} failure requires a positively detected FAIL check")
        if self.status in {StageStatus.BLOCKED, StageStatus.SKIPPED, StageStatus.CANCELLED} and any(c.status in {CheckStatus.PASS, CheckStatus.FAIL} for c in self.checks):
            raise ValueError("an unexecuted stage cannot report PASS or FAIL checks")


@dataclass(frozen=True)
class BuildResult:
    stages: tuple[StageResult, ...]
    required_checks: tuple[str, ...] = MINIMUM_REQUIRED_CHECKS
    required_artifacts: tuple[str, ...] = MINIMUM_REQUIRED_ARTIFACTS
    cancelled: bool = False
    signoff_blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Projects can extend the full-build minimum, never shrink it. Probe and
        # unit-run execution summaries are deliberately a different result type.
        object.__setattr__(self, "required_checks", tuple(dict.fromkeys((*MINIMUM_REQUIRED_CHECKS, *self.required_checks))))
        object.__setattr__(self, "required_artifacts", tuple(dict.fromkeys((*MINIMUM_REQUIRED_ARTIFACTS, *self.required_artifacts))))

    @property
    def exit_code(self) -> int:
        if self.cancelled or any(s.status == StageStatus.CANCELLED for s in self.stages):
            return 130
        if not self.stages or len({s.id for s in self.stages}) != len(self.stages):
            return 2
        if any(s.failure_kind in ("infrastructure", "configuration") for s in self.stages):
            return 2
        checks = [c for stage in self.stages for c in stage.checks]
        if len({c.id for c in checks}) != len(checks):
            return 2
        by_id = {c.id: c for c in checks}
        if any(check_id not in by_id for check_id in self.required_checks):
            return 2
        artifacts = {a.path: a.sha256 for s in self.stages for a in s.artifacts}
        if any(path not in artifacts for path in self.required_artifacts):
            return 2
        # A recorded, attributable downstream block is an expected consequence of
        # a positively detected design failure, not a malformed tool report.
        failed = any(c.status == CheckStatus.FAIL for c in checks)
        if any(bool(s.failed_ancestors) for s in self.stages) and not failed:
            return 2
        for stage in self.stages:
            if stage.status == StageStatus.BLOCKED and not stage.failed_ancestors:
                return 2
            if stage.status == StageStatus.FAILED and stage.failure_kind != "design":
                return 2
            for check in stage.checks:
                if check.id in self.required_checks and check.status != CheckStatus.PASS:
                    if check.status == CheckStatus.FAIL:
                        continue
                    if check.status == CheckStatus.NOT_RUN and stage.status == StageStatus.BLOCKED and stage.failed_ancestors and failed:
                        continue
                    return 2
        if failed:
            return 1
        if any(s.status in {StageStatus.FAILED, StageStatus.BLOCKED} for s in self.stages):
            return 2
        return 0

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(v) for v in value]
            return value
        data = convert(asdict(self))
        data.update({
            "schema_version": RESULT_SCHEMA_VERSION,
            "exit_code": self.exit_code,
            "complete": self.exit_code in (0, 1) and not any(s.status == StageStatus.BLOCKED for s in self.stages),
            "automated_verification": "PASS" if self.exit_code == 0 else "INCOMPLETE" if self.exit_code in (2, 130) else "FAIL",
            "signoff_ready": False,
        })
        return data
