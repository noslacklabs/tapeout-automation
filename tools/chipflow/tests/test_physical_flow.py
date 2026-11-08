from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from chipflow.config import load_project
from chipflow.config.pads import build_pad_plan, load_pad_map
from chipflow.stages.floorplan import build_floorplan
from chipflow.stages.wire_overlap import check_wire_vs_cells
from chipflow.technologies.profile import load_profile

DUMMY_LEF = """VERSION 5.7 ;
NOWIREEXTENSIONATPIN ON ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
MACRO INVR04
  CLASS CORE ;
  ORIGIN 0.0 0.0 ;
  SIZE 21.0 BY 48.7 ;
  PIN OUT1
    DIRECTION OUTPUT ;
    PORT
      LAYER metal1 ;
      RECT 5.0 10.0 15.0 20.0 ;
    END
  END OUT1
END INVR04
END LIBRARY
"""


class PhysicalFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chipflow physical ")
        self.root = Path(self.temp.name)
        self.lef_file = self.root / "cells.lef"
        self.lef_file.write_text(DUMMY_LEF, encoding="utf-8")
        self.profile = load_profile()
        self.addCleanup(self.temp.cleanup)

    def test_floorplan_generation_and_invariants(self):
        pad_plan = {
            "pads": [
                {"slot": 1, "side": "bottom", "cell": "IPAD3D", "kind": "signal", "port": "miso", "direction": "input", "offset_um": 350.95},
                {"slot": 2, "side": "bottom", "cell": "OPAD04", "kind": "signal", "port": "cs_n", "direction": "output", "driver": "INVR04", "offset_um": 700.95},
                {"slot": 5, "side": "right", "cell": "OPAD04", "kind": "signal", "port": "mosi", "direction": "output", "driver": "INVR04", "offset_um": 350.95},
                {"slot": 8, "side": "top", "cell": "IPAD3D", "kind": "signal", "port": "clk", "direction": "input", "offset_um": 1050.95},
                {"slot": 11, "side": "left", "cell": "IPAD3D", "kind": "signal", "port": "rst", "direction": "input", "offset_um": 1605.8},
                {"slot": 13, "side": "left", "cell": "PVDD01", "kind": "power", "net": "VDD", "offset_um": None},
            ]
        }
        terminals = {
            "IPAD3D": [219.25, 221.75],
            "OPAD04": [189.5, 193.65],
        }

        fp_dict, fp_tcl = build_floorplan(pad_plan, self.profile, terminals, self.lef_file)

        self.assertIn("rows", fp_dict)
        self.assertIn("pads", fp_dict)
        self.assertIn("block", fp_dict)
        self.assertEqual(len(fp_dict["rows"]), self.profile.frame_geometry.row_count)
        self.assertEqual(len(fp_dict["pads"]), 5)

        # Check driver assignments
        drivers = [p for p in fp_dict["pads"] if p["dir"] == "out"]
        self.assertEqual(len(drivers), 2)
        for drv in drivers:
            self.assertIn("drv_inst", drv)
            self.assertTrue(drv["drv_inst"].startswith("paddrv_"))
            self.assertIn("drv_x", drv)
            self.assertIn("drv_y", drv)

        # Tcl contains pin placements and block definitions
        self.assertIn("set BLOCK_LO", fp_tcl)
        self.assertIn("set ROWS", fp_tcl)
        self.assertIn("set PINS", fp_tcl)
        self.assertIn("set DRIVERS", fp_tcl)

    def test_floorplan_rejects_missing_pad_terminal(self):
        pad_plan = {
            "pads": [
                {"slot": 1, "side": "bottom", "cell": "UNKNOWN_PAD", "kind": "signal", "port": "in1", "direction": "input", "offset_um": 350.95},
            ]
        }
        terminals = {"IPAD3D": [219.25, 221.75]}
        with self.assertRaisesRegex(ValueError, "no measured terminal range"):
            build_floorplan(pad_plan, self.profile, terminals, self.lef_file)

    def test_wire_overlap_checker_clean(self):
        import klayout.db as db
        # Synthetic clean routed DEF
        def_content = """VERSION 5.7 ;
DESIGN test_core ;
UNITS DISTANCE MICRONS 1000 ;
COMPONENTS 0 ;
END COMPONENTS
NETS 0 ;
END NETS
END DESIGN
"""
        def_file = self.root / "routed.def"
        def_file.write_text(def_content, encoding="utf-8")
        # Valid empty GDS file
        gds_file = self.root / "core.gds"
        ly = db.Layout()
        ly.create_cell("TOP")
        ly.write(str(gds_file))

        # When DEF has 0 nets and 0 components, no wire problems should be detected
        res = check_wire_vs_cells(def_file, gds_file, self.lef_file)
        self.assertIn("problems", res)
        self.assertEqual(len(res["problems"]), 0)
