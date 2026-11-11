"""DRC, LVS and presubmission adapters: evidence rules and stage outcomes.

Tool processes are replaced by a script that writes planned files and exits
with a planned code, so every defect below is exercised without the PDK.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from chipflow.runner.execution import StageSpec, execute_stages, sha256_file
from chipflow.runner.results import Artifact, CheckStatus, StageResult, StageStatus
from chipflow.stages import lvs as lvs_module, presubmit as presubmit_module
from chipflow.stages.drc import deck_categories, drc_stage, flat_deck, read_drc_report
from chipflow.stages.evidence import EvidenceError
from chipflow.stages.lvs import lvs_stage, lvs_verdict
from chipflow.stages.presubmit import EXPECTED_ROWS, presubmit_stage, presubmit_verdict, submission_name
from chipflow.technologies.profile import load_profile
from chipflow.toolchain import PdkFile, Tool, Toolchain

TOP = "OS0000"
DECK = """# header: x.output("commented out", "never")
source($input, $top_cell) if $input
report("demo", $report1)
deep
m1 = input(8, 0)
log("a string over two lines # x.output('inside the string')
")
m1.width(1.0).output("M1.width : min 1.0", "text with # inside")
m1.space(1.0).output('M1.space', "d")  # trailing x.output("also a comment")
=begin
x.output("in a block comment")
=end
"""
CATEGORIES = ("M1.width : min 1.0", "M1.space")
FAKE_TOOL = """import json, os, sys
plan = json.loads(open(os.environ["CHIPFLOW_FAKE_TOOL_PLAN"], encoding="utf-8").read())
for name, text in plan.get("files", {}).items():
    with open(name, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)
sys.exit(plan.get("exit", 0))
"""


def drc_report(categories=CATEGORIES, markers=(), top=TOP, root="report-database", cells=(TOP,)):
    names = "".join(f"<category><name>{c}</name><description/><categories/></category>" for c in categories)
    cell_rows = "".join(f"<cell><name>{c}</name><variant/><references/></cell>" for c in cells)
    items = "".join(f"<item><tags/><category>'{c}'</category><cell>{cell}</cell><values/></item>"
                    for c, cell in markers)
    return (f'<?xml version="1.0" encoding="utf-8"?><{root}><description>demo</description>'
            f"<top-cell>{top}</top-cell><tags/><categories>{names}</categories>"
            f"<cells>{cell_rows}</cells><items>{items}</items></{root}>")


def lvs_summary(**changes):
    def circuit(name, **extra):
        row = {"layout": name, "schematic": name, "status": "Match", "nets": {"Match": 4},
               "pins": {"Match": 3}, "devices": {"Match": 2}, "subcircuits": {"Match": 1}}
        row.update(extra)
        return row
    circuits = [circuit("INVR04", subcircuits={}), circuit(TOP, devices={})]
    circuits[1].update(changes)
    return {"circuits": circuits}


def presubmit_report(statuses=None, *, exit_code=0, md5="0" * 32, version="0.1.0", errors=(), drop=()):
    statuses = {"C04.2": "WARN", "C18.1": "MANUAL", **(statuses or {})}
    rows = [{"id": i, "status": statuses.get(i, "PASS"), "title": f"rule {i}", "evidence": []}
            for i in sorted(EXPECTED_ROWS["0.1.0"]) if i not in drop]
    summary = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    return {"tool": "presubmit-check", "version": version, "summary": summary, "tool_errors": list(errors),
            "meta": {"die": 2, "silicon": TOP, "md5": md5, "input_unchanged": True, "exit_code": exit_code},
            "results": rows}


class DeckAndReportTests(unittest.TestCase):
    def test_categories_come_from_active_output_calls_only(self):
        self.assertEqual(deck_categories(DECK), CATEGORIES)

    def test_unpredictable_or_absent_categories_are_rejected(self):
        for text, reason in (("x.output(name, 'd')", "plain string"),
                             ('x.output("rule #{n}", "d")', "plain string"),
                             ("# x.output('only a comment')", "no rule categories"),
                             ('a.output("same", "d")\nb.output("same", "d")', "more than once"),
                             ("=begin\nx.output('a')", "unterminated comment"),
                             ('x.output("a", "d")\ny = "never closed', "unterminated string"),
                             ('x.output("two\nlines", "d")', "plain string")):
            with self.subTest(text=text), self.assertRaisesRegex(EvidenceError, reason):
                deck_categories(text)

    def test_flat_transform_replaces_exactly_one_bare_mode_line(self):
        flat = flat_deck(DECK.replace("\n", "\r\n"), "deep", "flat")
        self.assertIn("\r\nflat\r\n", flat)
        self.assertNotIn("\r\ndeep\r\n", flat)
        self.assertEqual(deck_categories(flat), CATEGORIES)
        for text in (DECK.replace("deep\n", ""), DECK + "deep\n", DECK.replace("deep\n", "deep_mode\n")):
            with self.subTest(text=text[-20:]), self.assertRaisesRegex(EvidenceError, "exactly one"):
                flat_deck(text, "deep", "flat")

    def check_report(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.xml"
            path.write_text(text, encoding="utf-8")
            return read_drc_report(path, TOP, CATEGORIES)

    def test_clean_report_and_marker_counts(self):
        clean = self.check_report(drc_report())
        self.assertEqual((clean["categories"], clean["markers"]), (2, 0))
        dirty = self.check_report(drc_report(markers=[("M1.space", TOP), ("M1.space", TOP), ("M1.width : min 1.0", TOP)]))
        self.assertEqual((dirty["markers"], dirty["violated_categories"]), (3, 2))
        self.assertEqual(dirty["markers_by_category"]["M1.space"], 2)

    def test_reports_that_support_no_verdict(self):
        cases = {
            "empty categories": drc_report(categories=()),
            "missing category": drc_report(categories=CATEGORIES[:1]),
            "unexpected category": drc_report(categories=(*CATEGORIES, "extra")),
            "duplicate category": drc_report(categories=(*CATEGORIES, CATEGORIES[0])),
            "wrong top": drc_report(top="OTHER", cells=("OTHER",)),
            "top absent from cells": drc_report(cells=("OTHER",)),
            "wrong root": drc_report(root="something-else"),
            "truncated": drc_report()[:-40],
            "marker in undeclared category": drc_report(markers=[("unknown", TOP)]),
            "marker in undeclared cell": drc_report(markers=[("M1.space", "nowhere")]),
            "no items section": drc_report().replace("<items></items>", ""),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(EvidenceError):
                self.check_report(text)


class LvsVerdictTests(unittest.TestCase):
    def test_complete_match(self):
        verdict = lvs_verdict(lvs_summary(), TOP)
        self.assertTrue(verdict["match"])
        self.assertEqual(verdict["matched"], {"nets": 8, "pins": 6, "devices": 2, "subcircuits": 1})

    def test_mismatches_are_design_failures(self):
        for change, text in (({"status": "NoMatch"}, "circuit NoMatch"),
                             ({"nets": {"Match": 3, "Mismatch": 1}}, "nets 1 Mismatch"),
                             ({"status": "Skipped"}, "circuit Skipped"),
                             ({"devices": {"MatchWithWarning": 2}}, "devices 2 MatchWithWarning"),
                             ({"schematic": None, "status": "Mismatch"}, "one side only")):
            with self.subTest(change=change):
                verdict = lvs_verdict(lvs_summary(**change), TOP)
                self.assertFalse(verdict["match"])
                self.assertTrue(any(text in problem for problem in verdict["problems"]), verdict["problems"])

    def test_comparisons_that_support_no_verdict(self):
        empty_top = lvs_summary(nets={}, pins={}, subcircuits={})
        no_top = {"circuits": [c for c in lvs_summary()["circuits"] if c["layout"] != TOP]}
        twice = {"circuits": lvs_summary()["circuits"] + lvs_summary()["circuits"][1:]}
        malformed = lvs_summary(nets=None)
        for name, summary in (("empty", {"circuits": []}), ("absent", {}), ("no top", no_top),
                              ("top twice", twice), ("nothing matched at top", empty_top),
                              ("malformed counts", malformed)):
            with self.subTest(name), self.assertRaises(EvidenceError):
                lvs_verdict(summary, TOP)


class PresubmitVerdictTests(unittest.TestCase):
    def verdict(self, report, code=0):
        return presubmit_verdict(report, code, top_cell=TOP, die_mm=2, gds_md5="0" * 32)

    def test_clean_run_keeps_warn_and_manual_rows(self):
        verdict = self.verdict(presubmit_report())
        self.assertEqual(verdict["counts"], {"PASS": 51, "FAIL": 0, "WARN": 1, "MANUAL": 1})
        self.assertEqual([row["id"] for row in verdict["warn"]], ["C04.2"])
        self.assertEqual([row["id"] for row in verdict["manual"]], ["C18.1"])

    def test_failed_rule_with_matching_exit(self):
        report = presubmit_report({"C03.1": "FAIL"}, exit_code=1, drop=("C03.2",))
        verdict = self.verdict(report, 1)
        self.assertEqual([row["id"] for row in verdict["fail"]], ["C03.1"])
        self.assertEqual(verdict["not_reported_after_failure"], ["C03.2"])

    def test_conditional_reference_warning_rows_are_accepted(self):
        report = presubmit_report()
        report["results"].append({"id": "C11.3", "status": "WARN", "title": "list size", "evidence": []})
        report["summary"]["WARN"] += 1
        self.assertEqual(len(self.verdict(report)["warn"]), 2)

    def test_reports_that_support_no_verdict(self):
        extra = presubmit_report()
        extra["results"].append({"id": "C99.1", "status": "PASS", "title": "new", "evidence": []})
        extra["summary"]["PASS"] += 1
        summary = presubmit_report()
        summary["summary"]["PASS"] -= 1
        untitled = presubmit_report()
        untitled["results"][0]["title"] = " "
        changed = presubmit_report()
        changed["meta"]["input_unchanged"] = False
        cases = [
            ("tool errors", presubmit_report(errors=["check_03: ValueError"], exit_code=2), 2, "tool error"),
            ("exit differs from report", presubmit_report(), 1, "exited 1"),
            ("missing row in clean run", presubmit_report(drop=("C19.2",)), 0, "inventory"),
            ("unknown row", extra, 0, "inventory"),
            ("unqualified version", presubmit_report(version="9.9"), 0, "unqualified"),
            ("other layout", presubmit_report(md5="1" * 32), 0, "staged layout"),
            ("summary disagrees", summary, 0, "summary"),
            ("fail row with exit 0", presubmit_report({"C03.1": "FAIL"}), 0, "disagrees"),
            ("blank title", untitled, 0, "title"),
            ("input changed", changed, 0, "unchanged"),
            ("other tool", {**presubmit_report(), "tool": "other"}, 0, "presubmit-check"),
        ]
        for name, report, code, reason in cases:
            with self.subTest(name), self.assertRaisesRegex(EvidenceError, reason):
                self.verdict(report, code)

    def test_development_file_name_uses_the_run_date(self):
        self.assertEqual(submission_name(TOP, "s8", Path("run-20261108T145307.485099Z-502c9d6bea7e")),
                         "OS0000_S8_08112026_v0")
        with self.assertRaises(EvidenceError):
            submission_name(TOP, "--", Path("run-20261108T145307.485099Z-502c9d6bea7e"))


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chipflow verify ")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "space µ"
        self.run = root / "run-20261108T145307.485099Z-502c9d6bea7e"
        assembly = self.run / "stages/assembly"
        assembly.mkdir(parents=True)
        (assembly / f"{TOP}.gds").write_bytes(b"layout bytes")
        (assembly / f"{TOP}.cdl").write_text(".SUBCKT OS0000\n.ENDS\n", encoding="utf-8")
        (self.run / "resolved-project.json").write_text("{}", encoding="utf-8")
        self.kit = root / "kit"
        self.kit.mkdir()
        (self.kit / "deck.drc").write_bytes(DECK.encode("utf-8"))
        (self.kit / "deck.lvs").write_text("# lvs deck\n", encoding="utf-8")
        self.tool = root / "fake_tool.py"
        self.tool.write_text(FAKE_TOOL, encoding="utf-8")
        self.plan = root / "plan.json"
        environment = mock.patch.dict(os.environ, {"CHIPFLOW_FAKE_TOOL_PLAN": str(self.plan)})
        environment.start()
        self.addCleanup(environment.stop)
        self.toolchain = Toolchain(
            root / "toolchain.toml",
            (Tool("klayout", self.tool, ("-v",), "fake", sha256_file(self.tool)),),
            self.kit,
            (PdkFile("drc_deck", self.kit / "deck.drc", sha256_file(self.kit / "deck.drc")),
             PdkFile("lvs_deck", self.kit / "deck.lvs", sha256_file(self.kit / "deck.lvs"))),
            {}, {})
        self.project = SimpleNamespace(top_cell=TOP, name="s8", development_identifiers=True,
                                       resources=SimpleNamespace(seed=1, threads=1, timeout_seconds=60))
        self.profile = load_profile()
        self.done = [self.produced("assembly", f"{TOP}.gds", f"{TOP}.cdl")]
        self.record()

    def produced(self, stage, *names):
        return StageResult(stage, StageStatus.SUCCEEDED, artifacts=tuple(
            Artifact(f"stages/{stage}/{name}", sha256_file(self.run / "stages" / stage / name)) for name in names))

    def record(self):
        (self.run / "execution.json").write_text(json.dumps({"stages": [
            {"id": s.id, "status": "succeeded", "artifacts": [dataclasses.asdict(a) for a in s.artifacts]}
            for s in self.done]}), encoding="utf-8")

    def execute(self, spec: StageSpec, files=None, exit_code=0):
        """Run one adapter with the planned tool in place of its executable."""
        self.plan.write_text(json.dumps({"files": files or {}, "exit": exit_code}), encoding="utf-8")
        arguments = spec.command[spec.command.index("presubmit_check") + 1:] if "presubmit_check" in spec.command \
            else spec.command[1:]
        spec = dataclasses.replace(spec, command=(sys.executable, str(self.tool), *arguments))
        summary = execute_stages(self.run, [spec], existing_stages=self.done)
        return summary.stages[0]

    def drc(self, **options):
        return drc_stage(self.project, self.profile, self.toolchain, self.run, **options)

    def test_drc_clean_pass_has_evidence_and_input_identity(self):
        stage = self.execute(self.drc(), {"report.xml": drc_report()})
        self.assertEqual(stage.status, StageStatus.SUCCEEDED)
        check, = stage.checks
        self.assertEqual((check.id, check.status), ("drc", CheckStatus.PASS))
        self.assertEqual(set(check.input_hashes), {"layout_gds", "drc_deck_source", "drc_deck_staged", "klayout"})
        self.assertEqual(check.input_hashes["layout_gds"], hashlib.sha256(b"layout bytes").hexdigest())
        paths = {a.path.replace("\\", "/") for a in check.evidence}
        self.assertEqual(paths, {"stages/drc/report.xml", "stages/drc/drc-summary.json", "stages/drc/deck-manifest.json"})
        manifest = json.loads((self.run / "stages/drc/deck-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["transform"], manifest["expected_categories"]), ("none", 2))
        self.assertEqual(sha256_file(self.run / "stages/drc/deck.drc"), manifest["source_sha256"])

    def test_drc_markers_are_a_design_failure(self):
        stage = self.execute(self.drc(), {"report.xml": drc_report(markers=[("M1.space", TOP)])})
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "design"))
        self.assertEqual(stage.checks[0].status, CheckStatus.FAIL)
        self.assertIn("1 markers in 1 categories", stage.checks[0].message)

    def test_drc_incomplete_evidence_is_never_clean_or_a_detected_defect(self):
        cases = {
            "category did not run": ({"report.xml": drc_report(categories=CATEGORIES[:1])}, 0, "differ from the deck"),
            "no categories": ({"report.xml": drc_report(categories=())}, 0, "no, unnamed or duplicate"),
            "truncated": ({"report.xml": drc_report()[:-30]}, 0, "not readable XML"),
            "tool failed after writing a clean report": ({"report.xml": drc_report()}, 3, "exit 3"),
        }
        for index, (name, (files, code, reason)) in enumerate(cases.items()):
            with self.subTest(name):
                stage = self.execute(dataclasses.replace(self.drc(), id=f"drc{index}"), files, code)
                self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))
                self.assertEqual([c.status for c in stage.checks], [CheckStatus.NOT_RUN])
                self.assertIn(reason, stage.checks[0].message)

    def test_drc_empty_or_missing_report_fails_execution(self):
        for index, files in enumerate(({"report.xml": ""}, {})):
            stage = self.execute(dataclasses.replace(self.drc(), id=f"empty{index}"), files)
            self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))
            self.assertIn("missing or empty", stage.error)

    def test_flat_drc_runs_a_recorded_transformed_deck(self):
        stage = self.execute(self.drc(flat=True), {"report.xml": drc_report()})
        self.assertEqual((stage.id, stage.status, stage.checks[0].id), ("drc_flat", StageStatus.SUCCEEDED, "drc_flat"))
        staged = (self.run / "stages/drc_flat/deck.drc").read_text(encoding="utf-8")
        self.assertIn("\nflat\n", staged)
        manifest = json.loads((self.run / "stages/drc_flat/deck-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["transform"], "single-mode-line-flat-v1")
        self.assertNotEqual(manifest["staged_sha256"], manifest["source_sha256"])
        self.assertNotEqual(stage.checks[0].input_hashes["drc_deck_staged"], stage.checks[0].input_hashes["drc_deck_source"])

    def test_changed_layout_deck_or_tool_stops_before_the_tool_runs(self):
        def change_layout():
            (self.run / f"stages/assembly/{TOP}.gds").write_bytes(b"changed after assembly")

        def change_deck():
            (self.kit / "deck.drc").write_bytes(DECK.encode("utf-8") + b"\n# edited\n")

        def change_tool():
            self.tool.write_text(FAKE_TOOL + "\n# replaced\n", encoding="utf-8")

        for index, mutate in enumerate((change_layout, change_deck, change_tool)):
            with self.subTest(mutate.__name__):
                original = {p: p.read_bytes() for p in (self.run / f"stages/assembly/{TOP}.gds",
                                                         self.kit / "deck.drc", self.tool)}
                mutate()
                stage = self.execute(dataclasses.replace(self.drc(), id=f"drift{index}"), {"report.xml": drc_report()})
                for path, data in original.items():
                    path.write_bytes(data)
                self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))
                self.assertFalse((self.run / f"stages/drift{index}/report.xml").exists())

    def lvs(self, summary, files=None, exit_code=0):
        files = {"report.lvsdb": "#%lvsdb-klayout\n", "extracted.cir": "* extracted\n"} if files is None else files
        with mock.patch.object(lvs_module, "read_lvs_database", return_value=summary):
            return self.execute(lvs_stage(self.project, self.profile, self.toolchain, self.run), files, exit_code)

    def test_lvs_complete_match_passes_with_input_identity(self):
        stage = self.lvs(lvs_summary())
        self.assertEqual((stage.status, stage.checks[0].status), (StageStatus.SUCCEEDED, CheckStatus.PASS))
        self.assertEqual(set(stage.checks[0].input_hashes), {"layout_gds", "schematic_cdl", "lvs_deck", "klayout"})
        self.assertIn("stages/lvs/lvs-summary.json", {a.path for a in stage.checks[0].evidence})

    def test_lvs_mismatch_is_a_design_failure(self):
        stage = self.lvs(lvs_summary(nets={"Match": 3, "Mismatch": 1}, status="NoMatch"))
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "design"))
        self.assertEqual(stage.checks[0].status, CheckStatus.FAIL)
        self.assertIn("nets 1 Mismatch", stage.checks[0].message)

    def test_lvs_empty_comparison_is_infrastructure(self):
        stage = self.lvs({"circuits": []})
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))
        self.assertEqual(stage.checks[0].status, CheckStatus.NOT_RUN)
        self.assertIn("empty", stage.checks[0].message)

    def test_lvs_missing_extracted_netlist_fails_execution(self):
        stage = self.lvs(lvs_summary(), files={"report.lvsdb": "#%lvsdb-klayout\n"})
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))

    def presubmit(self, report, exit_code=0, files=None):
        drc = self.run / "stages/drc"
        if not drc.exists():
            drc.mkdir()
            (drc / "report.xml").write_text(drc_report(), encoding="utf-8")
            self.done.append(self.produced("drc", "report.xml"))
            self.record()
        report["meta"]["md5"] = hashlib.md5(b"layout bytes").hexdigest()
        identity = {"version": "0.1.0", "files": 3, "source_sha256": "a" * 64}
        with mock.patch.object(presubmit_module, "checker_identity", return_value=identity):
            spec = presubmit_stage(self.project, self.profile, self.toolchain, self.run)
            self.assertIn("OS0000_S8_08112026_v0.gds", spec.command)
            self.assertIn("OS0000_S8_08112026_v0_drc.txt", spec.command)
            return self.execute(spec, {"presubmit.json": json.dumps(report)} if files is None else files, exit_code)

    def test_presubmit_clean_run_reports_warn_and_manual_separately(self):
        stage = self.presubmit(presubmit_report())
        self.assertEqual(stage.status, StageStatus.SUCCEEDED)
        self.assertEqual({c.id: c.status for c in stage.checks},
                         {"presubmit": CheckStatus.PASS, "presubmit_warnings": CheckStatus.WARN,
                          "presubmit_manual": CheckStatus.MANUAL})
        self.assertEqual(set(stage.checks[0].input_hashes), {"layout_gds", "drc_report", "presubmit_checker"})
        staged = self.run / "stages/presubmit"
        self.assertEqual((staged / "OS0000_S8_08112026_v0.gds").read_bytes(), b"layout bytes")
        self.assertTrue((staged / "OS0000_S8_08112026_v0_drc.txt").is_file())

    def test_presubmit_failed_rule_exits_one_and_is_a_design_failure(self):
        stage = self.presubmit(presubmit_report({"C16.1": "FAIL"}, exit_code=1), exit_code=1)
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "design"))
        self.assertEqual(stage.checks[0].status, CheckStatus.FAIL)
        self.assertIn("C16.1", stage.checks[0].message)

    def test_presubmit_tool_error_or_inconsistent_exit_is_infrastructure(self):
        for name, report, code in (("tool error", presubmit_report(errors=["boom"], exit_code=2), 2),
                                   ("exit one without a FAIL row", presubmit_report(exit_code=1), 1),
                                   ("exit zero with a FAIL row", presubmit_report({"C16.1": "FAIL"}), 0)):
            with self.subTest(name):
                previous = self.run / "stages/presubmit"
                if previous.exists():
                    previous.rename(self.run / f"stages/presubmit-{code}-{len(name)}")
                stage = self.presubmit(report, code)
                self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))
                self.assertNotIn(CheckStatus.PASS, [c.status for c in stage.checks])
                self.assertNotIn(CheckStatus.FAIL, [c.status for c in stage.checks])


class DesignReturncodeTests(unittest.TestCase):
    """A declared nonzero tool verdict is a design result only with a FAIL check."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chipflow verdict ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_stage(self, code, status, codes=(1,)):
        from chipflow.runner.execution import Assessment
        from chipflow.runner.results import CheckResult

        def assess(stage_dir, command, artifacts):
            if not artifacts:
                return Assessment(failure_kind="infrastructure")
            return Assessment(checks=(CheckResult("rules", status, evidence=artifacts, input_hashes={"x": "0" * 64}),))
        script = f'from pathlib import Path; Path("out.json").write_text("{{}}"); raise SystemExit({code})'
        directory = self.root / f"case-{code}-{status.value}-{len(codes)}"
        directory.mkdir()
        spec = StageSpec("checker", (sys.executable, "-c", script), required_outputs=("out.json",),
                         assess=assess, design_returncodes=codes)
        return execute_stages(directory, [spec]).stages[0]

    def test_declared_verdict_exit_with_fail_check_is_design(self):
        stage = self.run_stage(1, CheckStatus.FAIL)
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "design"))

    def test_declared_verdict_exit_without_fail_check_is_infrastructure(self):
        stage = self.run_stage(1, CheckStatus.PASS)
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))
        self.assertIn("without a positively detected FAIL", stage.error)

    def test_other_nonzero_exits_stay_execution_failures(self):
        stage = self.run_stage(2, CheckStatus.FAIL)
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))
        stage = self.run_stage(1, CheckStatus.FAIL, codes=())
        self.assertEqual((stage.status, stage.failure_kind), (StageStatus.FAILED, "infrastructure"))

    def test_zero_or_unassessed_verdict_codes_are_rejected(self):
        with self.assertRaises(ValueError):
            execute_stages(self.root, [StageSpec("a", (sys.executable, "-c", "pass"), design_returncodes=(0,),
                                                 assess=lambda *_: None)])
        with self.assertRaises(ValueError):
            execute_stages(self.root, [StageSpec("a", (sys.executable, "-c", "pass"), design_returncodes=(1,))])


if __name__ == "__main__":
    unittest.main()
