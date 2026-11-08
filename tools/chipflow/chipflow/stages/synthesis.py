"""Mapped synthesis stage: wrapper generation, Yosys mapping and netlist checks."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from chipflow.config.pads import build_pad_plan, load_pad_map
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
from chipflow.stages.connectivity import (
    ConnectivityError,
    build_index,
    check_connectivity,
    check_derived_output_gate,
)
from chipflow.stages.constraints import build_constraints
from chipflow.stages.envelope import StageEnvelope
from chipflow.stages.library_model import (
    LibraryParseError,
    parse_lef_macros,
    parse_liberty,
)
from chipflow.stages.netlist_checks import check_mapped_netlist
from chipflow.stages.staging import (
    assert_snapshot_unchanged,
    copy_verified_dependency,
    stage_project_sources,
    validate_design_inputs,
)
from chipflow.stages.wrapper import WrapperError, driver_pin_map, generate_wrapper
from chipflow.technologies.profile import ProcessProfile
from chipflow.toolchain import Toolchain, verify_file


def _read_json(path: Path, label: str) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid {label}: {error}") from error
    return data


def _design_ports(design: Mapping[str, Any], top: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(design, dict) or design.get("top") != top:
        raise RuntimeError(f"elaboration output is not for top {top!r}")
    ports = design.get("ports")
    if not isinstance(ports, dict) or not ports:
        raise RuntimeError("elaboration output has no ports")
    return ports


def synthesis_stage(
    project: ProjectConfig,
    profile: ProcessProfile,
    toolchain: Toolchain,
    run_dir: Path | str,
) -> StageSpec:
    run_path = Path(run_dir).resolve()
    if profile.synthesis is None:
        raise ValueError("profile has no synthesis section")
    validate_design_inputs(project)
    snapshot = load_snapshot_manifest(run_path, project)
    if project.pads not in snapshot:
        raise ConfigError("pad mapping is not in the run snapshot")
    try:
        yosys_tool = toolchain.tool("yosys")
    except StopIteration:
        raise ConfigError("toolchain missing required 'yosys' tool")
    conf_hash = sha256_file(run_path / "resolved-project.json")

    def prepare(stage_dir: Path) -> StageEnvelope:
        verify_file(yosys_tool.executable, yosys_tool.sha256)
        assert_snapshot_unchanged(project, run_path, snapshot)

        staged = stage_project_sources(project, run_path, stage_dir, snapshot)
        inputs: list[dict[str, Any]] = list(staged.inputs)
        inputs.append(copy_verified_dependency(
            run_path, stage_dir, "elaboration", "design.json", "work/design.json",
        ))
        inputs.append(copy_verified_dependency(
            run_path, stage_dir, "technology", "core_typ.lib", "work/core_typ.lib",
        ))
        inputs.append(copy_verified_dependency(
            run_path, stage_dir, "technology", "cells.lef", "work/cells.lef",
        ))
        inputs.append(copy_verified_dependency(
            run_path, stage_dir, "technology", "technology.json", "work/technology.json",
        ))

        pad_row = snapshot[project.pads]
        pad_snapshot = verified_snapshot_file(run_path, pad_row)
        pad_target = stage_dir / "work/pads.toml"
        pad_target.parent.mkdir(parents=True, exist_ok=True)
        pad_target.write_bytes(pad_snapshot.read_bytes())
        pad_hash = sha256_file(pad_target)
        if pad_hash != pad_row["sha256"]:
            raise RuntimeError("staged pad mapping hash mismatch")
        inputs.append({
            "id": "pads",
            "path": "work/pads.toml",
            "sha256": pad_hash,
            "bytes": pad_target.stat().st_size,
        })

        design = _read_json(stage_dir / "work/design.json", "elaboration design.json")
        ports = _design_ports(design, project.top)
        pad_config = load_pad_map(pad_target)
        pad_plan = build_pad_plan(pad_config, profile)

        liberty = parse_liberty(stage_dir / "work/core_typ.lib")
        drivers = driver_pin_map(profile, liberty.cells)
        wrapper = generate_wrapper(
            project.top, ports, pad_plan, profile, drivers, wrapper_top=f"{project.top}_core",
        )
        (stage_dir / "wrapper.v").write_text(wrapper.text, encoding="utf-8", newline="\n")
        atomic_write_json(stage_dir / "wrapper.json", {
            "schema_version": 1,
            "top": wrapper.top,
            "design_top": project.top,
            "ports": [asdict(port) for port in wrapper.ports],
            "drivers": [asdict(driver) for driver in wrapper.drivers],
            "warnings": list(wrapper.warnings),
        })

        constraints = build_constraints(project, profile, pad_plan, ports)
        (stage_dir / "impl.sdc").write_text(constraints.text, encoding="utf-8", newline="\n")
        atomic_write_json(stage_dir / "constraints.json", {
            "schema_version": 1,
            "translation": constraints.translation,
            "rows": [asdict(row) for row in constraints.rows],
        })
        (stage_dir / "abc.constr").write_text(
            f"set_driving_cell {profile.synthesis.abc_driving_cell}\n"
            f"set_load {profile.synthesis.abc_load_pf:g}\n",
            encoding="utf-8",
            newline="\n",
        )

        read_parts = ["read_verilog", "-sv"]
        for include in staged.include_flags:
            read_parts.extend(["-I", include])
        for define in sorted(project.defines):
            read_parts.append(f"-D{define}")
        read_parts.extend(staged.sources)
        read_parts.append("wrapper.v")

        lines = [
            "read_liberty -lib work/core_typ.lib",
            " ".join(read_parts),
        ]
        for name, value in sorted(project.parameters.items()):
            lines.append(f"chparam -set {name} {value} {project.top}")
        lines.extend([
            f"hierarchy -check -top {wrapper.top}",
            "proc",
            "flatten",
            "opt",
            "fsm",
            "opt",
            "memory",
            "opt",
            "techmap",
            "opt",
            f"dfflibmap -liberty work/core_typ.lib",
            # The qualified core Liberty carries power tables but no delay
            # tables, so ABC cannot run its timing-driven buffer insertion
            # (`read_constr`/`buffer` aborts with "Library delay info is not
            # available").  Mapping stays delay-agnostic, as in the legacy flow;
            # the resolved SDC still carries all implementation constraints.
            f"abc -liberty work/core_typ.lib",
            "opt",
            f"hilomap -hicell {profile.synthesis.tie_high_cell} {profile.synthesis.tie_high_pin} "
            f"-locell {profile.synthesis.tie_low_cell} {profile.synthesis.tie_low_pin}",
            "opt_clean -purge",
            "write_verilog -noattr -noexpr -nohex -nodec mapped.v",
            "write_json mapped.json",
            "stat",
            "",
        ])
        (stage_dir / "syn.ys").write_text("\n".join(lines), encoding="utf-8", newline="\n")
        atomic_write_json(stage_dir / "path-map.json", list(staged.path_map))

        return StageEnvelope(
            stage_id="synthesis",
            adapter={"id": "chipflow.synthesis", "version": "0.1.0"},
            inputs=tuple(inputs),
            tools=({
                "id": "yosys",
                "executable_sha256": yosys_tool.sha256,
                "version_evidence": "doctor/doctor.json",
            },),
            configuration_sha256=conf_hash,
            seed=project.resources.seed,
            threads=project.resources.threads,
            outputs=tuple(
                {"id": name, "path": name, "validator": validator}
                for name, validator in (
                    ("wrapper.v", "generated-wrapper-v1"),
                    ("impl.sdc", "resolved-sdc-v1"),
                    ("abc.constr", "abc-constraints-v1"),
                    ("syn.ys", "yosys-script-v1"),
                    ("mapped.v", "mapped-netlist-v1"),
                    ("mapped.json", "yosys-json-v1"),
                    ("syn.log", "log-v1"),
                )
            ),
        )

    def validate_wrapper(path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"^\s*module\s+{re.escape(project.top)}_core\s*\(", text, re.M):
            raise ValueError("wrapper.v does not declare the wrapper module")
        if "u_design" not in text:
            raise ValueError("wrapper.v does not instantiate the design")
        if path.stat().st_size == 0:
            raise ValueError("wrapper.v is empty")

    def validate_sdc(path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.stat().st_size == 0 or not re.search(r"^create_clock\b", text, re.M):
            raise ValueError("impl.sdc has no create_clock command")
        if not re.search(r"^set_(input|output)_delay\b", text, re.M):
            raise ValueError("impl.sdc has no I/O delay constraints")

    def validate_mapped_json(path: Path) -> None:
        data = _read_json(path, "mapped.json")
        modules = data.get("modules") if isinstance(data, dict) else None
        if not isinstance(modules, dict) or f"{project.top}_core" not in modules:
            raise ValueError("mapped.json has no wrapper top module")

    def validate_mapped_verilog(path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.stat().st_size == 0 or f"module {project.top}_core" not in text:
            raise ValueError("mapped.v has no wrapper top module")

    def validate_log(path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("syn.log is missing or empty")

    def validate_abc(path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "set_driving_cell" not in text or "set_load" not in text:
            raise ValueError("abc.constr is incomplete")

    def validate_script(path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "write_json mapped.json" not in text or "write_verilog" not in text:
            raise ValueError("syn.ys does not write the required netlists")

    def assess(stage_dir: Path, cmd: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        assert_snapshot_unchanged(project, run_path, snapshot)
        log_file = stage_dir / "syn.log"
        if not cmd.succeeded:
            message = ""
            if log_file.is_file():
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                message = next((line.strip() for line in lines if "ERROR:" in line), "")
            diagnostic = stage_dir / "synthesis-failure.json"
            atomic_write_json(diagnostic, {
                "schema_version": 1,
                "returncode": cmd.returncode,
                "timed_out": cmd.timed_out,
                "cancelled": cmd.cancelled,
                "error": cmd.error,
                "first_error": message,
            })
            failure_artifact = Artifact(str(diagnostic.relative_to(run_path)), sha256_file(diagnostic))
            evidence = (*artifacts, failure_artifact)
            if message:
                check = CheckResult(
                    "mapped_netlist", CheckStatus.FAIL, message=message, evidence=evidence,
                    input_hashes={"configuration": conf_hash},
                )
                return Assessment(checks=(check,), failure_kind="configuration", extra_artifacts=evidence)
            return Assessment(failure_kind="infrastructure", extra_artifacts=evidence)

        wrapper_data = _read_json(stage_dir / "wrapper.json", "wrapper.json")
        constraint_data = _read_json(stage_dir / "constraints.json", "constraints.json")
        mapped_data = _read_json(stage_dir / "mapped.json", "mapped.json")
        lef = parse_lef_macros(stage_dir / "work/cells.lef")
        liberty = parse_liberty(stage_dir / "work/core_typ.lib")
        allowed = liberty.allowed_cells()
        sequential = liberty.sequential_cells()
        wrapper_top = f"{project.top}_core"
        try:
            index = build_index(mapped_data, wrapper_top)
        except ConnectivityError as error:
            diagnostic = stage_dir / "synthesis-failure.json"
            atomic_write_json(diagnostic, {"schema_version": 1, "connectivity_error": str(error)})
            artifact = Artifact(str(diagnostic.relative_to(run_path)), sha256_file(diagnostic))
            return Assessment(
                checks=(CheckResult("mapped_netlist", CheckStatus.FAIL, str(error),
                                    evidence=(artifact,), input_hashes={"configuration": conf_hash}),),
                failure_kind="design",
                extra_artifacts=(artifact,),
            )

        netlist_rows = list(check_mapped_netlist(
            mapped_data, wrapper_top, allowed, sequential, project.clock.port,
        ))
        connectivity_row = check_connectivity(index, sequential)

        # Wrapper driver invariants: names and output bits survive mapping.
        wrapper_ports = {
            name: record for name, record in index.ports.items()
            if isinstance(record, dict)
        }
        driver_errors: list[str] = []
        driver_evidence: list[dict[str, Any]] = []
        for driver in wrapper_data.get("drivers", []):
            instance = driver.get("instance")
            cell = index.cells.get(instance)
            if cell is None:
                driver_errors.append(f"missing driver instance {instance}")
                continue
            if cell.get("type") != driver.get("cell"):
                driver_errors.append(f"{instance} is {cell.get('type')}, expected {driver.get('cell')}")
            output_port = str(driver.get("target", "")).split("[", 1)[0]
            output_index = 0
            target = driver.get("target", "")
            if "[" in target:
                output_index = int(target.split("[", 1)[1].rstrip("]"))
            port_record = wrapper_ports.get(output_port) or wrapper_ports.get(str(driver.get("target")))
            if port_record is None:
                driver_errors.append(f"{instance} targets unknown wrapper port {target!r}")
            else:
                bits = tuple(port_record.get("bits", ()))
                if output_index >= len(bits):
                    driver_errors.append(f"{instance} targets bit {output_index} outside {output_port}")
                else:
                    expected_bit = bits[output_index]
                    connections = cell.get("connections", {})
                    got = tuple(connections.get(driver.get("output_pin"), ()))
                    if not got or got[0] != expected_bit:
                        driver_errors.append(f"{instance} does not drive {target}")
            connections = cell.get("connections", {})
            inputs = tuple(connections.get(driver.get("input_pin"), ()))
            if not inputs:
                driver_errors.append(f"{instance} has no input connection")
            elif inputs[0] in ("0", "1"):
                driver_errors.append(f"{instance} input is tied to a constant")
            else:
                source_key = str(inputs[0])
                source_driven = source_key in index.drivers or any(
                    source_key in {str(b) for b in port.get("bits", ())}
                    for port in index.ports.values()
                )
                if not source_driven and inputs[0] not in index.drivers:
                    # Integer bit keys are JSON strings in the index; accept the
                    # raw value lookup as well.
                    source_driven = inputs[0] in index.drivers
                if not source_driven:
                    driver_errors.append(f"{instance} input net is undriven")
            driver_evidence.append({
                "instance": instance,
                "cell": cell.get("type"),
                "connections": {pin: list(bits) for pin, bits in cell.get("connections", {}).items()},
            })

        wrapper_errors: list[str] = []
        if wrapper_data.get("top") != wrapper_top:
            wrapper_errors.append("wrapper top does not match the mapped top")
        for port in wrapper_data.get("ports", []):
            if port.get("name") not in index.ports:
                wrapper_errors.append(f"wrapper port {port.get('name')} is missing after mapping")

        derived_rows = []
        derived_evidence = []
        for derived in project.clock.derived_outputs:
            row = check_derived_output_gate(
                index, wrapper_top, derived.check, derived.port, project.clock.port,
                wrapper_ports, sequential,
            )
            derived_rows.append(row)
            derived_evidence.append(dict(row.evidence))

        cell_counts: dict[str, int] = {}
        for cell in index.cells.values():
            ctype = str(cell.get("type", ""))
            cell_counts[ctype] = cell_counts.get(ctype, 0) + 1
        cell_errors = [
            f"{ctype}: no patched LEF width" for ctype in sorted(cell_counts) if ctype not in lef
        ]
        row_width_um = sum(
            lef[ctype].width_um * count for ctype, count in cell_counts.items() if ctype in lef
        )
        flipflops = sum(count for ctype, count in cell_counts.items() if ctype in sequential)
        ties = cell_counts.get(profile.synthesis.tie_high_cell, 0) + cell_counts.get(profile.synthesis.tie_low_cell, 0)
        inventory = {
            "cells": dict(sorted(cell_counts.items())),
            "total_cells": sum(cell_counts.values()),
            "flipflops": flipflops,
            "tie_cells": ties,
            "row_width_um": row_width_um,
            "allowed_cells": sorted(allowed),
            "sequential_cells": sorted(sequential),
        }

        synthesis_json = {
            "schema_version": 1,
            "top": wrapper_top,
            "design_top": project.top,
            "profile": {"id": profile.profile, "version": profile.profile_version, "sha256": profile.sha256},
            "inputs": {item["id"]: item["sha256"] for item in ({"id": "configuration", "sha256": conf_hash},)},
            "inventory": inventory,
            "connectivity": dict(connectivity_row.details),
            "wrapper": {
                "ports": wrapper_data.get("ports", []),
                "drivers": driver_evidence,
            },
            "constraints": constraint_data.get("translation", {}),
            "derived_outputs": derived_evidence,
        }
        synthesis_file = stage_dir / "synthesis.json"
        atomic_write_json(synthesis_file, synthesis_json)
        synthesis_artifact = Artifact(str(synthesis_file.relative_to(run_path)), sha256_file(synthesis_file))
        evidence = (*artifacts, synthesis_artifact)
        input_hashes = {
            "configuration": conf_hash,
            "mapped": sha256_file(stage_dir / "mapped.json"),
            "wrapper": sha256_file(stage_dir / "wrapper.v"),
            "technology": sha256_file(stage_dir / "work/core_typ.lib"),
            "elaboration": sha256_file(stage_dir / "work/design.json"),
        }

        rows: list[CheckResult] = []
        for row in netlist_rows:
            rows.append(CheckResult(row.id, row.status, message=row.message,
                                    evidence=evidence, input_hashes=input_hashes))
        rows.append(CheckResult(
            connectivity_row.id, CheckStatus(connectivity_row.status), message=connectivity_row.message,
            evidence=evidence, input_hashes=input_hashes,
        ))
        rows.append(CheckResult(
            "synthesis.drivers",
            CheckStatus.FAIL if driver_errors else CheckStatus.PASS,
            message="; ".join(driver_errors) or f"{len(driver_evidence)} pad drivers mapped exactly once",
            evidence=evidence, input_hashes=input_hashes,
        ))
        rows.append(CheckResult(
            "synthesis.wrapper",
            CheckStatus.FAIL if wrapper_errors else CheckStatus.PASS,
            message="; ".join(wrapper_errors) or "wrapper ports survive mapping",
            evidence=evidence, input_hashes=input_hashes,
        ))
        rows.append(CheckResult(
            "synthesis.cells",
            CheckStatus.FAIL if cell_errors else CheckStatus.PASS,
            message="; ".join(cell_errors) or (
                f"{sum(cell_counts.values())} cells, {flipflops} flip-flops, "
                f"{ties} ties, {row_width_um:.0f} um row width"
            ),
            evidence=evidence, input_hashes=input_hashes,
        ))
        rows.append(CheckResult(
            "synthesis.abc",
            CheckStatus.WARN,
            message=(
                "core Liberty has no delay tables; ABC mapping is delay-agnostic "
                "(legacy-compatible) and abc.constr is recorded but not applied"
            ),
            evidence=evidence, input_hashes=input_hashes,
        ))
        for row in derived_rows:
            rows.append(CheckResult(row.id, CheckStatus(row.status), message=row.message,
                                    evidence=evidence, input_hashes=input_hashes))
        for row in constraint_data.get("rows", []):
            rows.append(CheckResult(row["id"], CheckStatus(row["status"]), message=row["message"],
                                    evidence=evidence, input_hashes=input_hashes))

        netlist_failures = [row for row in rows if row.status == CheckStatus.FAIL and not row.id.startswith("synthesis.constraints")]
        constraint_failures = [row for row in rows if row.status == CheckStatus.FAIL and row.id.startswith("synthesis.constraints")]
        netlist_status = CheckStatus.FAIL if netlist_failures else CheckStatus.PASS
        aggregate = CheckResult(
            "mapped_netlist", netlist_status,
            message=(
                f"{netlist_failures[0].message}" if netlist_failures else
                f"{sum(cell_counts.values())} cells mapped from {len(allowed)} allowed cells"
            ),
            evidence=evidence, input_hashes=input_hashes,
        )
        rows.insert(0, aggregate)
        if constraint_failures:
            return Assessment(checks=tuple(rows), failure_kind="configuration", extra_artifacts=evidence)
        if netlist_failures:
            return Assessment(checks=tuple(rows), failure_kind="design", extra_artifacts=evidence)
        return Assessment(checks=tuple(rows), extra_artifacts=evidence)

    return StageSpec(
        id="synthesis",
        command=(str(yosys_tool.executable), "-q", "-l", "syn.log", "-s", "syn.ys"),
        dependencies=("elaboration", "technology"),
        timeout_s=float(project.physical.synthesis_timeout_seconds),
        required_outputs=("wrapper.v", "impl.sdc", "abc.constr", "syn.ys", "mapped.v", "mapped.json", "syn.log"),
        validators={
            "wrapper.v": validate_wrapper,
            "impl.sdc": validate_sdc,
            "abc.constr": validate_abc,
            "syn.ys": validate_script,
            "mapped.v": validate_mapped_verilog,
            "mapped.json": validate_mapped_json,
            "syn.log": validate_log,
        },
        prepare=prepare,
        assess=assess,
    )
