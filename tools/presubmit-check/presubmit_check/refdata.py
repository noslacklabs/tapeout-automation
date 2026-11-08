"""Configuration and reference data taken from the PDK.

The PDK is untrusted data. Files are opened read-only and parsed as plain data:
GDS through gdsraw, the LEF and pad list with line matching. No script, installer
or rule deck from the kit is ever run.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .gdsraw import Library, read_gds
from .geom import View, canonical_polygon, flat_instances, label_signature
from .model import ToolError

DEFAULT_CONFIG = Path(__file__).parent / "config" / "default.toml"
NAME_RE = re.compile(r"^[A-Za-z0-9_$]+$")
LEF_MACRO_RE = re.compile(r"^\s*macro\s+(\S+)", re.IGNORECASE | re.MULTILINE)


def load_config(path: str | None) -> dict:
    target = Path(path) if path else DEFAULT_CONFIG
    try:
        with open(target, "rb") as handle:
            config = tomllib.load(handle)
    except OSError as exc:
        raise ToolError(f"cannot read config {target}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ToolError(f"config {target} is not valid TOML: {exc}") from exc
    try:
        config["pdk"]["frame_sets"], config["cells"]["logo"], config["rules"]["dies"]
    except KeyError as exc:
        raise ToolError(f"config {target} is missing {exc}") from exc
    return config


def _pdk_hint(root: str) -> str:
    if "µ" in root or "μ" in root:
        swapped = root.replace("µ", "\0").replace("μ", "µ").replace("\0", "μ")
        if os.path.isdir(swapped):
            return (" The path has a different mu character from the one on disk: "
                    "the folder name uses U+00B5 or U+03BC, not the one given.")
    return ""


@dataclass
class FrameRef:
    set_name: str
    description: str
    path: Path
    lib: Library
    top: str
    instances: list = field(default_factory=list)  # (cell name, pose)


def _real_top(lib: Library, context: str, path: Path) -> str:
    tops = lib.top_cells(ignore=context)
    if len(tops) != 1:
        raise ToolError(f"reference frame {path} has {len(tops)} top cells, expected 1")
    return tops[0]


class References:
    """Everything the checks compare against."""

    def __init__(self, config: dict, pdk_root: str, die: int, frame_set: str | None):
        self.config = config
        self.root = Path(pdk_root)
        if not self.root.is_dir():
            raise ToolError(f"--pdk-root {pdk_root!r} is not a folder.{_pdk_hint(pdk_root)}")
        pdk = config["pdk"]
        self.cells_cfg = config["cells"]
        self.context = self.cells_cfg["context"]
        self.frame_set = frame_set or pdk["frame_set"]
        if self.frame_set not in pdk["frame_sets"]:
            raise ToolError(f"unknown frame set {self.frame_set!r}; config has {sorted(pdk['frame_sets'])}")

        self.pad_names = self._read_pad_list(self._path(pdk["pad_list"]))
        self.std_names = self._read_lef(self._path(pdk["core_lef"]))
        self.library_names = self.pad_names | self.std_names
        overlap = self.pad_names & self.std_names
        if overlap:
            raise ToolError(f"names in both the pad list and the LEF: {sorted(overlap)}")

        chosen = pdk["frame_sets"][self.frame_set]
        self.pad_gds_path = self._path(chosen["pad_gds"])
        self.core_gds_path = self._path(pdk["core_gds"])
        self.pad_lib = self._read_lib(self.pad_gds_path)
        self.core_lib = self._read_lib(self.core_gds_path)

        self.frames: dict[str, FrameRef] = {}
        for name, entry in pdk["frame_sets"].items():
            self.frames[name] = self._read_frame(name, entry, die, pdk["frame_file"])
        self.frame = self.frames[self.frame_set]

        self.frame_shape_keys: set = set()
        self.frame_labels: set = set()
        for frame in self.frames.values():
            cell = frame.lib.cells[frame.top]
            nm = frame.lib.nm
            for shape in cell.shapes:
                points = [(nm(x), nm(y)) for x, y in shape.pts]
                self.frame_shape_keys.add(((shape.layer, shape.datatype), canonical_polygon(points)))
            for text in cell.texts:
                self.frame_labels.add((text.layer, text.texttype, text.string, nm(text.x), nm(text.y)))

        self._views: dict[str, View] = {}
        self.ref_hash: dict[str, str] = {}
        self.ref_norm_hash: dict[str, set[str]] = {}
        self.ref_label_sets: dict[str, set[tuple]] = {}
        self.missing_reference: list[str] = []
        self._hash_library()
        self.logo_hashes = self._logo_hashes()
        self.frame_cell_hashes = {
            name: self.view_of(frame.lib).cell_hash(frame.top) for name, frame in self.frames.items()
        }

    def _path(self, relative: str) -> Path:
        path = self.root / relative
        if not path.is_file():
            raise ToolError(
                f"PDK file not found: {path}. --pdk-root must be the kit folder "
                f"holding PadFrame_C1D, Analog_C1D and Digital_C1D.{_pdk_hint(str(self.root))}"
            )
        return path

    def _read_lib(self, path: Path) -> Library:
        lib = read_gds(path)
        if lib.fatal:
            raise ToolError(f"reference GDS {path} cannot be parsed: {lib.issues[0]}")
        return lib

    def _read_pad_list(self, path: Path) -> set[str]:
        names = set()
        for line in path.read_text(encoding="latin-1").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not NAME_RE.match(line):
                raise ToolError(f"unexpected line in pad list {path}: {line!r}")
            names.add(line)
        if not names:
            raise ToolError(f"pad list {path} is empty")
        return names

    def _read_lef(self, path: Path) -> set[str]:
        names = set(LEF_MACRO_RE.findall(path.read_text(encoding="latin-1")))
        if not names:
            raise ToolError(f"no macros found in LEF {path}")
        return names

    def _read_frame(self, name: str, entry: dict, die: int, template: str) -> FrameRef:
        path = self._path(os.path.join(entry["frames_dir"], template.format(die=die)))
        lib = self._read_lib(path)
        top = _real_top(lib, self.context, path)
        frame = FrameRef(name, entry.get("description", name), path, lib, top)
        stop = self.library_names | {self.cells_cfg["logo"]}
        frame.instances = [(cell, trans.pose()) for cell, trans in flat_instances(lib, top, stop)]
        return frame

    def view_of(self, lib: Library) -> View:
        key = lib.path
        if key not in self._views:
            self._views[key] = View(lib)
        return self._views[key]

    def _hash_library(self) -> None:
        for names, lib in ((self.pad_names, self.pad_lib), (self.std_names, self.core_lib)):
            view = self.view_of(lib)
            for name in sorted(names):
                if name not in lib.cells:
                    self.missing_reference.append(name)
                    continue
                self.ref_hash[name] = view.cell_hash(name)
                self.ref_norm_hash.setdefault(view.cell_hash(name, normalise=True), set()).add(name)
                self.ref_label_sets.setdefault(name, set()).add(label_signature(lib, lib.cells[name]))
        # A pad embedded in the chosen frame can carry different labels from the
        # library copy while its geometry is identical, so both copies are accepted.
        frame_lib = self.frame.lib
        for name in self.pad_names:
            if name in frame_lib.cells:
                self.ref_label_sets.setdefault(name, set()).add(label_signature(frame_lib, frame_lib.cells[name]))

    def _logo_hashes(self) -> dict[str, str]:
        """Every logo variant the kit ships, mapped to where it came from."""
        logo = self.cells_cfg["logo"]
        result: dict[str, str] = {}
        sources = [(f"pad library of set {self.frame_set}", self.pad_lib)]
        for name, frame in self.frames.items():
            sources.append((f"frame of set {name}", frame.lib))
        for name, entry in self.config["pdk"]["frame_sets"].items():
            if name != self.frame_set:
                sources.append((f"pad library of set {name}", self._read_lib(self._path(entry["pad_gds"]))))
        for origin, lib in sources:
            if logo in lib.cells:
                result.setdefault(self.view_of(lib).cell_hash(logo), origin)
        return result
