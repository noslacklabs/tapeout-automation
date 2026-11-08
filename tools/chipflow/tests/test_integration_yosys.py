from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from chipflow.cli import snapshot_project
from chipflow.config import load_project
from chipflow.runner.execution import execute_stages
from chipflow.runner.results import CheckStatus, StageStatus
from chipflow.stages.elaboration import elaboration_stage
from chipflow.toolchain import doctor, load_toolchain


CHIP_TOML_TEMPLATE = '''schema_version = 1
[project]
name = "counter_test"
top = "{top}"
sources = ["{top}.v"]
include_dirs = ["inc"]
defines = ["DEF_W=4"]
parameters = {{ WIDTH = 4 }}

[technology]
id = "scl_c1d"
frame = "scl-c1d-2mm"
profile_version = "0.1.0"
top_cell = "OS0000"
mode = "development"
development_identifiers = true

[clock]
port = "clk"
period_ns = 100.0
waveform_ns = [0.0, 50.0]
transition_ns = 2.5
uncertainty_ns = 0.0

[reset]
port = "rst"
kind = "synchronous"
active = "high"

[io]
corners = ["min", "typ", "max"]
inputs = [{{ port = "rst", min_ns = 0.0, max_ns = 0.0 }}, {{ port = "in_bus[0]", min_ns = 0.0, max_ns = 0.0 }}]
outputs = [{{ port = "out_bus[1]", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 }}]

[pads]
mapping = "pads.toml"

[build]
root = "builds"
seed = 1
threads = 1
timeout_seconds = 30

[verification]
required_checks = ["counter.behavior"]
optional_checks = ["formal"]
manual_items = []
data = []
'''


