"""DRC adapter: run the pinned KLayout deck on the assembled layout.

Zero markers alone is not a clean result.  The rule categories that the staged
deck will emit are derived from the deck itself, and the report must contain
exactly that nonempty set for the expected top cell before markers are counted.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any

from chipflow.config.project import ProjectConfig
from chipflow.runner.execution import (
    Artifact, Assessment, CommandResult, StageSpec, atomic_write_json, sha256_file,
)
from chipflow.runner.results import CheckResult, CheckStatus
from chipflow.stages.envelope import StageEnvelope
from chipflow.stages.evidence import EvidenceError, artifact, nonempty, unusable, verified_tool
from chipflow.stages.staging import copy_verified_dependency
from chipflow.technologies.profile import ProcessProfile
from chipflow.toolchain import Toolchain, verify_file

ADAPTER = {"id": "chipflow.drc", "version": "0.1.0"}


MARK = "\0"


def _scan_ruby(text: str) -> tuple[str, list[str]]:
    """Split deck text into code and its quoted string literals.

    Returns the code with `#` comments and =begin/=end blocks removed and each
    quoted string replaced by a numbered placeholder, plus the string bodies.
    Strings may span lines.  Other Ruby literal forms are not interpreted; a
    deck that leaves a string or comment block open is rejected.
    """
    if MARK in text:
        raise EvidenceError("deck contains a NUL character")
    code: list[str] = []
    literals: list[str] = []
    body: list[str] = []
    quote = None
    in_block = False
    for line in text.splitlines(keepends=True):
        if quote is None:
            if in_block:
                in_block = not line.startswith("=end")
                continue
            if line.startswith("=begin"):
                in_block = True
                continue
        index = 0
        while index < len(line):
            char = line[index]
            if quote:
                if char == "\\" and index + 1 < len(line):
                    body.append(line[index:index + 2])
                    index += 1
                elif char == quote:
                    code.append(f"{MARK}{len(literals)}{MARK}")
                    literals.append("".join(body))
                    body, quote = [], None
                else:
                    body.append(char)
            elif char in "\"'":
                quote = char
            elif char == "#":
                code.append("\n")
                break
            else:
                code.append(char)
            index += 1
    if quote:
        raise EvidenceError("deck has an unterminated string literal; rule categories cannot be derived")
    if in_block:
        raise EvidenceError("deck has an unterminated comment block")
    return "".join(code), literals


def deck_categories(text: str) -> tuple[str, ...]:
    """Rule categories emitted by a KLayout DRC deck's active output calls.

    Every `.output(` call must name its category with a plain string literal.
    A computed or interpolated name cannot be predicted and is rejected rather
    than silently left out of the expected set.
    """
    code, literals = _scan_ruby(text)
    names: list[str] = []
    for match in re.finditer(r"\.output\s*\(\s*", code):
        literal = re.match(rf"{MARK}(\d+){MARK}\s*[,)]", code[match.end():])
        name = literals[int(literal[1])] if literal else None
        if name is None or not name.strip() or any(token in name for token in ("#{", "\\", "\n", "\r")):
            raise EvidenceError("deck has an output call without a plain string category name")
        names.append(name)
    if not names:
        raise EvidenceError("deck declares no rule categories")
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise EvidenceError(f"deck declares {len(duplicates)} rule categories more than once")
    return tuple(names)


def categories_digest(names: Any) -> str:
    """Identity of a category set that does not disclose the kit's rule text."""
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()


def flat_deck(text: str, hierarchical_line: str, flat_line: str) -> str:
    """Switch the deck's single bare hierarchy-mode line to flat processing."""
    changed, count = re.subn(rf"(?m)^{re.escape(hierarchical_line)}[ \t]*(?=\r?$)", flat_line, text)
    if count != 1:
        raise EvidenceError(f"expected exactly one {hierarchical_line!r} mode line in the deck, found {count}")
    return changed


