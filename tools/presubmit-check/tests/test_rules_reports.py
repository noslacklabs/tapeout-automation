"""Checks 16 to 20 and command-line behaviour."""

from __future__ import annotations

import hashlib
import json
import os
import stat

import gdstk
import klayout.rdb as rdb
import pytest

from presubmit_check import checks as checks_module
from presubmit_check.cli import main
from presubmit_check.drcreport import ReportInvalid, parse_report

from .conftest import GOOD_NAME, make_pad, make_std, rect, run_cli

# --- 16: metal size


@pytest.mark.parametrize("layer", [8, 10])
def test_c16_exactly_at_limit_passes(design, layer):
    design.core.add(rect(100, 100, 125, 125, layer))
    assert design.check().status("C16.1") == "PASS"


@pytest.mark.parametrize("layer", [8, 10])
def test_c16_over_limit_fails_with_coordinates(design, layer):
    design.core.add(rect(100, 100, 125.005, 125.005, layer))
    outcome = design.check()
    assert outcome.status("C16.1") == "FAIL"
    assert "(600.000, 600.000) to (625.005, 625.005)" in outcome.evidence("C16.1")


def test_c16_says_where_the_wide_part_is(design):
    # A long thin bus with one wide stub: the outline is large, the wide part small.
    design.core.add(rect(100, 100, 500, 110, 8), rect(200, 100, 230, 130, 8))
    outcome = design.check()
    assert outcome.status("C16.1") == "FAIL"
    assert "centre in (712.500, 612.500) to (717.500, 617.500)" in outcome.evidence("C16.1")


def test_listing_cut_short_says_so(design):
    # 201 separate overlaps: the count must not stop at the listing limit.
    for i in range(201):
        design.core.add(rect(100 + 3 * i, 100, 102 + 3 * i, 102, 9), rect(100 + 3 * i, 100, 102 + 3 * i, 102, 4))
    outcome = design.check()
    assert outcome.status("C17.1") == "FAIL"
    assert "201 via overlaps" in outcome.results["C17.1"]["title"]
    assert "201 in total; only 200 are listed" in outcome.evidence("C17.1")


def test_c16_long_narrow_bus_passes(design):
    design.core.add(rect(100, 100, 500, 124, 8))
    assert design.check().status("C16.1") == "PASS"


def test_c16_abutting_shapes_are_merged(design):
    design.core.add(rect(100, 100, 115, 130, 8), rect(115, 100, 130, 130, 8))
    assert design.check().status("C16.1") == "FAIL"


def test_c16_l_shape_wide_only_at_corner(design):
    design.core.add(gdstk.Polygon([(100, 100), (500, 100), (500, 110), (110, 110), (110, 500), (100, 500)], layer=8))
    assert design.check().status("C16.1") == "PASS"


def test_c16_wide_metal_in_a_hierarchy_fails(design):
    leaf = gdstk.Cell("LEAF")
    leaf.add(rect(0, 0, 30, 30, 10))
    design.add_cell(leaf, origin=(800, 800))
    assert design.check().status("C16.1") == "FAIL"


def test_c16_pad_metal_is_warn_only(design):
    outcome = design.check()
    assert outcome.status("C16.1") == "PASS"
    assert outcome.status("C16.2") == "WARN"
    assert "metal 1:" in outcome.evidence("C16.2") and "metal 2:" in outcome.evidence("C16.2")
    assert "exempt" in outcome.results["C16.2"]["assumption"]


def test_c16_edited_pad_metal_fails_as_user(design):
    bad = make_pad("FAKEPAD")
    bad.add(rect(0, 200, 40, 240, 8))
    design.lib.remove(design.children["FAKEPAD"])
    design.lib.add(bad)
    for ref in design.pad_refs("FAKEPAD"):
        ref.cell = bad
    assert design.check().status("C16.1") == "FAIL"


def test_c16_scl_logo_metal_is_reported_separately(design):
    outcome = design.check()
    assert outcome.status("C16.1") == "PASS"
    assert outcome.status("C16.3") == "WARN"
    assert "(905.000, 705.000) to (945.000, 745.000)" in outcome.evidence("C16.3")


def test_c16_shape_added_to_the_frame_cell_counts_as_user(design):
    design.frame.add(rect(1500, 500, 1540, 540, 8))
    outcome = design.check()
    assert outcome.status("C16.1") == "FAIL"
    assert "(1500.000, 500.000)" in outcome.evidence("C16.1")


def test_c16_narrow_bus_reaching_pad_metal_is_not_a_failure(design):
    design.top.add(rect(220, 40, 500, 60, 8))
    outcome = design.check()
    assert outcome.status("C16.1") == "PASS"
    assert outcome.status("C16.4") == "PASS"
    assert outcome.code == 0


