"""Positive evidence validation for the captured S8 reference (not chip signoff)."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/chipflow'))
from chipflow.runner.execution import atomic_write_json, sha256_file


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def drc_evidence(path: Path, expected: set[str]) -> dict:
    tree = ET.parse(path).getroot()
    if tree.tag != 'report-database':
        raise ValueError('unexpected DRC report root')
    categories = {e.findtext('name') for e in tree.findall('./categories//category')}
    if not categories or None in categories or categories != expected:
        raise ValueError('missing or changed DRC rule categories')
    markers = len(tree.findall('./items/item'))
    if markers:
        raise ValueError(f'DRC has {markers} markers')
    return dict(categories=len(categories), markers=markers)


def xunit_evidence(path: Path, expected: int) -> dict:
    cases = list(ET.parse(path).getroot().iter('testcase'))
    if len(cases) != expected or not expected:
        raise ValueError(f'expected {expected} executed tests, got {len(cases)}')
    if any(c.find(tag) is not None for c in cases for tag in ('failure', 'error', 'skipped')):
        raise ValueError('test failure/error/skip present')
    identifiers = [(c.get('classname'), c.get('name')) for c in cases]
    if len(set(identifiers)) != expected:
        raise ValueError('duplicate test identities')
    return dict(tests=len(cases), failures=0, errors=0, skipped=0)


def extraction_evidence(data: dict, spef: str, expectation: dict) -> dict:
    raw, estimate = data['openrcx_total_pF'], data['rescaled_total_pF']
    if any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in (raw, estimate)):
        raise ValueError('invalid positive parasitic totals')
    blocks = re.findall(r'^\*D_NET\s+(\S+)\s+([\d.eE+\-]+)\s*$', spef, re.M)
    if not re.search(r'^\*C_UNIT\s+1(?:\.0+)?\s+PF\s*$', spef, re.M):
        raise ValueError('unvalidated SPEF capacitance unit')
    if not blocks or len({b[0] for b in blocks}) != len(blocks) or len(re.findall(r'^\*END\s*$', spef, re.M)) != len(blocks):
        raise ValueError('empty, duplicate or truncated SPEF net blocks')
    if len(blocks) != data['nets_matched'] or data['nets_unmatched'] != 0:
        raise ValueError('SPEF/routed-length net coverage mismatch')
    if any(not math.isfinite(float(b[1])) or float(b[1]) < 0 for b in blocks):
        raise ValueError('nonfinite/negative SPEF net capacitance')
    if abs(sum(float(b[1]) for b in blocks) - raw) > 0.000051:
        raise ValueError('SPEF declared net totals disagree with the recorded raw total')
    ratio = estimate / raw
    if not expectation['raw_extraction_ratio_min'] <= ratio <= expectation['raw_extraction_ratio_max']:
        raise ValueError('raw extraction does not reproduce the predefined failure signature')
    return dict(check_id='parasitic_validation', status='FAIL', limitation_id='SCL-C1D-RCX-001',
                openrcx_total_pf=raw, diagnostic_estimate_pf=estimate, estimate_to_raw_ratio=ratio,
                matched_nets=len(blocks), unmatched_nets=0, raw_artifact_structure='PASS',
                estimate_electrical_validation='NOT RUN', full_reference_result=1)


def report_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        match = re.match(r'^\| (.*?) \| \*\*(PASS|FAIL|NOT RUN)\*\* \| (.*?) \|$', line)
        if match:
            rows.append(dict(check=match[1], result=match[2], detail=match[3]))
    if not rows or len({r['check'] for r in rows}) != len(rows):
        raise ValueError('empty/duplicate legacy report rows')
    return rows


def proof_evidence(status: str, log: str) -> dict:
    # The pinned SBY writes PASS followed by numeric diagnostic metadata.
    # Preserve the whole record; completion and process success are confirmed
    # separately from the final DONE line, never inferred from a prefix alone.
    record = status.strip()
    if not re.fullmatch(r'PASS(?:\s+\d+){0,2}', record):
        raise ValueError('formal status is not a valid PASS record')
    if not re.search(r'\bDONE \(PASS, rc=0\)\s*$', log, re.M):
        raise ValueError('missing successful proof completion')
    return dict(status='PASS', status_record=record)


def lvs_evidence(path: Path) -> dict:
    import klayout.db as db
    comparison = db.LayoutVsSchematic()
    comparison.read(str(path))
    xref = comparison.xref()
    circuits = list(xref.each_circuit_pair())
    if not circuits or any(str(c.status()) != 'Match' or c.first() is None or c.second() is None for c in circuits):
        raise ValueError('empty or unmatched LVS comparison')
    top = [c for c in circuits if c.first().name == 'OS0000' and c.second().name == 'OS0000']
    if len(top) != 1:
        raise ValueError('expected LVS top circuit is missing/duplicated')
    counts = {}
    for kind, method in (('nets', xref.each_net_pair), ('pins', xref.each_pin_pair),
                         ('devices', xref.each_device_pair), ('subcircuits', xref.each_subcircuit_pair)):
        pairs = [p for c in circuits for p in method(c)]
        if any(str(p.status()) != 'Match' for p in pairs):
            raise ValueError('LVS has incomplete matching ' + kind)
        counts[kind] = len(pairs)
    if not counts['nets'] or not counts['pins'] or not counts['subcircuits']:
        raise ValueError('empty LVS connectivity comparison')
    return dict(circuits=len(circuits), top='OS0000', matched=counts)


def collect(state_path: Path) -> dict:
    state = read(state_path)
    run, build = Path(state['run']), Path(state['run']) / 'artifacts/build'
    errors, evidence = [], {}
    def check(name, callback):
        try:
            evidence[name] = callback()
        except Exception as error:
            errors.append(dict(check=name, error=f'{type(error).__name__}: {error}'))
    old = read(ROOT / 'reference/s8/dependencies.json')
    expected_rows = {r['check']: r['result'] for r in old['baseline']['observed_historical_report_rows']}
    def physical_rows():
        rows = report_rows((run / 'artifacts/flow-report.md').read_text(encoding='utf-8'))
        if {r['check']: r['result'] for r in rows} != expected_rows:
            raise ValueError('unexpected reference report state; inspect the new rows')
        return rows
    check('legacy_report', physical_rows)
    historical_drc = Path(old['baseline']['historical_artifact_hashes']['chip/OS0000_drc.txt']['path'])
    if sha256_file(historical_drc) != old['baseline']['historical_artifact_hashes']['chip/OS0000_drc.txt']['sha256']:
        raise ValueError('historical DRC coverage reference drift')
    categories = {e.findtext('name') for e in ET.parse(historical_drc).getroot().findall('./categories//category')}
    check('drc', lambda: drc_evidence(build / 'chip/OS0000_drc.txt', categories))
    check('flat_drc', lambda: drc_evidence(build / 'chip/flat/flat_drc.txt', categories))
    check('lvs', lambda: lvs_evidence(build / 'chip/OS0000.lvsdb'))
    def clean_json(path, field='problems'):
        value = read(path)
        if value[field] or ('ok' in value and value['ok'] is not True):
            raise ValueError(f'failed {path.name}')
        return value
    check('routing_overlap', lambda: clean_json(build / 'pnr/overlaps.json'))
    check('power', lambda: clean_json(build / 'chip/power_connect.json'))
    check('sck_structure', lambda: clean_json(build / 'sta/sck_gate.json'))
    def own_geometry():
        data = read(build / 'chip/own_checks.json')
        if data['top_cells'] != ['OS0000'] or data['bbox'] != '(0,0;2000,2000)' or data['routed_vias'] <= 0:
            raise ValueError('invalid top/frame/routed inventory')
        zero_keys = ('routed_vias_over_poly', 'routed_vias_over_contact', 'drawn_shapes_off_grid',
                     'M1_wider_than_25um', 'M2_wider_than_25um', 'M1_space_with_notches', 'M2_space_with_notches')
        if any(data[k] != 0 for k in zero_keys):
            raise ValueError('unexpected geometry violation')
        return data
    check('geometry', own_geometry)
    def check_presubmit():
        report = read(build / 'chip/presubmit.json')
        old_path = Path(old['baseline']['historical_artifact_hashes']['chip/presubmit.json']['path'])
        if sha256_file(old_path) != old['baseline']['historical_artifact_hashes']['chip/presubmit.json']['sha256']:
            raise ValueError('historical checker evidence drift')
        previous = read(old_path)
        if report['tool_errors'] or report['meta']['exit_code'] != 0 or report['meta']['input_unchanged'] is not True:
            raise ValueError('checker tool error, altered input, or bad exit')
        statuses = {r['id']: r['status'] for r in report['results']}
        if len(statuses) != len(report['results']) or statuses != {r['id']: r['status'] for r in previous['results']}:
            raise ValueError('missing/duplicate/unexpected checker result state')
        return dict(summary=dict(Counter(statuses.values())), results=report['results'])
    check('presubmit', check_presubmit)
    def check_negatives():
        rows = read(build / 'chip/negative.json')
        if len(rows) != 11 or any(r['ok'] is not True for r in rows):
            raise ValueError('negative/control fixture did not produce expected evidence')
        for row in rows:
            if row['expected'] in ('pass', 'fail') and row['failed'] != (row['expected'] == 'fail'):
                raise ValueError('negative/control detection inconsistent with declared defect')
        return rows
    check('layout_negatives', check_negatives)
    check('raw_extraction', lambda: extraction_evidence(read(build / 'sta/wire_cap.json'),
        (build / 'sta/core.spef').read_text(encoding='utf-8'), state['expectation']))
    def clock_trace():
        text = (run / 'commands/clock-trace/command.stdout.log').read_text(encoding='utf-8')
        match = re.search(r'flip-flops (\d+), reaching clk through clock buffers (\d+)', text)
        expected = len(re.findall(r'^\s*DFFL11\s+', (build / 'pnr/routed.v').read_text(encoding='utf-8'), re.M))
        if match is None or not expected or tuple(map(int, match.groups())) != (expected, expected):
            raise ValueError('missing positive clock-trace result')
        return text
    check('clock_trace', clock_trace)
    def equivalence():
        text = (build / 'lec/log.txt').read_text(encoding='utf-8')
        result = proof_evidence((build / 'lec/lec/status').read_text(encoding='utf-8'), text)
        return dict(**result, liberty_sha256=sha256_file(run / 'artifacts/gate/du.lib'),
                    gold_sha256=sha256_file(build / 'lec/gold.v'), gate_sha256=sha256_file(build / 'lec/gate.v'))
    check('routed_equivalence', equivalence)
    check('rtl_tests', lambda: xunit_evidence(run / 'artifacts/sim/m1-rtl/results.xml', 20))
    for corner in ('min', 'typ', 'max'):
        check('routed_' + corner, lambda corner=corner: xunit_evidence(run / f'artifacts/sim/m1-routed-{corner}/results.xml', 20))
    def seeded():
        report = read(build / 'seeded-sdf/summary.json')
        expected = {(c, s) for c in ('min', 'typ', 'max') for s in (1, 2, 3, 4, 5)}
        if report['ok'] is not True or len(report['results']) != 15 or {(r['corner'], r['seed']) for r in report['results']} != expected:
            raise ValueError('seeded matrix incomplete or failed')
        if any(not r['ok'] or r['process_exit'] != 0 or r['tests'] != 20 for r in report['results']):
            raise ValueError('seeded test/crash evidence incomplete')
        if not report['distinct_seed_vectors'] or not report['same_seed_matches_across_corners']:
            raise ValueError('invalid seeded startup coverage')
        return dict(runs=15, tests=300, initialized_flops=report['expected_flops'], distinct_seeds=True, same_seed_across_corners=True)
    check('seeded_sdf', seeded)
    def sdf():
        rows = read(build / 'sdf-probe/summary.json')
        if len(rows) != 3 or {r['corner'] for r in rows} != {'min', 'typ', 'max'} or any(
            not r['ok'] or r['measured_ns'] <= 0 or abs(r['measured_ns'] - r['expected_ns']) > 0.002 for r in rows):
            raise ValueError('nonzero matched SDF arc evidence missing')
        return rows
    check('sdf_annotation', sdf)
    def ram(negative=False):
        name = 'ram-negative-audited' if negative else 'ram-positive-audited'
        text = (run / f'commands/{name}/command.stdout.log').read_text(encoding='utf-8')
        expected = (4, 0, 4) if negative else (36, 36, 0)
        match = re.search(r'^runs (\d+), passed (\d+), failed (\d+)$', text, re.M)
        if match is None or tuple(map(int, match.groups())) != expected:
            raise ValueError('RAM case counts/results incomplete')
        if negative:
            detections = re.findall(r'^single\s+(\w+)\s+FAIL (\d+) timing violations:', text, re.M)
            if len(detections) != 4 or {r[0] for r in detections} != {'fib', 'sieve', 'uart', 'blink'} or any(int(r[1]) <= 0 for r in detections):
                raise ValueError('RAM negative did not positively detect every intended timing violation')
            if re.search(r'OUT differs|memory errors|halt differs|no result line|Traceback', text):
                raise ValueError('RAM negative contains another failure')
        return dict(cases=expected[0], passed=expected[1], intended_timing_failures=expected[2])
    check('ram_positive', ram)
    check('ram_negative', lambda: ram(True))
    def ram_processes():
        rows = [json.loads(line) for line in (build / 'ram-process-audit.jsonl').read_text(encoding='utf-8').splitlines()]
        if len(rows) != 42 or any(row['returncode'] != 0 for row in rows):
            raise ValueError('missing or failed actual RAM simulator process')
        counts = Counter(Path(row['command'][0]).name for row in rows)
        if counts != {'iverilog': 2, 'vvp': 40}:
            raise ValueError('RAM process inventory mismatch')
        return dict(processes=len(rows), compilers=2, simulations=40, failed=0,
                    note='Negative test failure is an explicit bench timing result; simulator processes themselves succeeded.')
    check('ram_process_exits', ram_processes)
    def pads():
        data = read(build / 'pad-polarity/pad_polarity.json')
        if data['passed'] is not True or len(data['cases']) != 72 or any(r['ok'] is not True for r in data['cases']):
            raise ValueError('pad rail measurements failed or are incomplete')
        if not data['source_sha256']:
            raise ValueError('missing model provenance')
        return data
    check('pad_polarity', pads)
    def commands():
        required = {'physical', 'gate_model', 'clock-trace', 'routed-equivalence', 'rtl-tests', 'seeded-sdf', 'sdf-probe',
                    'ram-positive', 'ram-negative', 'routed-min', 'routed-typ', 'routed-max', 'pad-polarity',
                    'ram-positive-audited', 'ram-negative-audited'}
        if set(state['stages']) != required:
            raise ValueError('missing or unexpected executed command inventory')
        for name, row in state['stages'].items():
            if row['returncode'] != (1 if name in ('ram-negative', 'ram-negative-audited') else 0) or row['timed_out'] or row['cancelled'] or row['error']:
                raise ValueError('execution failed: ' + name)
        return {name: row['returncode'] for name, row in state['stages'].items()}
    check('command_outcomes', commands)
    def unchanged():
        bad = [r['original'] for r in state['inputs'] if sha256_file(Path(r['original'])) != r['sha256']]
        if bad:
            raise ValueError('live inputs changed: ' + str(bad))
        pdk = read(ROOT / 'reference/s8/pdk-files.json')
        if any(sha256_file(Path(pdk['root']) / r['path']) != r['sha256'] for r in pdk['files']):
            raise ValueError('PDK changed')
        return dict(live_inputs=len(state['inputs']), pdk_files=len(pdk['files']), unchanged=True)
    check('inputs_unchanged', unchanged)
    report = dict(schema_version=1, kind='milestone1-reference-reproduction', captured_at=datetime.now(timezone.utc).isoformat(),
                  source_rtl_sha256=sha256_file(run / 'source/rtl/s8.v'), run=str(run), errors=errors, evidence=evidence,
                  qualification='PASS' if not errors else 'FAIL', qualification_exit=0 if not errors else 2,
                  reference_design_result='INCOMPLETE' if errors else 'FAIL', reference_design_exit=2 if errors else 1,
                  raw_extraction='FAIL' if 'raw_extraction' in evidence else 'NOT RUN', signoff_ready=False,
                  chipflow_physical_adapter_qualification='NOT RUN', final_silicon_source_freeze='NOT RUN')
    previous = run / 'reference-validation.json'
    if previous.is_file():
        archived = run / ('reference-validation-attempt-' + sha256_file(previous)[:12] + '.json')
        if not archived.exists():
            archived.write_bytes(previous.read_bytes())
    atomic_write_json(previous, report)
    print(json.dumps({'qualification': report['qualification'], 'errors': errors, 'run': str(run)}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--outcome', choices=('qualification', 'design'), default='qualification',
                        help='software-reference reproduction or the actual failed design/check status')
    args = parser.parse_args()
    report = collect(args.state)
    raise SystemExit(report['qualification_exit'] if args.outcome == 'qualification' else report['reference_design_exit'])
