"""Reopen reference files in WSL, verify inventories, and emit a local toolchain."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def actual_path(value: str) -> Path:
    value = value.replace('\u00c2\u00b5', '\u00b5')
    if re.match(r'^[A-Za-z]:/', value):
        value = '/mnt/' + value[0].lower() + value[2:]
    return Path(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    reference = workspace / 'reference/s8'
    path = reference / 'dependencies.json'
    # Correct an encoding error in the interrupted initial inventory, then verify
    # all claims against actual files instead of trusting the repair alone.
    text = path.read_text(encoding='utf-8').replace('\\u00c2\\u00b5', '\\u00b5').replace('\u00c2\u00b5', '\u00b5')
    data = json.loads(text)
    verified, issues = 0, []
    for category in ('source_files', 'evidence_files', 'pdk_assets'):
        for row in data[category]:
            item = actual_path(row['path'])
            exists = item.is_file()
            observed = sha(item) if exists else None
            if observed != row.get('sha256'):
                issues.append({'path': row['path'], 'expected': row.get('sha256'), 'observed': observed})
            row['reopened_available'] = exists
            row['reopened_sha256'] = observed
            verified += int(exists and observed == row.get('sha256'))
    artifacts = []
    for name, row in data['baseline']['historical_artifact_hashes'].items():
        item = actual_path(row['path'])
        observed = sha(item) if item.is_file() else None
        artifacts.append({'name': name, 'path': str(item), 'recorded_sha256': row['sha256'],
                          'observed_sha256': observed, 'matches_record': observed == row['sha256']})
    for model in data['external_models']:
        item = Path(model['runner_reference']).expanduser()
        model['observed_path'] = str(item)
        model['observed_available'] = item.is_file()
        model['observed_sha256'] = sha(item) if item.is_file() else None
        if 'origin_sha256' in model:
            model['matches_origin'] = model['observed_sha256'] == model['origin_sha256']
            model['copied_model_availability'] = 'reopened in WSL; see observed fields'
        else:
            model['availability'] = 'reopened in WSL; see observed fields'
    pdk_roots = list((workspace / 'scl-pdk-1.2um/pdk').glob('*/SCL_C1D_PDK_17092026'))
    if len(pdk_roots) != 1:
        raise ValueError('expected one private PDK installation')
    pdk_root = pdk_roots[0]
    all_pdk = [{'path': str(p.relative_to(pdk_root)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
               for p in sorted(pdk_root.rglob('*')) if p.is_file()]
    # The full manifest fingerprints data; it does not execute any kit scripts.
    (reference / 'pdk-files.json').write_text(json.dumps({'schema_version': 1, 'root': str(pdk_root),
        'files': all_pdk}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    recheck = {'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
               'verified_inventory_files': verified, 'inventory_issues': issues,
               'historical_artifacts': artifacts, 'pdk_file_count': len(all_pdk),
               'full_reference_rebuild': 'NOT RUN', 'current_rtl_matches_historical_build': False}
    (reference / 'recheck.json').write_text(json.dumps(recheck, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    data['baseline']['artifacts_rehashed_this_capture'] = True
    data['baseline']['reason'] = 'See recheck.json for current availability and comparison to historical hashes; no new chip build.'
    data['inventory_recheck'] = {'path': 'recheck.json', 'verified_files': verified, 'issue_count': len(issues)}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    limitations_path = reference / 'limitations.json'
    limitations = json.loads(limitations_path.read_text(encoding='utf-8'))
    for limitation in limitations['limitations']:
        if limitation['id'] == 'SCL-C1D-RCX-001':
            limitation['check_id'] = 'parasitic_validation'
    limitations_path.write_text(json.dumps(limitations, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    digital = next(pdk_root.glob('Digital_C1D/my_dtech/my_dtech/*/open_source_scl_c1d/open_pdks/sclc1d'))
    cells = digital / 'digital_c1d'
    padframe = pdk_root / 'PadFrame_C1D/PadFrame_C1D/PadFrame_C1D'
    selected = {
        'tech_lef': cells / 'lef/tech_c1d.lef', 'core_lef': cells / 'lef/core_c1d.lef',
        'io_lef': cells / 'lef/io_c1d.lef', 'core_gds': cells / 'gds/core_c1d.gds',
        'io_gds': padframe / 'klayout/gds/io_pad_c1d.gds',
        'core_cdl': cells / 'cdl/core_iolib_c1d.cdl', 'io_cdl': padframe / 'klayout/lvs_cdl/io_pad_c1d.cdl',
        'frame_io_cdl': padframe / 'klayout/lvs_cdl/io_pad_c1d.cdl',
        'cells_verilog': cells / 'verilog/c1d.v', 'frame_gds': padframe / 'pad_frame_gds/frame2mmx2mm.gds',
        'frame_cdl': padframe / 'pad_frame_cdl/frame2mmx2mm.cdl',
        'drc_deck': digital / 'libs.tech/klayout/tech/drc/c1d_drc.drc',
        'lvs_deck': digital / 'libs.tech/klayout/tech/lvs/lvs_os_scl_c1d.lvs',
        'rcx_rules': digital / 'libs.tech/openrcx/scl_c1d.rcx.lib',
    }
    for corner in ('min', 'typ', 'max'):
        selected['core_' + corner + '_lib'] = cells / f'lib/c1d_core_{corner}.lib'
        selected['pads_' + corner + '_lib'] = cells / f'lib/scl1u_pads_{corner}.lib'
    environment = json.loads((reference / 'environment.json').read_text(encoding='utf-8'))
    lines = ['# PRIVATE local machine configuration; exact hashes reject installation drift.', 'schema_version = 1',
             '', '[environment]', 'system = "Linux"', 'machine = "x86_64"', 'os_id = "ubuntu"',
             'os_version = "24.04"', 'python_version = "3.12.3"', '', '[python_packages]',
             'klayout = "0.30.12"', 'gdstk = "1.0.2"']
    version_contains = dict(yosys='Yosys 0.33', openroad='26Q3', klayout='KLayout 0.30.7',
                            iverilog='Icarus Verilog version 12.0', vvp='Icarus Verilog runtime version 12.0',
                            formal_yosys='Yosys 0.70', sby='unknown SBY version', z3='5.3.2')
    q = lambda s: json.dumps(str(s), ensure_ascii=False)
    for name, row in environment['tools'].items():
        lines.extend(['', f'[tools.{name}]', 'executable = ' + q(row['path']),
                      'version_args = ' + json.dumps(row['version']['argv'][1:]),
                      'version_contains = ' + q(version_contains[name]), 'sha256 = ' + q(row['sha256'])])
    lines.extend(['', '[pdk]', 'root = ' + q(pdk_root)])
    for name, item in selected.items():
        if not item.is_file():
            raise FileNotFoundError(item)
        lines.extend(['', '[[pdk.files]]', 'id = ' + q(name),
                      'path = ' + q(item.relative_to(pdk_root)), 'sha256 = ' + q(sha(item))])
    output = workspace / 'toolchains/local.wsl.toml'
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'inventory_verified': verified, 'inventory_issues': issues,
                      'historical_artifacts_matching': sum(r['matches_record'] for r in artifacts),
                      'historical_artifacts_total': len(artifacts), 'pdk_files': len(all_pdk)}, ensure_ascii=False))
    if issues:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
