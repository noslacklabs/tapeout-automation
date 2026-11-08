"""Assembly stage: top-level GDS assembly, CDL schematic generation, and metal-only power connectivity check."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any

import klayout.db as db

from chipflow.config.project import ProjectConfig
from chipflow.runner.execution import (
    Artifact,
    Assessment,
    CommandResult,
    StageSpec,
    atomic_write_json,
    sha256_file,
)
from chipflow.runner.results import CheckResult, CheckStatus
from chipflow.stages.envelope import StageEnvelope
from chipflow.technologies.profile import ProcessProfile
from chipflow.toolchain import Toolchain, verify_file


def _validate_nonempty(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"required output {path.name} is missing or empty")


ASSEMBLY_RUNNER_SCRIPT = """#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

import klayout.db as db

config = json.loads(Path("assembly_config.json").read_text(encoding="utf-8"))
top_cell = config["top_cell"]
fp = json.load(open("floorplan.json", encoding="utf-8"))

U = 1000
GRID = 5

def dbu(v):
    n = int(round(v * U))
    assert n % GRID == 0, f"off grid: {v}"
    return n

# ---------------------------------------------------------------- Parse DEF
txt = Path("routed.def").read_text(encoding="utf-8", errors="ignore")
comp = re.search(r"\\nCOMPONENTS\\s+\\d+\\s*;(.*?)END COMPONENTS", txt, re.S).group(1)
insts = []
for st in comp.split(";"):
    m = re.search(r"-\\s+(\\S+)\\s+(\\S+).*?\\+\\s+(?:PLACED|FIXED|LOCKED)\\s+\\(\\s*(-?\\d+)\\s+(-?\\d+)\\s*\\)\\s+(\\S+)", st, re.S)
    if m:
        assert m.group(5) == "N", (m.group(1), m.group(5))
        insts.append((m.group(2), int(m.group(3)), int(m.group(4)), m.group(1)))

WIDTH = {"metal1": 2400, "metal2": 2500}
VIA = {"VIA12": ((-1250, -1250, 1250, 1250), (-750, -750, 750, 750), (-1250, -1250, 1250, 1250))}
wires = {"metal1": [], "metal2": []}
vias = []
netsec = re.search(r"\\nNETS\\s+\\d+\\s*;(.*?)END NETS", txt, re.S).group(1)
tok = netsec.replace("(", " ( ").replace(")", " ) ").split()
i = 0
route = False
layer = None
cx = cy = None
prev = False
prev_ext = None
while i < len(tok):
    t = tok[i]
    if t in (";", "-"):
        route = prev = False
        i += 2 if t == "-" else 1
        continue
    if t == "+":
        kw = tok[i + 1]
        route = kw in ("ROUTED", "FIXED", "COVER")
        if route:
            layer = tok[i + 2]
            prev = False
            i += 3
            continue
        i += 2
        route = False
        continue
    if t == "NEW":
        route = True
        layer = tok[i + 1]
        prev = False
        i += 2
        continue
    if not route or t == "TAPER":
        i += 1
        continue
    if t == "(":
        j = i + 1
        while tok[j] != ")":
            j += 1
        x, y = tok[i + 1], tok[i + 2]
        ext = int(tok[i + 3]) if j - i > 3 else None
        nx = cx if x == "*" else int(x)
        ny = cy if y == "*" else int(y)
        if prev:
            h = WIDTH[layer] // 2
            e0 = h if prev_ext is None else prev_ext
            e1 = h if ext is None else ext
            if nx == cx:
                lo, hi = (cy, ny) if cy <= ny else (ny, cy)
                elo, ehi = (e0, e1) if cy <= ny else (e1, e0)
                wires[layer].append(db.Box(cx - h, lo - elo, cx + h, hi + ehi))
            else:
                lo, hi = (cx, nx) if cx <= nx else (nx, cx)
                elo, ehi = (e0, e1) if cx <= nx else (e1, e0)
                wires[layer].append(db.Box(lo - elo, cy - h, hi + ehi, cy + h))
        cx, cy = nx, ny
        prev = True
        prev_ext = ext
        i = j + 1
        continue
    if t == "RECT":
        j = i + 1
        while tok[j] != ")" or tok[j + 1] == "(":
            j += 1
        a, b, c, d = [int(v) for v in tok[i + 2:j] if v not in "()"]
        wires[layer].append(db.Box(cx + a, cy + b, cx + c, cy + d))
        i = j + 1
        continue
    assert t in VIA, f"unknown via {t}"
    vias.append((t, cx, cy))
    layer = "metal2" if layer == "metal1" else "metal1"
    prev = True
    prev_ext = None
    i += 1

