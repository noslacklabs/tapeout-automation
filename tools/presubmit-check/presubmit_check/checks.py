"""Checks 1 to 20. Every check only reads; nothing here writes to any input."""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import klayout.db as db

from .drcreport import ReportInvalid, parse_report
from .gdsraw import Library, read_gds
from .geom import (LISTING_LIMIT, Extents, View, bboxes, canonical_polygon, describe_pose, flat_instances,
                   label_signature, overflow_note, reachable_cells)
from .model import FAIL, MANUAL, NOTICE, PASS, WARN, Result, Source, box, plural, pt, um
from .refdata import References


@dataclass
class Inputs:
    gds: str
    die: int
    silicon: str
    drc_report: str | None = None
    md5_report: str | None = None
    silicon_block: str | None = None


@dataclass
class Context:
    inputs: Inputs
    config: dict
    refs: References
    data: bytes
    results: list[Result] = field(default_factory=list)
    lib: Library | None = None
    top: str | None = None
    extents: Extents | None = None
    view: View | None = None
    context_name: str = ""
    # Classification of library-named cells, filled in by classify().
    unmodified: set[str] = field(default_factory=set)
    edited: set[str] = field(default_factory=set)
    logo_ok: set[str] = field(default_factory=set)
    block_cells: set[str] = field(default_factory=set)
    block_flat: dict = field(default_factory=dict)
    pads: list = field(default_factory=list)  # (cell, Trans) of listed pads under the top
    unlisted_at_pad_positions: list = field(default_factory=list)

    def add(self, id_: str, status: str, title: str, evidence=None, source: str = "", assumption: str = "") -> None:
        self.results.append(Result(id_, status, title, list(evidence or []), source, assumption))

    @property
    def rules(self) -> dict:
        return self.config["rules"]

    @property
    def die_rule(self) -> dict:
        return self.rules["dies"][str(self.inputs.die)]

    @property
    def geometry_ready(self) -> bool:
        return self.lib is not None and not self.lib.fatal and self.top is not None

    @property
    def pad_ok(self) -> set[str]:
        return self.unmodified & self.refs.pad_names

    @property
    def std_ok(self) -> set[str]:
        return self.unmodified & self.refs.std_names

    @property
    def scl_cells(self) -> set[str]:
        return self.unmodified | self.logo_ok | self.block_cells

    def layer(self, name: str) -> tuple[int, int]:
        return tuple(self.rules["layers"][name])

    def not_evaluated(self, id_: str, title: str, source: str = "") -> None:
        reason = "not evaluated: the GDS could not be read or has no top cell, so this has not been shown to pass"
        self.add(id_, MANUAL, title, [reason], source)


# ---------------------------------------------------------------- procedure rules


def check_01_stream(ctx: Context) -> None:
    """1. The file is a valid GDSII stream."""
    lib = ctx.lib
    size = len(ctx.data)
    if lib.valid:
        ctx.add("C01.1", PASS, "File is a valid GDSII stream",
                [f"{size} bytes, GDSII version {lib.version}, library {lib.libname!r}, "
                 f"{plural(len(lib.cells), 'cell')}, ended cleanly at byte {lib.bytes_read}"],
                Source.STREAM)
    else:
        ctx.add("C01.1", FAIL, "File is not a valid GDSII stream", [str(i) for i in lib.issues], Source.STREAM)
    if lib.fatal or lib.dbu_m is None:
        return
    if lib.nm_per_dbu == 1:
        ctx.add("C01.2", PASS, "Database unit is 1 nm", [f"database unit {lib.dbu_m:g} m"], Source.STREAM)
    else:
        ctx.add("C01.2", WARN, "Database unit differs from the 1 nm used by the SCL frames",
                [f"database unit {lib.dbu_m:g} m ({float(lib.nm_per_dbu):g} nm)"], Source.STREAM)


def check_02_top_cell(ctx: Context) -> None:
    """2. One top cell named exactly as the silicon number; no $$$CONTEXT_INFO$$$."""
    silicon = ctx.inputs.silicon
    pattern = ctx.rules["silicon_pattern"]
    if re.fullmatch(pattern, silicon):
        ctx.add("C02.1", PASS, "Silicon number has the expected format",
                [f"--silicon {silicon!r} matches OS followed by 4 digits"], Source.TOPCELL)
    else:
        ctx.add("C02.1", FAIL, "Silicon number has the wrong format",
                [f"--silicon {silicon!r} is not OS followed by 4 digits, in that exact case"], Source.TOPCELL)
    if ctx.lib.fatal:
        for id_, title in (("C02.2", "Exactly one top cell"), ("C02.3", "Top cell name"),
                           ("C02.4", "No $$$CONTEXT_INFO$$$ cell")):
            ctx.not_evaluated(id_, title, Source.TOPCELL)
        return
    lib = ctx.lib
    tops = lib.top_cells(ignore=ctx.context_name)
    if len(tops) == 1:
        ctx.add("C02.2", PASS, "Exactly one top cell", [f"top cell {tops[0]!r}"], Source.TOPCELL)
    else:
        ctx.add("C02.2", FAIL, f"Expected one top cell, found {len(tops)}",
                [f"top cell {t!r}" for t in tops] or ["no cell qualifies as a top cell"], Source.TOPCELL)
    if tops == [silicon]:
        ctx.add("C02.3", PASS, "Top cell is named exactly as the silicon number", [f"{silicon!r}"], Source.TOPCELL)
    elif silicon in tops:
        ctx.add("C02.3", FAIL, "Silicon number is one of several top cells", [f"top cells: {tops}"], Source.TOPCELL)
    else:
        near = [t for t in tops if t.lower() == silicon.lower()]
        detail = f"wrong case: {near[0]!r} against {silicon!r}" if near else f"found {tops}, expected {silicon!r}"
        ctx.add("C02.3", FAIL, "Top cell is not named as the silicon number", [detail], Source.TOPCELL)
    if ctx.context_name in lib.cells:
        cell = lib.cells[ctx.context_name]
        ctx.add("C02.4", FAIL, f"File contains a {ctx.context_name} cell",
                [f"cell {ctx.context_name!r} at byte {cell.offset} with {plural(len(cell.refs), 'reference')}. "
                 "It is a second top cell that KLayout hides. Save with context information turned off"],
                Source.TOPCELL)
    else:
        ctx.add("C02.4", PASS, f"No {ctx.context_name} cell", ["searched the raw stream records"], Source.TOPCELL)
    if silicon in tops:
        ctx.top = silicon
    elif tops:
        ctx.top = max(tops, key=lambda t: _area(ctx.extents.geometry(t)))


