"""Regression checks for snapshot isolation and complete run evidence."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chipflow.cli import main, snapshot_project, write_run_manifest
from chipflow.config import load_project
from chipflow.runner.execution import sha256_file
from chipflow.runner.results import CheckStatus
from chipflow.runner.snapshot import load_snapshot_manifest
from chipflow.technologies.profile import load_profile
from chipflow.toolchain import load_toolchain
from chipflow.validation import validate_project_configuration
from test_pads import make_s8_design
import test_toolchain_cli as cli_fixtures


class SnapshotIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="chipflow snapshot ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project_file = cli_fixtures.ToolchainTests._setup_valid_s8_project(self, self.root / "project")
        self.project = load_project(self.project_file)
        self.run = self.project.build_root / "snapshot-test"
        self.run.mkdir(parents=True)
        snapshot_project(self.project, self.run)
        self.snap_pads = self.run / "inputs/project/pads.toml"

    def validate(self):
        return validate_project_configuration(self.project, self.run, make_s8_design(), load_profile())

    def test_live_pad_edits_and_deletion_do_not_change_validation(self):
        original_hash = sha256_file(self.snap_pads)
        self.project.pads.write_text("invalid live pad map", encoding="utf-8")
        check, _ = self.validate()
        self.assertEqual(check.status, CheckStatus.PASS)
        self.assertEqual(check.input_hashes["pad_map"], original_hash)
        self.project.pads.unlink()
        check, _ = self.validate()
        self.assertEqual(check.status, CheckStatus.PASS)

    def test_missing_or_changed_snapshot_cannot_fall_back_to_valid_live_pads(self):
        original = self.snap_pads.read_bytes()
        for action in ("missing", "changed"):
            with self.subTest(action=action):
                self.snap_pads.write_bytes(original)
                if action == "missing":
                    self.snap_pads.unlink()
                else:
                    self.snap_pads.write_text("invalid snapshot", encoding="utf-8")
                with self.assertRaises((OSError, RuntimeError)):
                    self.validate()

    def test_invalid_snapshotted_pad_schema_is_reported_even_if_live_map_is_fixed(self):
        valid = self.project.pads.read_text(encoding="utf-8")
        self.project.pads.write_text("unknown_key = 123\n" + valid, encoding="utf-8")
        snapshot_project(self.project, self.run)
        self.project.pads.write_text(valid, encoding="utf-8")
        check, rows = self.validate()
        self.assertEqual(check.status, CheckStatus.FAIL)
        schema = next(row for row in rows if row.id == "pads.schema")
        self.assertIn("unknown keys in pad map", schema.message)
        self.assertFalse((self.run / "pad-plan.json").exists())

    def test_pad_plan_is_hashed_in_configuration_evidence(self):
        check, _ = self.validate()
        artifacts = {artifact.path: artifact.sha256 for artifact in check.evidence}
        self.assertEqual(artifacts["pad-plan.json"], sha256_file(self.run / "pad-plan.json"))
        plan = json.loads((self.run / "pad-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(len(plan["pads"]), 16)
        self.assertTrue(all(p["offset_um"] is None for p in plan["pads"] if p["kind"] == "power"))

    def test_missing_configuration_or_input_manifest_never_gets_a_placeholder_hash(self):
        for filename in ("resolved-project.json", "input-manifest.json"):
            with self.subTest(filename=filename):
                snapshot_project(self.project, self.run)
                (self.run / filename).unlink()
                with self.assertRaises(OSError):
                    self.validate()
        toolchain = load_toolchain(cli_fixtures.toolchain_file(self.root))
        with self.assertRaises(OSError):
            write_run_manifest(self.run, self.project, toolchain, "start", "end", [])
        self.assertFalse((self.run / "run-manifest.json").exists())

    def test_manifest_rejects_omitted_duplicate_and_escaping_inputs(self):
        manifest_file = self.run / "input-manifest.json"
        rows = json.loads(manifest_file.read_text(encoding="utf-8"))
        for bad in (rows[:-1], rows + [rows[0]], [{**r, "snapshot": "../escape"} for r in rows]):
            with self.subTest(manifest=bad):
                manifest_file.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_snapshot_manifest(self.run, self.project)

    def test_unexpected_cli_error_returns_infrastructure_and_preserves_run_evidence(self):
        local = cli_fixtures.toolchain_file(self.root)
        for command in ("validate", "build"):
            with self.subTest(command=command), patch("chipflow.cli.doctor", side_effect=RuntimeError("unexpected adapter error")), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
                code = main([command, str(self.project_file), "--toolchain", str(local)])
                self.assertEqual(code, 2)
                self.assertIn("unexpected adapter error", errors.getvalue())
        results = [json.loads(p.read_text(encoding="utf-8")) for p in self.project.build_root.glob("run-*/result.json")]
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result["exit_code"], 2)
            self.assertFalse(result["complete"])
            self.assertIn("RuntimeError", result["error"])

    def test_cli_result_includes_hashed_pad_plan(self):
        local = cli_fixtures.toolchain_file(self.root)
        helpers = cli_fixtures.ToolchainTests()
        with patch("chipflow.cli.doctor", side_effect=helpers._fake_passing_doctor), \
                patch("chipflow.cli.elaboration_stage", side_effect=helpers._fake_passing_elaboration()), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["validate", str(self.project_file), "--toolchain", str(local)]), 0)
        run, = self.project.build_root.glob("run-*")
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        stage = next(s for s in result["stages"] if s["id"] == "configuration")
        evidence = {a["path"]: a["sha256"] for a in stage["artifacts"]}
        self.assertEqual(evidence["pad-plan.json"], sha256_file(run / "pad-plan.json"))

    def test_unqualified_pad_blocks_implementation_and_retains_not_run(self):
        local = cli_fixtures.toolchain_file(self.root)
        pads = self.project.pads.read_text(encoding="utf-8")
        self.project.pads.write_text(pads.replace(
            'slot = 12, port = "gpi[2]", pad_cell = "IPAD2D"',
            'slot = 12, port = "gpi[2]", pad_cell = "IPAD30"'), encoding="utf-8")
        helpers = cli_fixtures.ToolchainTests()
        with patch("chipflow.cli.doctor", side_effect=helpers._fake_passing_doctor), \
                patch("chipflow.cli.elaboration_stage", side_effect=helpers._fake_passing_elaboration()), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["build", str(self.project_file), "--toolchain", str(local)]), 2)
        run, = self.project.build_root.glob("run-*")
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        stages = {s["id"]: s for s in result["stages"]}
        self.assertEqual(stages["configuration"]["status"], "failed")
        self.assertEqual(stages["configuration"]["failure_kind"], "infrastructure")
        self.assertEqual(stages["configuration"]["checks"][0]["status"], "NOT RUN")
        self.assertEqual(stages["implementation"]["status"], "blocked")
        self.assertIn("configuration", stages["implementation"]["failed_ancestors"])
        rows = json.loads((run / "validation.json").read_text(encoding="utf-8"))["rows"]
        self.assertEqual(next(r for r in rows if r["id"] == "pads.qualification")["status"], "NOT RUN")


if __name__ == "__main__":
    unittest.main()
