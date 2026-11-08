"""Technology preparation: filtered models, patched LEF, pad/pin qualification."""

from __future__ import annotations

from dataclasses import asdict
from importlib.resources import files
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import Any

from chipflow.config.project import ProjectConfig
from chipflow.runner.execution import (
    Artifact,
    Assessment,
    CommandResult,
    StageSpec,
    atomic_write_json,
    sha256_file,
)
from chipflow.runner.results import CheckResult, CheckStatus
from chipflow.stages.envelope import StageEnvelope
from chipflow.stages.library_model import (
    LibraryParseError,
    parse_cdl,
    parse_lef_macros,
    parse_liberty,
    parse_verilog_ports,
)
from chipflow.technologies.profile import ProcessProfile
from chipflow.toolchain import Toolchain, verify_file


INPUT_COPIES = (
    ("tech_lef", "work/tech.lef"),
    ("core_lef", "work/core.lef"),
    ("core_gds", "work/core.gds"),
    ("io_gds", "work/io_pad.gds"),
    ("core_cdl", "work/core.cdl"),
    ("io_cdl", "work/io_pad.cdl"),
    ("frame_gds", "work/frame.gds"),
    ("frame_cdl", "work/frame.cdl"),
    ("core_typ_lib", "work/core_typ.lib"),
    ("core_min_lib", "work/core_min.lib"),
    ("core_max_lib", "work/core_max.lib"),
    ("pads_typ_lib", "work/pads_typ.lib"),
    ("pads_min_lib", "work/pads_min.lib"),
    ("pads_max_lib", "work/pads_max.lib"),
    ("cells_verilog", "work/cells.v"),
)

OUTPUT_LIBS = (
    "core_typ.lib", "core_min.lib", "core_max.lib",
    "pads_typ.lib", "pads_min.lib", "pads_max.lib",
)


def _close(observed: float | None, expected: float) -> bool:
    if observed is None:
        return False
    return abs(observed - expected) <= max(1e-6, abs(expected) * 0.02)


def _pin_direction(cell, pin: str) -> str | None:
    info = cell.pins.get(pin)
    return info.direction if info else None


def _inverting_function(function: str, input_pin: str) -> bool | None:
    """Return True/False for a simple unary function, None if unsupported."""
    compact = re.sub(r"\s+", "", function or "")
    if compact == f"!{input_pin}":
        return True
    if compact == input_pin:
        return False
    return None


