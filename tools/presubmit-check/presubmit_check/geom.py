"""Hierarchy walking, bounding boxes and geometry hashes.

The KLayout objects here are built in memory from records already parsed by
gdsraw. KLayout never opens a design or PDK file, so its handling of hidden cells
cannot affect any result.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import klayout.db as db

from .gdsraw import Cell, Library, Ref


def _is_ortho(angle: float) -> bool:
    return abs(angle / 90.0 - round(angle / 90.0)) < 1e-9


@dataclass(frozen=True, slots=True)
class Trans:
    """GDSII placement: mirror about X, magnify, rotate, then translate (nanometres)."""

    x: float = 0
    y: float = 0
    angle: float = 0.0
    mirror: bool = False
    mag: float = 1.0

    def matrix(self):
        my = -self.mag if self.mirror else self.mag
        if _is_ortho(self.angle):
            quarter = round(self.angle / 90.0) % 4
            cos, sin = ((1, 0), (0, 1), (-1, 0), (0, -1))[quarter]
        else:
            rad = math.radians(self.angle)
            cos, sin = math.cos(rad), math.sin(rad)
        if self.mag == 1.0:
            my = -1 if self.mirror else 1
            return cos, -sin * my, sin, cos * my
        return cos * self.mag, -sin * my, sin * self.mag, cos * my

    def apply(self, x: float, y: float):
        a, b, c, d = self.matrix()
        return a * x + b * y + self.x, c * x + d * y + self.y

    def then(self, child: "Trans") -> "Trans":
        """The transform of ``child`` as seen through this parent."""
        cx, cy = self.apply(child.x, child.y)
        angle = (self.angle + (-child.angle if self.mirror else child.angle)) % 360.0
        return Trans(cx, cy, angle, self.mirror != child.mirror, self.mag * child.mag)

    @property
    def orthogonal(self) -> bool:
        return _is_ortho(self.angle) and self.mag == 1.0

    def pose(self) -> tuple:
        """Comparable position and orientation."""
        return (round(self.x), round(self.y), round(self.angle, 6) % 360.0, self.mirror, round(self.mag, 9))

    def describe(self) -> str:
        text = f"({self.x / 1000:.3f}, {self.y / 1000:.3f}) R{self.angle:g}"
        if self.mirror:
            text += " mirrored"
        if self.mag != 1.0:
            text += f" mag {self.mag:g}"
        return text


def describe_pose(pose: tuple) -> str:
    return Trans(pose[0], pose[1], pose[2], pose[3], pose[4]).describe()


def canonical_polygon(pts) -> tuple:
    """Same polygon whatever its start vertex or winding direction."""
    pts = list(pts)
    n = len(pts)
    start = min(range(n), key=pts.__getitem__)
    forward = tuple(pts[(start + i) % n] for i in range(n))
    backward = tuple(pts[(start - i) % n] for i in range(n))
    return min(forward, backward)


def ref_trans(lib: Library, ref: Ref, origin: tuple[int, int]) -> Trans:
    return Trans(lib.nm(origin[0]), lib.nm(origin[1]), ref.angle % 360.0, ref.mirror, ref.mag)


def flat_instances(lib: Library, top: str, stop: set[str] | None = None):
    """Yield (cell name, Trans) for each placement under ``top``.

    Cells in ``stop`` are yielded but not entered.
    """
    stop = stop or set()
    stack = [(top, Trans())]
    while stack:
        name, parent = stack.pop()
        cell = lib.cells.get(name)
        if cell is None:
            continue
        for ref in cell.refs:
            for origin in ref.origins():
                placed = parent.then(ref_trans(lib, ref, origin))
                yield ref.name, placed
                if ref.name not in stop:
                    stack.append((ref.name, placed))


def reachable_cells(lib: Library, top: str) -> set[str]:
    seen = {top}
    stack = [top]
    while stack:
        cell = lib.cells.get(stack.pop())
        if cell is None:
            continue
        for ref in cell.refs:
            if ref.name not in seen:
                seen.add(ref.name)
                stack.append(ref.name)
    return seen


class Extents:
    """Hierarchical bounding boxes in nanometres, with results cached per cell."""

    def __init__(self, lib: Library):
        self.lib = lib
        self._geometry: dict[str, tuple | None] = {}
        self._labels: dict[str, tuple | None] = {}

    @staticmethod
    def _merge(a, b):
        if a is None:
            return b
        if b is None:
            return a
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

    def _transformed(self, bbox, trans: Trans):
        if bbox is None:
            return None
        corners = [trans.apply(bbox[i], bbox[j]) for i in (0, 2) for j in (1, 3)]
        xs, ys = zip(*corners)
        return (min(xs), min(ys), max(xs), max(ys))

    def _children(self, cell: Cell, getter, result):
        for ref in cell.refs:
            child = getter(ref.name)
            if child is None:
                continue
            for origin in ref.origins():
                result = self._merge(result, self._transformed(child, ref_trans(self.lib, ref, origin)))
        return result

    def geometry(self, name: str):
        """Bounding box of polygons, boxes and paths. Labels do not count."""
        if name in self._geometry:
            return self._geometry[name]
        cell = self.lib.cells.get(name)
        if cell is None:
            return None
        nm = self.lib.nm
        result = None
        for shape in cell.shapes:
            xs = [nm(p[0]) for p in shape.pts]
            ys = [nm(p[1]) for p in shape.pts]
            result = self._merge(result, (min(xs), min(ys), max(xs), max(ys)))
        for path in cell.paths:
            polygon = path_polygon(self.lib, path)
            if polygon is not None:
                bb = polygon.bbox()
                result = self._merge(result, (bb.left, bb.bottom, bb.right, bb.top))
        result = self._children(cell, self.geometry, result)
        self._geometry[name] = result
        return result

    def labels(self, name: str):
        """Bounding box of label origins only."""
        if name in self._labels:
            return self._labels[name]
        cell = self.lib.cells.get(name)
        if cell is None:
            return None
        nm = self.lib.nm
        result = None
        for text in cell.texts:
            x, y = nm(text.x), nm(text.y)
            result = self._merge(result, (x, y, x, y))
        result = self._children(cell, self.labels, result)
        self._labels[name] = result
        return result


def path_polygon(lib: Library, path):
    """Convert a PATH to a polygon, or None when it has no area."""
    width = abs(lib.nm(path.width))
    if width == 0:
        return None
    points = [db.Point(lib.nm(x), lib.nm(y)) for x, y in path.pts]
    half = width // 2
    if path.pathtype == 2:
        begin, end, rounded = half, half, False
    elif path.pathtype == 4:
        begin, end, rounded = lib.nm(path.bgnextn), lib.nm(path.endextn), False
    elif path.pathtype == 1:
        begin, end, rounded = half, half, True
    else:
        begin, end, rounded = 0, 0, False
    return db.Path(points, width, begin, end, rounded).polygon()


class View:
    """An in-memory KLayout copy of a parsed library, in nanometres.

    ``frame_shapes`` optionally holds canonical polygons of the reference frame.
    Matching polygons in non-library cells go on a shadow layer so that they can be
    told apart from the designer's own shapes.
    """

    def __init__(self, lib: Library, frame_shapes: set | None = None, exclude: set[str] | None = None,
                 library_names: set[str] | None = None):
        self.lib = lib
        self.layout = db.Layout()
        self.layout.dbu = 0.001
        self.layers: dict[tuple[int, int], int] = {}
        self.shadow: dict[tuple[int, int], int] = {}
        self.index: dict[str, int] = {}
        frame_shapes = frame_shapes or set()
        library_names = library_names or set()
        exclude = exclude or set()
        for name in lib.cells:
            if name not in exclude:
                self.index[name] = self.layout.create_cell(name).cell_index()
        for name, cell in lib.cells.items():
            if name in exclude:
                continue
            self._fill(self.layout.cell(self.index[name]), cell,
                       frame_shapes if name not in library_names else set())

    def _layer(self, key: tuple[int, int], shadow: bool = False) -> int:
        table = self.shadow if shadow else self.layers
        if key not in table:
            # The shadow copies sit on a datatype no GDSII file can use.
            datatype = key[1] + 70000 if shadow else key[1]
            table[key] = self.layout.layer(db.LayerInfo(key[0], datatype))
        return table[key]

    def _fill(self, target, cell: Cell, frame_shapes: set) -> None:
        lib = self.lib
        for shape in cell.shapes:
            nm_points = [(lib.nm(x), lib.nm(y)) for x, y in shape.pts]
            key = (shape.layer, shape.datatype)
            is_frame = bool(frame_shapes) and (key, canonical_polygon(nm_points)) in frame_shapes
            polygon = db.Polygon([db.Point(x, y) for x, y in nm_points])
            target.shapes(self._layer(key, is_frame)).insert(polygon)
        for path in cell.paths:
            polygon = path_polygon(lib, path)
            if polygon is not None:
                target.shapes(self._layer((path.layer, path.datatype))).insert(polygon)
        for ref in cell.refs:
            child = self.index.get(ref.name)
            if child is None:
                continue
            for origin in ref.origins():
                placement = db.ICplxTrans(ref.mag, ref.angle, ref.mirror, lib.nm(origin[0]), lib.nm(origin[1]))
                target.insert(db.CellInstArray(child, placement))

    def layer_keys(self) -> set[tuple[int, int]]:
        return set(self.layers) | set(self.shadow)

    def region(self, top: str, key, only: set[str] | None = None, exclude: set[str] | None = None,
               shadow: bool = False) -> db.Region:
        """Flat shapes under ``top``. ``only``/``exclude`` select by cell, children included."""
        table = self.shadow if shadow else self.layers
        layer = table.get(tuple(key))
        if layer is None or top not in self.index:
            return db.Region()
        if only is not None:
            cells = [self.index[n] for n in only if n in self.index]
            if not cells:
                return db.Region()
        iterator = self.layout.cell(self.index[top]).begin_shapes_rec(layer)
        if only is not None:
            iterator.unselect_all_cells()
            iterator.select_cells(cells)
        if exclude:
            cells = [self.index[n] for n in exclude if n in self.index]
            if cells:
                iterator.unselect_cells(cells)
        return db.Region(iterator)

    def all_shapes(self, top: str, key) -> db.Region:
        """Flat shapes on a layer, including any matched to the reference frame."""
        return self.region(top, key) + self.region(top, key, shadow=True)

    def _merged_by_layer(self, cell: str) -> dict:
        layers = {}
        for key in sorted(self.layer_keys()):
            region = self.all_shapes(cell, key).merged()
            if not region.is_empty():
                layers[key] = region
        return layers

    def cell_hash(self, cell: str, normalise: bool = False) -> str:
        """Hash of a cell's flattened polygons, layer by layer.

        Shapes are merged first, then each polygon is written in KLayout's canonical
        form and the list sorted. The hash therefore ignores shape order, start
        vertex, winding direction and how an area is split into shapes. Labels are
        not included. With ``normalise`` the geometry is first moved to the origin.
        """
        layers = self._merged_by_layer(cell)
        return hash_layers(layers, normalise)

    def merged_layers(self, cell: str) -> dict:
        return self._merged_by_layer(cell)


def hash_layers(layers: dict, normalise: bool = False) -> str:
    digest = hashlib.sha256()
    shift = None
    if normalise and layers:
        total = db.Box()
        for region in layers.values():
            total += region.bbox()
        shift = (-total.left, -total.bottom)
    for key in sorted(layers):
        region = layers[key]
        if shift is not None:
            region = region.moved(shift[0], shift[1])
        polygons = sorted(p.to_s() for p in region.each())
        digest.update(f"{key[0]}/{key[1]}:".encode())
        digest.update("|".join(polygons).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def label_signature(lib: Library, cell: Cell) -> tuple:
    """Order-independent list of a cell's own labels, in nanometres."""
    return tuple(sorted((t.layer, t.texttype, t.string, lib.nm(t.x), lib.nm(t.y)) for t in cell.texts))


LISTING_LIMIT = 200


def bboxes(region: db.Region, limit: int = LISTING_LIMIT) -> list[tuple[int, int, int, int]]:
    result = []
    for polygon in region.each():
        bb = polygon.bbox()
        result.append((bb.left, bb.bottom, bb.right, bb.top))
        if len(result) >= limit:
            break
    return sorted(result)


def overflow_note(region: db.Region, limit: int = LISTING_LIMIT) -> list[str]:
    """A line saying a listing was cut short, so a count is never silently understated."""
    total = region.count()
    return [f"{total} in total; only {limit} are listed"] if total > limit else []
