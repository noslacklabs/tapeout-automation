from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from chipflow.config import ConfigError, load_project
from chipflow.runner.results import (
    Artifact, BuildResult, CheckResult, CheckStatus, StageResult, StageStatus,
    MINIMUM_REQUIRED_CHECKS, MINIMUM_REQUIRED_ARTIFACTS,
)

HASH = 'a' * 64
EVIDENCE = Artifact('report.json', HASH)
PROJECT = '''schema_version = 1
[project]
name = "counter"
top = "counter"
sources = ["counter.v"]
include_dirs = ["include"]
defines = ["WIDTH=4"]
parameters = { WIDTH = 4 }
[technology]
id = "scl_c1d"
frame = "scl-c1d-2mm"
profile_version = "0.1.0"
top_cell = "OS0000"
mode = "development"
development_identifiers = true
[clock]
port = "tick"
period_ns = 100.0
waveform_ns = [0.0, 50.0]
transition_ns = 1.0
uncertainty_ns = 0.5
[reset]
port = "reset_high"
kind = "synchronous"
active = "high"
[io]
corners = ["min", "typ", "max"]
inputs = [{ port = "reset_high", min_ns = 1.0, max_ns = 3.0 }]
outputs = [{ port = "value[0]", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }]
[pads]
mapping = "pads.toml"
[build]
root = "builds"
seed = 7
threads = 1
timeout_seconds = 60
[verification]
required_checks = ["counter.behavior"]
optional_checks = ["formal"]
manual_items = ["Confirm board loads"]
data = ["expected.json"]
'''


def write_project(root: Path, text: str = PROJECT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'counter.v').write_text('module counter(input tick, reset_high, output [3:0] value); endmodule\n')
    (root / 'pads.toml').write_text('qualification = "NOT RUN"\n')
    (root / 'expected.json').write_text('[0,1,2,3]\n')
    (root / 'include').mkdir(exist_ok=True)
    (root / 'include/width.vh').write_text('`define WIDTH 4\n')
    (root / 'chip.toml').write_text(text, encoding='utf-8')
    return root / 'chip.toml'


def passed(check_id: str) -> CheckResult:
    return CheckResult(check_id, CheckStatus.PASS, evidence=(EVIDENCE,), input_hashes={'input': HASH})


def passing_result() -> BuildResult:
    return BuildResult((StageResult('verify', StageStatus.SUCCEEDED,
        checks=tuple(passed(c) for c in MINIMUM_REQUIRED_CHECKS),
        artifacts=tuple(Artifact(p, HASH) for p in MINIMUM_REQUIRED_ARTIFACTS)),))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chipflow tests ')
        self.root = Path(self.temp.name) / 'space \u00b5'
        self.addCleanup(self.temp.cleanup)

    def test_paths_dependencies_and_minimum_resolve(self):
        path = write_project(self.root)
        project = load_project(path)
        self.assertEqual(project.sources, ((self.root / 'counter.v').resolve(),))
        self.assertIn((self.root / 'include/width.vh').resolve(), project.dependencies)
        self.assertIn((self.root / 'expected.json').resolve(), project.dependencies)
        self.assertEqual(project.clock.port, 'tick')
        self.assertTrue(set(MINIMUM_REQUIRED_CHECKS) <= set(project.verification.required_checks))
        self.assertTrue(project.remaining_checks)
        json.dumps(project.to_dict(), allow_nan=False)

    def test_invalid_inputs_fail_early(self):
        variants = [
            PROJECT.replace('schema_version = 1', 'schema_version = true'),
            PROJECT.replace('period_ns = 100.0', 'period_ns = nan'),
            PROJECT.replace('period_ns = 100.0', 'period_ns = 0'),
            PROJECT.replace('waveform_ns = [0.0, 50.0]', 'waveform_ns = [50.0, 1.0]'),
            PROJECT.replace('kind = "synchronous"', 'kind = "asynchronous"'),
            PROJECT.replace('sources = ["counter.v"]', 'sources = ["../outside.v"]'),
            PROJECT.replace('root = "builds"', 'root = "."'),
            PROJECT.replace('root = "builds"', 'root = "include/builds"'),
            PROJECT.replace('threads = 1', 'threads = true'),
            PROJECT.replace('max_ns = 3.0', 'max_ns = 0.0'),
            PROJECT.replace('corners = ["min", "typ", "max"]', 'corners = ["typ"]'),
            PROJECT.replace('optional_checks = ["formal"]', 'optional_checks = ["drc"]'),
            PROJECT.replace('top = "counter"', 'top = "counter"\nunknown = 1'),
            PROJECT.replace('parameters = { WIDTH = 4 }', 'parameters = { WIDTH = true }'),
            PROJECT.replace('load_pf = 10.0', 'load_pf = inf'),
        ]
        for text in variants:
            with self.subTest(text=text):
                with self.assertRaises(ConfigError):
                    load_project(write_project(self.root, text))

    def test_snapshot_captures_declared_dependencies(self):
        from chipflow.cli import snapshot_project
        from chipflow.runner.execution import sha256_file
        project = load_project(write_project(self.root))
        run = self.root / 'builds/run-one'
        run.mkdir(parents=True)
        artifact = snapshot_project(project, run)
        rows = json.loads((run / artifact.path).read_text())
        self.assertEqual(len(rows), len(project.dependencies))
        for row in rows:
            self.assertEqual(sha256_file(run / row['snapshot']), row['sha256'])