def _model_rows(profile: ProcessProfile, technology_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate pad direction, polarity, power pins and pin maps against the PDK views."""
    lib = parse_liberty(technology_dir / "pads_typ.lib")
    cdl = parse_cdl(technology_dir / "work/io_pad.cdl")
    verilog = parse_verilog_ports(technology_dir / "work/cells.v")
    rows: list[dict[str, Any]] = []
    models: dict[str, Any] = {}
    for name, expected in sorted(profile.pad_cells.items()):
        errors: list[str] = []
        warnings: list[str] = []
        cell = lib.cells.get(name)
        if cell is None:
            errors.append(f"{name}: missing from pads Liberty")
            model = {"errors": errors, "warnings": warnings}
            rows.append({"id": f"pads.model.{name}", "status": "FAIL", "message": "; ".join(errors)})
            models[name] = model
            continue
        non_power = {pin.name: pin for pin in cell.pins.values() if not pin.is_power}
        expected_pins = {expected.core_pin, expected.pad_pin}
        if set(non_power) != expected_pins:
            errors.append(f"{name}: Liberty pins {sorted(non_power)} != {sorted(expected_pins)}")
        if _pin_direction(cell, expected.core_pin) != ("input" if expected.direction == "output" else "output"):
            errors.append(f"{name}: core pin {expected.core_pin} has wrong direction")
        if _pin_direction(cell, expected.pad_pin) != ("output" if expected.direction == "output" else "input"):
            errors.append(f"{name}: pad pin {expected.pad_pin} has wrong direction")
        pad_pin = non_power.get(expected.pad_pin)
        core_pin = non_power.get(expected.core_pin)
        # Polarity is defined by the pad cell's output pin and the input pin its
        # function references.  For OPAD04 that is PAD=!IN1; for the input pads
        # it is OUT1=IN1.  The profile's `inverting` flag describes this path.
        output_pins = [pin for pin in non_power.values() if pin.direction == "output"]
        input_pins = [pin for pin in non_power.values() if pin.direction == "input"]
        polarity = None
        if len(output_pins) != 1 or len(input_pins) != 1:
            errors.append(f"{name}: expected exactly one input and one output pin")
        else:
            polarity = _inverting_function(output_pins[0].function or "", input_pins[0].name)
            if polarity is None:
                errors.append(f"{name}: unsupported pad function {output_pins[0].function!r}")
            elif polarity != expected.inverting:
                errors.append(f"{name}: pad polarity {'inverting' if polarity else 'non-inverting'} contradicts profile")
        if expected.direction == "output":
            if not _close(core_pin.capacitance_pf if core_pin else None, expected.core_pin_capacitance_pf):
                errors.append(f"{name}: core pin capacitance does not match profile")
            if not _close(pad_pin.max_capacitance_pf if pad_pin else None, expected.max_pad_load_pf):
                errors.append(f"{name}: pad max load does not match profile")
        else:
            if not _close(pad_pin.capacitance_pf if pad_pin else None, expected.pad_pin_capacitance_pf):
                errors.append(f"{name}: pad pin capacitance does not match profile")
            if not _close(core_pin.max_capacitance_pf if core_pin else None, expected.max_core_load_pf):
                errors.append(f"{name}: core max load does not match profile")

        subckt = cdl.get(name)
        if subckt is None:
            errors.append(f"{name}: missing from pad CDL")
            cdl_pins: tuple[str, ...] = ()
        else:
            cdl_pins = subckt.pins
            if tuple(expected.cdl_pins) != cdl_pins:
                errors.append(f"{name}: CDL pins {cdl_pins} != {expected.cdl_pins}")
        for power in expected.power_pins:
            if cdl_pins.count(power) != 1:
                errors.append(f"{name}: CDL must contain power pin {power} exactly once")
        if set(expected.power_pins) - set(expected.cdl_pins):
            errors.append(f"{name}: power pins are not declared in cdl_pins")

        v_pins = verilog.get(name)
        if v_pins is None:
            if any(item.startswith("SCL-C1D-MODEL") for item in expected.limitations):
                warnings.append(f"{name}: no Verilog model, covered by {', '.join(expected.limitations)}")
            else:
                errors.append(f"{name}: missing Verilog module")
        elif set(v_pins) != expected_pins:
            errors.append(f"{name}: Verilog ports {sorted(v_pins)} != {sorted(expected_pins)}")

        models[name] = {
            "liberty_pins": {
                pin.name: {
                    "direction": pin.direction,
                    "function": pin.function,
                    "capacitance_pf": pin.capacitance_pf,
                    "max_capacitance_pf": pin.max_capacitance_pf,
                }
                for pin in cell.pins.values()
            },
            "cdl_pins": list(cdl_pins),
            "verilog_pins": list(v_pins) if v_pins is not None else None,
            "polarity": "inverting" if polarity else "non-inverting" if polarity is not None else None,
            "errors": errors,
            "warnings": warnings,
        }
        status = "FAIL" if errors else "WARN" if warnings else "PASS"
        message = "; ".join(errors or warnings) or "Liberty, CDL and Verilog pin maps agree"
        rows.append({"id": f"pads.model.{name}", "status": status, "message": message})

    # Power pads are not synthesized, so only the pad Liberty and CDL identities matter.
    for pad in profile.power_pads:
        errors = []
        if pad.cell not in lib.cells:
            errors.append(f"{pad.cell}: missing from pads Liberty")
        subckt = cdl.get(pad.cell)
        if subckt is None:
            errors.append(f"{pad.cell}: missing from pad CDL")
        elif pad.net not in subckt.pins:
            errors.append(f"{pad.cell}: CDL has no {pad.net} pin")
        status = "FAIL" if errors else "PASS"
        rows.append({
            "id": f"pads.power.{pad.slot}",
            "status": status,
            "message": "; ".join(errors) or f"{pad.cell} carries {pad.net}",
        })
        models[f"power:{pad.slot}"] = {"cell": pad.cell, "net": pad.net, "errors": errors}
    return rows, models


def _driver_rows(profile: ProcessProfile, technology_dir: Path) -> list[dict[str, Any]]:
    lib = parse_liberty(technology_dir / "core_typ.lib")
    lef = parse_lef_macros(technology_dir / "cells.lef")
    rows = []
    for name, pad in sorted(profile.pad_cells.items()):
        if pad.direction != "output":
            continue
        errors = []
        for driver in pad.drivers:
            cell = lib.cells.get(driver)
            if cell is None:
                errors.append(f"{driver} missing from filtered core Liberty")
                continue
            if driver not in lef:
                errors.append(f"{driver} missing from patched cell LEF")
            outputs = [pin for pin in cell.pins.values() if pin.direction == "output"]
            if not outputs:
                errors.append(f"{driver} has no output pin")
                continue
            inverting = [
                pin for pin in outputs
                if any(_inverting_function(pin.function or "", candidate.name) is True for candidate in cell.pins.values()
                       if candidate.direction == "input")
            ]
            if not inverting:
                errors.append(f"{driver} is not an inverting driver")
        status = "FAIL" if errors else "PASS"
        rows.append({
            "id": f"pads.driver.{name}",
            "status": status,
            "message": "; ".join(errors) or f"drivers {', '.join(pad.drivers)} are present and inverting",
        })
    return rows


def technology_stage(
    project: ProjectConfig,
    profile: ProcessProfile,
    toolchain: Toolchain,
    run_dir: Path | str,
) -> StageSpec:
    run_path = Path(run_dir).resolve()
    if profile.frame_geometry is None or profile.technology_geometry is None or profile.synthesis is None:
        raise ValueError("profile has no physical geometry or synthesis section")
    if profile.frame != project.frame:
        raise ValueError("project frame does not match the technology profile")
    geometry = profile.technology_geometry
    frame = profile.frame_geometry

    pdk_files = {identifier: toolchain.pdk_file(identifier) for identifier, _ in INPUT_COPIES}
    config_hash = sha256_file(run_path / "resolved-project.json")
    profile_hash = profile.sha256

    def prepare(stage_dir: Path) -> StageEnvelope:
        for identifier, relative in INPUT_COPIES:
            item = pdk_files[identifier]
            verify_file(item.path, item.sha256)
            target = stage_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item.path, target)
            if sha256_file(target) != item.sha256:
                raise RuntimeError(f"staged PDK copy hash mismatch: {identifier}")

        helper = files("chipflow").joinpath("resources/tech_prep.py").read_bytes()
        (stage_dir / "tech_prep.py").write_bytes(helper)

        drop = list(profile.excluded_for("P01"))
        force = list(profile.excluded_for("P02"))
        input_data = {
            "grid_um": profile.grid_um,
            "site_width_um": geometry.site_width_um,
            "core_gds_offsets_um": dict(geometry.core_gds_offsets_um),
            "gds_layers": {
                "metal1": list(geometry.gds_metal1),
                "metal2": list(geometry.gds_metal2),
                "via1": list(geometry.gds_via1),
            },
            "drop_cells": drop,
            "force_cells": force,
            "tech_lef": "work/tech.lef",
            "core_lef": "work/core.lef",
            "core_gds": "work/core.gds",
            "io_gds": "work/io_pad.gds",
            "frame_gds": "work/frame.gds",
            "frame_top_cell": profile.frame_top_cell,
            "core_typ_lib": "work/core_typ.lib",
            "core_min_lib": "work/core_min.lib",
            "core_max_lib": "work/core_max.lib",
            "pads_typ_lib": "work/pads_typ.lib",
            "pads_min_lib": "work/pads_min.lib",
            "pads_max_lib": "work/pads_max.lib",
            "out_tech_lef": "tech.lef",
            "out_cells_lef": "cells.lef",
            "out_core_typ_lib": "core_typ.lib",
            "out_core_min_lib": "core_min.lib",
            "out_core_max_lib": "core_max.lib",
            "out_pads_typ_lib": "pads_typ.lib",
            "out_pads_min_lib": "pads_min.lib",
            "out_pads_max_lib": "pads_max.lib",
            "out_summary": "tech-summary.json",
            "pad_cells": sorted(profile.pad_cells),
        }
        atomic_write_json(stage_dir / "tech-input.json", input_data)
        atomic_write_json(stage_dir / "path-map.json", [
            {"id": identifier, "source": str(pdk_files[identifier].path),
             "staged": relative, "sha256": pdk_files[identifier].sha256}
            for identifier, relative in INPUT_COPIES
        ])
        return StageEnvelope(
            stage_id="technology",
            adapter={"id": "scl.technology-prep", "version": "0.1.0"},
            inputs=tuple(
                {"id": identifier, "path": relative, "sha256": pdk_files[identifier].sha256,
                 "bytes": (stage_dir / relative).stat().st_size}
                for identifier, relative in INPUT_COPIES
            ),
            tools=(
                {"id": "python", "executable_sha256": sha256_file(Path(sys.executable)),
                 "version_evidence": "doctor/doctor.json"},
            ),
            configuration_sha256=config_hash,
            seed=project.resources.seed,
            threads=project.resources.threads,
            outputs=tuple(
                {"id": name, "path": name, "validator": "technology-v1"}
                for name in ("tech.lef", "cells.lef", "tech-summary.json", *OUTPUT_LIBS)
            ),
        )

    def validate_nonempty(path: Path) -> None:
        if path.stat().st_size == 0:
            raise ValueError(f"empty technology output: {path.name}")
        if path.suffix == ".lib":
            parse_liberty(path)
        elif path.name == "cells.lef":
            parse_lef_macros(path)

    def assess(stage_dir: Path, cmd: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        if not cmd.succeeded:
            stderr = Path(cmd.stderr_log).read_text(encoding="utf-8", errors="replace") if Path(cmd.stderr_log).is_file() else ""
            stdout = Path(cmd.stdout_log).read_text(encoding="utf-8", errors="replace") if Path(cmd.stdout_log).is_file() else ""
            message = next((line.strip() for line in (stderr + "\n" + stdout).splitlines() if line.strip()), "")
            diagnostic = stage_dir / "technology-failure.json"
            atomic_write_json(diagnostic, {
                "schema_version": 1,
                "message": message or "technology preparation failed",
                "exit_code": cmd.returncode,
                "timed_out": cmd.timed_out,
                "cancelled": cmd.cancelled,
                "stdout": Path(cmd.stdout_log).name,
                "stderr": Path(cmd.stderr_log).name,
            })
            failure_artifact = Artifact(str(diagnostic.relative_to(run_path)), sha256_file(diagnostic))
            evidence = (*artifacts, failure_artifact)
            configuration = "absent from the core Liberty" in message
            check = CheckResult(
                "technology.inputs", CheckStatus.FAIL if configuration else CheckStatus.NOT_RUN,
                message=message or "technology preparation failed", evidence=evidence,
                input_hashes={"profile": profile_hash, "configuration": config_hash} if configuration else {},
            )
            return Assessment(checks=(check,), failure_kind="configuration" if configuration else "infrastructure",
                              extra_artifacts=evidence)

        summary = json.loads((stage_dir / "tech-summary.json").read_text(encoding="utf-8"))
        if not isinstance(summary, dict) or summary.get("schema_version") != 1:
            return Assessment(failure_kind="infrastructure")
        lib = parse_liberty(stage_dir / "core_typ.lib")
        lef = parse_lef_macros(stage_dir / "cells.lef")
        model_rows, pad_models = _model_rows(profile, stage_dir)
        driver_rows = _driver_rows(profile, stage_dir)

        tech_text = (stage_dir / "tech.lef").read_text(encoding="utf-8", errors="replace")
        expected_site = re.search(
            r"SITE\s+CORE\b.*?SIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)\s*;", tech_text, re.S,
        )
        pitch = re.search(r"LAYER\s+metal1\b.*?PITCH\s+([0-9.]+)\s*;", tech_text, re.S)
        lef_errors = []
        if expected_site is None or abs(float(expected_site.group(1)) - geometry.site_width_um) > 1e-6:
            lef_errors.append("CORE site width was not patched to the profile value")
        if pitch is None or abs(float(pitch.group(1)) - geometry.metal1_pitch_um) > 1e-6:
            lef_errors.append("metal1 pitch was not patched to the profile value")
        if expected_site and abs(float(expected_site.group(2)) - geometry.site_height_um) > 1e-6:
            lef_errors.append("CORE site height does not match the profile")
        for cell, macro in sorted(lef.items()):
            if cell.startswith("FILLER"):
                continue
            if abs(macro.width_um / geometry.site_width_um - round(macro.width_um / geometry.site_width_um)) > 1e-6:
                lef_errors.append(f"{cell}: width {macro.width_um} is not a site multiple")
        if "MX201B" in lef and abs(lef["MX201B"].width_um - 50.4) > 1e-6:
            lef_errors.append(f"MX201B width is {lef['MX201B'].width_um}, expected 50.4")
        if summary.get("fixed_widths") and not any("MX201B" in row for row in summary["fixed_widths"]):
            lef_errors.append("summary does not record the MX201B width fix")

        excluded = set(profile.excluded_for("P01")) | set(profile.excluded_for("P02"))
        liberty_errors = sorted(excluded & set(lib.cells))
        if not lib.cells:
            liberty_errors.append("filtered Liberty has no cells")
        if not lib.sequential_cells():
            liberty_errors.append("filtered Liberty has no sequential cells")
        for cell in sorted(lib.cells):
            if cell not in lef:
                liberty_errors.append(f"{cell}: no patched LEF macro")
        # The shipped abstract library describes more macros than the Liberty
        # model: antenna/clock variants, extra drive strengths and fillers are
        # legitimate LEF-only entries.  Synthesis may only use Liberty cells, so
        # their presence is not an error; a missing LEF entry for a usable cell
        # still is.
        lef_only = sorted(cell for cell in lef if cell not in lib.cells)
        if not any(cell.startswith("FILLER") for cell in lef):
            liberty_errors.append("patched LEF has no filler macros")
        for name in (profile.synthesis.tie_high_cell, profile.synthesis.tie_low_cell, profile.synthesis.buffer_cell):
            if name not in lib.cells:
                liberty_errors.append(f"{name}: required synthesis cell missing")

        rows = [
            {"id": "technology.inputs", "status": "PASS",
             "message": f"{len(INPUT_COPIES)} PDK inputs copied with verified fingerprints"},
            {"id": "technology.lef", "status": "FAIL" if lef_errors else "PASS",
             "message": "; ".join(lef_errors) or "site, pitch and cell widths match the profile"},
            {"id": "technology.liberty", "status": "FAIL" if liberty_errors else "PASS",
             "message": "; ".join(liberty_errors) or f"{len(lib.cells)} cells, {len(lib.sequential_cells())} sequential"},
            *model_rows,
            *driver_rows,
        ]
        if any(row["status"] == "FAIL" for row in rows):
            status = CheckStatus.FAIL
        elif any(row["status"] == "WARN" for row in rows):
            status = CheckStatus.WARN
        else:
            status = CheckStatus.PASS

        cell_pin_centers = {
            cell: {pin: list(centers) for pin, centers in macro.pin_pins.items()}
            for cell, macro in sorted(lef.items())
        }
        technology = {
            "schema_version": 1,
            "profile": {"id": profile.profile, "version": profile.profile_version,
                        "sha256": profile.sha256, "frame": profile.frame},
            "python_packages": dict(toolchain.python_packages),
            "frame": summary.get("frame"),
            "pad_terminals_um": summary.get("pad_terminals_um"),
            "technology": {
                "site": geometry.site,
                "site_width_um": geometry.site_width_um,
                "site_height_um": geometry.site_height_um,
                "metal1_pitch_um": geometry.metal1_pitch_um,
                "metal2_pitch_um": geometry.metal2_pitch_um,
                "via": geometry.via,
            },
            "cells": {
                "count": len(lef),
                "allowed": sorted(lib.cells),
                "lef_only": lef_only,
                "sequential": sorted(lib.sequential_cells()),
                "lef_widths_um": {cell: macro.width_um for cell, macro in sorted(lef.items())},
                "lef_heights_um": {cell: macro.height_um for cell, macro in sorted(lef.items())},
                "lef_pin_centers_um": cell_pin_centers,
                "liberty_pins": {
                    cell: {
                        pin.name: {"direction": pin.direction, "function": pin.function,
                                   "capacitance_pf": pin.capacitance_pf,
                                   "max_capacitance_pf": pin.max_capacitance_pf}
                        for pin in cell_info.pins.values()
                    }
                    for cell, cell_info in sorted(lib.cells.items())
                },
            },
            "pad_models": pad_models,
            "drop_cells": list(profile.excluded_for("P01")),
            "force_cells": list(profile.excluded_for("P02")),
            "rows": rows,
        }
        tech_file = stage_dir / "technology.json"
        atomic_write_json(tech_file, technology)
        tech_artifact = Artifact(str(tech_file.relative_to(run_path)), sha256_file(tech_file))
        evidence = (*artifacts, tech_artifact)
        input_hashes = {"profile": profile_hash, "configuration": config_hash,
                        "inputs": sha256_file(run_path / "input-manifest.json")}
        checks = tuple(
            CheckResult(row["id"], CheckStatus(row["status"]),
                        message=row["message"], evidence=evidence, input_hashes=input_hashes)
            for row in rows
        )
        if status == CheckStatus.FAIL:
            failing = [row for row in rows if row["status"] == "FAIL"]
            return Assessment(
                checks=(CheckResult(failing[0]["id"], CheckStatus.FAIL,
                                    message=failing[0]["message"], evidence=evidence,
                                    input_hashes=input_hashes),),
                failure_kind="configuration",
                extra_artifacts=(tech_artifact,),
            )
        return Assessment(checks=checks, extra_artifacts=(tech_artifact,))

    return StageSpec(
        id="technology",
        command=(
            str(sys.executable), "-I", "tech_prep.py", "tech-input.json",
        ),
        timeout_s=float(max(project.resources.timeout_seconds, 600.0)),
        required_outputs=("tech.lef", "cells.lef", "tech-summary.json", *OUTPUT_LIBS),
        validators={name: validate_nonempty for name in ("tech.lef", "cells.lef", "tech-summary.json", *OUTPUT_LIBS)},
        prepare=prepare,
        assess=assess,
    )
