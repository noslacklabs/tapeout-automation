"""CDL from tool-resolved bit identities, reconciled with the routed DEF."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .def_model import RoutedDef
from .library_model import CdlSubckt


class CdlError(ValueError):
    """The schematic cannot be represented without losing connectivity."""


def generate_cdl(data: Mapping[str, Any], routed: RoutedDef, top_cell: str,
                 pads: Sequence[Mapping[str, Any]], power_pads: Sequence[Mapping[str, Any]],
                 core: Mapping[str, CdlSubckt], io: Mapping[str, CdlSubckt],
                 pad_models: Mapping[str, Mapping[str, Any]]) -> tuple[str, dict[str, int]]:
    module = data.get("modules", {}).get(routed.design)
    if not isinstance(module, dict):
        raise CdlError(f"routed JSON missing top {routed.design!r}")
    cells, ports = module.get("cells"), module.get("ports")
    if not isinstance(cells, dict) or not isinstance(ports, dict):
        raise CdlError("routed JSON has no cells/ports")
    expected = {c.name: c.master for c in routed.components}
    observed = {name: cell.get("type") for name, cell in cells.items()}
    if expected != observed:
        raise CdlError("DEF/JSON component inventory differs")

    def net(bit):
        if type(bit) is int and bit >= 0:
            return f"n_{bit}"
        if bit == "0":
            return "VSS"
        if bit == "1":
            return "VDD"
        raise CdlError(f"unresolved routed bit {bit!r}")

    port_bits, port_directions = {}, {}
    endpoints = {}
    for name, port in ports.items():
        bits = port.get("bits", [])
        if port.get("direction") not in ("input", "output") or not bits:
            raise CdlError(f"unsupported routed port {name}")
        for index, bit in enumerate(bits):
            canonical = name if len(bits) == 1 else f"{name}[{index + port.get('offset', 0)}]"
            port_bits[canonical] = bit
            port_directions[canonical] = port["direction"]
            endpoints[("PIN", canonical)] = net(bit)
    if set(port_bits) != {p.name for p in routed.pins}:
        raise CdlError("DEF/JSON top pin inventory differs")
    if any(port_directions[p.name] != p.direction for p in routed.pins):
        raise CdlError("DEF/JSON top pin directions differ")
    for name, cell in cells.items():
        master = core.get(cell.get("type"))
        if master is None:
            raise CdlError(f"no CDL model for {cell.get('type')}")
        directions, connections = cell.get("port_directions", {}), cell.get("connections", {})
        if set(connections) != set(directions) or not set(connections) <= set(master.pins):
            raise CdlError(f"incomplete or unknown pin map for {name}")
        for pin in master.pins:
            if pin in ("VDD", "VSS"):
                continue
            bits = connections.get(pin, [])
            if len(bits) == 1:
                endpoints[(name, pin)] = net(bits[0])
            else:
                library_port = data["modules"].get(master.name, {}).get("ports", {}).get(pin, {})
                direction = directions.get(pin) or library_port.get("direction")
                if bits or direction != "output":
                    raise CdlError(f"unconnected or nonscalar input {name}/{pin}")

    def_endpoints = {}
    net_owners = {}
    for record in routed.nets:
        resolved = set()
        for endpoint in record.connections:
            if endpoint not in endpoints:
                raise CdlError(f"DEF has unknown/disconnected pin {endpoint}")
            def_endpoints[endpoint] = record.name
            resolved.add(endpoints[endpoint])
        if len(resolved) != 1:
            raise CdlError(f"DEF net {record.name} shorts different JSON bits or has no endpoints")
        identity = resolved.pop()
        if identity in net_owners:
            raise CdlError(f"JSON bit {identity} split across DEF nets")
        net_owners[identity] = record.name
    if set(endpoints) != set(def_endpoints):
        raise CdlError("DEF omits connected JSON instance/top pins")

    lines = [f"* Written by chipflow for {top_cell}. PRIVATE.", "",
             f".SUBCKT {top_cell} " + " ".join([f"PAD{p['pad']}" for p in pads] + ["VDD", "VSS"])]
    used = set()
    for index, (name, cell) in enumerate(sorted(cells.items())):
        master = core[cell["type"]]
        used.add(master.name)
        values = []
        for pin in master.pins:
            if pin in ("VDD", "VSS"):
                values.append(pin)
            elif (name, pin) in endpoints:
                values.append(endpoints[(name, pin)])
            else:
                values.append(f"nc_{index}_{pin}")
        lines.append(f"X{index} {' '.join(values)} {master.name}")
    mapped = set()
    for pad in pads:
        name = pad["core_port"]
        if name not in port_bits or name in mapped:
            raise CdlError(f"missing or duplicate pad/core mapping {name}")
        mapped.add(name)
        model = pad_models[pad["cell"]]
        master = io[pad["cell"]]
        mapping = {"VDD": "VDD", "VSS": "VSS", model["pad_pin"]: f"PAD{pad['pad']}",
                   model["core_pin"]: net(port_bits[name])}
        if set(master.pins) != set(mapping):
            raise CdlError(f"incomplete CDL pad pin map for {master.name}")
        lines.append(f"XPAD{pad['pad']} " + " ".join(mapping[p] for p in master.pins) + f" {master.name}")
        used.add(master.name)
    if mapped != set(port_bits):
        raise CdlError("physical pad mappings omit routed ports")
    for pad in power_pads:
        master = io[pad["cell"]]
        if set(master.pins) != {"VDD", "VSS"}:
            raise CdlError(f"unknown power-pad pins on {master.name}")
        lines.append(f"XP{pad['slot']} {' '.join(master.pins)} {master.name}")
        used.add(master.name)
    lines.extend([f".ENDS {top_cell}", ""])
    library = dict(core)
    def definition_key(master):
        return tuple(t.upper() if t.lower() in (".subckt", ".ends") else t
                     for t in master.definition.split())

    for name, master in io.items():
        if name in library:
            existing = library[name]
            if (existing.pins != master.pins or existing.body != master.body
                    or definition_key(existing) != definition_key(master)):
                raise CdlError(f"conflicting CDL definitions for {name}")
        library[name] = master
    emitted = set()
    pending = sorted(used)
    while pending:
        name = pending.pop(0)
        if name in emitted:
            continue
        master = library[name]
        emitted.add(name)
        if master.definition:
            # Preserve PDK header parameters and primitive models verbatim.
            lines.extend([master.definition.strip(), ""])
        else:
            lines.extend([f".SUBCKT {name} {' '.join(master.pins)}", master.body.strip(), f".ENDS {name}", ""])
        for line in master.body.splitlines():
            tokens = line.split()
            if tokens and tokens[0][0].upper() == "X":
                positional = [t for t in tokens if "=" not in t]
                child = positional[-1]
                if child not in library:
                    raise CdlError(f"undefined CDL subcircuit {child} in {name}")
                pending.append(child)
    return "\n".join(lines) + "\n", {
        "core_instances": len(cells), "signal_pads": len(pads),
        "power_pads": len(power_pads), "reconciled_endpoints": len(endpoints)}