pins_sec = re.search(r"\\nPINS\\s+\\d+\\s*;(.*?)END PINS", txt, re.S).group(1)
pin_shapes = []
for st in pins_sec.split(";"):
    m = re.search(r"\\+\\s+LAYER\\s+(\\S+)\\s+\\(\\s*(-?\\d+)\\s+(-?\\d+)\\s*\\)\\s+\\(\\s*(-?\\d+)\\s+(-?\\d+)\\s*\\).*?\\+\\s+(?:PLACED|FIXED|COVER)\\s+\\(\\s*(-?\\d+)\\s+(-?\\d+)\\s*\\)\\s+(\\S+)", st, re.S)
    if m:
        la = m.group(1)
        a, b, c, d, x, y = [int(m.group(k)) for k in range(2, 8)]
        assert m.group(8) in ("N", "S", "E", "W", "FN", "FS", "FE", "FW"), m.group(8)
        if m.group(8) in ("N", "FN", "S", "FS"):
            pin_shapes.append((la, db.Box(x + min(a, -c), y + min(b, -d), x + max(c, -a), y + max(d, -b))))
        else:
            pin_shapes.append((la, db.Box(x + min(b, -d), y + min(a, -c), x + max(d, -b), y + max(c, -a))))

# ---------------------------------------------------------------- Layout assembly
ly = db.Layout()
ly.dbu = 0.001
ly.read("frame.gds")
frame = ly.cell(config["frame_top_cell"])
lib = db.Layout()
lib.read("core.gds")
iolib = db.Layout()
iolib.read("io_pad.gds")

M1, M2, VIA1 = ly.layer(8, 0), ly.layer(10, 0), ly.layer(9, 0)
L1, L2 = ly.layer(8, 7), ly.layer(10, 7)
WIN = ly.layer(11, 0)

top = ly.create_cell(top_cell)
top.insert(db.CellInstArray(frame.cell_index(), db.Trans()))
core = ly.create_cell(top_cell + "_core")
top.insert(db.CellInstArray(core.cell_index(), db.Trans()))
wr = ly.create_cell(top_cell + "_wires")
top.insert(db.CellInstArray(wr.cell_index(), db.Trans()))

def get_cell(name):
    c = ly.cell(name)
    if c is None:
        c = ly.create_cell(name)
        c.copy_tree(lib.cell(name))
    return c

ORIGIN = {
    1: ("bottom", 350.95), 2: ("bottom", 700.95), 3: ("bottom", 1050.95), 4: ("bottom", 1400.95),
    5: ("right", 350.95), 6: ("right", 1605.8),
    7: ("top", 1400.95), 8: ("top", 1050.95), 9: ("top", 700.95), 10: ("top", 350.95),
    11: ("left", 1605.8), 12: ("left", 350.95),
}
frame_insts = {}
for inst in frame.each_inst():
    d = inst.dtrans.disp
    frame_insts[(round(d.x, 3), round(d.y, 3))] = inst

swapped = []
for p in fp["pads"]:
    side, org = ORIGIN[p["pad"]]
    key = {"bottom": (org, 304.9), "top": (org, 1695.1), "left": (304.9, org), "right": (1695.1, org)}[side]
    inst = frame_insts[key]
    old = ly.cell(inst.cell_index).name
    if old != p["cell"]:
        c = ly.cell(p["cell"])
        if c is None:
            c = ly.create_cell(p["cell"])
            c.copy_tree(iolib.cell(p["cell"]))
        inst.cell_index = c.cell_index()
        swapped.append(f"PAD{p['pad']} {old}->{p['cell']}")
    c = ly.cell(inst.cell_index)
    w = c.dbbox() and db.Region(c.shapes(WIN)).bbox()
    assert not w.empty(), p["cell"]
    pt = inst.trans * w.center()
    top.shapes(L2).insert(db.Text(f"PAD{p['pad']}", db.Trans(pt.x, pt.y)))

used_cells = {ly.cell(i.cell_index).name for i in frame.each_inst()}
for name in ("AIPAD00", "IPAD00"):
    if name not in used_cells and ly.cell(name) is not None:
        ly.cell(name).delete()

