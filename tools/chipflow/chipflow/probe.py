"""Small synthesis/geometry environment gate, deliberately separate from chip builds."""
from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
import shutil
import sys

from .runner.execution import atomic_write_json, run_command, sha256_file
from .toolchain import Toolchain, verify_file


def run_probe(toolchain: Toolchain, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    commands = []
    checks = []
    path_map = []
    resource_root = files('chipflow').joinpath('resources')
    for name in ('probe.v', 'probe.ys', 'probe_batch.py', 'probe_geometry.py'):
        target = output / name
        target.write_bytes(resource_root.joinpath(name).read_bytes())
        path_map.append(dict(original='package:chipflow/resources/' + name, staged=name, sha256=sha256_file(target)))
    for identifier, staged in (('core_typ_lib', 'core.lib'), ('frame_gds', 'frame.gds')):
        item = toolchain.pdk_file(identifier)
        verify_file(item.path, item.sha256)
        target = output / staged
        shutil.copyfile(item.path, target)
        verify_file(target, item.sha256)
        path_map.append(dict(original=str(item.path), staged=staged, sha256=item.sha256))
    atomic_write_json(output / 'path-map.json', path_map)
    try:
        tool = toolchain.tool('yosys')
        verify_file(tool.executable, tool.sha256)
        synthesis = run_command([str(tool.executable), '-s', 'probe.ys'], output, 60, log_prefix='synthesis')
        commands.append(synthesis.to_dict())
        if not synthesis.succeeded:
            raise ValueError('synthesis command failed; see synthesis logs')
        netlist = json.loads((output / 'mapped.json').read_text(encoding='utf-8'))
        module = netlist['modules']['chipflow_probe']
        cells = module['cells']
        library_cells = netlist['modules']
        if not cells or any(c['type'].startswith('$') or c['type'] not in library_cells for c in cells.values()):
            raise ValueError('probe is empty, unmapped, or references unknown library cells')
        if not any(c['type'] == 'DFFL11' for c in cells.values()):
            raise ValueError('probe has no mapped DFFL11 state')
        checks.append(dict(id='probe.synthesis', status='PASS', cells=len(cells), artifact='mapped.json', sha256=sha256_file(output / 'mapped.json')))
        tool = toolchain.tool('klayout')
        verify_file(tool.executable, tool.sha256)
        geometry = run_command([str(tool.executable), '-b', '-r', 'probe_batch.py'], output, 30, log_prefix='batch-geometry')
        commands.append(geometry.to_dict())
        if not geometry.succeeded:
            raise ValueError('batch KLayout command failed; see batch-geometry logs')
        validation = run_command([sys.executable, '-I', 'probe_geometry.py'], output, 30, log_prefix='geometry-validation')
        commands.append(validation.to_dict())
        if not validation.succeeded:
            raise ValueError('geometry validation failed; see geometry-validation logs')
        details = json.loads((output / 'geometry.json').read_text(encoding='utf-8'))
        if details['batch_round_trip'] != 'PASS' or details['frame_read'] != 'PASS':
            raise ValueError('geometry validation lacks positive evidence')
        checks.append(dict(id='probe.geometry', status='PASS', artifact='geometry.json', sha256=sha256_file(output / 'geometry.json'), details=details))
    except (OSError, ValueError, KeyError) as error:
        checks.append(dict(id='probe.execution', status='FAIL', message=str(error)))
    cancelled = any(c['cancelled'] for c in commands)
    report = dict(schema_version=1, kind='environment-probe', checks=checks, commands=commands,
                  exit_code=130 if cancelled else 0 if len(checks) == 2 and all(c['status'] == 'PASS' for c in checks) else 2,
                  chip_build='NOT RUN', clean_environment_installation='NOT RUN', signoff_ready=False)
    atomic_write_json(output / 'probe.json', report)
    return report
