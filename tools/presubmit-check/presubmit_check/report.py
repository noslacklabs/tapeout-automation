"""Readable and JSON reports."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter

from . import __version__
from .model import FAIL, MANUAL, NOTICE, PASS, STATUSES, WARN, Result

EVIDENCE_LINES = 12


def _sort_key(result: Result):
    match = re.fullmatch(r"C(\d+)\.(\d+)", result.id)
    return (int(match.group(1)), int(match.group(2))) if match else (999, 0)


def sorted_results(results: list[Result]) -> list[Result]:
    return sorted(results, key=_sort_key)


def format_text(meta: dict, results: list[Result], tool_errors: list[str], verbose: bool = False) -> str:
    lines = [
        f"presubmit-check {__version__}: eChipHub-SCL MPW, SCL 1.2 um C1D",
        f"File:      {meta['file']}",
        f"Die:       {meta['die']} mm     Silicon number: {meta['silicon']}",
        f"Frame set: {meta['frame_set']}  (not confirmed by SCL)",
        f"MD5:       {meta['md5']}",
        "",
    ]
    for result in sorted_results(results):
        lines.append(f"[{result.status:<6}] {result.id}  {result.title}")
        shown = result.evidence if verbose else result.evidence[:EVIDENCE_LINES]
        for item in shown:
            lines.append(f"           {item}")
        hidden = len(result.evidence) - len(shown)
        if hidden > 0:
            lines.append(f"           ... and {hidden} more (use --verbose or --json)")
        if result.source:
            lines.append(f"           source: {result.source}")
        if result.assumption:
            lines.append(f"           assumption: {result.assumption}")
    counts = Counter(r.status for r in results)
    lines += ["", "Summary: " + ", ".join(f"{counts[s]} {s}" for s in STATUSES)]
    for error in tool_errors:
        lines.append(f"TOOL ERROR: {error}")
    if meta.get("input_unchanged") is not None:
        lines.append(f"Input file unchanged during the run: {'yes' if meta['input_unchanged'] else 'NO'}")
    lines += ["", "IMPORTANT: " + NOTICE, ""]
    lines.append(f"Exit code {meta['exit_code']}: " + _exit_meaning(meta["exit_code"]))
    return "\n".join(lines) + "\n"


def _exit_meaning(code: int) -> str:
    return {
        0: "no FAILs from these checks. Read the WARN and MANUAL items, then run DRC and LVS.",
        1: "at least one FAIL.",
        2: "the tool itself had an error, so this report is incomplete.",
    }.get(code, "unknown")


def write_json(path: str, meta: dict, results: list[Result], tool_errors: list[str]) -> None:
    payload = {
        "tool": "presubmit-check",
        "version": __version__,
        "meta": meta,
        "notice": NOTICE,
        "summary": {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, WARN, MANUAL)},
        "tool_errors": tool_errors,
        "results": [r.as_dict() for r in sorted_results(results)],
    }
    folder = os.path.dirname(os.path.abspath(path))
    fd, temp = tempfile.mkstemp(prefix=".presubmit-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
