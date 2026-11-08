"""Capture and reproduce the private S8 reference without changing its live tree.

This is a milestone-1 reference harness, not the reusable physical-flow engine.
Legacy shell exit codes are retained separately from positive evidence validation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / 'tools/chipflow'))
from chipflow.runner.execution import atomic_write_json, run_command, sha256_file
from chipflow.toolchain import load_toolchain, verify_file


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def save(state: dict) -> None:
    atomic_write_json(Path(state['run']) / 'state.json', state)


def verify_inputs(state: dict) -> None:
    restore_staging(state)
    verify_file(Path(state['toolchain']), state['toolchain_sha256'])
    for row in state['inputs']:
        verify_file(Path(row['staged']), row['sha256'])
    toolchain = load_toolchain(state['toolchain'])
    for row in toolchain.files:
        verify_file(row.path, row.sha256)
    for row in toolchain.tools:
        verify_file(row.executable, row.sha256)


def restore_staging(state: dict) -> None:
    """Restore an evicted /tmp workspace from hash-checked retained inputs."""
    staging = Path(state['staging'])
    if staging.parent != Path('/tmp') or not staging.name.startswith('chipflow-m1-'):
        raise ValueError('unexpected staging path')
    if (Path(state['source']) / 'flow/README.md').is_file():
        return
    run = Path(state['run'])
    if not (run / 'source/flow/README.md').is_file():
        raise ValueError('retained source snapshot is missing')
    staging.mkdir(exist_ok=True)
    shutil.copytree(run / 'source', Path(state['source']), dirs_exist_ok=True)
    home = Path(state['home'])
    for relative in ('.venvs/presubmit-check', 'tool-eval/venv'):
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.symlink_to(Path.home() / relative, target_is_directory=True)
    for row in state['inputs']:
        target = Path(row['staged'])
        if not target.is_file():
            original = Path(row['original'])
            verify_file(original, row['sha256'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        verify_file(target, row['sha256'])
    if (run / 'artifacts/build').is_dir():
        shutil.copytree(run / 'artifacts/build', Path(state['build']), dirs_exist_ok=True)
    if (run / 'artifacts/flow-report.md').is_file():
        shutil.copyfile(run / 'artifacts/flow-report.md', staging / 'flow-report.md')
    state.setdefault('staging_restorations', []).append(datetime.now(timezone.utc).isoformat())
    save(state)


def prepare() -> Path:
    source = Path('/mnt/m/My Projects/Tapeout Chip')
    if not (source / 'rtl/s8.v').is_file():
        raise FileNotFoundError(source)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    run = WORKSPACE / 'work/milestone1' / ('reference-' + stamp)
    run.mkdir(parents=True, exist_ok=False)
    staging = Path(tempfile.mkdtemp(prefix='chipflow-m1-', dir='/tmp'))
    staged_source = staging / 'source'
    staged_source.mkdir()
    inputs = []
    for name in ('flow', 'rtl', 'tb', 'formal', 'model', 'sw', 'spec', 'docs', 'reports'):
        for item in sorted((source / name).rglob('*')):
            if not item.is_file() or any(p in {'__pycache__', '.pytest_cache', '.git'} for p in item.parts):
                continue
            relative = item.relative_to(source)
            target = staged_source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            before = sha256_file(item)
            shutil.copyfile(item, target)
            if sha256_file(target) != before or sha256_file(item) != before:
                raise ValueError(f'input changed during capture: {item}')
            inputs.append(dict(original=str(item), staged=str(target), relative_path=str(relative), sha256=before))
    toolchain = load_toolchain(WORKSPACE / 'toolchains/local.wsl.toml')
    for item in toolchain.files:
        verify_file(item.path, item.sha256)
    home = staging / 'home'
    home.mkdir()
    actual_home = Path.home()
    for relative in ('.venvs/presubmit-check', 'tool-eval/venv'):
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(actual_home / relative, target_is_directory=True)
    digital = toolchain.pdk_file('core_typ_lib').path.parents[1]
    data_target = home / 'tool-eval/data/digital_c1d'
    shutil.copytree(digital, data_target)
    model = actual_home / 'silicon/builds/chip-cpu/ram-model/v/23LC512.v'
    model_target = home / 'silicon/builds/chip-cpu/ram-model/v/23LC512.v'
    model_target.parent.mkdir(parents=True)
    shutil.copyfile(model, model_target)
    for original, staged in ((model, model_target),):
        inputs.append(dict(original=str(original), staged=str(staged), relative_path='external/' + original.name, sha256=sha256_file(staged)))
    for item in sorted(data_target.rglob('*')):
        if item.is_file():
            inputs.append(dict(original=str(digital / item.relative_to(data_target)), staged=str(item),
                               relative_path='external/digital_c1d/' + str(item.relative_to(data_target)), sha256=sha256_file(item)))
    build = staging / 'build'
    build.mkdir()
    environment = {
        'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'LC_ALL': 'C.UTF-8',
        'CPU': 's8', 'CPU_V': 'rtl/s8.v', 'TOP_CELL': 'OS0000', 'BUILD': str(build),
        'REPORT': str(staging / 'flow-report.md'), 'FAST': '0', 'THREADS': '10',
        'TRIES': '0 1 2 3 4 5', 'PNR_TIMEOUT': '3000', 'DRT_ITERS': '40',
        'REPAIR': '1', 'SLEW_MARGIN': '10', 'CAP_MARGIN': '5', 'DENSITY': 'uniform',
        'CTS_ARGS': '-distance_between_buffers 200', 'ALLOW_VIA_OVER_POLY': '0',
        'PERIOD': '100', 'EXTLOAD': '30', 'ABC_D': '',
    }
    ngspice = sorted(p for p in Path('/nix/store').glob('*ngspice-45/bin/ngspice') if p.is_file())
    state = dict(schema_version=1, kind='s8-milestone1-reference', run=str(run), staging=str(staging),
                 source=str(staged_source), original_source=str(source), home=str(home), build=str(build),
                 toolchain=str(toolchain.path), toolchain_sha256=sha256_file(toolchain.path),
                 captured_at=datetime.now(timezone.utc).isoformat(), inputs=inputs, environment=environment,
                 ngspice=str(ngspice[0]) if len(ngspice) == 1 else None,
                 ngspice_sha256=sha256_file(ngspice[0]) if len(ngspice) == 1 else None,
                 source_policy='immutable engineering snapshot; not an approved final silicon freeze',
                 expectation=dict(raw_extraction_ratio_min=100.0, raw_extraction_ratio_max=1000.0,
                                  expected_routed_test_count=20, expected_seeded_runs=15,
                                  expected_ram_positive_cases=36, expected_ram_negative_cases=4),
                 stages={})
    atomic_write_json(run / 'input-manifest.json', inputs)
    atomic_write_json(run / 'resolved-environment.json', environment)
    shutil.copyfile(toolchain.path, run / 'toolchain.toml')
    save(state)
    # Retain the captured source independently of temporary tool-compatible paths.
    shutil.copytree(staged_source, run / 'source')
    print(json.dumps({'run': str(run), 'state': str(run / 'state.json'), 'staging': str(staging),
                      'input_files': len(inputs), 'rtl_sha256': sha256_file(staged_source / 'rtl/s8.v')}))
    return run / 'state.json'


def run_stage(state: dict, name: str, argv: list[str], timeout: int = 1200, extra: dict | None = None) -> dict:
    directory = Path(state['run']) / 'commands' / name
    directory.mkdir(parents=True, exist_ok=False)
    result = run_command(argv, directory, timeout, {**state['environment'], **(extra or {})})
    return result.to_dict()


def checkpoint(state: dict) -> None:
    """Copy current outputs, preserving the original run and short-path workspace."""
    stage = Path(state['staging'])
    durable = Path(state['run']) / 'artifacts'
    shutil.copytree(stage / 'build', durable / 'build', dirs_exist_ok=True)
    for name in ('flow-report.md',):
        if (stage / name).is_file():
            shutil.copyfile(stage / name, durable / name)
    sim = Path(state['home']) / 'silicon/builds/chip-cpu/sim'
    if sim.exists():
        shutil.copytree(sim, durable / 'sim', dirs_exist_ok=True)
    gate = Path(state['home']) / 'silicon/builds/chip-cpu/gate'
    if gate.exists():
        shutil.copytree(gate, durable / 'gate', dirs_exist_ok=True)


def ram_audit(state: dict) -> None:
    verify_inputs(state)
    source, build = Path(state['source']), Path(state['build'])
    binaries = Path(state['staging']) / 'audited-bin'
    binaries.mkdir(exist_ok=True)
    for name in ('vvp', 'iverilog'):
        target = binaries / name
        shutil.copyfile(WORKSPACE / 'scripts/m1_process_wrapper.py', target)
        target.chmod(0o755)
    extra = dict(PATH=str(binaries) + ':' + state['environment']['PATH'],
                 CHIPFLOW_PROCESS_AUDIT=str(build / 'ram-process-audit.jsonl'))
    python = str(Path(state['home']) / 'tool-eval/venv/bin/python')
    for name, options in (('ram-positive-audited', ['--routed', str(build)]), ('ram-negative-audited', ['--period', '40'])):
        state['stages'][name] = run_stage(state, name, [python, str(source / 'tb/run_ram_model.py'),
            *options, '--build-dir', str(build / name)], 900, extra)
        save(state)
    checkpoint(state)
    print(json.dumps({'audited_ram_stages': 2, 'process_audit': str(build / 'ram-process-audit.jsonl')}))


def physical(state: dict) -> None:
    verify_inputs(state)
    state['stages']['physical'] = run_stage(state, 'physical', ['bash', str(Path(state['source']) / 'flow/run_all.sh')], timeout=3600)
    save(state)
    checkpoint(state)
    print(json.dumps({'legacy_shell_exit': state['stages']['physical']['returncode'],
                      'note': 'Legacy status is not an aggregate verification result. Positive evidence is checked separately.'}))


def regressions(state: dict) -> None:
    verify_inputs(state)
    source, build = Path(state['source']), Path(state['build'])
    python = str(Path(state['home']) / 'tool-eval/venv/bin/python')
    with (build / 'routed-with-shim.v').open('wb') as out:
        out.write((build / 'pnr/routed.v').read_bytes())
        out.write((build / 'sta/shim.v').read_bytes())
    state['stages']['gate_model'] = run_stage(state, 'gate-model', ['bash', str(source / 'tb/gate_build.sh')], timeout=120)
    save(state)
    if state['stages']['gate_model']['returncode'] != 0:
        raise ValueError('Gate-model preparation failed')
    jobs = [
        ('clock-trace', ['/usr/bin/python3', str(source / 'tb/check_routed_clocks.py'), str(build / 'pnr/routed.v')], 60, None),
        ('routed-equivalence', ['bash', str(source / 'formal/lec.sh')], 1500,
         {'NET': str(build / 'routed-with-shim.v'), 'L': str(build / 'lec'), 'LIMIT': '1200'}),
        ('rtl-tests', [python, str(source / 'tb/run.py'), '--tag', 'm1-rtl'], 900, None),
        ('seeded-sdf', [python, str(source / 'tb/routed_random.py'), str(build), '--output', str(build / 'seeded-sdf'),
                        '--seeds', '1', '2', '3', '4', '5', '--programs', '50', '--instr', '100', '--jobs', '3'], 1200, None),
        ('sdf-probe', [python, str(source / 'tb/check_random_start_sdf.py'), str(build), str(build / 'sdf-probe')], 120, None),
        ('ram-positive', [python, str(source / 'tb/run_ram_model.py'), '--routed', str(build), '--build-dir', str(build / 'ram-positive')], 900, None),
        ('ram-negative', [python, str(source / 'tb/run_ram_model.py'), '--period', '40', '--build-dir', str(build / 'ram-negative')], 300, None),
    ]
    for corner in ('min', 'typ', 'max'):
        jobs.append(('routed-' + corner, [python, str(source / 'tb/run.py'), '--routed', str(build),
                     '--corner', corner, '--tag', 'm1-routed-' + corner], 900, None))
    if state['ngspice']:
        verify_file(Path(state['ngspice']), state['ngspice_sha256'])
        toolchain = load_toolchain(state['toolchain'])
        models = list(toolchain.pdk_root.glob('Analog_C1D/**/libs.tech/ngpsice/model/hspice/models49'))
        if len(models) != 1:
            raise ValueError('Expected one analog model-data directory')
        jobs.append(('pad-polarity', [sys.executable, str(source / 'flow/libcheck/pad_polarity.py'),
                     str(toolchain.pdk_file('frame_io_cdl').path), str(toolchain.pdk_file('core_cdl').path),
                     str(models[0]), str(build / 'pad-polarity'), state['ngspice']], 300, None))
    def execute(job):
        name, argv, timeout, extra = job
        result = run_stage(state, name, argv, timeout, extra)
        print(json.dumps({'stage': name, 'returncode': result['returncode'], 'timed_out': result['timed_out']}), flush=True)
        return name, result
    # Bound CPU/memory use while the independent environment gate also runs.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for name, result in pool.map(execute, jobs):
            state['stages'][name] = result
            save(state)
    checkpoint(state)
    print(json.dumps({'regression_commands': len(jobs), 'run': state['run']}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'physical', 'regressions', 'ram-audit', 'checkpoint'))
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    if args.phase == 'prepare':
        prepare()
        return 0
    if args.state is None:
        parser.error('--state is required for subsequent phases')
    state = read(args.state)
    if Path(state['run']).resolve() != args.state.resolve().parent:
        raise ValueError('state/run path mismatch')
    if args.phase == 'physical':
        physical(state)
    elif args.phase == 'regressions':
        regressions(state)
    elif args.phase == 'ram-audit':
        ram_audit(state)
    else:
        checkpoint(state)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
