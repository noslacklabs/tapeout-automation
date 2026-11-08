"""Floorplan generation: row creation, track grid, pad pins and driver placement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from chipflow.stages.library_model import parse_lef_macros
from chipflow.technologies.profile import ProcessProfile


def build_floorplan(
    pad_plan: Mapping[str, Any],
    profile: ProcessProfile,
    pad_terminals: Mapping[str, Sequence[float]],
    cells_lef: Path | str,
) -> tuple[dict[str, Any], str]:
    """Derive core block floorplan, pin locations, and locked pad driver positions.

    Returns (floorplan_dict, floorplan_tcl_str).
    """
    if profile.frame_geometry is None or profile.technology_geometry is None:
        raise ValueError("profile has no frame_geometry or technology_geometry")

    fg = profile.frame_geometry
    tg = profile.technology_geometry

    block_lo = fg.block_lo_um
    block_hi = fg.block_hi_um
    free_lo = fg.free_lo_um
    free_hi = fg.free_hi_um
    rows_count = fg.row_count
    row_pitch = fg.row_pitch_um
    row_h = fg.row_height_um
    site = tg.site_width_um
    m1_w = tg.metal1_width_um
    m2_w = tg.metal2_width_um
    ring_w = fg.ring_width_um
    ring_gap = fg.ring_gap_um
    zone = fg.zone_um

    track_x0 = round(block_lo + fg.track_x_offset_um, 3)
    track_y0 = round(block_lo + fg.track_y_offset_um, 3)

    n_sites = int(round((block_hi - block_lo) / site))
    span = (rows_count - 1) * row_pitch + row_h
    free = block_hi - block_lo - span
    if free < 40.0:
        raise ValueError(f"rows do not fit inside block: {free:.1f} um spare")
    y0 = block_lo + int(free / 2.0 / site) * site
    rows = [round(y0 + i * row_pitch, 3) for i in range(rows_count)]

    # Horizontal tracks within channels between rows
    chan_tracks: list[float] = []
    edges = [block_lo] + [r for r in rows] + [block_hi]
    tops = [block_lo - 100.0] + [r + row_h for r in rows]
    for lo_edge, hi_edge in zip(tops, rows + [block_hi + 100.0]):
        y = track_y0
        while y < block_hi:
            if (lo_edge + 1.5 + m1_w / 2.0 <= y <= hi_edge - 1.5 - m1_w / 2.0 and
                    block_lo + 4.0 < y < block_hi - 4.0):
                chan_tracks.append(round(y, 3))
            y += site

    def track_x(v: float) -> float:
        return round(track_x0 + round((v - track_x0) / site) * site, 3)

    macros = parse_lef_macros(cells_lef)
    cell_widths = {m: macro.width_um for m, macro in macros.items()}
    pin_x_offsets = {
        m: macro.pin_pins["OUT1"][0][0]
        for m, macro in macros.items()
        if "OUT1" in macro.pin_pins and macro.pin_pins["OUT1"]
    }

    pads: list[dict[str, Any]] = []
    used_y: list[float] = []

    for p in pad_plan.get("pads", []):
        if p.get("kind") != "signal":
            continue
        slot_num = int(p["slot"])
        side = str(p["side"])
        cell = str(p["cell"])
        port = str(p.get("port", ""))
        direction = str(p.get("direction", "input"))
        driver = str(p.get("driver", "none"))

        if p.get("unused") is not None:
            # Unused signal slot policy handling
            continue

        if cell not in pad_terminals:
            raise ValueError(f"pad cell {cell!r} has no measured terminal range")
        term_a, term_b = pad_terminals[cell]
        org = float(p.get("offset_um", 0.0))

        # Sign of local x along die axis: slot 6 and 11 are inverted in the 2mm frame
        sign = -1 if slot_num in (6, 11) else +1
        term_lo, term_hi = sorted((round(org + sign * term_a, 3), round(org + sign * term_b, 3)))
        term_mid = round((term_lo + term_hi) / 2.0, 3)

        if direction == "output":
            core_port = port.replace("[", "_b[") if "[" in port else f"{port}_b"
        else:
            core_port = port

        if side in ("bottom", "top"):
            pin_layer = "metal2"
            pin_x = track_x(term_mid)
            pin_y = block_lo if side == "bottom" else block_hi
        else:
            pin_layer = "metal1"
            pin_x = block_lo if side == "left" else block_hi
            cand = [y for y in chan_tracks if y not in used_y]
            if not cand:
                raise ValueError("no spare channel tracks for left/right pin placement")
            pin_y = min(cand, key=lambda y: abs(y - term_mid))
            used_y.append(pin_y)

        pad_entry: dict[str, Any] = {
            "pad": slot_num,
            "port": port,
            "dir": "out" if direction == "output" else "in",
            "cell": cell,
            "driver": driver,
            "side": side,
            "term_lo": term_lo,
            "term_hi": term_hi,
            "term_mid": term_mid,
            "net": port,
            "core_port": core_port,
            "pin_layer": pin_layer,
            "pin_x": pin_x,
            "pin_y": pin_y,
        }

        if driver not in ("none", "-", ""):
            if driver not in cell_widths:
                raise ValueError(f"driver cell {driver!r} missing from LEF")
            drv_w = cell_widths[driver]
            px_drv = pin_x_offsets.get(driver, 0.0)
            if side in ("bottom", "top"):
                y = rows[0] if side == "bottom" else rows[-1]
                x = pin_x - px_drv
            else:
                y = min(rows, key=lambda r: abs(r + row_h / 2.0 - pin_y))
                x = block_lo if side == "left" else block_lo + (n_sites * site - drv_w)
            x = block_lo + round((x - block_lo) / site) * site
            x = min(max(x, block_lo), block_hi - drv_w)
            clean_port = port.replace("[", "_").replace("]", "")
            pad_entry["drv_inst"] = f"paddrv_{clean_port}"
            pad_entry["drv_x"] = round(x, 3)
            pad_entry["drv_y"] = round(y, 3)

        pads.append(pad_entry)

    fp_dict = {
        "free": [free_lo, free_hi],
        "block": [block_lo, block_hi],
        "site": site,
        "n_sites": n_sites,
        "rows": rows,
        "row_pitch": row_pitch,
        "row_h": row_h,
        "track_x0": track_x0,
        "track_y0": track_y0,
        "ring_w": ring_w,
        "ring_gap": ring_gap,
        "zone": zone,
        "vss_ring": [round(free_lo + zone, 3), round(free_lo + zone + ring_w, 3)],
        "vdd_ring": [round(free_lo + zone + ring_w + ring_gap, 3), round(free_lo + zone + 2.0 * ring_w + ring_gap, 3)],
        "pads": pads,
        "row_budget_um": round(rows_count * n_sites * site, 1),
    }

    tcl_lines = [
        "# Written by chipflow.stages.floorplan. Don't edit.",
        f"set BLOCK {{{block_lo:.3f} {block_lo:.3f} {block_hi:.3f} {block_hi:.3f}}}",
        "set ROWS {" + " ".join(f"{r:.3f}" for r in rows) + "}",
        f"set N_SITES {n_sites}",
        f"set ROW_H {row_h:.3f}",
        f"set X_OFF {fg.track_x_offset_um:.3f}",
        f"set Y_OFF {fg.track_y_offset_um:.3f}",
        f"set PITCH {site:.3f}",
        f"set BLOCK_LO {block_lo:.3f}",
        "set PINS {",
    ]
    for p in pads:
        tcl_lines.append(f"  {{{p['core_port']} {p['pin_layer']} {p['pin_x']:.3f} {p['pin_y']:.3f}}}")
    tcl_lines.append("}\nset DRIVERS {")
    for p in pads:
        if "drv_inst" in p:
            tcl_lines.append(f"  {{{p['drv_inst']} {p['drv_x']:.3f} {p['drv_y']:.3f}}}")
    tcl_lines.append("}\n")

    return fp_dict, "\n".join(tcl_lines)
