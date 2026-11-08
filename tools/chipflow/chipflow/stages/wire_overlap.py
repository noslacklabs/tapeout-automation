"""Check routed wires against cell metal in GDS to detect shorts and overlaps."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import klayout.db as db

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
    lib = db.Layout()
    lib.read(str(core_gds_path))
    m1_layer = lib.layer(8, 0)
    m2_layer = lib.layer(10, 0)
    via_layer = lib.layer(9, 0)

    txt = Path(def_path).read_text(encoding="utf-8", errors="ignore")
    comp_match = re.search(r"\nCOMPONENTS\s+\d+\s*;(.*?)END COMPONENTS", txt, re.S)
    if not comp_match:
        raise ValueError(f"no COMPONENTS section in DEF: {def_path}")

    inst: dict[str, tuple[str, int, int]] = {}
    for st in comp_match.group(1).split(";"):
        m = re.search(
            r"-\s+(\S+)\s+(\S+).*?\+\s+(?:PLACED|FIXED|LOCKED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)",
            st,
            re.S,
        )
        if m:
            inst[m.group(1)] = (m.group(2), int(m.group(3)), int(m.group(4)))

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

    nets_idx = txt.find("\nNETS ")
    end_nets_idx = txt.find("END NETS")
    if nets_idx < 0 or end_nets_idx < 0:
        raise ValueError(f"no NETS section in DEF: {def_path}")
    sec = txt[nets_idx:end_nets_idx]

    problems: list[dict[str, Any]] = []
    nets_checked = 0
    all_m1: dict[str, db.Region] = {}
    all_m2: dict[str, db.Region] = {}
    netpins: dict[str, list[tuple[str, str]]] = {}

    for st in re.split(r"\n\s*-\s+", sec)[1:]:
        name = st.split(None, 1)[0]
        nets_checked += 1
        netpins[name] = re.findall(r"\(\s*(\S+)\s+(\S+)\s*\)", st.split("+")[0])
        tok = st.replace("(", " ( ").replace(")", " ) ").split()
        w: dict[str, db.Region] = {"metal1": db.Region(), "metal2": db.Region()}
        lay: str | None = None
        px = py = None
        pe: int | None = None
        i = 0
        while i < len(tok):
            t = tok[i]
            if t in ("ROUTED", "NEW", "FIXED", "COVER"):
                lay = tok[i + 1]
                px = None
                i += 2
                continue
            if lay is None:
                i += 1
                continue
            if t == "(":
                j = i + 1
                while j < len(tok) and tok[j] != ")":
                    j += 1
                x_str, y_str = tok[i + 1], tok[i + 2]
                ext = int(tok[i + 3]) if j - i > 3 else None
                nx = px if x_str == "*" else int(x_str)
                ny = py if y_str == "*" else int(y_str)
                if px is not None and nx is not None and ny is not None and py is not None:
                    h = WIDTH[lay] // 2
                    e0 = h if pe is None else pe
                    e1 = h if ext is None else ext
                    if nx == px:
                        a, b = (py, ny) if py <= ny else (ny, py)
                        ea, eb = (e0, e1) if py <= ny else (e1, e0)
                        w[lay].insert(db.Box(px - h, a - ea, px + h, b + eb))
                    else:
                        a, b = (px, nx) if px <= nx else (nx, px)
                        ea, eb = (e0, e1) if px <= nx else (e1, e0)
                        w[lay].insert(db.Box(a - ea, py - h, b + eb, py + h))
                px, py, pe = nx, ny, ext
                i = j + 1
                continue
            if t == "RECT":
                j = i + 1
                while j < len(tok) and (tok[j] != ")" or (j + 1 < len(tok) and tok[j + 1] == "(")):
                    j += 1
                rect_vals = [int(v) for v in tok[i + 2:j] if v not in "()"]
                if len(rect_vals) == 4 and px is not None and py is not None:
                    ra, rb, rc, rd = rect_vals
                    w[lay].insert(db.Box(px + ra, py + rb, px + rc, py + rd))
                i = j + 1
                continue
            if t == "VIA12" and px is not None and py is not None:
                w["metal1"].insert(db.Box(px - 1250, py - 1250, px + 1250, py + 1250))
                w["metal2"].insert(db.Box(px - 1250, py - 1250, px + 1250, py + 1250))
                lay = "metal2" if lay == "metal1" else "metal1"
                pe = None
            i += 1
        all_m1[name] = w["metal1"].merged()
        all_m2[name] = w["metal2"].merged()

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