def test_c16_user_metal_widening_scl_metal_warns(design):
    # The frame's 20 um rail plus 20 um of the designer's metal makes a 40 um shape.
    design.top.add(rect(1500, 20, 1600, 40, 8))
    outcome = design.check()
    assert outcome.status("C16.1") == "PASS"
    assert outcome.status("C16.4") == "WARN"
    assert outcome.code == 0


# --- 17: via


def test_c17_clean(design):
    design.core.add(rect(100, 100, 102, 102, 9), rect(110, 100, 112, 102, 7), rect(120, 100, 122, 102, 4))
    assert design.check().status("C17.1") == "PASS"


def test_c17_via_over_contact_fails(design):
    design.core.add(rect(100, 100, 102, 102, 9), rect(101, 101, 103, 103, 7))
    outcome = design.check()
    assert outcome.status("C17.1") == "FAIL"
    assert "via over contact at (601.000, 601.000) to (602.000, 602.000)" in outcome.evidence("C17.1")


def test_c17_via_over_poly_fails(design):
    design.core.add(rect(100, 100, 102, 102, 9), rect(100, 100, 140, 102, 4))
    outcome = design.check()
    assert outcome.status("C17.1") == "FAIL"
    assert "via over poly" in outcome.evidence("C17.1")


def test_c17_touching_is_not_overlap(design):
    design.core.add(rect(100, 100, 102, 102, 9), rect(102, 100, 104, 102, 7))
    assert design.check().status("C17.1") == "PASS"


def test_c17_across_hierarchy_fails(design):
    a = gdstk.Cell("AAA")
    a.add(rect(0, 0, 2, 2, 9))
    b = gdstk.Cell("BBB")
    b.add(rect(0, 0, 2, 2, 4))
    design.add_cell(a, origin=(800, 800))
    design.add_cell(b, origin=(801, 800))
    assert design.check().status("C17.1") == "FAIL"


