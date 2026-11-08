"""Milestone 1 reproducible environment capture and isolated startup evidence."""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import uuid
import zipfile

BASE_URL = 'https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.3-base-amd64.tar.gz'
BASE_SHA256 = '6bc2cde3930ad088b3bb46fa45279e96d25bc3810f209850ecbe4722711874f9'
NIX = '/nix/var/nix/profiles/default/bin/nix'
NIX_STORE = '/nix/var/nix/profiles/default/bin/nix-store'


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(argv: list[str], timeout: int = 60) -> dict:
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, errors='replace', timeout=timeout, check=False)
        return dict(argv=argv, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return dict(argv=argv, error=str(e))


def inspect(root: Path) -> None:
    output = root / 'toolchains/milestone1'
    output.mkdir(parents=True, exist_ok=True)
    nix = '/nix/var/nix/profiles/default/bin/nix'
    home_dir = Path.home()
    report = dict(schema_version=1, captured_at=datetime.now(timezone.utc).isoformat(), uid=os.getuid(),
                  commands=[run(['id']), run(['which', 'chroot']), run(['which', 'proot']),
                            run(['df', '-h', str(root)]), run(['cat', '/etc/apt/sources.list.d/ubuntu.sources'])],
                  flake_evaluations={})
    for name, path, attr in [
        ('librelane_openroad', home_dir / 'silicon/upstream/librelane', 'packages.x86_64-linux.openroad.outPath'),
        ('orfs_openroad', home_dir / 'silicon/upstream/OpenROAD-flow-scripts', 'devShells.x86_64-linux.default.OPENROAD_EXE'),
        ('orfs_klayout', home_dir / 'silicon/upstream/OpenROAD-flow-scripts', 'inputs.nixpkgs.legacyPackages.x86_64-linux.klayout.outPath'),
    ]:
        report['flake_evaluations'][name] = run([nix, '--extra-experimental-features', 'nix-command flakes',
            'eval', '--offline', '--no-write-lock-file', '--raw', 'path:' + str(path) + '#' + attr], 120)
    (output / 'inspection.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'inspection': str(output / 'inspection.json'),
        'flake_outputs': {k: v.get('stdout', '').strip() for k, v in report['flake_evaluations'].items()}}))


def checked(argv: list[str], log: Path, *, cwd: Path | None = None, env: dict | None = None, timeout=1200) -> dict:
    with log.open('wb') as stream:
        p = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                           stderr=subprocess.STDOUT, check=False, timeout=timeout)
    row = dict(argv=argv, returncode=p.returncode, log=str(log))
    if p.returncode:
        raise RuntimeError(f'command exited {p.returncode}: {argv}; see {log}')
    return row


def download(url: str, destination: Path, expected: str) -> None:
    if destination.is_file() and sha(destination) == expected:
        return
    partial = destination.with_suffix(destination.suffix + '.partial')
    with urllib.request.urlopen(url, timeout=90) as response, partial.open('wb') as stream:
        shutil.copyfileobj(response, stream, 1024 * 1024)
    if sha(partial) != expected:
        raise ValueError('download fingerprint mismatch: ' + url)
    partial.replace(destination)


def installed_debian_closure() -> list[dict]:
    p = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Depends}\t${Pre-Depends}\t${Provides}\t${db:Status-Status}\n'],
                       check=True, capture_output=True, text=True)
    installed = {}
    for line in p.stdout.splitlines():
        fields = line.split('\t')
        if len(fields) == 6 and fields[5] == 'installed':
            package, version, depends, pre_depends, provides, _ = fields
            installed[package.split(':')[0]] = dict(package=package, version=version, depends=depends,
                                                   pre_depends=pre_depends, provides=provides)
    providers = {}
    for name, row in installed.items():
        for value in row['provides'].split(','):
            if value.strip():
                virtual = re.split(r'[:\s(]', value.strip())[0]
                providers.setdefault(virtual, []).append(name)
    pending = ['yosys', 'iverilog', 'python3.12', 'python3.12-venv']
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        row = installed[name]
        selected[name] = row
        for group in (row['depends'] + ',' + row['pre_depends']).split(','):
            if not group.strip():
                continue
            alternatives = [re.split(r'[:\s(]', part.strip())[0] for part in group.split('|')]
            match = next((a for a in alternatives if a in installed), None)
            if match is None:
                match = next((providers[a][0] for a in alternatives if a in providers), None)
            if match is None:
                raise ValueError(f'unresolved installed Debian dependency {name}: {group}')
            pending.append(match)
    return [selected[n] for n in sorted(selected)]


