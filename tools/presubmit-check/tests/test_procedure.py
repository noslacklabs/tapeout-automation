"""Checks 1 to 7: procedure rules."""

from __future__ import annotations

import hashlib
import shutil
import subprocess

import gdstk
import pytest

from .conftest import CONTEXT, GOOD_NAME, make_pad, make_std, rect

# --- 1: valid stream


def test_c01_valid(design):
    outcome = design.check()
    assert outcome.status("C01.1") == "PASS"
    assert outcome.status("C01.2") == "PASS"


def test_c01_garbage_is_fail_not_error_and_nothing_else_passes(design, tmp_path):
    bad = tmp_path / GOOD_NAME
    bad.write_bytes(b"not a gds stream, just text")
    outcome = design.check(path=bad)
    assert outcome.code == 1
    assert outcome.status("C01.1") == "FAIL"
    for id_ in ("C02.2", "C03.1", "C04.1", "C08.1", "C09.1", "C10.1", "C11.1", "C13.1", "C14.1", "C16.1", "C17.1"):
        assert outcome.status(id_) == "MANUAL", id_
        assert "not evaluated" in outcome.evidence(id_)
    # Checks that don't need the geometry still ran.
    assert outcome.status("C05.1") == "PASS"
    assert outcome.status("C07.1") == "PASS"


