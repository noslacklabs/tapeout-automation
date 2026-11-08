"""Command line. Exit 0: no FAILs. Exit 1: FAILs. Exit 2: the tool itself errored."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import traceback

from . import __version__
from .checks import Context, Inputs, run_all
from .gdsraw import parse_gds
from .geom import Extents
from .model import FAIL, NOTICE, ToolError
from .refdata import References, load_config
from .report import format_text, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="presubmit-check",
        description="Read-only pre-submission checks for an eChipHub-SCL MPW GDSII file. " + NOTICE,
    )
    parser.add_argument("gds", metavar="file.gds", help="GDSII file to check; it is only ever read")
    parser.add_argument("--die", type=int, required=True, help="die size in mm: 1, 2, 3, 4 or 5")
    parser.add_argument("--silicon", required=True, help="silicon number, such as OS0001")
    parser.add_argument("--pdk-root", required=True, help="the kit folder holding PadFrame_C1D, Analog_C1D and Digital_C1D")
    parser.add_argument("--drc-report", help="KLayout marker database from the DRC run")
    parser.add_argument("--md5-report", help="md5sum output to compare with")
    parser.add_argument("--silicon-block", help="silicon number block GDS as issued by SCL")
    parser.add_argument("--json", dest="json_out", metavar="out.json", help="also write the full results as JSON")
    parser.add_argument("--config", help="config file; defaults to the one shipped with the tool")
    parser.add_argument("--frame-set", help="reference pad frame set named in the config; overrides its frame_set")
    parser.add_argument("--verbose", action="store_true", help="print all evidence instead of the first few lines")
    parser.add_argument("--version", action="version", version=f"presubmit-check {__version__}")
    return parser


def _same_file(a: str, b: str | None) -> bool:
    if not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def _guard_json(path: str, args, pdk_root: str) -> None:
    """Writing the report must never touch an input or the PDK."""
    for label, other in (("the GDS", args.gds), ("the DRC report", args.drc_report),
                         ("the MD5 report", args.md5_report), ("the silicon block", args.silicon_block),
                         ("the config", args.config)):
        if _same_file(path, other):
            raise ToolError(f"--json {path!r} is {label}. Refusing to overwrite an input.")
    target = os.path.realpath(path)
    root = os.path.realpath(pdk_root)
    if target == root or target.startswith(root + os.sep):
        raise ToolError(f"--json {path!r} is inside the PDK. Refusing to write there.")
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        raise ToolError(f"--json folder {folder!r} does not exist")


def _read_input(path: str) -> bytes:
    if not os.path.isfile(path):
        raise ToolError(f"GDS file not found: {path}")
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc


def run(args) -> int:
    config = load_config(args.config)
    if str(args.die) not in config["rules"]["dies"]:
        raise ToolError(f"--die must be one of {sorted(config['rules']['dies'])}, got {args.die}")
    if args.json_out:
        _guard_json(args.json_out, args, args.pdk_root)
    data = _read_input(args.gds)
    digest_before = hashlib.md5(data, usedforsecurity=False).hexdigest()
    refs = References(config, args.pdk_root, args.die, args.frame_set)

    inputs = Inputs(args.gds, args.die, args.silicon, args.drc_report, args.md5_report, args.silicon_block)
    ctx = Context(inputs, config, refs, data)
    ctx.lib = parse_gds(data, args.gds)
    ctx.extents = Extents(ctx.lib)
    tool_errors: list[str] = []

    def on_error(step: str, exc: Exception) -> None:
        tool_errors.append(f"{step}: {type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)

    run_all(ctx, on_error)

    unchanged = hashlib.md5(_read_input(args.gds), usedforsecurity=False).hexdigest() == digest_before
    if not unchanged:
        tool_errors.append("the input file changed while it was being checked")

    has_fail = any(r.status == FAIL for r in ctx.results)
    code = 2 if tool_errors else (1 if has_fail else 0)
    meta = {
        "file": args.gds,
        "die": args.die,
        "silicon": args.silicon,
        "frame_set": refs.frame_set,
        "frame_reference": str(refs.frame.path),
        "md5": digest_before,
        "input_unchanged": unchanged,
        "exit_code": code,
    }
    print(format_text(meta, ctx.results, tool_errors, args.verbose), end="")
    if args.json_out:
        write_json(args.json_out, meta, ctx.results, tool_errors)
        print(f"JSON written to {args.json_out}. It holds PDK measurements, so keep it private.")
    return code


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ToolError as exc:
        print(f"presubmit-check: error: {exc}", file=sys.stderr)
        print(NOTICE, file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print("presubmit-check: internal error; no results should be relied on.", file=sys.stderr)
        return 2
