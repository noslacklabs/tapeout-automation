"""Stage input envelope matching stage-contracts.md."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Mapping, Sequence

from chipflow.runner.execution import atomic_write_json


def _validate_safe_path(p: str, field_name: str) -> None:
    if not isinstance(p, str) or not p.strip():
        raise ValueError(f"{field_name} must be a nonempty string")
    path = Path(p)
    windows = PureWindowsPath(p)
    if path.is_absolute() or path.drive or windows.drive or ".." in path.parts or ".." in windows.parts or path == Path("."):
        raise ValueError(f"{field_name} must be a safe relative path: {p!r}")


def _validate_hash(h: str, field_name: str) -> None:
    if not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h):
        raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters: {h!r}")


@dataclass(frozen=True)
class StageEnvelope:
    stage_id: str
    adapter: Mapping[str, str]
    inputs: Sequence[Mapping[str, Any]]
    tools: Sequence[Mapping[str, Any]]
    configuration_sha256: str
    seed: int
    threads: int
    outputs: Sequence[Mapping[str, Any]]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("schema_version must be an integer >= 1")
        if not isinstance(self.stage_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.stage_id):
            raise ValueError(f"invalid stage_id: {self.stage_id!r}")
        if not isinstance(self.adapter, Mapping) or not self.adapter.get("id") or not self.adapter.get("version"):
            raise ValueError("adapter must contain nonempty 'id' and 'version'")
        if not isinstance(self.adapter["id"], str) or not isinstance(self.adapter["version"], str):
            raise ValueError("adapter 'id' and 'version' must be strings")

        # Validate inputs
        if not isinstance(self.inputs, Sequence):
            raise ValueError("inputs must be a sequence")
        for idx, item in enumerate(self.inputs):
            if not isinstance(item, Mapping):
                raise ValueError(f"input[{idx}] must be a mapping")
            for key in ("id", "path", "sha256", "bytes"):
                if key not in item:
                    raise ValueError(f"input[{idx}] missing required key {key!r}")
            if not isinstance(item["id"], str) or not item["id"]:
                raise ValueError(f"input[{idx}].id must be a nonempty string")
            _validate_safe_path(item["path"], f"input[{idx}].path")
            _validate_hash(item["sha256"], f"input[{idx}].sha256")
            if isinstance(item["bytes"], bool) or not isinstance(item["bytes"], int) or item["bytes"] < 0:
                raise ValueError(f"input[{idx}].bytes must be a nonnegative integer")

        # Validate tools
        if not isinstance(self.tools, Sequence):
            raise ValueError("tools must be a sequence")
        for idx, item in enumerate(self.tools):
            if not isinstance(item, Mapping):
                raise ValueError(f"tool[{idx}] must be a mapping")
            for key in ("id", "executable_sha256", "version_evidence"):
                if key not in item:
                    raise ValueError(f"tool[{idx}] missing required key {key!r}")
            if not isinstance(item["id"], str) or not item["id"]:
                raise ValueError(f"tool[{idx}].id must be a nonempty string")
            _validate_hash(item["executable_sha256"], f"tool[{idx}].executable_sha256")
            _validate_safe_path(item["version_evidence"], f"tool[{idx}].version_evidence")

        _validate_hash(self.configuration_sha256, "configuration_sha256")

        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if isinstance(self.threads, bool) or not isinstance(self.threads, int) or self.threads < 1:
            raise ValueError("threads must be a positive integer >= 1")

        # Validate outputs
        if not isinstance(self.outputs, Sequence):
            raise ValueError("outputs must be a sequence")
        for idx, item in enumerate(self.outputs):
            if not isinstance(item, Mapping):
                raise ValueError(f"output[{idx}] must be a mapping")
            for key in ("id", "path", "validator"):
                if key not in item:
                    raise ValueError(f"output[{idx}] missing required key {key!r}")
            if not isinstance(item["id"], str) or not item["id"]:
                raise ValueError(f"output[{idx}].id must be a nonempty string")
            _validate_safe_path(item["path"], f"output[{idx}].path")
            if not isinstance(item["validator"], str) or not item["validator"]:
                raise ValueError(f"output[{idx}].validator must be a nonempty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage_id": self.stage_id,
            "adapter": dict(self.adapter),
            "inputs": [dict(x) for x in self.inputs],
            "tools": [dict(x) for x in self.tools],
            "configuration_sha256": self.configuration_sha256,
            "seed": self.seed,
            "threads": self.threads,
            "outputs": [dict(x) for x in self.outputs],
        }

    def write(self, path: Path | str) -> None:
        atomic_write_json(Path(path), self.to_dict())
