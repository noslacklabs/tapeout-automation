"""Checks 8 to 15: pad frame, library integrity, layers, silicon number block."""

from __future__ import annotations

import math

import gdstk
import pytest

from .conftest import LOGO, VDD, VSS, make_pad, make_std, new_lib, rect

QUARTER = math.pi / 2

# --- 8: counts


def test_c08_pass_2mm(design):
    outcome = design.check()
    for id_ in ("C08.1", "C08.2", "C08.3"):
        assert outcome.status(id_) == "PASS", id_
    assert "16 listed pad instances" in outcome.evidence("C08.1")


def test_c08_pass_1mm(design_1mm):
    outcome = design_1mm.check()
    assert outcome.status("C08.1") == "PASS"
    assert "6 listed pad instances" in outcome.evidence("C08.1")


def test_c08_missing_signal_pad(design):
    design.frame.remove(design.pad_refs("FAKEPAD")[0])
    outcome = design.check()
    assert outcome.status("C08.1") == "FAIL"
    assert outcome.status("C08.2") == "PASS"


def test_c08_missing_vdd(design):
    design.frame.remove(design.pad_refs(VDD)[0])
    outcome = design.check()
    assert outcome.status("C08.1") == "FAIL"
    assert outcome.status("C08.2") == "FAIL"
    assert outcome.status("C08.3") == "PASS"


def test_c08_vss_replaced_by_signal_pad(design):
    ref = design.pad_refs(VSS)[0]
    ref.cell = design.children["FAKEPAD"]
    outcome = design.check()
    assert outcome.status("C08.1") == "PASS"
    assert outcome.status("C08.3") == "FAIL"


# --- 9: power pads


def test_c09_pass_and_reports_the_set(design):
    outcome = design.check()
    assert outcome.status("C09.1") == "PASS"
    assert outcome.status("C09.2") == "PASS"
    assert outcome.status("C09.3") == "WARN"
    text = outcome.evidence("C09.3")
    assert "used set 'padframe'" in text
    assert "other set 'analog'" in text
    assert "matches set: padframe" in text
    assert "frame2mmx2mm.gds" in text


def test_c09_moved_power_pad_fails_with_coordinates(design):
    ref = design.pad_refs(VDD)[0]
    ref.origin = (ref.origin[0], ref.origin[1] + 5)
    outcome = design.check()
    assert outcome.status("C09.1") == "FAIL"
    assert "unexpected: PVDD01 at (110.000, 305.000) R90" in outcome.evidence("C09.1")
    assert "missing: PVDD01 expected at (110.000, 300.000) R90" in outcome.evidence("C09.1")
    assert outcome.status("C09.2") == "PASS"


def test_c09_rotated_power_pad_fails(design):
    ref = design.pad_refs(VSS)[0]
    ref.rotation = 0.0
    assert design.check().status("C09.2") == "FAIL"


def test_c09_mirrored_power_pad_fails(design):
    ref = design.pad_refs(VDD)[0]
    ref.x_reflection = True
    assert design.check().status("C09.1") == "FAIL"


def test_c09_composes_through_hierarchy(design):
    """The frame placed via an extra level at an offset no longer matches."""
    design.top.references[0].origin = (10, 0)
    assert design.check().status("C09.1") == "FAIL"


def test_c09_other_frame_set_is_used_when_chosen(design):
    outcome = design.check("--frame-set", "analog")
    assert outcome.code == 0, outcome.text
    assert "used set 'analog'" in outcome.evidence("C09.3")
    assert "matches set: padframe" in outcome.evidence("C09.3")


def test_c09_unknown_frame_set_is_tool_error(design):
    assert design.check("--frame-set", "nonsense").code == 2


# --- 10: signal pads


def test_c10_pass(design):
    outcome = design.check()
    for id_ in ("C10.1", "C10.2", "C10.3", "C10.4"):
        assert outcome.status(id_) == "PASS", id_


def test_c10_swap_to_listed_pad_is_reported(design):
    pad = make_pad("FAKEOFFG")
    design.lib.add(pad)
    design.pad_refs("FAKEPAD")[0].cell = pad
    outcome = design.check()
    assert outcome.status("C10.1") == "PASS"
    assert outcome.status("C10.2") == "WARN"
    assert "type changed: FAKEPAD replaced by FAKEOFFG" in outcome.evidence("C10.2")


def test_c10_moved_signal_pad_is_reported(design):
    ref = design.pad_refs("FAKEPAD")[0]
    ref.origin = (ref.origin[0] + 10, ref.origin[1])
    outcome = design.check()
    assert outcome.status("C10.2") == "WARN"
    text = outcome.evidence("C10.2")
    assert "moved or added: FAKEPAD at" in text and "missing: reference FAKEPAD at" in text


