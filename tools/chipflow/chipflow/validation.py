"""Semantic validation of project configuration, ports, constraints, and pads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from chipflow.config.pads import PadError, build_pad_plan, load_pad_map, validate_pads
from chipflow.config.project import ProjectConfig
from chipflow.runner.execution import atomic_write_json, sha256_file
from chipflow.runner.results import Artifact, CheckResult, CheckStatus
from chipflow.runner.snapshot import load_snapshot_manifest, verified_snapshot_file
from chipflow.stages.design_model import Design
from chipflow.technologies.profile import ProcessProfile


@dataclass(frozen=True)
class ValidationRow:
    id: str
    status: CheckStatus
    message: str


def _resolve_constraint_bit(
    port_spec: str,
    design: Design,
    expected_dir: str,
) -> tuple[str | None, str | None]:
    """Resolve a constraint port string to a canonical bit name or return an error."""
    if "[" in port_spec and port_spec.endswith("]"):
        base_name, idx_part = port_spec[:-1].split("[", 1)
        try:
            idx: int | None = int(idx_part)
        except ValueError:
            return None, f"invalid port bit index syntax: {port_spec!r}"
    else:
        base_name = port_spec
        idx = None

    if base_name not in design.ports:
        return None, f"constraint names unknown port {port_spec!r}"

    p_info = design.ports[base_name]
    if p_info.direction != expected_dir:
        return None, f"port {port_spec!r} direction {p_info.direction!r} does not match {expected_dir} constraints"

    if p_info.width == 1 and p_info.offset == 0 and not p_info.upto:
        if idx is not None and idx != 0:
            return None, f"out-of-range bit index [{idx}] on 1-bit port {base_name!r}"
        return base_name, None
    else:
        if idx is None:
            return None, f"missing bit index on bus port {base_name!r}"
        valid_indices = range(p_info.offset, p_info.offset + p_info.width)
        if idx not in valid_indices:
            return None, f"out-of-range bus index [{idx}] for port {base_name!r} (valid: {p_info.offset}..{p_info.offset + p_info.width - 1})"
        return f"{base_name}[{idx}]", None


def validate_project_configuration(
    project: ProjectConfig,
    run_dir: Path,
    design: Design | None,
    profile: ProcessProfile,
) -> tuple[CheckResult, tuple[ValidationRow, ...]]:
    """Validate project configuration against elaborated design and pad profile."""
    conf_hash = sha256_file(run_dir / "resolved-project.json")
    manifest = load_snapshot_manifest(run_dir, project)
    pad_row = manifest[project.pads]
    snap_pads = verified_snapshot_file(run_dir, pad_row)
    input_hashes = {
        "configuration": conf_hash,
        "input_manifest": sha256_file(run_dir / "input-manifest.json"),
        "pad_map": pad_row["sha256"],
        "profile": profile.sha256,
    }
    rows: list[ValidationRow] = []
    evidence: list[Artifact] = []

    # 1. config.clock
    if design is None or not design.top_present:
        rows.append(ValidationRow("config.clock", CheckStatus.NOT_RUN, "elaboration output required"))
    elif project.clock.port not in design.ports:
        rows.append(ValidationRow("config.clock", CheckStatus.FAIL, f"clock port {project.clock.port!r} not found in design"))
    elif design.ports[project.clock.port].direction != "input":
        rows.append(ValidationRow("config.clock", CheckStatus.FAIL, f"clock port {project.clock.port!r} is not an input (got {design.ports[project.clock.port].direction!r})"))
    elif design.ports[project.clock.port].width != 1:
        rows.append(ValidationRow("config.clock", CheckStatus.FAIL, f"clock port {project.clock.port!r} must be 1-bit (got width {design.ports[project.clock.port].width})"))
    else:
        rows.append(ValidationRow("config.clock", CheckStatus.PASS, f"clock port {project.clock.port!r} is a 1-bit input"))

    # 2. config.reset
    if design is None or not design.top_present:
        rows.append(ValidationRow("config.reset", CheckStatus.NOT_RUN, "elaboration output required"))
    elif project.reset.port not in design.ports:
        rows.append(ValidationRow("config.reset", CheckStatus.FAIL, f"reset port {project.reset.port!r} not found in design"))
    elif design.ports[project.reset.port].direction != "input":
        rows.append(ValidationRow("config.reset", CheckStatus.FAIL, f"reset port {project.reset.port!r} is not an input (got {design.ports[project.reset.port].direction!r})"))
    elif design.ports[project.reset.port].width != 1:
        rows.append(ValidationRow("config.reset", CheckStatus.FAIL, f"reset port {project.reset.port!r} must be 1-bit (got width {design.ports[project.reset.port].width})"))
    else:
        rows.append(ValidationRow("config.reset", CheckStatus.PASS, f"reset port {project.reset.port!r} is a 1-bit input"))

    # 3. config.io_coverage
    if design is None or not design.top_present:
        rows.append(ValidationRow("config.io_coverage", CheckStatus.NOT_RUN, "elaboration output required"))
    else:
        io_errors: list[str] = []
        seen_inputs: set[str] = set()
        for c in project.io.inputs:
            bit, err = _resolve_constraint_bit(c.port, design, "input")
            if err:
                io_errors.append(err)
            elif bit:
                if bit == project.clock.port:
                    io_errors.append(f"clock port {project.clock.port!r} must not have input data delay constraints")
                elif bit in seen_inputs:
                    io_errors.append(f"duplicate input constraint for port bit {bit!r}")
                else:
                    seen_inputs.add(bit)

        seen_outputs: set[str] = set()
        for c in project.io.outputs:
            if c.load_pf is None or c.load_pf <= 0:
                io_errors.append(f"output constraint for {c.port!r} missing positive load_pf")
            bit, err = _resolve_constraint_bit(c.port, design, "output")
            if err:
                io_errors.append(err)
            elif bit:
                if bit in seen_outputs:
                    io_errors.append(f"duplicate output constraint for port bit {bit!r}")
                else:
                    seen_outputs.add(bit)

        expected_inputs = {
            b for p in design.ports.values()
            if p.direction == "input" and p.name != project.clock.port
            for b in p.canonical_bits
        }
        expected_outputs = {
            b for p in design.ports.values()
            if p.direction == "output"
            for b in p.canonical_bits
        }

        missing_inputs = expected_inputs - seen_inputs
        if missing_inputs:
            io_errors.append(f"unconstrained input bits: {sorted(missing_inputs)}")

        missing_outputs = expected_outputs - seen_outputs
        if missing_outputs:
            io_errors.append(f"unconstrained output bits: {sorted(missing_outputs)}")

        if io_errors:
            rows.append(ValidationRow("config.io_coverage", CheckStatus.FAIL, "; ".join(io_errors)))
        else:
            rows.append(ValidationRow("config.io_coverage", CheckStatus.PASS, "all input and output port bits covered with timing constraints"))

    # 4. Pad checks
    pad_config = None
    try:
        pad_config = load_pad_map(snap_pads)
    except PadError as exc:
        rows.append(ValidationRow("pads.schema", CheckStatus.FAIL, f"failed to load pad map: {exc}"))
    # Detect a change during parsing too. I/O/hash failures are infrastructure.
    verified_snapshot_file(run_dir, pad_row)

    if pad_config is not None:
        pad_rows = validate_pads(pad_config, profile, design)
        for pr in pad_rows:
            rows.append(ValidationRow(pr.id, pr.status, pr.message))

        if all(pr.status == CheckStatus.PASS for pr in pad_rows):
            pad_plan = build_pad_plan(pad_config, profile)
            plan_file = run_dir / "pad-plan.json"
            atomic_write_json(plan_file, pad_plan)
            evidence.append(Artifact("pad-plan.json", sha256_file(plan_file)))

    # Write validation.json
    validation_data = {
        "schema_version": 1,
        "rows": [{"id": r.id, "status": r.status.value, "message": r.message} for r in rows],
    }
    val_file = run_dir / "validation.json"
    atomic_write_json(val_file, validation_data)
    val_rel = val_file.relative_to(run_dir).as_posix()
    val_artifact = Artifact(val_rel, sha256_file(val_file))
    evidence.insert(0, val_artifact)

    has_fail = any(r.status == CheckStatus.FAIL for r in rows)
    has_not_run = any(r.status == CheckStatus.NOT_RUN for r in rows)

    if has_fail:
        first_fail = next(r for r in rows if r.status == CheckStatus.FAIL)
        check = CheckResult(
            "configuration",
            CheckStatus.FAIL,
            message=first_fail.message,
            evidence=tuple(evidence),
            input_hashes=input_hashes,
        )
    elif has_not_run:
        first_not_run = next(r for r in rows if r.status == CheckStatus.NOT_RUN)
        check = CheckResult(
            "configuration",
            CheckStatus.NOT_RUN,
            message=first_not_run.message,
            evidence=tuple(evidence),
            input_hashes=input_hashes,
        )
    else:
        check = CheckResult(
            "configuration",
            CheckStatus.PASS,
            message="configuration validation passed",
            evidence=tuple(evidence),
            input_hashes=input_hashes,
        )

    return check, tuple(rows)