def test_c01_truncated_stream(design, tmp_path):
    good = design.write()
    data = good.read_bytes()
    good.write_bytes(data[: len(data) // 2])
    outcome = design.check(path=good)
    assert outcome.status("C01.1") == "FAIL"
    assert "byte" in outcome.evidence("C01.1")


# --- 2: top cell


def test_c02_pass(design):
    outcome = design.check()
    for id_ in ("C02.1", "C02.2", "C02.3", "C02.4"):
        assert outcome.status(id_) == "PASS", id_


def test_c02_wrong_name(design):
    design.top.name = "OS0002"
    outcome = design.check()
    assert outcome.status("C02.3") == "FAIL"
    assert "OS0002" in outcome.evidence("C02.3")


def test_c02_case_sensitive(design):
    design.top.name = "os0001"
    outcome = design.check()
    assert outcome.status("C02.3") == "FAIL"
    assert "wrong case" in outcome.evidence("C02.3")


def test_c02_two_top_cells(design):
    design.lib.add(gdstk.Cell("STRAY"))
    outcome = design.check()
    assert outcome.status("C02.2") == "FAIL"
    assert "STRAY" in outcome.evidence("C02.2")
    assert outcome.status("C02.3") == "FAIL"


def test_c02_context_cell_is_fail(design):
    context = gdstk.Cell(CONTEXT)
    context.add(gdstk.Reference(design.core))
    design.lib.add(context)
    outcome = design.check()
    assert outcome.status("C02.4") == "FAIL"
    assert outcome.status("C02.2") == "PASS"  # reported separately from the real top


@pytest.mark.parametrize("silicon", ["OS001", "OS00001", "os0001", "AB0001", "OS000A"])
def test_c02_bad_silicon_format(design, silicon):
    assert design.check(silicon=silicon).status("C02.1") == "FAIL"


# --- 3: extent


def test_c03_pass(design):
    outcome = design.check()
    assert outcome.status("C03.1") == "PASS"
    assert outcome.status("C03.2") == "PASS"
    assert "(2000.000, 2000.000)" in outcome.evidence("C03.1")


def test_c03_origin_offset(design):
    design.top.add(rect(-0.005, 100, 10, 110, 8))
    outcome = design.check()
    assert outcome.status("C03.1") == "FAIL"
    assert "bottom-left is (-0.005, 0.000)" in outcome.evidence("C03.1")


def test_c03_top_right_past_die(design):
    design.top.add(rect(1990, 1990, 2000.005, 2000.005, 8))
    outcome = design.check()
    assert outcome.status("C03.1") == "FAIL"
    assert "top-right is (2000.005, 2000.005)" in outcome.evidence("C03.1")


def test_c03_wrong_die_argument(design_1mm):
    outcome = design_1mm.check(die=2)
    assert outcome.status("C03.1") == "FAIL"


def test_c03_one_mm_pass(design_1mm):
    assert design_1mm.check().status("C03.1") == "PASS"


def test_c03_label_outside_die(design):
    design.top.add(gdstk.Label("X", (2001, 10), layer=8, texttype=7))
    outcome = design.check()
    assert outcome.status("C03.1") == "PASS"
    assert outcome.status("C03.2") == "FAIL"


# --- 4: grid


def test_c04_clean(design):
    outcome = design.check()
    assert outcome.status("C04.1") == "PASS"
    assert outcome.status("C04.5") == "PASS"
    # The fake frame's own off-grid label is SCL's.
    assert outcome.status("C04.4") == "WARN"


def test_c04_user_polygon_fail(design):
    design.core.add(gdstk.Polygon([(100, 100), (110.001, 100), (110.001, 110), (100, 110)], layer=8))
    outcome = design.check()
    assert outcome.status("C04.1") == "FAIL"
    assert "2 vertices" in outcome.evidence("C04.1")
    assert "(110.001, 100.000)" in outcome.evidence("C04.1")


def test_c04_user_label_fail(design):
    design.core.add(gdstk.Label("L", (1.001, 1), layer=8, texttype=7))
    outcome = design.check()
    assert outcome.status("C04.1") == "FAIL"
    assert "1 label" in outcome.evidence("C04.1")


def test_c04_user_instance_origin_fail(design):
    leaf = gdstk.Cell("LEAF")
    leaf.add(rect(0, 0, 1, 1, 8))
    design.add_cell(leaf, origin=(800.002, 800))
    outcome = design.check()
    assert outcome.status("C04.1") == "FAIL"
    assert "instance origin" in outcome.evidence("C04.1")


def test_c04_path_half_width_off_grid(design):
    design.core.add(gdstk.FlexPath([(100, 100), (120, 100)], 2.005, layer=8, simple_path=True))
    outcome = design.check()
    assert outcome.status("C04.1") == "FAIL"
    assert "path half-width" in outcome.evidence("C04.1")


def test_c04_library_pad_off_grid_is_warn(design):
    pad = _library_cell("FAKEOFFG")
    design.add_cell(pad, origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C04.1") == "PASS"
    assert outcome.status("C04.2") == "WARN"
    assert "FAKEOFFG: 2 vertices" in outcome.evidence("C04.2")
    assert "helpdesk" in outcome.evidence("C04.2")


def test_c04_library_std_off_grid_is_warn(design):
    design.add_cell(_library_cell("FAKESTD2"), origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C04.1") == "PASS"
    assert outcome.status("C04.3") == "WARN"


def test_c04_edited_library_cell_counts_as_user(design):
    pad = _library_cell("FAKEOFFG")
    pad.add(rect(200, 200, 201.001, 201, 8))
    design.add_cell(pad, origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C04.1") == "FAIL"
    assert "FAKEOFFG" in outcome.evidence("C04.1")
    assert outcome.status("C11.1") == "FAIL"


def test_c04_magnified_instance_warns(design):
    leaf = gdstk.Cell("LEAF")
    leaf.add(rect(0, 0, 1, 1, 8))
    design.add_cell(leaf, origin=(800, 800), magnification=1.5)
    assert design.check().status("C04.5") == "WARN"


def test_c04_five_nanometre_database_unit_is_on_grid(design):
    design.lib.precision = 5e-9
    outcome = design.check()
    assert outcome.status("C01.2") == "WARN"
    assert outcome.status("C04.1") == "PASS"


# --- 5: file name


def test_c05_good(design):
    outcome = design.check()
    for id_ in ("C05.1", "C05.2", "C05.3", "C05.4"):
        assert outcome.status(id_) == "PASS", id_


@pytest.mark.parametrize(
    "name",
    ["OS0001_MyChip_25072026.gds", "OS0001_25072026_v0.gds", "chip.gds", "OS0001__25072026_v0.gds"],
)
def test_c05_bad_pattern(design, name):
    assert design.check(name=name).status("C05.1") == "FAIL"


def test_c05_wrong_extension(design):
    assert design.check(name="OS0001_MyChip_25072026_v0.GDS").status("C05.1") == "FAIL"


@pytest.mark.parametrize("date", ["31022026", "29022025", "00012026", "25132026", "2507202", "ddmmyyyy", "25072026x"])
def test_c05_bad_date(design, date):
    assert design.check(name=f"OS0001_MyChip_{date}_v0.gds").status("C05.2") == "FAIL"


def test_c05_leap_day_is_real(design):
    assert design.check(name="OS0001_MyChip_29022024_v0.gds").status("C05.2") == "PASS"


def test_c05_disagrees_with_silicon(design):
    assert design.check(name="OS0002_MyChip_25072026_v0.gds").status("C05.3") == "FAIL"


def test_c05_lower_case_prefix_disagrees(design):
    assert design.check(name="os0001_MyChip_25072026_v0.gds").status("C05.3") == "FAIL"


@pytest.mark.parametrize("version", ["version1", "1", "V0", "v0a"])
def test_c05_unusual_version_warns_only(design, version):
    outcome = design.check(name=f"OS0001_MyChip_25072026_{version}.gds")
    assert outcome.status("C05.4") == "WARN"
    assert outcome.status("C05.1") == "PASS"


def test_c05_underscore_design_name_is_an_assumption(design):
    outcome = design.check(name="OS0001_My_Chip_25072026_v0.gds")
    assert outcome.status("C05.1") == "PASS"
    assert "underscores" in outcome.results["C05.1"]["assumption"]


# --- 6: DRC report name


def test_c06_manual_without_report(design):
    assert design.check().status("C06.1") == "MANUAL"


def test_c06_good_name(design, tmp_path):
    report = tmp_path / "OS0001_MyChip_25072026_v0_drc.txt"
    report.write_text("<report-database><categories/><items/></report-database>")
    assert design.check("--drc-report", str(report)).status("C06.1") == "PASS"


def test_c06_bad_name(design, tmp_path):
    report = tmp_path / "scl_drc.txt"
    report.write_text("<report-database><categories/><items/></report-database>")
    outcome = design.check("--drc-report", str(report))
    assert outcome.status("C06.1") == "FAIL"
    assert "OS0001_MyChip_25072026_v0_drc.txt" in outcome.evidence("C06.1")


# --- 7: MD5


def test_c07_matches_real_md5sum(design):
    if not shutil.which("md5sum"):
        pytest.skip("md5sum not available")
    path = design.write()
    expected = subprocess.run(["md5sum", path.name], cwd=path.parent, capture_output=True, text=True, check=True).stdout.strip()
    outcome = design.check(path=path)
    assert outcome.status("C07.1") == "PASS"
    assert outcome.results["C07.1"]["evidence"][0] == expected
    assert outcome.status("C07.2") == "MANUAL"


def test_c07_report_from_md5sum_passes(design, tmp_path):
    if not shutil.which("md5sum"):
        pytest.skip("md5sum not available")
    path = design.write()
    report = tmp_path / "md5.txt"
    report.write_text(subprocess.run(["md5sum", path.name], cwd=path.parent, capture_output=True, text=True, check=True).stdout)
    assert design.check("--md5-report", str(report), path=path).status("C07.2") == "PASS"


def _digest(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def test_c07_bare_hash_and_binary_marker_pass(design, tmp_path):
    path = design.write()
    report = tmp_path / "md5.txt"
    report.write_text(_digest(path) + "\n")
    assert design.check("--md5-report", str(report), path=path).status("C07.2") == "PASS"
    report.write_text(f"{_digest(path)} *{path.name}\r\n")
    assert design.check("--md5-report", str(report), path=path).status("C07.2") == "PASS"


def test_c07_mismatch_fails(design, tmp_path):
    report = tmp_path / "md5.txt"
    report.write_text("0123456789abcdef0123456789abcdef  " + GOOD_NAME + "\n")
    outcome = design.check("--md5-report", str(report))
    assert outcome.status("C07.2") == "FAIL"
    assert outcome.code == 1


def test_c07_right_hash_wrong_name_warns(design, tmp_path):
    path = design.write()
    report = tmp_path / "md5.txt"
    report.write_text(f"{_digest(path)}  other_name.gds\n")
    assert design.check("--md5-report", str(report), path=path).status("C07.2") == "WARN"


def test_c07_upper_case_warns(design, tmp_path):
    path = design.write()
    report = tmp_path / "md5.txt"
    report.write_text(f"{_digest(path).upper()}  {path.name}\n")
    assert design.check("--md5-report", str(report), path=path).status("C07.2") == "WARN"


def test_c07_junk_or_missing_report_fails(design, tmp_path):
    report = tmp_path / "md5.txt"
    report.write_text("no checksum here\n")
    assert design.check("--md5-report", str(report)).status("C07.2") == "FAIL"
    assert design.check("--md5-report", str(tmp_path / "absent.txt")).status("C07.2") == "FAIL"


def test_full_good_design_has_no_fail_and_exits_zero(design):
    outcome = design.check()
    assert outcome.code == 0, outcome.text
    assert outcome.statuses("FAIL") == []


def _library_cell(name):
    return make_std(name) if name.startswith("FAKESTD") else make_pad(name)
