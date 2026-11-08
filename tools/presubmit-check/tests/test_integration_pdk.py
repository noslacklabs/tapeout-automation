"""Optional integration tests against the real PDK. Skipped when it isn't present.

The kit is only read. These tests write nothing into it and hold no PDK values.
"""

from __future__ import annotations

import hashlib
import json

import gdstk
import pytest

from presubmit_check.cli import main
from presubmit_check.gdsraw import read_gds
from presubmit_check.refdata import load_config

from .conftest import Outcome, real_pdk_root, run_cli

ROOT = real_pdk_root()
CONFIG = load_config(None)
pytestmark = [pytest.mark.pdk, pytest.mark.skipif(ROOT is None, reason="PDK not present")]


def frame_path(die: int, frame_set: str = "padframe"):
    entry = CONFIG["pdk"]["frame_sets"][frame_set]
    return ROOT / entry["frames_dir"] / CONFIG["pdk"]["frame_file"].format(die=die)


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_on_copy_free(path, capsys, tmp_path, die, *extra):
    """Run against the kit's file in place, sending the JSON outside the PDK."""
    out = tmp_path / "result.json"
    capsys.readouterr()
    code = main([str(path), "--die", str(die), "--silicon", "OS0001", "--pdk-root", str(ROOT), "--json", str(out), *extra])
    captured = capsys.readouterr()
    return Outcome(code, captured.out, captured.err, json.loads(out.read_text()))


def test_unmodified_2mm_frame_gives_the_expected_results(capsys, tmp_path):
    path = frame_path(2)
    before = digest(path)
    outcome = run_on_copy_free(path, capsys, tmp_path, 2)
    assert digest(path) == before, "the PDK file must not change"
    assert outcome.code == 1
    assert outcome.status("C03.1") == "PASS"  # extent
    assert outcome.status("C02.3") == "FAIL"  # wrong top cell
    assert outcome.status("C02.4") == "FAIL"  # $$$CONTEXT_INFO$$$
    assert outcome.status("C04.2") == "WARN"  # off-grid pad points
    assert outcome.status("C04.1") == "PASS"
    assert outcome.status("C08.1") == "PASS"
    assert outcome.status("C09.1") == "PASS"
    assert outcome.status("C09.2") == "PASS"
    assert outcome.status("C09.3") == "WARN"
    assert outcome.status("C10.1") == "PASS"
    assert outcome.status("C10.3") == "PASS"
    assert outcome.status("C11.1") == "PASS"
    assert outcome.status("C13.1") == "PASS"
    assert outcome.status("C14.1") == "PASS"
    assert outcome.status("C16.1") == "PASS"
    assert outcome.status("C17.1") == "PASS"
    assert outcome.status("C16.2") == "WARN"
    assert "does not replace DRC or LVS" in outcome.text


@pytest.mark.parametrize("frame_set", ["padframe", "analog"])
@pytest.mark.parametrize("die", [1, 2, 3, 4, 5])
def test_every_shipped_frame_is_self_consistent(capsys, tmp_path, die, frame_set):
    """Each frame must agree with itself: any FAIL beyond naming would be a false alarm."""
    outcome = run_on_copy_free(frame_path(die, frame_set), capsys, tmp_path, die, "--frame-set", frame_set)
    fails = set(outcome.statuses("FAIL"))
    assert fails <= {"C02.3", "C02.4", "C05.1", "C05.3"}, {i: outcome.evidence(i) for i in fails}
    assert outcome.status("C03.1") == "PASS"
    assert outcome.status("C08.1") == "PASS"
    assert "matches set: " + frame_set in outcome.evidence("C09.3")


@pytest.mark.parametrize("die", [1, 2, 3, 4, 5])
def test_raw_parser_agrees_with_gdstk_on_real_frames(die):
    path = frame_path(die)
    raw = read_gds(path)
    other = gdstk.read_gds(path)
    assert raw.valid
    assert set(raw.cells) == {c.name for c in other.cells}
    assert set(raw.top_cells()) == {c.name for c in other.top_level()}
    for cell in other.cells:
        mine = raw.cells[cell.name]
        assert len(mine.shapes) == len(cell.polygons), cell.name
        assert len(mine.texts) == len(cell.labels), cell.name
        assert len(mine.refs) == len(cell.references), cell.name


def test_klayout_would_hide_the_context_cell():
    """Why the top-cell check can't use KLayout: documents the trap, doesn't depend on it."""
    import klayout.db as db

    layout = db.Layout()
    layout.read(str(frame_path(2)))
    names = {cell.name for cell in layout.top_cells()}
    assert "$$$CONTEXT_INFO$$$" not in names
    assert CONFIG["cells"]["context"] in {c.name for c in gdstk.read_gds(frame_path(2)).cells}


def test_run_cli_helper_refused_inside_pdk(capsys):
    """Using the normal helper would put its JSON beside the PDK file, which must be refused."""
    outcome = run_cli(frame_path(2), ROOT, capsys, 2, "OS0001")
    assert outcome.code == 2
    assert "inside the PDK" in outcome.error