def _area(bbox) -> float:
    return 0 if bbox is None else (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def check_03_extent(ctx: Context) -> None:
    """3. Bottom-left at exactly (0,0); top-right equal to the die size."""
    title = "Layout extent matches the die"
    if not ctx.geometry_ready:
        ctx.not_evaluated("C03.1", title, Source.WINDOW)
        return
    bbox = ctx.extents.geometry(ctx.top)
    size = ctx.die_rule["size_um"] * 1000
    if bbox is None:
        ctx.add("C03.1", FAIL, "Top cell has no geometry", [f"top cell {ctx.top!r}"], Source.WINDOW)
        return
    evidence = [f"top cell {ctx.top!r}: geometry extent {box(bbox)}", f"--die {ctx.inputs.die} expects (0.000, 0.000) to {pt(size, size)}"]
    if tuple(bbox) == (0, 0, size, size):
        ctx.add("C03.1", PASS, title, evidence, Source.WINDOW)
    else:
        if (bbox[0], bbox[1]) != (0, 0):
            evidence.append(f"bottom-left is {pt(bbox[0], bbox[1])}, not (0.000, 0.000)")
        if (bbox[2], bbox[3]) != (size, size):
            allowed = sorted(d["size_um"] * 1000 for d in ctx.rules["dies"].values())
            kind = "another accepted die size" if bbox[2] == bbox[3] and bbox[2] in allowed else "not an accepted die size"
            evidence.append(f"top-right is {pt(bbox[2], bbox[3])}, {kind}")
        ctx.add("C03.1", FAIL, "Layout extent does not match the die", evidence, Source.WINDOW)
    labels = ctx.extents.labels(ctx.top)
    assumption = ("A label outside the die fails because KLayout includes labels in a cell's extent, "
                  "so the window SCL sees could differ. SCL doesn't state how it measures the window.")
    if labels is not None and (labels[0] < 0 or labels[1] < 0 or labels[2] > size or labels[3] > size):
        ctx.add("C03.2", FAIL, "A label lies outside the die", [f"label origins span {box(labels)}"],
                Source.WINDOW, assumption)
    else:
        ctx.add("C03.2", PASS, "All labels lie inside the die",
                [f"label origins span {box(labels)}" if labels else "no labels"], Source.WINDOW, assumption)


def _off_grid(lib: Library, cell, grid: int) -> dict:
    found: dict = defaultdict(list)
    ok = lib.on_grid
    for shape in cell.shapes:
        for x, y in shape.pts:
            if not (ok(x, grid) and ok(y, grid)):
                found["vertex"].append((x, y))
    for path in cell.paths:
        for x, y in path.pts:
            if not (ok(x, grid) and ok(y, grid)):
                found["path point"].append((x, y))
        if path.width % 2 or not ok(path.width // 2, grid):
            found["path half-width"].append(path.pts[0])
    for node in cell.nodes:
        for x, y in node.pts:
            if not (ok(x, grid) and ok(y, grid)):
                found["node point"].append((x, y))
    for text in cell.texts:
        if not (ok(text.x, grid) and ok(text.y, grid)):
            found["label"].append((text.x, text.y))
    for ref in cell.refs:
        points = [(ref.x, ref.y)]
        if ref.is_array:
            points += [ref.col_pt, ref.row_pt]
        for x, y in points:
            if not (ok(x, grid) and ok(y, grid)):
                found["instance origin"].append((x, y))
    return found


def _describe_off_grid(lib: Library, name: str, found: dict) -> str:
    counts = ", ".join(plural(len(v), k) for k, v in found.items())
    first_kind = next(iter(found))
    x, y = found[first_kind][0]
    return f"{name}: {counts}; first at {pt(lib.nm(x), lib.nm(y))} in cell coordinates"


def _split_frame_elements(ctx: Context, cell, found: dict) -> tuple[dict, dict]:
    """Separate off-grid elements of a non-library cell that are the reference frame's own."""
    refs, lib = ctx.refs, ctx.lib
    nm = lib.nm
    user: dict = {k: list(v) for k, v in found.items()}
    frame: dict = defaultdict(list)
    if "label" in user:
        labels = {(nm(t.x), nm(t.y)): t for t in cell.texts}
        kept = []
        for x, y in user["label"]:
            text = labels[(nm(x), nm(y))]
            if (text.layer, text.texttype, text.string, nm(x), nm(y)) in refs.frame_labels:
                frame["label"].append((x, y))
            else:
                kept.append((x, y))
        user["label"] = kept
    if "vertex" in user:
        frame_vertices = set()
        for shape in cell.shapes:
            points = [(nm(x), nm(y)) for x, y in shape.pts]
            if ((shape.layer, shape.datatype), canonical_polygon(points)) in refs.frame_shape_keys:
                frame_vertices.update(shape.pts)
        kept = []
        for point in user["vertex"]:
            (frame["vertex"] if point in frame_vertices else kept).append(point)
        user["vertex"] = kept
    return {k: v for k, v in user.items() if v}, dict(frame)


def check_04_grid(ctx: Context) -> None:
    """4. Every coordinate is an integer multiple of the grid."""
    titles = {"C04.1": "User cells are on the 0.005 um grid", "C04.2": "SCL pad cells on grid",
              "C04.3": "SCL standard cells on grid", "C04.4": "SCL frame elements and logo on grid",
              "C04.5": "Instance transforms keep the grid"}
    if ctx.lib.fatal or ctx.view is None:
        for id_, title in titles.items():
            ctx.not_evaluated(id_, title, Source.GRID)
        return
    lib = ctx.lib
    grid = ctx.rules["grid_nm"]
    groups: dict[str, list[str]] = {"user": [], "pad": [], "std": [], "frame": []}
    totals = Counter()
    risky: list[str] = []
    cells_seen = Counter()
    for cell in lib.cells.values():
        if cell.name == ctx.context_name:
            continue
        found = _off_grid(lib, cell, grid)
        if cell.name in ctx.pad_ok:
            bucket = "pad"
        elif cell.name in ctx.std_ok:
            bucket = "std"
        elif cell.name in ctx.logo_ok:
            bucket = "frame"
        else:
            bucket = "user"
        cells_seen[bucket] += 1
        if bucket == "user":
            for ref in cell.refs:
                if ref.mag != 1.0 or abs(ref.angle / 90.0 - round(ref.angle / 90.0)) > 1e-9 or ref.absolute:
                    flags = ", absolute transform flag set" if ref.absolute else ""
                    risky.append(f"{cell.name}: instance of {ref.name} at {pt(lib.nm(ref.x), lib.nm(ref.y))} "
                                 f"has rotation {ref.angle:g}, magnification {ref.mag:g}{flags}")
        if not found:
            continue
        if bucket == "user":
            found, frame = _split_frame_elements(ctx, cell, found)
            if frame:
                groups["frame"].append(_describe_off_grid(lib, f"{cell.name} (matches the reference frame)", frame))
                totals["frame"] += sum(len(v) for v in frame.values())
            if not found:
                continue
        groups[bucket].append(_describe_off_grid(lib, cell.name, found))
        totals[bucket] += sum(len(v) for v in found.values())

    edited_note = [f"{n} carries a library name but its geometry differs (see C11.1), so it counts as a user cell"
                   for n in sorted(ctx.edited)]
    if groups["user"]:
        ctx.add("C04.1", FAIL, f"{plural(totals['user'], 'off-grid coordinate')} in user cells",
                groups["user"] + edited_note, Source.GRID)
    else:
        ctx.add("C04.1", PASS, titles["C04.1"], [f"{plural(cells_seen['user'], 'cell')} outside the SCL libraries checked, 0 off-grid coordinates of the designer's"] + edited_note, Source.GRID)
    question = "SCL ships these off-grid and they can't be edited. Open question for the helpdesk: are they accepted as they are?"
    for id_, bucket, word in (("C04.2", "pad", "pad"), ("C04.3", "std", "standard")):
        if groups[bucket]:
            ctx.add(id_, WARN, f"{plural(totals[bucket], 'off-grid coordinate')} in unmodified SCL {word} cells",
                    [question] + groups[bucket], Source.GRID_Q)
        else:
            ctx.add(id_, PASS, titles[id_], [f"{plural(cells_seen[bucket], 'unmodified cell')}, 0 off-grid coordinates"], Source.GRID)
    if groups["frame"]:
        ctx.add("C04.4", WARN, f"{plural(totals['frame'], 'off-grid coordinate')} from SCL's reference frame",
                [question] + groups["frame"], Source.GRID_Q,
                "Elements identical to the reference frame are treated as SCL's rather than the designer's.")
    else:
        ctx.add("C04.4", PASS, titles["C04.4"], ["0 off-grid coordinates"], Source.GRID)
    if risky:
        ctx.add("C04.5", WARN, "Instances with non-right-angle rotation or magnification can flatten off grid",
                risky, Source.GRID)
    else:
        ctx.add("C04.5", PASS, titles["C04.5"], ["all instances in user cells use right-angle rotation and magnification 1"], Source.GRID)


def check_05_filename(ctx: Context) -> None:
    """5. <SiliconNo>_<DesignName>_<ddmmyyyy>_<Version>.gds."""
    name = os.path.basename(ctx.inputs.gds)
    silicon = ctx.inputs.silicon
    template = "<SiliconNo>_<DesignName>_<ddmmyyyy>_<Version>.gds"
    if not name.endswith(".gds"):
        ctx.add("C05.1", FAIL, "File name must end in .gds", [name, f"expected {template}"], Source.NAMING)
        return
    parts = name[: -len(".gds")].split("_")
    if len(parts) < 4 or not all(parts):
        ctx.add("C05.1", FAIL, "File name doesn't follow the naming rule", [name, f"expected {template}"], Source.NAMING)
        if parts[0] != silicon:
            ctx.add("C05.3", FAIL, "File name disagrees with --silicon", [f"file starts {parts[0]!r}, --silicon is {silicon!r}"], Source.NAMING)
        return
    prefix, design, date, version = parts[0], "_".join(parts[1:-2]), parts[-2], parts[-1]
    assumption = ""
    if len(parts) > 4:
        assumption = "A design name may contain underscores: the last two fields are read as date and version."
    ctx.add("C05.1", PASS, "File name follows the naming rule",
            [f"silicon {prefix!r}, design {design!r}, date {date!r}, version {version!r}"], Source.NAMING, assumption)
    try:
        if not re.fullmatch("[0-9]{8}", date):
            raise ValueError("not 8 digits")
        real = datetime.date(int(date[4:]), int(date[2:4]), int(date[:2]))
        ctx.add("C05.2", PASS, "Date is a real calendar date", [f"{date} is {real.isoformat()}"], Source.NAMING)
    except ValueError as exc:
        ctx.add("C05.2", FAIL, "Date is not a real ddmmyyyy date", [f"{date!r}: {exc}"], Source.NAMING)
    if prefix == silicon:
        ctx.add("C05.3", PASS, "File name agrees with --silicon", [prefix], Source.NAMING)
    else:
        ctx.add("C05.3", FAIL, "File name disagrees with --silicon", [f"file says {prefix!r}, --silicon is {silicon!r}"], Source.NAMING)
    assumption = "SCL doesn't define the version format. The only example in [Procedure] is v0."
    if re.fullmatch(ctx.rules["version_pattern"], version):
        ctx.add("C05.4", PASS, "Version looks like v0", [version], Source.NAMING, assumption)
    else:
        ctx.add("C05.4", WARN, "Version isn't like v0", [f"{version!r}: the format is not defined, so confirm it with SCL"], Source.NAMING, assumption)


def check_06_drc_name(ctx: Context) -> None:
    """6. The DRC report carries the layout's name with _drc.txt."""
    report = ctx.inputs.drc_report
    stem = os.path.basename(ctx.inputs.gds)
    stem = stem[:-4] if stem.endswith(".gds") else stem
    expected = f"{stem}_drc.txt"
    if report is None:
        ctx.add("C06.1", MANUAL, "DRC report name not checked", [f"no --drc-report given; it must be named {expected}"], Source.DRC_NAME)
    elif os.path.basename(report) == expected:
        ctx.add("C06.1", PASS, "DRC report name matches the layout", [expected], Source.DRC_NAME)
    else:
        ctx.add("C06.1", FAIL, "DRC report name doesn't match the layout", [f"found {os.path.basename(report)}, expected {expected}"], Source.DRC_NAME)


MD5_LINE = re.compile(r"^\\?([0-9a-fA-F]{32})(?:\s[ *]?(.+))?$")


def check_07_md5(ctx: Context) -> None:
    """7. Compute the MD5 and compare it with --md5-report."""
    digest = hashlib.md5(ctx.data, usedforsecurity=False).hexdigest()
    name = os.path.basename(ctx.inputs.gds)
    line = f"{digest}  {name}"
    ctx.add("C07.1", PASS, "MD5 computed", [line, "same format as md5sum; run md5sum on the final file after any rename"], Source.MD5)
    report = ctx.inputs.md5_report
    if report is None:
        ctx.add("C07.2", MANUAL, "MD5 report not compared", ["no --md5-report given"], Source.MD5)
        return
    try:
        with open(report, "rb") as handle:
            text = handle.read(1 << 20).decode("latin-1").lstrip("﻿ï»¿")
    except OSError as exc:
        ctx.add("C07.2", FAIL, "MD5 report can't be read", [str(exc)], Source.MD5)
        return
    entries = []
    for raw in text.splitlines():
        raw = raw.strip()
        if raw:
            match = MD5_LINE.match(raw)
            if match:
                entries.append((match.group(1), match.group(2)))
    if not entries:
        ctx.add("C07.2", FAIL, "MD5 report holds no MD5 value", [f"{report} isn't in md5sum format"], Source.MD5)
        return
    named = [e for e in entries if e[1] and os.path.basename(e[1].strip()) == name]
    if len(entries) > 1 and not named:
        ctx.add("C07.2", FAIL, "MD5 report has no line for this file", [f"{len(entries)} lines, none for {name}"], Source.MD5)
        return
    value, listed = (named or entries)[0]
    evidence = [f"report: {value}  {listed or '(no file name)'}", f"computed: {line}"]
    if value.lower() != digest:
        ctx.add("C07.2", FAIL, "MD5 report doesn't match this file", evidence, Source.MD5)
    elif value != digest:
        ctx.add("C07.2", WARN, "MD5 matches, but is in upper case unlike md5sum output", evidence, Source.MD5)
    elif listed is not None and os.path.basename(listed.strip()) != name:
        ctx.add("C07.2", WARN, "MD5 matches, but the report names a different file", evidence, Source.MD5)
    else:
        ctx.add("C07.2", PASS, "MD5 report matches this file", evidence, Source.MD5)


# ---------------------------------------------------------------- classification


def build_view(ctx: Context) -> None:
    """Build the in-memory layout and sort library-named cells into edited and not."""
    lib, refs = ctx.lib, ctx.refs
    if lib.fatal:
        return
    ctx.view = View(lib, refs.frame_shape_keys, {ctx.context_name}, refs.library_names)
    for name in lib.cells:
        if name in refs.library_names:
            if name in refs.ref_hash and ctx.view.cell_hash(name) == refs.ref_hash[name]:
                ctx.unmodified.add(name)
            else:
                ctx.edited.add(name)
    logo = refs.cells_cfg["logo"]
    if logo in lib.cells and ctx.view.cell_hash(logo) in refs.logo_hashes:
        ctx.logo_ok.add(logo)


def collect_pads(ctx: Context) -> None:
    refs = ctx.refs
    keys = {pose for cell, pose in refs.frame.instances if cell in refs.pad_names}
    stop = refs.library_names | ctx.logo_ok | ctx.block_cells
    for name, trans in flat_instances(ctx.lib, ctx.top, stop):
        if name in refs.pad_names:
            ctx.pads.append((name, trans))
        elif name not in refs.std_names and trans.pose() in keys and name not in stop:
            ctx.unlisted_at_pad_positions.append((name, trans))


# ---------------------------------------------------------------- pad frame


def check_08_pad_count(ctx: Context) -> None:
    """8. Pad, VDD and VSS counts match the die."""
    if not ctx.geometry_ready:
        for id_, title in (("C08.1", "Pad count"), ("C08.2", "VDD pad count"), ("C08.3", "VSS pad count")):
            ctx.not_evaluated(id_, title, Source.PADS)
        return
    rule = ctx.die_rule
    cfg = ctx.refs.cells_cfg
    counts = Counter(n for n, _ in ctx.pads)
    die = ctx.inputs.die
    expected = [
        ("C08.1", "Pad count", len(ctx.pads), rule["pads"], "listed pad instances, flattened under the top cell"),
        ("C08.2", f"{cfg['vdd']} (VDD) count", counts[cfg["vdd"]], rule["vdd"], f"{cfg['vdd']} instances"),
        ("C08.3", f"{cfg['vss']} (VSS) count", counts[cfg["vss"]], rule["vss"], f"{cfg['vss']} instances"),
    ]
    for id_, title, got, want, what in expected:
        evidence = [f"{got} {what}; the {die} mm die needs {want}"]
        if id_ == "C08.1":
            evidence.append("by type: " + (", ".join(f"{n} {c}" for n, c in sorted(counts.items())) or "none"))
        ctx.add(id_, PASS if got == want else FAIL, f"{title}: {got}, expected {want}", evidence, Source.PADS)


def check_09_power(ctx: Context) -> None:
    """9. PVDD01 and PVSS01 sit exactly where the reference frame puts them."""
    refs = ctx.refs
    cfg = refs.cells_cfg
    frame = refs.frame
    if not ctx.geometry_ready:
        ctx.not_evaluated("C09.1", "VDD pad positions", Source.POWER)
        ctx.not_evaluated("C09.2", "VSS pad positions", Source.POWER)
    else:
        for id_, cell in (("C09.1", cfg["vdd"]), ("C09.2", cfg["vss"])):
            want = Counter(pose for n, pose in frame.instances if n == cell)
            got = Counter(t.pose() for n, t in ctx.pads if n == cell)
            evidence = []
            for pose in sorted((want - got).elements()):
                evidence.append(f"missing: {cell} expected at {describe_pose(pose)}")
            for pose in sorted((got - want).elements()):
                evidence.append(f"unexpected: {cell} at {describe_pose(pose)}")
            title = f"{cell} positions and orientations"
            if evidence:
                ctx.add(id_, FAIL, f"{title} differ from the reference frame", evidence, Source.POWER)
            else:
                ctx.add(id_, PASS, f"{title} match the reference frame",
                        [f"{cell} at {describe_pose(p)}" for p in sorted(want.elements())], Source.POWER)
    evidence = [f"used set {refs.frame_set!r}: {frame.description}", f"reference file: {frame.path}"]
    power = {cfg["vdd"], cfg["vss"]}
    for name, other in refs.frames.items():
        if name == refs.frame_set:
            continue
        same = sorted(i for i in other.instances if i[0] in power) == sorted(i for i in frame.instances if i[0] in power)
        evidence.append(f"other set {name!r}: power pad positions are {'identical' if same else 'DIFFERENT'} for this die")
    if ctx.lib is not None and not ctx.lib.fatal and ctx.view is not None:
        if frame.top in ctx.lib.cells:
            digest = ctx.view.cell_hash(frame.top)
            matches = [n for n, h in refs.frame_cell_hashes.items() if h == digest]
            evidence.append(f"cell {frame.top} in this file matches set: {', '.join(matches) or 'neither'}")
        logo = cfg["logo"]
        if logo in ctx.lib.cells:
            origin = refs.logo_hashes.get(ctx.view.cell_hash(logo))
            evidence.append(f"cell {logo} in this file matches: {origin or 'no variant shipped in the kit'}")
    ctx.add("C09.3", WARN, "SCL hasn't confirmed which pad frame set is correct", evidence, Source.FRAMESET)


def _is_window(polygon: db.Polygon, size: int) -> bool:
    bb = polygon.bbox()
    return polygon.is_box() and bb.width() == size and bb.height() == size


def check_10_signal_pads(ctx: Context) -> None:
    """10. Signal pads come from the list, changes are reported, and windows are exact."""
    if not ctx.geometry_ready:
        for id_, title in (("C10.1", "Pads only from io_pad_list.txt"), ("C10.2", "Signal pad position changes"),
                           ("C10.3", "Pad windows"), ("C10.4", "Other shapes on the pad layer")):
            ctx.not_evaluated(id_, title, Source.SIGNAL)
        return
    refs, lib, view = ctx.refs, ctx.lib, ctx.view
    cfg = refs.cells_cfg
    power = {cfg["vdd"], cfg["vss"]}
    size = round(ctx.rules["pad_window_um"] * 1000)
    pad_layer = ctx.layer("pad")
    nm = lib.nm

    # C10.1: anything acting as a pad must be a listed cell.
    bad = [f"cell {n!r} sits at a reference pad position {t.describe()} but isn't in io_pad_list.txt"
           for n, t in ctx.unlisted_at_pad_positions]
    exempt = refs.pad_names | ctx.logo_ok | ctx.block_cells
    for name in sorted(reachable_cells(lib, ctx.top) - exempt):
        cell = lib.cells.get(name)
        if cell is None or name == ctx.context_name:
            continue
        for shape in cell.shapes:
            if (shape.layer, shape.datatype) != pad_layer:
                continue
            xs = [nm(p[0]) for p in shape.pts]
            ys = [nm(p[1]) for p in shape.pts]
            if len(shape.pts) == 4 and max(xs) - min(xs) == size and max(ys) - min(ys) == size:
                bad.append(f"cell {name!r} has a pad window at {box((min(xs), min(ys), max(xs), max(ys)))} "
                           "in cell coordinates, but isn't in io_pad_list.txt")
    if bad:
        ctx.add("C10.1", FAIL, "Pad-like cells outside io_pad_list.txt", bad, Source.SIGNAL)
    else:
        kinds = sorted({n for n, _ in ctx.pads})
        ctx.add("C10.1", PASS, "All pads come from io_pad_list.txt", [f"types used: {', '.join(kinds) or 'none'}"], Source.SIGNAL)

    # C10.2: compare signal pads with the reference frame.
    reference = {pose: n for n, pose in refs.frame.instances if n in refs.pad_names and n not in power}
    at_pose: dict = defaultdict(list)
    for name, trans in ctx.pads:
        at_pose[trans.pose()].append(name)
    changes, unchanged = [], 0
    for pose, name in sorted(reference.items()):
        found = at_pose.get(pose)
        if not found:
            changes.append(f"missing: reference {name} at {describe_pose(pose)} has no pad there")
        elif name in found:
            unchanged += 1
        else:
            changes.append(f"type changed: {name} replaced by {', '.join(found)} at {describe_pose(pose)}. Update the .cdl to match")
    for name, trans in ctx.pads:
        if name not in power and trans.pose() not in reference:
            changes.append(f"moved or added: {name} at {trans.describe()} is not a reference pad position")
    note = f"{unchanged} of {len(reference)} reference signal positions unchanged"
    if changes:
        ctx.add("C10.2", WARN, f"{plural(len(changes), 'signal pad change')} from the reference frame", [note] + changes, Source.SIGNAL,
                "Type swaps are reported, not failed, because [Pad doc] step 4 permits them.")
    else:
        ctx.add("C10.2", PASS, "Signal pads match the reference frame", [note], Source.SIGNAL)

    # C10.3: every listed pad has an exact window.
    window_cache: dict = {}
    problems, good = [], []
    for name, trans in sorted(ctx.pads, key=lambda p: (p[0], p[1].pose())):
        if name not in window_cache:
            window_cache[name] = list(view.region(name, pad_layer).merged().each())
        polygons = window_cache[name]
        exact = [p for p in polygons if _is_window(p, size)]
        if not exact or len(exact) != len(polygons):
            detail = ", ".join(box((p.bbox().left, p.bbox().bottom, p.bbox().right, p.bbox().top)) for p in polygons) or "none"
            problems.append(f"{name} at {trans.describe()}: needs a {um(size)} x {um(size)} window on layer {pad_layer[0]}; found {detail} in cell coordinates")
            continue
        for polygon in exact:
            bb = polygon.bbox()
            corners = [trans.apply(x, y) for x in (bb.left, bb.right) for y in (bb.bottom, bb.top)]
            xs, ys = zip(*corners)
            good.append(f"{name}: window {box((min(xs), min(ys), max(xs), max(ys)))}")
    if problems:
        ctx.add("C10.3", FAIL, "Pads without an exact window", problems, Source.WINDOW90)
    elif not ctx.pads:
        ctx.add("C10.3", MANUAL, "No pads found, so no windows checked", [], Source.WINDOW90)
    else:
        ctx.add("C10.3", PASS, f"Every pad has a {um(size)} x {um(size)} window", good, Source.WINDOW90)

    # C10.4: any other shape on the pad layer.
    others = view.region(ctx.top, pad_layer, exclude=ctx.scl_cells)
    if pad_layer in ctx.block_flat:
        others = others - ctx.block_flat[pad_layer]
    merged_others = others.merged()
    stray = [b for b in bboxes(merged_others) if not (b[2] - b[0] == size and b[3] - b[1] == size)]
    if stray:
        ctx.add("C10.4", WARN, "Shapes on the pad layer outside listed pads, the logo or a declared block",
                [f"opening at {box(b)}" for b in stray] + overflow_note(merged_others)
                + ["if this is the silicon number block, declare it with --silicon-block"],
                Source.WINDOW90)
    else:
        ctx.add("C10.4", PASS, "No stray shapes on the pad layer", ["checked flat, excluding listed pads, the logo and a declared block"], Source.WINDOW90)


# ---------------------------------------------------------------- library integrity


def check_11_library(ctx: Context) -> None:
    """11. Library-named cells must match the reference geometry."""
    refs = ctx.refs
    cfg = refs.cells_cfg
    sizes = (f"reference names: {len(refs.std_names)} standard cells from core_c1d.lef, {len(refs.pad_names)} pads from io_pad_list.txt")
    if (len(refs.std_names), len(refs.pad_names)) != (cfg["expected_std_cells"], cfg["expected_pads"]):
        ctx.add("C11.3", WARN, "Library lists differ in size from the documented counts",
                [sizes, f"expected {cfg['expected_std_cells']} and {cfg['expected_pads']}"], Source.LIB)
    if refs.missing_reference:
        ctx.add("C11.4", WARN, "Listed cells missing from the reference GDS",
                [f"can't verify: {', '.join(sorted(refs.missing_reference))}"], Source.LIB)
    if ctx.lib.fatal or ctx.view is None:
        ctx.not_evaluated("C11.1", "Library cells unedited", Source.LIB)
        ctx.not_evaluated("C11.2", "Library cell labels", Source.LIB)
        return
    lib, view = ctx.lib, ctx.view
    assumption = "Compared on merged polygons per layer, so how an area is split into shapes doesn't count as an edit."
    if ctx.edited:
        evidence = []
        for name in sorted(ctx.edited):
            if name not in refs.ref_hash:
                evidence.append(f"{name}: no reference geometry to compare with")
                continue
            source_lib = refs.pad_lib if name in refs.pad_names else refs.core_lib
            reference = refs.view_of(source_lib).merged_layers(name)
            design = view.merged_layers(name)
            for key in sorted(set(reference) | set(design)):
                left = reference.get(key, db.Region())
                right = design.get(key, db.Region())
                difference = left ^ right
                if difference.is_empty():
                    continue
                bb = difference.bbox()
                evidence.append(f"{name}: layer {key[0]}/{key[1]} differs by {difference.area() / 1e6:.3f} um^2, "
                                f"within {box((bb.left, bb.bottom, bb.right, bb.top))} in cell coordinates")
        ctx.add("C11.1", FAIL, f"{plural(len(ctx.edited), 'edited library cell')}", evidence, Source.LIB, assumption)
    else:
        ctx.add("C11.1", PASS, "Library-named cells match the reference geometry",
                [sizes, f"{plural(len(ctx.unmodified), 'library-named cell')} in this file, all identical to the reference"],
                Source.LIB, assumption)
    differs = []
    for name in sorted(ctx.unmodified):
        signature = label_signature(lib, lib.cells[name])
        if signature not in refs.ref_label_sets.get(name, set()):
            differs.append(f"{name}: {len(signature)} labels; none of the reference copies has this label set")
    if differs:
        ctx.add("C11.2", WARN, "Labels in unmodified library cells differ from the reference", differs, Source.LIB,
                "Labels aren't on a mask layer, so a difference warns rather than fails.")
    else:
        ctx.add("C11.2", PASS, "Library cell labels match a reference copy",
                [f"{plural(len(ctx.unmodified), 'cell')} compared with the pad library, the standard cell library and the chosen frame"], Source.LIB)


def check_12_renamed(ctx: Context) -> None:
    """12. Cells that look like renamed copies of library cells."""
    title = "No renamed copies of library cells"
    if ctx.lib.fatal or ctx.view is None:
        ctx.not_evaluated("C12.1", title, Source.LIB)
        return
    refs, lib, view = ctx.refs, ctx.lib, ctx.view
    sizes = set()
    for source in (refs.pad_lib, refs.core_lib):
        extents = Extents(source)
        for name in refs.library_names:
            bb = extents.geometry(name) if name in source.cells else None
            if bb is not None:
                sizes.add((bb[2] - bb[0], bb[3] - bb[1]))
    found = []
    checked = 0
    for name in lib.cells:
        if name in refs.library_names or name == ctx.context_name or name in ctx.logo_ok:
            continue
        bb = ctx.extents.geometry(name)
        if bb is None or (bb[2] - bb[0], bb[3] - bb[1]) not in sizes:
            continue
        checked += 1
        matches = refs.ref_norm_hash.get(view.cell_hash(name, normalise=True))
        if matches:
            found.append(f"{name} at {box(bb)} has the same geometry as library cell {', '.join(sorted(matches))}")
    assumption = "Matches translated copies only. A rotated, mirrored or edited copy under another name isn't detected."
    if found:
        ctx.add("C12.1", WARN, f"{plural(len(found), 'possible renamed library cell')}", found, Source.LIB, assumption)
    else:
        ctx.add("C12.1", PASS, title, [f"{plural(checked, 'cell')} with a library cell's outline compared; no matches"], Source.LIB, assumption)


# ---------------------------------------------------------------- layers


def check_13_layers(ctx: Context) -> None:
    """13. Only layer/datatype pairs from the table."""
    title = "All layers are in the layer table"
    if ctx.lib.fatal:
        ctx.not_evaluated("C13.1", title, Source.LAYERS)
        return
    lib = ctx.lib
    allowed = {tuple(pair) for pair in ctx.rules["allowed_layers"]["layers"]}
    seen: dict = {}
    nm = lib.nm

    def note(key, cell, x, y, kind):
        entry = seen.setdefault(key, [0, cell, x, y, kind])
        entry[0] += 1

    for cell in lib.cells.values():
        if cell.name == ctx.context_name:
            continue
        for shape in cell.shapes:
            note((shape.layer, shape.datatype), cell.name, shape.pts[0][0], shape.pts[0][1], shape.kind)
        for path in cell.paths:
            note((path.layer, path.datatype), cell.name, path.pts[0][0], path.pts[0][1], "path")
        for text in cell.texts:
            note((text.layer, text.texttype), cell.name, text.x, text.y, "label")
        for node in cell.nodes:
            note((node.layer, node.nodetype), cell.name, node.pts[0][0], node.pts[0][1], "node")
    used = sorted(seen)
    unknown = [k for k in used if k not in allowed]
    if unknown:
        evidence = [f"{k[0]}/{k[1]}: {plural(seen[k][0], 'element')}, first a {seen[k][4]} in {seen[k][1]!r} at "
                    f"{pt(nm(seen[k][2]), nm(seen[k][3]))}" for k in unknown]
        ctx.add("C13.1", FAIL, f"{plural(len(unknown), 'layer')} not in the layer table", evidence, Source.LAYERS)
    else:
        ctx.add("C13.1", PASS, title, ["used: " + ", ".join(f"{k[0]}/{k[1]}" for k in used)], Source.LAYERS,
                "Marker layers the table lists without a datatype are taken as datatype 0, as the deck reads them.")


def check_14_ignore_layer(ctx: Context) -> None:
    """14. Layer 999:0 hides shapes from DRC."""
    if not ctx.geometry_ready:
        for id_, title in (("C14.1", "Layer 999 polygon limit"), ("C14.2", "Layer 999 outside the logo or a block"),
                           ("C14.3", "User shapes hidden under layer 999")):
            ctx.not_evaluated(id_, title, Source.IGNORE)
        return
    lib, view, top = ctx.lib, ctx.view, ctx.top
    key = ctx.layer("ignore_drc")
    limit = ctx.rules["ignore_drc_max_polygons"]
    total = view.all_shapes(top, key).count()
    direct = sum(1 for s in lib.cells[top].shapes if (s.layer, s.datatype) == key)
    direct += sum(1 for p in lib.cells[top].paths if (p.layer, p.datatype) == key)
    evidence = [f"{plural(total, 'polygon')} on layer {key[0]}:{key[1]} flattened under {top!r}, {direct} drawn directly in it; the deck's limit is {limit}"]
    assumption = ("The deck's message says 'top cell', but its count comes from a flattened region, so instances count "
                  "(my reading of the deck; the deck hasn't been run).")
    if total > limit:
        ctx.add("C14.1", FAIL, f"Layer {key[0]}:{key[1]} exceeds the deck's limit, which stops DRC", evidence, Source.IGNORE, assumption)
    else:
        ctx.add("C14.1", PASS, f"Layer {key[0]}:{key[1]} is within the deck's limit", evidence, Source.IGNORE, assumption)

    exempt_cells = ctx.logo_ok | ctx.block_cells
    stray = view.region(top, key, exclude=exempt_cells) + view.region(top, key, shadow=True)
    if key in ctx.block_flat:
        stray = stray - ctx.block_flat[key]
    if stray.is_empty():
        ctx.add("C14.2", PASS, f"No layer {key[0]} polygons outside the logo or a declared block",
                [f"{plural(total, 'polygon')} in recognised SCL cells" if total else "none on this layer"], Source.IGNORE)
    else:
        mask_keys = sorted(k for k in view.layer_keys() if k != key)
        evidence = []
        for bb in bboxes(stray):
            region = db.Region(db.Box(bb[0], bb[1], bb[2], bb[3]))
            hidden = [f"{k[0]}/{k[1]}" for k in mask_keys if not (view.all_shapes(top, k) & region).is_empty()]
            evidence.append(f"polygon within {box(bb)}, hiding layers: {', '.join(hidden) or 'none so far'}")
        evidence += overflow_note(stray)
        ctx.add("C14.2", WARN, f"Layer {key[0]} polygons outside the logo or a declared block hide shapes from DRC", evidence, Source.IGNORE)

    covered = view.region(top, key, only=exempt_cells)
    if key in ctx.block_flat:
        covered = covered + ctx.block_flat[key]
    hidden = []
    if not covered.is_empty():
        for layer in sorted(k for k in view.layers if k != key):
            for bb in bboxes(view.region(top, layer, exclude=ctx.scl_cells) & covered, 20):
                hidden.append(f"layer {layer[0]}/{layer[1]} under the SCL cover at {box(bb)}")
    if hidden:
        ctx.add("C14.3", WARN, "User shapes sit under SCL's layer 999 cover, so DRC can't see them", hidden, Source.IGNORE)
    else:
        ctx.add("C14.3", PASS, "No user shapes under SCL's layer 999 cover", [], Source.IGNORE)


# ---------------------------------------------------------------- silicon number block


def check_15_block(ctx: Context) -> None:
    """15. The silicon number block, when one is given, is present and unchanged."""
    path = ctx.inputs.silicon_block
    title = "Silicon number block"
    if path is None:
        ctx.add("C15.1", MANUAL, f"{title} not checked",
                ["its format isn't known until it is issued; re-run with --silicon-block <gds>, or confirm by eye that it is inserted unmodified and unscaled"],
                Source.BLOCK)
        return
    if not ctx.geometry_ready:
        ctx.not_evaluated("C15.1", title, Source.BLOCK)
        return
    try:
        block = read_gds(path)
    except OSError as exc:
        ctx.add("C15.1", FAIL, "Silicon number block file can't be read", [str(exc)], Source.BLOCK)
        return
    if block.fatal:
        ctx.add("C15.1", FAIL, "Silicon number block file can't be read", [f"{path}: {block.issues[0]}"], Source.BLOCK)
        return
    tops = block.top_cells(ignore=ctx.context_name)
    if len(tops) != 1:
        ctx.add("C15.1", FAIL, "Silicon number block file must have one top cell", [f"{path}: top cells {tops}"], Source.BLOCK)
        return
    block_top = tops[0]
    block_view = View(block, exclude={ctx.context_name})
    layers = block_view.merged_layers(block_top)
    if not layers:
        ctx.add("C15.1", FAIL, "Silicon number block file has no geometry", [path], Source.BLOCK)
        return
    want_norm = block_view.cell_hash(block_top, normalise=True)
    block_bbox = Extents(block).geometry(block_top)
    block_size = (block_bbox[2] - block_bbox[0], block_bbox[3] - block_bbox[1])

    lib, view, top = ctx.lib, ctx.view, ctx.top
    reachable = reachable_cells(lib, top) - {top}
    evidence = [f"block file {os.path.basename(path)}, top cell {block_top!r}"]
    same_name_differs = False
    for name in [block_top] + sorted(reachable - {block_top}):
        if name not in reachable:
            continue
        bb = ctx.extents.geometry(name)
        if bb is None or (bb[2] - bb[0], bb[3] - bb[1]) != block_size or view.cell_hash(name, normalise=True) != want_norm:
            same_name_differs = same_name_differs or name == block_top
            continue
        placements = [t for n, t in flat_instances(lib, top) if n == name]
        if not placements:
            continue
        ctx.block_cells.add(name)
        evidence += [f"cell {name!r} placed at {t.describe()}" for t in placements]
        scaled = [t for t in placements if t.mag != 1.0]
        turned = [t for t in placements if t.angle != 0.0 or t.mirror]
        if scaled:
            ctx.add("C15.1", FAIL, "Silicon number block is scaled", evidence, Source.BLOCK)
        elif name != block_top or turned or len(placements) != 1:
            notes = []
            if name != block_top:
                notes.append(f"renamed from {block_top!r}")
            if turned:
                notes.append("rotated or mirrored")
            if len(placements) != 1:
                notes.append(f"placed {len(placements)} times")
            ctx.add("C15.1", WARN, f"Silicon number block is present and unscaled, but {', '.join(notes)}", evidence, Source.BLOCK)
        else:
            ctx.add("C15.1", PASS, "Silicon number block is present, geometrically unchanged and unscaled", evidence, Source.BLOCK)
        return

    if same_name_differs:
        evidence.append(f"cell {block_top!r} exists in the layout but its geometry differs from the block file")
        ctx.add("C15.1", FAIL, "Silicon number block has been edited", evidence, Source.BLOCK)
        return
    mask_keys = {tuple(pair) for pair in ctx.rules["mask_layers"]["layers"]}
    offset, altered = _find_flat_block(layers, view, top, mask_keys)
    if offset is not None:
        for key, region in layers.items():
            ctx.block_flat[key] = region.moved(offset[0], offset[1])
        evidence.append(f"found flattened into the layout, moved by {pt(*offset)}, unscaled")
        ctx.add("C15.1", PASS, "Silicon number block geometry is present and unscaled", evidence, Source.BLOCK,
                "Once flattened, it is compared exactly on mask layers inside the block's own outline.")
        return
    if altered:
        evidence.append(f"block shapes are present at {pt(*altered)}, but other mask shapes overlap its outline there")
        ctx.add("C15.1", FAIL, "Silicon number block has been edited", evidence, Source.BLOCK)
        return
    evidence.append("no placed cell or flattened geometry matches the block file; a scaled copy would not match either")
    ctx.add("C15.1", FAIL, "Silicon number block not found in the layout", evidence, Source.BLOCK)


def _find_flat_block(layers: dict, view: View, top: str, mask_keys: set):
    """Look for the block flattened into the design.

    Returns (offset, None) for an exact match on mask layers, (None, offset) when all
    its shapes are there but something else overlaps its outline, else (None, None).
    """
    anchor_key = min(layers, key=lambda k: layers[k].count())
    anchor = next(layers[anchor_key].each())
    anchor_box = anchor.bbox()
    design = {key: view.all_shapes(top, key).merged() for key in layers}
    outline = db.Box()
    for region in layers.values():
        outline += region.bbox()
    others = {key: view.all_shapes(top, key).merged() for key in (mask_keys & view.layer_keys()) - set(layers)}
    tried = 0
    altered = None
    for candidate in design[anchor_key].each():
        bb = candidate.bbox()
        if (bb.width(), bb.height()) != (anchor_box.width(), anchor_box.height()):
            continue
        tried += 1
        if tried > 1000:
            break
        dx, dy = bb.left - anchor_box.left, bb.bottom - anchor_box.bottom
        if not all((region.moved(dx, dy) - design[key]).is_empty() for key, region in layers.items()):
            continue
        window = db.Region(outline.moved(dx, dy))
        exact = all(
            ((design[key] & window) ^ region.moved(dx, dy)).is_empty() for key, region in layers.items() if key in mask_keys
        ) and all((region & window).is_empty() for region in others.values())
        if exact:
            return (dx, dy), None
        altered = altered or (dx, dy)
    return None, altered


# ---------------------------------------------------------------- rules DRC doesn't cover


def _wide(region: db.Region, half: int) -> db.Region:
    return region.merged().sized(-half, 2)


def check_16_metal(ctx: Context) -> None:
    """16. Metal 1 and metal 2 maximum 25 x 25 um."""
    titles = {"C16.1": "User metal within the maximum", "C16.2": "Pad metal over the maximum",
              "C16.3": "Other SCL metal over the maximum", "C16.4": "User metal joined to SCL metal"}
    if not ctx.geometry_ready:
        for id_, title in titles.items():
            ctx.not_evaluated(id_, title, Source.METAL)
        return
    view, top = ctx.view, ctx.top
    limit = round(ctx.rules["max_metal_um"] * 1000)
    half = limit // 2
    if half * 2 != limit:
        raise ValueError("max_metal_um must be a whole even number of nanometres")
    text = f"{um(limit)} x {um(limit)} um"
    assumption = (f"Read as: no merged metal shape may contain a {text} square. A long narrow bus is allowed. "
                  "Exact for right-angled shapes; for angled edges it can over-report, never under-report.")
    pad_assumption = "The PDK doesn't say whether pads are exempt, so pad metal is reported separately instead of failing."
    groups = {"user": [], "pad": [], "scl": [], "joined": []}
    for name, key in (("metal 1", ctx.layer("metal1")), ("metal 2", ctx.layer("metal2"))):
        user = view.region(top, key, exclude=ctx.scl_cells)
        pad = view.region(top, key, only=ctx.pad_ok)
        other = view.region(top, key, only=ctx.std_ok | ctx.logo_ok | ctx.block_cells) + view.region(top, key, shadow=True)
        cores = {"user": _wide(user, half), "pad": _wide(pad, half), "scl": _wide(other, half)}
        sources = {"user": user, "pad": pad, "scl": other}
        for group, core in cores.items():
            if core.is_empty():
                continue
            shapes = sources[group].merged().interacting(core)
            for index, polygon in enumerate(shapes.each()):
                if index >= LISTING_LIMIT:
                    groups[group].extend(overflow_note(shapes))
                    break
                bb = polygon.bbox()
                part = (core & db.Region(polygon)).bbox()
                groups[group].append(
                    f"{name}: shape within {box((bb.left, bb.bottom, bb.right, bb.top))}; a {text} square fits with "
                    f"its centre in {box((part.left, part.bottom, part.right, part.top))}"
                )
        every = user + pad + other
        scl_only = pad + other
        joined = _wide(every, half) - cores["user"] - _wide(scl_only, half)
        for bb in bboxes(joined):
            groups["joined"].append(f"{name}: combined shape is over the limit near {box(bb)}")
        groups["joined"].extend(overflow_note(joined))
    if groups["user"]:
        ctx.add("C16.1", FAIL, f"User metal over {text}", groups["user"], Source.METAL, assumption)
    else:
        ctx.add("C16.1", PASS, f"No user metal contains a {text} square", ["metal 1 and metal 2, flattened and merged"], Source.METAL, assumption)
    if groups["pad"]:
        ctx.add("C16.2", WARN, f"Pad metal is over {text}; the PDK doesn't say if pads are exempt", groups["pad"], Source.METAL, pad_assumption)
    else:
        ctx.add("C16.2", PASS, "No pad metal over the maximum", [], Source.METAL, pad_assumption)
    if groups["scl"]:
        ctx.add("C16.3", WARN, f"Frame, logo, block or standard cell metal is over {text}", groups["scl"], Source.METAL, pad_assumption)
    else:
        ctx.add("C16.3", PASS, "No other SCL metal over the maximum", [], Source.METAL, pad_assumption)
    if groups["joined"]:
        ctx.add("C16.4", WARN, "User metal joined to SCL metal is over the maximum", groups["joined"], Source.METAL,
                "A warning, because a bus reaching a pad necessarily meets wide pad metal.")
    else:
        ctx.add("C16.4", PASS, "No wide metal formed by joining user and SCL metal", [], Source.METAL)


def check_17_via(ctx: Context) -> None:
    """17. Via overlapping contact or poly."""
    titles = {"C17.1": "No via over contact or poly in user geometry", "C17.2": "Via over contact or poly in SCL cells"}
    if not ctx.geometry_ready:
        for id_, title in titles.items():
            ctx.not_evaluated(id_, title, Source.VIA)
        return
    view, top = ctx.view, ctx.top
    via_key = ctx.layer("via")
    scl = ctx.scl_cells
    evidence, totals = [], 0
    pairs = (("contact", ctx.layer("contact")), ("poly", ctx.layer("poly")))
    reachable = reachable_cells(ctx.lib, top)
    # An overlap inside one SCL cell is SCL's. An overlap between cells comes from
    # how they were placed, so it counts as the designer's.
    internal: dict = {}
    for word, key in pairs:
        for name in sorted(scl & reachable):
            overlap = view.region(name, via_key).merged() & view.region(name, key).merged()
            if not overlap.is_empty():
                internal[(name, word)] = overlap
    placements = defaultdict(list)
    wanted = {name for name, _ in internal}
    if wanted:
        for name, trans in flat_instances(ctx.lib, top):
            if name in wanted:
                placements[name].append(trans)
    for word, key in pairs:
        user_caused = view.all_shapes(top, via_key).merged() & view.all_shapes(top, key).merged()
        for (name, kind), overlap in internal.items():
            if kind == word:
                for trans in placements[name]:
                    placed = db.ICplxTrans(trans.mag, trans.angle, trans.mirror, round(trans.x), round(trans.y))
                    user_caused = user_caused - overlap.transformed(placed)
        for bb in bboxes(user_caused):
            evidence.append(f"via over {word} at {box(bb)}")
        evidence += overflow_note(user_caused)
        totals += user_caused.count()
    note = ("[DRC deck] rule 9.3.1 also flags via over contact, although [Analog doc] Level 9 says no check is "
            "defined. This check doesn't rely on the deck.")
    if evidence:
        ctx.add("C17.1", FAIL, f"{plural(totals, 'via overlap')} in user geometry", evidence + [note], Source.VIA)
    else:
        ctx.add("C17.1", PASS, titles["C17.1"], ["flattened; overlaps between SCL's own cells are reported in C17.2", note], Source.VIA)
    library = []
    for (name, word), overlap in sorted(internal.items()):
        first = bboxes(overlap)[0]
        library.append(f"{name}: {plural(overlap.count(), 'overlap')} of via over {word}, first at {box(first)} in cell coordinates")
    if library:
        ctx.add("C17.2", WARN, "SCL's own cells have via over contact or poly. Ask SCL whether they are accepted", library, Source.VIA,
                "Unmodified SCL cells warn, because the designer can't edit them.")
    else:
        ctx.add("C17.2", PASS, "No via over contact or poly in SCL cells", [], Source.VIA)


def check_18_manual(ctx: Context) -> None:
    """18. Rules no tool here can decide."""
    ctx.add("C18.1", MANUAL, "Metal necking at single diffusion contacts", ["review by hand: this rule has no DRC check and no check here"], Source.NECK)
    ctx.add("C18.2", MANUAL, "Metal 1 bus width on a single diffusion contact", ["review by hand against the 12 um limit: no DRC check and no check here"], Source.NECK)
    ctx.add("C18.3", MANUAL, "ERC guidelines", ["review each item in Table 11A; the deck reports only some of them, as @GUIDELINE"], Source.ERC)
    ctx.add("C18.4", MANUAL, "All electrical checks", ["current density, operating limits, connectivity and device use are outside this tool"], Source.ELECTRICAL)


def check_19_drc_report(ctx: Context) -> None:
    """19. Read the KLayout marker database."""
    path = ctx.inputs.drc_report
    if path is None:
        ctx.add("C19.1", MANUAL, "DRC report not read", ["no --drc-report given, so nothing is known about DRC"], Source.DRC_REPORT)
        return
    try:
        report = parse_report(path)
    except ReportInvalid as exc:
        ctx.add("C19.1", FAIL, "DRC report is not a valid KLayout marker database",
                [f"{path}: {exc}", "this is not treated as clean: DRC status is unknown"], Source.DRC_REPORT)
        return
    categories = report.categories
    evidence = [f"{plural(len(categories), 'category')}, {plural(report.total_markers, 'marker')}",
                f"generator: {report.generator or '(none)'}", f"top cell: {report.top_cell or '(none)'}",
                f"original file: {report.original_file or '(none)'}"]
    if not categories:
        ctx.add("C19.1", WARN, "DRC report is valid XML but has no categories, so it can't show a rule was run", evidence, Source.DRC_REPORT)
    else:
        ctx.add("C19.1", PASS, "DRC report is a valid KLayout marker database", evidence, Source.DRC_REPORT)

    def line(category):
        where = f" first: {category.first_value}" if category.first_value else ""
        cell = f" in {category.first_cell}" if category.first_cell else ""
        return f"{category.markers} x {category.path}{cell}.{where}"

    rule = [c for c in categories.values() if c.markers and not c.is_guideline]
    guide = [c for c in categories.values() if c.markers and c.is_guideline]
    if rule:
        ctx.add("C19.2", FAIL, f"{plural(sum(c.markers for c in rule), 'DRC marker')} in {plural(len(rule), 'rule category')}",
                [line(c) for c in sorted(rule, key=lambda c: c.path)], Source.DRC_REPORT)
    elif categories:
        count = sum(1 for c in categories.values() if not c.is_guideline)
        ctx.add("C19.2", PASS, "No markers in rule categories",
                [f"{plural(count, 'rule category')} with 0 markers in this report. This doesn't show the report is current or from this file"],
                Source.DRC_REPORT)
    if guide:
        ctx.add("C19.3", WARN, f"{plural(sum(c.markers for c in guide), '@GUIDELINE marker')} to review",
                [line(c) for c in sorted(guide, key=lambda c: c.path)], Source.DRC_REPORT)
    elif categories:
        ctx.add("C19.3", PASS, "No @GUIDELINE markers", [], Source.DRC_REPORT)
    mismatch = []
    if report.top_cell and report.top_cell != ctx.inputs.silicon:
        mismatch.append(f"report top cell {report.top_cell!r}, --silicon {ctx.inputs.silicon!r}")
    original = report.original_file.replace("\\", "/").split("/")[-1]
    if original and original != os.path.basename(ctx.inputs.gds):
        mismatch.append(f"report was made from {original!r}, this file is {os.path.basename(ctx.inputs.gds)!r}")
    if mismatch:
        ctx.add("C19.4", WARN, "DRC report may be from a different layout", mismatch, Source.DRC_REPORT)
    elif report.top_cell or report.original_file:
        ctx.add("C19.4", PASS, "DRC report names this layout", [], Source.DRC_REPORT,
                "Names only: the report holds no checksum of the GDS, so it can't prove it was run on these bytes.")
    else:
        ctx.add("C19.4", MANUAL, "DRC report doesn't say which layout it came from", [], Source.DRC_REPORT)


def check_20_notice(ctx: Context) -> None:
    """20. Always say what this tool isn't."""
    ctx.add("C20.1", MANUAL, "DRC and LVS must still be run separately", [NOTICE],
            "[Procedure] Design Verification Guidelines; PDK_GUIDE.md sections 4 and 13")


def run_all(ctx: Context, on_error) -> None:
    """Run every check. ``on_error`` records a failure of the tool itself."""
    ctx.context_name = ctx.refs.context
    sequence = [
        check_01_stream, check_02_top_cell, check_03_extent, build_view, check_05_filename, check_06_drc_name,
        check_07_md5, check_15_block, _collect_pads_if_ready, check_04_grid, check_08_pad_count, check_09_power,
        check_10_signal_pads, check_11_library, check_12_renamed, check_13_layers, check_14_ignore_layer,
        check_16_metal, check_17_via, check_18_manual, check_19_drc_report, check_20_notice,
    ]
    for step in sequence:
        try:
            step(ctx)
        except Exception as exc:  # A bug here must never read as a pass.
            on_error(step.__name__, exc)


def _collect_pads_if_ready(ctx: Context) -> None:
    if ctx.geometry_ready:
        collect_pads(ctx)
