"""Read a KLayout XML marker database (a DRC report) without trusting it."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

GUIDELINE_TAG = "@GUIDELINE"
_FORBIDDEN = (b"<!DOCTYPE", b"<!ENTITY")


class ReportInvalid(Exception):
    """The file is not a usable marker database."""


@dataclass
class Category:
    path: str
    markers: int = 0
    first_value: str = ""
    first_cell: str = ""

    @property
    def is_guideline(self) -> bool:
        return GUIDELINE_TAG in self.path


@dataclass
class DrcReport:
    description: str = ""
    original_file: str = ""
    generator: str = ""
    top_cell: str = ""
    categories: dict[str, Category] = field(default_factory=dict)
    undeclared: set[str] = field(default_factory=set)

    @property
    def total_markers(self) -> int:
        return sum(c.markers for c in self.categories.values())


def _segments(path: str) -> list[str]:
    """Split a KLayout category path such as ``'a b'.c`` into its parts."""
    parts: list[str] = []
    current: list[str] = []
    quote = None
    i = 0
    while i < len(path):
        ch = path[i]
        if quote:
            if ch == "\\" and i + 1 < len(path):
                current.append(path[i + 1])
                i += 1
            elif ch == quote:
                quote = None
            else:
                current.append(ch)
        elif ch in "'\"":
            quote = ch
        elif ch == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _normal(path: str) -> str:
    return ".".join(part.strip() for part in _segments(path.strip()))


def _collect_categories(element, prefix: str, table: dict[str, Category]) -> None:
    for category in element.findall("category"):
        name = (category.findtext("name") or "").strip()
        path = f"{prefix}.{name}" if prefix else name
        children = category.find("categories")
        has_children = children is not None and len(children) > 0
        if path and not has_children:
            table.setdefault(path, Category(path))
        if has_children:
            _collect_categories(children, path, table)


def parse_report(path: str | Path) -> DrcReport:
    """Parse a report, raising ReportInvalid when it isn't a marker database."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ReportInvalid(f"cannot read the file: {exc}") from exc
    if not raw.strip():
        raise ReportInvalid("the file is empty")
    for marker in _FORBIDDEN:
        if marker in raw:
            raise ReportInvalid(f"the file contains {marker.decode()}, which KLayout reports never use")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ReportInvalid(f"not well-formed XML: {exc}") from exc
    if root.tag != "report-database":
        raise ReportInvalid(f"root element is <{root.tag}>, not <report-database>")
    categories = root.find("categories")
    items = root.find("items")
    if categories is None or items is None:
        raise ReportInvalid("<categories> or <items> is missing")

    report = DrcReport(
        description=(root.findtext("description") or "").strip(),
        original_file=(root.findtext("original-file") or "").strip(),
        generator=(root.findtext("generator") or "").strip(),
        top_cell=(root.findtext("top-cell") or "").strip(),
    )
    _collect_categories(categories, "", report.categories)
    for item in items.findall("item"):
        raw_category = item.findtext("category")
        if raw_category is None:
            raise ReportInvalid("an <item> has no <category>")
        name = _normal(raw_category)
        entry = report.categories.get(name)
        if entry is None:
            report.undeclared.add(name)
            entry = report.categories.setdefault(name, Category(name))
        text = (item.findtext("multiplicity") or "1").strip()
        try:
            multiplicity = int(text)
        except ValueError as exc:
            raise ReportInvalid(f"marker multiplicity {text!r} is not a number") from exc
        entry.markers += max(multiplicity, 1)
        if not entry.first_value:
            values = item.find("values")
            first = values.find("value") if values is not None else None
            entry.first_value = (first.text or "").strip()[:160] if first is not None else ""
            entry.first_cell = (item.findtext("cell") or "").strip()
    return report
