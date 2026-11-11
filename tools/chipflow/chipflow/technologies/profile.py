"""Process and frame technology profile loader."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.resources
import math
from pathlib import Path
import re
import tomllib
from typing import Any, Mapping, Sequence


class ProfileError(ValueError):
    """Profile specification is invalid or unsupported."""


@dataclass(frozen=True)
class SignalSlot:
    slot: int
    side: str
    offset_um: float
    shipped_cell: str


@dataclass(frozen=True)
class PowerPad:
    slot: int
    cell: str
    net: str
    side: str
    offset_um: float
    x_um: float
    y_um: float
    rotation: int
    mirror: bool


@dataclass(frozen=True)
class PadCellInfo:
    name: str
    direction: str
    core_pin: str
    pad_pin: str
    drivers: tuple[str, ...]
    qualified_slots: tuple[int, ...]
    inverting: bool = False
    limitations: tuple[str, ...] = ()
    cdl_pins: tuple[str, ...] = ()
    power_pins: tuple[str, ...] = ()
    terminal_x_um: tuple[float, float] = ()
    core_pin_capacitance_pf: float = 0.0
    pad_pin_capacitance_pf: float = 0.0
    max_core_load_pf: float = 0.0
    max_pad_load_pf: float = 0.0


@dataclass(frozen=True)
class ExcludedCell:
    cell: str
    workaround: str
    reason: str


@dataclass(frozen=True)
class UnusedSlotPolicy:
    id: str
    evidence: str
    allowed_cells: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class FrameGeometry:
    free_lo_um: float
    free_hi_um: float
    block_lo_um: float
    block_hi_um: float
    row_count: int
    row_pitch_um: float
    row_height_um: float
    ring_width_um: float
    ring_gap_um: float
    zone_um: float
    track_x_offset_um: float
    track_y_offset_um: float
    pin_size_um: float
    inner_ring_lo_um: float
    inner_ring_hi_um: float
    outer_ring_hi_um: float
    vdd_probe_um: tuple[float, float]
    vss_probe_um: tuple[float, float]


@dataclass(frozen=True)
class TechnologyGeometry:
    site: str
    site_width_um: float
    site_height_um: float
    metal1_pitch_um: float
    metal2_pitch_um: float
    metal1_width_um: float
    metal2_width_um: float
    via: str
    via_metal1: tuple[float, float, float, float]
    via_cut: tuple[float, float, float, float]
    via_metal2: tuple[float, float, float, float]
    gds_metal1: tuple[int, int]
    gds_metal2: tuple[int, int]
    gds_via1: tuple[int, int]
    gds_pin_metal1: tuple[int, int]
    gds_pin_metal2: tuple[int, int]
    gds_window: tuple[int, int]
    core_gds_offsets_um: Mapping[str, float]


@dataclass(frozen=True)
class SynthesisProfile:
    tie_high_cell: str
    tie_high_pin: str
    tie_low_cell: str
    tie_low_pin: str
    buffer_cell: str
    clock_buffer_cell: str
    abc_driving_cell: str
    abc_load_pf: float


@dataclass(frozen=True)
class PhysicalVerificationProfile:
    """How the pinned DRC/LVS decks and the presubmission checker are driven."""
    drc_report_variable: str
    drc_hierarchical_line: str
    drc_flat_line: str
    lvs_schematic_variable: str
    lvs_netlist_variable: str
    lvs_report_variable: str
    presubmit_die_mm: int


@dataclass(frozen=True)
class ProcessProfile:
    path: str
    sha256: str
    schema_version: int
    profile: str
    profile_version: str
    frame: str
    frame_top_cell: str
    die_um: float
    dbu_um: float
    grid_um: float
    unused_slot_policies: tuple[UnusedSlotPolicy, ...]
    qualification_evidence: str
    signal_slots: tuple[SignalSlot, ...]
    power_pads: tuple[PowerPad, ...]
    pad_cells: Mapping[str, PadCellInfo]
    excluded_cells: tuple[ExcludedCell, ...] = ()
    frame_geometry: FrameGeometry | None = None
    technology_geometry: TechnologyGeometry | None = None
    synthesis: SynthesisProfile | None = None
    physical_verification: PhysicalVerificationProfile | None = None

    def excluded_for(self, workaround: str) -> tuple[str, ...]:
        return tuple(c.cell for c in self.excluded_cells if c.workaround == workaround)


def _table(value: Any, field: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ProfileError(f"{field} must be a table")
    unknown = value.keys() - allowed
    missing = required - value.keys()
    if unknown:
        raise ProfileError(f"{field}: unknown keys: {', '.join(sorted(unknown))}")
    if missing:
        raise ProfileError(f"{field}: missing keys: {', '.join(sorted(missing))}")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProfileError(f"{field} must be a nonempty string")
    return value


def _number(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ProfileError(f"{field} must be finite numeric data")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ProfileError(f"{field} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ProfileError(f"{field} must be at most {maximum}")
    return number


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProfileError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _pair(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ProfileError(f"{field} must contain exactly two numbers")
    first = _number(value[0], field + "[0]")
    second = _number(value[1], field + "[1]")
    return first, second


def _integer_pair(value: Any, field: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ProfileError(f"{field} must contain exactly two integers")
    return _integer(value[0], field + "[0]", 0, 1000000), _integer(value[1], field + "[1]", 0, 1000000)


def _box(value: Any, field: str, *, signed: bool) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ProfileError(f"{field} must contain exactly four numbers")
    result = tuple(
        _number(v, f"{field}[{i}]", minimum=None if signed else 0.0)
        for i, v in enumerate(value)
    )
    if len(result) != 4:
        raise ProfileError(f"{field} is malformed")
    return result  # type: ignore[return-value]


def load_profile(path: Path | str | None = None) -> ProcessProfile:
    if path is None:
        resource = importlib.resources.files("chipflow").joinpath("technologies/scl_c1d/profile-0.1.0.toml")
        content_bytes = resource.read_bytes()
        path_str = str(resource)
    else:
        p = Path(path).resolve()
        if not p.is_file():
            raise ProfileError(f"profile file not found: {p}")
        content_bytes = p.read_bytes()
        path_str = str(p)

    sha256 = hashlib.sha256(content_bytes).hexdigest()
    try:
        raw = tomllib.loads(content_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ProfileError(f"invalid TOML in profile: {err}") from err

    top_keys = {
        "schema_version", "profile", "profile_version", "frame", "frame_top_cell",
        "die_um", "dbu_um", "grid_um", "unused_slot_policy", "qualification_evidence",
        "signal_slot", "power_pad", "pad_cell", "excluded_cell",
        "frame_geometry", "technology_geometry", "synthesis", "physical_verification",
    }
    unknown = raw.keys() - top_keys
    if unknown:
        raise ProfileError(f"unknown keys in profile: {', '.join(sorted(unknown))}")

    if raw.get("schema_version") != 1:
        raise ProfileError("profile schema_version must be 1")

    # Signal slots
    sig_raw = raw.get("signal_slot", [])
    if not isinstance(sig_raw, list):
        raise ProfileError("signal_slot must be a list")
    signal_slots: list[SignalSlot] = []
    seen_slots: set[int] = set()
    for item in sig_raw:
        if not isinstance(item, dict):
            raise ProfileError("signal_slot entries must be tables")
        slot_keys = {"slot", "side", "offset_um", "shipped_cell"}
        if item.keys() - slot_keys:
            raise ProfileError(f"unknown keys in signal_slot: {sorted(item.keys() - slot_keys)}")
        s = _integer(item["slot"], "signal_slot.slot", 1, 12)
        if s in seen_slots:
            raise ProfileError(f"duplicate signal slot: {s}")
        seen_slots.add(s)
        side = _string(item["side"], "signal_slot.side")
        if side not in {"bottom", "top", "left", "right"}:
            raise ProfileError(f"signal_slot.side must be bottom, top, left or right: {side!r}")
        signal_slots.append(SignalSlot(
            slot=s,
            side=side,
            offset_um=_number(item["offset_um"], "signal_slot.offset_um", minimum=0.0),
            shipped_cell=_string(item["shipped_cell"], "signal_slot.shipped_cell"),
        ))
    if len(signal_slots) != 12 or seen_slots != set(range(1, 13)):
        raise ProfileError("profile must define exactly 12 signal slots numbered 1 to 12")

    # Power pads, including the shipped frame placement.
    pwr_raw = raw.get("power_pad", [])
    if not isinstance(pwr_raw, list):
        raise ProfileError("power_pad must be a list")
    power_pads: list[PowerPad] = []
    seen_pwr: set[int] = set()
    for item in pwr_raw:
        if not isinstance(item, dict):
            raise ProfileError("power_pad entries must be tables")
        pwr_keys = {"slot", "cell", "net", "side", "offset_um", "x_um", "y_um", "rotation", "mirror"}
        if item.keys() - pwr_keys:
            raise ProfileError(f"unknown keys in power_pad: {sorted(item.keys() - pwr_keys)}")
        s = _integer(item["slot"], "power_pad.slot", 13, 16)
        if s in seen_pwr or s in seen_slots:
            raise ProfileError(f"duplicate or overlapping power pad slot: {s}")
        seen_pwr.add(s)
        side = _string(item["side"], "power_pad.side")
        if side not in {"bottom", "top", "left", "right"}:
            raise ProfileError(f"power_pad.side must be bottom, top, left or right: {side!r}")
        rotation = _integer(item["rotation"], "power_pad.rotation", 0, 270)
        if rotation % 90:
            raise ProfileError("power_pad.rotation must be a multiple of 90")
        power_pads.append(PowerPad(
            slot=s,
            cell=_string(item["cell"], "power_pad.cell"),
            net=_string(item["net"], "power_pad.net"),
            side=side,
            offset_um=_number(item["offset_um"], "power_pad.offset_um", minimum=0.0),
            x_um=_number(item["x_um"], "power_pad.x_um", minimum=0.0),
            y_um=_number(item["y_um"], "power_pad.y_um", minimum=0.0),
            rotation=rotation,
            mirror=bool(item["mirror"]),
        ))
    if len(power_pads) != 4 or seen_pwr != {13, 14, 15, 16}:
        raise ProfileError("profile must define exactly 4 power pads for slots 13 to 16")

    # Pad cells
    cells_raw = raw.get("pad_cell", {})
    if not isinstance(cells_raw, dict):
        raise ProfileError("pad_cell must be a table")
    pad_cells: dict[str, PadCellInfo] = {}
    for cell_name, cdata in cells_raw.items():
        if not isinstance(cdata, dict):
            raise ProfileError(f"pad_cell.{cell_name} must be a table")
        cell_keys = {
            "direction", "core_pin", "pad_pin", "drivers", "qualified_slots", "inverting", "limitations",
            "cdl_pins", "power_pins", "terminal_x_um", "core_pin_capacitance_pf",
            "pad_pin_capacitance_pf", "max_core_load_pf", "max_pad_load_pf",
        }
        if cdata.keys() - cell_keys:
            raise ProfileError(f"unknown keys in pad_cell.{cell_name}: {sorted(cdata.keys() - cell_keys)}")
        direction = _string(cdata["direction"], f"pad_cell.{cell_name}.direction")
        if direction not in {"input", "output"}:
            raise ProfileError(f"pad_cell.{cell_name}.direction must be input or output")
        q_slots = tuple(_integer(x, f"pad_cell.{cell_name}.qualified_slots", 1, 12) for x in cdata.get("qualified_slots", ()))
        for qs in q_slots:
            if qs not in seen_slots or qs in seen_pwr:
                raise ProfileError(f"qualified slot {qs} for {cell_name} is not a valid signal slot")
        cdl_pins = tuple(_string(x, f"pad_cell.{cell_name}.cdl_pins") for x in cdata.get("cdl_pins", ()))
        power_pins = tuple(_string(x, f"pad_cell.{cell_name}.power_pins") for x in cdata.get("power_pins", ()))
        terminal: tuple[float, float] = ()
        if "terminal_x_um" in cdata:
            terminal = _pair(cdata["terminal_x_um"], f"pad_cell.{cell_name}.terminal_x_um")
            if terminal[0] >= terminal[1]:
                raise ProfileError(f"pad_cell.{cell_name}.terminal_x_um must be increasing")
        pad_cells[cell_name] = PadCellInfo(
            name=cell_name,
            direction=direction,
            core_pin=_string(cdata["core_pin"], f"pad_cell.{cell_name}.core_pin"),
            pad_pin=_string(cdata["pad_pin"], f"pad_cell.{cell_name}.pad_pin"),
            drivers=tuple(_string(x, f"pad_cell.{cell_name}.drivers") for x in cdata.get("drivers", ())),
            qualified_slots=q_slots,
            inverting=bool(cdata.get("inverting", False)),
            limitations=tuple(_string(x, f"pad_cell.{cell_name}.limitations") for x in cdata.get("limitations", ())),
            cdl_pins=cdl_pins,
            power_pins=power_pins,
            terminal_x_um=terminal,
            core_pin_capacitance_pf=_number(cdata.get("core_pin_capacitance_pf", 0.0), f"pad_cell.{cell_name}.core_pin_capacitance_pf", minimum=0.0),
            pad_pin_capacitance_pf=_number(cdata.get("pad_pin_capacitance_pf", 0.0), f"pad_cell.{cell_name}.pad_pin_capacitance_pf", minimum=0.0),
            max_core_load_pf=_number(cdata.get("max_core_load_pf", 0.0), f"pad_cell.{cell_name}.max_core_load_pf", minimum=0.0),
            max_pad_load_pf=_number(cdata.get("max_pad_load_pf", 0.0), f"pad_cell.{cell_name}.max_pad_load_pf", minimum=0.0),
        )

    # Excluded cells (P01 drop policy, P02 force policy)
    ex_raw = raw.get("excluded_cell", [])
    if not isinstance(ex_raw, list):
        raise ProfileError("excluded_cell must be a list of tables")
    excluded_cells: list[ExcludedCell] = []
    seen_excluded: set[str] = set()
    for item in ex_raw:
        if not isinstance(item, dict):
            raise ProfileError("excluded_cell entries must be tables")
        cell = _string(item.get("cell"), "excluded_cell.cell")
        if cell in seen_excluded:
            raise ProfileError(f"duplicate excluded cell: {cell}")
        seen_excluded.add(cell)
        excluded_cells.append(ExcludedCell(
            cell=cell,
            workaround=_string(item.get("workaround", ""), "excluded_cell.workaround"),
            reason=_string(item.get("reason", ""), "excluded_cell.reason"),
        ))

    # Unused-slot policies
    policy_raw = raw.get("unused_slot_policy", [])
    if not isinstance(policy_raw, list):
        raise ProfileError("unused_slot_policy must be an array of tables")
    policies: list[UnusedSlotPolicy] = []
    for item in policy_raw:
        row = _table(item, "unused_slot_policy", {"id", "evidence", "allowed_cells", "description"}, {"id", "evidence", "allowed_cells"})
        policy_id = _string(row["id"], "unused_slot_policy.id")
        if policy_id in {p.id for p in policies}:
            raise ProfileError(f"duplicate unused-slot policy: {policy_id}")
        allowed = tuple(_string(x, "unused_slot_policy.allowed_cells") for x in row["allowed_cells"])
        if not allowed:
            raise ProfileError("unused_slot_policy.allowed_cells must not be empty")
        policies.append(UnusedSlotPolicy(
            id=policy_id,
            evidence=_string(row["evidence"], "unused_slot_policy.evidence"),
            allowed_cells=allowed,
            description=str(row.get("description", "")),
        ))

    # Geometry and synthesis.
    frame_geometry = None
    if "frame_geometry" in raw:
        g = _table(raw["frame_geometry"], "frame_geometry", {
            "free_lo_um", "free_hi_um", "block_lo_um", "block_hi_um", "row_count",
            "row_pitch_um", "row_height_um", "ring_width_um", "ring_gap_um", "zone_um",
            "track_x_offset_um", "track_y_offset_um", "pin_size_um", "inner_ring_lo_um",
            "inner_ring_hi_um", "outer_ring_hi_um", "vdd_probe_um", "vss_probe_um",
        }, {
            "free_lo_um", "free_hi_um", "block_lo_um", "block_hi_um", "row_count",
            "row_pitch_um", "row_height_um", "ring_width_um", "ring_gap_um", "zone_um",
            "track_x_offset_um", "track_y_offset_um", "pin_size_um", "inner_ring_lo_um",
            "inner_ring_hi_um", "outer_ring_hi_um", "vdd_probe_um", "vss_probe_um",
        })
        frame_geometry = FrameGeometry(
            free_lo_um=_number(g["free_lo_um"], "frame_geometry.free_lo_um", minimum=0.0),
            free_hi_um=_number(g["free_hi_um"], "frame_geometry.free_hi_um", minimum=0.0),
            block_lo_um=_number(g["block_lo_um"], "frame_geometry.block_lo_um", minimum=0.0),
            block_hi_um=_number(g["block_hi_um"], "frame_geometry.block_hi_um", minimum=0.0),
            row_count=_integer(g["row_count"], "frame_geometry.row_count", 1, 1000),
            row_pitch_um=_number(g["row_pitch_um"], "frame_geometry.row_pitch_um", minimum=0.0),
            row_height_um=_number(g["row_height_um"], "frame_geometry.row_height_um", minimum=0.0),
            ring_width_um=_number(g["ring_width_um"], "frame_geometry.ring_width_um", minimum=0.0),
            ring_gap_um=_number(g["ring_gap_um"], "frame_geometry.ring_gap_um", minimum=0.0),
            zone_um=_number(g["zone_um"], "frame_geometry.zone_um", minimum=0.0),
            track_x_offset_um=_number(g["track_x_offset_um"], "frame_geometry.track_x_offset_um", minimum=0.0),
            track_y_offset_um=_number(g["track_y_offset_um"], "frame_geometry.track_y_offset_um", minimum=0.0),
            pin_size_um=_number(g["pin_size_um"], "frame_geometry.pin_size_um", minimum=0.0),
            inner_ring_lo_um=_number(g["inner_ring_lo_um"], "frame_geometry.inner_ring_lo_um", minimum=0.0),
            inner_ring_hi_um=_number(g["inner_ring_hi_um"], "frame_geometry.inner_ring_hi_um", minimum=0.0),
            outer_ring_hi_um=_number(g["outer_ring_hi_um"], "frame_geometry.outer_ring_hi_um", minimum=0.0),
            vdd_probe_um=_pair(g["vdd_probe_um"], "frame_geometry.vdd_probe_um"),
            vss_probe_um=_pair(g["vss_probe_um"], "frame_geometry.vss_probe_um"),
        )
        if not (frame_geometry.free_lo_um < frame_geometry.block_lo_um < frame_geometry.block_hi_um < frame_geometry.free_hi_um):
            raise ProfileError("frame_geometry must satisfy free_lo < block_lo < block_hi < free_hi")

    technology_geometry = None
    if "technology_geometry" in raw:
        g = _table(raw["technology_geometry"], "technology_geometry", {
            "site", "site_width_um", "site_height_um", "metal1_pitch_um", "metal2_pitch_um",
            "metal1_width_um", "metal2_width_um", "via", "via_metal1", "via_cut", "via_metal2",
            "gds_metal1", "gds_metal2", "gds_via1", "gds_pin_metal1", "gds_pin_metal2",
            "gds_window", "core_gds_offsets_um",
        }, {
            "site", "site_width_um", "site_height_um", "metal1_pitch_um", "metal2_pitch_um",
            "metal1_width_um", "metal2_width_um", "via", "via_metal1", "via_cut", "via_metal2",
            "gds_metal1", "gds_metal2", "gds_via1", "gds_pin_metal1", "gds_pin_metal2",
            "gds_window", "core_gds_offsets_um",
        })
        offsets_raw = g["core_gds_offsets_um"]
        if not isinstance(offsets_raw, dict):
            raise ProfileError("technology_geometry.core_gds_offsets_um must be a table")
        offsets = {
            _string(name, "technology_geometry.core_gds_offsets_um key"): _number(value, f"technology_geometry.core_gds_offsets_um.{name}")
            for name, value in offsets_raw.items()
        }
        technology_geometry = TechnologyGeometry(
            site=_string(g["site"], "technology_geometry.site"),
            site_width_um=_number(g["site_width_um"], "technology_geometry.site_width_um", minimum=0.0),
            site_height_um=_number(g["site_height_um"], "technology_geometry.site_height_um", minimum=0.0),
            metal1_pitch_um=_number(g["metal1_pitch_um"], "technology_geometry.metal1_pitch_um", minimum=0.0),
            metal2_pitch_um=_number(g["metal2_pitch_um"], "technology_geometry.metal2_pitch_um", minimum=0.0),
            metal1_width_um=_number(g["metal1_width_um"], "technology_geometry.metal1_width_um", minimum=0.0),
            metal2_width_um=_number(g["metal2_width_um"], "technology_geometry.metal2_width_um", minimum=0.0),
            via=_string(g["via"], "technology_geometry.via"),
            via_metal1=_box(g["via_metal1"], "technology_geometry.via_metal1", signed=True),
            via_cut=_box(g["via_cut"], "technology_geometry.via_cut", signed=True),
            via_metal2=_box(g["via_metal2"], "technology_geometry.via_metal2", signed=True),
            gds_metal1=_integer_pair(g["gds_metal1"], "technology_geometry.gds_metal1"),
            gds_metal2=_integer_pair(g["gds_metal2"], "technology_geometry.gds_metal2"),
            gds_via1=_integer_pair(g["gds_via1"], "technology_geometry.gds_via1"),
            gds_pin_metal1=_integer_pair(g["gds_pin_metal1"], "technology_geometry.gds_pin_metal1"),
            gds_pin_metal2=_integer_pair(g["gds_pin_metal2"], "technology_geometry.gds_pin_metal2"),
            gds_window=_integer_pair(g["gds_window"], "technology_geometry.gds_window"),
            core_gds_offsets_um=offsets,
        )

    synthesis = None
    if "synthesis" in raw:
        s = _table(raw["synthesis"], "synthesis", {
            "tie_high_cell", "tie_high_pin", "tie_low_cell", "tie_low_pin",
            "buffer_cell", "clock_buffer_cell", "abc_driving_cell", "abc_load_pf",
        }, {"tie_high_cell", "tie_high_pin", "tie_low_cell", "tie_low_pin", "buffer_cell", "clock_buffer_cell", "abc_driving_cell", "abc_load_pf"})
        synthesis = SynthesisProfile(
            tie_high_cell=_string(s["tie_high_cell"], "synthesis.tie_high_cell"),
            tie_high_pin=_string(s["tie_high_pin"], "synthesis.tie_high_pin"),
            tie_low_cell=_string(s["tie_low_cell"], "synthesis.tie_low_cell"),
            tie_low_pin=_string(s["tie_low_pin"], "synthesis.tie_low_pin"),
            buffer_cell=_string(s["buffer_cell"], "synthesis.buffer_cell"),
            clock_buffer_cell=_string(s["clock_buffer_cell"], "synthesis.clock_buffer_cell"),
            abc_driving_cell=_string(s["abc_driving_cell"], "synthesis.abc_driving_cell"),
            abc_load_pf=_number(s["abc_load_pf"], "synthesis.abc_load_pf", minimum=0.0),
        )

    physical_verification = None
    if "physical_verification" in raw:
        keys = {"drc_report_variable", "drc_hierarchical_line", "drc_flat_line", "lvs_schematic_variable",
                "lvs_netlist_variable", "lvs_report_variable", "presubmit_die_mm"}
        v = _table(raw["physical_verification"], "physical_verification", keys, keys)
        names = {}
        for key in sorted(keys - {"presubmit_die_mm"}):
            names[key] = _string(v[key], f"physical_verification.{key}")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", names[key]):
                raise ProfileError(f"physical_verification.{key} must be a plain identifier")
        if names["drc_hierarchical_line"] == names["drc_flat_line"]:
            raise ProfileError("physical_verification DRC hierarchical and flat mode lines must differ")
        physical_verification = PhysicalVerificationProfile(
            presubmit_die_mm=_integer(v["presubmit_die_mm"], "physical_verification.presubmit_die_mm", 1, 5),
            **names)

    return ProcessProfile(
        path=path_str,
        sha256=sha256,
        schema_version=1,
        profile=str(raw["profile"]),
        profile_version=str(raw["profile_version"]),
        frame=str(raw["frame"]),
        frame_top_cell=str(raw["frame_top_cell"]),
        die_um=float(raw["die_um"]),
        dbu_um=float(raw["dbu_um"]),
        grid_um=float(raw["grid_um"]),
        unused_slot_policies=tuple(policies),
        qualification_evidence=str(raw["qualification_evidence"]),
        signal_slots=tuple(signal_slots),
        power_pads=tuple(power_pads),
        pad_cells=pad_cells,
        excluded_cells=tuple(excluded_cells),
        frame_geometry=frame_geometry,
        technology_geometry=technology_geometry,
        synthesis=synthesis,
        physical_verification=physical_verification,
    )
