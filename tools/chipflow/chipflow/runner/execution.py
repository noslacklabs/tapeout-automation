"""Isolated, dependency-aware command execution; no technology-specific stages.

Commands use argument arrays, never a shell. Each stage gets a new directory.
Validators must positively validate required report structure, not just existence.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence

from .results import Artifact, CheckResult, CheckStatus, StageResult, StageStatus


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path | str, data: Any) -> None:
    """Write in the destination directory, flush, then atomically replace."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_build_root(
    build_root: Path | str, protected_paths: Sequence[Path | str] = ()
) -> Path:
    """Reject filesystem roots and overlap with protected source/PDK paths."""
    root = Path(build_root).expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("build root must not be a filesystem root")
    for item in protected_paths:
        protected = Path(item).expanduser().resolve()
        if root == protected or protected.is_relative_to(root):
            raise ValueError(f"build root contains protected input: {protected}")
        if protected.is_dir() and root.is_relative_to(protected):
            raise ValueError(f"build root is inside protected input directory: {protected}")
    if root.exists() and not root.is_dir():
        raise ValueError("build root must be a directory")
    return root


class RunDirectory:
    """An active run owns an exclusive lock until closed; evidence is retained."""

    def __init__(self, path: Path):
        self.path = path
        self._lock_path = path / ".active.lock"
        self._lock = self._lock_path.open("x+b")
        self._lock.write(json.dumps({"pid": os.getpid()}).encode("ascii"))
        self._lock.flush()
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self._lock.close()
            raise

    def close(self) -> None:
        if not self._lock.closed:
            self._lock.close()
            self._lock_path.unlink(missing_ok=True)

    def __enter__(self) -> "RunDirectory":
        if self._lock.closed:
            raise RuntimeError("run lock is already closed")
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def create_run(
    build_root: Path | str, protected_paths: Sequence[Path | str] = ()
) -> RunDirectory:
    root = validate_build_root(build_root, protected_paths)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = root / f"run-{stamp}-{uuid.uuid4().hex[:12]}"
    path.mkdir(exist_ok=False)
    return RunDirectory(path)


