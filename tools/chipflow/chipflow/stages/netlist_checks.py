"""Checks for gate-level mapped netlist (Plan M2.2 / G8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from chipflow.runner.results import CheckStatus


@dataclass(frozen=True)
class NetlistCheckRow:
    id: str
    status: CheckStatus
    message: str


def check_mapped_netlist(
    data: Mapping[str, Any],
    top: str,
    allowed_cells: Sequence[str] | set[str] | frozenset[str],
    sequential_cells: Sequence[str] | set[str] | frozenset[str],
    clock_port: str,
) -> tuple[NetlistCheckRow, ...]:
    """Validate mapped netlist cells, clocks, and constant ties."""
    rows: list[NetlistCheckRow] = []
    modules = data.get("modules", {})
    if top not in modules:
        rows.append(NetlistCheckRow("netlist.top", CheckStatus.FAIL, f"top module {top!r} not found"))
        return tuple(rows)

    mod = modules[top]
    ports = mod.get("ports", {})
    cells = mod.get("cells", {})

    allowed_set = frozenset(allowed_cells)
    seq_set = frozenset(sequential_cells)

    # 1. No $ cells remain
    dollar_cells = [c.get("type", "") for c in cells.values() if str(c.get("type", "")).startswith("$")]
    if dollar_cells:
        rows.append(NetlistCheckRow(
            "netlist.primitives",
            CheckStatus.FAIL,
            f"unmapped primitives remain: {', '.join(sorted(set(dollar_cells)))}",
        ))
    else:
        rows.append(NetlistCheckRow("netlist.primitives", CheckStatus.PASS, "no unmapped '$' primitives"))

    # 2. Every cell in allowed_cells
    unsupported = [c.get("type", "") for c in cells.values() if c.get("type", "") not in allowed_set]
    if unsupported:
        rows.append(NetlistCheckRow(
            "netlist.allowed_cells",
            CheckStatus.FAIL,
            f"unsupported or excluded cells: {', '.join(sorted(set(unsupported)))}",
        ))
    else:
        rows.append(NetlistCheckRow("netlist.allowed_cells", CheckStatus.PASS, "all cells are in the allowlist"))

    # 3. Every sequential cell's clock pin is on the clock port bit
    if clock_port not in ports or not ports[clock_port].get("bits"):
        rows.append(NetlistCheckRow("netlist.sequential_clock", CheckStatus.FAIL, f"clock port {clock_port!r} not found"))
    else:
        clock_bit = ports[clock_port]["bits"][0]
        clock_errors: list[str] = []
        seq_count = 0
        for cname, cdata in cells.items():
            ctype = cdata.get("type", "")
            if ctype in seq_set:
                seq_count += 1
                conns = cdata.get("connections", {})
                clk_pin = "C" if "C" in conns else "CLK" if "CLK" in conns else None
                if not clk_pin:
                    clock_errors.append(f"{cname} ({ctype}): missing clock pin")
                else:
                    pin_bits = conns.get(clk_pin, [])
                    if not pin_bits or pin_bits[0] != clock_bit:
                        clock_errors.append(f"{cname} ({ctype}) pin {clk_pin} not connected to {clock_port}")

        if clock_errors:
            rows.append(NetlistCheckRow(
                "netlist.sequential_clock",
                CheckStatus.FAIL,
                f"sequential clock violations: {'; '.join(clock_errors)}",
            ))
        else:
            rows.append(NetlistCheckRow(
                "netlist.sequential_clock",
                CheckStatus.PASS,
                f"all {seq_count} sequential cells clocked by {clock_port}",
            ))

    # 4. Constants come only from tie cells VDDCON (pin TIEHI) and VSSCON (pin TIELO) (P04)
    tie_cell_types = {"VDDCON", "VSSCON"}
    constant_errors: list[str] = []
    for cname, cdata in cells.items():
        ctype = cdata.get("type", "")
        if ctype in tie_cell_types:
            continue
        conns = cdata.get("connections", {})
        for pin_name, bits in conns.items():
            for b in bits:
                if b in ("0", "1"):
                    constant_errors.append(f"{cname} ({ctype}) pin {pin_name} tied directly to '{b}'")
                    break

    if constant_errors:
        rows.append(NetlistCheckRow(
            "netlist.constants",
            CheckStatus.FAIL,
            f"direct constant ties detected (violates P04): {'; '.join(constant_errors)}",
        ))
    else:
        rows.append(NetlistCheckRow("netlist.constants", CheckStatus.PASS, "no direct constant ties (P04 compliant)"))

    return tuple(rows)
