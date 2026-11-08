"""Structured connectivity checks for mapped gate-level netlists."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence


class ConnectivityError(ValueError):
    """The mapped netlist contains an unsupported or ambiguous record."""


def bit_key(bit: Any) -> str:
    return json.dumps(bit, sort_keys=True)


def is_constant(bit: Any) -> bool:
    return bit in ("0", "1")


@dataclass(frozen=True)
class Cone:
    cells: tuple[str, ...]
    leaves: tuple[str, ...]
    constants: tuple[str, ...]
    multi_input: tuple[str, ...]
    clock_pins: tuple[tuple[str, str], ...]
    unknown: tuple[str, ...]


@dataclass(frozen=True)
class NetlistIndex:
    top: str
    ports: Mapping[str, Mapping[str, Any]]
    cells: Mapping[str, Mapping[str, Any]]
    drivers: Mapping[str, tuple[tuple[str, str], ...]]
    consumers: Mapping[str, tuple[tuple[str, str], ...]]
    alias_bits: Mapping[str, tuple[str, ...]]
    bit_names: Mapping[str, str]

    def input_bits(self, cell_name: str) -> tuple[Any, ...]:
        cell = self.cells[cell_name]
        directions = cell.get("port_directions") or {}
        connections = cell.get("connections") or {}
        if not directions:
            raise ConnectivityError(f"cell {cell_name} has no port directions")
        bits: list[Any] = []
        for pin, direction in directions.items():
            if direction != "input":
                continue
            for bit in connections.get(pin, ()):
                bits.append(bit)
        return tuple(bits)

    def output_bits(self, cell_name: str) -> tuple[Any, ...]:
        cell = self.cells[cell_name]
        directions = cell.get("port_directions") or {}
        connections = cell.get("connections") or {}
        bits: list[Any] = []
        for pin, direction in directions.items():
            if direction != "output":
                continue
            for bit in connections.get(pin, ()):
                bits.append(bit)
        return tuple(bits)


def build_index(data: Mapping[str, Any], top: str) -> NetlistIndex:
    modules = data.get("modules")
    if not isinstance(modules, dict) or top not in modules:
        raise ConnectivityError(f"top module {top!r} not found in mapped netlist")
    module = modules[top]
    ports = module.get("ports")
    cells = module.get("cells")
    if not isinstance(ports, dict) or not isinstance(cells, dict):
        raise ConnectivityError("mapped module has no ports or cells table")

    drivers: dict[str, list[tuple[str, str]]] = {}
    consumers: dict[str, list[tuple[str, str]]] = {}
    for name, cell in cells.items():
        directions = cell.get("port_directions") or {}
        connections = cell.get("connections") or {}
        if not directions:
            raise ConnectivityError(f"cell {name} has no port directions")
        for pin, direction in directions.items():
            if direction not in {"input", "output"}:
                raise ConnectivityError(f"cell {name} pin {pin} has unsupported direction {direction!r}")
            for bit in connections.get(pin, ()):
                key = bit_key(bit)
                if direction == "output":
                    drivers.setdefault(key, []).append((name, pin))
                else:
                    consumers.setdefault(key, []).append((name, pin))

    alias_bits: dict[str, list[str]] = {}
    bit_names: dict[str, str] = {}
    netnames = module.get("netnames") or {}
    for name, record in netnames.items():
        bits = record.get("bits", ())
        if not isinstance(bits, Sequence) or isinstance(bits, (str, bytes)):
            raise ConnectivityError(f"net name {name} has an invalid bit array")
        for bit in bits:
            key = bit_key(bit)
            alias_bits.setdefault(key, []).append(name)
            bit_names.setdefault(key, name)

    return NetlistIndex(
        top=top,
        ports=ports,
        cells=cells,
        drivers={key: tuple(value) for key, value in drivers.items()},
        consumers={key: tuple(value) for key, value in consumers.items()},
        alias_bits={key: tuple(sorted(set(value))) for key, value in alias_bits.items()},
        bit_names=bit_names,
    )


def trace_cone(
    index: NetlistIndex,
    output_bit: Any,
    sequential_cells: Sequence[str] | frozenset[str] = (),
) -> Cone:
    """Walk backwards through combinational cells to primary/sequential leaves."""
    sequential = frozenset(sequential_cells)
    stack: list[Any] = [output_bit]
    visited_bits: set[str] = set()
    cells: set[str] = set()
    leaves: set[str] = set()
    constants: set[str] = set()
    unknown: set[str] = set()
    multi_input: set[str] = set()
    clock_pins: list[tuple[str, str]] = []
    sequential_outputs: set[str] = set()

    while stack:
        bit = stack.pop()
        key = bit_key(bit)
        if key in visited_bits:
            continue
        visited_bits.add(key)
        if is_constant(bit):
            constants.add(key)
            continue
        driver_list = index.drivers.get(key, ())
        if not driver_list:
            # A top-level input port drives this bit; anything else is undriven.
            is_port = any(
                port.get("direction") == "input" and key in {bit_key(b) for b in port.get("bits", ())}
                for port in index.ports.values()
            )
            if is_port:
                leaves.add(key)
            else:
                unknown.add(key)
            continue
        for cell_name, pin_name in driver_list:
            cell = index.cells[cell_name]
            ctype = str(cell.get("type", ""))
            if ctype.startswith("$"):
                raise ConnectivityError(f"unmapped primitive {ctype} drives {key}")
            # A sequential cell's output is a cone leaf; it is not traversed.
            if ctype in sequential:
                sequential_outputs.add(key)
                leaves.add(key)
                continue
            cells.add(cell_name)
            inputs = index.input_bits(cell_name)
            if not inputs:
                # A tie cell is a constant source, not a combinational gate.
                constants.add(key)
                cells.discard(cell_name)
                continue
            distinct = {bit_key(b) for b in inputs}
            if len(distinct) > 1:
                multi_input.add(cell_name)
            for input_bit in inputs:
                if is_constant(input_bit):
                    constants.add(bit_key(input_bit))
                    continue
                stack.append(input_bit)

    return Cone(
        cells=tuple(sorted(cells)),
        leaves=tuple(sorted(leaves | sequential_outputs)),
        constants=tuple(sorted(constants)),
        multi_input=tuple(sorted(multi_input)),
        clock_pins=tuple(clock_pins),
        unknown=tuple(sorted(unknown)),
    )


@dataclass(frozen=True)
class GateRow:
    id: str
    status: str
    message: str
    evidence: Mapping[str, Any]


def check_derived_output_gate(
    index: NetlistIndex,
    wrapper_top: str,
    check_id: str,
    derived_port: str,
    clock_port: str,
    wrapper_ports: Mapping[str, Mapping[str, Any]],
    sequential_cells: Sequence[str] | frozenset[str],
) -> GateRow:
    """Check a pad-driver output is a single-gate gated clock.

    The check is structural and project-agnostic: the cone feeding the pad
    driver must contain exactly one cell with more than one distinct input, that
    cell must take the clock and one other leaf, and every other cell in the
    cone must be unary.  The check ID comes from the project, so the engine
    carries no project-specific names.
    """
    wrapper_name = f"{derived_port}_b"
    record = wrapper_ports.get(wrapper_name)
    if record is None:
        return GateRow(check_id, "FAIL", f"derived output {derived_port!r} has no wrapper port {wrapper_name!r}", {})
    bits = tuple(record.get("bits", ()))
    if len(bits) != 1:
        return GateRow(check_id, "FAIL", f"derived output {derived_port!r} is not scalar in the wrapper", {})
    clock_record = wrapper_ports.get(clock_port)
    if clock_record is None:
        return GateRow(check_id, "FAIL", f"clock port {clock_port!r} is missing from the wrapper", {})
    clock_bits = tuple(clock_record.get("bits", ()))
    if len(clock_bits) != 1:
        return GateRow(check_id, "FAIL", f"clock port {clock_port!r} is not scalar in the wrapper", {})
    clock_bit = clock_bits[0]
    output_bit = bits[0]
    if bit_key(output_bit) == bit_key(clock_bit):
        return GateRow(check_id, "FAIL", "derived output is the clock net with no gate", {})
    try:
        cone = trace_cone(index, output_bit, sequential_cells)
    except ConnectivityError as error:
        return GateRow(check_id, "FAIL", str(error), {})

    sequential = frozenset(sequential_cells)
    problems: list[str] = []
    if cone.unknown:
        problems.append(f"undriven or unknown cone inputs: {', '.join(cone.unknown)}")
    if cone.constants:
        problems.append(f"constant cone inputs: {', '.join(cone.constants)}")
    if not cone.cells:
        problems.append("cone has no combinational cells")
    if len(cone.multi_input) != 1:
        problems.append(
            f"expected exactly one multi-input gate, found {len(cone.multi_input)}: "
            + ", ".join(cone.multi_input)
        )
    clock_uses = 0
    non_clock_leaves: list[str] = []
    for leaf in cone.leaves:
        if leaf == bit_key(clock_bit):
            continue
        driver_list = index.drivers.get(leaf, ())
        is_sequential = False
        for cell_name, _ in driver_list:
            if str(index.cells[cell_name].get("type", "")) in sequential:
                is_sequential = True
                break
        is_port = any(
            port.get("direction") == "input"
            and leaf in {bit_key(b) for b in port.get("bits", ())}
            for port in index.ports.values()
        )
        if is_sequential or is_port:
            non_clock_leaves.append(leaf)
        else:
            problems.append(f"cone input {leaf} is neither a clock, a port, nor a flip-flop output")
    if bit_key(clock_bit) not in cone.leaves:
        problems.append("clock does not reach the cone")
    if len(non_clock_leaves) != 1:
        problems.append(f"expected exactly one non-clock leaf, found {len(non_clock_leaves)}")
    if len(cone.cells) < 2:
        problems.append("cone has no driver stage between the gate and the pad")

    # Require the clock to be used exactly once, by the single multi-input cell.
    clock_key = bit_key(clock_bit)
    for cell_name in cone.cells:
        for input_bit in index.input_bits(cell_name):
            if bit_key(input_bit) == clock_key:
                clock_uses += 1
    if clock_uses != 1:
        problems.append(f"clock reaches the cone {clock_uses} times, expected once")

    evidence = {
        "derived_port": derived_port,
        "wrapper_port": wrapper_name,
        "output_bit": bit_key(output_bit),
        "clock_bit": clock_key,
        "cone_cells": [
            {
                "name": name,
                "type": str(index.cells[name].get("type", "")),
                "inputs": [bit_key(b) for b in index.input_bits(name)],
                "outputs": [bit_key(b) for b in index.output_bits(name)],
            }
            for name in cone.cells
        ],
        "leaves": list(cone.leaves),
        "multi_input": list(cone.multi_input),
        "clock_uses": clock_uses,
    }
    status = "FAIL" if problems else "PASS"
    message = "; ".join(problems) if problems else (
        f"{derived_port} is a single-gate gated clock through {cone.multi_input[0]}, "
        f"with clock {clock_port} and one non-clock leaf"
    )
    return GateRow(check_id, status, message, evidence)


@dataclass(frozen=True)
class ConnectivityRow:
    id: str
    status: str
    message: str
    details: Mapping[str, Any]


def check_connectivity(index: NetlistIndex, sequential_cells: Sequence[str] | frozenset[str]) -> ConnectivityRow:
    """Reconcile every mapped bit with exactly one driver and known consumers."""
    errors: list[str] = []
    multi_driver = [
        key for key, driver_list in index.drivers.items()
        if len({cell for cell, _ in driver_list}) > 1
    ]
    if multi_driver:
        errors.append(f"{len(multi_driver)} bit(s) have multiple drivers")
    sequential = frozenset(sequential_cells)
    sequential_count = sum(
        1 for cell in index.cells.values() if str(cell.get("type", "")) in sequential
    )
    if sequential_count == 0 and any(
        record.get("direction") == "output"
        for record in index.ports.values()
    ):
        errors.append("no sequential cell remains in a design with outputs")
    output_bits: set[str] = set()
    for port in index.ports.values():
        if port.get("direction") != "output":
            continue
        for bit in port.get("bits", ()):
            output_bits.add(bit_key(bit))
    undriven_outputs = sorted(
        bit for bit in output_bits
        if bit not in index.drivers and not is_constant(json.loads(bit))
    )
    if undriven_outputs:
        errors.append(f"{len(undriven_outputs)} top output bit(s) are undriven")
    unresolved_inputs: list[str] = []
    for key, consumer_list in index.consumers.items():
        if not consumer_list:
            continue
        if key in index.drivers:
            continue
        bit = json.loads(key)
        if is_constant(bit):
            continue
        if any(key in {bit_key(b) for b in port.get("bits", ())} for port in index.ports.values()):
            continue
        unresolved_inputs.append(key)
    if unresolved_inputs:
        errors.append(f"{len(unresolved_inputs)} cell input bit(s) have no driver or port")

    alias_count = sum(1 for names in index.alias_bits.values() if len(names) > 1)
    details = {
        "bits": len(index.alias_bits),
        "cells": len(index.cells),
        "sequential_cells": sequential_count,
        "alias_bits": alias_count,
        "multi_driver_bits": len(multi_driver),
        "undriven_outputs": undriven_outputs,
        "unresolved_inputs": unresolved_inputs,
    }
    status = "FAIL" if errors else "PASS"
    message = "; ".join(errors) if errors else (
        f"{len(index.cells)} cells, {len(index.alias_bits)} bits, {alias_count} aliased bit(s)"
    )
    return ConnectivityRow("synthesis.connectivity", status, message, details)
