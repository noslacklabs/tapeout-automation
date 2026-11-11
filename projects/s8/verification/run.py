"""Run S8 downstream checks on an existing chipflow physical run, without rebuilding it.

This project-specific qualification runner uses fingerprinted reference helpers.
It does not implement generic milestone-4 adapters or claim silicon signoff.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/chipflow"))
sys.path.insert(0, str(ROOT / "scripts"))
from chipflow.config import load_project
from chipflow.runner.execution import atomic_write_json, run_command, sha256_file
from chipflow.toolchain import doctor, load_toolchain, verify_file
from m1_collect import drc_evidence, lvs_evidence, xunit_evidence, proof_evidence, extraction_evidence
from chipflow.stages.def_model import read_def


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timing_path_evidence(text, expected):
    """Require every requested path group and retain its worst reported slack."""
    parts = re.split(r"(?m)^### (.*)$", text)
    sections = {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    require(set(expected) <= set(sections), f"missing STA path sections {set(expected) - set(sections)}")
    result = {}
    for name in sorted(expected):
        slacks = [float(s) for s in re.findall(r"(?m)^\s*(-?[\d.]+)\s+slack", sections[name])]
        require(slacks, f"no constrained path for {name}")
        result[name] = {"slack_ns": min(slacks), "reported_paths": len(slacks)}
    return result


def spef_coverage(spef, def_file):
    """Independently reconcile extracted nets and every connection with DEF."""
    text = Path(spef).read_text()
    unit_rows = re.findall(r"(?m)^\*([TCRL]_UNIT)\s+(\S+\s+\S+)\s*$", text)
    require(len(unit_rows) == 4, "missing or duplicate SPEF units")
    units = dict(unit_rows)
    require(units == {"T_UNIT": "1 NS", "C_UNIT": "1 PF", "R_UNIT": "1 OHM", "L_UNIT": "1 HENRY"},
            f"unsupported SPEF units {units}")
    names = dict(re.findall(r"(?m)^(\*\d+)\s+(\S+)\s*$", text))
    def expand(value):
        ref, separator, suffix = value.partition(":")
        return names.get(ref, ref).replace("\\", "") + (":" + suffix if separator else "")
    model = read_def(def_file, require_pins=True)
    expected = {w.net for w in model.wires} | {v.net for v in model.vias}
    connections = {n.name: set(n.connections) for n in model.nets}
    observed = {}
    for block in re.finditer(r"(?ms)^\*D_NET\s+(\S+)\s+\S+\s*\n(.*?)^\*END\s*$", text):
        name = expand(block[1])
        require(name not in observed, f"duplicate SPEF net {name}")
        part = re.search(r"(?ms)^\*CONN\s*\n(.*?)^\*CAP", block[2])
        require(part is not None, f"missing SPEF connection section {name}")
        endpoints = set()
        for kind, value in re.findall(r"(?m)^\*(I|P)\s+(\S+)\s+", part[1]):
            decoded = expand(value)
            endpoint = tuple(decoded.rsplit(":", 1)) if kind == "I" else ("PIN", decoded)
            require(len(endpoint) == 2 and endpoint not in endpoints, f"duplicate/invalid SPEF pin {decoded}")
            endpoints.add(endpoint)
        observed[name] = endpoints
    require(set(observed) == expected, f"SPEF/DEF routed net mismatch: missing {expected - set(observed)}, extra {set(observed) - expected}")
    for name, endpoints in observed.items():
        require(endpoints == connections[name], f"SPEF pin coverage differs on {name}: {endpoints ^ connections[name]}")
    excluded = {name: sorted(pins) for name, pins in connections.items() if name not in expected}
    require(all(len(pins) <= 1 for pins in excluded.values()), "unextracted multi-pin DEF net")
    return {"units": units, "extracted_nets": len(observed),
            "matched_pin_connections": sum(len(p) for p in observed.values()),
            "excluded_unrouted_single_pin_nets": excluded}


class Verification:
    def __init__(self, state_path):
        self.state_path = Path(state_path)
        self.state = read(state_path)
        self.out = self.state_path.parent
        self.runtime = Path(self.state["runtime"])
        # WSL's temporary aliases can disappear between tool sessions. All
        # durable data lives inside this run; only the short alias is rebuilt.
        require(self.runtime.parent == Path("/tmp") and self.runtime.name.startswith("chipflow-s8-check-"),
                "unexpected runtime alias")
        if not self.runtime.exists():
            target = self.out / "runtime"
            require(target.is_dir(), "durable verification runtime is missing")
            self.runtime.symlink_to(target, target_is_directory=True)
        self.source = self.runtime / "source"
        self.build = self.runtime / "build"
        self.toolchain = load_toolchain(self.state["toolchain"])
        self.checks = self.state.setdefault("checks", {})
        self.commands = self.state.setdefault("commands", {})
        self.lock = threading.RLock()
        self.env = {"CHIPFLOW_RUNTIME_ROOT": str(self.runtime), "PYTHONDONTWRITEBYTECODE": "1",
                    "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

    def save(self):
        with self.lock:
            atomic_write_json(self.state_path, self.state)

    def command(self, name, argv, *, timeout=600, extra=None):
        with self.lock:
            attempts = self.commands.setdefault(name, [])
            directory = self.out / "commands" / f"{name}-{len(attempts) + 1}"
            directory.mkdir(parents=True)
        result = run_command([str(a) for a in argv], directory, timeout,
                             {**self.env, **(extra or {})})
        with self.lock:
            attempts.append(result.to_dict())
        # Concurrent callers update distinct names; the parent saves after joins.
        return result

    def checked_command(self, name, argv, **kwargs):
        result = self.command(name, argv, **kwargs)
        self.save()
        require(result.succeeded, f"{name}: execution failed: {result.returncode}, {result.error}")
        return result

    def record(self, name, status, detail, evidence=()):
        self.checks[name] = {"status": status, "detail": detail,
                             "evidence": [str(p) for p in evidence]}
        self.save()
        print(f"{name}: {status}: {str(detail)[:220]}", flush=True)

    def checkpoint(self):
        # No links to home directories or virtualenvs are followed or copied.
        for name, path in (("build", self.build),
                           ("sim", self.runtime / "silicon/builds/chip-cpu/sim")):
            if path.is_dir():
                shutil.copytree(path, self.out / name, dirs_exist_ok=True)
        self.save()

    @classmethod
    def prepare(cls, args):
        physical = args.physical_run.resolve(strict=True)
        result = read(physical / "result.json")
        require(result["schema_version"] == 1, "unsupported physical result")
        stages = {s["id"]: s for s in result["stages"]}
        for name in ("technology", "synthesis", "pnr", "assembly"):
            require(stages[name]["status"] == "succeeded", f"physical stage {name} did not succeed")
        hashes = {a["path"]: a["sha256"] for s in result["stages"] for a in s["artifacts"]}
        for relative, expected in hashes.items():
            path = (physical / relative).resolve(strict=True)
            require(path.is_relative_to(physical), "physical artifact escapes run")
            verify_file(path, expected)
        manifest = read(physical / "input-manifest.json")
        for row in manifest:
            verify_file(physical / row["snapshot"], row["sha256"])
        project = load_project(physical / "inputs/project/chip.toml")
        require(project.name == "s8" and project.top == "s8" and project.top_cell == "OS0000",
                "this runner supports the captured S8 / OS0000 project")
        require(project.clock.period_ns == 100.0 and project.clock.waveform_ns == (0.0, 50.0)
                and all(p.min_ns == 0 and p.max_ns == 0 for p in (*project.io.inputs, *project.io.outputs))
                and all(p.load_pf == 30.0 for p in project.io.outputs),
                "S8 functional/RAM harness is qualified for the captured 100 ns, 50% clock and legacy I/O assumptions only")
        toolchain = load_toolchain(args.toolchain)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        out = (args.output or ROOT / "work/s8-downstream" / f"run-{stamp}").resolve()
        out.mkdir(parents=True, exist_ok=False)
        durable = out / "runtime"
        durable.mkdir()
        runtime = Path("/tmp") / ("chipflow-s8-check-" + uuid.uuid4().hex[:12])
        runtime.symlink_to(durable, target_is_directory=True)
        source = runtime / "source"
        baseline = read(ROOT / "reference/milestone1/baseline.json")
        frozen = Path(baseline["run"]) / "source"
        expected_sources = {r["relative_path"]: r["sha256"]
                            for r in read(ROOT / "reference/milestone1/source-inputs.json")
                            if not r["relative_path"].startswith("external/")}
        inputs = []
        for item in sorted(frozen.rglob("*")):
            if not item.is_file():
                continue
            relative = item.relative_to(frozen)
            require(relative.as_posix() in expected_sources, f"unbound reference source {relative}")
            verify_file(item, expected_sources[relative.as_posix()])
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = item.read_bytes()
            text = data.decode("utf-8") if item.suffix == ".py" else None
            changed = text
            if text is not None and "pathlib.Path.home()" in text:
                changed = text.replace("pathlib.Path.home()", "pathlib.Path(os.environ['CHIPFLOW_RUNTIME_ROOT'])")
                module = ast.parse(changed)
                insertion = 0
                for node in module.body:
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        insertion = node.end_lineno
                    elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
                        insertion = node.end_lineno
                    else:
                        break
                lines = changed.splitlines(keepends=True)
                lines.insert(insertion, "import os\n")
                changed = "".join(lines)
                if relative.as_posix() == "tb/run_ram_model.py":
                    changed = re.sub(r"\bHOME\b", "DUT_ROOT", changed)
                    # Verilog filename registers are 128 bytes. Keep the short
                    # no-space runtime alias instead of resolving the symlink.
                    require(changed.count("B = args.build_dir.resolve()") == 1,
                            "unexpected RAM build-directory handling")
                    changed = changed.replace("B = args.build_dir.resolve()", "B = args.build_dir.absolute()")
            if changed is None:
                destination.write_bytes(data)
            else:
                destination.write_text(changed, encoding="utf-8")
            inputs.append({"original": str(item), "source_sha256": sha256_file(item),
                           "staged": str(destination), "staged_sha256": sha256_file(destination),
                           "transform": ("explicit-runtime-root-short-ram-path-v2" if relative.as_posix() == "tb/run_ram_model.py"
                                         else "explicit-runtime-root-v1") if changed != text else "none"})
        verify_file(source / "rtl/s8.v", sha256_file(physical / "inputs/project/rtl/s8.v"))
        build = runtime / "build"
        mapping = {
            "artifacts/chip.gds": "chip/OS0000.gds", "artifacts/chip.cdl": "chip/OS0000.cdl",
            "artifacts/mapped.v": "synth/mapped.v",
            **{f"stages/pnr/{n}": f"pnr/{n}" for n in ("routed.def", "routed.v", "routed.odb", "floorplan.json", "overlaps.json", "route.log")},
            **{f"stages/assembly/{n}": f"chip/{n}" for n in ("assemble.json", "power_connect.json")},
            **{f"stages/technology/{n}": f"tech/{n}" for n in
               ("tech.lef", "cells.lef", "core_typ.lib", "core_min.lib", "core_max.lib",
                "pads_typ.lib", "pads_min.lib", "pads_max.lib")},
        }
        for relative, target in mapping.items():
            src = physical / relative
            # Diagnostic route logs/overlaps are identified by their originating
            # stage input/output manifest; all release and implementation data
            # must have an emitted artifact hash.
            if relative not in hashes:
                require(relative.endswith(("/route.log", "/overlaps.json")), f"unrecorded producer input {relative}")
            else:
                verify_file(src, hashes[relative])
            destination = build / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, destination)
            inputs.append({"original": str(src), "source_sha256": sha256_file(src),
                           "staged": str(destination), "staged_sha256": sha256_file(destination), "transform": "none"})
        shutil.copyfile(source / "rtl/s8.v", build / "synth/s8.v")
        extra_files = {"io_lef": build / "tech/io.lef", "rcx_rules": build / "sta/rcx.lib",
                       "cells_verilog": runtime / "tool-eval/data/digital_c1d/verilog/c1d.v"}
        for name, destination in extra_files.items():
            item = toolchain.pdk_file(name)
            verify_file(item.path, item.sha256)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item.path, destination)
            inputs.append({"original": str(item.path), "source_sha256": item.sha256,
                           "staged": str(destination), "staged_sha256": sha256_file(destination), "transform": "none"})
        external = read(ROOT / "reference/s8/dependencies.json")["external_models"]
        ram = next(m for m in external if m["id"] == "microchip.23LC512")
        model = Path(ram["observed_path"])
        verify_file(model, ram["observed_sha256"])
        destination = runtime / "silicon/builds/chip-cpu/ram-model/v/23LC512.v"
        destination.parent.mkdir(parents=True)
        shutil.copyfile(model, destination)
        inputs.append({"original": str(model), "source_sha256": sha256_file(model),
                       "staged": str(destination), "staged_sha256": sha256_file(destination), "transform": "none"})
        shutil.copyfile(physical / "resolved-project.json", out / "resolved-project.json")
        shutil.copyfile(physical / "result.json", out / "physical-result.json")
        shutil.copyfile(physical / "input-manifest.json", out / "physical-input-manifest.json")
        shutil.copyfile(physical / "pad-plan.json", out / "pad-plan.json")
        shutil.copytree(source, out / "verification-source")
        atomic_write_json(out / "verification-inputs.json", inputs)
        old_state = read(Path(baseline["run"]) / "state.json")
        state = {"schema_version": 1, "physical_run": str(physical), "physical_result_sha256": sha256_file(physical / "result.json"),
                 "runtime": str(runtime), "toolchain": str(toolchain.path), "inputs": inputs,
                 "source_sha256": sha256_file(Path(__file__)), "created_at": datetime.now(timezone.utc).isoformat(),
                 "ngspice": old_state["ngspice"], "ngspice_sha256": old_state["ngspice_sha256"]}
        # The captured simulation environment is a separate, already-installed venv.
        state["simulation_python"] = "/home/sushant/tool-eval/venv/bin/python"
        atomic_write_json(out / "state.json", state)
        print(f"Verification run: {out}", flush=True)
        return cls(out / "state.json")

    def layout(self):
        report = doctor(self.toolchain, self.out / "doctor")
        require(report["exit_code"] == 0, "pinned environment doctor failed")
        self.record("toolchain", "PASS", "pinned executables, Python bindings and PDK fingerprints verified",
                    [self.out / "doctor/doctor.json"])
        chip = self.build / "chip"
        top = "OS0000"
        baseline = read(ROOT / "reference/milestone1/baseline.json")
        reference_drc = Path(baseline["run"]) / "artifacts/build/chip/OS0000_drc.txt"
        expected = {c.findtext("name") for c in ET.parse(reference_drc).getroot().findall("./categories//category")}
        require(len(expected) == 72, "expected DRC coverage must have 72 categories")
        klayout = self.toolchain.tool("klayout").executable
        for name, flat in (("drc", False), ("flat_drc", True)):
            work = chip / name
            work.mkdir()
            deck = self.toolchain.pdk_file("drc_deck")
            verify_file(deck.path, deck.sha256)
            text = deck.path.read_text()
            if flat:
                text, count = re.subn(r"(?m)^deep\s*$", "flat", text)
                require(count == 1, "flat-deck transform did not replace exactly one deep mode")
            (work / "deck.drc").write_text(text, encoding="utf-8")
            self.checked_command(name, [klayout, "-b", "-rd", f"input={chip / (top + '.gds')}",
                "-rd", f"top_cell={top}", "-rd", f"report1={work / 'report.xml'}", "-r", work / "deck.drc"])
            evidence = drc_evidence(work / "report.xml", expected)
            self.record(name, "PASS", evidence, [work / "report.xml", work / "deck.drc"])
        work = chip / "lvs"
        work.mkdir()
        deck = self.toolchain.pdk_file("lvs_deck")
        verify_file(deck.path, deck.sha256)
        shutil.copyfile(deck.path, work / "deck.lvs")
        self.checked_command("lvs", [klayout, "-b", "-rd", f"input={chip / (top + '.gds')}",
            "-rd", f"top_cell={top}", "-rd", f"schematic1={chip / (top + '.cdl')}",
            "-rd", f"target_netlist1={work / 'extracted.cir'}", "-rd", f"report={work / 'report.lvsdb'}",
            "-r", work / "deck.lvs"])
        self.record("lvs", "PASS", lvs_evidence(work / "report.lvsdb"), [work / "report.lvsdb"])
        name = chip / "OS0000_S8_08112026_v0.gds"
        shutil.copyfile(chip / "OS0000.gds", name)
        named_drc = name.with_name(name.stem + "_drc.txt")
        shutil.copyfile(chip / "drc/report.xml", named_drc)
        self.checked_command("presubmit", [sys.executable, "-m", "presubmit_check", name,
            "--die", "2", "--silicon", top, "--pdk-root", self.toolchain.pdk_root,
            "--drc-report", named_drc, "--json", chip / "presubmit.json"])
        presubmit = read(chip / "presubmit.json")
        require(not presubmit["tool_errors"] and presubmit["summary"].get("FAIL", 0) == 0,
                "presubmit failed or reported a tool error")
        self.record("presubmit", "WARN", presubmit["summary"], [chip / "presubmit.json"])
        self.checked_command("geometry", [sys.executable, "-I", self.source / "flow/checks/own_checks.py",
            chip / "OS0000.gds", top, chip / "own_checks.json", self.build / "tech/cells.lef"])
        geometry = read(chip / "own_checks.json")
        keys = ("routed_vias_over_poly", "routed_vias_over_contact", "drawn_shapes_off_grid",
                "M1_wider_than_25um", "M2_wider_than_25um", "M1_space_with_notches", "M2_space_with_notches")
        require(geometry["top_cells"] == [top] and geometry["bbox"] == "(0,0;2000,2000)"
                and geometry["routed_vias"] > 0 and all(geometry[k] == 0 for k in keys), "geometry invariant failed")
        self.record("geometry", "PASS", geometry, [chip / "own_checks.json"])
        self.checked_command("negative_controls", [sys.executable, "-I", self.source / "flow/checks/negative_tests.py",
            chip / "OS0000.gds", top, chip / "OS0000.cdl", chip / "negative", klayout,
            self.toolchain.pdk_file("drc_deck").path, self.toolchain.pdk_file("lvs_deck").path,
            chip / "negative.json"], timeout=900)
        negative = read(chip / "negative.json")
        require(len(negative) == 11 and all(r["ok"] for r in negative), "negative-control matrix incomplete")
        self.record("negative_controls", "PASS", negative, [chip / "negative.json"])
        self.checkpoint()

    def timing(self):
        sta = self.build / "sta"
        pnr = self.build / "pnr"
        tech = self.build / "tech"
        project = load_project(Path(self.state["physical_run"]) / "inputs/project/chip.toml")
        fp = read(pnr / "floorplan.json")
        pads = {p["port"]: f"PAD{p['pad']}" for p in fp["pads"]}
        clock = pads[project.clock.port]
        self.checked_command("chip-wrapper", [sys.executable, "-I", self.source / "flow/sta/gen_chip.py",
            pnr / "floorplan.json", project.top, sta / "chip_top.v", sta / "shim.v"])
        self.checked_command("sck-gate", [sys.executable, "-I", self.source / "flow/checks/sck_gate.py",
            pnr / "routed.v", tech / "core_typ.lib", "sck_b", project.clock.port, sta / "sck_gate.json"])
        gate_report = read(sta / "sck_gate.json")
        require(not gate_report["problems"] and gate_report["gate"], "S8 clock-output gate check failed")
        gate = gate_report["gate"]
        openroad = self.toolchain.tool("openroad").executable
        self.checked_command("raw-extraction", [openroad, "-no_init", "-exit", self.source / "flow/sta/extract.tcl"],
            extra={"ODB": str(pnr / "routed.odb"), "RCX": str(sta / "rcx.lib"), "SPEF": str(sta / "core.spef")})
        self.checked_command("diagnostic-estimate", [sys.executable, "-I", self.source / "flow/sta/scale_spef.py",
            pnr / "routed.def", sta / "core.spef", sta / "core_estimated.spef", sta / "wire_cap.json"])
        parasitics = extraction_evidence(read(sta / "wire_cap.json"), (sta / "core.spef").read_text(),
                                        {"raw_extraction_ratio_min": 100.0, "raw_extraction_ratio_max": 1000.0})
        self.record("parasitic_artifact", "PASS", "raw OpenRCX execution and nonempty SPEF", [sta / "core.spef"])
        self.record("parasitic_units", "PASS", "raw SPEF capacitance unit 1 PF verified", [sta / "core.spef"])
        self.record("parasitic_coverage", "PASS", parasitics["matched_nets"], [sta / "wire_cap.json"])
        self.record("parasitic_validation", "FAIL", parasitics, [sta / "core.spef", sta / "wire_cap.json"])

        lines = [f"create_clock -name {project.clock.port} -period {project.clock.period_ns:g} "
                 f"-waveform {{{project.clock.waveform_ns[0]:g} {project.clock.waveform_ns[1]:g}}} [get_ports {clock}]",
                 f"set_clock_uncertainty {project.clock.uncertainty_ns:g} [get_clocks {project.clock.port}]",
                 f"set_propagated_clock [get_clocks {project.clock.port}]",
                 f"set_input_transition {project.clock.transition_ns:g} [get_ports {clock}]"]
        for delay in project.io.inputs:
            port = pads[delay.port]
            lines += [f"set_input_delay -min {delay.min_ns:g} -clock {project.clock.port} [get_ports {port}]",
                      f"set_input_delay -max {delay.max_ns:g} -clock {project.clock.port} [get_ports {port}]",
                      f"set_input_transition {project.clock.transition_ns:g} [get_ports {port}]"]
        for delay in project.io.outputs:
            port = pads[delay.port]
            lines += [f"set_output_delay -min {delay.min_ns:g} -clock {project.clock.port} [get_ports {port}]",
                      f"set_output_delay -max {delay.max_ns:g} -clock {project.clock.port} [get_ports {port}]",
                      f"set_load {delay.load_pf:g} [get_ports {port}]"]
        (sta / "chip.sdc").write_text("\n".join(lines) + "\n", encoding="utf-8")
        tcl = (self.source / "flow/sta/sta.tcl").read_text()
        start = tcl.index("set period $::env(PERIOD)")
        end = tcl.index('puts "### CORNER', start)
        tcl = tcl[:start] + "read_sdc $::env(SDC)\nwrite_sdc $::env(APPLIED_SDC)\n" + tcl[end:]
        # These counts and constraints are queried after linking/SDC, rather
        # than inferred from a successful tool process or empty timing report.
        tcl += """
