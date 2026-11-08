"""The raw parser: strictness, and agreement with gdstk on valid files."""

from __future__ import annotations

import struct

import gdstk
import pytest

from presubmit_check.gdsraw import parse_gds, read_gds, real8
from presubmit_check.geom import canonical_polygon

from .conftest import CONTEXT, make_frame, new_lib, rect, run_cli

UNITS = bytes.fromhex("3E4189374BC6A7EF") + bytes.fromhex("3944B82FA09B5A54")


def rec(rtype: int, dtype: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload


def stream(*cell_records: bytes) -> bytes:
    start = (
        rec(0x00, 2, struct.pack(">h", 600))
        + rec(0x01, 2, bytes(24))
        + rec(0x02, 6, b"LIBX")
        + rec(0x03, 5, UNITS)
    )
    return start + b"".join(cell_records) + rec(0x04, 0)


def cell(name: bytes, *elements: bytes) -> bytes:
    if len(name) % 2:
        name += b"\0"
    return rec(0x05, 2, bytes(24)) + rec(0x06, 6, name) + b"".join(elements) + rec(0x07, 0)


def boundary(points, layer=1, datatype=0) -> bytes:
    xy = b"".join(struct.pack(">ii", x, y) for x, y in points)
    return rec(0x08, 0) + rec(0x0D, 2, struct.pack(">h", layer)) + rec(0x0E, 2, struct.pack(">h", datatype)) + rec(0x10, 3, xy) + rec(0x11, 0)


SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]


def test_real8_decodes_known_values():
    assert real8(bytes.fromhex("4110000000000000")) == 1.0
    assert real8(bytes.fromhex("C110000000000000")) == -1.0
    assert real8(bytes.fromhex("0000000000000000")) == 0.0
    assert real8(UNITS[8:]) == pytest.approx(1e-9, rel=1e-12)


def test_minimal_stream_is_valid():
    lib = parse_gds(stream(cell(b"TOP", boundary(SQUARE))))
    assert lib.valid
    assert list(lib.cells) == ["TOP"]
    assert lib.cells["TOP"].shapes[0].pts == SQUARE[:-1]
    assert lib.nm_per_dbu == 1


@pytest.mark.parametrize(
    "mangle, message",
    [
        (lambda d: d[:-3], "truncated record header"),
        (lambda d: d[:-4], "without ENDLIB"),
        (lambda d: b"this is not a gds file at all", None),
        (lambda d: b"\x00\x05\x00\x02abc", "illegal record length"),
        (lambda d: b"", "empty"),
        (lambda d: d[6:], "must start with HEADER"),
        (lambda d: d + b"\x00\x04\x04\x00", None),  # padding after ENDLIB needs to be zeros
    ],
)
def test_broken_streams_are_reported(mangle, message):
    lib = parse_gds(mangle(stream(cell(b"TOP", boundary(SQUARE)))))
    assert not lib.valid
    if message:
        assert any(message in str(i) for i in lib.issues), [str(i) for i in lib.issues]


def test_zero_padding_after_endlib_is_allowed():
    assert parse_gds(stream(cell(b"TOP")) + bytes(512)).valid


def test_open_polygon_is_an_issue():
    lib = parse_gds(stream(cell(b"TOP", boundary([(0, 0), (10, 0), (10, 10), (0, 10), (1, 1)]))))
    assert any("not closed" in str(i) for i in lib.issues)


def test_wrong_data_type_is_fatal():
    bad = rec(0x05, 2, bytes(24)) + rec(0x06, 3, b"ABCD") + rec(0x07, 0)
    lib = parse_gds(stream(bad))
    assert lib.fatal and "data type" in str(lib.issues[0])


def test_undefined_reference_and_duplicates_are_issues():
    sref = rec(0x0A, 0) + rec(0x12, 6, b"GONE") + rec(0x10, 3, struct.pack(">ii", 0, 0)) + rec(0x11, 0)
    lib = parse_gds(stream(cell(b"TOP", sref), cell(b"TOP")))
    messages = "\n".join(str(i) for i in lib.issues)
    assert "undefined cell 'GONE'" in messages and "duplicate cell name 'TOP'" in messages