class IntegrationYosysTests(unittest.TestCase):
    def setUp(self):
        toolchain_path_str = os.environ.get("CHIPFLOW_TEST_TOOLCHAIN")
        if not toolchain_path_str:
            self.skipTest("CHIPFLOW_TEST_TOOLCHAIN environment variable not set")
        self.toolchain_path = Path(toolchain_path_str)
        if not self.toolchain_path.is_file():
            self.skipTest(f"CHIPFLOW_TEST_TOOLCHAIN file does not exist: {self.toolchain_path}")
        self.toolchain = load_toolchain(self.toolchain_path)

        self.temp = tempfile.TemporaryDirectory(prefix="chipflow yosys ")
        self.proj_dir = Path(self.temp.name) / "test_proj"
        self.proj_dir.mkdir()
        self.addCleanup(self.temp.cleanup)

        self.inc_dir = self.proj_dir / "inc"
        self.inc_dir.mkdir()
        (self.inc_dir / "width.vh").write_text("`define EXTRA_INC 1\n", encoding="utf-8")
        (self.proj_dir / "pads.toml").write_text("qualification = 'NOT RUN'\n", encoding="utf-8")

    def _run_elaboration(self, top: str, verilog_code: str, edits: dict[str, str] | None = None):
        (self.proj_dir / f"{top}.v").write_text(verilog_code, encoding="utf-8")
        toml_content = CHIP_TOML_TEMPLATE.format(top=top)
        for old, new in (edits or {}).items():
            self.assertIn(old, toml_content)
            toml_content = toml_content.replace(old, new)
        (self.proj_dir / "chip.toml").write_text(toml_content, encoding="utf-8")

        project = load_project(self.proj_dir / "chip.toml")
        run_dir = self.proj_dir / "builds/run1"
        run_dir.mkdir(parents=True)
        snapshot_project(project, run_dir)
        environment = doctor(self.toolchain, run_dir / "doctor")
        self.assertEqual(environment["exit_code"], 0, environment)

        spec = elaboration_stage(project, self.toolchain, run_dir)
        summary = execute_stages(run_dir, [spec])
        return summary.stages[0], run_dir

    def test_passing_counter_with_ranges_and_includes(self):
        v_code = '''
`include "width.vh"
module counter #(parameter WIDTH = 4) (
    input clk,
    input rst,
    input [0:3] in_bus,
    output reg [8:1] out_bus
);
    always @(posedge clk) begin
        if (rst)
            out_bus <= 8'd0;
        else
            out_bus <= {out_bus[7:1], in_bus[0]};
    end
endmodule
'''
        stage_res, run_dir = self._run_elaboration("counter", v_code)
        self.assertEqual(stage_res.status, StageStatus.SUCCEEDED)
        self.assertEqual(stage_res.checks[0].status, CheckStatus.PASS)

    def test_negative_undefined_module(self):
        v_code = '''
module undef_mod #(parameter WIDTH = 4) (input clk, input rst, input [0:3] in_bus, output [8:1] out_bus);
    missing_submodule sub (.clk(clk));
endmodule
'''
        stage_res, run_dir = self._run_elaboration("undef_mod", v_code)
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "configuration")
        self.assertIn("missing_submodule", stage_res.checks[0].message)

    def test_negative_async_reset(self):
        v_code = '''
module async_mod #(parameter WIDTH = 4) (input clk, input rst, input [0:3] in_bus, output reg [8:1] out_bus);
    always @(posedge clk or posedge rst) begin
        if (rst)
            out_bus <= 8'd0;
        else
            out_bus <= out_bus + 8'd1;
    end
endmodule
'''
        stage_res, run_dir = self._run_elaboration("async_mod", v_code)
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "configuration")
        self.assertIn("asynchronous reset", stage_res.checks[0].message)

    def test_negative_negative_edge_flop(self):
        v_code = '''
module negedge_mod #(parameter WIDTH = 4) (input clk, input rst, input [0:3] in_bus, output reg [8:1] out_bus);
    always @(negedge clk) begin
        if (rst)
            out_bus <= 8'd0;
        else
            out_bus <= out_bus + 8'd1;
    end
endmodule
'''
        stage_res, run_dir = self._run_elaboration("negedge_mod", v_code)
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "configuration")
        self.assertIn("negative-edge flop", stage_res.checks[0].message)

    def test_negative_inout_port(self):
        v_code = '''
module inout_mod #(parameter WIDTH = 4) (input clk, input rst, input [0:3] in_bus, output [8:1] out_bus, inout io_pin);
    assign out_bus = 8'd0;
endmodule
'''
        stage_res, run_dir = self._run_elaboration("inout_mod", v_code)
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "configuration")
        self.assertIn("inout ports not supported", stage_res.checks[0].message)

    HEAD = "module {top} #(parameter WIDTH = 4) (input clk, input rst, input [0:3] in_bus, output [8:1] out_bus{extra});\n"
    FLOP = "    reg r; always @(posedge clk) r <= rst ? 1'b0 : in_bus[0]; assign out_bus = {7'd0, r};\n"

    def _rows(self, run_dir: Path) -> dict[str, dict]:
        design = json.loads((run_dir / "stages/elaboration/design.json").read_text(encoding="utf-8"))
        return {row["id"]: row for row in design["rows"]}

    def _assert_row_fails(self, top: str, body: str, row_id: str, text: str, extra: str = "") -> None:
        stage, run_dir = self._run_elaboration(top, self.HEAD.format(top=top, extra=extra) + body + "endmodule\n")
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "configuration"))
        row = self._rows(run_dir)[row_id]
        self.assertEqual(row["status"], "FAIL", row)
        self.assertIn(text, row["message"])

    def test_negative_constructs_from_handoff_section_7(self):
        cases = [
            ("latch_mod", "    reg l; always @* if (in_bus[1]) l = in_bus[2];\n    assign out_bus = {7'd0, l};\n" + self.FLOP.replace("assign out_bus = {7'd0, r};", ""),
             "elab.async_or_latch", "$dlatch"),
            ("init_mod", self.FLOP.replace("reg r;", "reg r = 1'b1;"), "elab.init_values", "r"),
            ("tri_mod", self.FLOP.replace("{7'd0, r}", "{7'd0, in_bus[1] ? r : 1'bz}"), "elab.tristate", "'z'"),
            ("gated_mod", "    wire g = clk & in_bus[3];\n" + self.FLOP.replace("posedge clk", "posedge g"), "elab.single_clock", "not on primary clock"),
            ("twoclk_mod", "    reg r2; always @(posedge in_bus[3]) r2 <= rst ? 1'b0 : in_bus[1];\n"
                           + self.FLOP.replace("{7'd0, r}", "{6'd0, r2, r}"), "elab.single_clock", "not on primary clock"),
        ]
        for top, body, row_id, text in cases:
            with self.subTest(top=top):
                self.setUp()
                self._assert_row_fails(top, body, row_id, text)
        self.setUp()
        bb = "(* blackbox *) module bb(input a, output y); endmodule\n"
        stage, run_dir = self._run_elaboration("bb_mod", bb + self.HEAD.format(top="bb_mod", extra="") + "    wire y;\n"
                                               + self.FLOP.replace("{7'd0, r}", "{7'd0, y}") + "    bb u(.a(r), .y(y));\nendmodule\n")
        self.assertEqual(self._rows(run_dir)["elab.blackbox"]["status"], "FAIL")

    def test_clock_feedthrough_to_output_needs_a_declaration(self):
        body = self.FLOP + "    assign co = clk;\n"
        self._assert_row_fails("ft_mod", body, "elab.clock_usage", "drives output bit(s) directly: co", extra=", output co")
        self.setUp()
        stage, run_dir = self._run_elaboration("ft_mod", self.HEAD.format(top="ft_mod", extra=", output co") + body + "endmodule\n", {
            "uncertainty_ns = 0.0": 'uncertainty_ns = 0.0\nderived_outputs = [{ port = "co", check = "ft.co_gate" }]',
            'outputs = [{ port = "out_bus[1]"': 'outputs = [{ port = "co", min_ns = 0.0, max_ns = 0.0, load_pf = 1.0 }, { port = "out_bus[1]"',
        })
        self.assertEqual(stage.status, StageStatus.SUCCEEDED, self._rows(run_dir)["elab.clock_usage"])

    def test_outside_include_sharing_a_declared_basename_fails(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "width.vh").write_text("`define OUTSIDE_BIT 1'b0\n", encoding="utf-8")
        body = self.FLOP.replace("1'b0", "`OUTSIDE_BIT")
        code = f'`include "{(outside / "width.vh").as_posix()}"\n' + self.HEAD.format(top="inc_mod", extra="") + body + "endmodule\n"
        stage, run_dir = self._run_elaboration("inc_mod", code)
        row = self._rows(run_dir)["elab.dependencies"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("undeclared dependency", row["message"])
        self.assertIn("outside/width.vh", row["message"])

    def test_bit_indices_follow_declared_ranges(self):
        # out_bus[8] = in_bus[3] and out_bus[1] = in_bus[0] share Yosys bits, so the
        # canonical names at those positions must agree with the HDL indices.
        code = ("module idx_mod #(parameter WIDTH = 4) (input clk, input rst, input [0:3] in_bus, output [8:1] out_bus, output [3:3] s);\n"
                "    assign out_bus[8] = in_bus[3]; assign out_bus[1] = in_bus[0]; assign out_bus[7:2] = 6'd0;\n"
                "    assign s[3] = in_bus[2];\n    reg r; always @(posedge clk) r <= rst;\nendmodule\n")
        stage, run_dir = self._run_elaboration("idx_mod", code)
        raw = json.loads((run_dir / "stages/elaboration/elab.json").read_text(encoding="utf-8"))["modules"]["idx_mod"]["ports"]
        ports = json.loads((run_dir / "stages/elaboration/design.json").read_text(encoding="utf-8"))["ports"]
        name_of = {}
        for port in ("in_bus", "out_bus", "s"):
            for bit, name in zip(raw[port]["bits"], ports[port]["canonical_bits"]):
                if isinstance(bit, int):
                    name_of.setdefault(bit, set()).add(name)
        self.assertIn({"in_bus[3]", "out_bus[8]"}, name_of.values())
        self.assertIn({"in_bus[0]", "out_bus[1]"}, name_of.values())
        self.assertIn({"in_bus[2]", "s[3]"}, name_of.values())


if __name__ == "__main__":
    unittest.main()
