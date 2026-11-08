"""Structured design model and checks for Yosys elaboration output."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from chipflow.runner.results import CheckResult, CheckStatus


SUPPORTED_COMBINATIONAL_CELLS = frozenset({
    "$not", "$pos", "$neg", "$and", "$or", "$xor", "$xnor",
    "$reduce_and", "$reduce_or", "$reduce_xor", "$reduce_xnor", "$reduce_bool",
    "$logic_not", "$logic_and", "$logic_or",
    "$shl", "$shr", "$sshl", "$sshr", "$shift", "$shiftx",
    "$lt", "$le", "$eq", "$ne", "$eqx", "$nex", "$ge", "$gt",
    "$add", "$sub", "$mul", "$div", "$mod", "$divfloor", "$modfloor", "$pow",
    "$mux", "$pmux", "$bmux", "$demux",
})
SUPPORTED_CELL_TYPES = SUPPORTED_COMBINATIONAL_CELLS | {"$dff"}
ASYNC_OR_LATCH_PREFIXES = ("$adff", "$aldff", "$dffsr", "$dlatch", "$sr")


def _tokenize_deps(s: str) -> list[str]:
    tokens = []
    current: list[str] = []
    i = 0
    s = s.strip()
    while i < len(s):
        if s[i].isspace():
            if current:
                tokens.append("".join(current))
                current = []
            i += 1
        elif s[i] == "\\":
            if i + 1 < len(s):
                if s[i + 1] == " ":
                    current.append(" ")
                    i += 2
                elif s[i + 1] == "\\":
                    current.append("\\")
                    i += 2
                else:
                    current.append(s[i + 1])
                    i += 2
            else:
                current.append("\\")
                i += 1
        else:
            current.append(s[i])
            i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def parse_make_deps(text: str) -> tuple[str, tuple[str, ...]]:
    """Parse make dependency rules from yosys -E deps.txt."""
    if not isinstance(text, str):
        raise ValueError("unexpected dependency syntax")
    if "$" in text or "#" in text:
        raise ValueError("unexpected dependency syntax: forbidden characters ($ or #)")
    cleaned = re.sub(r"\\\r?\n", " ", text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("unexpected dependency syntax: expected exactly one rule")
    line = lines[0]
    if line.count(":") != 1:
        raise ValueError("unexpected dependency syntax: expected exactly one ':'")
    target_part, prereq_part = line.split(":")
    targets = _tokenize_deps(target_part)
    if len(targets) != 1:
        raise ValueError("unexpected dependency syntax: expected exactly one target")
    prereqs = _tokenize_deps(prereq_part)
    return targets[0], tuple(prereqs)


@dataclass(frozen=True)
class PortInfo:
    name: str
    direction: str
    width: int
    offset: int = 0
    upto: bool = False
    signed: bool = False
    bits: tuple[Any, ...] = ()
    canonical_bits: tuple[str, ...] = ()


@dataclass(frozen=True)
class CellInfo:
    name: str
    type: str
    parameters: Mapping[str, int]
    port_directions: Mapping[str, str]
    connections: Mapping[str, tuple[Any, ...]]
    src: str = ""


@dataclass(frozen=True)
class Design:
    top: str
    top_present: bool
    ports: Mapping[str, PortInfo]
    cells: Mapping[str, CellInfo]
    netnames_with_init: tuple[str, ...]


def load_design(data: Mapping[str, Any], top: str) -> Design:
    modules = data.get("modules", {})
    if top not in modules:
        return Design(top=top, top_present=False, ports={}, cells={}, netnames_with_init=())
    mod = modules[top]

    ports: dict[str, PortInfo] = {}
    for port_name, pdata in mod.get("ports", {}).items():
        direction = pdata.get("direction", "input")
        bits = tuple(pdata.get("bits", ()))
        width = len(bits)
        offset = int(pdata.get("offset", 0))
        upto = bool(pdata.get("upto", 0))
        signed = bool(pdata.get("signed", 0))

        if width == 1 and offset == 0 and not upto:
            canonical_bits = (port_name,)
        else:
            canonical_bits = tuple(
                f"{port_name}[{offset + (width - 1 - i if upto else i)}]"
                for i in range(width)
            )
        ports[port_name] = PortInfo(
            name=port_name,
            direction=direction,
            width=width,
            offset=offset,
            upto=upto,
            signed=signed,
            bits=bits,
            canonical_bits=canonical_bits,
        )

    cells: dict[str, CellInfo] = {}
    for cell_name, cdata in mod.get("cells", {}).items():
        ctype = cdata.get("type", "")
        params: dict[str, int] = {}
        for pk, pv in cdata.get("parameters", {}).items():
            if isinstance(pv, str):
                params[pk] = int(pv, 2)
            elif isinstance(pv, int):
                params[pk] = pv
            else:
                params[pk] = int(pv)
        port_dirs = {k: str(v) for k, v in cdata.get("port_directions", {}).items()}
        conns: dict[str, tuple[Any, ...]] = {}
        for pk, pv in cdata.get("connections", {}).items():
            if isinstance(pv, Sequence):
                conns[pk] = tuple(pv)
            else:
                conns[pk] = (pv,)
        src = str(cdata.get("attributes", {}).get("src", ""))
        cells[cell_name] = CellInfo(
            name=cell_name,
            type=ctype,
            parameters=params,
            port_directions=port_dirs,
            connections=conns,
            src=src,
        )

    netnames_with_init: list[str] = []
    for net_name, ndata in mod.get("netnames", {}).items():
        attrs = ndata.get("attributes", {})
        if "init" in attrs:
            netnames_with_init.append(net_name)

    return Design(
        top=top,
        top_present=True,
        ports=ports,
        cells=cells,
        netnames_with_init=tuple(netnames_with_init),
    )


def map_src(src: str, path_map: Mapping[str, str] | None) -> str:
    if not src or not path_map:
        return src
    parts = src.split(":", 1)
    filename = parts[0]
    rest = ":" + parts[1] if len(parts) > 1 else ""
    mapped = path_map.get(filename, path_map.get(Path(filename).name, filename))
    return f"{mapped}{rest}"


@dataclass(frozen=True)
class DesignCheckRow:
    id: str
    status: CheckStatus
    message: str


def check_design(
    design: Design,
    clock: Any,
    reset: Any,
    derived_outputs: Sequence[Any] = (),
    path_map: Mapping[str, str] | None = None,
) -> tuple[DesignCheckRow, ...]:
    rows: list[DesignCheckRow] = []

    # 1. elab.top
    if not design.top_present:
        rows.append(DesignCheckRow("elab.top", CheckStatus.FAIL, f"top module {design.top!r} not found"))
        return tuple(rows)
    rows.append(DesignCheckRow("elab.top", CheckStatus.PASS, f"top module {design.top!r} found"))

    # 2. elab.inout
    inout_ports = [p.name for p in design.ports.values() if p.direction == "inout"]
    if inout_ports:
        rows.append(DesignCheckRow("elab.inout", CheckStatus.FAIL, f"inout ports not supported: {', '.join(inout_ports)}"))
    else:
        rows.append(DesignCheckRow("elab.inout", CheckStatus.PASS, "no inout ports"))

    # 3. elab.tristate
    has_z = False
    for cell in design.cells.values():
        for conns in cell.connections.values():
            if any(b == "z" for b in conns):
                has_z = True
                break
        if has_z:
            break
    if not has_z:
        for port in design.ports.values():
            if any(b == "z" for b in port.bits):
                has_z = True
                break
    if has_z:
        rows.append(DesignCheckRow("elab.tristate", CheckStatus.FAIL, "tristate / 'z' bits detected"))
    else:
        rows.append(DesignCheckRow("elab.tristate", CheckStatus.PASS, "no tristate or 'z' bits"))

    # 4. elab.blackbox
    non_dollar_cells = [c.type for c in design.cells.values() if not c.type.startswith("$")]
    if non_dollar_cells:
        unique = sorted(set(non_dollar_cells))
        rows.append(DesignCheckRow("elab.blackbox", CheckStatus.FAIL, f"unresolved blackbox cell(s): {', '.join(unique)}"))
    else:
        rows.append(DesignCheckRow("elab.blackbox", CheckStatus.PASS, "all cells are built-in primitives"))

    # 5. elab.async_or_latch
    async_or_latch = [c.type for c in design.cells.values() if c.type.startswith(ASYNC_OR_LATCH_PREFIXES)]
    if async_or_latch:
        unique = sorted(set(async_or_latch))
        rows.append(DesignCheckRow("elab.async_or_latch", CheckStatus.FAIL, f"asynchronous reset or latch cells not supported: {', '.join(unique)}"))
    else:
        rows.append(DesignCheckRow("elab.async_or_latch", CheckStatus.PASS, "no async reset or latch cells"))

    # 6. elab.cell_types
    unsupported_cells = [c.type for c in design.cells.values() if c.type.startswith("$") and c.type not in SUPPORTED_CELL_TYPES and not c.type.startswith(ASYNC_OR_LATCH_PREFIXES)]
    if unsupported_cells:
        unique = sorted(set(unsupported_cells))
        rows.append(DesignCheckRow("elab.cell_types", CheckStatus.FAIL, f"unsupported in yosys.elaboration 0.1.0: {', '.join(unique)}"))
    else:
        rows.append(DesignCheckRow("elab.cell_types", CheckStatus.PASS, "all cell types are supported"))

    # 7. elab.clock_polarity
    neg_flops: list[str] = []
    dff_cells = [c for c in design.cells.values() if c.type == "$dff"]
    for dff in dff_cells:
        polarity = dff.parameters.get("CLK_POLARITY", 1)
        if polarity != 1:
            neg_flops.append(f"{dff.name} at {map_src(dff.src, path_map)}")
    if neg_flops:
        rows.append(DesignCheckRow("elab.clock_polarity", CheckStatus.FAIL, f"negative-edge flop detected: {', '.join(neg_flops)}"))
    else:
        rows.append(DesignCheckRow("elab.clock_polarity", CheckStatus.PASS, "all flops are positive-edge"))

    # 8. elab.single_clock
    clock_port_name = clock.port if hasattr(clock, "port") else clock.get("port") if isinstance(clock, dict) else str(clock)
    clock_port = design.ports.get(clock_port_name)
    if not clock_port or not clock_port.bits:
        rows.append(DesignCheckRow("elab.single_clock", CheckStatus.FAIL, f"clock port {clock_port_name!r} not found"))
    else:
        clock_bit = clock_port.bits[0]
        wrong_clock_flops: list[str] = []
        for dff in dff_cells:
            clk_conns = dff.connections.get("CLK", ())
            if not clk_conns or clk_conns[0] != clock_bit:
                wrong_clock_flops.append(f"{dff.name} at {map_src(dff.src, path_map)}")
        if wrong_clock_flops:
            rows.append(DesignCheckRow("elab.single_clock", CheckStatus.FAIL, f"flops not on primary clock {clock_port_name!r}: {', '.join(wrong_clock_flops)}"))
        else:
            rows.append(DesignCheckRow("elab.single_clock", CheckStatus.PASS, f"all flops clocked by {clock_port_name!r}"))

    # 9. elab.init_values
    if design.netnames_with_init:
        rows.append(DesignCheckRow("elab.init_values", CheckStatus.FAIL, f"register init attributes detected on: {', '.join(design.netnames_with_init)}"))
    else:
        rows.append(DesignCheckRow("elab.init_values", CheckStatus.PASS, "no register init attributes"))

    # 10. elab.clock_usage (D1)
    if not clock_port or not clock_port.bits:
        rows.append(DesignCheckRow("elab.clock_usage", CheckStatus.FAIL, f"clock port {clock_port_name!r} not found"))
    else:
        clock_bit = clock_port.bits[0]
        non_clk_uses: list[tuple[CellInfo, str]] = []
        for cell in design.cells.values():
            for pin_name, conns in cell.connections.items():
                if clock_bit in conns:
                    if cell.type == "$dff" and pin_name == "CLK":
                        continue
                    non_clk_uses.append((cell, pin_name))

        # A top-level output that shares the clock bit is driven by the clock with
        # no cell in between (for example `assign out = clk;`). That is a use too.
        output_bit_names: dict[Any, list[str]] = {}
        for port in design.ports.values():
            if port.direction == "output":
                for idx, b in enumerate(port.bits):
                    output_bit_names.setdefault(b, []).append(port.canonical_bits[idx])
        direct_outputs = sorted(output_bit_names.get(clock_bit, ()))

        declared_ports = set()
        for d in derived_outputs:
            if isinstance(d, dict):
                declared_ports.add(d["port"])
            elif hasattr(d, "port"):
                declared_ports.add(d.port)

        if not non_clk_uses and not direct_outputs:
            if declared_ports:
                rows.append(DesignCheckRow(
                    "elab.clock_usage",
                    CheckStatus.FAIL,
                    f"stale derived_outputs declaration: clock net does not drive {', '.join(sorted(declared_ports))}",
                ))
            else:
                rows.append(DesignCheckRow("elab.clock_usage", CheckStatus.PASS, "clock net is only used by flop CLK pins"))
        else:
            if not declared_ports:
                if non_clk_uses:
                    first_cell, first_pin = non_clk_uses[0]
                    src = map_src(first_cell.src, path_map)
                    message = f"clock net used outside flop CLK pins: {first_cell.name} ({first_cell.type}) pin {first_pin} at {src}"
                else:
                    message = f"clock net drives output bit(s) directly: {', '.join(direct_outputs)}"
                rows.append(DesignCheckRow("elab.clock_usage", CheckStatus.FAIL, message))
            else:
                # Walk forward combinational cone
                bit_to_pins: dict[Any, list[tuple[CellInfo, str, str]]] = {}
                for cell in design.cells.values():
                    for pin_name, conns in cell.connections.items():
                        pdir = cell.port_directions.get(pin_name, "input")
                        for b in conns:
                            bit_to_pins.setdefault(b, []).append((cell, pin_name, pdir))

                queue: list[Any] = []
                cone_error: str | None = None
                for cell, pin_name in non_clk_uses:
                    if cell.type == "$dff":
                        cone_error = f"clock net directly feeds $dff {cell.name} pin {pin_name} at {map_src(cell.src, path_map)}"
                        break
                    for out_pin, out_dir in cell.port_directions.items():
                        if out_dir == "output":
                            for b in cell.connections.get(out_pin, ()):
                                queue.append(b)

                if cone_error:
                    rows.append(DesignCheckRow("elab.clock_usage", CheckStatus.FAIL, cone_error))
                else:
                    visited = set(queue)
                    reached_bits: set[str] = set(direct_outputs)
                    while queue:
                        b = queue.pop(0)
                        reached_bits.update(output_bit_names.get(b, ()))
                        for cell, pin_name, pdir in bit_to_pins.get(b, ()):
                            if pdir == "input":
                                if cell.type == "$dff":
                                    cone_error = f"clock cone reaches $dff {cell.name} pin {pin_name} at {map_src(cell.src, path_map)}"
                                    break
                                for out_pin, out_dir in cell.port_directions.items():
                                    if out_dir == "output":
                                        for out_b in cell.connections.get(out_pin, ()):
                                            if out_b not in visited:
                                                visited.add(out_b)
                                                queue.append(out_b)
                        if cone_error:
                            break

                    # Every bit of every declared port must be clock-derived, and nothing else.
                    missing_ports = sorted(p for p in declared_ports
                                           if p not in design.ports or design.ports[p].direction != "output")
                    expected_bits = {b for p in declared_ports if p in design.ports
                                     and design.ports[p].direction == "output"
                                     for b in design.ports[p].canonical_bits}
                    if cone_error:
                        rows.append(DesignCheckRow("elab.clock_usage", CheckStatus.FAIL, cone_error))
                    elif missing_ports:
                        rows.append(DesignCheckRow(
                            "elab.clock_usage",
                            CheckStatus.FAIL,
                            f"declared derived output(s) are not output ports of the design: {', '.join(missing_ports)}",
                        ))
                    elif reached_bits != expected_bits:
                        rows.append(DesignCheckRow(
                            "elab.clock_usage",
                            CheckStatus.FAIL,
                            f"clock cone reached output bits {sorted(reached_bits)}, expected {sorted(expected_bits)}",
                        ))
                    else:
                        rows.append(DesignCheckRow(
                            "elab.clock_usage",
                            CheckStatus.PASS,
                            f"clock cone reaches declared output port(s): {', '.join(sorted(declared_ports))}",
                        ))

    # 11. elab.reset_used
    reset_port_name = reset.port if hasattr(reset, "port") else reset.get("port") if isinstance(reset, dict) else str(reset)
    reset_port = design.ports.get(reset_port_name)
    if not reset_port or not reset_port.bits:
        rows.append(DesignCheckRow("elab.reset_used", CheckStatus.FAIL, f"reset port {reset_port_name!r} not found"))
    else:
        reset_bits = set(reset_port.bits)
        reset_used = False
        for cell in design.cells.values():
            for conns in cell.connections.values():
                if any(b in reset_bits for b in conns):
                    reset_used = True
                    break
            if reset_used:
                break
        if reset_used:
            rows.append(DesignCheckRow("elab.reset_used", CheckStatus.PASS, f"reset port {reset_port_name!r} is connected"))
        else:
            rows.append(DesignCheckRow("elab.reset_used", CheckStatus.FAIL, f"reset port {reset_port_name!r} has no connections in design"))

    # 12. elab.state_bits
    total_state_bits = sum(dff.parameters.get("WIDTH", 1) for dff in dff_cells)
    rows.append(DesignCheckRow("elab.state_bits", CheckStatus.PASS, f"{total_state_bits} state bits"))

    return tuple(rows)