def repack_distribution(name: str, version: str, destination: Path) -> dict:
    distribution = importlib.metadata.distribution(name)
    if distribution.version != version:
        raise ValueError(f'package version drift: {name}: {distribution.version} != {version}')
    members = []
    wheel_metadata = None
    for item in distribution.files or ():
        relative = str(item)
        if relative.startswith('../') or relative.endswith(('/RECORD', '/INSTALLER', '/REQUESTED', '/direct_url.json')):
            continue
        source = Path(distribution.locate_file(item))
        if source.is_file() and '__pycache__' not in source.parts and not source.suffix == '.pyc':
            members.append((relative, source))
            if relative.endswith('.dist-info/WHEEL'):
                wheel_metadata = source.read_text(encoding='utf-8')
    if wheel_metadata is None:
        raise ValueError(f'installed distribution has no wheel metadata: {name}')
    tags = re.findall(r'^Tag: (.+)$', wheel_metadata, re.M)
    if not tags:
        raise ValueError('wheel has no platform tags')
    name_normalized = name.replace('-', '_').replace('.', '_')
    wheel = destination / f'{name_normalized}-{version}-{tags[0]}.whl'
    records = []
    with zipfile.ZipFile(wheel, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative, source in sorted(members):
            content = source.read_bytes()
            info = zipfile.ZipInfo(relative, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 << 16)
            archive.writestr(info, content)
            hashed = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
            records.append((relative, 'sha256=' + hashed, len(content)))
        metadata_dir = next(n.split('/')[0] for n, _ in members if n.endswith('.dist-info/WHEEL'))
        record_name = metadata_dir + '/RECORD'
        records.append((record_name, '', ''))
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator='\n').writerows(records)
        archive.writestr(record_name, buffer.getvalue())
    return dict(name=name, version=version, filename=wheel.name, sha256=sha(wheel), bytes=wheel.stat().st_size,
                provenance='Repacked from the recorded installed binary distribution; original METADATA/WHEEL/licenses retained, RECORD regenerated.',
                source_distribution=str(distribution.locate_file('')), file_count=len(members), platform_tags=tags)


def repack_installed_certificate_data(row: dict, destination: Path, logs: Path) -> None:
    """Retain unavailable installed certificate data as an explicit binary snapshot.

    This package is only extracted, never configured or run in the rootfs.
    Missing EDA/Python executables cannot use this fallback.
    """
    if row['package'] != 'ca-certificates':
        raise ValueError('exact Debian artifact unavailable: ' + row['package'])
    folder = Path(tempfile.mkdtemp(prefix='chipflow-m1-certificates-', dir='/tmp'))
    control = folder / 'DEBIAN'
    control.mkdir()
    metadata = subprocess.run(['dpkg-query', '-s', 'ca-certificates'], check=True, capture_output=True, text=True).stdout
    allowed = {'Package', 'Version', 'Architecture', 'Maintainer', 'Depends', 'Pre-Depends', 'Provides',
               'Replaces', 'Breaks', 'Conflicts', 'Description', 'Section', 'Priority'}
    lines, active = [], False
    for line in metadata.splitlines():
        if line and not line[0].isspace():
            active = line.split(':', 1)[0] in allowed
        if active:
            lines.append(line)
    (control / 'control').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    paths = subprocess.run(['dpkg-query', '-L', 'ca-certificates'], check=True, capture_output=True, text=True).stdout.splitlines()
    files = []
    for value in paths:
        original = Path(value)
        if not original.is_absolute() or not (original.is_file() or original.is_symlink()):
            continue
        staged = folder / original.relative_to('/')
        staged.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            staged.symlink_to(os.readlink(original))
            files.append(dict(path=value, symlink=os.readlink(original)))
        else:
            shutil.copy2(original, staged)
            files.append(dict(path=value, sha256=sha(original)))
    target = destination / f"ca-certificates_{row['version']}_all.repacked.deb"
    checked(['dpkg-deb', '--build', '--root-owner-group', str(folder), str(target)], logs / 'certificate-data-repack.log',
            env={**os.environ, 'SOURCE_DATE_EPOCH': '0'})
    row.update(filename='debs/' + target.name, sha256=sha(target), bytes=target.stat().st_size,
               provenance='Explicit installed certificate-data snapshot; repository returned 404 for this exact version. Not a vendor .deb or independent rebuild.',
               installed_files=files)


