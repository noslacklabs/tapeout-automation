"""Strict, bounded parsers for the PDK library views used by physical stages.

The engine never trusts a tool report alone.  These parsers have fixed size and
nesting limits and reject malformed or ambiguous records rather than skipping
them.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping


MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_GROUPS = 10000
MAX_DEPTH = 64


class LibraryParseError(ValueError):
    """A library view is malformed, truncated or outside the supported subset."""


def _read_bounded(path) -> str:
    from pathlib import Path

    data = Path(path).read_bytes()
    if len(data) > MAX_TEXT_BYTES:
        raise LibraryParseError(f"library view exceeds {MAX_TEXT_BYTES} bytes: {path}")
    return data.decode("utf-8", errors="ignore").replace("\r", "")


def _strip_liberty_comments(text: str) -> str:
    # Liberty comments are C-style blocks and // lines.  Remove blocks before
    # line comments so a // inside a removed block cannot hide following text.
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", " ", text)
    return text


def _matching_brace(text: str, start: int) -> int:
    depth = 1
    i = start
    while depth:
        ch = text[i]
        depth += (ch == "{") - (ch == "}")
        i += 1
        if i - start > MAX_TEXT_BYTES:
            raise LibraryParseError("unbounded group body")
    return i


@dataclass(frozen=True)
class LibertyPin:
    name: str
    direction: str | None
    function: str | None
    capacitance_pf: float | None
    max_capacitance_pf: float | None
    is_power: bool


@dataclass(frozen=True)
class LibertyCell:
    name: str
    pins: Mapping[str, LibertyPin]
    sequential: bool


@dataclass(frozen=True)
class LibertyLibrary:
    cells: Mapping[str, LibertyCell]

    def allowed_cells(self) -> frozenset[str]:
        return frozenset(self.cells)

    def sequential_cells(self) -> frozenset[str]:
        return frozenset(name for name, cell in self.cells.items() if cell.sequential)


_CELL_START = re.compile(r'\bcell\s*\(\s*"?([A-Za-z_][A-Za-z0-9_$]*)"?\s*\)\s*\{')
_PIN_START = re.compile(r'\bpin\s*\(\s*"?([A-Za-z_][A-Za-z0-9_$]*)"?\s*\)\s*\{')


def _number(value: str) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number


def parse_liberty(path) -> LibertyLibrary:
    text = _strip_liberty_comments(_read_bounded(path))
    cells: dict[str, LibertyCell] = {}
    pos = 0
    while True:
        match = _CELL_START.search(text, pos)
        if match is None:
            break
        if len(cells) >= MAX_GROUPS:
            raise LibraryParseError("too many Liberty cells")
        name = match.group(1)
        if name in cells:
            raise LibraryParseError(f"duplicate Liberty cell: {name}")
        end = _matching_brace(text, match.end())
        body = text[match.end():end - 1]
        pins: dict[str, LibertyPin] = {}
        pin_pos = 0
        while True:
            pin_match = _PIN_START.search(body, pin_pos)
            if pin_match is None:
                break
            pin_name = pin_match.group(1)
            pin_end = _matching_brace(body, pin_match.end())
            pin_body = body[pin_match.end():pin_end - 1]
            direction_match = re.search(r"\bdirection\s*:\s*\"?([A-Za-z]+)\"?", pin_body)
            function_match = re.search(r'\bfunction\s*:\s*"([^"]+)"', pin_body)
            cap_match = re.search(r"\bcapacitance\s*:\s*([0-9.eE+-]+)", pin_body)
            maxcap_match = re.search(r"\bmax_capacitance\s*:\s*([0-9.eE+-]+)", pin_body)
            capacitance = _number(cap_match.group(1)) if cap_match else None
            maxcap = _number(maxcap_match.group(1)) if maxcap_match else None
            if cap_match and capacitance is None:
                raise LibraryParseError(f"invalid capacitance in {name}/{pin_name}")
            if maxcap_match and maxcap is None:
                raise LibraryParseError(f"invalid max_capacitance in {name}/{pin_name}")
            is_power = bool(re.search(r"\bpg_pin\s*\(", pin_body)) or (
                direction_match is None and function_match is None and cap_match is None
            )
            if pin_name in pins:
                raise LibraryParseError(f"duplicate pin {pin_name} in Liberty cell {name}")
            pins[pin_name] = LibertyPin(
                name=pin_name,
                direction=direction_match.group(1) if direction_match else None,
                function=function_match.group(1) if function_match else None,
                capacitance_pf=capacitance,
                max_capacitance_pf=maxcap,
                is_power=is_power,
            )
            pin_pos = pin_end
        cells[name] = LibertyCell(
            name=name,
            pins=pins,
            sequential=bool(re.search(r"\b(ff|ff_bank|latch|statetable)\s*\(", body)),
        )
        pos = end
    if not cells:
        raise LibraryParseError(f"no Liberty cells in {path}")
    return LibertyLibrary(cells)


@dataclass(frozen=True)
class CdlSubckt:
    name: str
    pins: tuple[str, ...]
    body: str


_CDL_START = re.compile(r"(?im)^\s*\.(?:subckt|SUBCKT)\s+([A-Za-z_][A-Za-z0-9_$]*)([^\n]*)$")
_CDL_END = re.compile(r"(?im)^\s*\.(?:ends|ENDS)\b[^\n]*$")


def parse_cdl(path) -> Mapping[str, CdlSubckt]:
    text = _read_bounded(path)
    result: dict[str, CdlSubckt] = {}
    pos = 0
    while True:
        start = _CDL_START.search(text, pos)
        if start is None:
            break
        if len(result) >= MAX_GROUPS:
            raise LibraryParseError("too many CDL subcircuits")
        name = start.group(1)
        end = _CDL_END.search(text, start.end())
        if end is None:
            raise LibraryParseError(f"unterminated CDL subcircuit: {name}")
        if name in result:
            raise LibraryParseError(f"duplicate CDL subcircuit: {name}")
        header_tokens = start.group(2).split()
        pins = tuple(token for token in header_tokens if "=" not in token)
        result[name] = CdlSubckt(name=name, pins=pins, body=text[start.end():end.start()])
        pos = end.end()
    if not result:
        raise LibraryParseError(f"no CDL subcircuits in {path}")
    return result


_MODULE_HEADER = re.compile(
    r"(?m)^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(?:#\s*\([^;]*?\)\s*)?\(",
    re.S,
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
_DIRECTIONS = {"input", "output", "inout"}


def parse_verilog_ports(path) -> Mapping[str, tuple[str, ...]]:
    """Return module name -> ordered port names from ANSI or non-ANSI headers."""
    text = re.sub(r"/\*.*?\*/", " ", _read_bounded(path), flags=re.S)
    text = re.sub(r"(?m)//.*$", " ", text)
    result: dict[str, tuple[str, ...]] = {}
    pos = 0
    while True:
        match = _MODULE_HEADER.search(text, pos)
        if match is None:
            break
        name = match.group(1)
        depth = 1
        i = match.end()
        while depth and i < len(text):
            ch = text[i]
            depth += (ch == "(") - (ch == ")")
            i += 1
        if depth:
            raise LibraryParseError(f"unterminated module header: {name}")
        header = text[match.end():i - 1]
        # Remove parameter overrides and simple ranges; port entries may be ANSI
        # declarations ("input [3:0] a") or plain names.
        header = re.sub(r"#\s*\([^)]*\)", " ", header, flags=re.S)
        ports: list[str] = []
        for raw in header.split(","):
            entry = raw.strip()
            if not entry:
                continue
            entry = re.sub(r"\[[^\]]*\]", " ", entry)
            tokens = [token.strip() for token in entry.split() if token.strip()]
            tokens = [token for token in tokens if token not in _DIRECTIONS and token not in {"wire", "reg", "logic", "signed", "unsigned"}]
            if len(tokens) != 1 or not _IDENTIFIER.match(tokens[0]):
                raise LibraryParseError(f"unsupported port declaration in {name}: {raw!r}")
            ports.append(tokens[0])
        if name in result:
            raise LibraryParseError(f"duplicate Verilog module: {name}")
        if len(set(ports)) != len(ports):
            raise LibraryParseError(f"duplicate port in Verilog module: {name}")
        result[name] = tuple(ports)
        pos = i
    if not result:
        raise LibraryParseError(f"no Verilog modules in {path}")
    return result


@dataclass(frozen=True)
class LefMacro:
    name: str
    width_um: float
    height_um: float
    pin_pins: Mapping[str, tuple[tuple[float, float], ...]]


_LEF_MACRO = re.compile(r"(?m)^\s*MACRO\s+([A-Za-z_][A-Za-z0-9_$]*)\s*$")


def parse_lef_macros(path) -> Mapping[str, LefMacro]:
    text = _read_bounded(path)
    macros: dict[str, LefMacro] = {}
    current: str | None = None
    width = height = None
    pin = None
    pins: dict[str, list[tuple[float, float]]] = {}
    for line in text.splitlines():
        token = line.split()
        if not token:
            continue
        key = token[0].upper()
        if key == "MACRO" and len(token) >= 2:
            current = token[1]
            if current in macros:
                raise LibraryParseError(f"duplicate LEF macro: {current}")
            width = height = None
            pin = None
            pins = {}
        elif key == "SIZE" and current and len(token) >= 4 and token[2].upper() == "BY":
            width, height = float(token[1]), float(token[3].rstrip(";"))
        elif key == "PIN" and current and len(token) >= 2:
            pin = token[1]
        elif key == "OBS":
            pin = None
        elif key == "END" and current and len(token) >= 2 and token[1] == current:
            if width is None or height is None:
                raise LibraryParseError(f"LEF macro missing SIZE: {current}")
            macros[current] = LefMacro(current, width, height, {k: tuple(v) for k, v in pins.items()})
            current = None
        elif key == "RECT" and current and pin and len(token) >= 5:
            x1, y1, x2, y2 = (float(v.rstrip(";")) for v in token[1:5])
            pins.setdefault(pin, []).append(((x1 + x2) / 2.0, (y1 + y2) / 2.0))
    if current is not None:
        raise LibraryParseError(f"unterminated LEF macro: {current}")
    if not macros:
        raise LibraryParseError(f"no LEF macros in {path}")
    return macros
