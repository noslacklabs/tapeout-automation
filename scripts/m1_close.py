"""Audit every milestone-1 acceptance item and publish immutable local evidence."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/chipflow'))
from chipflow.runner.execution import atomic_write_json, run_command, sha256_file


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_path(value):
    if len(value) > 2 and value[1:3] == ':/':
        return Path('/mnt/' + value[0].lower() + value[2:])
    return Path(value)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(state_path: Path) -> None:
    run = state_path.parent
    state = read(state_path)
    evidence_dir = run / 'acceptance-checks'
    evidence_dir.mkdir(exist_ok=False)
    # Exercise both real exit policies against the actual current-run evidence.
    design = run_command([sys.executable, str(ROOT / 'scripts/m1_collect.py'), '--state', str(state_path),
                          '--outcome', 'design'], evidence_dir, 120, log_prefix='design-status')
    require(design.returncode == 1 and not (design.error or design.timed_out or design.cancelled),
            'actual failed design must exit 1; crash/infrastructure cannot qualify')
    baseline = read(run / 'reference-validation.json')
    require(baseline['qualification'] == 'PASS' and not baseline['errors'], 'reference reproduction failed')
    unit = run_command([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests/milestone1'), '-v'],
                       evidence_dir, 120, log_prefix='evidence-guards')
    require(unit.succeeded, 'milestone-1 evidence guard tests failed')
    unit_log = Path(unit.stderr_log).read_text(encoding='utf-8')
    require(re.search(r'Ran 8 tests\b', unit_log) and re.search(r'^OK\s*$', unit_log, re.M),
            'expected nonzero evidence guard suite did not run')
    runtime_dir = ROOT / 'toolchains/milestone1'
    runtime = read(runtime_dir / 'capture.json')
    startup = read(runtime_dir / 'fresh-startup.json')
    require(runtime['status'] == 'CAPTURED', 'runtime bundle is incomplete')
    require(startup['capture_manifest_sha256'] == sha256_file(runtime_dir / 'capture.json'), 'runtime manifest drift')
    require(startup['exit_code'] == 0 and startup['fresh_startup'] == 'PASS', 'fresh environment did not pass')
    require(not startup['host_venv_reuse'] and not startup['host_store_mounted'] and startup['private_pid_namespace'],
            'fresh environment used a host dependency')
    require(startup['external_pdk_unchanged'] and startup['private_pdk_staged_files'] == 20, 'PDK startup scope invalid')
    for row in (runtime['ubuntu_base'], runtime['nix_runtime'], *runtime['debian_packages']):
        require(sha256_file(runtime_dir / 'artifacts' / row['filename']) == row['sha256'], 'runtime artifact drift')
    for row in runtime['python_wheels']:
        require(sha256_file(runtime_dir / 'artifacts/wheels' / row['filename']) == row['sha256'], 'wheel drift')
    require(sha256_file(runtime_dir / 'requirements.lock') == runtime['requirements_lock_sha256'], 'wheel lock drift')
    audit = read(ROOT / 'reference/s8/m1-contract-audit.json')
    require(not audit['source_drift_since_inventory'] and audit['workarounds'], 'source/workaround audit incomplete')
    for value, expected in audit['source_hashes'].items():
        require(sha256_file(source_path(value)) == expected, 'contract source reference drift: ' + value)
    required_docs = ['project-format.md', 'result-contract.md', 'stage-contracts.md', 'm1-workarounds.md',
                     'm1-environment.md', 'reference-inventory.md', 'known-limitations.md']
    for name in required_docs:
        require((ROOT / 'docs' / name).stat().st_size > 500, 'missing contract/inventory document: ' + name)
    dependencies = read(ROOT / 'reference/s8/dependencies.json')
    require(len(dependencies['regressions']) == 10 and all(m['observed_available'] for m in dependencies['external_models']),
            'wider regression/model inventory incomplete')
    require(baseline['source_rtl_sha256'] == audit['current_rtl_sha256'], 'reference source drift')
    limitations = read(ROOT / 'reference/s8/limitations.json')
    require(len(limitations['limitations']) >= 21, 'limitation manifest incomplete')
    require(baseline['reference_design_exit'] == 1 and baseline['raw_extraction'] == 'FAIL', 'known failure was waived')
    checker_source = runtime_dir / 'artifacts/checker-source'
    checker_files = []
    for path in sorted((checker_source / 'presubmit_check').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            relative = path.relative_to(checker_source)
            original = ROOT / 'tools/presubmit-check' / relative
            require(sha256_file(path) == sha256_file(original), 'checker code drift')
            checker_files.append(dict(path=str(relative), sha256=sha256_file(path)))
    require(bool(checker_files), 'checker source snapshot is empty')
    checker_files.append(dict(path='pyproject.toml', sha256=sha256_file(checker_source / 'pyproject.toml')))
    require(checker_files[-1]['sha256'] == sha256_file(ROOT / 'tools/presubmit-check/pyproject.toml'), 'checker metadata drift')
    target = ROOT / 'reference/milestone1'
    target.mkdir(exist_ok=False)
    shutil.copyfile(run / 'reference-validation.json', target / 'baseline.json')
    shutil.copyfile(run / 'input-manifest.json', target / 'source-inputs.json')
    shutil.copyfile(run / 'resolved-environment.json', target / 'reference-environment.json')
    shutil.copyfile(run / 'artifacts/flow-report.md', target / 'flow-report.md')
    shutil.copyfile(runtime_dir / 'fresh-startup.json', target / 'environment-startup.json')
    shutil.copyfile(evidence_dir / 'evidence-guards.stderr.log', target / 'evidence-tests.log')
    atomic_write_json(target / 'checker-source-files.json', checker_files)
    limitations.update(manifest_version='0.1.0-milestone1', captured_at=datetime.now(timezone.utc).isoformat(),
        observed_reference=dict(run=str(run), source_rtl_sha256=baseline['source_rtl_sha256'],
            toolchain_sha256=state['toolchain_sha256'], extraction=baseline['evidence']['raw_extraction'],
            presubmit_summary=baseline['evidence']['presubmit']['summary'], reference_design_exit=1,
            final_silicon_source_freeze='NOT RUN', signoff_complete=False))
    for limitation in limitations['limitations']:
        if limitation['id'] == 'S8-FREEZE-001':
            limitation['detail'] += ' A new immutable engineering reference now exists; final user-approved silicon freeze/review remains pending.'
    atomic_write_json(target / 'limitations.json', limitations)
    artifacts = []
    for base in (run / 'artifacts', run / 'commands', evidence_dir):
        for path in sorted(base.rglob('*')):
            if path.is_file():
                artifacts.append(dict(path=str(path.relative_to(ROOT)), bytes=path.stat().st_size, sha256=sha256_file(path)))
    atomic_write_json(target / 'artifact-manifest.json', artifacts)
    contracts = []
    for path in [*(ROOT / 'docs' / n for n in required_docs), ROOT / 'reference/s8/m1-contract-audit.json',
                 ROOT / 'toolchains/milestone1/capture.json', ROOT / 'toolchains/milestone1/requirements.lock',
                 ROOT / 'docs/milestone-1.md', ROOT / 'toolchains/milestone1/README.md',
                 *(ROOT / 'scripts' / name for name in ('m1_environment.py', 'm1_reference.py', 'm1_collect.py', 'm1_close.py', 'm1_process_wrapper.py')),
                 ROOT / 'tests/milestone1/test_evidence.py']:
        contracts.append(dict(path=str(path.relative_to(ROOT)), sha256=sha256_file(path)))
    atomic_write_json(target / 'contract-manifest.json', contracts)
    criteria = [
        dict(id='M1.1', status='PASS', evidence=['source-inputs.json', '../s8/dependencies.json'],
             detail='Current source/scripts/environment and historical results inventoried and immutable reference inputs captured.'),
        dict(id='M1.2', status='PASS', evidence=['baseline.json', 'artifact-manifest.json', 'reference-environment.json'],
             detail='Exact tool/config/source/PDK identities and fresh current-source physical/verification artifacts recorded.'),
        dict(id='M1.3', status='PASS', evidence=['../../docs/m1-workarounds.md', '../s8/m1-contract-audit.json'],
             detail='Engine/process/S8/local assumptions and every registered workaround classified with source/evidence hashes.'),
        dict(id='M1.4', status='PASS', evidence=['../../docs/project-format.md', '../../docs/stage-contracts.md', '../../docs/result-contract.md'],
             detail='Versioned project/stage/result/required-check contracts defined before reusable physical extraction; semantic implementation remains M2.'),
        dict(id='M1.5', status='PASS', evidence=['environment-startup.json', '../../toolchains/milestone1/capture.json'],
             detail='Hash-pinned binary recipe installed offline in fresh Ubuntu userspace; configured startup and real synthesis/geometry probes passed without host store/venv visibility.'),
        dict(id='M1.6', status='PASS', evidence=['baseline.json', '../s8/dependencies.json'],
             detail='Ten wider regression groups and external model availability/provenance captured; 380 RTL/routed tests, routed proof/clock tracing, RAM and DC baseline evidence reproduced.'),
        dict(id='M1.7', status='PASS', evidence=['limitations.json', 'baseline.json'],
             detail='Exact expected process/model/manual states preserved; raw extraction remains FAIL and real design-status validation exits 1.'),
    ]
    report = dict(schema_version=1, milestone=1, status='COMPLETE', captured_at=datetime.now(timezone.utc).isoformat(),
        acceptance=criteria, source_rtl_sha256=baseline['source_rtl_sha256'],
        reference_run=str(run.relative_to(ROOT)), reference_reproduction='PASS', reference_design_exit=1,
        qualification_exit=0, fresh_userspace_startup='PASS', evidence_guard_tests=8,
        artifact_count=len(artifacts), registered_workarounds=len(audit['workarounds']),
        active_project_and_pdk_unchanged=True, engine_physical_adapters='NOT RUN',
        clean_wsl_installation='NOT RUN', independent_eda_source_rebuild='NOT RUN',
        final_silicon_source_freeze='NOT RUN', signoff_ready=False,
        explanation='Milestone 1 is reference capture/contracts and fresh binary-runtime startup; later engine/release qualification and chip signoff remain separate.')
    atomic_write_json(target / 'acceptance.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    close(args.state.resolve())
