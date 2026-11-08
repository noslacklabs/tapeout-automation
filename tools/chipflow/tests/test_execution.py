from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from chipflow.runner.execution import create_run, execute_stages, run_command, StageSpec, validate_build_root
from chipflow.runner.results import StageStatus


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chipflow executor ')
        self.root = Path(self.temp.name) / 'space \u00b5'
        self.root.mkdir()
        self.addCleanup(self.temp.cleanup)

    def test_arguments_logs_and_nonzero_exit(self):
        message = 'space \u00b5 $literal ; literal'
        result = run_command([sys.executable, '-c', 'import sys; print(sys.argv[1]); print("oops",file=sys.stderr); sys.exit(7)', message], self.root, env={'PYTHONUTF8': '1'})
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.succeeded)
        self.assertEqual(Path(result.stdout_log).read_text(encoding='utf-8').strip(), message)
        self.assertIn('oops', Path(result.stderr_log).read_text())
        self.assertEqual(json.loads((self.root / 'command.json').read_text(encoding='utf-8'))['argv'][-1], message)

    def test_missing_tool_recorded(self):
        result = run_command([str(self.root / 'missing-tool')], self.root)
        self.assertFalse(result.succeeded)
        self.assertIsNotNone(result.error)
        self.assertTrue((self.root / 'command.result.json').is_file())

    def test_failed_dependency_blocks_but_independent_stage_runs(self):
        stages = [StageSpec('fail', (sys.executable, '-c', 'raise SystemExit(3)')),
                  StageSpec('dependent', (sys.executable, '-c', 'raise SystemExit(0)'), dependencies=('fail',)),
                  StageSpec('independent', (sys.executable, '-c', 'from pathlib import Path; Path("out.json").write_text("{}")'), required_outputs=('out.json',))]
        summary = execute_stages(self.root, stages)
        by_id = {s.id: s for s in summary.stages}
        self.assertEqual(by_id['fail'].status, StageStatus.FAILED)
        self.assertEqual(by_id['dependent'].status, StageStatus.BLOCKED)
        self.assertEqual(by_id['dependent'].failed_ancestors, ('fail',))
        self.assertEqual(by_id['independent'].status, StageStatus.SUCCEEDED)

    def test_old_output_cannot_satisfy_new_stage(self):
        (self.root / 'report.json').write_text('{"old": true}')
        summary = execute_stages(self.root, [StageSpec('check', (sys.executable, '-c', 'pass'), required_outputs=('report.json',))])
        self.assertEqual(summary.stages[0].status, StageStatus.FAILED)
        self.assertIn('missing', summary.stages[0].error)

    def test_truncated_report_is_execution_failure(self):
        def validate(path):
            data = json.loads(path.read_text())
            if not data.get('categories'):
                raise ValueError('missing expected rule categories')
        summary = execute_stages(self.root, [StageSpec('drc', (sys.executable, '-c', 'from pathlib import Path; Path("report.json").write_text("{}")'),
            required_outputs=('report.json',), validators={'report.json': validate})])
        self.assertEqual(summary.stages[0].failure_kind, 'infrastructure')
        self.assertIn('categories', summary.stages[0].error)

    def test_timeout_terminates_process_tree(self):
        # A surviving child would create the marker after the timed-out parent dies.
        marker = self.root / 'orphan.txt'
        child = 'import time; from pathlib import Path; time.sleep(1); Path(' + repr(str(marker)) + ').write_text("orphan")'
        parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(20)'
        result = run_command([sys.executable, '-c', parent], self.root, timeout_s=0.35)
        self.assertTrue(result.timed_out)
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_cancellation_preserves_evidence_and_stops_descendants(self):
        event = threading.Event()
        timer = threading.Timer(0.25, event.set)
        timer.start()
        self.addCleanup(timer.cancel)
        summary = execute_stages(self.root, [StageSpec('long', (sys.executable, '-c', 'import time;time.sleep(10)')),
            StageSpec('next', (sys.executable, '-c', 'pass'), dependencies=('long',))], event)
        self.assertTrue(summary.cancelled)
        self.assertEqual([s.status for s in summary.stages], [StageStatus.CANCELLED, StageStatus.CANCELLED])
        self.assertFalse(json.loads((self.root / 'execution.json').read_text())['complete'])

    def test_concurrent_runs_are_disjoint_and_prior_evidence_retained(self):
        def make_run(_):
            with create_run(self.root / 'runs') as run:
                self.assertTrue((run.path / '.active.lock').exists())
                (run.path / 'marker').write_text('kept')
                return run.path
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(make_run, range(8)))
        self.assertEqual(len(set(paths)), 8)
        self.assertTrue(all((p / 'marker').is_file() and not (p / '.active.lock').exists() for p in paths))

    def test_unsafe_roots_cycles_and_reuse_rejected(self):
        for root, protected in [(self.root, (self.root,)), (self.root / 'inside', (self.root,)),
                                (self.root, (self.root / 'source.v',)), (Path(self.root.anchor), ())]:
            with self.subTest(root=root):
                with self.assertRaises(ValueError):
                    validate_build_root(root, protected)
        with self.assertRaises(ValueError):
            execute_stages(self.root, [StageSpec('a', ('a',), dependencies=('b',)), StageSpec('b', ('b',), dependencies=('a',))])
        with self.assertRaises(ValueError):
            execute_stages(self.root, [StageSpec('a', ('a',), required_outputs=('../report',))])
        run_command([sys.executable, '-c', 'pass'], self.root)
        with self.assertRaises(FileExistsError):
            run_command([sys.executable, '-c', 'pass'], self.root)

    def test_invalid_later_stage_rejected_before_any_commands_run(self):
        for invalid in [StageSpec('later', ('bad',), timeout_s=0),
                        StageSpec('later', 'shell text'),
                        StageSpec('later', ('bad',), required_outputs=('C:\\escape',))]:
            with self.subTest(stage=invalid):
                with self.assertRaises(ValueError):
                    execute_stages(self.root, [StageSpec('first', (sys.executable, '-c', 'pass')), invalid])
                self.assertFalse((self.root / 'stages').exists())
        with self.assertRaises(ValueError):
            execute_stages(self.root, [])


if __name__ == '__main__':
    unittest.main()
