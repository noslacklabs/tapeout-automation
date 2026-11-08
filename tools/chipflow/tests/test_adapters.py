from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from chipflow.runner.execution import (
    Assessment,
    StageSpec,
    execute_stages,
    sha256_file,
)
from chipflow.runner.results import (
    Artifact,
    BuildResult,
    CheckResult,
    CheckStatus,
    MINIMUM_REQUIRED_ARTIFACTS,
    MINIMUM_REQUIRED_CHECKS,
    StageResult,
    StageStatus,
)
from chipflow.stages.envelope import StageEnvelope

DUMMY_HASH = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def make_envelope(stage_id: str) -> StageEnvelope:
    return StageEnvelope(
        stage_id=stage_id,
        adapter={"id": "test.adapter", "version": "0.1.0"},
        inputs=({"id": "dummy", "path": "inputs/dummy.txt", "sha256": DUMMY_HASH, "bytes": 10},),
        tools=({"id": "tool", "executable_sha256": DUMMY_HASH, "version_evidence": "evidence.json"},),
        configuration_sha256=DUMMY_HASH,
        seed=1,
        threads=1,
        outputs=({"id": "out", "path": "out.json", "validator": "json-v1"},),
    )


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chipflow adapters ")
        self.root = Path(self.temp.name) / "run space \u00b5"
        self.root.mkdir()
        self.addCleanup(self.temp.cleanup)

    def test_diagnostic_inputs_outside_dependencies_rejected(self):
        with self.assertRaises(ValueError):
            execute_stages(
                self.root,
                [StageSpec("diag", (sys.executable, "-c", "pass"), diagnostic_inputs=("unknown",))],
            )

    def test_prepare_raises_is_infrastructure_failure_and_blocks_dependents(self):
        def bad_prepare(path: Path):
            raise RuntimeError("prepare crashed")

        stages = [
            StageSpec("prep", (sys.executable, "-c", "pass"), prepare=bad_prepare),
            StageSpec("dep", (sys.executable, "-c", "pass"), dependencies=("prep",)),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["prep"].status, StageStatus.FAILED)
        self.assertEqual(by_id["prep"].failure_kind, "infrastructure")
        self.assertIn("prepare crashed", by_id["prep"].error or "")
        # No command executed for prep
        self.assertEqual(len(summary.commands), 0)
        self.assertEqual(by_id["dep"].status, StageStatus.BLOCKED)
        self.assertEqual(by_id["dep"].failed_ancestors, ("prep",))

    def test_stage_input_written_and_valid_before_command(self):
        recorded_input = {}

        def good_prepare(path: Path) -> StageEnvelope:
            return make_envelope("stage1")

        cmd = (
            sys.executable,
            "-c",
            "from pathlib import Path; import json; out = Path('stage-input.json'); Path('out.txt').write_text(out.read_text())",
        )
        stages = [
            StageSpec(
                "stage1",
                cmd,
                prepare=good_prepare,
                required_outputs=("out.txt",),
            ),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["stage1"].status, StageStatus.SUCCEEDED)
        input_file = self.root / "stages/stage1/stage-input.json"
        self.assertTrue(input_file.is_file())
        data = json.loads(input_file.read_text(encoding="utf-8"))
        self.assertEqual(data["stage_id"], "stage1")
        self.assertEqual(data["schema_version"], 1)

    def test_assess_returns_fail_keeps_artifacts_and_blocks_normal_dependent(self):
        evidence_hash = DUMMY_HASH

        def assess_hook(stage_dir: Path, cmd, artifacts):
            check = CheckResult("design.rule", CheckStatus.FAIL, evidence=artifacts, input_hashes={"in": DUMMY_HASH})
            return Assessment(checks=(check,), failure_kind="design")

        cmd = (sys.executable, "-c", "from pathlib import Path; Path('out.txt').write_text('content')")
        stages = [
            StageSpec("producer", cmd, required_outputs=("out.txt",), assess=assess_hook),
            StageSpec("consumer", (sys.executable, "-c", "pass"), dependencies=("producer",)),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["producer"].status, StageStatus.FAILED)
        self.assertEqual(by_id["producer"].failure_kind, "design")
        self.assertEqual(len(by_id["producer"].artifacts), 1)
        self.assertEqual(by_id["consumer"].status, StageStatus.BLOCKED)
        self.assertEqual(by_id["consumer"].failed_ancestors, ("producer",))

    def test_diagnostic_consumer_runs_after_design_fail_and_propagates_provenance(self):
        def assess_producer(stage_dir: Path, cmd, artifacts):
            check = CheckResult("drc", CheckStatus.FAIL, evidence=artifacts, input_hashes={"in": DUMMY_HASH})
            return Assessment(checks=(check,), failure_kind="design")

        def assess_diagnostic(stage_dir: Path, cmd, artifacts):
            check = CheckResult("timing", CheckStatus.PASS, evidence=artifacts, input_hashes={"in": DUMMY_HASH})
            return Assessment(checks=(check,))

        producer_cmd = (sys.executable, "-c", "from pathlib import Path; Path('spef.txt').write_text('raw spef')")
        diag_cmd = (sys.executable, "-c", "from pathlib import Path; Path('report.txt').write_text('diag timing')")
        child_cmd = (sys.executable, "-c", "from pathlib import Path; Path('final.txt').write_text('done')")

        stages = [
            StageSpec("producer", producer_cmd, required_outputs=("spef.txt",), assess=assess_producer),
            StageSpec(
                "diagnostic",
                diag_cmd,
                dependencies=("producer",),
                diagnostic_inputs=("producer",),
                required_outputs=("report.txt",),
                assess=assess_diagnostic,
            ),
            StageSpec(
                "child",
                child_cmd,
                dependencies=("diagnostic",),
                required_outputs=("final.txt",),
            ),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["producer"].status, StageStatus.FAILED)
        self.assertEqual(by_id["producer"].failure_kind, "design")

        self.assertEqual(by_id["diagnostic"].status, StageStatus.SUCCEEDED)
        self.assertEqual(by_id["diagnostic"].failed_ancestors, ("producer",))

        self.assertEqual(by_id["child"].status, StageStatus.SUCCEEDED)
        self.assertEqual(by_id["child"].failed_ancestors, ("producer",))

        # Check BuildResult exit code with this design failure + passing diagnostic
        passing_checks = tuple(
            CheckResult(c, CheckStatus.PASS, evidence=by_id["diagnostic"].artifacts, input_hashes={"in": DUMMY_HASH})
            for c in MINIMUM_REQUIRED_CHECKS
            if c != "drc"
        )
        all_stages = (
            by_id["producer"],
            StageResult(
                "diag_and_others",
                StageStatus.SUCCEEDED,
                checks=passing_checks + by_id["diagnostic"].checks,
                artifacts=tuple(Artifact(p, DUMMY_HASH) for p in MINIMUM_REQUIRED_ARTIFACTS),
                failed_ancestors=("producer",),
            ),
        )
        build_result = BuildResult(all_stages)
        self.assertEqual(build_result.exit_code, 1)

    def test_producer_crash_leaves_diagnostic_consumer_blocked(self):
        producer_cmd = (sys.executable, "-c", "raise SystemExit(1)")
        stages = [
            StageSpec("producer", producer_cmd),
            StageSpec("diagnostic", (sys.executable, "-c", "pass"), dependencies=("producer",), diagnostic_inputs=("producer",)),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["producer"].status, StageStatus.FAILED)
        self.assertEqual(by_id["producer"].failure_kind, "infrastructure")
        self.assertEqual(by_id["diagnostic"].status, StageStatus.BLOCKED)
        self.assertEqual(by_id["diagnostic"].failed_ancestors, ("producer",))

    def test_producer_invalid_output_leaves_diagnostic_consumer_blocked(self):
        producer_cmd = (sys.executable, "-c", "from pathlib import Path; Path('out.txt').write_text('')")
        stages = [
            StageSpec("producer", producer_cmd, required_outputs=("out.txt",)),
            StageSpec("diagnostic", (sys.executable, "-c", "pass"), dependencies=("producer",), diagnostic_inputs=("producer",)),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["producer"].status, StageStatus.FAILED)
        self.assertEqual(by_id["producer"].failure_kind, "infrastructure")
        self.assertEqual(by_id["diagnostic"].status, StageStatus.BLOCKED)

    def test_non_diagnostic_consumer_is_blocked_on_design_failure(self):
        def assess_producer(stage_dir: Path, cmd, artifacts):
            check = CheckResult("drc", CheckStatus.FAIL, evidence=artifacts, input_hashes={"in": DUMMY_HASH})
            return Assessment(checks=(check,), failure_kind="design")

        producer_cmd = (sys.executable, "-c", "from pathlib import Path; Path('out.txt').write_text('data')")
        stages = [
            StageSpec("producer", producer_cmd, required_outputs=("out.txt",), assess=assess_producer),
            StageSpec("consumer", (sys.executable, "-c", "pass"), dependencies=("producer",)),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["consumer"].status, StageStatus.BLOCKED)

    def test_assess_raising_is_infrastructure_failure(self):
        def bad_assess(stage_dir: Path, cmd, artifacts):
            raise RuntimeError("crash in assess")

        cmd = (sys.executable, "-c", "from pathlib import Path; Path('out.txt').write_text('data')")
        stages = [
            StageSpec("stage", cmd, required_outputs=("out.txt",), assess=bad_assess),
        ]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id["stage"].status, StageStatus.FAILED)
        self.assertEqual(by_id["stage"].failure_kind, "infrastructure")
        self.assertIn("assessment failed", by_id["stage"].error or "")

    def _run_with_assessment(self, assessment: Assessment):
        cmd = (sys.executable, "-c", "from pathlib import Path; Path('out.txt').write_text('data')")
        stages = [
            StageSpec("stage", cmd, required_outputs=("out.txt",), assess=lambda d, c, a: assessment),
            StageSpec("dep", (sys.executable, "-c", "pass"), dependencies=("stage",)),
        ]
        return {s.id: s for s in execute_stages(self.root, stages).stages}

    def test_assess_infrastructure_verdict_is_never_ignored_or_downgraded(self):
        evidence = Artifact("x.json", DUMMY_HASH)
        fail = CheckResult("c", CheckStatus.FAIL, "unusable report", evidence=(evidence,), input_hashes={"i": DUMMY_HASH})
        for checks in ((), (fail,)):
            with self.subTest(checks=len(checks)):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                by_id = self._run_with_assessment(Assessment(checks=checks, failure_kind="infrastructure"))
                self.assertEqual(by_id["stage"].status, StageStatus.FAILED)
                self.assertEqual(by_id["stage"].failure_kind, "infrastructure")
                self.assertEqual(by_id["dep"].status, StageStatus.BLOCKED)

    def test_assess_design_kind_without_fail_check_is_infrastructure(self):
        by_id = self._run_with_assessment(Assessment(failure_kind="design"))
        self.assertEqual(by_id["stage"].status, StageStatus.FAILED)
        self.assertEqual(by_id["stage"].failure_kind, "infrastructure")
        self.assertIn("without a FAIL check", by_id["stage"].error or "")

    def test_prepare_must_return_an_envelope(self):
        stages = [StageSpec("prep", (sys.executable, "-c", "pass"), prepare=lambda d: None)]
        summary = execute_stages(self.root, stages)
        self.assertEqual(summary.stages[0].status, StageStatus.FAILED)
        self.assertEqual(summary.stages[0].failure_kind, "infrastructure")
        self.assertEqual(len(summary.commands), 0)


if __name__ == "__main__":
    unittest.main()
