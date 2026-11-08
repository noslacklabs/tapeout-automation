"""Process and frame technology profile loader."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.resources
from pathlib import Path
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


@dataclass(frozen=True)
class ExcludedCell:
    cell: str
    workaround: str
    reason: str


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
    unused_slot_policies: tuple[str, ...]
    qualification_evidence: str
    signal_slots: tuple[SignalSlot, ...]
    power_pads: tuple[PowerPad, ...]
    pad_cells: Mapping[str, PadCellInfo]
    excluded_cells: tuple[ExcludedCell, ...] = ()


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
        "die_um", "dbu_um", "grid_um", "unused_slot_policies", "qualification_evidence",
        "signal_slot", "power_pad", "pad_cell", "excluded_cell",
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
        s = int(item["slot"])
        if s in seen_slots:
            raise ProfileError(f"duplicate signal slot: {s}")
        seen_slots.add(s)
        signal_slots.append(SignalSlot(
            slot=s,
            side=str(item["side"]),
            offset_um=float(item["offset_um"]),
            shipped_cell=str(item["shipped_cell"]),
        ))
    if len(signal_slots) != 12 or seen_slots != set(range(1, 13)):
        raise ProfileError("profile must define exactly 12 signal slots numbered 1 to 12")

    # Power pads
    pwr_raw = raw.get("power_pad", [])
    if not isinstance(pwr_raw, list):
        raise ProfileError("power_pad must be a list")
    power_pads: list[PowerPad] = []
    seen_pwr: set[int] = set()
    for item in pwr_raw:
        if not isinstance(item, dict):
            raise ProfileError("power_pad entries must be tables")
        pwr_keys = {"slot", "cell", "net"}
        if item.keys() - pwr_keys:
            raise ProfileError(f"unknown keys in power_pad: {sorted(item.keys() - pwr_keys)}")
        s = int(item["slot"])
        if s in seen_pwr or s in seen_slots:
            raise ProfileError(f"duplicate or overlapping power pad slot: {s}")
        seen_pwr.add(s)
        power_pads.append(PowerPad(
            slot=s,
            cell=str(item["cell"]),
            net=str(item["net"]),
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
        cell_keys = {"direction", "core_pin", "pad_pin", "drivers", "qualified_slots", "inverting", "limitations"}
        if cdata.keys() - cell_keys:
            raise ProfileError(f"unknown keys in pad_cell.{cell_name}: {sorted(cdata.keys() - cell_keys)}")
        q_slots = tuple(int(x) for x in cdata.get("qualified_slots", ()))
        for qs in q_slots:
            if qs not in seen_slots or qs in seen_pwr:
                raise ProfileError(f"qualified slot {qs} for {cell_name} is not a valid signal slot")
        pad_cells[cell_name] = PadCellInfo(
            name=cell_name,
            direction=str(cdata["direction"]),
            core_pin=str(cdata["core_pin"]),
            pad_pin=str(cdata["pad_pin"]),
            drivers=tuple(str(x) for x in cdata.get("drivers", ())),
            qualified_slots=q_slots,
            inverting=bool(cdata.get("inverting", False)),
            limitations=tuple(str(x) for x in cdata.get("limitations", ())),
        )

    # Excluded cells (Plan M2.2 / G8)
    ex_raw = raw.get("excluded_cell", [])
    if not isinstance(ex_raw, list):
        raise ProfileError("excluded_cell must be a list of tables")
    excluded_cells: list[ExcludedCell] = []
    for item in ex_raw:
        if not isinstance(item, dict):
            raise ProfileError("excluded_cell entries must be tables")
        excluded_cells.append(ExcludedCell(
            cell=str(item["cell"]),
            workaround=str(item.get("workaround", "")),
            reason=str(item.get("reason", "")),
        ))

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
        unused_slot_policies=tuple(str(x) for x in raw.get("unused_slot_policies", ())),
        qualification_evidence=str(raw["qualification_evidence"]),
        signal_slots=tuple(signal_slots),
        power_pads=tuple(power_pads),
        pad_cells=pad_cells,
        excluded_cells=tuple(excluded_cells),
    )
