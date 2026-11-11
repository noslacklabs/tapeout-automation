"""Check routed wires against cell metal in GDS to detect shorts and overlaps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chipflow.stages.def_model import read_def

WIDTH = {"metal1": 2400, "metal2": 2500}
GDS_OFFSET = {"MX201B": 5200}


def check_wire_vs_cells(
    def_path: Path | str,
    core_gds_path: Path | str,
    lef_path: Path | str,
) -> dict[str, Any]:
    """Find routed wires that overlap cell metal they shouldn't.

    Returns dict(nets=int, cells=int, problems=list[dict]).
    """
    import klayout.db as db
    routed = read_def(def_path)
    lib = db.Layout()
    lib.read(str(core_gds_path))
    if abs(lib.dbu - 0.001) > 1e-12:
        raise ValueError("core GDS DBU does not match routed DEF")
    m1_layer = lib.layer(8, 0)
    m2_layer = lib.layer(10, 0)
    via_layer = lib.layer(9, 0)
    inst = {c.name: (c.master, c.x, c.y) for c in routed.components}

    pin_rects: dict[str, dict[str, list[db.Box]]] = {}
    cur = pn = la = None
    for line in Path(lef_path).read_text(encoding="utf-8", errors="ignore").splitlines():
        q = line.split()
        if not q:
            continue
        k = q[0].upper()
        if k == "MACRO":
            cur = q[1]
            pin_rects[cur] = {}
            pn = None
        elif k == "PIN":
            pn = q[1]
        elif k == "OBS":
            pn = None
        elif k == "LAYER":
            la = q[1]
        elif k == "RECT" and pn and la == "metal1" and cur:
            a, b, c, d = [int(round(float(v) * 1000.0)) for v in q[1:5]]
            pin_rects[cur].setdefault(pn, []).append(db.Box(a, b, c, d))

    cell_cache: dict[str, tuple[db.Region, db.Region, dict[str, tuple[db.Region, db.Region]]]] = {}

    def cell_metal(master: str):
        if master in cell_cache:
            return cell_cache[master]
        c = lib.cell(master)
        if c is None:
            raise ValueError(f"cell master {master!r} missing from core GDS")
        off = GDS_OFFSET.get(master, 0)
        m1 = db.Region(c.begin_shapes_rec(m1_layer)).merged().moved(off, 0)
        m2 = db.Region(c.begin_shapes_rec(m2_layer)).merged().moved(off, 0)
        vi = db.Region(c.begin_shapes_rec(via_layer)).merged().moved(off, 0)
        pins: dict[str, tuple[db.Region, db.Region]] = {}
        for pin_name, rects in pin_rects.get(master, {}).items():
            seed = db.Region()
            for b in rects:
                seed.insert(b)
            a1 = m1.interacting(seed)
            a2 = db.Region()
            for _ in range(4):
                v = vi.interacting(a1)
                a2 = m2.interacting(v)
                a1 = (a1 + m1.interacting(vi.interacting(a2))).merged()
            pins[pin_name] = (a1.merged(), a2.merged())
        cell_cache[master] = (m1, m2, pins)
        return cell_cache[master]

    problems: list[dict[str, Any]] = []
    nets_checked = len(routed.nets)
    all_m1 = {n.name: db.Region() for n in routed.nets}
    all_m2 = {n.name: db.Region() for n in routed.nets}
    netpins = {n.name: list(n.connections) for n in routed.nets}
    for wire in routed.wires:
        (all_m1 if wire.layer == "metal1" else all_m2)[wire.net].insert(db.Box(*wire.box))
    for via in routed.vias:
        box = db.Box(via.x - 1250, via.y - 1250, via.x + 1250, via.y + 1250)
        all_m1[via.net].insert(box)
        all_m2[via.net].insert(box)
    all_m1 = {name: region.merged() for name, region in all_m1.items()}
    all_m2 = {name: region.merged() for name, region in all_m2.items()}

    # Allowed metal: pins of the same net
    allowed: dict[str, tuple[db.Region, db.Region]] = {}
    for name, plist in netpins.items():
        r1 = db.Region()
        r2 = db.Region()
        for iname, pin_name in plist:
            if iname == "PIN" or iname not in inst:
                continue
            master, x, y = inst[iname]
            _, _, pins = cell_metal(master)
            if pin_name in pins:
                a1, a2 = pins[pin_name]
                r1 += a1.transformed(db.Trans(x, y))
                r2 += a2.transformed(db.Trans(x, y))
        allowed[name] = (r1.merged(), r2.merged())

    # Check routed wire overlaps against cell metal
    for iname, (master, x, y) in inst.items():
        m1, m2, _ = cell_metal(master)
        c_inst = lib.cell(master)
        b = db.Box(x - 6000, y - 6000, x + c_inst.bbox().width() + 6000, y + 60000)
        tr = db.Trans(x, y)
        c1 = m1.transformed(tr)
        c2 = m2.transformed(tr)
        for name in all_m1:
            for label, wire, cm, idx in (("metal1", all_m1[name], c1, 0), ("metal2", all_m2[name], c2, 1)):
                if wire.is_empty() or not wire.bbox().touches(b):
                    continue
                ov = wire & cm
                if ov.is_empty():
                    continue
                ov = ov - allowed[name][idx]
                if not ov.is_empty():
                    bb = ov.bbox()
                    problems.append({
                        "net": name,
                        "layer": label,
                        "cell": master,
                        "inst": iname,
                        "x": x / 1000.0,
                        "y": y / 1000.0,
                        "at_um": [
                            (bb.left - x) / 1000.0,
                            (bb.bottom - y) / 1000.0,
                            (bb.right - x) / 1000.0,
                            (bb.top - y) / 1000.0,
                        ],
                    })

    # Wire-to-wire overlaps between different nets
    names = list(all_m1.keys())
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for label, wire_a, wire_b in (
                ("metal1", all_m1[a], all_m1[b]),
                ("metal2", all_m2[a], all_m2[b]),
            ):
                if wire_a.is_empty() or wire_b.is_empty() or not wire_a.bbox().touches(wire_b.bbox()):
                    continue
                ov = wire_a & wire_b
                if not ov.is_empty():
                    bb = ov.bbox()
                    problems.append({
                        "net": f"{a} + {b}",
                        "layer": label,
                        "cell": "wire",
                        "inst": "",
                        "x": bb.left / 1000.0,
                        "y": bb.bottom / 1000.0,
                        "at_um": [],
                    })

    return {
        "nets": nets_checked,
        "cells": len(inst),
        "problems": problems,
    }
