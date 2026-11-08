"""Development CLI. Missing implementation stages always produce a nonzero result."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.resources
import json
import os
from pathlib import Path
import shutil
import sys

from . import PROFILE_VERSION, SCHEMA_VERSION, __version__
from .config import ConfigError, load_project
from .config.pads import load_pad_map
from .config.project import ProjectConfig
from .probe import run_probe
from .runner.execution import atomic_write_json, create_run, execute_stages, sha256_file
from .runner.results import Artifact, BuildResult, CheckResult, CheckStatus, StageResult, StageStatus
from .stages.design_model import load_design
from .stages.elaboration import elaboration_stage
from .technologies.profile import load_profile
from .toolchain import Toolchain, ToolchainError, doctor, load_toolchain
from .validation import validate_project_configuration


@contextmanager
def active_run(root, protected, kind):
    with create_run(root, protected) as run:
        atomic_write_json(run.path / 'result.json', dict(kind=kind, exit_code=2, complete=False,
            error='Run has not completed', chip_build='NOT RUN', signoff_ready=False))
        try:
            yield run
        except BaseException as error:
            atomic_write_json(run.path / 'result.json', dict(kind=kind,
                exit_code=130 if isinstance(error, KeyboardInterrupt) else 2, complete=False,
                error=f'{type(error).__name__}: {error}', signoff_ready=False))
            raise


def snapshot_project(project: ProjectConfig, run_dir: Path) -> Artifact:
    rows = []
    for source in project.dependencies:
        relative = source.relative_to(project.directory)
        target = run_dir / 'inputs/project' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = sha256_file(source)
        shutil.copyfile(source, target)
        if sha256_file(target) != expected or sha256_file(source) != expected:
            raise ConfigError(f'input changed while snapshotting: {source}')
        rows.append(dict(original=str(source), snapshot=str(target.relative_to(run_dir)), sha256=expected))
    manifest = run_dir / 'input-manifest.json'
    atomic_write_json(manifest, rows)
    atomic_write_json(run_dir / 'resolved-project.json', project.to_dict())
    return Artifact('input-manifest.json', sha256_file(manifest))


def _stage_to_dict(stage: StageResult) -> dict[str, Any]:
    from dataclasses import asdict
    from enum import Enum
    def convert(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        return value
    return convert(asdict(stage))


def write_run_manifest(
    run_dir: Path,
    project: ProjectConfig,
    toolchain: Toolchain,
    start_time: str,
    end_time: str,
    argv: list[str],
) -> Artifact:
    profile_path = Path(str(importlib.resources.files("chipflow").joinpath("technologies/scl_c1d/profile-0.1.0.toml")))
    profile_hash = sha256_file(profile_path)
    manifest_path = run_dir / "input-manifest.json"
    manifest_hash = sha256_file(manifest_path) if manifest_path.is_file() else "0" * 64
    toolchain_hash = sha256_file(toolchain.path)

    allowlisted_env = {
        k: os.environ[k]
        for k in ("LANG", "LC_ALL", "PATH", "PYTHONUTF8", "PYTHONIOENCODING", "TMPDIR")
        if k in os.environ
    }

    manifest_data = {
        "chipflow_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "profile_sha256": profile_hash,
        "argv": list(argv),
        "start_time": start_time,
        "end_time": end_time,
        "platform": sys.platform,
        "python_version": sys.version,
        "toolchain_sha256": toolchain_hash,
        "input_manifest_sha256": manifest_hash,
        "seed": project.resources.seed,
        "threads": project.resources.threads,
        "timeout_seconds": project.resources.timeout_seconds,
        "environment_overrides": {},
        "environment_snapshot": allowlisted_env,
    }
    out_file = run_dir / "run-manifest.json"
    atomic_write_json(out_file, manifest_data)
    return Artifact("run-manifest.json", sha256_file(out_file))


def _validation_stages(project: ProjectConfig, toolchain: Toolchain, run_path: Path, label: str):
    """Run the toolchain, elaboration and configuration gates for one run.

    Returns (toolchain_stage, elaboration_stage, configuration_stage, rows), or
    None after recording a cancelled result.
    """
    environment = doctor(toolchain, run_path / 'doctor')
    if environment['exit_code'] == 130:
        result = BuildResult((StageResult('toolchain', StageStatus.CANCELLED),), cancelled=True)
        atomic_write_json(run_path / 'result.json', result.to_dict())
        print(f'{label} cancelled: {run_path}')
        return None
    doc_evidence = Artifact('doctor/doctor.json', sha256_file(run_path / 'doctor/doctor.json'))
    toolchain_status = CheckStatus.PASS if environment['exit_code'] == 0 else CheckStatus.FAIL
    toolchain_check = CheckResult('toolchain', toolchain_status, evidence=(doc_evidence,), input_hashes={'toolchain': sha256_file(toolchain.path)})
    toolchain_stage = StageResult('toolchain', StageStatus.SUCCEEDED if toolchain_status == CheckStatus.PASS else StageStatus.FAILED,
                                  checks=(toolchain_check,), artifacts=(doc_evidence,),
                                  failure_kind=None if toolchain_status == CheckStatus.PASS else 'infrastructure')

    if toolchain_status != CheckStatus.PASS:
        elab_stage = StageResult('elaboration', StageStatus.BLOCKED, failed_ancestors=('toolchain',),
                                 checks=(CheckResult('elaboration', CheckStatus.NOT_RUN),))
    else:
        try:
            elab_spec = elaboration_stage(project, toolchain, run_path)
        except ConfigError as err:
            # Rejected before Yosys runs (parameters, defines, include names). Record
            # the reason as evidence so the FAIL row is attributable.
            rejected = run_path / 'elaboration-rejected.json'
            atomic_write_json(rejected, dict(schema_version=1, stage='elaboration', failure_kind='configuration', message=str(err)))
            evidence = Artifact(rejected.name, sha256_file(rejected))
            elab_check = CheckResult('elaboration', CheckStatus.FAIL, message=str(err), evidence=(evidence,),
                                     input_hashes={'configuration': sha256_file(run_path / 'resolved-project.json')})
            elab_stage = StageResult('elaboration', StageStatus.FAILED, checks=(elab_check,), artifacts=(evidence,),
                                     failure_kind='configuration', error=str(err))
        else:
            elab_summary = execute_stages(run_path, (elab_spec,))
            if elab_summary.cancelled:
                result = BuildResult((StageResult('elaboration', StageStatus.CANCELLED),), cancelled=True)
                atomic_write_json(run_path / 'result.json', result.to_dict())
                print(f'{label} cancelled: {run_path}')
                return None
            elab_stage = elab_summary.stages[0]

    elab_json = run_path / 'stages/elaboration/elab.json'
    design = None
    if elab_json.is_file():
        try:
            design = load_design(json.loads(elab_json.read_text(encoding='utf-8')), project.top)
        except Exception:
            design = None

    snap_pads = run_path / 'inputs/project' / project.pads.relative_to(project.directory)
    pad_cfg = None
    if snap_pads.is_file():
        try:
            pad_cfg = load_pad_map(snap_pads)
        except Exception:
            pad_cfg = None

    config_check, val_rows = validate_project_configuration(project, run_path, design, pad_cfg, load_profile())
    inherited_failed = tuple(s.id for s in (toolchain_stage, elab_stage) if s.status == StageStatus.FAILED)
    if inherited_failed:
        config_stage = StageResult('configuration', StageStatus.BLOCKED, failed_ancestors=inherited_failed,
                                   checks=(CheckResult('configuration', CheckStatus.NOT_RUN),))
    elif config_check.status == CheckStatus.FAIL:
        config_stage = StageResult('configuration', StageStatus.FAILED, failure_kind='configuration',
                                   checks=(config_check,), artifacts=config_check.evidence)
    else:
        config_stage = StageResult('configuration', StageStatus.SUCCEEDED, checks=(config_check,), artifacts=config_check.evidence)
    return toolchain_stage, elab_stage, config_stage, val_rows


def _print_summary(stages, val_rows) -> None:
    """Report what each stage actually recorded, so a blocked stage never reads as PASS."""
    for stage in stages:
        for check in stage.checks:
            print(f'  {check.id}: {check.status.value}')
            if check.status == CheckStatus.FAIL and check.message:
                print(f'    {check.message}')
    config_stage = stages[-1]
    if config_stage.status != StageStatus.BLOCKED:
        first_fail = next((r for r in val_rows if r.status == CheckStatus.FAIL), None)
        first_open = next((r for r in val_rows if r.status == CheckStatus.NOT_RUN), None)
        if first_fail:
            print(f'  First failing row: {first_fail.id}: {first_fail.message}')
        elif first_open:
            print(f'  First open row: {first_open.id}: {first_open.message}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='chipflow', description='Private development foundation; physical-flow adapters are not yet qualified.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    env_parser = commands.add_parser('doctor', help='check pinned tools/data; optionally run tiny synthesis and geometry probes')
    env_parser.add_argument('--toolchain', type=Path, required=True)
    env_parser.add_argument('--run-root', type=Path)
    env_parser.add_argument('--probe', action='store_true')
    validate = commands.add_parser('validate', help='validate project syntax; full elaboration/pad validation is pending')
    validate.add_argument('project', type=Path)
    validate.add_argument('--structure-only', action='store_true', help='explicitly limit success to structural schema/path checks')
    validate.add_argument('--toolchain', type=Path)
    validate.add_argument('--json', type=Path)
    build = commands.add_parser('build', help='snapshot preflight; exit 2 until physical-flow adapters are implemented')
    build.add_argument('project', type=Path)
    build.add_argument('--toolchain', type=Path, required=True)
    args = parser.parse_args(argv)
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == 'doctor':
            toolchain = load_toolchain(args.toolchain)
            build_root = args.run_root or toolchain.path.parent / 'runs'
            protected = (toolchain.path, toolchain.pdk_root, Path(__file__).parent)
            with active_run(build_root, protected, 'environment-gate') as run:
                atomic_write_json(run.path / 'resolved-toolchain.json', toolchain.to_dict())
                shutil.copyfile(toolchain.path, run.path / 'toolchain.toml')
                report = doctor(toolchain, run.path / 'doctor')
                code = report['exit_code']
                if args.probe and code == 0:
                    code = run_probe(toolchain, run.path / 'probe')['exit_code']
                atomic_write_json(run.path / 'result.json', dict(kind='environment-gate', exit_code=code,
                    probe_requested=args.probe, complete=code == 0, chip_build='NOT RUN', signoff_ready=False))
                print(f'Environment gate {"PASS" if code == 0 else "FAILED"}: {run.path}')
                return code

        project = load_project(args.project)

        if args.command == 'validate':
            if args.structure_only:
                toolchain = load_toolchain(args.toolchain) if args.toolchain else None
                report = dict(schema_version=1, kind='structural-validation', structure='PASS',
                              project=project.to_dict(), remaining_checks=list(project.remaining_checks),
                              full_validation='NOT RUN', toolchain_loaded=toolchain is not None,
                              exit_code=0, signoff_ready=False)
                if args.json:
                    atomic_write_json(args.json, report)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                return 0

            if not args.toolchain:
                report = dict(schema_version=1, kind='structural-validation', structure='PASS',
                              project=project.to_dict(), remaining_checks=list(project.remaining_checks),
                              full_validation='NOT RUN', toolchain_loaded=False,
                              exit_code=2, signoff_ready=False, error="full validation needs --toolchain")
                if args.json:
                    atomic_write_json(args.json, report)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                print("chipflow: full validation needs --toolchain", file=sys.stderr)
                return 2

            toolchain = load_toolchain(args.toolchain)
            protected = (*project.dependencies, *project.include_dirs, toolchain.path, toolchain.pdk_root, Path(__file__).parent)
            start_time = datetime.now(timezone.utc).isoformat()
            with active_run(project.build_root, protected, 'validation') as run:
                manifest = snapshot_project(project, run.path)
                shutil.copyfile(toolchain.path, run.path / 'toolchain.toml')
                atomic_write_json(run.path / 'resolved-toolchain.json', toolchain.to_dict())

                gates = _validation_stages(project, toolchain, run.path, 'Validation')
                if gates is None:
                    return 130
                toolchain_stage, elab_stage, config_stage, val_rows = gates

                end_time = datetime.now(timezone.utc).isoformat()
                run_manifest_art = write_run_manifest(run.path, project, toolchain, start_time, end_time, effective_argv)

                stages = [StageResult('snapshot', StageStatus.SUCCEEDED, artifacts=(manifest, run_manifest_art)),
                          toolchain_stage, elab_stage, config_stage]

                all_pass = all(
                    s.status == StageStatus.SUCCEEDED and s.checks and all(c.status == CheckStatus.PASS for c in s.checks)
                    for s in (toolchain_stage, elab_stage, config_stage)
                )
                code = 0 if all_pass else 2

                validation_result = {
                    "schema_version": 1,
                    "kind": "validation",
                    "exit_code": code,
                    "complete": code == 0,
                    "stages": [_stage_to_dict(s) for s in stages],
                    "required_checks": ["toolchain", "elaboration", "configuration"],
                    "chip_build": "NOT RUN",
                    "signoff_ready": False,
                }
                atomic_write_json(run.path / 'result.json', validation_result)
                if args.json:
                    atomic_write_json(args.json, validation_result)

                print(f'Validation: {run.path}')
                _print_summary((toolchain_stage, elab_stage, config_stage), val_rows)

                return code

        # build command
        toolchain = load_toolchain(args.toolchain)
        protected = (*project.dependencies, *project.include_dirs, toolchain.path, toolchain.pdk_root, Path(__file__).parent)
        start_time = datetime.now(timezone.utc).isoformat()
        with active_run(project.build_root, protected, 'build') as run:
            manifest = snapshot_project(project, run.path)
            shutil.copyfile(toolchain.path, run.path / 'toolchain.toml')
            atomic_write_json(run.path / 'resolved-toolchain.json', toolchain.to_dict())

            gates = _validation_stages(project, toolchain, run.path, 'Build')
            if gates is None:
                return 130
            toolchain_stage, elab_stage, config_stage, val_rows = gates

            validation_passed = (toolchain_stage.status == StageStatus.SUCCEEDED and
                                 elab_stage.status == StageStatus.SUCCEEDED and
                                 config_stage.status == StageStatus.SUCCEEDED)

            validation_check_ids = {'toolchain', 'elaboration', 'configuration'}
            impl_check_ids = [c for c in project.verification.required_checks if c not in validation_check_ids]

            if not validation_passed:
                failed_ancestors = tuple(dict.fromkeys(
                    s.id for s in (toolchain_stage, elab_stage, config_stage)
                    if s.status == StageStatus.FAILED
                ))
                impl_stage = StageResult(
                    'implementation',
                    StageStatus.BLOCKED,
                    failed_ancestors=failed_ancestors,
                    checks=tuple(CheckResult(c, CheckStatus.NOT_RUN) for c in impl_check_ids),
                )
            else:
                impl_stage = StageResult(
                    'implementation',
                    StageStatus.FAILED,
                    failure_kind='infrastructure',
                    error='physical adapters not implemented (milestone 3)',
                    checks=tuple(CheckResult(c, CheckStatus.NOT_RUN) for c in impl_check_ids),
                )

            end_time = datetime.now(timezone.utc).isoformat()
            run_manifest_art = write_run_manifest(run.path, project, toolchain, start_time, end_time, effective_argv)

            all_req = tuple(dict.fromkeys((*project.verification.required_checks, 'toolchain', 'elaboration', 'configuration')))
            stages = [StageResult('snapshot', StageStatus.SUCCEEDED, artifacts=(manifest, run_manifest_art)),
                      toolchain_stage, elab_stage, config_stage, impl_stage]
            result = BuildResult(tuple(stages), required_checks=all_req,
                                 signoff_blockers=project.verification.manual_items + ('Physical flow and release qualification pending',))
            atomic_write_json(run.path / 'result.json', result.to_dict())
            print(f'Preflight recorded; build incomplete (exit {result.exit_code}): {run.path}')
            _print_summary((toolchain_stage, elab_stage, config_stage), val_rows)
            return result.exit_code

    except KeyboardInterrupt:
        print('Cancelled', file=sys.stderr)
        return 130
    except (ConfigError, ToolchainError, OSError, ValueError) as error:
        print(f'chipflow: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
