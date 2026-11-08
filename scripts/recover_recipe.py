"""Recover local public tool recipe provenance without fetching or installing tools."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def run(argv: list[str]) -> dict:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, errors='replace',
                           stdin=subprocess.DEVNULL, timeout=30, check=False)
        return {'argv': argv, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'argv': argv, 'error': str(error)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    upstream = Path.home() / 'silicon/upstream'
    rows = []
    for name in ('librelane', 'OpenROAD-flow-scripts'):
        source = upstream / name
        row = {'path': str(source), 'head': run(['git', '-C', str(source), 'rev-parse', 'HEAD']),
               'status': run(['git', '-C', str(source), 'status', '--porcelain']), 'files': []}
        target = args.output / name
        target.mkdir()
        for basename in ('flake.nix', 'flake.lock', 'shell.nix'):
            src = source / basename
            if src.is_file():
                shutil.copyfile(src, target / basename)
                row['files'].append({'path': basename, 'sha256': hashlib.sha256(src.read_bytes()).hexdigest()})
        rows.append(row)
    nix_store = '/nix/var/nix/profiles/default/bin/nix-store'
    closures = {}
    for name, store in {
        'openroad': '/nix/store/6qbn5zjnnnyna6md4rsmiqw4y26y8556-openroad-26Q3',
        'klayout': '/nix/store/c8mymw0gckkiw7lqwnm2l6mqnd99xmzy-klayout-0.30.7',
    }.items():
        derivation = run([nix_store, '--query', '--deriver', store])
        closure = run([nix_store, '--query', '--requisites', store])
        closures[name] = {'store_path': store, 'derivation': derivation, 'closure': closure}
        drv = Path(derivation.get('stdout', '').strip())
        if drv.is_file():
            shutil.copyfile(drv, args.output / (name + '.drv'))
    # Recover exact installed Debian versions and hashes, not an unpinned apt-get command.
    apt = run(['dpkg-query', '-W', '-f=${Package}=${Version}\n', 'yosys', 'iverilog', 'python3.12', 'python3.12-venv'])
    report = {'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
              'fresh_environment_qualification': 'NOT RUN',
              'note': 'Recovered inputs and closures are evidence. A clean installation has not been exercised.',
              'upstream': rows, 'nix_closures': closures, 'debian_packages': apt}
    (args.output / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'debian_packages': apt, 'upstream': rows}))


if __name__ == '__main__':
    main()
