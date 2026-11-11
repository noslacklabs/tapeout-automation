from pathlib import Path
import tempfile
import unittest

from chipflow.stages.def_model import DefError, read_def


DEF = """VERSION 5.8 ;
DESIGN demo ;
UNITS DISTANCE MICRONS 1000 ;
COMPONENTS 1 ;
- u0 INV + SOURCE TIMING + PLACED ( 0 0 ) N ;
END COMPONENTS
PINS 1 ;
- a + NET a + DIRECTION INPUT + USE SIGNAL + PORT
  + LAYER metal1 ( -1250 -1250 ) ( 1250 1250 ) + FIXED ( 0 0 ) N ;
END PINS
NETS 1 ;
- a ( PIN a ) ( u0 A ) + USE SIGNAL
  + ROUTED metal1 ( 0 0 ) ( 10000 * ) VIA12
  NEW metal2 ( 10000 0 ) ( * 10000 ) ;
END NETS
END DESIGN
"""


class DefModelTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory(prefix="def-") as directory:
            path = Path(directory) / "r.def"
            path.write_text(text, encoding="utf-8")
            return read_def(path, require_pins=True)

    def test_complete_counts_coordinates_and_vias(self):
        model = self.parse(DEF)
        self.assertEqual((len(model.components), len(model.pins), len(model.nets)), (1, 1, 1))
        self.assertEqual(model.wires[0].box, (-1200, -1200, 11200, 1200))
        self.assertEqual(model.wires[1].box, (8750, -1250, 11250, 11250))
        self.assertEqual((model.vias[0].x, model.vias[0].y), (10000, 0))

    def test_rejects_declared_inventory_mismatches(self):
        for section in ("COMPONENTS", "PINS", "NETS"):
            with self.subTest(section=section), self.assertRaises(DefError):
                self.parse(DEF.replace(f"{section} 1 ;", f"{section} 2 ;"))

    def test_rejects_unplaced_component_and_unsupported_orientation(self):
        for placement in ("UNPLACED", "PLACED ( 0 0 ) S", "PLACED ( 0 0 ) INVALID"):
            with self.subTest(placement=placement), self.assertRaisesRegex(DefError, "component"):
                self.parse(DEF.replace("PLACED ( 0 0 ) N", placement, 1))

    def test_rejects_wrong_units_and_off_grid_geometry(self):
        for old, new in (("MICRONS 1000", "MICRONS 2000"), ("10000", "10001")):
            with self.subTest(new=new), self.assertRaises(DefError):
                self.parse(DEF.replace(old, new))

    def test_rejects_unknown_via_route_and_pin_records(self):
        for old, new in (("VIA12", "BADVIA"), ("metal1 ( 0 0 )", "metal3 ( 0 0 )"),
                         ("+ FIXED", "+ UNPLACED"), ("+ USE SIGNAL", "+ UNKNOWN SIGNAL")):
            with self.subTest(new=new), self.assertRaises(DefError):
                self.parse(DEF.replace(old, new))

    def test_rejects_missing_records_duplicate_connections_and_diagonals(self):
        for text in (
            DEF.replace("( u0 A )", "( missing A )"),
            DEF.replace("( u0 A )", "( u0 A ) ( u0 A )"),
            DEF.replace("( 10000 * )", "( 10000 10000 )"),
            DEF.replace("VIA12", "VIA12 MASK 1"),
            DEF.replace("END NETS", "END WRONG"),
        ):
            with self.subTest(text=text), self.assertRaises(DefError):
                self.parse(text)

    def test_rejects_unsupported_sections_and_truncated_records(self):
        for text in (DEF.replace("END DESIGN", "SPECIALNETS 0 ;\nEND SPECIALNETS\nEND DESIGN"),
                     DEF.replace("( 10000 * ) VIA12", "( 10000 * VIA12")):
            with self.assertRaises(DefError):
                self.parse(text)
