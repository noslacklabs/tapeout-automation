"""Result types, shared formatting helpers and rule-source citations."""

from __future__ import annotations

from dataclasses import dataclass, field

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
MANUAL = "MANUAL"
STATUSES = (FAIL, WARN, MANUAL, PASS)

NOTICE = (
    "This tool does not replace DRC or LVS. Both must still be run separately with SCL's "
    "current deck, and no result here, including a run with no FAILs, means the design is "
    "ready to submit."
)


class ToolError(Exception):
    """The tool itself could not run (bad setup, unreadable PDK). Exit code 2."""


class Source:
    """Where each rule comes from. Short names are the ones PDK_GUIDE.md defines."""

    GRID = "[Procedure] Basic Design Guidelines, Layout Grid"
    GRID_Q = "[Procedure] Layout Grid; PDK_GUIDE.md section 14, open helpdesk question on off-grid library cells"
    WINDOW = "[Procedure] Layout Grid (window at (0,0)); [Pad doc] pp.3-11 (die sizes)"
    NAMING = "[Procedure] Design Verification Guidelines (naming convention)"
    TOPCELL = "[Procedure] Design Verification Guidelines (top cell name); PDK_GUIDE.md section 14"
    DRC_NAME = "[Procedure] Design Verification Guidelines (DRC report naming)"
    MD5 = "[Procedure] Design Verification Guidelines (MD5 checksum)"
    STREAM = "GDSII stream format; [Procedure] requires a GDSII database"
    PADS = "[Pad doc] pp.3-11; PDK_GUIDE.md section 6"
    POWER = '[Pad doc] p.3 onward: position and type of power pads "should not be altered"'
    FRAMESET = "PDK_GUIDE.md section 6 (two frame sets) and section 14 (open helpdesk question)"
    SIGNAL = "PF/klayout/lvs_cdl/io_pad_list.txt; [Pad doc] pp.13-14 (changing signal pads)"
    WINDOW90 = "[Analog doc] rule 11.1.1; [DRC deck] line 530"
    LIB = "[Procedure] Design Kit (do not edit or rename library cells)"
    LAYERS = "PDK_GUIDE.md section 5, Layers table"
    IGNORE = "[DRC deck] lines 93, 120-121; PDK_GUIDE.md section 5"
    BLOCK = "[Procedure] Integrate GDS Block into Design (no modification or scaling)"
    METAL = "[Analog doc] Level 8 and Level 10 mandatory rules (PDF pp.29, 31)"
    VIA = "[Analog doc] Level 9 mandatory rule (PDF p.30)"
    NECK = "[Analog doc] Level 8 mandatory rules, Fig.9A (PDF p.29), states no DRC check"
    ERC = "[Analog doc] Table 11A"
    ELECTRICAL = "[Analog doc] sections 2 and 5, Tables 3 and 4; [Digital doc] Tables 6 and 7"
    DRC_REPORT = "[Procedure] Design Verification Guidelines; PDK_GUIDE.md section 4; [DRC deck] @GUIDELINE outputs"


@dataclass
class Result:
    id: str
    status: str
    title: str
    evidence: list[str] = field(default_factory=list)
    source: str = ""
    assumption: str = ""

    def as_dict(self, limit: int = 1000) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title,
            "evidence": self.evidence[:limit],
            "evidence_total": len(self.evidence),
            "source": self.source,
            "assumption": self.assumption,
        }


def um(nm: float) -> str:
    """Format nanometres as micrometres with three decimals."""
    return f"{nm / 1000:.3f}"


def pt(x: float, y: float) -> str:
    return f"({um(x)}, {um(y)})"


def box(bbox) -> str:
    """Format (x1, y1, x2, y2) in nanometres."""
    return f"{pt(bbox[0], bbox[1])} to {pt(bbox[2], bbox[3])}"


_IRREGULAR = {"vertex": "vertices", "category": "categories", "rule category": "rule categories",
              "path half-width": "path half-widths"}


def plural(count: int, word: str) -> str:
    if count == 1:
        return f"{count} {word}"
    return f"{count} {_IRREGULAR.get(word, word + 's')}"
