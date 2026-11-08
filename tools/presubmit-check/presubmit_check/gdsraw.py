"""Strict, read-only GDSII stream parser in pure Python.

KLayout hides ``$$$CONTEXT_INFO$$$`` and gdstk normalises coordinates to floats,
so the top-cell and grid checks read the raw integer records here instead.

Input is treated as untrusted: nothing is evaluated, and every structural problem
is recorded as an issue with its byte offset rather than being repaired.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from fractions import Fraction

RECORD_NAMES = {
    0x00: "HEADER", 0x01: "BGNLIB", 0x02: "LIBNAME", 0x03: "UNITS", 0x04: "ENDLIB",
    0x05: "BGNSTR", 0x06: "STRNAME", 0x07: "ENDSTR", 0x08: "BOUNDARY", 0x09: "PATH",
    0x0A: "SREF", 0x0B: "AREF", 0x0C: "TEXT", 0x0D: "LAYER", 0x0E: "DATATYPE",
    0x0F: "WIDTH", 0x10: "XY", 0x11: "ENDEL", 0x12: "SNAME", 0x13: "COLROW",
    0x14: "TEXTNODE", 0x15: "NODE", 0x16: "TEXTTYPE", 0x17: "PRESENTATION",
    0x18: "SPACING", 0x19: "STRING", 0x1A: "STRANS", 0x1B: "MAG", 0x1C: "ANGLE",
    0x1D: "UINTEGER", 0x1E: "USTRING", 0x1F: "REFLIBS", 0x20: "FONTS", 0x21: "PATHTYPE",
    0x22: "GENERATIONS", 0x23: "ATTRTABLE", 0x24: "STYPTABLE", 0x25: "STRTYPE",
    0x26: "ELFLAGS", 0x27: "ELKEY", 0x28: "LINKTYPE", 0x29: "LINKKEYS", 0x2A: "NODETYPE",
    0x2B: "PROPATTR", 0x2C: "PROPVALUE", 0x2D: "BOX", 0x2E: "BOXTYPE", 0x2F: "PLEX",
    0x30: "BGNEXTN", 0x31: "ENDEXTN", 0x32: "TAPENUM", 0x33: "TAPECODE", 0x34: "STRCLASS",
    0x35: "RESERVED", 0x36: "FORMAT", 0x37: "MASK", 0x38: "ENDMASKS", 0x39: "LIBDIRSIZE",
    0x3A: "SRFNAME", 0x3B: "LIBSECUR",
}

# Data type each record must carry: 0 none, 1 bit array, 2 int16, 3 int32, 5 real8, 6 ascii.
# Records that the GDSII spec lists as unused (SPACING, UINTEGER, USTRING, ...) may hold anything.
RECORD_DTYPES = {
    "HEADER": 2, "BGNLIB": 2, "LIBNAME": 6, "UNITS": 5, "ENDLIB": 0, "BGNSTR": 2, "STRNAME": 6,
    "ENDSTR": 0, "BOUNDARY": 0, "PATH": 0, "SREF": 0, "AREF": 0, "TEXT": 0, "LAYER": 2,
    "DATATYPE": 2, "WIDTH": 3, "XY": 3, "ENDEL": 0, "SNAME": 6, "COLROW": 2, "TEXTNODE": 0,
    "NODE": 0, "TEXTTYPE": 2, "PRESENTATION": 1, "STRING": 6, "STRANS": 1, "MAG": 5, "ANGLE": 5,
    "REFLIBS": 6, "FONTS": 6, "PATHTYPE": 2, "GENERATIONS": 2, "ATTRTABLE": 6, "STRTYPE": 2,
    "ELFLAGS": 1, "ELKEY": 3, "NODETYPE": 2, "PROPATTR": 2, "PROPVALUE": 6, "BOX": 0, "BOXTYPE": 2,
    "PLEX": 3, "BGNEXTN": 3, "ENDEXTN": 3, "TAPENUM": 2, "TAPECODE": 2, "STRCLASS": 1, "FORMAT": 2,
    "MASK": 6, "ENDMASKS": 0, "LIBDIRSIZE": 2, "SRFNAME": 6, "LIBSECUR": 2,
}

ELEMENT_RECORDS = {"BOUNDARY", "PATH", "SREF", "AREF", "TEXT", "NODE", "BOX"}
ELEMENT_FIELDS = {"LAYER", "DATATYPE", "TEXTTYPE", "BOXTYPE", "NODETYPE", "PATHTYPE", "WIDTH", "XY", "SNAME",
                  "STRING", "STRANS", "MAG", "ANGLE", "COLROW", "BGNEXTN", "ENDEXTN"}


@dataclass(slots=True)
class Shape:
    """A BOUNDARY or BOX. ``pts`` omits the closing vertex."""

    layer: int
    datatype: int
    pts: list[tuple[int, int]]
    kind: str = "boundary"


@dataclass(slots=True)
class PathEl:
    layer: int
    datatype: int
    pts: list[tuple[int, int]]
    width: int = 0
    pathtype: int = 0
    bgnextn: int = 0
    endextn: int = 0


@dataclass(slots=True)
class TextEl:
    layer: int
    texttype: int
    x: int
    y: int
    string: str


@dataclass(slots=True)
class NodeEl:
    layer: int
    nodetype: int
    pts: list[tuple[int, int]]


@dataclass(slots=True)
class Ref:
    """An SREF, or an AREF when ``cols``/``rows`` are above 1 or ``is_array`` is set."""

    name: str
    x: int
    y: int
    mirror: bool = False
    mag: float = 1.0
    angle: float = 0.0
    is_array: bool = False
    cols: int = 1
    rows: int = 1
    col_pt: tuple[int, int] = (0, 0)
    row_pt: tuple[int, int] = (0, 0)
    absolute: bool = False

    def origins(self):
        """Yield every placement origin, expanding an AREF lattice."""
        if not self.is_array:
            yield (self.x, self.y)
            return
        dcx, dcy = self.col_pt[0] - self.x, self.col_pt[1] - self.y
        drx, dry = self.row_pt[0] - self.x, self.row_pt[1] - self.y
        for j in range(self.rows):
            for i in range(self.cols):
                yield (
                    self.x + (i * dcx) // self.cols + (j * drx) // self.rows,
                    self.y + (i * dcy) // self.cols + (j * dry) // self.rows,
                )


@dataclass(slots=True)
class Cell:
    name: str
    offset: int = 0
    shapes: list[Shape] = field(default_factory=list)
    paths: list[PathEl] = field(default_factory=list)
    texts: list[TextEl] = field(default_factory=list)
    nodes: list[NodeEl] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)


@dataclass(slots=True)
class Issue:
    offset: int
    message: str

    def __str__(self) -> str:
        return f"byte {self.offset}: {self.message}"


@dataclass
class Library:
    path: str = ""
    version: int | None = None
    libname: str = ""
    uu_per_dbu: float | None = None
    dbu_m: float | None = None
    nm_per_dbu: Fraction = Fraction(1)
    cells: dict[str, Cell] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    fatal: bool = False
    bytes_read: int = 0

    @property
    def valid(self) -> bool:
        return not self.issues and not self.fatal

    def nm(self, value: int) -> int:
        """Convert a raw database value to whole nanometres, rounding if necessary."""
        if self.nm_per_dbu == 1:
            return value
        return round(value * self.nm_per_dbu)

    def on_grid(self, value: int, grid_nm: int) -> bool:
        """Exact test that a raw value is an integer multiple of ``grid_nm``."""
        scale = self.nm_per_dbu
        return (value * scale.numerator) % (grid_nm * scale.denominator) == 0

    def children_of(self, cell: Cell) -> set[str]:
        return {r.name for r in cell.refs}

    def top_cells(self, ignore: str | None = None) -> list[str]:
        """Cells nothing references. References made by ``ignore`` do not count."""
        referenced: set[str] = set()
        for cell in self.cells.values():
            if cell.name != ignore:
                referenced |= self.children_of(cell)
        return [n for n in self.cells if n not in referenced and n != ignore]


def real8(raw: bytes) -> float:
    """Decode an excess-64, base-16 GDSII real."""
    value = int.from_bytes(raw, "big")
    sign = -1.0 if value >> 63 else 1.0
    exponent = (value >> 56) & 0x7F
    mantissa = value & ((1 << 56) - 1)
    return sign * mantissa * (16.0 ** (exponent - 64 - 14))


def _ascii(raw: bytes) -> str:
    return raw.rstrip(b"\x00").decode("latin-1")


def _points(raw: bytes) -> list[tuple[int, int]]:
    count = len(raw) // 4
    values = struct.unpack(f">{count}i", raw[: count * 4])
    return list(zip(values[0::2], values[1::2]))


def parse_gds(data: bytes, path: str = "") -> Library:
    """Parse a GDSII stream. Problems are recorded on the returned Library."""
    return _Parser(data, path).run()


def read_gds(path) -> Library:
    """Open ``path`` read-only and parse it."""
    with open(path, "rb") as handle:
        data = handle.read()
    return parse_gds(data, str(path))


class _Parser:
    def __init__(self, data: bytes, path: str):
        self.data = data
        self.lib = Library(path=path)
        self.cell: Cell | None = None
        self.el: dict | None = None
        self.ended = False

    def issue(self, offset: int, message: str) -> None:
        self.lib.issues.append(Issue(offset, message))

    def fatal(self, offset: int, message: str) -> None:
        self.issue(offset, message)
        self.lib.fatal = True

    def run(self) -> Library:
        data, lib = self.data, self.lib
        size = len(data)
        if size == 0:
            self.fatal(0, "file is empty")
            return lib
        pos = 0
        first = True
        while pos < size:
            if self.ended:
                if any(data[pos:]):
                    self.issue(pos, "non-zero data after ENDLIB")
                pos = size
                break
            if pos + 4 > size:
                self.fatal(pos, "truncated record header")
                break
            length = (data[pos] << 8) | data[pos + 1]
            rtype = data[pos + 2]
            dtype = data[pos + 3]
            if length < 4 or length % 2:
                self.fatal(pos, f"illegal record length {length}")
                break
            if pos + length > size:
                self.fatal(pos, f"record runs past end of file (length {length})")
                break
            name = RECORD_NAMES.get(rtype)
            if name is None:
                self.fatal(pos, f"unknown record type 0x{rtype:02x}")
                break
            if first and name != "HEADER":
                self.fatal(pos, f"stream must start with HEADER, found {name}")
                break
            first = False
            expected = RECORD_DTYPES.get(name)
            if expected is not None and dtype != expected and not (expected == 0 and length == 4):
                self.fatal(pos, f"{name} has data type {dtype}, expected {expected}")
                break
            self.record(pos, name, data[pos + 4 : pos + length])
            if lib.fatal:
                break
            pos += length
        lib.bytes_read = pos
        if not lib.fatal:
            if not self.ended:
                self.fatal(size, "stream ended without ENDLIB")
            elif self.cell is not None:
                self.issue(size, f"cell {self.cell.name!r} has no ENDSTR")
        if not lib.fatal and not lib.cells:
            self.issue(0, "library contains no cells")
        if not lib.fatal:
            self.check_hierarchy()
        return lib

    def check_hierarchy(self) -> None:
        lib = self.lib
        for cell in lib.cells.values():
            for ref in cell.refs:
                if ref.name not in lib.cells:
                    self.issue(cell.offset, f"cell {cell.name!r} references undefined cell {ref.name!r}")
        state: dict[str, int] = {}
        for start in lib.cells:
            if state.get(start):
                continue
            stack = [(start, iter(sorted(lib.children_of(lib.cells[start]))))]
            state[start] = 1
            while stack:
                current, children = stack[-1]
                for child in children:
                    if child not in lib.cells:
                        continue
                    if state.get(child) == 1:
                        self.fatal(lib.cells[current].offset, f"reference cycle: {current!r} -> {child!r}")
                        return
                    if not state.get(child):
                        state[child] = 1
                        stack.append((child, iter(sorted(lib.children_of(lib.cells[child])))))
                        break
                else:
                    state[current] = 2
                    stack.pop()

    def record(self, pos: int, name: str, body: bytes) -> None:
        lib = self.lib
        if name == "HEADER":
            lib.version = struct.unpack(">h", body[:2])[0] if len(body) >= 2 else None
        elif name == "BGNLIB":
            if len(body) != 24:
                self.issue(pos, f"BGNLIB must hold 24 bytes, found {len(body)}")
        elif name == "LIBNAME":
            lib.libname = _ascii(body)
        elif name == "UNITS":
            self.units(pos, body)
        elif name == "ENDLIB":
            if self.cell is not None:
                self.issue(pos, f"ENDLIB inside cell {self.cell.name!r}")
            self.ended = True
        elif name == "BGNSTR":
            if lib.dbu_m is None:
                self.fatal(pos, "BGNSTR before UNITS")
            elif self.cell is not None:
                self.fatal(pos, "BGNSTR inside another cell")
            else:
                self.cell = Cell(name="", offset=pos)
        elif name == "STRNAME":
            self.strname(pos, body)
        elif name == "ENDSTR":
            if self.cell is None:
                self.fatal(pos, "ENDSTR without BGNSTR")
            else:
                if self.el is not None:
                    self.issue(pos, "ENDSTR inside an unfinished element")
                    self.el = None
                if not self.cell.name:
                    self.issue(pos, "cell has no STRNAME")
                self.cell = None
        elif name in ELEMENT_RECORDS:
            if self.cell is None:
                self.fatal(pos, f"{name} outside a cell")
            elif self.el is not None:
                self.issue(pos, f"{name} begins before the previous element's ENDEL")
                self.el = {"kind": name, "pos": pos}
            else:
                self.el = {"kind": name, "pos": pos}
        elif name == "ENDEL":
            if self.el is None:
                self.issue(pos, "ENDEL without an element")
            else:
                self.finish_element()
                self.el = None
        elif self.el is not None:
            self.field(pos, name, body)
        elif name in ELEMENT_FIELDS:
            self.issue(pos, f"{name} outside an element")

    def units(self, pos: int, body: bytes) -> None:
        lib = self.lib
        if len(body) != 16:
            self.fatal(pos, "UNITS must hold two 8-byte reals")
            return
        lib.uu_per_dbu = real8(body[:8])
        lib.dbu_m = real8(body[8:])
        if not lib.dbu_m > 0 or not lib.uu_per_dbu > 0:
            self.fatal(pos, f"UNITS must be positive, found {lib.uu_per_dbu} and {lib.dbu_m}")
            return
        lib.nm_per_dbu = Fraction(lib.dbu_m * 1e9).limit_denominator(1_000_000)
        if lib.nm_per_dbu == 0:
            self.fatal(pos, f"database unit {lib.dbu_m} m is too small to handle")

    def strname(self, pos: int, body: bytes) -> None:
        if self.cell is None:
            self.fatal(pos, "STRNAME outside a cell")
            return
        name = _ascii(body)
        if not name:
            self.issue(pos, "empty cell name")
            return
        if name in self.lib.cells:
            self.issue(pos, f"duplicate cell name {name!r}")
            self.cell.name = name
            return
        self.cell.name = name
        self.lib.cells[name] = self.cell

    def field(self, pos: int, name: str, body: bytes) -> None:
        el = self.el
        if name in ("LAYER", "DATATYPE", "TEXTTYPE", "BOXTYPE", "NODETYPE", "PATHTYPE"):
            if len(body) != 2:
                self.issue(pos, f"{name} must hold one 2-byte integer")
                return
            el[name] = struct.unpack(">h", body)[0]
        elif name in ("WIDTH", "BGNEXTN", "ENDEXTN"):
            if len(body) != 4:
                self.issue(pos, f"{name} must hold one 4-byte integer")
                return
            el[name] = struct.unpack(">i", body)[0]
        elif name == "XY":
            if len(body) % 8:
                self.issue(pos, "XY holds an odd number of coordinates")
                return
            el["XY"] = _points(body)
        elif name in ("SNAME", "STRING"):
            el[name] = _ascii(body)
        elif name == "STRANS":
            if len(body) != 2:
                self.issue(pos, "STRANS must hold 2 bytes")
                return
            flags = (body[0] << 8) | body[1]
            el["mirror"] = bool(flags & 0x8000)
            el["absolute"] = bool(flags & 0x0006)
        elif name in ("MAG", "ANGLE"):
            if len(body) != 8:
                self.issue(pos, f"{name} must hold one 8-byte real")
                return
            el[name] = real8(body)
        elif name == "COLROW":
            if len(body) != 4:
                self.issue(pos, "COLROW must hold two integers")
                return
            el["COLROW"] = struct.unpack(">hh", body)

    def finish_element(self) -> None:
        el = self.el
        kind, pos = el["kind"], el["pos"]
        cell = self.cell
        pts = el.get("XY")
        if pts is None:
            self.issue(pos, f"{kind} in cell {cell.name!r} has no XY")
            return
        if kind in ("BOUNDARY", "PATH", "TEXT", "NODE", "BOX") and "LAYER" not in el:
            self.issue(pos, f"{kind} in cell {cell.name!r} has no LAYER")
            return
        layer = el.get("LAYER", 0)
        if kind != "SREF" and kind != "AREF" and layer < 0:
            self.issue(pos, f"{kind} in cell {cell.name!r} has negative layer {layer}")
            return
        if kind == "BOUNDARY":
            if "DATATYPE" not in el:
                self.issue(pos, f"BOUNDARY in cell {cell.name!r} has no DATATYPE")
                return
            if len(pts) < 4:
                self.issue(pos, f"BOUNDARY in cell {cell.name!r} has only {len(pts)} points")
                return
            if pts[0] != pts[-1]:
                self.issue(pos, f"BOUNDARY in cell {cell.name!r} is not closed at {pts[0]}")
                return
            cell.shapes.append(Shape(layer, el["DATATYPE"], pts[:-1]))
        elif kind == "BOX":
            if len(pts) != 5 or pts[0] != pts[-1]:
                self.issue(pos, f"BOX in cell {cell.name!r} must have 5 closed points")
                return
            cell.shapes.append(Shape(layer, el.get("BOXTYPE", 0), pts[:-1], kind="box"))
        elif kind == "PATH":
            if len(pts) < 2:
                self.issue(pos, f"PATH in cell {cell.name!r} has only {len(pts)} point")
                return
            cell.paths.append(
                PathEl(layer, el.get("DATATYPE", 0), pts, el.get("WIDTH", 0), el.get("PATHTYPE", 0),
                       el.get("BGNEXTN", 0), el.get("ENDEXTN", 0))
            )
        elif kind == "TEXT":
            if "TEXTTYPE" not in el or "STRING" not in el or len(pts) != 1:
                self.issue(pos, f"TEXT in cell {cell.name!r} needs TEXTTYPE, STRING and one point")
                return
            cell.texts.append(TextEl(layer, el["TEXTTYPE"], pts[0][0], pts[0][1], el["STRING"]))
        elif kind == "NODE":
            cell.nodes.append(NodeEl(layer, el.get("NODETYPE", 0), pts))
        elif kind == "SREF":
            if "SNAME" not in el or len(pts) != 1:
                self.issue(pos, f"SREF in cell {cell.name!r} needs SNAME and one point")
                return
            cell.refs.append(
                Ref(el["SNAME"], pts[0][0], pts[0][1], el.get("mirror", False), el.get("MAG", 1.0),
                    el.get("ANGLE", 0.0), absolute=el.get("absolute", False))
            )
        elif kind == "AREF":
            if "SNAME" not in el or "COLROW" not in el or len(pts) != 3:
                self.issue(pos, f"AREF in cell {cell.name!r} needs SNAME, COLROW and three points")
                return
            cols, rows = el["COLROW"]
            if cols <= 0 or rows <= 0:
                self.issue(pos, f"AREF in cell {cell.name!r} has COLROW {cols}x{rows}")
                return
            cell.refs.append(
                Ref(el["SNAME"], pts[0][0], pts[0][1], el.get("mirror", False), el.get("MAG", 1.0),
                    el.get("ANGLE", 0.0), True, cols, rows, pts[1], pts[2], el.get("absolute", False))
            )