class _WindowsJob:
    """Kill descendants when the command finishes, times out or is cancelled."""

    def __init__(self, process: subprocess.Popen[bytes]):
        # A job also owns children that outlive their immediate parent. No breakaway
        # flag is enabled. Failure to establish containment is an execution error.
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def terminate(self) -> None:
        if self.handle and not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    """Resume the primary thread after assigning the suspended process to a job.

    Popen closes the primary thread handle. Toolhelp lets us recover it using
    documented Windows APIs; containment is established before any child code.
    """
    from ctypes import wintypes

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    api.Thread32First.restype = wintypes.BOOL
    api.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    api.Thread32Next.restype = wintypes.BOOL
    api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenThread.restype = wintypes.HANDLE
    api.ResumeThread.argtypes = [wintypes.HANDLE]
    api.ResumeThread.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    snapshot = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    resumed = False
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        valid = api.Thread32First(snapshot, ctypes.byref(entry))
        while valid:
            if entry.th32OwnerProcessID == process.pid:
                thread = api.OpenThread(0x2, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if api.ResumeThread(thread) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                    resumed = True
                finally:
                    api.CloseHandle(thread)
            valid = api.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    if not resumed:
        raise OSError("cannot find suspended command's primary thread")


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    cwd: str
    returncode: int | None
    timed_out: bool
    cancelled: bool
    error: str | None
    stdout_log: str
    stderr_log: str
    started_at: str
    duration_s: float

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not (self.timed_out or self.cancelled or self.error)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stop_process(process: subprocess.Popen[bytes], job: _WindowsJob | None) -> None:
    if os.name == "nt":
        if job is not None:
            job.terminate()
        elif process.poll() is None:
            # Only needed if job creation itself failed.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def run_command(
    argv: Sequence[str], cwd: Path | str, timeout_s: float = 300,
    env: Mapping[str, str] | None = None,
    cancellation: threading.Event | None = None, log_prefix: str = "command",
) -> CommandResult:
    """Run one command in its own process group, recording separate log files."""
    if not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv) or not argv[0]:
        raise ValueError("command must be a nonempty sequence of string arguments")
    if isinstance(argv, (str, bytes)):
        raise ValueError("command must be an argument array, not shell text")
    if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", log_prefix):
        raise ValueError("invalid log prefix")
    if env is not None and any(
        not isinstance(key, str) or not isinstance(value, str) or not key or "=" in key or "\0" in key + value
        for key, value in env.items()
    ):
        raise ValueError("environment overrides must be valid string pairs")
    directory = Path(cwd).resolve(strict=True)
    stdout_path = directory / f"{log_prefix}.stdout.log"
    stderr_path = directory / f"{log_prefix}.stderr.log"
    command_path = directory / f"{log_prefix}.json"
    if any(path.exists() for path in (stdout_path, stderr_path, command_path)):
        raise FileExistsError("command evidence already exists; use a fresh directory or log prefix")
    atomic_write_json(command_path, {
        "argv": list(argv), "cwd": str(directory), "timeout_s": timeout_s,
        "environment_overrides": dict(env or {}), "shell": False,
    })
    started_at = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    process = None
    job = None
    timed_out = False
    cancelled = bool(cancellation and cancellation.is_set())
    error = None
    returncode = None
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            if not cancelled:
                options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4,
                }
                process = subprocess.Popen(
                    list(argv), cwd=directory, env={**os.environ, **dict(env or {})},
                    stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, shell=False, **options,
                )
                if os.name == "nt":
                    job = _WindowsJob(process)
                    _resume_windows_process(process)
                while process.poll() is None:
                    if cancellation is not None and cancellation.is_set():
                        cancelled = True
                        _stop_process(process, job)
                        break
                    if time.monotonic() - start >= timeout_s:
                        timed_out = True
                        _stop_process(process, job)
                        break
                    time.sleep(min(0.05, timeout_s))
                returncode = process.wait()
    except KeyboardInterrupt:
        cancelled = True
        if cancellation is not None:
            cancellation.set()
        if process is not None:
            _stop_process(process, job)
            returncode = process.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        if process is not None:
            _stop_process(process, job)
            returncode = process.returncode
    finally:
        if job is not None:
            job.close()
        elif process is not None and os.name != "nt":
            # Stop orphaned background children even when their parent exited 0.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    result = CommandResult(tuple(argv), str(directory), returncode, timed_out, cancelled, error,
                           str(stdout_path), str(stderr_path), started_at, time.monotonic() - start)
    atomic_write_json(directory / f"{log_prefix}.result.json", result.to_dict())
    return result


@dataclass(frozen=True)
class Assessment:
    checks: tuple[CheckResult, ...] = ()
    failure_kind: str | None = None
    extra_artifacts: tuple[Artifact, ...] = ()

    def __post_init__(self) -> None:
        if self.failure_kind not in (None, "design", "infrastructure", "configuration"):
            raise ValueError("failure_kind must be design, infrastructure, configuration or None")


@dataclass(frozen=True)
class StageSpec:
    id: str
    command: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    timeout_s: float = 300
    required_outputs: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    validators: Mapping[str, Callable[[Path], None]] = field(default_factory=dict)
    prepare: Callable[[Path], Any] | None = None
    assess: Callable[[Path, CommandResult, tuple[Artifact, ...]], Assessment] | None = None
    diagnostic_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionSummary:
    stages: tuple[StageResult, ...]
    commands: tuple[CommandResult, ...]
    cancelled: bool


