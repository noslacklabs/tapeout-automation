"""Test the installed wheel, using existing EDA Python deps; no clean-install claim."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, default=None, help='explicit wheel path')
    args = parser.parse_args()
    root = args.workspace.resolve()
    if args.wheel is not None:
        wheel = args.wheel.resolve()
    else:
        wheel, = (root / 'dist').glob('chipflow-*.whl')
    directory = root / 'work' / ('installed-wheel-' + uuid.uuid4().hex[:10])
    directory.mkdir(parents=True)
    venv = directory / 'venv'
    records = []
    def run(name: str, argv: list[str]) -> int:
        with (directory / (name + '.log')).open('wb') as output:
            p = subprocess.run(argv, cwd=directory, stdin=subprocess.DEVNULL, stdout=output,
                               stderr=subprocess.STDOUT, check=False, timeout=120)
        records.append(dict(name=name, argv=argv, returncode=p.returncode, log=name + '.log'))
        return p.returncode
    code = run('create-venv', [sys.executable, '-m', 'venv', str(venv)])
    python = venv / 'bin/python'
    if code == 0:
        # Reuse only pre-existing EDA dependencies, after this venv's own packages.
        # The assertion below ensures chipflow itself comes from the installed wheel.
        dependency_site = Path(sys.prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
        site_dir = venv / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
        (site_dir / 'reference-eda-dependencies.pth').write_text(str(dependency_site) + '\n', encoding='utf-8')
        code = run('install', [str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', '--disable-pip-version-check', str(wheel)])
    if code == 0:
        expression = 'import chipflow; from pathlib import Path; p=Path(chipflow.__file__).resolve(); print(p); assert p.is_relative_to(Path(' + repr(str(venv)) + '))'
        code = run('import-location', [str(python), '-I', '-c', expression])
    if code == 0:
        code = run('tests', [str(python), '-I', '-m', 'unittest', 'discover', '-s', str(root / 'tools/chipflow/tests'), '-v'])
    if code == 0:
        code = run('doctor-probe', [str(venv / 'bin/chipflow'), 'doctor', '--toolchain', str(root / 'toolchains/local.wsl.toml'),
                                  '--run-root', str(directory / 'probe path \u00b5'), '--probe'])
    report = dict(schema_version=1, captured_at=datetime.now(timezone.utc).isoformat(),
                  wheel=str(wheel), wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), records=records,
                  exit_code=code, installed_package_verification='PASS' if code == 0 else 'FAIL',
                  dependency_environment='existing EDA packages reused via explicit .pth after venv packages',
                  clean_eda_installation='NOT RUN', full_chip_build='NOT RUN', signoff_ready=False)
    (directory / 'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'directory': str(directory), 'exit_code': code, 'wheel_sha256': report['wheel_sha256']}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
