from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from chipflow.config.pads import PadError, build_pad_plan, load_pad_map, validate_pads
from chipflow.runner.results import CheckStatus
from chipflow.stages.design_model import Design, PortInfo
from chipflow.technologies.profile import load_profile

S8_PADS_TOML = '''schema_version = 1
frame = "scl-c1d-2mm"
profile_version = "0.1.0"

[[slot]]
slot = 1
port = "cs_n"
pad_cell = "OPAD04"
driver = "INVR04"

[[slot]]
slot = 2
port = "miso"
pad_cell = "IPAD2D"
driver = "none"

[[slot]]
slot = 3
port = "mosi"
pad_cell = "OPAD04"
driver = "INVR04"

[[slot]]
slot = 4
port = "clk"
pad_cell = "IPAD30"
driver = "none"

[[slot]]
slot = 5
port = "sck"
pad_cell = "OPAD04"
driver = "INVR04"

[[slot]]
slot = 6
port = "rst"
pad_cell = "IPAD3D"
driver = "none"

[[slot]]
slot = 7
port = "gpo[0]"
pad_cell = "OPAD04"
driver = "INVR04"

[[slot]]
slot = 8
port = "gpi[0]"
pad_cell = "IPAD2D"
driver = "none"

[[slot]]
slot = 9
port = "gpo[1]"
pad_cell = "OPAD04"
driver = "INVR04"

[[slot]]
slot = 10
port = "gpi[1]"
pad_cell = "IPAD2D"
driver = "none"

[[slot]]
slot = 11
port = "gpo[2]"
pad_cell = "OPAD04"
driver = "INVR04"

[[slot]]
slot = 12
port = "gpi[2]"
pad_cell = "IPAD2D"
driver = "none"
'''


def make_s8_design() -> Design:
    ports = {
        "clk": PortInfo("clk", "input", 1, canonical_bits=("clk",)),
        "rst": PortInfo("rst", "input", 1, canonical_bits=("rst",)),
        "miso": PortInfo("miso", "input", 1, canonical_bits=("miso",)),
        "gpi": PortInfo("gpi", "input", 3, canonical_bits=("gpi[0]", "gpi[1]", "gpi[2]")),
        "sck": PortInfo("sck", "output", 1, canonical_bits=("sck",)),
        "cs_n": PortInfo("cs_n", "output", 1, canonical_bits=("cs_n",)),
        "mosi": PortInfo("mosi", "output", 1, canonical_bits=("mosi",)),
        "gpo": PortInfo("gpo", "output", 3, canonical_bits=("gpo[0]", "gpo[1]", "gpo[2]")),
    }
    return Design(top="s8", top_present=True, ports=ports, cells={}, netnames_with_init=())


class PadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chipflow pads ")
        self.root = Path(self.temp.name)
        self.profile = load_profile()
        self.addCleanup(self.temp.cleanup)

    def test_profile_loading_from_package_resources(self):
        self.assertEqual(self.profile.profile, "scl_c1d")
        self.assertEqual(self.profile.frame, "scl-c1d-2mm")
        self.assertEqual(len(self.profile.signal_slots), 12)
        self.assertEqual(len(self.profile.power_pads), 4)
        self.assertIn("OPAD04", self.profile.pad_cells)

    def test_exact_s8_mapping_passes_and_builds_pad_plan(self):
        p_file = self.root / "pads.toml"
        p_file.write_text(S8_PADS_TOML, encoding="utf-8")
        pad_config = load_pad_map(p_file)
        design = make_s8_design()
        rows = {r.id: r for r in validate_pads(pad_config, self.profile, design)}
        for row_id, r in rows.items():
            self.assertEqual(r.status, CheckStatus.PASS, f"Row {row_id} did not pass: {r.message}")

        plan = build_pad_plan(pad_config, self.profile)
        self.assertEqual(plan["schema_version"], 1)
        self.assertEqual(len(plan["pads"]), 16)
        self.assertEqual(plan["pads"][0]["slot"], 1)
        self.assertEqual(plan["pads"][0]["cell"], "OPAD04")
        self.assertEqual(plan["pads"][12]["slot"], 13)
        self.assertEqual(plan["pads"][12]["cell"], "PVDD01")
        for pad in plan["pads"][12:]:
            self.assertEqual(pad["kind"], "power")
            self.assertIn(pad["side"], {"left", "right"})
            self.assertGreater(pad["x_um"], 0.0)
            self.assertGreater(pad["y_um"], 0.0)

    def test_duplicate_slot_missing_slot_and_slot_13_fail(self):
        # Missing slot 12, duplicate slot 1
        bad_dup = S8_PADS_TOML.replace("slot = 12\nport = \"gpi[2]\"", "slot = 1\nport = \"gpi[2]\"")
        p_file = self.root / "pads_dup.toml"
        p_file.write_text(bad_dup, encoding="utf-8")
        pad_config = load_pad_map(p_file)
        rows = {r.id: r for r in validate_pads(pad_config, self.profile)}
        self.assertEqual(rows["pads.slot_coverage"].status, CheckStatus.FAIL)
        self.assertIn("duplicate slots", rows["pads.slot_coverage"].message)
        self.assertIn("missing slots", rows["pads.slot_coverage"].message)

        # Slot 13 (out of range)
        bad_13 = S8_PADS_TOML.replace("slot = 12", "slot = 13")
        p_file2 = self.root / "pads_13.toml"
        p_file2.write_text(bad_13, encoding="utf-8")
        pad_config2 = load_pad_map(p_file2)
        rows2 = {r.id: r for r in validate_pads(pad_config2, self.profile)}
        self.assertEqual(rows2["pads.slot_coverage"].status, CheckStatus.FAIL)
        self.assertIn("out of range slots: [13]", rows2["pads.slot_coverage"].message)

    def test_unknown_pad_cell(self):
        bad_cell = S8_PADS_TOML.replace("pad_cell = \"OPAD04\"", "pad_cell = \"UNKNOWN_CELL\"")
        p_file = self.root / "pads_cell.toml"
        p_file.write_text(bad_cell, encoding="utf-8")
        pad_config = load_pad_map(p_file)
        rows = {r.id: r for r in validate_pads(pad_config, self.profile)}
        self.assertEqual(rows["pads.cell"].status, CheckStatus.FAIL)
        self.assertIn("unsupported pad cell", rows["pads.cell"].message)

    def test_driver_mismatches(self):
        # Input with a driver
        bad_in_driver = S8_PADS_TOML.replace('slot = 2\nport = "miso"\npad_cell = "IPAD2D"\ndriver = "none"',
                                            'slot = 2\nport = "miso"\npad_cell = "IPAD2D"\ndriver = "INVR04"')
        p_file = self.root / "pads_in_driver.toml"
        p_file.write_text(bad_in_driver, encoding="utf-8")
        rows = {r.id: r for r in validate_pads(load_pad_map(p_file), self.profile)}
        self.assertEqual(rows["pads.driver"].status, CheckStatus.FAIL)
        self.assertIn("must have driver='none'", rows["pads.driver"].message)

        # Output with wrong driver
        bad_out_driver = S8_PADS_TOML.replace('slot = 1\nport = "cs_n"\npad_cell = "OPAD04"\ndriver = "INVR04"',
                                             'slot = 1\nport = "cs_n"\npad_cell = "OPAD04"\ndriver = "WRONG_DRIVER"')
        p_file2 = self.root / "pads_out_driver.toml"
        p_file2.write_text(bad_out_driver, encoding="utf-8")
        rows2 = {r.id: r for r in validate_pads(load_pad_map(p_file2), self.profile)}
        self.assertEqual(rows2["pads.driver"].status, CheckStatus.FAIL)
        self.assertIn("not in ('INVR04',)", rows2["pads.driver"].message)

    def test_direction_mismatch_and_port_coverage(self):
        design = make_s8_design()
        # cs_n is output in design; assign to input pad IPAD2D
        bad_dir = S8_PADS_TOML.replace('slot = 1\nport = "cs_n"\npad_cell = "OPAD04"\ndriver = "INVR04"',
                                      'slot = 1\nport = "cs_n"\npad_cell = "IPAD2D"\ndriver = "none"')
        p_file = self.root / "pads_dir.toml"
        p_file.write_text(bad_dir, encoding="utf-8")
        rows = {r.id: r for r in validate_pads(load_pad_map(p_file), self.profile, design)}
        self.assertEqual(rows["pads.direction"].status, CheckStatus.FAIL)
        self.assertIn("direction output != pad cell IPAD2D direction input", rows["pads.direction"].message)

        # Port bit mapped twice (slot 3 maps cs_n instead of mosi)
        bad_cov = S8_PADS_TOML.replace('slot = 3\nport = "mosi"', 'slot = 3\nport = "cs_n"')
        p_file2 = self.root / "pads_cov.toml"
        p_file2.write_text(bad_cov, encoding="utf-8")
        rows2 = {r.id: r for r in validate_pads(load_pad_map(p_file2), self.profile, design)}
        self.assertEqual(rows2["pads.port_coverage"].status, CheckStatus.FAIL)
        self.assertIn("port bits mapped more than once: ['cs_n']", rows2["pads.port_coverage"].message)
        self.assertIn("unmapped port bits: ['mosi']", rows2["pads.port_coverage"].message)

        # Nonexistent port and gpo[3] on 3-bit bus [2:0]
        bad_out_of_range = S8_PADS_TOML.replace('port = "gpo[2]"', 'port = "gpo[3]"')
        p_file3 = self.root / "pads_oor.toml"
        p_file3.write_text(bad_out_of_range, encoding="utf-8")
        rows3 = {r.id: r for r in validate_pads(load_pad_map(p_file3), self.profile, design)}
        self.assertEqual(rows3["pads.port_coverage"].status, CheckStatus.FAIL)
        self.assertIn("names unknown port bit: gpo[3]", rows3["pads.port_coverage"].message)

    def test_unused_slot_gives_configuration_fail(self):
        unused_pad = S8_PADS_TOML.replace('slot = 12\nport = "gpi[2]"\npad_cell = "IPAD2D"\ndriver = "none"',
                                         'slot = 12\nunused = "tie_low"')
        p_file = self.root / "pads_unused.toml"
        p_file.write_text(unused_pad, encoding="utf-8")
        rows = {r.id: r for r in validate_pads(load_pad_map(p_file), self.profile)}
        self.assertEqual(rows["pads.unused"].status, CheckStatus.FAIL)
        self.assertIn("no qualified unused-slot policy 'tie_low'", rows["pads.unused"].message)

    def test_frame_mismatch_and_unknown_key(self):
        # Frame mismatch
        bad_frame = S8_PADS_TOML.replace('frame = "scl-c1d-2mm"', 'frame = "scl-c1d-3mm"')
        p_file = self.root / "pads_frame.toml"
        p_file.write_text(bad_frame, encoding="utf-8")
        rows = {r.id: r for r in validate_pads(load_pad_map(p_file), self.profile)}
        self.assertEqual(rows["pads.frame"].status, CheckStatus.FAIL)

        # Before the first table header this is genuinely a top-level key.
        bad_key = "unknown_key = 123\n" + S8_PADS_TOML
        p_file2 = self.root / "pads_key.toml"
        p_file2.write_text(bad_key, encoding="utf-8")
        with self.assertRaisesRegex(PadError, "unknown keys in pad map: unknown_key"):
            load_pad_map(p_file2)

    def test_unknown_slot_key_is_rejected(self):
        # After the final [[slot]], this belongs to that slot rather than the root.
        path = self.root / "pads_slot_key.toml"
        path.write_text(S8_PADS_TOML + "\nunknown_key = 123\n", encoding="utf-8")
        with self.assertRaisesRegex(PadError, "unknown keys in active slot"):
            load_pad_map(path)

    def test_known_cell_in_unqualified_slot_gives_not_run(self):
        # Swap slots 1 and 2: OPAD04 placed in slot 2, IPAD2D placed in slot 1
        swapped = S8_PADS_TOML.replace('slot = 1\nport = "cs_n"\npad_cell = "OPAD04"\ndriver = "INVR04"\n\n[[slot]]\nslot = 2\nport = "miso"\npad_cell = "IPAD2D"\ndriver = "none"',
                                      'slot = 1\nport = "miso"\npad_cell = "IPAD2D"\ndriver = "none"\n\n[[slot]]\nslot = 2\nport = "cs_n"\npad_cell = "OPAD04"\ndriver = "INVR04"')
        p_file = self.root / "pads_swap.toml"
        p_file.write_text(swapped, encoding="utf-8")
        rows = {r.id: r for r in validate_pads(load_pad_map(p_file), self.profile)}
        self.assertEqual(rows["pads.qualification"].status, CheckStatus.NOT_RUN)
        self.assertIn("not yet physically qualified", rows["pads.qualification"].message)


if __name__ == "__main__":
    unittest.main()
