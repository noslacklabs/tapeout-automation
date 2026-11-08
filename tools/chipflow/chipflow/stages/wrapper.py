"""Generic core-wrapper generation from structured design ports and pad slots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from chipflow.technologies.profile import ProcessProfile


class WrapperError(ValueError):
    """The pad plan cannot be expressed as a safe wrapper around the design."""


@dataclass(frozen=True)
class Driver:
    instance: str
    cell: str
    input_pin: str
    output_pin: str
    source: str
    target: str


@dataclass(frozen=True)
class WrapperPort:
    name: str
    direction: str
    width: int
    canonical_base: str


@dataclass(frozen=True)
class WrapperResult:
    text: str
    top: str
    ports: tuple[WrapperPort, ...]
    drivers: tuple[Driver, ...]
    warnings: tuple[str, ...]


def _range(width: int) -> str:
    return "" if width == 1 else f"[{width - 1}:0] "


def generate_wrapper(
    design_top: str,
    design_ports: Mapping[str, Mapping[str, Any]],
    pad_plan: Mapping[str, Any],
    profile: ProcessProfile,
    driver_pins: Mapping[str, tuple[str, str]],
    wrapper_top: str | None = None,
) -> WrapperResult:
    """Build a wrapper that connects every design port bit to its pad driver.

    Output pads are driven through the profile-declared inverting driver so that
    driver plus pad preserve the design polarity.  Input pads connect directly.
    Every design port bit must be mapped exactly once; an unmapped or duplicated
    bit is an error rather than a silently omitted connection.
    """
    if not design_ports:
        raise WrapperError("design has no ports")
    top = wrapper_top or f"{design_top}_core"

    port_info: dict[str, Mapping[str, Any]] = {}
    bit_index: dict[str, tuple[str, int | None]] = {}
    for name, info in design_ports.items():
        direction = info.get("direction")
        width = info.get("width")
        bits = tuple(info.get("canonical_bits", ()))
        if direction not in {"input", "output"}:
            raise WrapperError(f"port {name!r} has unsupported direction {direction!r}")
        if type(width) is not int or width < 1 or len(bits) != width:
            raise WrapperError(f"port {name!r} has inconsistent width and canonical bits")
        port_info[name] = info
        for index, bit in enumerate(bits):
            if bit in bit_index:
                raise WrapperError(f"canonical bit {bit!r} appears in more than one port")
            bit_index[bit] = (name, None if width == 1 else index)

    pads = pad_plan.get("pads", ())
    if not isinstance(pads, Sequence) or isinstance(pads, (str, bytes)):
        raise WrapperError("pad plan has no pad array")

    mapped: dict[str, dict[int | None, dict[str, Any]]] = {}
    warnings: list[str] = []
    for pad in pads:
        if pad.get("kind") != "signal":
            continue
        port = pad.get("port")
        if port is None:
            if pad.get("unused") is None:
                raise WrapperError(f"signal slot {pad.get('slot')} has neither a port nor an unused policy")
            continue
        if port in bit_index:
            base, index = bit_index[port]
        else:
            base_candidate, _, suffix = port.partition("[")
            index_candidate = suffix.rstrip("]") if suffix else ""
            if (
                suffix
                and index_candidate == "0"
                and base_candidate in port_info
                and port_info[base_candidate].get("width") == 1
            ):
                base, index = base_candidate, None
            else:
                raise WrapperError(f"pad slot {pad.get('slot')} names unknown design port bit {port!r}")
        design_direction = port_info[base]["direction"]
        cell_name = pad.get("cell")
        if not isinstance(cell_name, str) or cell_name not in profile.pad_cells:
            raise WrapperError(f"pad slot {pad.get('slot')} has unknown pad cell {cell_name!r}")
        cell = profile.pad_cells[cell_name]
        if cell.direction != design_direction:
            raise WrapperError(
                f"pad slot {pad.get('slot')} {cell_name} direction {cell.direction} "
                f"does not match design port {base} direction {design_direction}"
            )
        if index in mapped.setdefault(base, {}):
            raise WrapperError(f"design port bit {port!r} is mapped more than once")
        mapped[base][index] = pad

    for base, info in port_info.items():
        bits = mapped.get(base, {})
        expected = 1 if info["width"] == 1 else info["width"]
        if len(bits) != expected:
            raise WrapperError(
                f"design port {base!r} has {len(bits)} mapped bit(s), expected {expected}"
            )
        if info["width"] == 1 and None not in bits:
            raise WrapperError(f"scalar design port {base!r} is not mapped")
        if info["width"] > 1 and set(bits) != set(range(info["width"])):
            raise WrapperError(f"design port {base!r} has non-contiguous mapped bits")

    declarations: list[str] = []
    wires: list[str] = []
    connections: list[str] = []
    wrapper_ports: list[WrapperPort] = []
    drivers: list[Driver] = []
    seen_wrapper_names: dict[str, str] = {}

    for base, info in port_info.items():
        direction = info["direction"]
        width = info["width"]
        wrapper_name = base if direction == "input" else f"{base}_b"
        if wrapper_name in seen_wrapper_names:
            raise WrapperError(
                f"wrapper port {wrapper_name!r} for {base!r} collides with {seen_wrapper_names[wrapper_name]!r}"
            )
        seen_wrapper_names[wrapper_name] = base
        declarations.append(f"  {direction} {_range(width)}{wrapper_name}")
        wrapper_ports.append(WrapperPort(wrapper_name, direction, width, base))
        if direction == "output":
            wires.append(f"  wire {_range(width)}{base};")
        connections.append(f".{base}({base})")
        if direction == "input":
            continue
        for bit in sorted(mapped[base]):
            pad = mapped[base][bit]
            cell_name = str(pad["cell"])
            cell = profile.pad_cells[cell_name]
            if not cell.drivers:
                raise WrapperError(f"pad cell {cell_name} has no qualified output driver")
            driver_cell = cell.drivers[0]
            pins = driver_pins.get(driver_cell)
            if pins is None or len(cell.drivers) != 1:
                raise WrapperError(f"driver {driver_cell} is not uniquely modelled")
            input_pin, output_pin = pins
            suffix = "" if bit is None else f"_{bit}"
            target = f"{base}_b" if bit is None else f"{base}_b[{bit}]"
            source = base if bit is None else f"{base}[{bit}]"
            drivers.append(Driver(
                instance=f"paddrv_{base}{suffix}",
                cell=driver_cell,
                input_pin=input_pin,
                output_pin=output_pin,
                source=source,
                target=target,
            ))

    lines = [
        "// Generated by chipflow. Do not edit.",
        f"module {top}(",
        ",\n".join(declarations),
        ");",
        *wires,
        f"  {design_top} u_design({', '.join(connections)});",
        *(f"  (* keep *) {d.cell} {d.instance}(.{d.input_pin}({d.source}), .{d.output_pin}({d.target}));"
          for d in drivers),
        "endmodule",
        "",
    ]
    return WrapperResult(
        text="\n".join(lines),
        top=top,
        ports=tuple(wrapper_ports),
        drivers=tuple(drivers),
        warnings=tuple(warnings),
    )


def driver_pin_map(profile: ProcessProfile, liberty_cells: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """Return driver cell -> (single input pin, single output pin) from Liberty."""
    result: dict[str, tuple[str, str]] = {}
    driver_names = {
        driver
        for cell in profile.pad_cells.values()
        for driver in cell.drivers
    }
    for name in sorted(driver_names):
        cell = liberty_cells.get(name)
        if cell is None:
            continue
        inputs = [pin.name for pin in cell.pins.values() if pin.direction == "input"]
        outputs = [pin.name for pin in cell.pins.values() if pin.direction == "output"]
        if len(inputs) == 1 and len(outputs) == 1:
            result[name] = (inputs[0], outputs[0])
    return result
