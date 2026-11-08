"""Test fixtures. All geometry here is invented, and no PDK data is used.

A throwaway "kit" is written into a temp folder under the same relative paths as
the default config, including a folder name with a mu in it, so the real code paths
run against made-up libraries with made-up cell names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import gdstk
import pytest

from presubmit_check.cli import main
from presubmit_check.refdata import load_config

CONFIG = load_config(None)
LOGO = CONFIG["cells"]["logo"]
CONTEXT = CONFIG["cells"]["context"]
VDD = CONFIG["cells"]["vdd"]
VSS = CONFIG["cells"]["vss"]
PAD_NAMES = ["FAKEPAD", "FAKEPAD2", "FAKEOFFG", VDD, VSS]
STD_NAMES = ["FAKESTD1", "FAKESTD2", "FAKESTD3"]
GOOD_NAME = "OS0001_MyChip_25072026_v0.gds"


def new_lib() -> gdstk.Library:
    return gdstk.Library("LIB", unit=1e-6, precision=1e-9)


def rect(x1, y1, x2, y2, layer, datatype=0):
    return gdstk.rectangle((x1, y1), (x2, y2), layer=layer, datatype=datatype)


def make_pad(name: str, window: float = 90.0) -> gdstk.Cell:
    """An invented pad: metal, window and outline, 110 x 110 um."""
    cell = gdstk.Cell(name)
    cell.add(rect(0, 0, 110, 110, 8), rect(0, 0, 110, 110, 10), rect(0, 0, 110, 110, 28))
    cell.add(rect(10, 10, 10 + window, 10 + window, 11))
    cell.add(gdstk.Label("PAD", (55, 55), layer=8, texttype=7))
    if name == "FAKEOFFG":
        cell.add(gdstk.Polygon([(0, 120), (20.001, 120), (20.001, 130), (0, 130)], layer=2))
    return cell


def make_std(name: str) -> gdstk.Cell:
    cell = gdstk.Cell(name)
    cell.add(rect(0, 0, 10, 40, 8), rect(1, 1, 3, 3, 7), rect(0, 20, 10, 22, 4))
    if name == "FAKESTD2":
        cell.add(gdstk.Polygon([(5, 5), (6.001, 5), (6.001, 6), (5, 6)], layer=2))
    if name == "FAKESTD3":
        cell.add(rect(1, 20, 3, 22, 9))  # via over poly inside the cell
    return cell


def make_logo(with_ignore: bool = True) -> gdstk.Cell:
    cell = gdstk.Cell(LOGO)
    cell.add(rect(0, 0, 50, 60, 28), rect(5, 5, 45, 45, 8), rect(5, 5, 45, 45, 11))
    cell.add(rect(5, 5, 20, 20, 9), rect(5, 5, 20, 20, 7), rect(5, 5, 20, 20, 4))
    if with_ignore:
        cell.add(rect(0, 0, 50, 60, 999))
    return cell


def pad_layout(die: int) -> list:
    """Positions for an invented frame: (cell, x, y, rotation in degrees, mirror)."""
    size = die * 1000.0
    if die == 1:
        return [
            (VDD, 110, 300, 90, False), (VSS, size - 110, 500, 90, True),
            ("FAKEPAD", 300, 0, 0, False), ("FAKEPAD2", 500, 0, 0, False),
            ("FAKEPAD", 300, size, 0, True), ("FAKEPAD2", 500, size, 0, True),
        ]
    assert die == 2
    placed = [
        (VDD, 110, 300, 90, False), (VDD, size - 110, 1000, 90, True),
        (VSS, 110, 1000, 90, False), (VSS, size - 110, 300, 90, True),
    ]
    for i in range(6):
        cell = "FAKEPAD" if i % 2 else "FAKEPAD2"
        x = 300 + 200 * i
        placed.append((cell, x, 0, 0, False))
        placed.append((cell, x, size, 0, True))
    return placed


def make_frame(die: int, variant: str = "padframe") -> tuple[gdstk.Cell, list[gdstk.Cell]]:
    """The frame cell plus the pad cells and logo it uses."""
    size = die * 1000.0
    frame = gdstk.Cell(f"frame{die}mmx{die}mm")
    frame.add(rect(0, 0, size, size, 28))
    frame.add(rect(0, 0, size, 20, 8))
    frame.add(gdstk.Label("VDD", (150.001, 150.002), layer=8, texttype=7))
    if variant == "analog":
        frame.add(rect(0, size - 20, size, size, 8))
    pads = {}
    for name, x, y, rotation, mirror in pad_layout(die):
        pads.setdefault(name, make_pad(name))
        frame.add(gdstk.Reference(pads[name], (x, y), rotation=rotation * 3.14159265358979 / 180, x_reflection=mirror))
    logo = make_logo(variant == "padframe")
    frame.add(gdstk.Reference(logo, (900 if die == 2 else 400, 700)))
    return frame, list(pads.values()) + [logo]


def write_frame_file(path: Path, die: int, variant: str) -> None:
    frame, children = make_frame(die, variant)
    lib = new_lib()
    context = gdstk.Cell(CONTEXT)
    context.add(gdstk.Reference(children[0]))
    lib.add(frame, context, *children)
    lib.write_gds(path)


@pytest.fixture(scope="session")
def fake_pdk(tmp_path_factory) -> Path:
    """A made-up kit under a folder whose name contains a mu."""
    root = tmp_path_factory.mktemp("kit") / "SCL 1.2 µm PDK" / "SCL_C1D_FAKE"
    pdk = CONFIG["pdk"]
    for relative in [pdk["pad_list"], pdk["core_lef"], pdk["core_gds"]]:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / pdk["pad_list"]).write_text("\n".join(PAD_NAMES) + "\n")
    (root / pdk["core_lef"]).write_text(
        "VERSION 5.3 ;\n\n" + "".join(f"macro {n}\n  CLASS CORE ;\nEND {n}\n\n" for n in STD_NAMES)
    )
    core = new_lib()
    core.add(*[make_std(n) for n in STD_NAMES])
    core.write_gds(root / pdk["core_gds"])
    for variant, entry in pdk["frame_sets"].items():
        frames = root / entry["frames_dir"]
        frames.mkdir(parents=True, exist_ok=True)
        pad_path = root / entry["pad_gds"]
        pad_path.parent.mkdir(parents=True, exist_ok=True)
        pads = new_lib()
        pads.add(*[make_pad(n) for n in PAD_NAMES])
        pads.add(make_logo(variant == "padframe"))
        pads.write_gds(pad_path)
        for die in (1, 2):
            write_frame_file(frames / pdk["frame_file"].format(die=die), die, variant)
    return root


class Design:
    """A design built on the fake kit. Starts as one that should have no FAILs."""

    def __init__(self, folder: Path, pdk: Path, capsys, die: int = 2, silicon: str = "OS0001"):
        self.folder = folder
        self.pdk = pdk
        self.capsys = capsys
        self.die = die
        self.silicon = silicon
        self.lib = new_lib()
        self.frame, children = make_frame(die)
        self.children = {c.name: c for c in children}
        self.top = gdstk.Cell(silicon)
        self.core = gdstk.Cell("MYCORE")
        self.core.add(rect(0, 0, 20, 20, 8), rect(40, 0, 60, 20, 10))
        self.top.add(gdstk.Reference(self.frame), gdstk.Reference(self.core, (500, 500)))
        self.lib.add(self.top, self.frame, self.core, *children)

    def add_cell(self, cell: gdstk.Cell, origin=(800, 800), **kwargs) -> gdstk.Cell:
        self.lib.add(cell)
        self.top.add(gdstk.Reference(cell, origin, **kwargs))
        return cell

    def pad_refs(self, name: str) -> list:
        return [r for r in self.frame.references if r.cell.name == name]

    def write(self, name: str = GOOD_NAME) -> Path:
        path = self.folder / name
        self.lib.write_gds(path)
        return path

    def check(self, *extra, name: str = GOOD_NAME, path: Path | None = None, die: int | None = None,
              silicon: str | None = None):
        path = path or self.write(name)
        return run_cli(path, self.pdk, self.capsys, die or self.die, silicon or self.silicon, *extra)


class Outcome:
    def __init__(self, code: int, text: str, error: str, payload: dict | None):
        self.code, self.text, self.error, self.payload = code, text, error, payload
        self.results = {r["id"]: r for r in payload["results"]} if payload else {}

    def status(self, id_: str) -> str:
        return self.results[id_]["status"]

    def evidence(self, id_: str) -> str:
        return "\n".join(self.results[id_]["evidence"])

    def statuses(self, wanted: str) -> list[str]:
        return [i for i, r in self.results.items() if r["status"] == wanted]


def run_cli(path, pdk, capsys, die=2, silicon="OS0001", *extra) -> Outcome:
    out = Path(str(path) + ".result.json")
    if out.exists():
        out.unlink()
    capsys.readouterr()
    code = main([str(path), "--die", str(die), "--silicon", silicon, "--pdk-root", str(pdk), "--json", str(out), *extra])
    captured = capsys.readouterr()
    payload = json.loads(out.read_text()) if out.exists() else None
    return Outcome(code, captured.out, captured.err, payload)


@pytest.fixture
def design(tmp_path, fake_pdk, capsys) -> Design:
    return Design(tmp_path, fake_pdk, capsys)


@pytest.fixture
def design_1mm(tmp_path, fake_pdk, capsys) -> Design:
    return Design(tmp_path, fake_pdk, capsys, die=1)


def real_pdk_root() -> Path | None:
    default = ("/mnt/m/Desktop Folders/My Projects/Tapeout Automation/scl-pdk-1.2um/pdk/"
               "SCL 1.2 µm PDK/SCL_C1D_PDK_17092026")
    root = Path(os.environ.get("PRESUBMIT_PDK_ROOT", default))
    return root if root.is_dir() else None