def _validate_stages(stages: Sequence[StageSpec]) -> tuple[StageSpec, ...]:
    if not stages:
        raise ValueError("stage graph must not be empty")
    ids: set[str] = set()
    for stage in stages:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", stage.id) or stage.id in (".", ".."):
            raise ValueError(f"invalid stage id: {stage.id!r}")
        if stage.id in ids:
            raise ValueError(f"duplicate stage: {stage.id}")
        ids.add(stage.id)
        if len(stage.dependencies) != len(set(stage.dependencies)):
            raise ValueError(f"duplicate dependency for {stage.id}")
        if len(stage.diagnostic_inputs) != len(set(stage.diagnostic_inputs)):
            raise ValueError(f"duplicate diagnostic input for {stage.id}")
        if set(stage.diagnostic_inputs) - set(stage.dependencies):
            raise ValueError(f"diagnostic_inputs must be a subset of dependencies for {stage.id}")
        if len(stage.required_outputs) != len(set(stage.required_outputs)):
            raise ValueError(f"duplicate output for {stage.id}")
        if isinstance(stage.command, (str, bytes)) or not stage.command or any(
            not isinstance(arg, str) or "\0" in arg for arg in stage.command
        ) or not stage.command[0]:
            raise ValueError(f"invalid argument array for {stage.id}")
        if isinstance(stage.timeout_s, bool) or not math.isfinite(stage.timeout_s) or stage.timeout_s <= 0:
            raise ValueError(f"invalid timeout for {stage.id}")
        if any(not isinstance(k, str) or not isinstance(v, str) or not k or "=" in k or "\0" in k + v
               for k, v in stage.env.items()):
            raise ValueError(f"invalid environment for {stage.id}")
        for output in (*stage.required_outputs, *stage.validators):
            path = Path(output)
            windows = PureWindowsPath(output)
            if path.is_absolute() or path.drive or windows.drive or ".." in path.parts or ".." in windows.parts or not output or path == Path("."):
                raise ValueError(f"unsafe stage output: {output!r}")
        if set(stage.validators) - set(stage.required_outputs):
            raise ValueError("validators must refer to required outputs")
    pending = list(stages)
    ordered: list[StageSpec] = []
    done: set[str] = set()
    for stage in pending:
        if set(stage.dependencies) - ids:
            raise ValueError(f"unknown dependency for {stage.id}")
    while pending:
        ready = [stage for stage in pending if set(stage.dependencies) <= done]
        if not ready:
            raise ValueError("stage dependency cycle")
        for stage in ready:
            pending.remove(stage)
            ordered.append(stage)
            done.add(stage.id)
    return tuple(ordered)