def test_c17_inside_scl_cell_warns(design):
    design.add_cell(make_std("FAKESTD3"), origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C17.1") == "PASS"
    assert outcome.status("C17.2") == "WARN"
    assert "FAKESTD3: 1 overlap of via over poly" in outcome.evidence("C17.2")


def test_c17_scl_via_over_user_poly_fails(design):
    design.add_cell(make_std("FAKESTD3"), origin=(800, 800))
    design.top.add(rect(800, 800, 802, 900, 9))  # own via over the cell's poly at y=820
    assert design.check().status("C17.1") == "FAIL"


def test_c17_placed_std_cells_overlapping_each_other_fails(design):
    """Different SCL cells overlapped by placement: the designer's error, not SCL's."""
    plain = make_std("FAKESTD1")
    via_cell = make_std("FAKESTD3")
    design.add_cell(plain, origin=(800, 800))
    design.add_cell(via_cell, origin=(800, 781.5))  # FAKESTD3's via lands on FAKESTD1's contact
    outcome = design.check()
    assert outcome.status("C17.1") == "FAIL"
    assert outcome.status("C17.2") == "WARN"


# --- 18 and 20: always there


def test_c18_manual_with_sources(design):
    outcome = design.check()
    for id_, word in (("C18.1", "necking"), ("C18.2", "bus"), ("C18.3", "ERC"), ("C18.4", "electrical")):
        assert outcome.status(id_) == "MANUAL"
        assert word in outcome.results[id_]["title"]
        assert outcome.results[id_]["source"]


def test_c20_notice_is_always_in_the_output(design):
    outcome = design.check()
    assert outcome.code == 0
    assert "does not replace DRC or LVS" in outcome.text
    assert "ready to submit" in outcome.text
    assert outcome.status("C20.1") == "MANUAL"
    assert "ready to submit" in outcome.payload["notice"]
    assert "exit code 0" in outcome.text.lower()
    assert "no FAILs from these checks" in outcome.text
    assert "run DRC and LVS" in outcome.text


def test_c20_notice_on_tool_error(tmp_path, capsys, fake_pdk):
    code = main([str(tmp_path / "absent.gds"), "--die", "2", "--silicon", "OS0001", "--pdk-root", str(fake_pdk)])
    assert code == 2
    assert "does not replace DRC or LVS" in capsys.readouterr().err


# --- 19: DRC report


def make_report(path, categories: dict, top="OS0001", original=GOOD_NAME):
    database = rdb.ReportDatabase("test")
    database.generator = "synthetic test"
    database.top_cell_name = top
    database.original_file = original
    cell = database.create_cell(top)
    for name, count in categories.items():
        category = database.create_category(name)
        for i in range(count):
            item = database.create_item(cell.rdb_id(), category.rdb_id())
            item.add_value(f"marker {i} at (1,2)")
    database.save(str(path))
    return path


DRC_NAME = "OS0001_MyChip_25072026_v0_drc.txt"


def test_c19_manual_without_report(design):
    outcome = design.check()
    assert outcome.status("C19.1") == "MANUAL"
    assert "nothing is known about DRC" in outcome.evidence("C19.1")


def test_c19_clean_real_klayout_report(design, tmp_path):
    report = make_report(tmp_path / DRC_NAME, {"1.1.1 : fake rule = 1um": 0, "2.2.2 : other": 0})
    outcome = design.check("--drc-report", str(report))
    assert outcome.status("C19.1") == "PASS"
    assert outcome.status("C19.2") == "PASS"
    assert outcome.status("C19.3") == "PASS"
    assert outcome.status("C19.4") == "PASS"
    assert "This doesn't show the report is current" in outcome.evidence("C19.2")


def test_c19_rule_markers_fail_with_counts(design, tmp_path):
    report = make_report(tmp_path / DRC_NAME, {"1.1.1 : fake rule = 1um": 3, "2.2.2 : other": 0, "3.3 & x": 1})
    outcome = design.check("--drc-report", str(report))
    assert outcome.status("C19.2") == "FAIL"
    assert outcome.code == 1
    text = outcome.evidence("C19.2")
    assert "3 x 1.1.1 : fake rule = 1um" in text
    assert "1 x 3.3 & x" in text
    assert "2.2.2" not in text


@pytest.mark.parametrize("name", ["@GUIDELINE-3 Bad contact", " @GUIDELINE-3 leading space", "@GUIDELINE_4_floating_gates  "])
def test_c19_guideline_markers_warn_only(design, tmp_path, name):
    report = make_report(tmp_path / DRC_NAME, {name: 2, "1.1.1 : rule": 0})
    outcome = design.check("--drc-report", str(report))
    assert outcome.status("C19.2") == "PASS"
    assert outcome.status("C19.3") == "WARN"
    assert "2 x @GUIDELINE" in outcome.evidence("C19.3")
    assert outcome.code == 0


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"Everything is clean! 0 errors",
        b"<notes><categories/><items/></notes>",
        b"<report-database><description>no sections</description></report-database>",
        b"<report-database><categories/><items>",
        b'<!DOCTYPE report-database [<!ENTITY x "y">]><report-database><categories/><items/></report-database>',
        b"<report-database><categories/><items><item><cell>A</cell></item></items></report-database>",
    ],
)
def test_c19_invalid_report_is_not_clean(design, tmp_path, content):
    report = tmp_path / DRC_NAME
    report.write_bytes(content)
    outcome = design.check("--drc-report", str(report))
    assert outcome.status("C19.1") == "FAIL"
    assert "not treated as clean" in outcome.evidence("C19.1")
    assert "C19.2" not in outcome.results


def test_c19_missing_file_is_not_clean(design, tmp_path):
    outcome = design.check("--drc-report", str(tmp_path / DRC_NAME))
    assert outcome.status("C19.1") == "FAIL"


def test_c19_no_categories_warns(design, tmp_path):
    report = tmp_path / DRC_NAME
    report.write_text("<report-database><categories/><items/></report-database>")
    outcome = design.check("--drc-report", str(report))
    assert outcome.status("C19.1") == "WARN"


def test_c19_report_for_another_layout_warns(design, tmp_path):
    report = make_report(tmp_path / DRC_NAME, {"1.1.1 : rule": 0}, top="OS0999", original="/x/other.gds")
    outcome = design.check("--drc-report", str(report))
    assert outcome.status("C19.4") == "WARN"
    assert "OS0999" in outcome.evidence("C19.4") and "other.gds" in outcome.evidence("C19.4")


def test_c19_multiplicity_counts(tmp_path):
    report = tmp_path / "r.txt"
    report.write_text(
        "<report-database><categories><category><name>a</name></category></categories><items>"
        "<item><category>a</category><multiplicity>5</multiplicity></item></items></report-database>"
    )
    assert parse_report(report).total_markers == 5


def test_c19_nested_category_paths(tmp_path):
    database = rdb.ReportDatabase("t")
    parent = database.create_category("group one")
    child = database.create_category(parent, "rule.with.dots")
    cell = database.create_cell("T")
    database.create_item(cell.rdb_id(), child.rdb_id()).add_value("x")
    path = tmp_path / "r.txt"
    database.save(str(path))
    parsed = parse_report(path)
    assert parsed.total_markers == 1
    assert list(parsed.categories) == ["group one.rule.with.dots"]


