import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from chipflow.stages.cdl import CdlError, generate_cdl
from chipflow.stages.def_model import Component, Net, Pin, RoutedDef
from chipflow.stages.library_model import CdlSubckt


class CdlTests(unittest.TestCase):
    def setUp(self):
        self.core = {"INV": CdlSubckt("INV", ("A", "Y", "VDD", "VSS"), "")}
        self.io = {"OPAD": CdlSubckt("OPAD", ("PAD", "A", "VSS", "VDD"), ""),
                   "VDDPAD": CdlSubckt("VDDPAD", ("VDD", "VSS"), ""),
                   "IPAD": CdlSubckt("IPAD", ("PAD", "Y", "VDD", "VSS"), "")}
        self.data = {"modules": {"demo": {
            "ports": {"a": {"direction": "input", "bits": [2]},
                      "y": {"direction": "output", "bits": [3]}},
            "cells": {"escaped.name": {"type": "INV", "port_directions": {"A": "input", "Y": "output"},
                                        "connections": {"A": [2], "Y": [3]}}},
            "netnames": {"n": {"bits": [3]}, "y": {"bits": [3]}}
        }}}
        self.routed = RoutedDef("demo", (Component("escaped.name", "INV", 0, 0),),
            (Pin("a", "a", "input", "metal1", (0, 0, 5, 5)),
             Pin("y", "y", "output", "metal1", (0, 0, 5, 5))),
            (Net("a", (("PIN", "a"), ("escaped.name", "A"))),
             Net("y", (("PIN", "y"), ("escaped.name", "Y")))), (), ())
        self.pads = [{"pad": 1, "cell": "IPAD", "core_port": "a"},
                     {"pad": 2, "cell": "OPAD", "core_port": "y"}]
        self.models = {"IPAD": {"pad_pin": "PAD", "core_pin": "Y"},
                       "OPAD": {"pad_pin": "PAD", "core_pin": "A"}}

    def generate(self, **kwargs):
        return generate_cdl(kwargs.get("data", self.data), kwargs.get("routed", self.routed),
                            "CHIP", self.pads, [{"slot": 13, "cell": "VDDPAD"}],
                            self.core, self.io, self.models)

    def test_aliases_and_escaped_names_use_resolved_bits(self):
        text, inventory = self.generate()
        self.assertIn("X0 n_2 n_3 VDD VSS INV", text)
        self.assertIn("XPAD2 PAD2 n_3 VSS VDD OPAD", text)
        self.assertEqual(inventory["core_instances"], 1)
        self.assertEqual(inventory["reconciled_endpoints"], 4)

    def test_net_name_collisions_cannot_merge_unrelated_bits(self):
        data = copy.deepcopy(self.data)
        data["modules"]["demo"]["netnames"] = {"a.b": {"bits": [2]}, "a_b": {"bits": [3]}, "VDD": {"bits": [3]}}
        text, _ = self.generate(data=data)
        self.assertIn("X0 n_2 n_3 VDD VSS INV", text)

    def test_constants_resolve_to_supplies_and_unknowns_fail(self):
        for bit, expected in (("0", "VSS"), ("1", "VDD"), ("x", None), ("z", None)):
            data = copy.deepcopy(self.data)
            data["modules"]["demo"]["ports"]["a"]["bits"] = [bit]
            data["modules"]["demo"]["cells"]["escaped.name"]["connections"]["A"] = [bit]
            if expected:
                text, _ = self.generate(data=data)
                self.assertIn(f"X0 {expected} n_3 VDD VSS INV", text)
            else:
                with self.assertRaises(CdlError):
                    self.generate(data=data)

    def test_rejects_def_missing_instance_and_shorted_or_omitted_pins(self):
        from dataclasses import replace
        cases = [replace(self.routed, components=()),
                 replace(self.routed, nets=(Net("a", (("PIN", "a"), ("escaped.name", "Y"))), self.routed.nets[1])),
                 replace(self.routed, nets=(self.routed.nets[0], Net("y", (("PIN", "y"),)))),
                 replace(self.routed, pins=(replace(self.routed.pins[0], direction="output"), self.routed.pins[1]))]
        for routed in cases:
            with self.subTest(routed=routed), self.assertRaises(CdlError):
                self.generate(routed=routed)

    def test_rejects_unmodelled_or_missing_input_pins(self):
        for connection in ({}, {"A": [], "Y": [3]}, {"A": [2, 3], "Y": [3]}):
            data = copy.deepcopy(self.data)
            data["modules"]["demo"]["cells"]["escaped.name"]["connections"] = connection
            with self.subTest(connection=connection), self.assertRaises(CdlError):
                self.generate(data=data)

    def test_unconnected_library_output_has_a_unique_floating_net(self):
        self.core["INV"] = CdlSubckt("INV", ("A", "Y", "Z", "VDD", "VSS"), "")
        self.data["modules"]["INV"] = {"ports": {"Z": {"direction": "output"}}}
        text, _ = self.generate()
        self.assertIn("X0 n_2 n_3 nc_0_Z VDD VSS INV", text)

    def test_library_header_parameters_and_model_body_are_preserved(self):
        from chipflow.stages.library_model import parse_cdl
        definition = ".SUBCKT RNW N1 N2 W='W' L='L'\nR1 N1 N2 '2680*(L/W)' RNW\n.ENDS RNW"
        with tempfile.TemporaryDirectory(prefix="model-") as directory:
            path = Path(directory) / "library.cdl"
            path.write_text(definition + "\n", encoding="utf-8")
            self.core.update(parse_cdl(path))
        self.core["INV"] = CdlSubckt("INV", ("A", "Y", "VDD", "VSS"), "XRES A Y RNW W=2 L=3\n")
        text, _ = self.generate()
        self.assertIn(definition, text)

    def test_library_directive_case_does_not_create_a_model_conflict(self):
        from dataclasses import replace
        self.core["INV"] = replace(self.core["INV"], definition=".SUBCKT INV A Y VDD VSS\n.ENDS INV")
        self.io["INV"] = replace(self.core["INV"], definition=".subckt INV A Y VDD VSS\n.ENDS INV")
        self.generate()

    def test_conflicting_library_parameter_defaults_are_rejected(self):
        from dataclasses import replace
        self.core["INV"] = replace(self.core["INV"], definition=".SUBCKT INV A Y VDD VSS W=1\n.ENDS INV")
        self.io["INV"] = replace(self.core["INV"], definition=".SUBCKT INV A Y VDD VSS W=2\n.ENDS INV")
        with self.assertRaisesRegex(CdlError, "conflicting CDL"):
            self.generate()

    @unittest.skipUnless(os.environ.get("CHIPFLOW_TEST_TOOLCHAIN"),
                         "CHIPFLOW_TEST_TOOLCHAIN environment variable not set")
    def test_real_yosys_resolves_verilog_alias_before_cdl_generation(self):
        from chipflow.toolchain import load_toolchain, verify_file
        tool = load_toolchain(os.environ["CHIPFLOW_TEST_TOOLCHAIN"]).tool("yosys")
        verify_file(tool.executable, tool.sha256)
        with tempfile.TemporaryDirectory(prefix="cdl-") as directory:
            directory = Path(directory)
            (directory / "cells.v").write_text("module INV(input A, output Y); endmodule\n", encoding="utf-8")
            (directory / "design.v").write_text(
                "module demo(input a, output y);\nwire n;\n"
                "INV \\escaped.name (.A(a), .Y(n));\nassign y = n;\nendmodule\n", encoding="utf-8")
            result = subprocess.run([str(tool.executable), "-Q", "-T", "-p",
                "read_verilog -lib cells.v; read_verilog design.v; hierarchy -check -top demo; write_json resolved.json"],
                cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            data = json.loads((directory / "resolved.json").read_text())
            text, _ = self.generate(data=data)
            core = next(line for line in text.splitlines() if line.startswith("X0 ")).split()
            pad = next(line for line in text.splitlines() if line.startswith("XPAD2 ")).split()
            self.assertEqual(core[2], pad[2])
