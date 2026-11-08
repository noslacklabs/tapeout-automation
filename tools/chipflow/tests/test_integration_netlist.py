"""Exercise the mapped-netlist checker on a newly synthesized configured probe."""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from chipflow.runner.execution import run_command, sha256_file
from chipflow.runner.results import CheckStatus
from chipflow.stages.netlist_checks import check_mapped_netlist
from chipflow.technologies.profile import load_profile
from chipflow.toolchain import load_toolchain, verify_file


class IntegrationNetlistTests(unittest.TestCase):
    def test_fresh_mapped_probe_and_detected_defects(self):
        configured = os.environ.get('CHIPFLOW_TEST_TOOLCHAIN')
        if not configured:
            self.skipTest('CHIPFLOW_TEST_TOOLCHAIN environment variable not set')
        toolchain = load_toolchain(configured)
        tool = toolchain.tool('yosys')
        library = toolchain.pdk_file('core_typ_lib')
        profile = load_profile()
        with tempfile.TemporaryDirectory(prefix='chipflow mapped probe ') as temporary:
            output = Path(temporary)
            resources = files('chipflow').joinpath('resources')
            for name in ('probe.v', 'probe.ys'):
                (output / name).write_bytes(resources.joinpath(name).read_bytes())
            verify_file(library.path, library.sha256)
            shutil.copyfile(library.path, output / 'core.lib')
            verify_file(output / 'core.lib', library.sha256)
            verify_file(tool.executable, tool.sha256)
            command = run_command([str(tool.executable), '-s', 'probe.ys'], output, 60, log_prefix='mapping')
            self.assertTrue(command.succeeded, command.to_dict())
            # Recheck both consumed inputs after mapping as well.
            verify_file(library.path, library.sha256)
            verify_file(output / 'core.lib', library.sha256)
            verify_file(tool.executable, tool.sha256)
            mapped_path = output / 'mapped.json'
            self.assertGreater(mapped_path.stat().st_size, 0)
            self.assertEqual(len(sha256_file(mapped_path)), 64)
            data = json.loads(mapped_path.read_text(encoding='utf-8'))
            module = data['modules']['chipflow_probe']
            cells = module['cells']
            self.assertTrue(cells)
            self.assertTrue(any(cell['type'] == 'DFFL11' for cell in cells.values()))
            excluded = {entry.cell for entry in profile.excluded_cells}
            # The environment probe uses the raw library, rather than the
            # profile-filtered synthesis flow that belongs to Milestone 3.
            allowed = set(data['modules']) - {'chipflow_probe'}
            sequential = {'DFFL11'}
            rows = check_mapped_netlist(data, 'chipflow_probe', allowed, sequential, 'probe_clock')
            self.assertEqual(len(rows), 4)
            for row in rows:
                self.assertEqual(row.status, CheckStatus.PASS, f'{row.id}: {row.message}')

            used_excluded = {cell['type'] for cell in cells.values()} & excluded
            self.assertTrue(used_excluded, 'raw-library probe no longer exercises excluded cells')
            strict_rows = {row.id: row for row in check_mapped_netlist(
                data, 'chipflow_probe', allowed - excluded, sequential, 'probe_clock')}
            self.assertEqual(strict_rows['netlist.allowed_cells'].status, CheckStatus.FAIL)
            for cell_type in used_excluded:
                self.assertIn(cell_type, strict_rows['netlist.allowed_cells'].message)

            # Change actual mapped connectivity, rather than a synthetic fixture.
            wrong_clock = deepcopy(data)
            broken_cell = next(cell for cell in wrong_clock['modules']['chipflow_probe']['cells'].values()
                               if cell['type'] == 'DFFL11')
            self.assertIn('C', broken_cell['connections'])
            broken_cell['connections']['C'] = module['ports']['reset_high']['bits']
            broken_rows = {row.id: row for row in check_mapped_netlist(
                wrong_clock, 'chipflow_probe', allowed, sequential, 'probe_clock')}
            self.assertEqual(broken_rows['netlist.sequential_clock'].status, CheckStatus.FAIL)

            direct_tie = deepcopy(data)
            broken_cell = next(cell for cell in direct_tie['modules']['chipflow_probe']['cells'].values()
                               if cell['type'] == 'DFFL11')
            broken_cell['connections']['D'] = ['0']
            broken_rows = {row.id: row for row in check_mapped_netlist(
                direct_tie, 'chipflow_probe', allowed, sequential, 'probe_clock')}
            self.assertEqual(broken_rows['netlist.constants'].status, CheckStatus.FAIL)


if __name__ == '__main__':
    unittest.main()