top.shapes(L1).insert(db.Text("VDD", db.Trans(235034, 1323041)))
top.shapes(L1).insert(db.Text("VSS", db.Trans(17122, 1323041)))

block_lo, block_hi = dbu(fp["block"][0]), dbu(fp["block"][1])
site = dbu(fp["site"])
GDS_OFFSET = {"MX201B": 5200}
lef_w = {}
m = None
for line in open("cells.lef"):
    q = line.split()
    if q and q[0].upper() == "MACRO":
        m = q[1]
    elif q and q[0].upper() == "SIZE" and m:
        lef_w.setdefault(m, int(round(float(q[1]) * U)))

byrow = {}
for master, x, y, name in insts:
    assert (x - block_lo) % site == 0, (name, x)
    assert x + lef_w[master] <= block_hi, (name, x)
    core.insert(db.CellInstArray(get_cell(master).cell_index(), db.Trans(x + GDS_OFFSET.get(master, 0), y)))
    byrow.setdefault(y, []).append((x, master))

rows = [dbu(v) for v in fp["rows"]]
assert set(byrow) <= set(rows), sorted(set(byrow) - set(rows))

filler = get_cell("FILLER2")
n_fill = 0
fill_w = 0
for master, x, y, name in insts:
    if master == "MX201B":
        assert lef_w[master] == 50400, lef_w[master]
        core.insert(db.CellInstArray(filler.cell_index(), db.Trans(x, y)))
        core.insert(db.CellInstArray(get_cell("FILLER4").cell_index(), db.Trans(x + 4200, y)))
        n_fill += 2

for y in rows:
    x = block_lo
    for cx_, master in sorted(byrow.get(y, [])) + [(block_hi, None)]:
        while x + site <= cx_:
            core.insert(db.CellInstArray(filler.cell_index(), db.Trans(x, y)))
            n_fill += 1
            fill_w += site
            x += site
        if master:
            x = cx_ + lef_w[master]

for y in rows:
    core.shapes(M1).insert(db.Box(block_lo, y, block_hi, y + 9000))
    core.shapes(M1).insert(db.Box(block_lo, y + 39700, block_hi, y + 48700))

for la, boxes in wires.items():
    lay = M1 if la == "metal1" else M2
    for b in boxes:
        core.shapes(lay).insert(b)
for name, x, y in vias:
    a, b, c = VIA[name]
    core.shapes(M1).insert(db.Box(*a).moved(x, y))
    core.shapes(VIA1).insert(db.Box(*b).moved(x, y))
    core.shapes(M2).insert(db.Box(*c).moved(x, y))
for la, b in pin_shapes:
    core.shapes(M1 if la == "metal1" else M2).insert(b)

def rect(lay, x1, y1, x2, y2):
    wr.shapes(lay).insert(db.Box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))

def via_array(x1, y1, x2, y2):
    nx = (x2 - x1 - 1000 + 1500) // 3000
    ny = (y2 - y1 - 1000 + 1500) // 3000
    assert nx > 0 and ny > 0
    ox = (x2 - x1 - (nx * 3000 - 1500)) // 2 // GRID * GRID
    oy = (y2 - y1 - (ny * 3000 - 1500)) // 2 // GRID * GRID
    for i_ in range(nx):
        for j_ in range(ny):
            xx = x1 + ox + i_ * 3000
            yy = y1 + oy + j_ * 3000
            wr.shapes(VIA1).insert(db.Box(xx, yy, xx + 1500, yy + 1500))
    return nx * ny

FREE_LO, FREE_HI = dbu(fp["free"][0]), dbu(fp["free"][1])
n_ring_vias = 0
for (a, b) in (fp["vss_ring"], fp["vdd_ring"]):
    a, b = dbu(a), dbu(b)
    a2, b2 = 2000000 - b, 2000000 - a
    rect(M2, a, a, b, b2 + (b - a))
    rect(M2, a2, a, b2, b2 + (b - a))
    rect(M1, a, a, b2, b)
    rect(M1, a, a2, b2, b2)
    for x1, x2 in ((a, b), (a2, b2)):
        for y1, y2 in ((a, b), (a2, b2)):
            n_ring_vias += via_array(x1, y1, x2, y2)