def test_reference_cycle_is_fatal():
    def sref(name):
        return rec(0x0A, 0) + rec(0x12, 6, name) + rec(0x10, 3, struct.pack(">ii", 0, 0)) + rec(0x11, 0)

    lib = parse_gds(stream(cell(b"AAAA", sref(b"BBBB")), cell(b"BBBB", sref(b"AAAA"))))
    assert lib.fatal and "cycle" in str(lib.issues[-1])


def test_aref_lattice_expands():
    aref = (
        rec(0x0B, 0)
        + rec(0x12, 6, b"LEAF")
        + rec(0x13, 2, struct.pack(">hh", 2, 3))
        + rec(0x10, 3, struct.pack(">iiiiii", 0, 0, 200, 0, 0, 300))
        + rec(0x11, 0)
    )
    lib = parse_gds(stream(cell(b"TOP", aref), cell(b"LEAF", boundary(SQUARE))))
    origins = sorted(lib.cells["TOP"].refs[0].origins())
    assert origins == [(0, 0), (0, 100), (0, 200), (100, 0), (100, 100), (100, 200)]


def test_agrees_with_gdstk_on_a_synthetic_file(tmp_path):
    frame, children = make_frame(2)
    lib = new_lib()
    context = gdstk.Cell(CONTEXT)
    context.add(gdstk.Reference(children[0]))
    path_cell = gdstk.Cell("WITHPATH")
    path_cell.add(gdstk.FlexPath([(0, 0), (10, 0), (10, 10)], 1.0, layer=8, simple_path=True))
    lib.add(frame, context, path_cell, *children)
    path = tmp_path / "x.gds"
    lib.write_gds(path)

    raw = read_gds(path)
    other = gdstk.read_gds(path)
    assert raw.valid
    assert set(raw.cells) == {c.name for c in other.cells}
    assert {c.name for c in other.top_level()} == set(raw.top_cells()) | set()
    for cell_ in other.cells:
        mine = raw.cells[cell_.name]
        assert len(mine.shapes) == len(cell_.polygons)
        assert len(mine.refs) == len(cell_.references)
        assert len(mine.texts) == len(cell_.labels)
        assert len(mine.paths) == len(cell_.paths)
    assert raw.dbu_m == pytest.approx(1e-9)


def test_context_cell_references_do_not_hide_a_top_cell():
    sref = rec(0x0A, 0) + rec(0x12, 6, b"LOST") + rec(0x10, 3, struct.pack(">ii", 0, 0)) + rec(0x11, 0)
    lib = parse_gds(stream(cell(CONTEXT.encode(), sref), cell(b"REAL"), cell(b"LOST")))
    assert sorted(lib.top_cells(ignore=CONTEXT)) == ["LOST", "REAL"]


def test_absolute_transform_flag_warns(tmp_path, fake_pdk, capsys):
    sref = (
        rec(0x0A, 0)
        + rec(0x12, 6, b"LEAF")
        + rec(0x1A, 1, struct.pack(">H", 0x0004))
        + rec(0x10, 3, struct.pack(">ii", 0, 0))
        + rec(0x11, 0)
    )
    path = tmp_path / "x.gds"
    path.write_bytes(stream(cell(b"OS0001", sref), cell(b"LEAF", boundary(SQUARE))))
    outcome = run_cli(path, fake_pdk, capsys)
    assert outcome.status("C04.5") == "WARN"
    assert "absolute" in outcome.evidence("C04.5")


def test_canonical_polygon_ignores_start_and_direction():
    points = [(0, 0), (10, 0), (10, 10), (0, 10)]
    variants = [points[k:] + points[:k] for k in range(4)]
    variants += [list(reversed(v)) for v in variants]
    assert len({canonical_polygon(v) for v in variants}) == 1
    assert canonical_polygon(points) != canonical_polygon([(0, 0), (11, 0), (10, 10), (0, 10)])


def test_rect_helper_is_closed_in_the_stream(tmp_path):
    lib = new_lib()
    item = gdstk.Cell("A")
    item.add(rect(0, 0, 1, 1, 8))
    lib.add(item)
    path = tmp_path / "a.gds"
    lib.write_gds(path)
    assert read_gds(path).valid
