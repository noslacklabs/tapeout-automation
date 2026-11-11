import json
from pathlib import Path
import sys
import tempfile
import unittest

from chipflow.runner.execution import StageSpec, execute_stages, sha256_file
from chipflow.runner.results import Artifact, StageResult, StageStatus
from chipflow.stages.staging import copy_verified_dependency


class ProducerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="identity-")
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.source = self.run / "stages/synthesis/mapped.v"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("verified netlist", encoding="utf-8")
        self.producer = StageResult("synthesis", StageStatus.SUCCEEDED,
            artifacts=(Artifact("stages/synthesis/mapped.v", sha256_file(self.source)),))
        self.output = self.run / "prepared"
        self.output.mkdir()
        self.record()

    def record(self):
        artifact = self.producer.artifacts[0]
        (self.run / "execution.json").write_text(json.dumps({"stages": [
            {"id": "synthesis", "status": "succeeded",
             "artifacts": [{"path": artifact.path, "sha256": artifact.sha256}]}]}), encoding="utf-8")

    def test_verified_copy_records_the_emitted_hash(self):
        row = copy_verified_dependency(self.run, self.output, "synthesis", "mapped.v", "mapped.v")
        self.assertEqual(row["sha256"], self.producer.artifacts[0].sha256)

    def test_changed_missing_or_unrecorded_producer_cannot_be_copied(self):
        for mutation in ("change", "delete", "unrecord"):
            with self.subTest(mutation=mutation):
                self.source.write_text("verified netlist", encoding="utf-8")
                self.record()
                if mutation == "change":
                    self.source.write_text("changed netlist", encoding="utf-8")
                elif mutation == "delete":
                    self.source.unlink()
                else:
                    (self.run / "execution.json").unlink()
                with self.assertRaises(RuntimeError):
                    copy_verified_dependency(self.run, self.output, "synthesis", "mapped.v", "mapped.v")

    def test_runner_checks_producer_even_without_adapter_prepare(self):
        self.source.write_text("changed netlist", encoding="utf-8")
        consumer = StageSpec("pnr", (sys.executable, "-c", "raise RuntimeError('must not run')"),
                             dependencies=("synthesis",))
        result = execute_stages(self.run, [consumer], existing_stages=[self.producer])
        self.assertEqual(len(result.commands), 0)
        self.assertEqual(result.stages[0].status, StageStatus.FAILED)
        self.assertIn("hash mismatch", result.stages[0].error)