vss_lo, vss_hi = dbu(fp["vss_ring"][0]), dbu(fp["vss_ring"][1])
vdd_lo, vdd_hi = dbu(fp["vdd_ring"][0]), dbu(fp["vdd_ring"][1])
OVER = 900
for y in rows:
    rect(M1, vss_lo, y, block_lo, y + 9000)
    rect(M1, block_hi, y, 2000000 - vss_lo, y + 9000)
    via_array(vss_lo, y, vss_hi, y + 9000)
    via_array(2000000 - vss_hi, y, 2000000 - vss_lo, y + 9000)
    rect(M1, FREE_LO - OVER, y + 39700, block_lo, y + 48700)
    rect(M1, block_hi, y + 39700, FREE_HI + OVER, y + 48700)
    via_array(vdd_lo, y + 39700, vdd_hi, y + 48700)
    via_array(2000000 - vdd_hi, y + 39700, 2000000 - vdd_lo, y + 48700)

rect(M2, FREE_LO - 500, dbu(1130.3), vss_lo + 1000, dbu(1150.3))
rect(M2, FREE_HI + 500, dbu(780.3), 2000000 - vss_lo - 1000, dbu(800.3))

for p in fp["pads"]:
    lo, hi = dbu(p["term_lo"]), dbu(p["term_hi"])
    mid = (lo + hi) // 2
    px, py = dbu(p["pin_x"]), dbu(p["pin_y"])
    if p["side"] in ("bottom", "top"):
        edge = FREE_LO if p["side"] == "bottom" else FREE_HI
        sgn = 1 if p["side"] == "bottom" else -1
        jog = edge + sgn * 5000
        rect(M2, lo, edge - sgn * 500, hi, jog + sgn * 1250)
        rect(M2, min(lo, px - 1250), jog - 1250, max(hi, px + 1250), jog + 1250)
        rect(M2, px - 1250, jog, px + 1250, py + sgn * 1000)
    else:
        edge = FREE_LO if p["side"] == "left" else FREE_HI
        sgn = 1 if p["side"] == "left" else -1
        xv = edge + sgn * 4300
        rect(M2, edge - sgn * 500, lo, xv + sgn * 1250, hi)
        rect(M2, xv - 1250, min(lo, py - 1250), xv + 1250, max(hi, py + 1250))
        rect(M1, xv - sgn * 1250, py - 1200, px + sgn * 1000, py + 1200)
        rect(M1, xv - 1250, py - 1250, xv + 1250, py + 1250)
        wr.shapes(VIA1).insert(db.Box(xv - 750, py - 750, xv + 750, py + 750))
    name = p["core_port"].replace("[", "_").replace("]", "")
    if p["side"] in ("bottom", "top"):
        top.shapes(L2).insert(db.Text(name, db.Trans(px, py - sgn * 3000)))
    else:
        top.shapes(L1).insert(db.Text(name, db.Trans(px - sgn * 3000, py)))

opt = db.SaveLayoutOptions()
opt.format = "GDS2"
opt.write_context_info = False
opt.select_cell(top.cell_index())
out_gds = f"{top_cell}.gds"
ly.write(out_gds, opt)

