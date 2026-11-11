"""Defect controls for independent downstream SPEF coverage validation."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("s8_downstream", Path(__file__).with_name("run.py"))
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)

DEF = """VERSION 5.8 ;
DESIGN demo ;
UNITS DISTANCE MICRONS 1000 ;
COMPONENTS 1 ;
- u0 INV + PLACED ( 0 0 ) N ;
END COMPONENTS
PINS 1 ;
- a + NET a + DIRECTION INPUT + USE SIGNAL + PORT
  + LAYER metal1 ( -1250 -1250 ) ( 1250 1250 ) + FIXED ( 0 0 ) N ;
END PINS
NETS 1 ;
- a ( PIN a ) ( u0 A ) + USE SIGNAL
  + ROUTED metal1 ( 0 0 ) ( 10000 * ) ;
END NETS
END DESIGN
"""
SPEF = """*SPEF "IEEE 1481-1998"
*T_UNIT 1 NS
*C_UNIT 1 PF
*R_UNIT 1 OHM
*L_UNIT 1 HENRY
*NAME_MAP
*1 a
*2 u0
*D_NET *1 0.01
*CONN
*P *1 I
*I *2:A I
*CAP
1 *1 0.01
*RES
*END
"""


class ParasiticCoverageControls(unittest.TestCase):
    def validate(self, text=SPEF):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "r.def").write_text(DEF, encoding="utf-8")
            (root / "r.spef").write_text(text, encoding="utf-8")
            return DRIVER.spef_coverage(root / "r.spef", root / "r.def")

    def test_complete_coverage(self):
        result = self.validate()
        self.assertEqual(result["extracted_nets"], 1)
        self.assertEqual(result["matched_pin_connections"], 2)

    def test_empty_extraction_rejected(self):
        with self.assertRaisesRegex(ValueError, "routed net mismatch"):
            self.validate(SPEF[:SPEF.index("*D_NET")])

    def test_missing_and_duplicate_pins_rejected(self):
        for replacement in ("", "*I *2:A I\n*I *2:A I\n"):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                self.validate(SPEF.replace("*I *2:A I\n", replacement))

    def test_unknown_pin_rejected(self):
        with self.assertRaisesRegex(ValueError, "pin coverage differs"):
            self.validate(SPEF.replace("*2:A", "*2:Y"))

    def test_duplicate_net_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate SPEF net"):
            self.validate(SPEF + SPEF[SPEF.index("*D_NET"):])

    def test_missing_duplicate_or_wrong_units_rejected(self):
        for text in (SPEF.replace("*C_UNIT 1 PF\n", ""),
                     SPEF + "*C_UNIT 1 PF\n", SPEF.replace("1 PF", "1 FF")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.validate(text)

    def test_connection_section_required(self):
        with self.assertRaisesRegex(ValueError, "missing SPEF connection section"):
            self.validate(SPEF.replace("*CONN", "*MISSING"))


class TimingCoverageControls(unittest.TestCase):
    def test_input_hold_failure_survives_positive_core_hold(self):
        text = "### HOLD\n 2.17 slack (MET)\n### IN PAD6 MIN\n -2.02 slack (VIOLATED)\n"
        result = DRIVER.timing_path_evidence(text, {"HOLD", "IN PAD6 MIN"})
        self.assertEqual(min(r["slack_ns"] for r in result.values()), -2.02)

    def test_missing_or_empty_io_path_is_not_complete(self):
        for text in ("### HOLD\n 2.17 slack (MET)\n", "### IN PAD6 MIN\nNo paths found.\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                DRIVER.timing_path_evidence(text, {"IN PAD6 MIN"})

    def test_worst_path_is_checked(self):
        text = "### IN PAD6 MIN\n 1.00 slack (MET)\n -0.50 slack (VIOLATED)\n"
        self.assertEqual(DRIVER.timing_path_evidence(text, {"IN PAD6 MIN"})["IN PAD6 MIN"]["slack_ns"], -0.5)


if __name__ == "__main__":
    unittest.main()