def test_parse_report_raises_for_unreadable(tmp_path):
    with pytest.raises(ReportInvalid):
        parse_report(tmp_path / "absent.txt")


# --- command-line contract


def test_exit_codes_0_1(design):
    good = design.check()
    assert good.code == 0
    design.core.add(rect(100, 100, 150, 150, 8))
    assert design.check().code == 1


def test_exit_2_missing_input(tmp_path, fake_pdk, capsys):
    assert main([str(tmp_path / "x.gds"), "--die", "2", "--silicon", "OS0001", "--pdk-root", str(fake_pdk)]) == 2
    assert "not found" in capsys.readouterr().err


def test_exit_2_missing_pdk(design, tmp_path, capsys):
    path = design.write()
    code = main([str(path), "--die", "2", "--silicon", "OS0001", "--pdk-root", str(tmp_path / "nokit")])
    assert code == 2
    assert "not a folder" in capsys.readouterr().err


def test_exit_2_wrong_pdk_folder(design, tmp_path, capsys):
    path = design.write()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main([str(path), "--die", "2", "--silicon", "OS0001", "--pdk-root", str(empty)]) == 2
    assert "PDK file not found" in capsys.readouterr().err


def test_exit_2_mu_character_hint(design, tmp_path, capsys, fake_pdk):
    path = design.write()
    other = str(fake_pdk).replace("µ", "μ")
    assert main([str(path), "--die", "2", "--silicon", "OS0001", "--pdk-root", other]) == 2
    assert "mu character" in capsys.readouterr().err


def test_exit_2_bad_die(design, capsys):
    path = design.write()
    assert main([str(path), "--die", "7", "--silicon", "OS0001", "--pdk-root", str(design.pdk)]) == 2


def test_exit_2_missing_argument(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["only.gds"])
    assert caught.value.code == 2


def test_exit_2_when_a_check_crashes(design, monkeypatch):
    def broken(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(checks_module, "check_13_layers", broken)
    outcome = design.check()
    assert outcome.code == 2
    assert "TOOL ERROR" in outcome.text and "boom" in outcome.text
    assert "incomplete" in outcome.text
    assert outcome.payload["tool_errors"]


def test_exit_2_beats_fail_when_a_check_crashes(design, monkeypatch):
    monkeypatch.setattr(checks_module, "check_13_layers", lambda ctx: 1 / 0)
    design.core.add(rect(100, 100, 150, 150, 8))
    assert design.check().code == 2


def test_input_is_never_modified(design):
    path = design.write()
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    outcome = design.check(path=path)
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before
    assert outcome.payload["meta"]["input_unchanged"] is True


def test_works_on_a_read_only_input(design):
    path = design.write()
    os.chmod(path, stat.S_IREAD)
    try:
        assert design.check(path=path).status("C01.1") == "PASS"
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_json_refuses_to_overwrite_the_input(design, capsys):
    path = design.write()
    before = path.read_bytes()
    code = main([str(path), "--die", "2", "--silicon", "OS0001", "--pdk-root", str(design.pdk), "--json", str(path)])
    assert code == 2
    assert "Refusing" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_json_refuses_to_write_inside_the_pdk(design, capsys):
    path = design.write()
    code = main([str(path), "--die", "2", "--silicon", "OS0001", "--pdk-root", str(design.pdk), "--json", str(design.pdk / "out.json")])
    assert code == 2
    assert not (design.pdk / "out.json").exists()


def test_json_shape_and_ids_are_sorted(design):
    outcome = design.check()
    payload = outcome.payload
    assert payload["meta"]["exit_code"] == outcome.code
    assert payload["meta"]["frame_set"] == "padframe"
    ids = [r["id"] for r in payload["results"]]
    assert ids == sorted(ids, key=lambda i: tuple(int(p) for p in i[1:].split(".")))
    for result in payload["results"]:
        assert result["status"] in ("PASS", "FAIL", "WARN", "MANUAL")
        assert result["id"] and result["title"]
        assert result["source"], result["id"]
    json.dumps(payload)


def test_every_numbered_check_reports(design):
    outcome = design.check()
    seen = {int(i[1:].split(".")[0]) for i in outcome.results}
    assert seen == set(range(1, 21))


def test_text_report_caps_evidence_and_verbose_does_not(design, capsys):
    path = design.write()
    short = run_cli(path, design.pdk, capsys, 2, "OS0001")
    long = run_cli(path, design.pdk, capsys, 2, "OS0001", "--verbose")
    assert "more (use --verbose or --json)" in short.text
    assert "more (use --verbose or --json)" not in long.text