puts "### COVERAGE"
puts "CLOCKS [llength [all_clocks]]"
puts "REGISTERS [llength [all_registers]]"
puts "SEQUENTIAL_CLOCK_PINS [llength [all_registers -clock_pins]]"
puts "INPUT_PORTS [llength [all_inputs]]"
puts "OUTPUT_PORTS [llength [all_outputs]]"
check_setup -verbose
puts "### COVERAGE_END"
"""
        (sta / "sta.tcl").write_text(tcl, encoding="utf-8")
        expected_flops = read(Path(self.state["physical_run"]) / "stages/synthesis/synthesis.json")["connectivity"]["sequential_cells"]
        expected_sections = {"UNITS", "MINPERIOD", "SKEW", "WNS", "SETUP", "HOLD", "LIMITS", "PULSE", "END"}
        expected_sections |= {f"OUT {pads[p.port]} {kind}" for p in project.io.outputs for kind in ("MAX", "MIN")}
        expected_sections |= {f"IN {pads[p.port]} {kind}" for p in project.io.inputs for kind in ("MAX", "MIN")}
        rows = []
        for corner in ("min", "typ", "max"):
            result = self.checked_command("sta-" + corner, [openroad, "-no_init", "-exit", sta / "sta.tcl"],
                extra={"T": str(tech), "CORNER": corner, "CHIP": str(sta / "chip_top.v"),
                       "CORE": str(pnr / "routed.v"), "SPEF": str(sta / "core_estimated.spef"),
                       "SDC": str(sta / "chip.sdc"), "APPLIED_SDC": str(sta / f"applied_{corner}.sdc"),
                       "CLKPAD": clock, "SCK_PAD": pads["sck"], "SDF": str(sta / f"chip_{corner}.sdf"),
                       "INS": " ".join(pads[p.port] for p in project.io.inputs) + " " + clock,
                       "OUTS": " ".join(pads[p.port] for p in project.io.outputs),
                       "GATE_CLK": f"u_core/{gate['name']}/{gate['clk_pin']}",
                       "GATE_IDLE": f"u_core/{gate['name']}/{gate['idle_pin']}"})
            text = Path(result.stdout_log).read_text()
            require(not re.search(r"(?m)^\[ERROR|^Error", text), f"{corner}: STA tool error")
            parts = re.split(r"(?m)^### (.*)$", text)
            sections = {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
            require(expected_sections <= set(sections), f"{corner}: missing STA sections {expected_sections - set(sections)}")
            coverage = {k: int(v) for k, v in re.findall(r"(?m)^(CLOCKS|REGISTERS|SEQUENTIAL_CLOCK_PINS|INPUT_PORTS|OUTPUT_PORTS) (\d+)$", sections.get("COVERAGE", ""))}
            require(coverage == {"CLOCKS": 1, "REGISTERS": expected_flops, "SEQUENTIAL_CLOCK_PINS": expected_flops,
                                "INPUT_PORTS": len(project.io.inputs) + 1, "OUTPUT_PORTS": len(project.io.outputs)},
                    f"{corner}: incomplete sequential/port coverage: {coverage}")
            setup = re.search(r"(?m)^\s*(-?[\d.]+)\s+slack", sections["SETUP"])
            hold = re.search(r"(?m)^\s*(-?[\d.]+)\s+slack", sections["HOLD"])
            require(setup and hold, f"{corner}: missing setup/hold paths")
            for key in expected_sections:
                if key.startswith(("IN ", "OUT ")):
                    require(re.search(r"(?m)^\s*(-?[\d.]+)\s+slack", sections[key]), f"{corner}: no constrained path for {key}")
            limits = [line.strip() for line in sections["LIMITS"].splitlines() if "VIOLATED" in line]
            core_limits = [line for line in limits if not re.match(r"xpad\d+/PAD\b", line)]
            pad_limits = [line for line in limits if line not in core_limits]
            pulse = [line.strip() for line in sections["PULSE"].splitlines() if "VIOLATED" in line]
            io = timing_path_evidence(text, {key for key in expected_sections if key.startswith(("IN ", "OUT "))})
            rows.append({"corner": corner, "coverage": coverage, "setup_slack_ns": float(setup[1]),
                         "hold_slack_ns": float(hold[1]), "core_limit_violations": core_limits,
                         "pad_limit_violations": pad_limits, "pulse_violations": pulse,
                         "core_setup_slack_ns": float(setup[1]), "core_hold_slack_ns": float(hold[1]),
                         "io_paths": io,
                         "timing_parasitics": "diagnostic routed-length/LEF estimate, electrically unvalidated"})
            rows[-1]["setup_slack_ns"] = min(float(setup[1]), *(r["slack_ns"] for k, r in io.items() if k.endswith(" MAX")))
            rows[-1]["hold_slack_ns"] = min(float(hold[1]), *(r["slack_ns"] for k, r in io.items() if k.endswith(" MIN")))
            shutil.copyfile(result.stdout_log, sta / f"sta_{corner}.log")
            require((sta / f"chip_{corner}.sdf").stat().st_size > 0, "missing SDF")
        atomic_write_json(sta / "timing.json", rows)
        self.record("timing_coverage", "PASS", rows, [sta / "timing.json", sta / "chip.sdc"])
        self.record("timing_setup", "PASS" if all(r["setup_slack_ns"] >= 0 for r in rows) else "FAIL",
                    rows, [sta / "timing.json"])
        self.record("timing_hold", "PASS" if all(r["hold_slack_ns"] >= 0 for r in rows) else "FAIL",
                    rows, [sta / "timing.json"])
        limit_status = "FAIL" if any(r["core_limit_violations"] or r["pulse_violations"] for r in rows) else (
            "WARN" if any(r["pad_limit_violations"] for r in rows) else "PASS")
        self.record("timing_limits", limit_status,
                    rows, [sta / "timing.json"])
        self.record("pad_model_limits", "MANUAL", "Pad slew/current/load interpretations remain unresolved; not signoff qualified",
                    [sta / "timing.json"])
        self.checkpoint()


    def simulation_jobs(self, suffix=""):
        """Set up strict process auditing and fresh affected simulation outputs."""
        build, source = self.build, self.source
        ram_source = source / "tb/run_ram_model.py"
        text = ram_source.read_text()
        if "B = args.build_dir.resolve()" in text:
            before = sha256_file(ram_source)
            revision = self.out / "source-revisions" / (before + "-run_ram_model.py")
            revision.parent.mkdir(exist_ok=True)
            shutil.copyfile(ram_source, revision)
            text = text.replace("B = args.build_dir.resolve()", "B = args.build_dir.absolute()")
            ram_source.write_text(text, encoding="utf-8")
            row = next(r for r in self.state["inputs"] if r["staged"] == str(ram_source))
            row["staged_sha256"] = sha256_file(ram_source)
            row["transform"] = "explicit-runtime-root-short-ram-path-v2"
            self.state.setdefault("source_revisions", []).append({
                "path": str(ram_source), "before_sha256": before,
                "after_sha256": sha256_file(ram_source), "previous_copy": str(revision),
                "reason": "Preserve short alias for 128-byte Verilog filename registers"})
            atomic_write_json(self.out / "verification-inputs.json", self.state["inputs"])
        require("B = args.build_dir.absolute()" in ram_source.read_text(), "RAM short-path transform is missing")
        binaries = self.runtime / "audited-bin"
        binaries.mkdir(exist_ok=True)
        helper = Path(__file__).with_name("process_audit.py")
        for name in ("vvp", "iverilog"):
            target = binaries / name
            shutil.copyfile(helper, target)
            target.chmod(0o755)
        self.state["process_audit_helper"] = {"source": str(helper), "sha256": sha256_file(helper),
                                               "staged": [str(binaries / n) for n in ("vvp", "iverilog")]}
        ram_audit = build / ("ram-process-audit" + suffix + ".jsonl")
        seeded_audit = build / ("seeded-process-audit" + suffix + ".jsonl")
        seeded = build / ("seeded-sdf" + suffix)
        self.state.setdefault("products", {}).update(seeded_sdf=str(seeded), ram_process_audit=str(ram_audit),
                                                     seeded_process_audit=str(seeded_audit))
        self.save()
        def env(audit):
            return {"PATH": str(binaries) + ":" + self.env["PATH"], "CHIPFLOW_PROCESS_AUDIT": str(audit)}
        python = self.state["simulation_python"]
        return [
            ("seeded-sdf", [python, source / "tb/routed_random.py", build, "--output", seeded,
                "--seeds", "1", "2", "3", "4", "5", "--programs", "50", "--instr", "100", "--jobs", "3"],
                1200, env(seeded_audit)),
            ("ram-positive", [python, source / "tb/run_ram_model.py", "--routed", build,
                "--build-dir", build / ("ram-positive" + suffix)], 900, env(ram_audit)),
            ("ram-negative", [python, source / "tb/run_ram_model.py", "--period", "40",
                "--build-dir", build / ("ram-negative" + suffix)], 300, env(ram_audit)),
        ]

    def execute_jobs(self, jobs):
        def execute(job):
            name, argv, timeout, extra = job
            result = self.command(name, argv, timeout=timeout, extra=extra)
            print(f"{name}: process exit {result.returncode}, timeout={result.timed_out}", flush=True)
            return name, result.to_dict()
        with ThreadPoolExecutor(max_workers=3) as pool:
            for name, result in pool.map(execute, jobs):
                self.state.setdefault("regression_results", {})[name] = result
                self.save()
        self.checkpoint()

    def retry_simulation_io(self):
        suffix = "-io-fixed-" + str(len(self.commands.get("seeded-sdf", [])) + 1)
        self.execute_jobs(self.simulation_jobs(suffix))

    def regressions(self):
        build, source = self.build, self.source
        python = self.state["simulation_python"]
        gate = self.runtime / "gate"
        gate.mkdir(exist_ok=True)
        shutil.copyfile(build / "tech/core_typ.lib", gate / "du.lib")
        for name, netlist in (("routed", build / "pnr/routed.v"), ("mapped", build / "synth/mapped.v")):
            (build / f"{name}-with-shim.v").write_bytes(netlist.read_bytes() + (build / "sta/shim.v").read_bytes())
        self.checked_command("startup-models", [sys.executable, "-c",
            "import sys; from pathlib import Path; sys.path.insert(0, " + repr(str(source / "tb")) + "); "
            "from routed_util import random_start_cells; "
            + "; ".join(f"random_start_cells({seed}, Path({str(build / ('mapped-seed-' + str(seed)))!r}))" for seed in range(1, 6))])
        jobs = self.simulation_jobs() + [
            ("clock-trace", [sys.executable, source / "tb/check_routed_clocks.py", build / "pnr/routed.v"], 60, {}),
            ("routed-equivalence", ["bash", source / "formal/lec.sh"], 1500,
             {"NET": str(build / "routed-with-shim.v"), "L": str(build / "lec"), "G": str(gate), "LIMIT": "1200"}),
            ("rtl-tests", [python, source / "tb/run.py", "--tag", "downstream-rtl"], 900, {}),
            ("routed-zero", [python, source / "tb/run.py", "--netlist", build / "routed-with-shim.v",
                "--lib", build / "mapped-seed-1/c1d_random_start.v", "--tag", "routed-zero"], 900, {}),
            ("sdf-probe", [python, source / "tb/check_random_start_sdf.py", build, build / "sdf-probe"], 180, {}),
            ("cpu-formal", ["bash", source / "formal/run.sh"], 1500,
             {"W": str(build / "cpu-formal"), "LIMIT": "180"}),
        ]
        for corner in ("min", "typ", "max"):
            jobs.append(("routed-" + corner, [python, source / "tb/run.py", "--routed", build,
                "--corner", corner, "--tag", "downstream-" + corner], 900, {}))
        for seed in range(1, 6):
            jobs.append(("mapped-" + str(seed), [python, source / "tb/run.py", "--netlist", build / "mapped-with-shim.v",
                "--lib", build / f"mapped-seed-{seed}/c1d_random_start.v", "--seed", str(seed),
                "--tag", "mapped-" + str(seed), "--programs", "50", "--instr", "100"], 900, {}))
        verify_file(Path(self.state["ngspice"]), self.state["ngspice_sha256"])
        model_directories = list(self.toolchain.pdk_root.glob("Analog_C1D/**/libs.tech/ngpsice/model/hspice/models49"))
        require(len(model_directories) == 1, "expected one analog model directory")
        jobs.append(("pad-polarity", [sys.executable, source / "flow/libcheck/pad_polarity.py",
            self.toolchain.pdk_file("frame_io_cdl").path, self.toolchain.pdk_file("core_cdl").path,
            model_directories[0], build / "pad-polarity", self.state["ngspice"]], 300, {}))
        self.execute_jobs(jobs)


    def collect(self):
        infrastructure = []
        def last(name, expected=0):
            require(name in self.commands and self.commands[name], f"missing command {name}")
            row = self.commands[name][-1]
            require(row["returncode"] == expected and not any(row[k] for k in ("error", "timed_out", "cancelled")),
                    f"unexpected command outcome {name}: {row['returncode']}")
            return row
        def assess(name, callback, evidence=(), command=None, expected=0):
            try:
                if command:
                    last(command, expected)
                value = callback()
                self.record(name, "PASS", value, evidence)
            except Exception as error:
                infrastructure.append({"check": name, "error": f"{type(error).__name__}: {error}"})
                self.record(name, "NOT RUN" if isinstance(error, FileNotFoundError) else "FAIL",
                            f"{type(error).__name__}: {error}", evidence)
        build = self.build
        sim = self.runtime / "silicon/builds/chip-cpu/sim"
        physical = Path(self.state["physical_run"])
        expected_flops = read(physical / "stages/synthesis/synthesis.json")["connectivity"]["sequential_cells"]
        coverage_file = build / "sta/parasitic-coverage.json"
        def coverage():
            value = spef_coverage(build / "sta/core.spef", build / "pnr/routed.def")
            atomic_write_json(coverage_file, value)
            return value
        assess("parasitic_coverage", coverage, [coverage_file])
        assess("parasitic_units", lambda: spef_coverage(build / "sta/core.spef", build / "pnr/routed.def")["units"],
               [build / "sta/core.spef"])
        timing = read(build / "sta/timing.json")
        # Re-evaluate every board input/output path, including already executed
        # phases. Register-only slack must never qualify full-chip timing.
        project = load_project(physical / "inputs/project/chip.toml")
        port_to_pad = {p["port"]: f"PAD{p['pad']}" for p in read(build / "pnr/floorplan.json")["pads"]}
        io_sections = {f"{direction} {port_to_pad[p.port]} {kind}"
                       for direction, ports in (("IN", project.io.inputs), ("OUT", project.io.outputs))
                       for p in ports for kind in ("MIN", "MAX")}
        for row in timing:
            text = (build / f"sta/sta_{row['corner']}.log").read_text()
            row["io_paths"] = timing_path_evidence(text, io_sections)
            row.setdefault("core_setup_slack_ns", row["setup_slack_ns"])
            row.setdefault("core_hold_slack_ns", row["hold_slack_ns"])
            row["setup_slack_ns"] = min(row["core_setup_slack_ns"],
                *(r["slack_ns"] for k, r in row["io_paths"].items() if k.endswith(" MAX")))
            row["hold_slack_ns"] = min(row["core_hold_slack_ns"],
                *(r["slack_ns"] for k, r in row["io_paths"].items() if k.endswith(" MIN")))
        atomic_write_json(build / "sta/timing.json", timing)
        self.record("timing_coverage", "PASS", timing, [build / "sta/timing.json", build / "sta/chip.sdc"])
        for check, field in (("timing_setup", "setup_slack_ns"), ("timing_hold", "hold_slack_ns")):
            self.record(check, "PASS" if all(r[field] >= 0 for r in timing) else "FAIL",
                        timing, [build / "sta/timing.json"])
        core_failures = any(r["core_limit_violations"] or r["pulse_violations"] for r in timing)
        pad_warnings = sum(len(r["pad_limit_violations"]) for r in timing)
        self.record("timing_limits", "FAIL" if core_failures else "WARN" if pad_warnings else "PASS",
                    {"corners": timing, "modelled_pad_slew_violations": pad_warnings,
                     "interpretation": "Core/pulse limits checked; pad-limit interpretation is an unresolved signoff blocker"},
                    [build / "sta/timing.json"])

        def clock_trace():
            row = last("clock-trace")
            text = Path(row["stdout_log"]).read_text()
            match = re.search(r"flip-flops (\d+), reaching clk through clock buffers (\d+)", text)
            require(match is not None and tuple(map(int, match.groups())) == (expected_flops, expected_flops),
                    "incomplete routed clock tracing")
            return {"sequential_elements": expected_flops, "externally_clocked": expected_flops}
        assess("routed_clocks", clock_trace)
        def sck_gate():
            report = read(build / "sta/sck_gate.json")
            require(report["ok"] and report["gated"] and not report["problems"]
                    and report["gate"]["cell"] == "OR2101"
                    and report["truth_table_of_sck_b"] == {
                        "clk=0 idle=0": 0, "clk=0 idle=1": 1,
                        "clk=1 idle=0": 1, "clk=1 idle=1": 1},
                    "S8 clock-output gate structure/truth table differs")
            return report
        assess("sck_gate", sck_gate, [build / "sta/sck_gate.json"], "sck-gate")
        def proof():
            row = last("routed-equivalence")
            log = (build / "lec/log.txt").read_text()
            require("yosys 0.33 exit=0" in Path(row["stdout_log"]).read_text()
                    and "lec exit=0" in Path(row["stdout_log"]).read_text(), "masked proof subprocess failure")
            value = proof_evidence((build / "lec/lec/status").read_text(), log)
            value.update(gold_sha256=sha256_file(build / "lec/gold.v"),
                         gate_sha256=sha256_file(build / "lec/gate.v"),
                         liberty_sha256=sha256_file(self.runtime / "gate/du.lib"))
            return value
        assess("routed_equivalence", proof, [build / "lec/lec/status", build / "lec/log.txt"], "routed-equivalence")

        assess("rtl_tests", lambda: xunit_evidence(sim / "downstream-rtl/results.xml", 20),
               [sim / "downstream-rtl/results.xml"], "rtl-tests")
        assess("routed_zero", lambda: xunit_evidence(sim / "routed-zero/results.xml", 20),
               [sim / "routed-zero/results.xml"], "routed-zero")
        mapped = []
        for seed in range(1, 6):
            name = "mapped-" + str(seed)
            assess(name, lambda seed=seed: xunit_evidence(sim / f"mapped-{seed}/results.xml", 20),
                   [sim / f"mapped-{seed}/results.xml"], name)
            mapped.append(name)
        self.record("mapped_tests", "PASS" if all(self.checks[n]["status"] == "PASS" for n in mapped) else "FAIL",
                    {"runs": 5, "tests": 100, "seeds": [1, 2, 3, 4, 5]}, [sim / f"mapped-{s}/results.xml" for s in range(1, 6)])
        for corner in ("min", "typ", "max"):
            def sdf_case(corner=corner):
                value = xunit_evidence(sim / f"downstream-{corner}/results.xml", 20)
                row = last("routed-" + corner)
                log = Path(row["stdout_log"]).read_text() + Path(row["stderr_log"]).read_text()
                require(not re.search(r"SDF (?:WARNING|ERROR)|Unable to match|Failed to find", log, re.I),
                        f"{corner}: SDF annotation diagnostic")
                sdf = (build / f"sta/chip_{corner}.sdf").read_text()
                observed = re.findall(r"\(INSTANCE\s+([^\s)]+)\s*\)", sdf)
                model = read(physical / "stages/assembly/routed.json")["modules"]["s8_core"]
                expected = {f"u_core/{name}" for name in model["cells"]}
                expected |= {f"xpad{p['pad']}" for p in read(build / "pnr/floorplan.json")["pads"]}
                require(len(observed) == len(set(observed)) and set(observed) == expected,
                        "SDF instance inventory differs from routed design")
                require("(TIMESCALE 1ns)" in sdf and not re.search(r"\([^()]*::[^()]*\)", sdf),
                        "SDF timescale or min/typ/max triples unsupported")
                require(len(re.findall(r'\(CELLTYPE "DFFL11"\)', sdf)) == expected_flops,
                        "SDF lacks a sequential cell")
                return {**value, "annotated_instances": len(expected), "sequential_cells": expected_flops,
                        "mode": "SDF with -gspecify, explicit corner, no annotation diagnostics"}
            assess("sdf_" + corner, sdf_case,
                   [sim / f"downstream-{corner}/results.xml", build / f"sta/chip_{corner}.sdf"], "routed-" + corner)
        def seeded():
            report = read(Path(self.state["products"]["seeded_sdf"]) / "summary.json")
            require(report["ok"] is True and report["expected_flops"] == expected_flops
                    and len(report["results"]) == 15
                    and {(r["corner"], r["seed"]) for r in report["results"]} ==
                    {(c, s) for c in ("min", "typ", "max") for s in range(1, 6)}, "incomplete seeded SDF matrix")
            require(report["distinct_seed_vectors"] and report["same_seed_matches_across_corners"],
                    "invalid startup seed vectors")
            require(len({r["xml"] for r in report["results"]}) == 15, "reused seeded XML evidence")
            for row in report["results"]:
                require(row["ok"] and row["process_exit"] == 0 and row["tests"] == 20, "failed seeded run")
                xunit_evidence(Path(row["xml"]), 20)
            for path, digest in report["input_sha256"].items():
                verify_file(Path(path), digest)
            audit = Path(self.state["products"]["seeded_process_audit"])
            processes = [json.loads(line) for line in audit.read_text().splitlines()]
            require(len(processes) == 30 and all(r["returncode"] == 0 for r in processes)
                    and Counter(Path(r["command"][0]).name for r in processes) == {"iverilog": 15, "vvp": 15},
                    "seeded simulator/compiler process inventory incomplete")
            for row in processes:
                for channel in ("stdout", "stderr"):
                    verify_file(Path(row[channel + "_log"]), row[channel + "_sha256"])
            return {"runs": 15, "tests": 300, "seeds": 5, "corners": 3,
                    "initialized_flops": expected_flops, "distinct_seed_vectors": True,
                    "audited_compiler_simulator_processes": len(processes)}
        assess("seeded_sdf", seeded, [Path(self.state["products"]["seeded_sdf"]) / "summary.json"], "seeded-sdf")
        def probe():
            rows = read(build / "sdf-probe/summary.json")
            require(len(rows) == 3 and {r["corner"] for r in rows} == {"min", "typ", "max"}
                    and all(r["ok"] and r["measured_ns"] > 0
                            and abs(r["measured_ns"] - r["expected_ns"]) <= 0.002 for r in rows),
                    "nonzero SDF delay probe failed")
            return rows
        assess("sdf_annotation_probe", probe, [build / "sdf-probe/summary.json"], "sdf-probe")
        def ram(negative=False):
            row = last("ram-negative" if negative else "ram-positive", 1 if negative else 0)
            text = Path(row["stdout_log"]).read_text()
            result = re.search(r"(?m)^runs (\d+), passed (\d+), failed (\d+)$", text)
            expected = (4, 0, 4) if negative else (36, 36, 0)
            require(result is not None and tuple(map(int, result.groups())) == expected, "incomplete RAM test matrix")
            if negative:
                detected = re.findall(r"(?m)^single\s+(\w+)\s+FAIL (\d+) timing violations:", text)
                require(len(detected) == 4 and {r[0] for r in detected} == {"fib", "sieve", "uart", "blink"}
                        and all(int(r[1]) > 0 for r in detected), "RAM negatives lack positive timing-violation detection")
                require(not re.search(r"OUT differs|memory errors|halt differs|no result line|Traceback", text),
                        "RAM negative has another failure")
            return {"cases": expected[0], "passed": expected[1], "expected_timing_failures": expected[2]}
        def command_logs(name):
            rows = self.commands.get(name, [])
            return [Path(rows[-1]["stdout_log"])] if rows else []
        assess("ram_positive", ram, command_logs("ram-positive"), "ram-positive")
        assess("ram_negative", lambda: ram(True), command_logs("ram-negative"), "ram-negative", 1)
        def process_audit():
            rows = [json.loads(line) for line in Path(self.state["products"]["ram_process_audit"]).read_text().splitlines()]
            require(len(rows) == 42 and all(r["returncode"] == 0 for r in rows)
                    and Counter(Path(r["command"][0]).name for r in rows) == {"iverilog": 2, "vvp": 40},
                    "RAM subprocess inventory/outcomes incomplete")
            for row in rows:
                for channel in ("stdout", "stderr"):
                    verify_file(Path(row[channel + "_log"]), row[channel + "_sha256"])
                text = Path(row["stdout_log"]).read_text() + Path(row["stderr_log"]).read_text()
                require(not re.search(r"ERROR:|Unable to open|SDF (?:WARNING|ERROR)|Traceback", text, re.I),
                        "RAM simulator emitted an infrastructure diagnostic")
            return {"processes": 42, "compilers": 2, "simulators": 40, "all_returncodes": 0}
        assess("ram_process_exits", process_audit, [Path(self.state["products"]["ram_process_audit"])])
        def pad_polarity():
            report = read(build / "pad-polarity/pad_polarity.json")
            require(report["passed"] and len(report["cases"]) == 72 and all(r["ok"] for r in report["cases"])
                    and report["source_sha256"], "pad polarity measurements incomplete/failed")
            return {"cases": 72, "passed": True, "model_hashes": report["source_sha256"]}
        assess("pad_polarity", pad_polarity, [build / "pad-polarity/pad_polarity.json"], "pad-polarity")
        def cpu_formal():
            rows = {}
            for task in ("reset_hold", "reset_release", "bus", "commands", "isa", "isa_cover", "halt"):
                log = (build / f"cpu-formal/log_{task}.txt").read_text()
                status = build / f"cpu-formal/s8_{task}/status"
                rows[task] = proof_evidence(status.read_text(), log)
            return rows
        assess("cpu_formal", cpu_formal, [build / "cpu-formal"], "cpu-formal")
        self.record("simulation_model_limits", "MANUAL",
                    "S8 IPAD30 simulation stand-in and finite seeded startup; pad electrical/timing models are not signoff-qualified")
        def unchanged():
            for row in self.state["inputs"]:
                verify_file(Path(row["original"]), row["source_sha256"])
                verify_file(Path(row["staged"]), row["staged_sha256"])
            pdk = read(ROOT / "reference/s8/pdk-files.json")
            for row in pdk["files"]:
                verify_file(Path(pdk["root"]) / row["path"], row["sha256"])
            verify_file(physical / "result.json", self.state["physical_result_sha256"])
            current_physical = read(physical / "result.json")
            artifacts = [a for stage in current_physical["stages"] for a in stage["artifacts"]]
            for artifact in artifacts:
                verify_file(physical / artifact["path"], artifact["sha256"])
            helper = self.state["process_audit_helper"]
            verify_file(Path(helper["source"]), helper["sha256"])
            for path in helper["staged"]:
                verify_file(Path(path), helper["sha256"])
            return {"input_files": len(self.state["inputs"]), "pdk_files": len(pdk["files"]),
                    "physical_artifacts": len(artifacts), "unchanged": True}
        assess("inputs_unchanged", unchanged, [self.out / "verification-inputs.json"])
        behavior = ("rtl_tests", "routed_zero", "sdf_min", "sdf_typ", "sdf_max", "seeded_sdf",
                    "sdf_annotation_probe", "ram_positive", "ram_negative", "ram_process_exits",
                    "routed_equivalence", "routed_clocks", "sck_gate", "mapped_tests", "cpu_formal")
        self.record("s8.behavior", "PASS" if all(self.checks[n]["status"] == "PASS" for n in behavior) else "FAIL",
                    {"functional_tests": 500, "ram_positive": 36, "ram_negative_controls": 4,
                     "required_groups": list(behavior)})
        required = ("toolchain", "drc", "flat_drc", "lvs", "presubmit", "geometry", "negative_controls",
                    "parasitic_artifact", "parasitic_units", "parasitic_coverage", "parasitic_validation",
                    "timing_coverage", "timing_setup", "timing_hold", "timing_limits", "pad_model_limits",
                    "simulation_model_limits", "pad_polarity", "inputs_unchanged", "s8.behavior", *behavior)
        for name in required:
            if name not in self.checks:
                self.record(name, "NOT RUN", "required S8 downstream check has no evidence")
        self.checkpoint()
        failed = [name for name, row in self.checks.items() if row["status"] == "FAIL"]
        not_run = [name for name, row in self.checks.items() if row["status"] == "NOT RUN"]
        code = 2 if infrastructure or not_run else 1 if failed else 0
        result = {"schema_version": 1, "kind": "s8-downstream-verification", "exit_code": code,
                  "upstream_physical_run": str(physical), "upstream_result_sha256": self.state["physical_result_sha256"],
                  "verification_complete": not infrastructure and not not_run, "checks": self.checks,
                  "failed_checks": failed, "not_run": not_run, "infrastructure_errors": infrastructure,
                  "known_extraction_failure_reproduced": self.checks.get("parasitic_validation", {}).get("status") == "FAIL",
                  "generic_milestone4_adapters": "NOT IMPLEMENTED", "signoff_ready": False,
                  "signoff_blockers": ["Raw parasitic extraction is electrically unvalidated",
                                      "Input hold timing fails under declared board interface assumptions",
                                      "Pad limit interpretations and simulation stand-in remain unresolved",
                                      "Final issued identifiers, source freeze and release qualification remain pending"]}
        atomic_write_json(self.out / "result.json", result)
        lines = ["# S8 downstream verification", "", f"Aggregate result: exit {code}; signoff_ready: false.",
                 "", "This is a project-specific execution against an existing physical run. Generic milestone-4 adapters remain future work.",
                 "", "| Check | Result |", "|---|---|"]
        lines += [f"| {name} | {row['status']} |" for name, row in self.checks.items()]
        lines += ["", "Raw extraction FAIL is preserved. Timing/SDF use the diagnostic routed-length/LEF estimate, which is not electrically validated.",
                  "Pad slew violations and model limitations remain explicit signoff blockers.", "",
                  "See result.json, state.json and verification-inputs.json for outcomes, command exits and source/working-copy fingerprints.", ""]
        (self.out / "report.md").write_text("\n".join(lines), encoding="utf-8")
        outputs = []
        for path in sorted(self.out.rglob("*")):
            if path.is_file() and path.name != "output-manifest.json":
                outputs.append({"path": str(path.relative_to(self.out)), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
        atomic_write_json(self.out / "output-manifest.json", outputs)
        print(f"Downstream verification exit {code}: {self.out / 'report.md'}", flush=True)
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-run", type=Path)
    parser.add_argument("--toolchain", type=Path, default=ROOT / "toolchains/local.wsl.toml")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--phase", choices=("prepare", "layout", "timing", "regressions", "retry-simulation-io", "collect", "all"), default="all")
    args = parser.parse_args()
    if args.state:
        verifier = Verification(args.state)
    else:
        parser.error("--physical-run is required") if args.physical_run is None else None
        verifier = Verification.prepare(args)
    captures = verifier.out / "driver-captures"
    captures.mkdir(exist_ok=True)
    driver_hash = sha256_file(Path(__file__))
    capture = captures / (driver_hash + ".py")
    if not capture.exists():
        shutil.copyfile(Path(__file__), capture)
    verifier.state.setdefault("driver_phases", []).append({
        "phase": args.phase, "sha256": driver_hash, "capture": str(capture),
        "started_at": datetime.now(timezone.utc).isoformat()})
    verifier.save()
    if args.phase in ("layout", "all"):
        verifier.layout()
    if args.phase in ("timing", "all"):
        verifier.timing()
    if args.phase in ("regressions", "all"):
        verifier.regressions()
    if args.phase == "retry-simulation-io":
        verifier.retry_simulation_io()
    if args.phase in ("collect", "all"):
        return verifier.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
