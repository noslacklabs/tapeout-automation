#!/usr/bin/env python3
"""Prepare SCL working LEF/Liberty copies.  Run by the engine with sys.executable.

The helper is deliberately narrow: it only performs the transformations recorded
in the technology profile and emits a machine-readable summary.  All checks on
the result are made by the standard-library engine after this process exits.

Input is a JSON file (tech-input.json) with paths relative to the working
directory and the profile's geometry values.  Requires klayout.db.
"""
import json
import math
import re
import sys
from pathlib import Path

import klayout.db as db

MAX_FILE = 256 * 1024 * 1024

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
grid_um = float(config["grid_um"])
unit = 1000
grid = int(round(grid_um * unit))
site_width = int(round(float(config["site_width_um"]) * unit))
offsets = {name: int(round(float(value) * unit)) for name, value in config.get("core_gds_offsets_um", {}).items()}
layers = config["gds_layers"]
M1 = (int(layers["metal1"][0]), int(layers["metal1"][1]))
M2 = (int(layers["metal2"][0]), int(layers["metal2"][1]))
VIA = (int(layers["via1"][0]), int(layers["via1"][1]))
POLY = (4, 0)
CONT = (7, 0)
DIFF = (2, 0)


def read_text(path):
    data = Path(path).read_bytes()
    if len(data) > MAX_FILE:
        raise SystemExit("input too large: %s" % path)
    return data.decode("utf-8", errors="ignore").replace("\r", "")


def write_text(path, text):
    Path(path).write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- tech LEF
tech_text = read_text(config["tech_lef"])
tech_text, n = re.subn(r"(SITE\s+CORE\b.*?SIZE\s+)0\.01(\s+BY)", r"\g<1>4.2\2", tech_text, flags=re.S)
if n != 1:
    raise SystemExit("CORE site width pattern did not match exactly once")
tech_text, n = re.subn(r"(LAYER metal1\s.*?PITCH\s+)4\.0", r"\g<1>4.2", tech_text, flags=re.S)
if n != 1:
    raise SystemExit("metal1 pitch pattern did not match exactly once")
write_text(config["out_tech_lef"], tech_text)

# ---------------------------------------------------------------- cell GDS
lib = db.Layout()
lib.read(config["core_gds"])
if lib.dbu <= 0:
    raise SystemExit("core GDS has invalid DBU")
M1I, M2I, VIAI = lib.layer(*M1), lib.layer(*M2), lib.layer(*VIA)
POLYI, CONTI, DIFFI = lib.layer(*POLY), lib.layer(*CONT), lib.layer(*DIFF)