def read_drc_report(path: Path, top_cell: str, expected: tuple[str, ...]) -> dict[str, Any]:
    """Validate a KLayout marker database and count markers per rule category."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise EvidenceError(f"DRC report is not readable XML: {error}") from error
    if root.tag != "report-database":
        raise EvidenceError(f"unexpected DRC report root {root.tag!r}")
    if any(root.find(tag) is None for tag in ("categories", "cells", "items")):
        raise EvidenceError("DRC report lacks a categories, cells or items section")
    if (root.findtext("top-cell") or "") != top_cell:
        raise EvidenceError(f"DRC report top cell is {root.findtext('top-cell')!r}, expected {top_cell!r}")
    if root.findall("./categories/category/categories/category"):
        raise EvidenceError("DRC report has nested rule categories, which this adapter does not interpret")
    names = [element.findtext("name") or "" for element in root.findall("./categories/category")]
    if not names or "" in names or len(set(names)) != len(names):
        raise EvidenceError("DRC report has no, unnamed or duplicate rule categories")
    missing, extra = set(expected) - set(names), set(names) - set(expected)
    if missing or extra:
        raise EvidenceError(f"DRC report rule categories differ from the deck: {len(missing)} missing, {len(extra)} unexpected")
    cells = {element.findtext("name") or "" for element in root.findall("./cells/cell")}
    if top_cell not in cells:
        raise EvidenceError("DRC report cell inventory lacks the top cell")
    per_category: Counter[str] = Counter()
    for item in root.findall("./items/item"):
        category = (item.findtext("category") or "").strip()
        if len(category) >= 2 and category[0] == category[-1] == "'":
            category = category[1:-1]
        if category not in names or (item.findtext("cell") or "") not in cells:
            raise EvidenceError("DRC report has a marker outside its declared categories or cells")
        per_category[category] += 1
    return {
        "top_cell": top_cell,
        "categories": len(names),
        "categories_sha256": categories_digest(names),
        "markers": sum(per_category.values()),
        "violated_categories": len(per_category),
        "markers_by_category": dict(sorted(per_category.items())),
    }


def drc_stage(project: ProjectConfig, profile: ProcessProfile, toolchain: Toolchain,
              run_dir: Path | str, *, flat: bool = False) -> StageSpec:
    """Stage spec for hierarchical DRC, or flat DRC of the same layout and deck."""
    run_path = Path(run_dir).resolve()
    top_cell = project.top_cell
    stage_id = check_id = "drc_flat" if flat else "drc"
    settings = profile.physical_verification
    if settings is None:
        raise ValueError("process profile does not describe physical verification")
    klayout = toolchain.tool("klayout")

    def prepare(stage_dir: Path) -> StageEnvelope:
        tool_row = verified_tool(klayout)
        layout = copy_verified_dependency(run_path, stage_dir, "assembly", f"{top_cell}.gds", "layout.gds")
        deck = toolchain.pdk_file("drc_deck")
        verify_file(deck.path, deck.sha256)
        text = deck.path.read_bytes().decode("utf-8")
        transform = "none"
        if flat:
            text = flat_deck(text, settings.drc_hierarchical_line, settings.drc_flat_line)
            transform = "single-mode-line-flat-v1"
        staged = stage_dir / "deck.drc"
        staged.write_bytes(text.encode("utf-8"))
        expected = deck_categories(text)
        atomic_write_json(stage_dir / "deck-manifest.json", {
            "schema_version": 1, "source_id": "drc_deck", "source_sha256": deck.sha256,
            "staged": "deck.drc", "staged_sha256": sha256_file(staged), "transform": transform,
            "expected_categories": len(expected), "expected_categories_sha256": categories_digest(expected),
            "report_variable": settings.drc_report_variable, "top_cell": top_cell,
        })
        return StageEnvelope(
            stage_id=stage_id, adapter=ADAPTER,
            inputs=(layout, {"id": "drc_deck", "path": "deck.drc", "sha256": sha256_file(staged),
                             "bytes": staged.stat().st_size}),
            tools=(tool_row,),
            configuration_sha256=sha256_file(run_path / "resolved-project.json"),
            seed=project.resources.seed, threads=project.resources.threads,
            outputs=({"id": "drc_report", "path": "report.xml", "validator": "klayout-rdb-v1"},),
        )

    def assess(stage_dir: Path, result: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        if not result.succeeded or not artifacts:
            return unusable((check_id,), f"DRC did not complete: exit {result.returncode}")
        try:
            manifest = json.loads((stage_dir / "deck-manifest.json").read_text(encoding="utf-8"))
            verify_file(stage_dir / "deck.drc", manifest["staged_sha256"])
            expected = deck_categories((stage_dir / "deck.drc").read_bytes().decode("utf-8"))
            if categories_digest(expected) != manifest["expected_categories_sha256"]:
                raise EvidenceError("staged deck no longer matches its manifest")
            summary = read_drc_report(stage_dir / "report.xml", top_cell, expected)
        except (EvidenceError, OSError, KeyError, ValueError) as error:
            return unusable((check_id,), error)
        summary.update(mode="flat" if flat else "hierarchical", deck_transform=manifest["transform"])
        atomic_write_json(stage_dir / "drc-summary.json", summary)
        evidence = (*artifacts, artifact(run_path, stage_dir / "drc-summary.json"),
                    artifact(run_path, stage_dir / "deck-manifest.json"))
        clean = summary["markers"] == 0
        check = CheckResult(
            check_id, CheckStatus.PASS if clean else CheckStatus.FAIL,
            message=(f"{summary['categories']} rule categories ran on {top_cell}, "
                     f"{summary['markers']} markers in {summary['violated_categories']} categories"),
            evidence=evidence,
            input_hashes={"layout_gds": sha256_file(stage_dir / "layout.gds"),
                          "drc_deck_source": manifest["source_sha256"],
                          "drc_deck_staged": manifest["staged_sha256"], "klayout": klayout.sha256},
        )
        extra = evidence[len(artifacts):]
        return Assessment(checks=(check,), failure_kind=None if clean else "design", extra_artifacts=extra)

    return StageSpec(
        id=stage_id,
        command=(str(klayout.executable), "-b", "-rd", "input=layout.gds", "-rd", f"top_cell={top_cell}",
                 "-rd", f"{settings.drc_report_variable}=report.xml", "-r", "deck.drc"),
        dependencies=("assembly",),
        timeout_s=float(max(project.resources.timeout_seconds, 1800.0)),
        required_outputs=("report.xml",),
        validators={"report.xml": nonempty},
        prepare=prepare,
        assess=assess,
    )
