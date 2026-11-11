from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from chipflow.config import load_project
from chipflow.config.pads import build_pad_plan, load_pad_map
from chipflow.runner.execution import sha256_file
from chipflow.stages.constraints import build_constraints
from chipflow.stages.pnr import pnr_stage
from chipflow.technologies.profile import load_profile
from chipflow.toolchain import Tool, Toolchain

from test_physical_flow import DUMMY_LEF


class PnrConstraintsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sdc-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        repo = Path(__file__).resolve().parents[3]
        self.project = load_project(repo / "projects/s8/chip.toml")
        self.profile = load_profile()
        self.plan = build_pad_plan(load_pad_map(self.project.pads), self.profile)
        tool = self.root / "openroad"
        tool.write_text("synthetic executable identity", encoding="utf-8")
        self.toolchain = Toolchain(self.root / "toolchain.toml",
            (Tool("openroad", tool, ("-version",), "OpenROAD", sha256_file(tool)),),
            self.root, (), {}, {})
        self.ports = {}
        for pad in self.plan["pads"]:
            if pad["kind"] != "signal":
                continue
            base = pad["port"].split("[")[0]
            self.ports.setdefault(base, {"direction": pad["direction"], "canonical_bits": []})
            self.ports[base]["canonical_bits"].append(pad["port"])
        for port in self.ports.values():
            port["canonical_bits"].sort()
            port["width"] = len(port["canonical_bits"])

    def prepare(self, name, project, corrupt=None):
        run = self.root / name
        run.mkdir()
        (run / "resolved-project.json").write_text(json.dumps(project.to_dict()), encoding="utf-8")
        (run / "pad-plan.json").write_text(json.dumps(self.plan), encoding="utf-8")
        stages = []
        for stage, files in {
            "technology": {"tech.lef": "tech", "cells.lef": DUMMY_LEF, "core_typ.lib": "typ",
                "core_min.lib": "min", "core_max.lib": "max", "pads_typ.lib": "pt",
                "pads_min.lib": "pmin", "pads_max.lib": "pmax", "work/core.gds": "gds",
                "technology.json": json.dumps({"pad_terminals_um": {
                    "OPAD04": [189.5, 193.65], "IPAD2D": [210.85, 213.35],
                    "IPAD3D": [219.25, 221.75], "IPAD30": [210.85, 213.35]}})},
            "synthesis": {"mapped.v": "mapped",
                          "impl.sdc": build_constraints(project, self.profile, self.plan, self.ports).text}
        }.items():
            artifacts = []
            for relative, text in files.items():
                path = run / "stages" / stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                artifacts.append({"path": str(path.relative_to(run)), "sha256": sha256_file(path)})
            stages.append({"id": stage, "status": "succeeded", "artifacts": artifacts})
        stages.append({"id": "configuration", "status": "succeeded", "artifacts": [
            {"path": "pad-plan.json", "sha256": sha256_file(run / "pad-plan.json")}]})
        (run / "execution.json").write_text(json.dumps({"stages": stages}), encoding="utf-8")
        if corrupt:
            (run / corrupt).write_text("unrecorded change", encoding="utf-8")
        output = run / "prepared"
        output.mkdir()
        envelope = pnr_stage(project, self.profile, self.toolchain, run).prepare(output)
        return output, envelope

    def test_resolved_sdc_is_consumed_by_placement_and_routing(self):
        output, envelope = self.prepare("base", self.project)
        self.assertIn("read_sdc $::env(SDC)", (output / "pnr.tcl").read_text())
        self.assertIn("read_sdc impl.sdc", (output / "route.tcl").read_text())
        self.assertNotIn("set_load 1.0", (output / "pnr.tcl").read_text())
        row, = [r for r in envelope.inputs if r["path"] == "impl.sdc"]
        self.assertEqual(row["sha256"], sha256_file(output / "impl.sdc"))
        self.assertIn("set_load 0.993700", (output / "impl.sdc").read_text())

    def test_changed_waveform_uncertainty_and_io_delays_change_consumed_sdc(self):
        project = replace(self.project, clock=replace(self.project.clock, waveform_ns=(0.0, 35.0), uncertainty_ns=5.0),
            io=replace(self.project.io, inputs=tuple(replace(p, min_ns=2.0, max_ns=12.0) for p in self.project.io.inputs)))
        original, _ = self.prepare("original", self.project)
        variant, _ = self.prepare("variant", project)
        self.assertNotEqual((original / "impl.sdc").read_bytes(), (variant / "impl.sdc").read_bytes())
        text = (variant / "impl.sdc").read_text()
        self.assertIn("-waveform {0.000000 35.000000}", text)
        self.assertIn("set_clock_uncertainty 5.000000", text)
        self.assertIn("-max 12.000000", text)

    def test_unrecorded_netlist_or_pad_plan_change_is_rejected(self):
        for index, path in enumerate(("stages/synthesis/mapped.v", "pad-plan.json")):
            with self.subTest(path=path), self.assertRaisesRegex((RuntimeError, ValueError), "hash mismatch"):
                self.prepare(f"corrupt{index}", self.project, corrupt=path)
