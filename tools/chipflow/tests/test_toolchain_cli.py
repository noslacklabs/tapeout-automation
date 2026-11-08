from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch

from chipflow.cli import active_run, main
from chipflow.runner.execution import atomic_write_json, sha256_file
from chipflow.toolchain import doctor, load_toolchain, REQUIRED_PDK_FILES, REQUIRED_TOOLS, ToolchainError, verify_file
from test_contracts import write_project


def toolchain_file(root: Path) -> Path:
    q = json.dumps
    pdk = root / 'pdk'
    pdk.mkdir()
    (pdk / 'data.bin').write_bytes(b'synthetic PDK fingerprint fixture')
    os_info = platform.freedesktop_os_release() if platform.system() == 'Linux' else {}
    environment = dict(system=platform.system(), machine=platform.machine(), os_id=os_info.get('ID', 'fixture'),
                       os_version=os_info.get('VERSION_ID', 'fixture'), python_version=platform.python_version())
    lines = ['schema_version = 1', '[environment]']
    lines += [k + ' = ' + q(v) for k, v in environment.items()]
    lines += ['[python_packages]', 'klayout = "synthetic"', 'gdstk = "synthetic"']
    for name in sorted(REQUIRED_TOOLS):
        lines += [f'[tools.{name}]', 'executable = ' + q(sys.executable), 'version_args = ["--version"]',
                  'version_contains = "Python"', 'sha256 = ' + q(sha256_file(Path(sys.executable)))]
    lines += ['[pdk]', 'root = "pdk"']
    for name in sorted(REQUIRED_PDK_FILES):
        lines += ['[[pdk.files]]', 'id = ' + q(name), 'path = "data.bin"', 'sha256 = ' + q(sha256_file(pdk / 'data.bin'))]
    path = root / 'local.toml'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chipflow toolchain ')
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_fingerprint_drift_detected_before_tool_use(self):
        toolchain = load_toolchain(toolchain_file(self.root))
        item = toolchain.pdk_file('frame_gds')
        verify_file(item.path, item.sha256)
        item.path.write_bytes(b'changed')
        with self.assertRaisesRegex(ToolchainError, 'drift'):
            verify_file(item.path, item.sha256)

    def test_malformed_toolchain_does_not_reach_subprocess(self):
        path = toolchain_file(self.root)
        text = path.read_text(encoding='utf-8')
        for invalid in [text.replace('schema_version = 1', 'schema_version = true'),
                        text.replace('root = "pdk"', 'root = "pdk"\nunknown = 1'),
                        text.replace('path = "data.bin"', 'path = "../escape.bin"'),
                        text.replace('version_args = ["--version"]', 'version_args = "--version"')]:
            path.write_text(invalid, encoding='utf-8')
            with self.assertRaises(ToolchainError):
                load_toolchain(path)

    def test_doctor_reports_missing_packages_and_records_tools(self):
        path = toolchain_file(self.root)
        toolchain = load_toolchain(path)
        with patch('chipflow.toolchain.importlib.metadata.version', return_value='wrong'):
            report = doctor(toolchain, self.root / 'doctor')
        self.assertEqual(report['exit_code'], 2)
        self.assertEqual(report['clean_environment_installation'], 'NOT RUN')
        self.assertTrue(any(c['id'] == 'python.klayout' and c['status'] == 'FAIL' for c in report['checks']))

    def test_structure_only_does_not_claim_full_validation(self):
        project = write_project(self.root / 'project')
        for extra, code in [([], 2), (['--structure-only'], 0)]:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['validate', str(project), *extra]), code)
            result = json.loads(output.getvalue())
            self.assertEqual(result['full_validation'], 'NOT RUN')
            self.assertTrue(result['remaining_checks'])

    def test_build_preflight_never_passes_unimplemented_flow(self):
        project = write_project(self.root / 'project')
        local = toolchain_file(self.root)
        def fake_doctor(toolchain, output):
            output.mkdir()
            report = {'exit_code': 0, 'kind': 'synthetic-doctor'}
            atomic_write_json(output / 'doctor.json', report)
            return report
        with patch('chipflow.cli.doctor', side_effect=fake_doctor), redirect_stdout(io.StringIO()):
            code = main(['build', str(project), '--toolchain', str(local)])
        self.assertEqual(code, 2)
        runs = list((project.parent / 'builds').iterdir())
        self.assertEqual(len(runs), 1)
        report = json.loads((runs[0] / 'result.json').read_text(encoding='utf-8'))
        self.assertFalse(report['complete'])
        self.assertEqual(report['exit_code'], 2)
        self.assertFalse((runs[0] / '.active.lock').exists())
        self.assertTrue((runs[0] / 'inputs/project/counter.v').is_file())

    def test_cancelled_run_gets_final_result(self):
        run_path = None
        with self.assertRaises(KeyboardInterrupt):
            with active_run(self.root / 'runs', (), 'test') as run:
                run_path = run.path
                raise KeyboardInterrupt()
        report = json.loads((run_path / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(report['exit_code'], 130)
        self.assertFalse(report['complete'])
        self.assertFalse((run_path / '.active.lock').exists())

    def test_build_preserves_doctor_cancellation_status(self):
        project = write_project(self.root / 'project')
        local = toolchain_file(self.root)
        def cancelled_doctor(toolchain, output):
            output.mkdir()
            report = {'exit_code': 130}
            atomic_write_json(output / 'doctor.json', report)
            return report
        with patch('chipflow.cli.doctor', side_effect=cancelled_doctor), redirect_stdout(io.StringIO()):
            self.assertEqual(main(['build', str(project), '--toolchain', str(local)]), 130)
        result, = (project.parent / 'builds').glob('*/result.json')
        self.assertEqual(json.loads(result.read_text(encoding='utf-8'))['exit_code'], 130)

    def _setup_valid_s8_project(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'rtl.v').write_text('module s8; endmodule\n', encoding='utf-8')
        (folder / 'pads.toml').write_text("""schema_version = 1
frame = "scl-c1d-2mm"
profile_version = "0.1.0"
slot = [
    { slot = 1, port = "cs_n", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 2, port = "miso", pad_cell = "IPAD2D", driver = "none" },
    { slot = 3, port = "mosi", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 4, port = "clk", pad_cell = "IPAD30", driver = "none" },
    { slot = 5, port = "sck", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 6, port = "rst", pad_cell = "IPAD3D", driver = "none" },
    { slot = 7, port = "gpo[0]", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 8, port = "gpi[0]", pad_cell = "IPAD2D", driver = "none" },
    { slot = 9, port = "gpo[1]", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 10, port = "gpi[1]", pad_cell = "IPAD2D", driver = "none" },
    { slot = 11, port = "gpo[2]", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 12, port = "gpi[2]", pad_cell = "IPAD2D", driver = "none" }
]
""", encoding='utf-8')
        (folder / 'chip.toml').write_text("""schema_version = 1
[project]
name = "s8_valid"
top = "s8"
sources = ["rtl.v"]

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
derived_outputs = [{ port = "sck", check = "s8.sck_gate" }]

[reset]
port = "rst"
kind = "synchronous"
active = "high"

[io]
corners = ["min", "typ", "max"]
inputs = [
    { port = "rst", min_ns = 0.0, max_ns = 0.0 },
    { port = "miso", min_ns = 0.0, max_ns = 0.0 },
    { port = "gpi[0]", min_ns = 0.0, max_ns = 0.0 },
    { port = "gpi[1]", min_ns = 0.0, max_ns = 0.0 },
    { port = "gpi[2]", min_ns = 0.0, max_ns = 0.0 }
]
outputs = [
    { port = "cs_n", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 },
    { port = "mosi", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 },
    { port = "sck", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 },
    { port = "gpo[0]", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 },
    { port = "gpo[1]", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 },
    { port = "gpo[2]", min_ns = 0.0, max_ns = 0.0, load_pf = 30.0 }
]

[pads]
mapping = "pads.toml"

[build]
root = "builds"
seed = 42
threads = 1
timeout_seconds = 30

[verification]
required_checks = ["s8.behavior"]
""", encoding='utf-8')
        return folder / 'chip.toml'

    def _fake_passing_doctor(self, toolchain, output):
        output.mkdir(parents=True, exist_ok=True)
        report = {'exit_code': 0, 'kind': 'synthetic-doctor'}
        atomic_write_json(output / 'doctor.json', report)
        return report

    def _fake_passing_elaboration(self):
        import dataclasses
        from chipflow.stages.elaboration import elaboration_stage as real_elab_stage

        def fake_elab_stage(project, toolchain, run_path):
            spec = real_elab_stage(project, toolchain, run_path)
            fake_py = (
                "import json, pathlib; "
                "pathlib.Path('elab.log').write_text('Elaboration successful\\n', encoding='utf-8'); "
                "pathlib.Path('deps.txt').write_text('elab.json: elab.ys work/src/0000_rtl.v\\n', encoding='utf-8'); "
                "elab_data = {'modules': {'s8': {"
                "'ports': {"
                "'clk': {'direction': 'input', 'bits': [2]}, "
                "'rst': {'direction': 'input', 'bits': [3]}, "
                "'miso': {'direction': 'input', 'bits': [4]}, "
                "'gpi': {'direction': 'input', 'bits': [5, 6, 7]}, "
                "'sck': {'direction': 'output', 'bits': [8]}, "
                "'cs_n': {'direction': 'output', 'bits': [9]}, "
                "'mosi': {'direction': 'output', 'bits': [10]}, "
                "'gpo': {'direction': 'output', 'bits': [11, 12, 13]}"
                "}, "
                "'cells': {"
                "'u_dff': {'type': '$dff', 'parameters': {'CLK_POLARITY': 1}, 'port_directions': {'CLK': 'input', 'D': 'input', 'Q': 'output'}, 'connections': {'CLK': [2], 'D': [3], 'Q': [14]}, 'attributes': {'src': 'rtl.v:10'}}, "
                "'u_or': {'type': '$or', 'parameters': {}, 'port_directions': {'A': 'input', 'B': 'input', 'Y': 'output'}, 'connections': {'A': [2], 'B': [3], 'Y': [8]}, 'attributes': {'src': 'rtl.v:20'}}"
                "}, "
                "'netnames': {}"
                "}}}; "
                "pathlib.Path('elab.json').write_text(json.dumps(elab_data), encoding='utf-8')"
            )
            return dataclasses.replace(spec, command=(sys.executable, "-c", fake_py))
        return fake_elab_stage

    def test_full_validation_passes_and_manifest(self):
        project = self._setup_valid_s8_project(self.root / 'valid_project')
        local = toolchain_file(self.root)
        fake_doctor = self._fake_passing_doctor
        fake_elab = self._fake_passing_elaboration()

        with patch('chipflow.cli.doctor', side_effect=fake_doctor), patch('chipflow.cli.elaboration_stage', side_effect=fake_elab), redirect_stdout(io.StringIO()):
            code = main(['validate', str(project), '--toolchain', str(local)])
        self.assertEqual(code, 0)

        runs = list((project.parent / 'builds').iterdir())
        self.assertEqual(len(runs), 1)
        res_file = runs[0] / 'result.json'
        self.assertTrue(res_file.is_file())
        res = json.loads(res_file.read_text(encoding='utf-8'))
        self.assertEqual(res['exit_code'], 0)
        self.assertTrue(res['complete'])

        manifest_file = runs[0] / 'run-manifest.json'
        self.assertTrue(manifest_file.is_file())
        man = json.loads(manifest_file.read_text(encoding='utf-8'))
        for req_field in ('chipflow_version', 'schema_version', 'profile_version', 'profile_sha256',
                          'argv', 'start_time', 'end_time', 'platform', 'python_version',
                          'toolchain_sha256', 'input_manifest_sha256', 'seed', 'threads',
                          'timeout_seconds', 'environment_overrides', 'environment_snapshot'):
            self.assertIn(req_field, man)

    def test_full_validation_failure_classes(self):
        project = self._setup_valid_s8_project(self.root / 'fail_proj')
        local = toolchain_file(self.root)

        # 1. Doctor fails -> exit 2
        def failing_doctor(tc, out):
            out.mkdir(parents=True, exist_ok=True)
            report = {'exit_code': 2}
            atomic_write_json(out / 'doctor.json', report)
            return report
        with patch('chipflow.cli.doctor', side_effect=failing_doctor), redirect_stdout(io.StringIO()):
            code = main(['validate', str(project), '--toolchain', str(local)])
        self.assertEqual(code, 2)

        # 2. Elaboration fails -> exit 2
        import dataclasses
        from chipflow.stages.elaboration import elaboration_stage as real_elab_stage
        def failing_elab(p, tc, rp):
            spec = real_elab_stage(p, tc, rp)
            fake_py = (
                "import pathlib; "
                "pathlib.Path('elab.log').write_text('ERROR: syntax error in rtl.v\\n', encoding='utf-8'); "
                "import sys; sys.exit(1)"
            )
            return dataclasses.replace(spec, command=(sys.executable, "-c", fake_py))

        with patch('chipflow.cli.doctor', side_effect=self._fake_passing_doctor), patch('chipflow.cli.elaboration_stage', side_effect=failing_elab), redirect_stdout(io.StringIO()):
            code = main(['validate', str(project), '--toolchain', str(local)])
        self.assertEqual(code, 2)

    def test_full_validation_cancellation_gives_130(self):
        project = self._setup_valid_s8_project(self.root / 'cancel_proj')
        local = toolchain_file(self.root)
        def cancel_doc(tc, out):
            out.mkdir(parents=True, exist_ok=True)
            report = {'exit_code': 130}
            atomic_write_json(out / 'doctor.json', report)
            return report
        with patch('chipflow.cli.doctor', side_effect=cancel_doc), redirect_stdout(io.StringIO()):
            code = main(['validate', str(project), '--toolchain', str(local)])
        self.assertEqual(code, 130)

    def test_concurrent_validations_distinct_run_directories(self):
        project = self._setup_valid_s8_project(self.root / 'concurrent_proj')
        local = toolchain_file(self.root)
        fake_doctor = self._fake_passing_doctor
        fake_elab = self._fake_passing_elaboration()

        with patch('chipflow.cli.doctor', side_effect=fake_doctor), patch('chipflow.cli.elaboration_stage', side_effect=fake_elab), redirect_stdout(io.StringIO()):
            code1 = main(['validate', str(project), '--toolchain', str(local)])
            code2 = main(['validate', str(project), '--toolchain', str(local)])
        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        runs = list((project.parent / 'builds').iterdir())
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0], runs[1])

    def test_build_valid_configuration_reports_pass_and_not_run_exits_2(self):
        project = self._setup_valid_s8_project(self.root / 'build_proj')
        local = toolchain_file(self.root)
        fake_doctor = self._fake_passing_doctor
        fake_elab = self._fake_passing_elaboration()

        with patch('chipflow.cli.doctor', side_effect=fake_doctor), patch('chipflow.cli.elaboration_stage', side_effect=fake_elab), redirect_stdout(io.StringIO()):
            code = main(['build', str(project), '--toolchain', str(local)])
        self.assertEqual(code, 2)

        runs = list((project.parent / 'builds').iterdir())
        self.assertEqual(len(runs), 1)
        res_file = runs[0] / 'result.json'
        res = json.loads(res_file.read_text(encoding='utf-8'))
        self.assertEqual(res['exit_code'], 2)
        stage_map = {s['id']: s for s in res['stages']}
        self.assertIn('toolchain', stage_map)
        self.assertIn('elaboration', stage_map)
        self.assertIn('configuration', stage_map)
        self.assertIn('implementation', stage_map)

        self.assertEqual(stage_map['toolchain']['status'], 'succeeded')
        self.assertEqual(stage_map['elaboration']['status'], 'succeeded')
        self.assertEqual(stage_map['configuration']['status'], 'succeeded')
        self.assertEqual(stage_map['implementation']['status'], 'failed')

        impl_checks = stage_map['implementation']['checks']
        self.assertTrue(impl_checks)
        for c in impl_checks:
            self.assertEqual(c['status'], 'NOT RUN')

    def test_input_rejected_before_yosys_is_an_attributed_configuration_fail(self):
        local = toolchain_file(self.root)
        for command in ('validate', 'build'):
            with self.subTest(command=command):
                project = self._setup_valid_s8_project(self.root / f'rejected_{command}')
                text = project.read_text(encoding='utf-8')
                project.write_text(text.replace('sources = ["rtl.v"]', 'sources = ["rtl.v"]\nparameters = { W = -3 }'), encoding='utf-8')
                output = io.StringIO()
                with patch('chipflow.cli.doctor', side_effect=self._fake_passing_doctor), redirect_stdout(output):
                    code = main([command, str(project), '--toolchain', str(local)])
                self.assertEqual(code, 2)
                self.assertIn('must be a nonnegative integer', output.getvalue())
                self.assertIn('configuration: NOT RUN', output.getvalue())
                run, = (project.parent / 'builds').iterdir()
                stages = {s['id']: s for s in json.loads((run / 'result.json').read_text(encoding='utf-8'))['stages']}
                elab = stages['elaboration']
                self.assertEqual((elab['status'], elab['failure_kind']), ('failed', 'configuration'))
                self.assertIn('must be a nonnegative integer', elab['checks'][0]['message'])
                evidence = elab['checks'][0]['evidence'][0]
                self.assertEqual(evidence['path'], 'elaboration-rejected.json')
                self.assertEqual(sha256_file(run / evidence['path']), evidence['sha256'])
                self.assertEqual(stages['configuration']['status'], 'blocked')
                self.assertTrue((run / 'validation.json').is_file())
                self.assertTrue((run / 'run-manifest.json').is_file())
                if command == 'build':
                    self.assertEqual(stages['implementation']['status'], 'blocked')

    def test_console_summary_never_reports_blocked_configuration_as_pass(self):
        import dataclasses
        from chipflow.stages.elaboration import elaboration_stage as real_elab_stage
        project = self._setup_valid_s8_project(self.root / 'summary_proj')
        local = toolchain_file(self.root)

        def failing_elab(p, tc, rp):
            spec = real_elab_stage(p, tc, rp)
            fake_py = ("import pathlib, sys; pathlib.Path('elab.log').write_text('ERROR: syntax error in work/src/0000_rtl.v\\n', "
                       "encoding='utf-8'); sys.exit(1)")
            return dataclasses.replace(spec, command=(sys.executable, "-c", fake_py))

        output = io.StringIO()
        with patch('chipflow.cli.doctor', side_effect=self._fake_passing_doctor), \
                patch('chipflow.cli.elaboration_stage', side_effect=failing_elab), redirect_stdout(output):
            self.assertEqual(main(['validate', str(project), '--toolchain', str(local)]), 2)
        printed = output.getvalue()
        self.assertIn('elaboration: FAIL', printed)
        self.assertIn('syntax error in', printed)
        self.assertIn('configuration: NOT RUN', printed)
        self.assertNotIn('configuration: PASS', printed)


if __name__ == '__main__':
    unittest.main()