def execute_stages(
    run_dir: Path | str, stages: Sequence[StageSpec],
    cancellation: threading.Event | None = None,
) -> ExecutionSummary:
    """Run a DAG sequentially; failures block descendants, not independent work.

    This only reports execution/artifact validity. Successful commands alone do
    not establish a PASS check. Technology adapters must add positive checks.
    """
    ordered = _validate_stages(stages)
    directory = Path(run_dir).resolve(strict=True)
    stage_root = directory / "stages"
    stage_root.mkdir(exist_ok=False)
    results: list[StageResult] = []
    commands: list[CommandResult] = []
    by_id: dict[str, StageResult] = {}
    cancelled = False
    for stage in ordered:
        if cancelled or (cancellation is not None and cancellation.is_set()):
            cancelled = True
            result = StageResult(stage.id, StageStatus.CANCELLED, error="run cancelled")
        else:
            inherited: list[str] = []
            blocked_by: list[str] = []
            for dep in stage.dependencies:
                r = by_id[dep]
                inherited.extend(r.failed_ancestors)
                if r.status == StageStatus.SUCCEEDED:
                    continue
                if (dep in stage.diagnostic_inputs and r.status == StageStatus.FAILED
                        and r.failure_kind == "design" and r.artifacts):
                    inherited.append(dep)
                    continue
                blocked_by.append(dep)
            ancestors = tuple(dict.fromkeys(inherited + blocked_by))
            if blocked_by:
                result = StageResult(stage.id, StageStatus.BLOCKED, error="dependency did not succeed",
                                     failed_ancestors=ancestors)
            else:
                stage_dir = stage_root / stage.id
                stage_dir.mkdir(exist_ok=False)
                prepare_failed = False
                if stage.prepare is not None:
                    try:
                        envelope = stage.prepare(stage_dir)
                        if hasattr(envelope, "write"):
                            envelope.write(stage_dir / "stage-input.json")
                        elif isinstance(envelope, dict):
                            atomic_write_json(stage_dir / "stage-input.json", envelope)
                        else:
                            raise TypeError("prepare must return a stage envelope")
                    except Exception as exc:
                        prepare_failed = True
                        result = StageResult(stage.id, StageStatus.FAILED, error=f"prepare failed: {exc}",
                                             failure_kind="infrastructure", failed_ancestors=ancestors)
                if not prepare_failed:
                    command = run_command(stage.command, stage_dir, stage.timeout_s, stage.env, cancellation)
                    commands.append(command)
                    if command.cancelled:
                        cancelled = True
                        result = StageResult(stage.id, StageStatus.CANCELLED, error="command cancelled",
                                             failed_ancestors=ancestors)
                    elif command.timed_out:
                        result = StageResult(stage.id, StageStatus.FAILED, error="command timed out",
                                             failure_kind="infrastructure", failed_ancestors=ancestors)
                    elif not command.succeeded:
                        if stage.assess is not None:
                            try:
                                assessment = stage.assess(stage_dir, command, ())
                                if assessment.failure_kind == "configuration" and any(c.status == CheckStatus.FAIL for c in assessment.checks):
                                    result = StageResult(stage.id, StageStatus.FAILED, checks=assessment.checks,
                                                         artifacts=assessment.extra_artifacts,
                                                         error=command.error or f"command exited {command.returncode}",
                                                         failure_kind="configuration", failed_ancestors=ancestors)
                                else:
                                    reason = command.error or f"command exited {command.returncode}"
                                    result = StageResult(stage.id, StageStatus.FAILED, checks=assessment.checks,
                                                         artifacts=assessment.extra_artifacts, error=reason,
                                                         failure_kind="infrastructure", failed_ancestors=ancestors)
                            except Exception as exc:
                                result = StageResult(stage.id, StageStatus.FAILED, error=f"assessment failed: {exc}",
                                                     failure_kind="infrastructure", failed_ancestors=ancestors)
                        else:
                            reason = command.error or f"command exited {command.returncode}"
                            result = StageResult(stage.id, StageStatus.FAILED, error=reason,
                                                 failure_kind="infrastructure", failed_ancestors=ancestors)
                    else:
                        artifacts: list[Artifact] = []
                        output_valid = True
                        try:
                            for output in stage.required_outputs:
                                path = stage_dir / output
                                if not path.resolve().is_relative_to(stage_dir.resolve()):
                                    raise ValueError(f"required output escapes stage directory: {output}")
                                if not path.is_file() or path.stat().st_size == 0:
                                    raise ValueError(f"required output missing or empty: {output}")
                                if output in stage.validators:
                                    stage.validators[output](path)
                                artifacts.append(Artifact(str(path.relative_to(directory)), sha256_file(path)))
                        except Exception as exc:
                            output_valid = False
                            result = StageResult(stage.id, StageStatus.FAILED, error=f"invalid output: {exc}",
                                                 failure_kind="infrastructure", failed_ancestors=ancestors)
                        if output_valid:
                            if stage.assess is not None:
                                try:
                                    assessment = stage.assess(stage_dir, command, tuple(artifacts))
                                    all_artifacts = tuple(artifacts) + tuple(assessment.extra_artifacts)
                                    has_fail = any(c.status == CheckStatus.FAIL for c in assessment.checks)
                                    if assessment.failure_kind == "infrastructure":
                                        # The adapter says its evidence is unusable; never downgrade that.
                                        result = StageResult(stage.id, StageStatus.FAILED, checks=assessment.checks,
                                                             artifacts=all_artifacts, error="assessment reported an infrastructure failure",
                                                             failure_kind="infrastructure", failed_ancestors=ancestors)
                                    elif assessment.failure_kind in ("design", "configuration") and not has_fail:
                                        result = StageResult(stage.id, StageStatus.FAILED, checks=assessment.checks,
                                                             artifacts=all_artifacts,
                                                             error=f"assessment reported a {assessment.failure_kind} failure without a FAIL check",
                                                             failure_kind="infrastructure", failed_ancestors=ancestors)
                                    elif has_fail:
                                        kind = assessment.failure_kind or "design"
                                        result = StageResult(stage.id, StageStatus.FAILED, checks=assessment.checks,
                                                             artifacts=all_artifacts, failure_kind=kind,
                                                             failed_ancestors=ancestors)
                                    else:
                                        result = StageResult(stage.id, StageStatus.SUCCEEDED, checks=assessment.checks,
                                                             artifacts=all_artifacts, failed_ancestors=ancestors)
                                except Exception as exc:
                                    result = StageResult(stage.id, StageStatus.FAILED, error=f"assessment failed: {exc}",
                                                         failure_kind="infrastructure", failed_ancestors=ancestors)
                            else:
                                result = StageResult(stage.id, StageStatus.SUCCEEDED, artifacts=tuple(artifacts),
                                                     failed_ancestors=ancestors)
        results.append(result)
        by_id[stage.id] = result
        atomic_write_json(directory / "execution.json", {
            "stages": [item.to_dict() if hasattr(item, "to_dict") else asdict(item) for item in results],
            "cancelled": cancelled, "complete": len(results) == len(ordered) and not cancelled,
        })
    return ExecutionSummary(tuple(results), tuple(commands), cancelled)