# ---------------------------------------------------------------- cell LEF first pass
lines = read_text(config["core_lef"]).split("\n")
pins = {}
pinrects = {}
obs = {}
signal_pins = {}
cur = sec = layer = None
for line in lines:
    p = line.split()
    if not p:
        continue
    k = p[0].upper()
    if k == "MACRO":
        cur = p[1]
        pins[cur], pinrects[cur], obs[cur] = [], {"metal1": [], "metal2": []}, {"metal1": [], "metal2": []}
        signal_pins[cur] = {}
        sec = None
    elif cur and k == "PIN":
        sec = p[1]
    elif cur and k == "OBS":
        sec = "#OBS"
    elif cur and k == "LAYER":
        layer = p[1]
    elif cur and sec and k == "RECT" and layer in ("metal1", "metal2"):
        b = db.Box(int(round(float(p[1]) * unit)), int(round(float(p[2]) * unit)),
                   int(round(float(p[3]) * unit)), int(round(float(p[4]) * unit)))
        if sec == "#OBS":
            obs[cur][layer].append(b)
        else:
            pinrects[cur][layer].append(b)
            if sec not in ("VDD", "VSS") and layer == "metal1":
                pins[cur].append(((b.left + b.right) // 2, (b.bottom + b.top) // 2))
                signal_pins[cur].setdefault(sec, []).append(b)


def rect_lines(region, layer):
    res = ["    LAYER %s ;" % layer]
    for poly in region.decompose_trapezoids_to_region().each():
        b = poly.bbox()
        x1 = math.floor(b.left / grid) * grid
        y1 = math.floor(b.bottom / grid) * grid
        x2 = math.ceil(b.right / grid) * grid
        y2 = math.ceil(b.top / grid) * grid
        res.append("      RECT %.3f %.3f %.3f %.3f ;" % (x1 / unit, y1 / unit, x2 / unit, y2 / unit))
    return res if len(res) > 1 else []


M2_PIN = {}


def pin_straps(name):
    if name in M2_PIN:
        return M2_PIN[name]
    res = {}
    cell = lib.cell(name)
    if cell is not None:
        m1 = db.Region(cell.begin_shapes_rec(M1I)).merged()
        m2 = db.Region(cell.begin_shapes_rec(M2I)).merged()
        vi = db.Region(cell.begin_shapes_rec(VIAI)).merged()
        for pin, rects in signal_pins[name].items():
            seed = db.Region()
            for b in rects:
                seed.insert(b)
            cur_m1 = m1.interacting(seed)
            cur_m2 = db.Region()
            for _ in range(4):
                v = vi.interacting(cur_m1)
                new_m2 = m2.interacting(v)
                new_m1 = m1.interacting(vi.interacting(new_m2)) + cur_m1
                if new_m2.count() == cur_m2.count() and new_m1.merged().count() == cur_m1.merged().count():
                    cur_m2 = new_m2
                    break
                cur_m2, cur_m1 = new_m2, new_m1.merged()
            if not cur_m2.is_empty():
                res[pin] = cur_m2.merged()
    M2_PIN[name] = res
    return res


def extra_obs(name):
    cell = lib.cell(name)
    if cell is None:
        return []
    res = []
    diff = db.Region(cell.begin_shapes_rec(DIFFI)).merged()
    region = (db.Region(cell.begin_shapes_rec(CONTI))
              + db.Region(cell.begin_shapes_rec(POLYI)).merged().sized(-300)
              + (diff.sized(10) - diff.sized(-10))).merged()
    holes = db.Region()
    for x, y in pins.get(name, []):
        holes.insert(db.Box(x - 2300, y - 2300, x + 2300, y + 2300))
    res += rect_lines((region - holes).merged(), "via1")
    real = db.Region(cell.begin_shapes_rec(M1I)).merged()
    shown = db.Region()
    for b in obs[name]["metal1"] + pinrects[name]["metal1"]:
        shown.insert(b)
    missing = (real - shown.merged().sized(30)).merged()
    if not missing.is_empty():
        res += rect_lines(missing, "metal1")
    m2 = db.Region(cell.begin_shapes_rec(M2I)).merged()
    for reg in pin_straps(name).values():
        m2 = m2 - reg
    res += rect_lines(m2.merged(), "metal2")
    return res


pat = re.compile(r"^(\s*RECT\s+)([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)(\s*;.*)$", re.I)
size_pat = re.compile(r"^(\s*SIZE\s+)([\d.]+)(\s+BY\s+[\d.]+\s*;.*)$", re.I)
res = []
cur = None
inobs = sawobs = False
fixed_sizes = []
pin = None
layer = None
for line in lines:
    p = line.split()
    k = p[0].upper() if p else ""
    if k == "LAYER":
        layer = p[1]
    if inobs and layer == "metal2" and (k == "RECT" or k == "LAYER"):
        continue
    g = pat.match(line)
    if g:
        v = [round(float(g.group(i)) * unit / grid) * grid / unit for i in (2, 3, 4, 5)]
        line = "%s%.3f %.3f %.3f %.3f%s" % (g.group(1), v[0], v[1], v[2], v[3], g.group(6))
    g = size_pat.match(line)
    if g and cur and not cur.startswith("FILLER"):
        w = int(round(float(g.group(2)) * unit))
        if w % site_width:
            neww = (w // site_width + 1) * site_width
            if neww - w < 1500:
                neww += site_width
            fixed_sizes.append("%s %.2f->%.2f" % (cur, w / unit, neww / unit))
            line = "%s%.3f%s" % (g.group(1), neww / unit, g.group(3))
    if k == "MACRO":
        cur = p[1]
        inobs = sawobs = False
        pin = None
    if cur and k == "PIN":
        pin = p[1]
    if cur and pin and k == "END" and len(p) == 1:
        strap = pin_straps(cur).get(pin)
        if strap is not None:
            res.extend(rect_lines(strap, "metal2"))
    if cur and pin and k == "END" and len(p) == 2 and p[1] == pin:
        pin = None
    if cur and k == "OBS":
        inobs = sawobs = True
        layer = None
    if cur and inobs and k == "END" and len(p) == 1:
        res.extend(extra_obs(cur))
        inobs = False
    if cur and k == "END" and len(p) >= 2 and p[1] == cur:
        if not sawobs:
            res.append("  OBS")
            res.extend(extra_obs(cur))
            res.append("  END")
        cur = None
    res.append(line)

shifted = []
cur = None
for line in res:
    p = line.split()
    if p and p[0].upper() == "MACRO":
        cur = p[1]
    g = pat.match(line)
    if g and cur in offsets:
        d = offsets[cur] / unit
        line = "%s%.3f %s %.3f %s%s" % (
            g.group(1), float(g.group(2)) + d, g.group(3),
            float(g.group(4)) + d, g.group(5), g.group(6),
        )
    shifted.append(line)
write_text(config["out_cells_lef"], "\n".join(shifted))

# ---------------------------------------------------------------- Liberty filter
def filter_liberty(src, dst, drop):
    text = read_text(src)
    keep = []
    pos = 0
    for m in re.finditer(r'\bcell\s*\(\s*"?(\w+)"?\s*\)\s*\{', text):
        if m.start() < pos:
            continue
        if m.group(1) in drop:
            depth = 1
            i = m.end()
            while depth and i < len(text):
                ch = text[i]
                depth += (ch == "{") - (ch == "}")
                i += 1
            if depth:
                raise SystemExit("unbalanced Liberty cell body: %s" % src)
            keep.append(text[pos:m.start()])
            pos = i
    keep.append(text[pos:])
    write_text(dst, "".join(keep))


drop = set(config.get("drop_cells", []))
force = set(config.get("force_cells", []))
missing = (drop | force) - {
    m.group(1) for m in re.finditer(r'\bcell\s*\(\s*"?(\w+)"?\s*\)\s*\{', read_text(config["core_typ_lib"]))
}
if missing:
    raise SystemExit("profile excludes cell(s) absent from the core Liberty: %s" % ", ".join(sorted(missing)))

for corner in ("typ", "min", "max"):
    filter_liberty(config["core_%s_lib" % corner], config["out_core_%s_lib" % corner], drop | force)
    # Pad Liberty is copied without filtering: pad availability is controlled by
    # the pad allowlist, not by the core synthesis DROP/FORCE policy.
    write_text(config["out_pads_%s_lib" % corner], read_text(config["pads_%s_lib" % corner]))

summary = {
    "schema_version": 1,
    "grid_um": grid_um,
    "site_width_um": site_width / unit,
    "fixed_widths": fixed_sizes,
    "core_gds_cells": len([cell for cell in lib.each_cell()]),
    "drop_cells": sorted(drop),
    "force_cells": sorted(force),
    "gds_offsets_um": {name: value / unit for name, value in offsets.items()},
}

# ---------------------------------------------------------------- frame and pad geometry
frame = db.Layout()
frame.read(config["frame_gds"])
if frame.dbu <= 0:
    raise SystemExit("frame GDS has invalid DBU")
top = frame.cell(config["frame_top_cell"])
if top is None:
    raise SystemExit("frame top cell not found: %s" % config["frame_top_cell"])
bbox = top.bbox()
summary["frame"] = {
    "dbu_um": frame.dbu,
    "top_cell": config["frame_top_cell"],
    "bbox_um": [bbox.left * frame.dbu, bbox.bottom * frame.dbu, bbox.right * frame.dbu, bbox.top * frame.dbu],
    "instances": [
        {
            "cell": frame.cell(inst.cell_index).name,
            "x_um": inst.dtrans.disp.x,
            "y_um": inst.dtrans.disp.y,
            "rotation": int(inst.dtrans.angle),
            "mirror": bool(inst.dtrans.is_mirror()),
        }
        for inst in top.each_inst()
    ],
}

io = db.Layout()
io.read(config["io_gds"])
if io.dbu <= 0:
    raise SystemExit("pad GDS has invalid DBU")
summary["pad_terminals_um"] = {}
for cell_name in config.get("pad_cells", []):
    cell = io.cell(cell_name)
    if cell is None:
        raise SystemExit("pad cell missing from pad GDS: %s" % cell_name)
    window = db.Region(db.Box(-5000, 0, 300000, 500)) & cell.bbox()
    region = db.Region(cell.begin_shapes_rec(io.layer(*M2))) & window
    boxes = sorted((p.bbox().left * io.dbu, p.bbox().right * io.dbu) for p in region.merged().each())
    if len(boxes) != 1:
        raise SystemExit("pad %s has %d inner metal2 terminals" % (cell_name, len(boxes)))
    summary["pad_terminals_um"][cell_name] = [boxes[0][0], boxes[0][1]]

write_text(config["out_summary"], json.dumps(summary, indent=1) + "\n")
print("tech prep: %d core GDS cells, %d filtered cells, %d width fixes" % (
    summary["core_gds_cells"], len(drop | force), len(fixed_sizes)))
