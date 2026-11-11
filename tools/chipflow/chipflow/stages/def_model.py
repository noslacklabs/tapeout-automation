"""Bounded reader for the routed, Manhattan DEF subset emitted by this profile.

Every connectivity and geometry record is consumed completely. Unsupported
syntax is an error, never an empty geometry result.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


class DefError(ValueError):
    """A DEF is incomplete or outside the qualified routing subset."""


@dataclass(frozen=True)
class Component:
    name: str
    master: str
    x: int
    y: int


@dataclass(frozen=True)
class Pin:
    name: str
    net: str
    direction: str
    layer: str
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class Wire:
    net: str
    layer: str
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class Via:
    net: str
    name: str
    x: int
    y: int


@dataclass(frozen=True)
class Net:
    name: str
    connections: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RoutedDef:
    design: str
    components: tuple[Component, ...]
    pins: tuple[Pin, ...]
    nets: tuple[Net, ...]
    wires: tuple[Wire, ...]
    vias: tuple[Via, ...]


WIDTHS = {"metal1": 2400, "metal2": 2500}
_TOKEN = re.compile(r"[();+]|[^\s();+]+")
_INT = r"-?\d+"


def _coordinate(value: str, grid: int) -> int:
    if not re.fullmatch(_INT, value):
        raise DefError(f"invalid DEF coordinate {value!r}")
    number = int(value)
    if number % grid:
        raise DefError(f"off-grid DEF coordinate {number}, grid {grid}")
    return number


def _records(text: str, name: str, *, required: bool = True) -> tuple[list[str], str]:
    starts = list(re.finditer(rf"(?m)^\s*{name}\s+(\d+)\s*;", text))
    if not starts and not required:
        return [], text
    if len(starts) != 1:
        raise DefError(f"expected exactly one {name} section")
    start = starts[0]
    end = re.search(rf"(?m)^\s*END\s+{name}\s*$", text[start.end():])
    if end is None:
        raise DefError(f"unterminated {name} section")
    stop = start.end() + end.start()
    body = text[start.end():stop].strip()
    if body and not body.endswith(";"):
        raise DefError(f"unterminated {name} record")
    records = [r.strip() for r in body.split(";") if r.strip()]
    if len(records) != int(start.group(1)) or any(not r.startswith("- ") for r in records):
        raise DefError(f"{name} declared {start.group(1)} records, parsed {len(records)}")
    finish = start.end() + end.end()
    return records, text[:start.start()] + "\n" + text[finish:]


def read_def(path: Path | str, *, require_pins: bool = False,
             expected_dbu: int = 1000, grid: int = 5) -> RoutedDef:
    raw = Path(path).read_bytes()
    if len(raw) > 64 * 1024 * 1024:
        raise DefError("DEF exceeds 64 MiB")
    text = re.sub(r"(?m)#.*$", "", raw.decode("utf-8"))
    units = re.findall(r"\bUNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", text)
    if units != [str(expected_dbu)]:
        raise DefError(f"DEF units {units} do not match expected DBU {expected_dbu}")
    designs = re.findall(r"(?m)^\s*DESIGN\s+(\S+)\s*;", text)
    if len(designs) != 1 or not re.search(r"\bEND\s+DESIGN\s*\Z", text):
        raise DefError("DEF requires one DESIGN and a final END DESIGN")

    component_records, remaining = _records(text, "COMPONENTS")
    pin_records, remaining = _records(remaining, "PINS", required=require_pins)
    net_records, remaining = _records(remaining, "NETS")
    ndr_records, remaining = _records(remaining, "NONDEFAULTRULES", required=False)
    blockage_records, remaining = _records(remaining, "BLOCKAGES", required=False)
    ndr_names = set()
    for record in ndr_records:
        match = re.fullmatch(r"-\s+(\S+)((?:\s+\+\s+LAYER\s+\S+\s+WIDTH\s+\d+\s+SPACING\s+\d+)+)", record)
        if match is None or match[1] in ndr_names:
            raise DefError(f"unsupported NONDEFAULTRULES record: {record[:160]}")
        ndr_names.add(match[1])
        for layer, width, spacing in re.findall(r"\+\s+LAYER\s+(\S+)\s+WIDTH\s+(\d+)\s+SPACING\s+(\d+)", match[2]):
            if WIDTHS.get(layer) != int(width) or int(spacing) <= 0:
                raise DefError(f"unsupported NDR layer/width/spacing: {layer} {width} {spacing}")
    for record in blockage_records:
        if not re.fullmatch(rf"-\s+LAYER\s+(?:metal1|metal2)\s+RECT\s+\(\s*{_INT}\s+{_INT}\s*\)\s+\(\s*{_INT}\s+{_INT}\s*\)", record):
            raise DefError(f"unsupported BLOCKAGES record: {record[:160]}")
    # These declarations do not add final wires or connectivity. All other
    # sections (including SPECIALNETS/VIAS) must be added deliberately.
    remaining = re.sub(r"END\s+DESIGN\s*\Z", "", remaining)
    for statement in remaining.split(";"):
        statement = statement.strip()
        if not statement:
            continue
        patterns = (
            r"VERSION\s+5\.[78]", r'DIVIDERCHAR\s+"/"', r'BUSBITCHARS\s+"\[\]"',
            r"DESIGN\s+\S+", r"UNITS\s+DISTANCE\s+MICRONS\s+\d+",
            rf"DIEAREA\s+\(\s*{_INT}\s+{_INT}\s*\)\s+\(\s*{_INT}\s+{_INT}\s*\)",
            rf"ROW\s+\S+\s+\S+\s+{_INT}\s+{_INT}\s+N\s+DO\s+\d+\s+BY\s+1\s+STEP\s+\d+\s+0",
            r"TRACKS\s+[XY]\s+\d+\s+DO\s+\d+\s+STEP\s+\d+\s+LAYER\s+(?:metal1|metal2)",
            r"GCELLGRID\s+[XY]\s+\d+\s+DO\s+\d+\s+STEP\s+\d+",
        )
        if not any(re.fullmatch(pattern, statement) for pattern in patterns):
            raise DefError(f"unsupported DEF declaration: {statement[:160]}")

    components = []
    names = set()
    for record in component_records:
        match = re.fullmatch(rf"-\s+(\S+)\s+(\S+)(?:\s+\+\s+SOURCE\s+(?:NETLIST|DIST|USER|TIMING))?\s+\+\s+(?:PLACED|FIXED|LOCKED)\s+\(\s*({_INT})\s+({_INT})\s*\)\s+N", record)
        if match is None:
            raise DefError(f"unsupported/unplaced component: {record[:160]}")
        name, master, x, y = match.groups()
        if name in names:
            raise DefError(f"duplicate component {name}")
        names.add(name)
        components.append(Component(name, master, _coordinate(x, grid), _coordinate(y, grid)))

    pins = []
    pin_names = set()
    for record in pin_records:
        match = re.fullmatch(
            rf"-\s+(\S+)\s+\+\s+NET\s+(\S+)\s+\+\s+DIRECTION\s+(INPUT|OUTPUT)"
            rf"\s+\+\s+USE\s+SIGNAL\s+\+\s+PORT\s+\+\s+LAYER\s+(metal1|metal2)"
            rf"\s+\(\s*({_INT})\s+({_INT})\s*\)\s+\(\s*({_INT})\s+({_INT})\s*\)"
            rf"\s+\+\s+(?:PLACED|FIXED|COVER)\s+\(\s*({_INT})\s+({_INT})\s*\)\s+N", record)
        if match is None:
            raise DefError(f"unsupported pin record: {record[:160]}")
        name, net, direction, layer, *coords = match.groups()
        if name in pin_names:
            raise DefError(f"duplicate pin {name}")
        pin_names.add(name)
        a, b, c, d, x, y = (_coordinate(v, grid) for v in coords)
        if a >= c or b >= d:
            raise DefError(f"invalid rectangle on pin {name}")
        pins.append(Pin(name, net, direction.lower(), layer, (x + a, y + b, x + c, y + d)))

    nets, wires, vias = [], [], []
    net_names = set()
    used_connections = set()
    for record in net_records:
        tokens = _TOKEN.findall(record)
        if len(tokens) < 2 or tokens[0] != "-":
            raise DefError("invalid net record")
        name = tokens[1]
        if name in net_names:
            raise DefError(f"duplicate net {name}")
        net_names.add(name)
        i = 2
        connections = []
        while i < len(tokens) and tokens[i] == "(":
            if i + 3 >= len(tokens) or tokens[i + 3] != ")":
                raise DefError(f"invalid connection on net {name}")
            instance, pin = tokens[i + 1:i + 3]
            if (instance == "PIN" and pin not in pin_names) or (instance != "PIN" and instance not in names):
                raise DefError(f"net {name} references unknown component/pin {instance}/{pin}")
            if (instance, pin) in used_connections:
                raise DefError(f"duplicate connection {instance}/{pin}")
            used_connections.add((instance, pin))
            connections.append((instance, pin))
            i += 4
        while i < len(tokens):
            if tokens[i] != "+" or i + 1 >= len(tokens):
                raise DefError(f"unsupported net token on {name}: {tokens[i:]}")
            keyword = tokens[i + 1]
            i += 2
            if keyword in ("USE", "NONDEFAULTRULE"):
                if i >= len(tokens) or (keyword == "USE" and tokens[i] not in ("SIGNAL", "CLOCK")) or (keyword == "NONDEFAULTRULE" and tokens[i] not in ndr_names):
                    raise DefError(f"unsupported {keyword} on net {name}")
                i += 1
                continue
            if keyword not in ("ROUTED", "FIXED", "COVER"):
                raise DefError(f"unsupported net property {keyword} on {name}")
            while True:
                if i >= len(tokens) or tokens[i] not in WIDTHS:
                    raise DefError(f"unsupported route layer on {name}")
                layer = tokens[i]
                i += 1
                point = None
                extension = None
                events = 0
                if i < len(tokens) and tokens[i] == "TAPER":
                    i += 1
                while i < len(tokens) and tokens[i] not in ("NEW", "+"):
                    token = tokens[i]
                    if token == "(":
                        try:
                            end = tokens.index(")", i + 1)
                        except ValueError as error:
                            raise DefError(f"unterminated point on {name}") from error
                        values = tokens[i + 1:end]
                        if len(values) not in (2, 3):
                            raise DefError(f"unsupported point on {name}: {values}")
                        xy = []
                        for axis, value in enumerate(values[:2]):
                            if value == "*":
                                if point is None:
                                    raise DefError(f"wildcard without previous point on {name}")
                                xy.append(point[axis])
                            else:
                                xy.append(_coordinate(value, grid))
                        ext = _coordinate(values[2], grid) if len(values) == 3 else None
                        if ext is not None and ext < 0:
                            raise DefError(f"negative route extension on {name}")
                        x, y = xy
                        if point is not None:
                            px, py = point
                            if x != px and y != py:
                                raise DefError(f"non-Manhattan segment on {name}")
                            h = WIDTHS[layer] // 2
                            e0, e1 = h if extension is None else extension, h if ext is None else ext
                            if x == px:
                                lo, hi = sorted((py, y))
                                a, b = (e0, e1) if py <= y else (e1, e0)
                                box = (x - h, lo - a, x + h, hi + b)
                            else:
                                lo, hi = sorted((px, x))
                                a, b = (e0, e1) if px <= x else (e1, e0)
                                box = (lo - a, y - h, hi + b, y + h)
                            wires.append(Wire(name, layer, box))
                            events += 1
                        point, extension, i = (x, y), ext, end + 1
                    elif token == "VIA12" and point is not None:
                        vias.append(Via(name, token, *point))
                        layer = "metal2" if layer == "metal1" else "metal1"
                        extension = None
                        i += 1
                        events += 1
                    elif token == "RECT" and point is not None:
                        if i + 6 >= len(tokens) or tokens[i + 1] != "(" or tokens[i + 6] != ")":
                            raise DefError(f"invalid routed rectangle on {name}")
                        a, b, c, d = (_coordinate(v, grid) for v in tokens[i + 2:i + 6])
                        if a >= c or b >= d:
                            raise DefError(f"invalid routed rectangle bounds on {name}")
                        wires.append(Wire(name, layer, (point[0] + a, point[1] + b, point[0] + c, point[1] + d)))
                        i += 7
                        events += 1
                    else:
                        raise DefError(f"unsupported route token {token!r} on {name}")
                if not events:
                    raise DefError(f"route contains no geometry on {name}")
                if i < len(tokens) and tokens[i] == "NEW":
                    i += 1
                    continue
                break
        nets.append(Net(name, tuple(connections)))
    for pin in pins:
        if pin.net not in net_names or ("PIN", pin.name) not in used_connections:
            raise DefError(f"pin {pin.name} is absent from its declared net {pin.net}")
        net = next(n for n in nets if n.name == pin.net)
        if ("PIN", pin.name) not in net.connections:
            raise DefError(f"pin {pin.name} is connected to the wrong net")
    return RoutedDef(designs[0], tuple(components), tuple(pins), tuple(nets), tuple(wires), tuple(vias))
