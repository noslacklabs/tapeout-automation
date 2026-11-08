from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import sys
import tempfile
import unittest

from chipflow.cli import snapshot_project
from chipflow.config import ConfigError, load_project
from chipflow.runner.execution import (
    execute_stages,
    atomic_write_json,
    sha256_file,
    StageSpec,
)
from chipflow.runner.results import CheckStatus, StageStatus
from chipflow.stages.elaboration import elaboration_stage
from chipflow.toolchain import Tool, Toolchain

PROJECT_TEMPLATE = '''schema_version = 1
[project]
name = "test_chip"
top = "top"
sources = ["top.v"]
include_dirs = ["inc"]
defines = ["DEF1=VAL1"]
parameters = { PARAM1 = 4 }

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
inputs = [{ port = "rst", min_ns = 0.0, max_ns = 0.0 }, { port = "in1", min_ns = 0.0, max_ns = 0.0 }]
outputs = [{ port = "out1", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 }]

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


def make_dummy_toolchain(root: Path) -> Toolchain:
    dummy_tool = root / "dummy_yosys.py"
    dummy_tool.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    t_hash = sha256_file(dummy_tool)
    tools = (
        Tool("yosys", dummy_tool, ("-V",), "Yosys", t_hash),
    )
    return Toolchain(root / "toolchain.toml", tools, root, (), {}, {})


class ElaborationStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chipflow elab ")
        self.proj_dir = Path(self.temp.name) / "project space \u00b5"
        self.proj_dir.mkdir()
        self.addCleanup(self.temp.cleanup)

        # Create basic files
        (self.proj_dir / "top.v").write_text("module top; endmodule\n", encoding="utf-8")
        inc_dir = self.proj_dir / "inc"
        inc_dir.mkdir()
        (inc_dir / "header.vh").write_text("// header\n", encoding="utf-8")
        (self.proj_dir / "pads.toml").write_text("qualification = 'NOT RUN'\n", encoding="utf-8")
        (self.proj_dir / "chip.toml").write_text(PROJECT_TEMPLATE, encoding="utf-8")

    def _snapshot_with_synthetic_doctor(self, project, run_dir):
        snapshot_project(project, run_dir)
        atomic_write_json(run_dir / "doctor/doctor.json", {
            "exit_code": 0,
            "kind": "synthetic-doctor-for-adapter-unit-tests",
        })

    def test_unsafe_include_name_rejected_before_execution(self):
        bad_inc = self.proj_dir / "inc" / "bad name with space.vh"
        bad_inc.write_text("// bad\n", encoding="utf-8")
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        run_dir = self.proj_dir / "builds/run1"
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)
        with self.assertRaises(ConfigError) as ctx:
            elaboration_stage(project, toolchain, run_dir)
        self.assertIn("include path has unsupported characters", str(ctx.exception))

    def test_negative_and_string_parameters_rejected(self):
        toolchain = make_dummy_toolchain(self.proj_dir)

        # String parameter
        p_str = PROJECT_TEMPLATE.replace("PARAM1 = 4", "PARAM1 = 'string'")
        (self.proj_dir / "chip.toml").write_text(p_str, encoding="utf-8")
        project = load_project(self.proj_dir / "chip.toml")
        with self.assertRaises(ConfigError):
            elaboration_stage(project, toolchain, self.proj_dir / "builds/run1")

        # Negative parameter
        p_neg = PROJECT_TEMPLATE.replace("PARAM1 = 4", "PARAM1 = -1")
        (self.proj_dir / "chip.toml").write_text(p_neg, encoding="utf-8")
        project = load_project(self.proj_dir / "chip.toml")
        with self.assertRaises(ConfigError):
            elaboration_stage(project, toolchain, self.proj_dir / "builds/run1")

    def test_staging_with_spaces_and_special_chars(self):
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        run_dir = self.proj_dir / "builds/run1"
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)

        spec = elaboration_stage(project, toolchain, run_dir)
        stage_dir = run_dir / "stages/elaboration"
        stage_dir.mkdir(parents=True)
        envelope = spec.prepare(stage_dir)
        envelope.write(stage_dir / "stage-input.json")

        self.assertTrue((stage_dir / "path-map.json").is_file())
        self.assertTrue((stage_dir / "elab.ys").is_file())
        ys_content = (stage_dir / "elab.ys").read_text(encoding="utf-8")
        self.assertIn("-I work/inc/0000", ys_content)
        self.assertIn("work/src/0000_top.v", ys_content)
        self.assertIn("-chparam PARAM1 4", ys_content)

    def test_include_staging_uses_only_snapshot_after_live_tree_changes(self):
        inc_dir = self.proj_dir / "inc"
        removed_live = inc_dir / "removed.vh"
        removed_live.write_text("// original removed header\n", encoding="utf-8")
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        run_dir = self.proj_dir / "builds/snapshot-only"
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)

        (inc_dir / "header.vh").write_text("// changed live bytes\n", encoding="utf-8")
        removed_live.unlink()
        # A newly added unsupported name must not affect the earlier snapshot.
        (inc_dir / "new live header.vh").write_text("// not an input\n", encoding="utf-8")
        spec = elaboration_stage(project, toolchain, run_dir)
        stage_dir = run_dir / "stages/elaboration"
        stage_dir.mkdir(parents=True)
        spec.prepare(stage_dir)

        staged_inc = stage_dir / "work/inc/0000"
        self.assertEqual(sorted(p.name for p in staged_inc.iterdir()), ["header.vh", "removed.vh"])
        self.assertEqual((staged_inc / "header.vh").read_text(encoding="utf-8"), "// header\n")
        self.assertEqual((staged_inc / "removed.vh").read_text(encoding="utf-8"), "// original removed header\n")
        path_map = json.loads((stage_dir / "path-map.json").read_text(encoding="utf-8"))
        for row in path_map:
            self.assertEqual(sha256_file(stage_dir / row["staged"]), row["sha256"])

    def test_missing_or_corrupt_evidence_blocks_execution_and_dependents(self):
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        cases = (
            ("missing-manifest", "input-manifest.json", None),
            ("corrupt-manifest", "input-manifest.json", "not json"),
            ("incomplete-manifest", "input-manifest.json", "[]"),
            ("missing-configuration", "resolved-project.json", None),
            ("missing-doctor", "doctor/doctor.json", None),
            ("corrupt-doctor", "doctor/doctor.json", "not json"),
            ("failed-doctor", "doctor/doctor.json", '{"exit_code": 2}'),
            ("missing-source", "inputs/project/top.v", None),
            ("changed-source", "inputs/project/top.v", "// corrupt source\n"),
            ("missing-include", "inputs/project/inc/header.vh", None),
            ("changed-include", "inputs/project/inc/header.vh", "// corrupt header\n"),
        )
        for name, relative, replacement in cases:
            with self.subTest(name=name):
                run_dir = self.proj_dir / "builds" / name
                run_dir.mkdir(parents=True)
                self._snapshot_with_synthetic_doctor(project, run_dir)
                spec = elaboration_stage(project, toolchain, run_dir)
                target = run_dir / relative
                if replacement is None:
                    target.unlink()
                else:
                    target.write_text(replacement, encoding="utf-8")
                command_marker = run_dir / "command-ran"
                fake_spec = dataclasses.replace(spec, command=(
                    sys.executable, "-c",
                    f"from pathlib import Path; Path({str(command_marker)!r}).touch()",
                ))
                consumer = StageSpec("consumer", (sys.executable, "-c", "pass"), dependencies=("elaboration",))
                summary = execute_stages(run_dir, (fake_spec, consumer))
                elab, downstream = summary.stages
                self.assertEqual((elab.status, elab.failure_kind), (StageStatus.FAILED, "infrastructure"))
                self.assertIn("prepare failed", elab.error)
                self.assertEqual(downstream.status, StageStatus.BLOCKED)
                self.assertIn("elaboration", downstream.failed_ancestors)
                self.assertFalse(command_marker.exists())

    def test_missing_snapshot_manifest_is_refused_when_constructing_adapter(self):
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        with self.assertRaises(FileNotFoundError):
            elaboration_stage(project, toolchain, self.proj_dir / "builds/unsnapshotted")

    def test_faked_yosys_error_log_gives_configuration_failure(self):
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        run_dir = self.proj_dir / "builds/run1"
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)

        fake_script = (
            "from pathlib import Path; "
            "Path('elab.log').write_text('ERROR: Module work/src/0000_top.v:10 syntax error\\n'); "
            "import sys; sys.exit(1)"
        )
        spec = elaboration_stage(project, toolchain, run_dir)
        fake_spec = dataclasses.replace(spec, command=(sys.executable, "-c", fake_script))
        summary = execute_stages(run_dir, [fake_spec])
        stage_res = summary.stages[0]
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "configuration")
        check = stage_res.checks[0]
        self.assertEqual(check.status, CheckStatus.FAIL)
        # The staged name is replaced by the original project path
        self.assertIn(f"{project.sources[0]}:10", check.message)
        self.assertNotIn("work/src/", check.message)

    def test_faked_yosys_crash_without_error_gives_infrastructure_failure(self):
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        run_dir = self.proj_dir / "builds/run1"
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)

        fake_script = "import sys; sys.exit(139)"  # SIGSEGV exit
        spec = elaboration_stage(project, toolchain, run_dir)
        fake_spec = dataclasses.replace(spec, command=(sys.executable, "-c", fake_script))
        summary = execute_stages(run_dir, [fake_spec])
        stage_res = summary.stages[0]
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "infrastructure")

    def test_missing_or_garbled_elab_json_gives_infrastructure_failure(self):
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        run_dir = self.proj_dir / "builds/run1"
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)

        fake_script = (
            "from pathlib import Path; "
            "Path('elab.log').write_text('ok\\n'); "
            "Path('deps.txt').write_text('elab.json: elab.ys\\n'); "
            "Path('elab.json').write_text('garbled not json'); "
        )
        spec = elaboration_stage(project, toolchain, run_dir)
        fake_spec = dataclasses.replace(spec, command=(sys.executable, "-c", fake_script))
        summary = execute_stages(run_dir, [fake_spec])
        stage_res = summary.stages[0]
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "infrastructure")

    def test_dependency_outside_path_map_gives_fail(self):
        project = load_project(self.proj_dir / "chip.toml")
        toolchain = make_dummy_toolchain(self.proj_dir)
        run_dir = self.proj_dir / "builds/run1"
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)

        # Write valid outputs but deps.txt mentions an unmapped file
        fake_script = (
            "from pathlib import Path; import json; "
            "Path('elab.log').write_text('ok\\n'); "
            "Path('deps.txt').write_text('elab.json: elab.ys work/src/0000_top.v secret/undeclared.v\\n'); "
            "d = {'modules': {'top': {'ports': {'clk': {'direction': 'input', 'bits': [1]}, 'rst': {'direction': 'input', 'bits': [2]}}, "
            "'cells': {'dff': {'type': '$dff', 'parameters': {'CLK_POLARITY': '1', 'WIDTH': '1'}, 'port_directions': {'CLK': 'input', 'D': 'input', 'Q': 'output'}, 'connections': {'CLK': [1], 'D': [2], 'Q': [3]}}}, 'netnames': {}}}};"
            "Path('elab.json').write_text(json.dumps(d)); "
        )
        spec = elaboration_stage(project, toolchain, run_dir)
        fake_spec = dataclasses.replace(spec, command=(sys.executable, "-c", fake_script))
        summary = execute_stages(run_dir, [fake_spec])
        stage_res = summary.stages[0]
        self.assertEqual(stage_res.status, StageStatus.FAILED)
        self.assertEqual(stage_res.failure_kind, "configuration")
        self.assertIn("undeclared dependency", stage_res.checks[0].message)

    def _run_with_deps(self, run_name: str, deps: str):
        project = load_project(self.proj_dir / "chip.toml")
        run_dir = self.proj_dir / "builds" / run_name
        run_dir.mkdir(parents=True)
        self._snapshot_with_synthetic_doctor(project, run_dir)
        fake_script = (
            "from pathlib import Path; import json; "
            "Path('elab.log').write_text('ok\\n'); "
            f"Path('deps.txt').write_text({deps!r}); "
            "d = {'modules': {'top': {'ports': {'clk': {'direction': 'input', 'bits': [1]}, 'rst': {'direction': 'input', 'bits': [2]}}, "
            "'cells': {'dff': {'type': '$dff', 'parameters': {'CLK_POLARITY': '1', 'WIDTH': '1'}, 'port_directions': {'CLK': 'input', 'D': 'input', 'Q': 'output'}, 'connections': {'CLK': [1], 'D': [2], 'Q': [3]}}}, 'netnames': {}}}};"
            "Path('elab.json').write_text(json.dumps(d)); "
        )
        spec = elaboration_stage(project, make_dummy_toolchain(self.proj_dir), run_dir)
        stage = execute_stages(run_dir, [dataclasses.replace(spec, command=(sys.executable, "-c", fake_script))]).stages[0]
        return stage, run_dir

    def test_undeclared_file_sharing_a_declared_basename_fails(self):
        # The declared include is inc/header.vh; an outside file of the same name must not pass.
        stage, _ = self._run_with_deps("collide", "elab.json: elab.ys work/src/0000_top.v /elsewhere/header.vh\n")
        self.assertEqual(stage.status, StageStatus.FAILED)
        self.assertEqual(stage.failure_kind, "configuration")
        self.assertIn("undeclared dependency: /elsewhere/header.vh", stage.checks[0].message)

    def test_staged_include_is_a_declared_dependency(self):
        stage, run_dir = self._run_with_deps("ok", "elab.json: elab.ys work/src/0000_top.v work/inc/0000/header.vh\n")
        self.assertEqual(stage.status, StageStatus.SUCCEEDED, stage.checks)
        design = json.loads((run_dir / "stages/elaboration/design.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(Path(p).name for p in design["discovered_dependencies"]), ["header.vh", "top.v"])


if __name__ == "__main__":
    unittest.main()