def capture(root: Path) -> None:
    output = root / 'toolchains/milestone1'
    artifacts = output / 'artifacts'
    artifacts.mkdir(parents=True, exist_ok=True)
    logs = output / 'capture-logs'
    logs.mkdir(exist_ok=True)
    manifest = {'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
                'kind': 'pinned-binary-runtime-recipe', 'independent_source_rebuild': 'NOT RUN', 'commands': []}
    def save():
        (output / 'capture.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    save()
    base = artifacts / 'ubuntu-base-24.04.3-base-amd64.tar.gz'
    download(BASE_URL, base, BASE_SHA256)
    manifest['ubuntu_base'] = dict(url=BASE_URL, filename=base.name, sha256=BASE_SHA256, bytes=base.stat().st_size,
                                  checksum_source='https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/SHA256SUMS')
    save()
    packages = installed_debian_closure()
    debs = artifacts / 'debs'
    debs.mkdir(exist_ok=True)
    specs = [p['package'] + '=' + p['version'] for p in packages]
    download_argv = ['apt-get', '-o', 'Acquire::Retries=0', 'download', *specs]
    with (logs / 'debian-download.log').open('wb') as log:
        downloaded = subprocess.run(download_argv, cwd=debs, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, check=False, timeout=1200)
    manifest['commands'].append(dict(argv=download_argv, returncode=downloaded.returncode, log=str(logs / 'debian-download.log')))
    for path in debs.glob('*.deb'):
        if path.name.endswith('.repacked.deb'):
            continue
        values = subprocess.run(['dpkg-deb', '-f', str(path), 'Package', 'Version'], check=True, capture_output=True, text=True).stdout.splitlines()
        package_name = values[0].split(': ', 1)[-1]
        version = values[1].split(': ', 1)[-1]
        row = next(p for p in packages if p['package'].split(':')[0] == package_name)
        if row['version'] != version:
            raise ValueError('Debian download version mismatch')
        row.update(filename='debs/' + path.name, sha256=sha(path), bytes=path.stat().st_size,
                   provenance='Exact-version Ubuntu repository binary package')
    for row in packages:
        if 'sha256' not in row:
            repack_installed_certificate_data(row, debs, logs)
    manifest['debian_packages'] = packages
    save()
    toolchain = tomllib.loads((root / 'toolchains/local.wsl.toml').read_text(encoding='utf-8'))
    store_roots = {str(Path(t['executable']).parent.parent) for t in toolchain['tools'].values() if t['executable'].startswith('/nix/store/')}
    ngspices = sorted(Path('/nix/store').glob('*-ngspice-45/bin/ngspice'))
    if len(ngspices) != 1:
        raise ValueError(f'expected one Ngspice45 runtime, observed {ngspices}')
    store_roots.add(str(ngspices[0].parent.parent))
    manifest['ngspice'] = dict(executable=str(ngspices[0]), sha256=sha(ngspices[0]), version=run([str(ngspices[0]), '--version']))
    query = subprocess.run([NIX_STORE, '--query', '--requisites', *sorted(store_roots)], check=True, capture_output=True, text=True)
    closure = sorted(set(query.stdout.splitlines()))
    listing = output / 'nix-runtime-paths.txt'
    listing.write_text('\n'.join(p.lstrip('/') for p in closure) + '\n', encoding='utf-8')
    (output / 'nix-runtime-info.json').write_text(subprocess.run([NIX, '--extra-experimental-features', 'nix-command', 'path-info', '--json', *closure],
        check=True, capture_output=True, text=True).stdout, encoding='utf-8')
    archive = artifacts / 'nix-runtime.tar.gz'
    if not archive.exists():
        manifest['commands'].append(checked(['tar', '-C', '/', '--sort=name', '--mtime=@0', '--owner=0', '--group=0', '--numeric-owner',
            '-czf', str(archive), '--files-from', str(listing)], logs / 'nix-runtime-archive.log'))
    manifest['nix_runtime'] = dict(filename=archive.name, sha256=sha(archive), bytes=archive.stat().st_size,
                                  roots=sorted(store_roots), paths=closure, path_count=len(closure))
    # A complete derivation graph is retained as build provenance, distinct from
    # the executable binary archive used for this milestone's startup test.
    drv_roots = subprocess.run([NIX_STORE, '--query', '--deriver', *sorted(store_roots)], check=True, capture_output=True, text=True).stdout.splitlines()
    drv_roots = [d for d in drv_roots if Path(d).is_file()]
    derivations = run([NIX, '--extra-experimental-features', 'nix-command', 'derivation', 'show', '--recursive', *drv_roots], timeout=300)
    (output / 'active-derivations.json').write_text(json.dumps(derivations, indent=2) + '\n', encoding='utf-8')
    save()
    wheels = artifacts / 'wheels'
    wheels.mkdir(exist_ok=True)
    # Capture the exact installed binary distributions. This is explicit binary
    # repackaging, not a claim that a PyPI release or clean source build was tested.
    sys.path.extend(['/home/sushant/tool-eval/venv/lib/python3.12/site-packages', str(root / 'work/package-build')])
    versions = {'pip': '24.0', 'klayout': '0.30.12', 'gdstk': '1.0.2', 'numpy': '2.5.4', 'cocotb': '2.1.0',
                'find_libpython': '0.5.1', 'pytest': '9.2.0', 'iniconfig': '2.3.1', 'packaging': '26.3',
                'pluggy': '1.7.0', 'Pygments': '2.21.0', 'setuptools': '80.9.0', 'wheel': '0.45.1'}
    manifest['python_wheels'] = [repack_distribution(name, version, wheels) for name, version in versions.items()]
    engine_wheel = root / 'dist/chipflow-0.1.0.dev1-py3-none-any.whl'
    shutil.copyfile(engine_wheel, wheels / engine_wheel.name)
    manifest['python_wheels'].append(dict(name='chipflow', version='0.1.0.dev1', filename=engine_wheel.name,
        sha256=sha(engine_wheel), bytes=engine_wheel.stat().st_size, provenance='Frozen tested project wheel'))
    checker_source = artifacts / 'checker-source'
    if not checker_source.exists():
        checker_source.mkdir()
        shutil.copyfile(root / 'tools/presubmit-check/pyproject.toml', checker_source / 'pyproject.toml')
        shutil.copytree(root / 'tools/presubmit-check/presubmit_check', checker_source / 'presubmit_check',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    manifest['commands'].append(checked([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation',
        '--disable-pip-version-check', '--wheel-dir', str(wheels), str(checker_source)], logs / 'checker-wheel.log',
        env={**os.environ, 'PYTHONPATH': str(root / 'work/package-build')}))
    checker_wheel, = wheels.glob('presubmit_check-*.whl')
    manifest['python_wheels'].append(dict(name='presubmit-check', version='0.1.0', filename=checker_wheel.name,
        sha256=sha(checker_wheel), bytes=checker_wheel.stat().st_size, provenance='Built offline from copied private checker source'))
    lock = output / 'requirements.lock'
    lock.write_text('\n'.join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}" for p in manifest['python_wheels']) + '\n', encoding='utf-8')
    manifest['requirements_lock_sha256'] = sha(lock)
    manifest['status'] = 'CAPTURED'
    save()
    print(json.dumps({'capture': str(output / 'capture.json'), 'debian_packages': len(packages),
                      'nix_paths': len(closure), 'python_wheels': len(manifest['python_wheels']),
                      'nix_archive_bytes': archive.stat().st_size}))


def isolate(root: Path) -> None:
    if os.getuid() != 0:
        raise ValueError('isolated rootfs assembly requires root solely for chroot/device creation')
    output = root / 'toolchains/milestone1'
    manifest = json.loads((output / 'capture.json').read_text(encoding='utf-8'))
    artifacts = output / 'artifacts'
    for item in [manifest['ubuntu_base'], manifest['nix_runtime'], *manifest['debian_packages']]:
        path = artifacts / item['filename']
        if sha(path) != item['sha256']:
            raise ValueError('pinned artifact drift: ' + str(path))
    for wheel in manifest['python_wheels']:
        if sha(artifacts / 'wheels' / wheel['filename']) != wheel['sha256']:
            raise ValueError('pinned wheel drift')
    sandbox = Path(tempfile.mkdtemp(prefix='chipflow-m1-fresh-', dir='/tmp'))
    report = dict(schema_version=1, started_at=datetime.now(timezone.utc).isoformat(), rootfs=str(sandbox),
                  kind='isolated-fresh-userspace-startup', commands=[], host_venv_reuse=False,
                  host_store_mounted=False, runtime_archive_import=True, independent_source_rebuild='NOT RUN',
                  clean_wsl_installation='NOT RUN', exit_code=2)
    logs = output / ('fresh-startup-' + sandbox.name.rsplit('-', 1)[-1])
    logs.mkdir()
    def save():
        (logs / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    def command(name, argv, timeout=1200):
        try:
            row = checked(argv, logs / (name + '.log'), timeout=timeout)
        except Exception as error:
            report['commands'].append(dict(argv=argv, log=str(logs / (name + '.log')), error=str(error)))
            report.update(fresh_startup='FAIL', error=str(error))
            if (sandbox / 'work').is_dir():
                shutil.copytree(sandbox / 'work', logs / 'run-evidence', dirs_exist_ok=True)
            save()
            raise
        report['commands'].append(row)
        save()
    save()
    command('base-extract', ['tar', '-xzf', str(artifacts / manifest['ubuntu_base']['filename']), '-C', str(sandbox), '--numeric-owner'])
    for index, package in enumerate(manifest['debian_packages']):
        command(f'debian-extract-{index:02d}', ['dpkg-deb', '-x', str(artifacts / package['filename']), str(sandbox)])
    command('nix-runtime-extract', ['tar', '-xzf', str(artifacts / manifest['nix_runtime']['filename']), '-C', str(sandbox), '--numeric-owner'])
    for name, major, minor in [('null', 1, 3), ('zero', 1, 5), ('random', 1, 8), ('urandom', 1, 9)]:
        path = sandbox / 'dev' / name
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(major, minor))
        os.chmod(path, 0o666)
    for relative in ('artifacts/wheels', 'config', 'external/pdk', 'home/chipflow', 'work'):
        (sandbox / relative).mkdir(parents=True, exist_ok=True)
    for relative in ('home/chipflow', 'work'):
        os.chown(sandbox / relative, 1000, 1000)
    for wheel in manifest['python_wheels']:
        shutil.copyfile(artifacts / 'wheels' / wheel['filename'], sandbox / 'artifacts/wheels' / wheel['filename'])
    shutil.copyfile(output / 'requirements.lock', sandbox / 'artifacts/requirements.lock')
    source_toolchain = root / 'toolchains/local.wsl.toml'
    toolchain = tomllib.loads(source_toolchain.read_text(encoding='utf-8'))
    pdk_root = Path(toolchain['pdk']['root'])
    path_map = []
    for item in toolchain['pdk']['files']:
        original = pdk_root / item['path']
        if sha(original) != item['sha256']:
            raise ValueError('external PDK fingerprint drift')
        target = sandbox / 'external/pdk' / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        if sha(target) != item['sha256']:
            raise ValueError('staged PDK mismatch')
        path_map.append(dict(original=str(original), staged='/external/pdk/' + item['path'], sha256=item['sha256']))
    config_text = source_toolchain.read_text(encoding='utf-8').replace(str(pdk_root), '/external/pdk')
    (sandbox / 'config/toolchain.toml').write_text(config_text, encoding='utf-8')
    shutil.copyfile(sandbox / 'config/toolchain.toml', logs / 'toolchain.toml')
    (logs / 'pdk-path-map.json').write_text(json.dumps(path_map, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    passwd = sandbox / 'etc/passwd'
    passwd.write_text(passwd.read_text() + 'chipflow:x:1000:1000:chipflow:/home/chipflow:/bin/bash\n')
    group = sandbox / 'etc/group'
    group.write_text(group.read_text() + 'chipflow:x:1000:\n')
    # Bootstrap pip from the captured wheel into a completely empty venv. This
    # does not add any host site-packages or source directory to Python's path.
    # Yosys needs /proc/self/exe. A private PID/mount namespace provides it
    # without exposing host process roots or leaving a host-wide mount behind.
    prefix = ['unshare', '--mount', '--pid', '--fork', '--propagation', 'private',
              '--mount-proc=' + str(sandbox / 'proc'), 'chroot', '--userspec=1000:1000', str(sandbox),
              '/usr/bin/env', '-i', 'HOME=/home/chipflow',
              'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', 'PYTHONIOENCODING=utf-8']
    command('python-venv', [*prefix, '/usr/bin/python3.12', '-I', '-m', 'venv', '--without-pip', '/home/chipflow/venv'])
    pip_wheel = next(p for p in manifest['python_wheels'] if p['name'] == 'pip')
    bootstrap = "import zipfile; zipfile.ZipFile('/artifacts/wheels/" + pip_wheel['filename'] + "').extractall('/home/chipflow/venv/lib/python3.12/site-packages')"
    command('bootstrap-pip', [*prefix, '/home/chipflow/venv/bin/python', '-I', '-c', bootstrap])
    command('install-wheels', [*prefix, '/home/chipflow/venv/bin/python', '-I', '-m', 'pip', 'install',
        '--no-index', '--no-deps', '--require-hashes', '--disable-pip-version-check', '--find-links=/artifacts/wheels', '-r', '/artifacts/requirements.lock'])
    checks = "import sys,json,pathlib,chipflow,klayout.db,gdstk,numpy,cocotb; assert not pathlib.Path('/home/sushant').exists(); assert not pathlib.Path('/mnt/m').exists(); assert not any('sushant' in p for p in sys.path); assert pathlib.Path(chipflow.__file__).is_relative_to('/home/chipflow/venv'); pids=sorted(p.name for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()); assert pids==['1'],pids; print(json.dumps({'sys_path':sys.path,'chipflow':chipflow.__file__,'python':sys.version,'host_home_visible':False,'host_mnt_visible':False,'visible_pids':pids}))"
    command('isolation-check', [*prefix, '/home/chipflow/venv/bin/python', '-I', '-c', checks])
    command('doctor-probe', [*prefix, '/home/chipflow/venv/bin/chipflow', 'doctor', '--toolchain', '/config/toolchain.toml',
        '--run-root', '/work/probe path \u00b5', '--probe'])
    ng = manifest['ngspice']
    command('ngspice-startup', [*prefix, ng['executable'], '--version'])
    shutil.copytree(sandbox / 'work', logs / 'run-evidence')
    report.update(exit_code=0, completed_at=datetime.now(timezone.utc).isoformat(), fresh_startup='PASS', private_pid_namespace=True,
                  toolchain_config_sha256=sha(logs / 'toolchain.toml'),
                  capture_manifest_sha256=sha(output / 'capture.json'), private_pdk_staged_files=len(path_map),
                  external_pdk_unchanged=all(sha(pdk_root / f['path']) == f['sha256'] for f in toolchain['pdk']['files']))
    save()
    (output / 'fresh-startup.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'result': str(logs / 'result.json'), 'exit_code': 0, 'rootfs': str(sandbox), 'fresh_startup': 'PASS'}))


def diagnose(root: Path) -> None:
    output = root / 'toolchains/milestone1'
    for path in sorted(output.glob('fresh-startup-*/result.json')):
        report = json.loads(path.read_text(encoding='utf-8'))
        sandbox = Path(report['rootfs'])
        if (sandbox / 'work').is_dir():
            shutil.copytree(sandbox / 'work', path.parent / 'run-evidence', dirs_exist_ok=True)
        for doctor in (path.parent / 'run-evidence').glob('**/doctor.json'):
            rows = json.loads(doctor.read_text(encoding='utf-8'))['checks']
            print(json.dumps({'run': str(path.parent), 'failed_checks': [r for r in rows if r['status'] != 'PASS']}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('action', choices=['inspect', 'capture', 'isolate', 'diagnose'])
    args = parser.parse_args()
    {'inspect': inspect, 'capture': capture, 'isolate': isolate, 'diagnose': diagnose}[args.action](args.workspace.resolve())


if __name__ == '__main__':
    main()