class ResultTests(unittest.TestCase):
    def test_positive_full_evidence_exits_zero_without_signoff(self):
        result = passing_result()
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.to_dict()['signoff_ready'])
        json.dumps(result.to_dict())

    def test_known_extraction_failure_is_exit_one(self):
        result = passing_result()
        stage = result.stages[0]
        checks = tuple(replace(c, status=CheckStatus.FAIL) if c.id == 'parasitic_validation' else c for c in stage.checks)
        result = replace(result, stages=(replace(stage, checks=checks),))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.to_dict()['automated_verification'], 'FAIL')

    def test_missing_or_infrastructure_evidence_is_exit_two(self):
        result = passing_result()
        stage = result.stages[0]
        variants = [replace(result, stages=()),
                    replace(result, stages=(replace(stage, checks=stage.checks[:-1]),)),
                    replace(result, stages=(replace(stage, artifacts=()),)),
                    replace(result, stages=(stage, StageResult('crash', StageStatus.FAILED, failure_kind='infrastructure'))),
                    replace(result, stages=(replace(stage, checks=stage.checks + (stage.checks[0],)),))]
        for variant in variants:
            self.assertEqual(variant.exit_code, 2)
            self.assertFalse(variant.to_dict()['complete'])

    def test_project_cannot_remove_profile_minimum(self):
        result = BuildResult((StageResult('empty', StageStatus.SUCCEEDED),), required_checks=(), required_artifacts=())
        self.assertEqual(result.required_checks, MINIMUM_REQUIRED_CHECKS)
        self.assertEqual(result.exit_code, 2)

    def test_optional_executed_fail_is_still_failure(self):
        result = passing_result()
        check = CheckResult('extra', CheckStatus.FAIL, evidence=(EVIDENCE,), input_hashes={'input': HASH})
        self.assertEqual(replace(result, stages=(*result.stages, StageResult('optional', StageStatus.SUCCEEDED, checks=(check,)))).exit_code, 1)

    def test_infrastructure_precedes_design_and_cancel_precedes_both(self):
        result = passing_result()
        fail = CheckResult('extra', CheckStatus.FAIL, evidence=(EVIDENCE,), input_hashes={'input': HASH})
        result = replace(result, stages=(*result.stages, StageResult('design', StageStatus.FAILED, checks=(fail,), failure_kind='design'),
                                         StageResult('crash', StageStatus.FAILED, failure_kind='infrastructure')))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(replace(result, cancelled=True).exit_code, 130)

    def test_configuration_failure_rules(self):
        result = passing_result()
        config_fail = CheckResult('config_fail', CheckStatus.FAIL, evidence=(EVIDENCE,), input_hashes={'input': HASH})
        config_stage = StageResult('config', StageStatus.FAILED, checks=(config_fail,), failure_kind='configuration')
        self.assertEqual(replace(result, stages=(*result.stages, config_stage)).exit_code, 2)

        design_fail = CheckResult('design_fail', CheckStatus.FAIL, evidence=(EVIDENCE,), input_hashes={'input': HASH})
        design_stage = StageResult('design', StageStatus.FAILED, checks=(design_fail,), failure_kind='design')
        self.assertEqual(replace(result, stages=(*result.stages, config_stage, design_stage)).exit_code, 2)
        self.assertEqual(replace(result, stages=(*result.stages, config_stage), cancelled=True).exit_code, 130)

        # Inconsistent provenance: failed_ancestors present without any FAIL check anywhere
        blocked = StageResult('blocked', StageStatus.BLOCKED, failed_ancestors=('upstream',))
        self.assertEqual(replace(result, stages=(*result.stages, blocked)).exit_code, 2)

    def test_invalid_or_unattributed_results_rejected(self):
        with self.assertRaises(ValueError):
            CheckResult('empty', CheckStatus.PASS)
        with self.assertRaises(ValueError):
            CheckResult('na', CheckStatus.NOT_APPLICABLE)
        with self.assertRaises(ValueError):
            StageResult('crash', StageStatus.FAILED, failure_kind='design')
        with self.assertRaises(ValueError):
            StageResult('cfg', StageStatus.FAILED, failure_kind='configuration')
        with self.assertRaises(ValueError):
            Artifact('a', 'invalid')


if __name__ == '__main__':
    unittest.main()
