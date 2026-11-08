"""Archive evidence for this first increment and verify that the kit is unchanged."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def test_count(path: Path) -> int:
    text = path.read_text(encoding='utf-8')
    count = re.search(r'Ran (\d+) tests', text)
    if count is None or not re.search(r'^OK\s*$', text, re.M):
        raise ValueError(f'passing test summary missing: {path}')
    return int(count[1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--installed-verification', type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    verified = json.loads(args.installed_verification.read_text(encoding='utf-8'))
    if verified['exit_code'] != 0 or verified['installed_package_verification'] != 'PASS':
        raise ValueError('installed verification failed')
    wheel = Path(verified['wheel'])
    if digest(wheel) != verified['wheel_sha256']:
        raise ValueError('wheel changed after verification')
    installed = args.installed_verification.parent
    doctor, = installed.glob('probe path */*/doctor/doctor.json')
    probe, = installed.glob('probe path */*/probe/probe.json')
    for path in (doctor, probe):
        if json.loads(path.read_text(encoding='utf-8'))['exit_code'] != 0:
            raise ValueError(f'failed gate: {path}')
    counts = {'windows': test_count(root / 'work/windows-tests.log'), 'installed_wsl_wheel': test_count(installed / 'tests.log')}
    pdk = json.loads((root / 'reference/s8/pdk-files.json').read_text(encoding='utf-8'))
    kit_root = Path(pdk['root'])
    after = {str(p.relative_to(kit_root)): digest(p) for p in kit_root.rglob('*') if p.is_file()}
    before = {r['path']: r['sha256'] for r in pdk['files']}
    if after != before:
        raise ValueError('PDK files differ from the initial fingerprint inventory')
    target = root / 'reference/foundation'
    target.mkdir(exist_ok=False)
    evidence_files = {
        'windows-tests.log': root / 'work/windows-tests.log', 'installed-tests.log': installed / 'tests.log',
        'installed-wheel.json': args.installed_verification, 'import-location.log': installed / 'import-location.log',
        'doctor.json': doctor, 'probe.json': probe,
    }
    copied = []
    for name, source in evidence_files.items():
        shutil.copyfile(source, target / name)
        copied.append({'path': name, 'sha256': digest(target / name)})
    source_hashes = []
    for base in (root / 'tools/chipflow/chipflow', root / 'tools/chipflow/tests', root / 'scripts'):
        for path in sorted(base.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                source_hashes.append({'path': str(path.relative_to(root)), 'sha256': digest(path)})
    source_hashes.append({'path': 'tools/chipflow/pyproject.toml', 'sha256': digest(root / 'tools/chipflow/pyproject.toml')})
    (target / 'source-files.json').write_text(json.dumps(source_hashes, indent=2) + '\n', encoding='utf-8')
    report = dict(schema_version=1, captured_at=datetime.now(timezone.utc).isoformat(),
        kind='foundation-increment-verification', engine_version='0.1.0.dev1',
        wheel_path=str(wheel.relative_to(root)), wheel_sha256=verified['wheel_sha256'],
        tests=counts, installed_import_verified=True, real_environment_probe='PASS',
        unicode_space_path='PASS', pdk_unchanged=True, pdk_files_checked=len(after), evidence=copied,
        full_chip_build='NOT RUN', clean_eda_installation='NOT RUN',
        reference_current_rtl_reproduction='NOT RUN', milestones_1_and_2='partial', signoff_ready=False)
    (target / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