logic = sum(lef_w[m] for m, *_ in insts)
budget = len(rows) * (block_hi - block_lo)
stats = dict(
    cells=len(insts), fillers=n_fill, routed_vias=len(vias),
    row_budget_um=budget / U, cell_width_um=logic / U, row_fill=round(logic / budget, 4),
    swapped=swapped,
)
Path("assemble.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
print(f"cells {len(insts)} fillers {n_fill} routed vias {len(vias)} row width {logic/U:.0f} of {budget/U:.0f} um, fill {100*logic/budget:.1f}%")

# ---------------------------------------------------------------- CDL generation
def subckts(path):
    text = open(path, errors="ignore").read().replace("\\r", "")
    res = {}
    for sm in re.finditer(r"^\\.subckt\\s+(\\S+)([^\\n]*)\\n(.*?)^\\.ends[^\\n]*\\n", text, re.I | re.M | re.S):
        res[sm.group(1)] = (sm.group(2).split(), sm.group(0))
    return res

core_subckts = subckts("core.cdl")
pad_subckts = subckts("io_pad.cdl")

v_text = open("routed.v").read()
v_text = re.sub(r"/\\*.*?\\*/", "", v_text, flags=re.S)
names = {}

def clean(n):
    n = n.strip().lstrip("\\\\").strip()
    if n not in names:
        s = re.sub(r"\\W", "_", n)
        s = s.strip("_") or "n"
        while s in names.values():
            s += "_x"
        names[n] = s
    return names[n]

body = v_text[v_text.index(";", v_text.index("module")) + 2:]
cdl_insts = []
for cm in re.finditer(r"^\\s*(\\w+)\\s+(\\\\\\S+\\s|\\w+)\\s*\\((.*?)\\)\\s*;", body, re.M | re.S):
    cell, inst, conns = cm.group(1), cm.group(2).strip(), cm.group(3)
    if cell in ("module", "input", "output", "wire"):
        continue
    con = {}
    for c in re.finditer(r"\\.(\\w+)\\s*\\(\\s*(\\\\?[^\\s()]*(?:\\s*\\[\\d+\\])?)\\s*\\)", conns):
        con[c.group(1)] = re.sub(r"\\s+", "", c.group(2))
    cdl_insts.append((cell, inst, con))

used_masters = sorted({c for c, _, _ in cdl_insts})
OUTPUT_PINS = {"OUT1", "Q", "QB", "OUTB"}
cdl_lines = [f"* Written by chipflow assembly stage for {top_cell}. PRIVATE.", ""]
ports = [f"PAD{p['pad']}" for p in fp["pads"]] + ["VDD", "VSS"]
cdl_lines.append(f".SUBCKT {top_cell} {' '.join(ports)}")

for k, (cell, inst, con) in enumerate(cdl_insts):
    pins, _ = core_subckts[cell]
    nets = []
    for pn in pins:
        if pn in ("VDD", "VSS"):
            nets.append(pn)
        elif pn in con and con[pn]:
            nets.append(clean(con[pn]))
        else:
            assert pn in OUTPUT_PINS, (cell, inst, pn, con)
            nets.append(f"nc_{k}_{pn}")
    cdl_lines.append(f"X{k} {' '.join(nets)} {cell}")

for p in fp["pads"]:
    pins, _ = pad_subckts[p["cell"]]
    outside, inside = ("PAD", "IN1") if p["dir"] == "out" else ("IN1", "OUT1")
    corenet = clean(p["core_port"])
    m_pins = {"VDD": "VDD", "VSS": "VSS", outside: f"PAD{p['pad']}", inside: corenet}
    cdl_lines.append(f"XPAD{p['pad']} {' '.join(m_pins[q] for q in pins)} {p['cell']}")

for k in (13, 15):
    cdl_lines.append(f"XP{k} VDD VSS PVDD01")
for k in (14, 16):
    cdl_lines.append(f"XP{k} VSS VDD PVSS01")

cdl_lines.append(f".ENDS {top_cell}\\n")
for c in used_masters:
    cdl_lines.append(core_subckts[c][1])
for c in sorted({p["cell"] for p in fp["pads"]} | {"PVDD01", "PVSS01"}):
    cdl_lines.append(pad_subckts[c][1])
for c in ("RNW", "RPP", "RNP", "RNDIFF", "RPDIFF"):
    if c in pad_subckts:
        cdl_lines.append(pad_subckts[c][1])

Path(f"{top_cell}.cdl").write_text("\\n".join(cdl_lines) + "\\n", encoding="utf-8")

# ---------------------------------------------------------------- Metal-only power connectivity
ly_check = db.Layout()
ly_check.read(out_gds)
top_check = ly_check.cell(top_cell)
l2n = db.LayoutToNetlist(db.RecursiveShapeIterator(ly_check, top_check, []))
m1_l = l2n.make_layer(ly_check.layer(8, 0), "m1")
via_l = l2n.make_layer(ly_check.layer(9, 0), "via")
m2_l = l2n.make_layer(ly_check.layer(10, 0), "m2")
l2n.connect(m1_l)
l2n.connect(via_l)
l2n.connect(m2_l)
l2n.connect(m1_l, via_l)
l2n.connect(via_l, m2_l)
l2n.extract_netlist()

def probe(region, px, py):
    return l2n.probe_net(region, db.DPoint(px, py))

blo, bhi = fp["block"]
vss_mid = sum(fp["vss_ring"]) / 2.0
vdd_mid = sum(fp["vdd_ring"]) / 2.0
pts = {"VSS": [], "VDD": []}
pts["VSS"] += [
    ("frame VSS ring", m1_l, 17.122, 1323.041),
    ("left PVSS01 pad", m2_l, 129.35, 1138.3),
    ("right PVSS01 pad", m2_l, 1870.65, 788.3),
    ("VSS ring left", m2_l, vss_mid, 1000.0),
    ("VSS ring right", m2_l, 2000.0 - vss_mid, 1000.0),
    ("VSS ring bottom", m1_l, 1000.0, vss_mid),
    ("VSS ring top", m1_l, 1000.0, 2000.0 - vss_mid),
]
pts["VDD"] += [
    ("frame VDD ring", m1_l, 235.034, 1323.041),
    ("left PVDD01 pad", m2_l, 129.35, 788.3),
    ("right PVDD01 pad", m2_l, 1870.65, 1138.3),
    ("VDD ring left", m2_l, vdd_mid, 1000.0),
    ("VDD ring right", m2_l, 2000.0 - vdd_mid, 1000.0),
    ("VDD ring bottom", m1_l, 1000.0, vdd_mid),
    ("VDD ring top", m1_l, 1000.0, 2000.0 - vdd_mid),
]
for row_i, ry in enumerate(fp["rows"]):
    for frac_name, frac in (("left", 0.001), ("middle", 0.5), ("right", 0.999)):
        rx = blo + (bhi - blo) * frac
        pts["VSS"].append((f"row {row_i} VSS rail {frac_name}", m1_l, rx, ry + 4.5))
        pts["VDD"].append((f"row {row_i} VDD rail {frac_name}", m1_l, rx, ry + 44.2))

pwr_res = {"checked": 0, "problems": []}
pwr_ids = {}
for sup, plist in pts.items():
    first_id = None
    for pname, pregion, px, py in plist:
        net_node = probe(pregion, px, py)
        pwr_res["checked"] += 1
        if net_node is None:
            pwr_res["problems"].append(f"{pname}: no metal at ({px:.1f}, {py:.1f})")
            continue
        if first_id is None:
            first_id = net_node.cluster_id
            pwr_ids[sup] = first_id
        elif net_node.cluster_id != first_id:
            pwr_res["problems"].append(f"{pname} isn't connected to the {sup} pad metal")

if pwr_ids.get("VSS") == pwr_ids.get("VDD") and "VSS" in pwr_ids:
    pwr_res["problems"].append("VDD and VSS are shorted in metal")

pwr_res["ok"] = not pwr_res["problems"]
Path("power_connect.json").write_text(json.dumps(pwr_res, indent=2), encoding="utf-8")
print(f"power by metal alone: {pwr_res['checked']} points checked, {len(pwr_res['problems'])} problems")
sys.exit(0 if pwr_res["ok"] else 1)
"""


def assembly_stage(
    project: ProjectConfig,
    profile: ProcessProfile,
    toolchain: Toolchain,
    run_dir: Path | str,
) -> StageSpec:
    run_path = Path(run_dir).resolve()
    top_cell = project.top_cell

    def prepare(stage_dir: Path) -> StageEnvelope:
        tech_stage = run_path / "stages/technology"
        pnr_stage_dir = run_path / "stages/pnr"

        # Copy verified dependencies
        shutil.copyfile(tech_stage / "work/frame.gds", stage_dir / "frame.gds")
        shutil.copyfile(tech_stage / "work/io_pad.gds", stage_dir / "io_pad.gds")
        shutil.copyfile(tech_stage / "work/core.gds", stage_dir / "core.gds")
        shutil.copyfile(tech_stage / "work/core.cdl", stage_dir / "core.cdl")
        shutil.copyfile(tech_stage / "work/io_pad.cdl", stage_dir / "io_pad.cdl")
        shutil.copyfile(tech_stage / "cells.lef", stage_dir / "cells.lef")
        shutil.copyfile(pnr_stage_dir / "floorplan.json", stage_dir / "floorplan.json")
        shutil.copyfile(pnr_stage_dir / "routed.def", stage_dir / "routed.def")
        shutil.copyfile(pnr_stage_dir / "routed.v", stage_dir / "routed.v")

        assembly_config = {
            "top_cell": top_cell,
            "frame_top_cell": profile.frame_top_cell,
        }
        (stage_dir / "assembly_config.json").write_text(json.dumps(assembly_config, indent=2), encoding="utf-8")
        (stage_dir / "assembly_runner.py").write_text(ASSEMBLY_RUNNER_SCRIPT, encoding="utf-8")

        config_hash = sha256_file(run_path / "resolved-project.json")
        envelope_inputs = [
            {"id": name, "path": name, "sha256": sha256_file(stage_dir / name), "bytes": (stage_dir / name).stat().st_size}
            for name in ("routed.def", "routed.v", "floorplan.json", "frame.gds", "core.gds", "core.cdl", "io_pad.gds", "io_pad.cdl", "cells.lef")
        ]
        return StageEnvelope(
            stage_id="assembly",
            adapter={"id": "chipflow.assembly", "version": "0.1.0"},
            inputs=tuple(envelope_inputs),
            tools=(),
            configuration_sha256=config_hash,
            seed=project.resources.seed,
            threads=project.resources.threads,
            outputs=tuple(
                {"id": name, "path": name, "validator": "assembly-v1"}
                for name in (f"{top_cell}.gds", f"{top_cell}.cdl", "assemble.json", "power_connect.json")
            ),
        )

    def assess(stage_dir: Path, result: CommandResult, artifacts: tuple[Artifact, ...]) -> Assessment:
        gds_file = stage_dir / f"{top_cell}.gds"
        cdl_file = stage_dir / f"{top_cell}.cdl"
        stats_file = stage_dir / "assemble.json"
        pwr_file = stage_dir / "power_connect.json"

        input_hashes = {
            "routed_def": sha256_file(stage_dir / "routed.def"),
            "routed_verilog": sha256_file(stage_dir / "routed.v"),
            "floorplan_json": sha256_file(stage_dir / "floorplan.json"),
            "frame_gds": sha256_file(stage_dir / "frame.gds"),
            "core_gds": sha256_file(stage_dir / "core.gds"),
        }

        if not result.succeeded and not (stats_file.is_file() and pwr_file.is_file()):
            return Assessment(failure_kind="infrastructure")

        stats = json.loads(stats_file.read_text(encoding="utf-8")) if stats_file.is_file() else {}
        pwr = json.loads(pwr_file.read_text(encoding="utf-8")) if pwr_file.is_file() else {}

        evidence = tuple(
            Artifact(str(f.relative_to(run_path)), sha256_file(f))
            for f in (gds_file, cdl_file, stats_file, pwr_file)
            if f.is_file()
        )
        evidence_artifacts = tuple(artifacts) if artifacts else evidence

        gds_ok = gds_file.is_file() and gds_file.stat().st_size > 0
        cdl_ok = cdl_file.is_file() and cdl_file.stat().st_size > 0
        assembly_ok = gds_ok and cdl_ok and stats.get("cells", 0) > 0

        pwr_ok = pwr.get("ok", False)
        checked_pts = pwr.get("checked", 0)
        pwr_problems = pwr.get("problems", [])

        checks = (
            CheckResult(
                "assembly",
                CheckStatus.PASS if assembly_ok else CheckStatus.FAIL,
                message=f"{stats.get('cells', 0)} cells, {stats.get('fillers', 0)} fillers, {len(stats.get('swapped', []))} pad swap(s)",
                evidence=evidence_artifacts,
                input_hashes=input_hashes,
            ),
            CheckResult(
                "power_connectivity",
                CheckStatus.PASS if pwr_ok else CheckStatus.FAIL,
                message=f"{checked_pts} points checked, {len(pwr_problems)} problems",
                evidence=evidence_artifacts,
                input_hashes=input_hashes,
            ),
        )
        if not (assembly_ok and pwr_ok):
            return Assessment(checks=checks, failure_kind="design", extra_artifacts=evidence_artifacts)
        return Assessment(checks=checks, extra_artifacts=evidence_artifacts)

    return StageSpec(
        id="assembly",
        command=(sys.executable, "-I", "assembly_runner.py"),
        dependencies=("technology", "pnr"),
        timeout_s=float(max(project.resources.timeout_seconds, 1200.0)),
        required_outputs=(f"{top_cell}.gds", f"{top_cell}.cdl", "assemble.json", "power_connect.json"),
        validators={name: _validate_nonempty for name in (f"{top_cell}.gds", f"{top_cell}.cdl", "assemble.json", "power_connect.json")},
        prepare=prepare,
        assess=assess,
    )
