"""Structural project contract. Elaboration and electrical qualification are separate gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re
import tomllib
from typing import Any

from chipflow import PROFILE_VERSION, SCHEMA_VERSION
from chipflow.runner.results import MINIMUM_REQUIRED_CHECKS


class ConfigError(ValueError):
    """A project cannot be resolved safely and unambiguously."""


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
_PORT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*(?:\[-?[0-9]+\])?\Z")


def _table(value: Any, field: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a table")
    unknown = value.keys() - allowed
    missing = required - value.keys()
    if unknown:
        raise ConfigError(f"{field}: unknown keys: {', '.join(sorted(unknown))}")
    if missing:
        raise ConfigError(f"{field}: missing keys: {', '.join(sorted(missing))}")
    return value


def _string(value: Any, field: str, pattern: re.Pattern | None = None) -> str:
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\x00\r\n"):
        raise ConfigError(f"{field} must be a nonempty single-line string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ConfigError(f"{field} has unsupported syntax: {value!r}")
    return value


def _strings(value: Any, field: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ConfigError(f"{field} must be {'a nonempty' if nonempty else 'an'} array of strings")
    result = tuple(_string(v, f"{field}[{i}]") for i, v in enumerate(value))
    if len(set(result)) != len(result):
        raise ConfigError(f"{field} contains duplicate entries")
    return result


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ConfigError(f"{field} must be finite numeric data")
    if value < 0 or (positive and value == 0):
        raise ConfigError(f"{field} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _path(value: Any, field: str, root: Path, *, directory: bool = False) -> Path:
    raw = _string(value, field)
    # Reject Windows absolute syntax even when a manifest is loaded on Linux.
    if Path(raw).is_absolute() or re.match(r"^[A-Za-z]:", raw) or raw.startswith(("\\", "/")):
        raise ConfigError(f"{field} must be relative to the project directory")
    path = (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ConfigError(f"{field} escapes the project directory: {raw}")
    if directory and not path.is_dir():
        raise ConfigError(f"{field} is not an existing directory: {raw}")
    if not directory and not path.is_file():
        raise ConfigError(f"{field} is not an existing file: {raw}")
    return path


@dataclass(frozen=True)
class DerivedOutput:
    port: str
    check: str


@dataclass(frozen=True)
class Clock:
    port: str
    period_ns: float
    waveform_ns: tuple[float, float]
    transition_ns: float
    uncertainty_ns: float
    derived_outputs: tuple[DerivedOutput, ...] = ()


@dataclass(frozen=True)
class Reset:
    port: str
    kind: str
    active: str


@dataclass(frozen=True)
class PortDelay:
    port: str
    min_ns: float
    max_ns: float
    load_pf: float | None = None


@dataclass(frozen=True)
class IOConstraints:
    corners: tuple[str, ...]
    inputs: tuple[PortDelay, ...]
    outputs: tuple[PortDelay, ...]


@dataclass(frozen=True)
class Resources:
    seed: int
    threads: int
    timeout_seconds: float


@dataclass(frozen=True)
class Verification:
    required_checks: tuple[str, ...]
    optional_checks: tuple[str, ...]
    manual_items: tuple[str, ...]
    data: tuple[Path, ...]


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    directory: Path
    schema_version: int
    name: str
    top: str
    sources: tuple[Path, ...]
    include_dirs: tuple[Path, ...]
    defines: tuple[str, ...]
    parameters: dict[str, int | str]
    extra_files: tuple[Path, ...]
    technology: str
    frame: str
    profile_version: str
    top_cell: str
    mode: str
    development_identifiers: bool
    clock: Clock
    reset: Reset
    io: IOConstraints
    pads: Path
    build_root: Path
    resources: Resources
    verification: Verification
    dependencies: tuple[Path, ...]
    remaining_checks: tuple[str, ...] = (
        "RTL elaboration and supported state/clock structure",
        "port, timing and pad-map coverage against elaborated RTL",
        "pad allowlist, complete frame slots and unused-slot policy qualification",
        "technology/model qualification and external fingerprints",
    )

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Path):
                return str(value)
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(v) for v in value]
            return value
        return convert(asdict(self))


def _delays(value: Any, field: str, *, outputs: bool) -> tuple[PortDelay, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be an array of tables")
    allowed = {"port", "min_ns", "max_ns"} | ({"load_pf"} if outputs else set())
    result = []
    seen = set()
    for i, entry in enumerate(value):
        key = f"{field}[{i}]"
        row = _table(entry, key, allowed, allowed)
        port = _string(row["port"], key + ".port", _PORT)
        if port in seen:
            raise ConfigError(f"{field}: duplicate port {port}")
        seen.add(port)
        low = _number(row["min_ns"], key + ".min_ns")
        high = _number(row["max_ns"], key + ".max_ns")
        if low > high:
            raise ConfigError(f"{key}: min_ns exceeds max_ns")
        load = _number(row["load_pf"], key + ".load_pf", positive=True) if outputs else None
        result.append(PortDelay(port, low, high, load))
    return tuple(result)


def load_project(path: str | Path) -> ProjectConfig:
    """Resolve schema v1. This does not claim elaboration or pad qualification."""
    path = Path(path).resolve()
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read project {path}: {exc}") from exc
    root = path.parent
    sections = {"schema_version", "project", "technology", "clock", "reset", "io", "pads", "build", "verification"}
    raw = _table(raw, "chip", sections, sections)
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise ConfigError(f"schema_version must be {SCHEMA_VERSION}")

    project = _table(raw["project"], "project", {"name", "top", "sources", "include_dirs", "defines", "parameters", "extra_files"}, {"name", "top", "sources"})
    name = _string(project["name"], "project.name", re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z"))
    top = _string(project["top"], "project.top", _IDENTIFIER)
    sources = tuple(_path(v, "project.sources", root) for v in _strings(project["sources"], "project.sources", nonempty=True))
    if len(set(sources)) != len(sources):
        raise ConfigError("project.sources resolves to duplicate files")
    if any(p.suffix.lower() not in {".v", ".sv"} for p in sources):
        raise ConfigError("project.sources supports only .v and .sv RTL")
    includes = tuple(_path(v, "project.include_dirs", root, directory=True) for v in _strings(project.get("include_dirs", []), "project.include_dirs"))
    defines = _strings(project.get("defines", []), "project.defines")
    for define in defines:
        _string(define.split("=", 1)[0], "project.defines macro", _IDENTIFIER)
    parameters = project.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ConfigError("project.parameters must be a table")
    for key, value in parameters.items():
        _string(key, "project.parameters name", _IDENTIFIER)
        if type(value) is not int and not isinstance(value, str):
            raise ConfigError(f"project.parameters.{key} must be an integer or single-line string")
        if isinstance(value, str):
            _string(value, f"project.parameters.{key}")
    extras = tuple(_path(v, "project.extra_files", root) for v in _strings(project.get("extra_files", []), "project.extra_files"))

    technology = _table(raw["technology"], "technology", {"id", "frame", "profile_version", "top_cell", "mode", "development_identifiers"}, {"id", "frame", "profile_version", "top_cell", "mode", "development_identifiers"})
    for key, expected in {"id": "scl_c1d", "frame": "scl-c1d-2mm", "profile_version": PROFILE_VERSION, "mode": "development"}.items():
        if technology[key] != expected:
            raise ConfigError(f"technology.{key}: this foundation supports only {expected!r}")
    top_cell = _string(technology["top_cell"], "technology.top_cell", _IDENTIFIER)
    if type(technology["development_identifiers"]) is not bool:
        raise ConfigError("technology.development_identifiers must be a boolean")

    clock_raw = _table(raw["clock"], "clock", {"port", "period_ns", "waveform_ns", "transition_ns", "uncertainty_ns", "derived_outputs"}, {"port", "period_ns", "waveform_ns", "transition_ns", "uncertainty_ns"})
    period = _number(clock_raw["period_ns"], "clock.period_ns", positive=True)
    waveform = clock_raw["waveform_ns"]
    if not isinstance(waveform, list) or len(waveform) != 2:
        raise ConfigError("clock.waveform_ns must contain rising and falling edge times")
    edges = tuple(_number(v, "clock.waveform_ns") for v in waveform)
    if not 0 <= edges[0] < edges[1] < period:
        raise ConfigError("clock.waveform_ns must satisfy 0 <= rising < falling < period_ns")
    transition = _number(clock_raw["transition_ns"], "clock.transition_ns", positive=True)
    uncertainty = _number(clock_raw["uncertainty_ns"], "clock.uncertainty_ns")
    if transition >= period or uncertainty >= period:
        raise ConfigError("clock transition and uncertainty must each be less than the period")
    derived_outputs_raw = clock_raw.get("derived_outputs", [])
    if not isinstance(derived_outputs_raw, list):
        raise ConfigError("clock.derived_outputs must be an array of tables")
    derived_outputs_list = []
    seen_derived_ports = set()
    for i, entry in enumerate(derived_outputs_raw):
        key = f"clock.derived_outputs[{i}]"
        entry_table = _table(entry, key, {"port", "check"}, {"port", "check"})
        d_port = _string(entry_table["port"], f"{key}.port", _IDENTIFIER)
        if d_port in seen_derived_ports:
            raise ConfigError(f"clock.derived_outputs: duplicate port {d_port}")
        seen_derived_ports.add(d_port)
        d_check = _string(entry_table["check"], f"{key}.check", re.compile(r"[a-z][a-z0-9_.-]*\Z"))
        derived_outputs_list.append(DerivedOutput(d_port, d_check))
    derived_outputs = tuple(derived_outputs_list)
    clock = Clock(_string(clock_raw["port"], "clock.port", _IDENTIFIER), period, edges, transition, uncertainty, derived_outputs)

    reset_raw = _table(raw["reset"], "reset", {"port", "kind", "active"}, {"port", "kind", "active"})
    if reset_raw["kind"] != "synchronous":
        raise ConfigError("reset.kind must be synchronous; asynchronous-reset qualification is unsupported")
    if reset_raw["active"] not in ("high", "low"):
        raise ConfigError("reset.active must be high or low")
    reset = Reset(_string(reset_raw["port"], "reset.port", _IDENTIFIER), "synchronous", reset_raw["active"])
    if reset.port == clock.port:
        raise ConfigError("clock and reset must name different ports")

    io_raw = _table(raw["io"], "io", {"corners", "inputs", "outputs"}, {"corners", "inputs", "outputs"})
    corners = _strings(io_raw["corners"], "io.corners", nonempty=True)
    if set(corners) != {"min", "typ", "max"}:
        raise ConfigError("io.corners must cover min, typ and max")
    inputs = _delays(io_raw["inputs"], "io.inputs", outputs=False)
    outputs = _delays(io_raw["outputs"], "io.outputs", outputs=True)
    input_ports, output_ports = {p.port for p in inputs}, {p.port for p in outputs}
    if input_ports & output_ports:
        raise ConfigError("io: a port cannot have both input and output constraints")
    if clock.port in input_ports | output_ports or reset.port in output_ports:
        raise ConfigError("io: clock must not have data delays and reset must not be an output")
    if reset.port not in input_ports:
        raise ConfigError("io.inputs must include the synchronous reset's min/max delay")
    for d in clock.derived_outputs:
        if d.port not in output_ports and not any(p.port == d.port or p.port.startswith(f"{d.port}[") for p in outputs):
            raise ConfigError(f"clock.derived_outputs port {d.port!r} must be declared in io.outputs")

    pads_raw = _table(raw["pads"], "pads", {"mapping"}, {"mapping"})
    pads = _path(pads_raw["mapping"], "pads.mapping", root)
    # This first contract records the mapping bytes. Its cell/pin/frame policy must
    # be qualified against the real kit; parsing a file does not establish that.

    build = _table(raw["build"], "build", {"root", "seed", "threads", "timeout_seconds"}, {"root", "seed", "threads", "timeout_seconds"})
    build_root_raw = _string(build["root"], "build.root")
    build_root = (root / build_root_raw).resolve()
    if not build_root.is_relative_to(root) or build_root == root:
        raise ConfigError("build.root must be a dedicated subdirectory of the project")
    resources = Resources(_integer(build["seed"], "build.seed", 0, 2**32 - 1), _integer(build["threads"], "build.threads", 1, 1024), _number(build["timeout_seconds"], "build.timeout_seconds", positive=True))

    verification_raw = _table(raw["verification"], "verification", {"required_checks", "optional_checks", "manual_items", "data"})
    required = _strings(verification_raw.get("required_checks", []), "verification.required_checks")
    optional = _strings(verification_raw.get("optional_checks", []), "verification.optional_checks")
    derived_checks = tuple(d.check for d in clock.derived_outputs)
    required = tuple(dict.fromkeys((*MINIMUM_REQUIRED_CHECKS, *derived_checks, *required)))
    for check_id in (*required, *optional):
        _string(check_id, "verification check ID", re.compile(r"[a-z][a-z0-9_.-]*\Z"))
    if set(optional) & set(required):
        raise ConfigError("verification.optional_checks cannot include a required/profile-minimum check")
    manual = _strings(verification_raw.get("manual_items", []), "verification.manual_items")
    data = tuple(_path(v, "verification.data", root) for v in _strings(verification_raw.get("data", []), "verification.data"))

    dependency_list = [path, *sources, *extras, pads, *data]
    for include in includes:
        for candidate in sorted(include.rglob("*")):
            if candidate.is_symlink() and not candidate.resolve().is_relative_to(root):
                raise ConfigError(f"include dependency escapes project through symlink: {candidate}")
            if candidate.is_file():
                dependency_list.append(candidate.resolve())
    dependencies = tuple(dict.fromkeys(dependency_list))
    for dependency in dependencies:
        if dependency.is_relative_to(build_root) or build_root.is_relative_to(dependency):
            raise ConfigError(f"build.root overlaps an input dependency: {dependency}")
    for include in includes:
        if build_root.is_relative_to(include) or include.is_relative_to(build_root):
            raise ConfigError("build.root overlaps an include directory")

    return ProjectConfig(path, root, SCHEMA_VERSION, name, top, sources, includes, defines, dict(parameters), extras, technology["id"], technology["frame"], PROFILE_VERSION, top_cell, "development", technology["development_identifiers"], clock, reset, IOConstraints(corners, inputs, outputs), pads, build_root, resources, Verification(required, optional, manual, data), dependencies)