def test_c10_pad_not_on_the_list_fails(design):
    design.add_cell(make_pad("NOTLISTED"), origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C10.1") == "FAIL"
    assert "'NOTLISTED'" in outcome.evidence("C10.1")


def test_c10_unlisted_cell_at_pad_position_fails_even_without_window(design):
    other = gdstk.Cell("MYPADISH")
    other.add(rect(0, 0, 5, 5, 8))
    design.lib.add(other)
    design.pad_refs("FAKEPAD")[0].cell = other
    outcome = design.check()
    assert outcome.status("C10.1") == "FAIL"
    assert "reference pad position" in outcome.evidence("C10.1")


@pytest.mark.parametrize("window", [89.995, 90.005, 50.0])
def test_c10_wrong_window_fails(design, window):
    bad = make_pad("FAKEPAD2", window=window)
    for name in ("FAKEPAD2",):
        original = design.children[name]
        design.lib.remove(original)
        design.lib.add(bad)
        for ref in design.pad_refs(name):
            ref.cell = bad
    outcome = design.check()
    assert outcome.status("C10.3") == "FAIL"
    assert "FAKEPAD2" in outcome.evidence("C10.3")
    assert outcome.status("C11.1") == "FAIL"


def test_c10_missing_window_fails(design):
    bad = gdstk.Cell("FAKEPAD2")
    bad.add(rect(0, 0, 110, 110, 8))
    design.lib.remove(design.children["FAKEPAD2"])
    design.lib.add(bad)
    for ref in design.pad_refs("FAKEPAD2"):
        ref.cell = bad
    outcome = design.check()
    assert outcome.status("C10.3") == "FAIL"
    assert "found none" in outcome.evidence("C10.3")


def test_c10_stray_pad_layer_shape_warns(design):
    design.core.add(rect(100, 100, 120, 120, 11))
    outcome = design.check()
    assert outcome.status("C10.4") == "WARN"
    assert "(600.000, 600.000) to (620.000, 620.000)" in outcome.evidence("C10.4")


# --- 11: library integrity


def test_c11_pass_with_placed_library_cells(design):
    design.add_cell(make_std("FAKESTD1"), origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C11.1") == "PASS"
    assert "5 library-named cells" in outcome.evidence("C11.1")


def test_c11_edited_std_cell_fails_with_evidence(design):
    cell = make_std("FAKESTD1")
    cell.add(rect(0, 50, 5, 55, 8))
    design.add_cell(cell, origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C11.1") == "FAIL"
    text = outcome.evidence("C11.1")
    assert "FAKESTD1: layer 8/0" in text
    assert "25.000 um^2" in text
    assert "(0.000, 50.000) to (5.000, 55.000)" in text


def test_c11_custom_cell_using_a_library_name_fails(design):
    cell = gdstk.Cell("FAKESTD1")
    cell.add(rect(0, 0, 1, 1, 8))
    design.add_cell(cell, origin=(800, 800))
    assert design.check().status("C11.1") == "FAIL"


def test_c11_unplaced_library_named_cell_is_still_checked(design):
    cell = gdstk.Cell("FAKESTD1")
    cell.add(rect(0, 0, 1, 1, 8))
    design.lib.add(cell)
    assert design.check().status("C11.1") == "FAIL"


def test_c11_hash_ignores_shape_order_and_vertex_start(design):
    original = make_std("FAKESTD1")
    shuffled = gdstk.Cell("FAKESTD1")
    for polygon in reversed(original.polygons):
        points = polygon.points.tolist()
        shuffled.add(gdstk.Polygon(points[2:] + points[:2], layer=polygon.layer, datatype=polygon.datatype))
    design.add_cell(shuffled, origin=(800, 800))
    assert design.check().status("C11.1") == "PASS"


def test_c11_hash_ignores_how_an_area_is_split(design):
    cell = gdstk.Cell("FAKESTD1")
    cell.add(rect(0, 0, 10, 20, 8), rect(0, 20, 10, 40, 8), rect(1, 1, 3, 3, 7), rect(0, 20, 10, 22, 4))
    design.add_cell(cell, origin=(800, 800))
    assert design.check().status("C11.1") == "PASS"


def test_c11_changed_label_warns_not_fails(design):
    cell = make_pad("FAKEPAD")
    cell.add(gdstk.Label("EXTRA", (2, 2), layer=8, texttype=7))
    design.lib.remove(design.children["FAKEPAD"])
    design.lib.add(cell)
    for ref in design.pad_refs("FAKEPAD"):
        ref.cell = cell
    outcome = design.check()
    assert outcome.status("C11.1") == "PASS"
    assert outcome.status("C11.2") == "WARN"


def test_c11_list_size_mismatch_warns(design):
    # The fake kit's lists are deliberately smaller than the documented 68 and 20.
    assert design.check().status("C11.3") == "WARN"


# --- 12: renamed copies


def test_c12_pass(design):
    assert design.check().status("C12.1") == "PASS"


def test_c12_renamed_copy_warns(design):
    cell = make_std("FAKESTD1")
    cell.name = "MYSTD"
    design.add_cell(cell, origin=(800, 800))
    outcome = design.check()
    assert outcome.status("C12.1") == "WARN"
    assert "MYSTD" in outcome.evidence("C12.1") and "FAKESTD1" in outcome.evidence("C12.1")


def test_c12_shifted_renamed_pad_warns(design):
    cell = make_pad("OTHERNAME")
    moved = gdstk.Cell("MOVEDPAD")
    for polygon in cell.polygons:
        moved.add(polygon.copy().translate(3, 4))
    design.lib.add(moved)
    design.top.add(gdstk.Reference(moved, (800, 800)))
    outcome = design.check()
    assert outcome.status("C12.1") == "WARN"
    assert "FAKEPAD" in outcome.evidence("C12.1")


def test_c12_similar_but_different_cell_passes(design):
    cell = gdstk.Cell("MYSTD")
    cell.add(rect(0, 0, 10, 40, 8), rect(1, 1, 3, 3, 7), rect(0, 21, 10, 23, 4))
    design.add_cell(cell, origin=(800, 800))
    assert design.check().status("C12.1") == "PASS"


# --- 13: layers


def test_c13_pass(design):
    outcome = design.check()
    assert outcome.status("C13.1") == "PASS"
    assert "8/0" in outcome.evidence("C13.1")


@pytest.mark.parametrize("layer, datatype", [(12, 0), (8, 1), (10, 5), (999, 1), (0, 0)])
def test_c13_unknown_polygon_layer_fails(design, layer, datatype):
    design.core.add(rect(100, 100, 110, 110, layer, datatype))
    outcome = design.check()
    assert outcome.status("C13.1") == "FAIL"
    assert f"{layer}/{datatype}" in outcome.evidence("C13.1")
    assert "MYCORE" in outcome.evidence("C13.1")
    assert "(100.000, 100.000)" in outcome.evidence("C13.1")


def test_c13_unknown_label_layer_fails(design):
    design.core.add(gdstk.Label("X", (1, 1), layer=8, texttype=3))
    assert design.check().status("C13.1") == "FAIL"


def test_c13_unknown_path_layer_fails(design):
    design.core.add(gdstk.FlexPath([(0, 0), (1, 0)], 1.0, layer=77, simple_path=True))
    assert design.check().status("C13.1") == "FAIL"


def test_c13_marker_layers_are_allowed(design):
    design.core.add(rect(100, 100, 110, 110, 43), rect(120, 100, 130, 110, 46), rect(140, 100, 150, 110, 35))
    assert design.check().status("C13.1") == "PASS"


# --- 14: layer 999


def test_c14_logo_cover_is_fine(design):
    outcome = design.check()
    assert outcome.status("C14.1") == "PASS"
    assert outcome.status("C14.2") == "PASS"
    assert outcome.status("C14.3") == "PASS"
    assert "1 polygon on layer 999:0" in outcome.evidence("C14.1")


def test_c14_extra_polygon_warns_and_lists_hidden_layers(design):
    design.core.add(rect(0, 0, 30, 30, 999))
    outcome = design.check()
    assert outcome.status("C14.1") == "PASS"
    assert outcome.status("C14.2") == "WARN"
    assert "(500.000, 500.000) to (530.000, 530.000)" in outcome.evidence("C14.2")
    assert "8/0" in outcome.evidence("C14.2")


def test_c14_over_limit_fails(design):
    design.core.add(rect(0, 0, 1, 1, 999), rect(3, 0, 4, 1, 999))
    outcome = design.check()
    assert outcome.status("C14.1") == "FAIL"
    assert "3 polygons" in outcome.evidence("C14.1")


def test_c14_limit_counts_instances(design):
    leaf = gdstk.Cell("LEAF")
    leaf.add(rect(0, 0, 1, 1, 999))
    design.add_cell(leaf, origin=(800, 800))
    design.top.add(gdstk.Reference(leaf, (810, 800)))
    outcome = design.check()
    assert outcome.status("C14.1") == "FAIL"
    assert "0 drawn directly" in outcome.evidence("C14.1")


def test_c14_edited_logo_is_not_exempt(design):
    logo = design.children[LOGO]
    logo.add(rect(100, 100, 300, 300, 999))
    outcome = design.check()
    assert outcome.status("C14.2") == "WARN"
    assert outcome.status("C10.1") == "PASS"


def test_c14_user_shape_under_logo_cover_warns(design):
    design.top.add(rect(910, 710, 920, 720, 10))
    outcome = design.check()
    assert outcome.status("C14.3") == "WARN"
    assert "layer 10/0" in outcome.evidence("C14.3")


# --- 15: silicon number block


def make_block() -> gdstk.Cell:
    block = gdstk.Cell("BLOCKTOP")
    block.add(rect(0, 0, 40, 10, 8), rect(0, 20, 40, 30, 10), rect(0, 0, 40, 30, 999))
    block.add(rect(5, 5, 15, 8, 11))
    return block


@pytest.fixture
def block_file(tmp_path):
    lib = new_lib()
    lib.add(make_block())
    path = tmp_path / "block.gds"
    lib.write_gds(path)
    return path


def test_c15_manual_without_block(design):
    assert design.check().status("C15.1") == "MANUAL"


def test_c15_present_unchanged(design, block_file):
    design.add_cell(make_block(), origin=(1000, 1000))
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "PASS"
    assert "(1000.000, 1000.000)" in outcome.evidence("C15.1")
    # A declared block isn't stray on layer 999 or the pad layer.
    assert outcome.status("C14.2") == "PASS"
    assert outcome.status("C10.4") == "PASS"


def test_c15_undeclared_block_trips_the_stray_checks(design):
    design.add_cell(make_block(), origin=(1000, 1000))
    outcome = design.check()
    assert outcome.status("C14.2") == "WARN"
    assert outcome.status("C10.4") == "WARN"


def test_c15_missing_fails(design, block_file):
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "FAIL"
    assert "not found" in outcome.results["C15.1"]["title"]


def test_c15_edited_fails(design, block_file):
    block = make_block()
    block.add(rect(0, 12, 5, 14, 8))
    design.add_cell(block, origin=(1000, 1000))
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "FAIL"
    assert "edited" in outcome.results["C15.1"]["title"]


def test_c15_scaled_instance_fails(design, block_file):
    design.add_cell(make_block(), origin=(1000, 1000), magnification=2.0)
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "FAIL"
    assert "scaled" in outcome.results["C15.1"]["title"]


def test_c15_rescaled_geometry_is_not_found(design, block_file):
    block = gdstk.Cell("BLOCKTOP")
    for polygon in make_block().polygons:
        block.add(polygon.copy().scale(1.5))
    design.add_cell(block, origin=(1000, 1000))
    assert design.check("--silicon-block", str(block_file)).status("C15.1") == "FAIL"


def test_c15_renamed_warns(design, block_file):
    block = make_block()
    block.name = "RENAMED"
    design.add_cell(block, origin=(1000, 1000))
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "WARN"
    assert "renamed" in outcome.results["C15.1"]["title"]


def test_c15_rotated_warns(design, block_file):
    design.add_cell(make_block(), origin=(1000, 1000), rotation=QUARTER)
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "WARN"


def test_c15_flattened_into_the_top_cell_passes(design, block_file):
    for polygon in make_block().polygons:
        design.top.add(polygon.copy().translate(1000, 1000))
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "PASS"
    assert "flattened" in outcome.evidence("C15.1")
    assert outcome.status("C14.2") == "PASS"


def test_c15_flattened_with_an_added_shape_fails(design, block_file):
    for polygon in make_block().polygons:
        design.top.add(polygon.copy().translate(1000, 1000))
    design.top.add(rect(1001, 1012, 1005, 1014, 8))
    outcome = design.check("--silicon-block", str(block_file))
    assert outcome.status("C15.1") == "FAIL"
    assert "edited" in outcome.results["C15.1"]["title"]


def test_c15_flattened_with_a_shape_on_a_layer_the_block_lacks_fails(design, block_file):
    for polygon in make_block().polygons:
        design.top.add(polygon.copy().translate(1000, 1000))
    design.top.add(rect(1001, 1012, 1005, 1014, 4))
    assert design.check("--silicon-block", str(block_file)).status("C15.1") == "FAIL"


def test_c15_block_cell_named_like_the_top_cell(design, tmp_path):
    block = make_block()
    block.name = design.silicon
    lib = new_lib()
    lib.add(block)
    path = tmp_path / "issued.gds"
    lib.write_gds(path)
    for polygon in make_block().polygons:
        design.top.add(polygon.copy().translate(1000, 1000))
    assert design.check("--silicon-block", str(path)).status("C15.1") == "PASS"


def test_c15_unreadable_block_fails(design, tmp_path):
    bad = tmp_path / "bad.gds"
    bad.write_bytes(b"junk bytes")
    assert design.check("--silicon-block", str(bad)).status("C15.1") == "FAIL"
    assert design.check("--silicon-block", str(tmp_path / "absent.gds")).status("C15.1") == "FAIL"
