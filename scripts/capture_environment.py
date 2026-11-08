"""Read-only inventory of the reference WSL tools; writes evidence in this project."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(argv: list[str]) -> dict:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, errors='replace', timeout=30, check=False)
        return {'argv': argv, 'returncode': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'argv': argv, 'error': str(error)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    home_dir = Path.home()
    tools = {
        'yosys': ('/usr/bin/yosys', '-V'),
        'openroad': ('/nix/store/6qbn5zjnnnyna6md4rsmiqw4y26y8556-openroad-26Q3/bin/openroad', '-version'),
        'klayout': ('/nix/store/c8mymw0gckkiw7lqwnm2l6mqnd99xmzy-klayout-0.30.7/bin/klayout', '-v'),
        'iverilog': ('/usr/bin/iverilog', '-V'),
        'vvp': ('/usr/bin/vvp', '-V'),
        'formal_yosys': ('/nix/store/cfry5mn7l5qk6cy730gspaa51fahc108-yosys-0.70/bin/yosys', '-V'),
        'sby': ('/nix/store/hmrimr3nwb4d2di2jm9s5r9g70cc6zix-sby-0.70/bin/sby', '--version'),
        'z3': ('/nix/store/g91d3ysw40238p0wlkw7789kx9k8hxbq-z3-5.3.2/bin/z3', '--version'),
    }
    tool_data = {}
    for name, argv in tools.items():
        binary = Path(argv[0])
        row = {'path': str(binary), 'exists': binary.is_file(), 'version': command(list(argv))}
        if binary.is_file():
            row.update(sha256=digest(binary), resolved_path=str(binary.resolve()))
        tool_data[name] = row
    distributions = {}
    for name in ('pip', 'setuptools', 'wheel', 'build', 'pytest', 'klayout', 'gdstk', 'numpy', 'cocotb', 'find_libpython', 'presubmit-check'):
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            distributions[name] = None
    search_roots = [home_dir / 'tool-eval', home_dir / 'silicon']
    recipe_candidates = []
    # Bound the discovery and avoid traversing venvs, caches, builds or the Nix store.
    def scan(root: Path, depth: int = 0) -> None:
        if not root.is_dir() or depth > 3:
            return
        for path in sorted(root.iterdir()):
            if path.name in {'flake.nix', 'flake.lock', 'shell.nix', 'requirements.txt', 'pyproject.toml'} and path.is_file():
                recipe_candidates.append({'path': str(path), 'sha256': digest(path)})
            elif path.is_dir() and not path.is_symlink() and path.name not in {
                'venv', '.venv', 'builds', '.git', 'node_modules', 'udocker', '.cache', 'work', 'data', 'logs',
            }:
                scan(path, depth + 1)
    for root in search_roots:
        scan(root)
    report = {
        'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'Installed reference environment; no fresh installation or full chip build performed.',
        'platform': platform.platform(), 'machine': platform.machine(),
        'os_release': platform.freedesktop_os_release(),
        'python': {'executable': sys.executable, 'version': sys.version, 'packages': distributions},
        'installed_distributions': sorted(
            [{'name': d.metadata['Name'], 'version': d.version} for d in importlib.metadata.distributions()],
            key=lambda d: d['name'].lower()),
        'tools': tool_data,
        'apt_packages': command(['dpkg-query', '-W', 'yosys', 'iverilog', 'python3.12']),
        'nix': command(['/nix/var/nix/profiles/default/bin/nix', '--version']),
        'recipe_candidates': recipe_candidates,
        'reference_directories': {
            str(root): sorted(p.name for p in root.iterdir()) if root.is_dir() else []
            for root in search_roots
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'tools': {k: v['exists'] for k, v in tool_data.items()},
                      'python_packages': distributions, 'recipe_candidates': recipe_candidates}, ensure_ascii=False))


if __name__ == '__main__':
    main()
