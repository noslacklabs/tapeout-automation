"""Configured tools and external-file fingerprints. No discovery from ambient PATH."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import platform
from pathlib import Path
import re
import tomllib
from typing import Any

from .runner.execution import atomic_write_json, run_command, sha256_file

REQUIRED_TOOLS = frozenset({'yosys', 'openroad', 'klayout', 'iverilog', 'vvp'})
REQUIRED_PDK_FILES = frozenset({
    'core_typ_lib', 'core_min_lib', 'core_max_lib', 'pads_typ_lib', 'pads_min_lib', 'pads_max_lib',
    'tech_lef', 'core_lef', 'io_lef', 'core_gds', 'io_gds', 'core_cdl', 'io_cdl',
    'cells_verilog', 'frame_gds', 'frame_cdl', 'frame_io_cdl', 'drc_deck', 'lvs_deck', 'rcx_rules',
})


class ToolchainError(ValueError):
    pass


def _keys(data: Any, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(data, dict):
        raise ToolchainError(f'{label} must be a table')
    if data.keys() - allowed or required - data.keys():
        raise ToolchainError(f'{label}: unknown keys {sorted(data.keys() - allowed)}; missing keys {sorted(required - data.keys())}')
    return data


def _text(data: Any, label: str) -> str:
    if not isinstance(data, str) or not data.strip() or any(c in data for c in '\0\r\n'):
        raise ToolchainError(f'{label} must be a nonempty single-line string')
    return data


def _sha(data: Any, label: str) -> str:
    if not isinstance(data, str) or not re.fullmatch('[a-f0-9]{64}', data):
        raise ToolchainError(f'{label} must be a lowercase SHA-256')
    return data


@dataclass(frozen=True)
class Tool:
    name: str
    executable: Path
    version_args: tuple[str, ...]
    version_contains: str
    sha256: str


@dataclass(frozen=True)
class PdkFile:
    id: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class Toolchain:
    path: Path
    tools: tuple[Tool, ...]
    pdk_root: Path
    files: tuple[PdkFile, ...]
    environment: dict[str, str]
    python_packages: dict[str, str]

    def tool(self, name: str) -> Tool:
        return next(t for t in self.tools if t.name == name)

    def pdk_file(self, name: str) -> PdkFile:
        return next(f for f in self.files if f.id == name)

    def to_dict(self) -> dict:
        return {'path': str(self.path), 'tools': [dict(name=t.name, executable=str(t.executable),
                version_args=list(t.version_args), version_contains=t.version_contains, sha256=t.sha256) for t in self.tools],
                'pdk_root': str(self.pdk_root), 'files': [dict(id=f.id, path=str(f.path), sha256=f.sha256) for f in self.files],
                'environment': self.environment, 'python_packages': self.python_packages}


def load_toolchain(path: Path | str) -> Toolchain:
    path = Path(path).resolve()
    try:
        raw = tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ToolchainError(f'cannot read toolchain {path}: {error}') from error
    keys = {'schema_version', 'environment', 'python_packages', 'tools', 'pdk'}
    _keys(raw, keys, keys, 'toolchain')
    if type(raw['schema_version']) is not int or raw['schema_version'] != 1:
        raise ToolchainError('toolchain schema_version must be 1')
    environment = _keys(raw['environment'], {'system', 'machine', 'os_id', 'os_version', 'python_version'},
                        {'system', 'machine', 'os_id', 'os_version', 'python_version'}, 'environment')
    for key, value in environment.items():
        _text(value, 'environment.' + key)
    packages = raw['python_packages']
    if not isinstance(packages, dict) or not {'klayout', 'gdstk'} <= packages.keys():
        raise ToolchainError('python_packages must pin klayout and gdstk separately from batch executables')
    for key, value in packages.items():
        _text(key, 'package name')
        _text(value, 'package version')
    if not isinstance(raw['tools'], dict):
        raise ToolchainError('tools must be a table')
    if not REQUIRED_TOOLS <= raw['tools'].keys():
        raise ToolchainError('missing required tool: ' + ', '.join(sorted(REQUIRED_TOOLS - raw['tools'].keys())))
    tools = []
    for name, item in raw['tools'].items():
        if not re.fullmatch('[a-z][a-z0-9_]*', name):
            raise ToolchainError('invalid tool identifier')
        keys = {'executable', 'version_args', 'version_contains', 'sha256'}
        _keys(item, keys, keys, 'tools.' + name)
        executable = Path(_text(item['executable'], 'executable')).expanduser()
        if not executable.is_absolute():
            executable = path.parent / executable
        executable = executable.resolve()
        args = item['version_args']
        if not isinstance(args, list) or not args:
            raise ToolchainError('version_args must be a nonempty argument array')
        args = tuple(_text(a, 'version argument') for a in args)
        tools.append(Tool(name, executable, args, _text(item['version_contains'], 'version_contains'), _sha(item['sha256'], 'tool.sha256')))
    pdk = _keys(raw['pdk'], {'root', 'files'}, {'root', 'files'}, 'pdk')
    pdk_root = Path(_text(pdk['root'], 'pdk.root')).expanduser()
    if not pdk_root.is_absolute():
        pdk_root = path.parent / pdk_root
    pdk_root = pdk_root.resolve()
    if not isinstance(pdk['files'], list):
        raise ToolchainError('pdk.files must be an array of tables')
    files = []
    for item in pdk['files']:
        _keys(item, {'id', 'path', 'sha256'}, {'id', 'path', 'sha256'}, 'pdk.files')
        identifier = _text(item['id'], 'file.id')
        relative = Path(_text(item['path'], 'file.path'))
        if relative.is_absolute() or relative.drive or '..' in relative.parts:
            raise ToolchainError('PDK file paths must be relative within pdk.root')
        resolved = (pdk_root / relative).resolve()
        if not resolved.is_relative_to(pdk_root):
            raise ToolchainError('PDK file escapes root through symlink')
        files.append(PdkFile(identifier, resolved, _sha(item['sha256'], 'file.sha256')))
    ids = [f.id for f in files]
    if len(set(ids)) != len(ids) or not REQUIRED_PDK_FILES <= set(ids):
        raise ToolchainError('duplicate PDK file IDs or missing required files: ' + ', '.join(sorted(REQUIRED_PDK_FILES - set(ids))))
    return Toolchain(path, tuple(tools), pdk_root, tuple(files), dict(environment), dict(packages))


def verify_file(path: Path, expected: str) -> str:
    if not path.is_file():
        raise ToolchainError(f'missing input: {path}')
    actual = sha256_file(path)
    if actual != expected:
        raise ToolchainError(f'fingerprint drift: {path}; expected {expected}, observed {actual}')
    return actual


def doctor(toolchain: Toolchain, output: Path) -> dict:
    """Check configured versions and hashes; never implies full-build qualification."""
    output.mkdir(parents=True, exist_ok=False)
    checks = []
    def add(identifier: str, ok: bool, message: str, **extra: Any) -> None:
        checks.append(dict(id=identifier, status='PASS' if ok else 'FAIL', message=message, **extra))
    system = platform.system()
    os_release = platform.freedesktop_os_release() if system == 'Linux' else {}
    observed = dict(system=system, machine=platform.machine(), os_id=os_release.get('ID', ''),
                    os_version=os_release.get('VERSION_ID', ''), python_version=platform.python_version())
    for key, expected in toolchain.environment.items():
        add('environment.' + key, observed[key] == expected, f'expected {expected}; observed {observed[key]}')
    for name, expected in toolchain.python_packages.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = 'MISSING'
        add('python.' + name, actual == expected, f'expected {expected}; observed {actual}')
    for tool in toolchain.tools:
        try:
            verify_file(tool.executable, tool.sha256)
            directory = output / tool.name
            directory.mkdir()
            result = run_command([str(tool.executable), *tool.version_args], directory, timeout_s=30)
            text = Path(result.stdout_log).read_text(encoding='utf-8', errors='replace') + Path(result.stderr_log).read_text(encoding='utf-8', errors='replace')
            add('tool.' + tool.name, result.succeeded and tool.version_contains in text,
                f'expected banner {tool.version_contains!r}; binary fingerprint checked',
                version_output=text.strip(), command=result.to_dict(), sha256=tool.sha256)
            if result.cancelled:
                report = dict(schema_version=1, kind='environment-doctor', checks=checks, exit_code=130,
                              full_build_qualification='NOT RUN', clean_environment_installation='NOT RUN', signoff_ready=False)
                atomic_write_json(output / 'doctor.json', report)
                return report
        except (OSError, ToolchainError) as error:
            add('tool.' + tool.name, False, str(error))
    for item in toolchain.files:
        try:
            verify_file(item.path, item.sha256)
            add('pdk.' + item.id, True, 'fingerprint checked', path=str(item.path), sha256=item.sha256)
        except (OSError, ToolchainError) as error:
            add('pdk.' + item.id, False, str(error))
    report = dict(schema_version=1, kind='environment-doctor', checks=checks,
                  exit_code=0 if all(c['status'] == 'PASS' for c in checks) else 2,
                  full_build_qualification='NOT RUN', clean_environment_installation='NOT RUN', signoff_ready=False)
    atomic_write_json(output / 'doctor.json', report)
    return report
