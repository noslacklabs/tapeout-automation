"""Strict pad-map configuration loader and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import Any, Mapping, Sequence

from chipflow.config.project import ConfigError
from chipflow.runner.results import CheckStatus
from chipflow.stages.design_model import Design
from chipflow.technologies.profile import ProcessProfile


class PadError(ConfigError):
    """Pad configuration is invalid."""


@dataclass(frozen=True)
class PadSlotConfig:
    slot: int
    port: str | None = None
    pad_cell: str | None = None
    driver: str | None = None
    unused: str | None = None


@dataclass(frozen=True)
class PadConfig:
    path: Path
    schema_version: int
    frame: str
    profile_version: str
    slots: tuple[PadSlotConfig, ...]


def load_pad_map(path: Path | str) -> PadConfig:
    p = Path(path).resolve()
    if not p.is_file():
        raise PadError(f"pad configuration file not found: {p}")
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise PadError(f"invalid TOML in pad map: {err}") from err

    top_keys = {"schema_version", "frame", "profile_version", "slot"}
    unknown = raw.keys() - top_keys
    if unknown:
        raise PadError(f"unknown keys in pad map: {', '.join(sorted(unknown))}")

    if raw.get("schema_version") != 1:
        raise PadError("pad map schema_version must be 1")

    frame = str(raw.get("frame", ""))
    profile_version = str(raw.get("profile_version", ""))
    if not frame or not profile_version:
        raise PadError("pad map missing frame or profile_version")

    slots_raw = raw.get("slot", [])
    if not isinstance(slots_raw, list):
        raise PadError("slot must be an array of tables")

    slots: list[PadSlotConfig] = []
    for idx, item in enumerate(slots_raw):
        if not isinstance(item, dict):
            raise PadError(f"slot[{idx}] must be a table")
        if "slot" not in item:
            raise PadError(f"slot[{idx}] missing 'slot' number")
        slot_num = item["slot"]
        if isinstance(slot_num, bool) or not isinstance(slot_num, int):
            raise PadError(f"slot[{idx}] 'slot' must be an integer")

        if "unused" in item:
            slot_keys = {"slot", "unused"}
            if item.keys() - slot_keys:
                raise PadError(f"unknown keys in unused slot[{idx}]: {sorted(item.keys() - slot_keys)}")
            slots.append(PadSlotConfig(slot=slot_num, unused=str(item["unused"])))
        else:
            slot_keys = {"slot", "port", "pad_cell", "driver"}
            if item.keys() - slot_keys:
                raise PadError(f"unknown keys in active slot[{idx}]: {sorted(item.keys() - slot_keys)}")
            for req in ("port", "pad_cell", "driver"):
                if req not in item or not isinstance(item[req], str) or not item[req].strip():
                    raise PadError(f"active slot[{idx}] missing or empty {req!r}")
            slots.append(PadSlotConfig(
                slot=slot_num,
                port=str(item["port"]),
                pad_cell=str(item["pad_cell"]),
                driver=str(item["driver"]),
            ))

    return PadConfig(
        path=p,
        schema_version=1,
        frame=frame,
        profile_version=profile_version,
        slots=tuple(slots),
    )


@dataclass(frozen=True)
class PadCheckRow:
    id: str
    status: CheckStatus
    message: str


def validate_pads(
    pad_config: PadConfig,
    profile: ProcessProfile,
    design: Design | None = None,
) -> tuple[PadCheckRow, ...]:
    rows: list[PadCheckRow] = []

    # 1. pads.schema
    rows.append(PadCheckRow("pads.schema", CheckStatus.PASS, "strict pad-map schema validated"))

    # 2. pads.frame
    if pad_config.frame != profile.frame or pad_config.profile_version != profile.profile_version:
        rows.append(PadCheckRow(
            "pads.frame",
            CheckStatus.FAIL,
            f"frame mismatch: expected {profile.frame} v{profile.profile_version}, got {pad_config.frame} v{pad_config.profile_version}",
        ))
    else:
        rows.append(PadCheckRow("pads.frame", CheckStatus.PASS, f"frame {profile.frame} v{profile.profile_version} matched"))

    # 3. pads.slot_coverage
    slot_numbers = [s.slot for s in pad_config.slots]
    seen_slots: set[int] = set()
    dup_slots: set[int] = set()
    for s in slot_numbers:
        if s in seen_slots:
            dup_slots.add(s)
        seen_slots.add(s)

    expected_slots = set(range(1, 13))
    out_of_range = seen_slots - expected_slots
    missing_slots = expected_slots - seen_slots

    if dup_slots or out_of_range or missing_slots:
        errors = []
        if dup_slots:
            errors.append(f"duplicate slots: {sorted(dup_slots)}")
        if out_of_range:
            errors.append(f"out of range slots: {sorted(out_of_range)}")
        if missing_slots:
            errors.append(f"missing slots: {sorted(missing_slots)}")
        rows.append(PadCheckRow("pads.slot_coverage", CheckStatus.FAIL, "; ".join(errors)))
    else:
        rows.append(PadCheckRow("pads.slot_coverage", CheckStatus.PASS, "slots 1..12 covered exactly once"))

    # 4. pads.cell
    unknown_cells = []
    for s in pad_config.slots:
        if s.pad_cell and s.pad_cell not in profile.pad_cells:
            unknown_cells.append(f"slot {s.slot}: {s.pad_cell}")
    if unknown_cells:
        rows.append(PadCheckRow("pads.cell", CheckStatus.FAIL, f"unsupported pad cell(s): {', '.join(unknown_cells)}"))
    else:
        rows.append(PadCheckRow("pads.cell", CheckStatus.PASS, "all pad cells in allowlist"))

    # 5. pads.driver
    driver_errors = []
    for s in pad_config.slots:
        if s.pad_cell and s.pad_cell in profile.pad_cells:
            cell_info = profile.pad_cells[s.pad_cell]
            if cell_info.direction == "input":
                if s.driver != "none":
                    driver_errors.append(f"slot {s.slot} input cell {s.pad_cell} must have driver='none', got {s.driver!r}")
            elif cell_info.direction == "output":
                if s.driver not in cell_info.drivers:
                    driver_errors.append(f"slot {s.slot} output cell {s.pad_cell} driver {s.driver!r} not in {cell_info.drivers}")
    if driver_errors:
        rows.append(PadCheckRow("pads.driver", CheckStatus.FAIL, "; ".join(driver_errors)))
    else:
        rows.append(PadCheckRow("pads.driver", CheckStatus.PASS, "drivers match cell requirements"))

    # 6. pads.unused (D3)
    unused_slots = [s for s in pad_config.slots if s.unused is not None]
    if unused_slots:
        policies = {p.id: p for p in profile.unused_slot_policies}
        unused_errors: list[str] = []
        used_policies: list[str] = []
        sig_slot_by_num = {s.slot: s for s in profile.signal_slots}
        for s in unused_slots:
            policy = policies.get(s.unused)
            if policy is None:
                unused_errors.append(f"slot {s.slot}: no qualified unused-slot policy {s.unused!r} in profile {profile.profile_version}")
                continue
            shipped = sig_slot_by_num[s.slot].shipped_cell
            if shipped not in policy.allowed_cells:
                unused_errors.append(
                    f"slot {s.slot}: policy {policy.id!r} does not allow shipped cell {shipped!r}"
                )
                continue
            used_policies.append(f"{s.slot}:{policy.id}")
        if unused_errors:
            rows.append(PadCheckRow("pads.unused", CheckStatus.FAIL, "; ".join(unused_errors)))
        else:
            rows.append(PadCheckRow(
                "pads.unused",
                CheckStatus.PASS,
                f"qualified unused-slot policies applied: {', '.join(used_policies)}",
            ))
    else:
        rows.append(PadCheckRow("pads.unused", CheckStatus.PASS, "no unused slots"))

    # 7. pads.qualification (D4)
    unqualified = []
    for s in pad_config.slots:
        if s.pad_cell and s.pad_cell in profile.pad_cells:
            cell_info = profile.pad_cells[s.pad_cell]
            if s.slot not in cell_info.qualified_slots:
                unqualified.append(f"slot {s.slot} with {s.pad_cell}")
    if unqualified:
        rows.append(PadCheckRow(
            "pads.qualification",
            CheckStatus.NOT_RUN,
            f"not yet physically qualified: {', '.join(unqualified)}",
        ))
    else:
        rows.append(PadCheckRow("pads.qualification", CheckStatus.PASS, "all pad cell and slot placements are qualified"))

    # Rows that require design
    if design is None or not design.top_present:
        rows.append(PadCheckRow("pads.direction", CheckStatus.NOT_RUN, "elaboration output required"))
        rows.append(PadCheckRow("pads.port_coverage", CheckStatus.NOT_RUN, "elaboration output required"))
        rows.append(PadCheckRow("pads.capacity", CheckStatus.NOT_RUN, "elaboration output required"))
    else:
        # Build map of bit name -> PortInfo
        design_bits: dict[str, str] = {}  # bit_name -> direction
        for p in design.ports.values():
            for b in p.canonical_bits:
                design_bits[b] = p.direction

        # 8. pads.direction
        direction_errors = []
        for s in pad_config.slots:
            if s.port and s.pad_cell and s.pad_cell in profile.pad_cells:
                port_bit = s.port
                # Normalize port bit: if port_bit has [0] and port was scalar [offset 0], handle alias
                if port_bit not in design_bits:
                    base = port_bit.split("[")[0]
                    if base in design_bits and port_bit.endswith("[0]"):
                        port_bit = base
                if port_bit in design_bits:
                    pdir = design_bits[port_bit]
                    cdir = profile.pad_cells[s.pad_cell].direction
                    if pdir != cdir:
                        direction_errors.append(f"slot {s.slot} port {s.port} direction {pdir} != pad cell {s.pad_cell} direction {cdir}")
        if direction_errors:
            rows.append(PadCheckRow("pads.direction", CheckStatus.FAIL, "; ".join(direction_errors)))
        else:
            rows.append(PadCheckRow("pads.direction", CheckStatus.PASS, "pad cell directions match design ports"))

        # 9. pads.port_coverage
        coverage_errors = []
        mapped_bits: list[str] = []
        for s in pad_config.slots:
            if s.port:
                port_bit = s.port
                if port_bit not in design_bits:
                    base = port_bit.split("[")[0]
                    if base in design_bits and port_bit.endswith("[0]"):
                        port_bit = base
                if port_bit not in design_bits:
                    coverage_errors.append(f"slot {s.slot} names unknown port bit: {s.port}")
                else:
                    mapped_bits.append(port_bit)

        seen_bits: set[str] = set()
        dup_bits: set[str] = set()
        for b in mapped_bits:
            if b in seen_bits:
                dup_bits.add(b)
            seen_bits.add(b)

        missing_bits = set(design_bits.keys()) - seen_bits
        if dup_bits:
            coverage_errors.append(f"port bits mapped more than once: {sorted(dup_bits)}")
        if missing_bits:
            coverage_errors.append(f"unmapped port bits: {sorted(missing_bits)}")

        if coverage_errors:
            rows.append(PadCheckRow("pads.port_coverage", CheckStatus.FAIL, "; ".join(coverage_errors)))
        else:
            rows.append(PadCheckRow("pads.port_coverage", CheckStatus.PASS, "all design port bits mapped exactly once"))

        # 10. pads.capacity
        total_bits = len(design_bits)
        if total_bits > 12:
            rows.append(PadCheckRow("pads.capacity", CheckStatus.FAIL, f"design requires {total_bits} I/O bits, exceeding 12 available signal slots"))
        else:
            rows.append(PadCheckRow("pads.capacity", CheckStatus.PASS, f"design I/O bits ({total_bits}) fit in 12 signal slots"))

    return tuple(rows)


def build_pad_plan(pad_config: PadConfig, profile: ProcessProfile) -> dict[str, Any]:
    sig_slot_by_num = {s.slot: s for s in profile.signal_slots}
    pads_list: list[dict[str, Any]] = []

    # Slots 1 to 12
    for s in sorted(pad_config.slots, key=lambda x: x.slot):
        sig_info = sig_slot_by_num[s.slot]
        if s.unused is not None:
            pads_list.append({
                "slot": s.slot,
                "kind": "signal",
                "side": sig_info.side,
                "offset_um": sig_info.offset_um,
                "unused": s.unused,
            })
        else:
            cell_info = profile.pad_cells.get(s.pad_cell, None)
            direction = cell_info.direction if cell_info else ""
            pads_list.append({
                "slot": s.slot,
                "kind": "signal",
                "side": sig_info.side,
                "offset_um": sig_info.offset_um,
                "port": s.port,
                "direction": direction,
                "cell": s.pad_cell,
                "driver": s.driver,
            })

    # Slots 13 to 16
    for p in sorted(profile.power_pads, key=lambda x: x.slot):
        pads_list.append({
            "slot": p.slot,
            "kind": "power",
            "side": p.side,
            "offset_um": None,
            "x_um": p.x_um,
            "y_um": p.y_um,
            "rotation": p.rotation,
            "mirror": p.mirror,
            "cell": p.cell,
            "net": p.net,
        })

    return {
        "schema_version": 1,
        "frame": profile.frame,
        "profile_version": profile.profile_version,
        "pads": pads_list,
    }
