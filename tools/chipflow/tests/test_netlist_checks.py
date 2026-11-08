"""Tests for mapped netlist checker (Phase 9: G8)."""

from pathlib import Path
import unittest

from chipflow.runner.results import CheckStatus
from chipflow.stages.netlist_checks import check_mapped_netlist
from chipflow.technologies.profile import load_profile


SYNTHETIC_CLEAN = {
    "modules": {
        "my_top": {
            "ports": {
                "clk": {"direction": "input", "bits": [2]},
                "in1": {"direction": "input", "bits": [3]},
                "out1": {"direction": "output", "bits": [4]},
            },
            "cells": {
                "u_inv": {
                    "type": "INVR01",
                    "connections": {"A": [3], "OUT1": [5]},
                },
                "u_dff": {
                    "type": "DFFL11",
                    "connections": {"C": [2], "D": [5], "Q": [4]},
                },
            },
        }
    }
}


class NetlistChecksTests(unittest.TestCase):
    def test_clean_mapped_netlist_passes(self) -> None:
        allowed = {"INVR01", "DFFL11"}
        seq = {"DFFL11"}
        rows = check_mapped_netlist(SYNTHETIC_CLEAN, "my_top", allowed, seq, "clk")
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual(r.status, CheckStatus.PASS, f"Failed row: {r.id}: {r.message}")

    def test_dollar_primitives_remain_fails(self) -> None:
        data = {
            "modules": {
                "my_top": {
                    "ports": {"clk": {"direction": "input", "bits": [2]}},
                    "cells": {
                        "u_dff": {"type": "$dff", "connections": {"CLK": [2]}},
                    },
                }
            }
        }
        rows = check_mapped_netlist(data, "my_top", {"$dff"}, {"$dff"}, "clk")
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["netlist.primitives"].status, CheckStatus.FAIL)
        self.assertIn("unmapped primitives", row_map["netlist.primitives"].message)

    def test_unsupported_cell_fails(self) -> None:
        data = {
            "modules": {
                "my_top": {
                    "ports": {"clk": {"direction": "input", "bits": [2]}},
                    "cells": {
                        "u_cell": {"type": "EXCLUDED_CELL", "connections": {}},
                    },
                }
            }
        }
        rows = check_mapped_netlist(data, "my_top", {"INVR01"}, set(), "clk")
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["netlist.allowed_cells"].status, CheckStatus.FAIL)
        self.assertIn("unsupported or excluded", row_map["netlist.allowed_cells"].message)

    def test_sequential_clock_mismatch_fails(self) -> None:
        data = {
            "modules": {
                "my_top": {
                    "ports": {
                        "clk": {"direction": "input", "bits": [2]},
                        "other_clk": {"direction": "input", "bits": [3]},
                    },
                    "cells": {
                        "u_dff": {"type": "DFFL11", "connections": {"C": [3]}},
                    },
                }
            }
        }
        rows = check_mapped_netlist(data, "my_top", {"DFFL11"}, {"DFFL11"}, "clk")
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["netlist.sequential_clock"].status, CheckStatus.FAIL)
        self.assertIn("not connected to clk", row_map["netlist.sequential_clock"].message)

    def test_direct_constant_ties_fail_p04(self) -> None:
        data = {
            "modules": {
                "my_top": {
                    "ports": {"clk": {"direction": "input", "bits": [2]}},
                    "cells": {
                        "u_inv": {"type": "INVR01", "connections": {"A": ["0"]}},
                    },
                }
            }
        }
        rows = check_mapped_netlist(data, "my_top", {"INVR01"}, set(), "clk")
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["netlist.constants"].status, CheckStatus.FAIL)
        self.assertIn("violates P04", row_map["netlist.constants"].message)

    def test_profile_excluded_cells_loaded(self) -> None:
        profile = load_profile()
        self.assertTrue(len(profile.excluded_cells) >= 38)
        p01_cells = {e.cell for e in profile.excluded_cells if e.workaround == "P01"}
        self.assertIn("DFCL11", p01_cells)
        self.assertIn("MX2101", p01_cells)
        p02_cells = {e.cell for e in profile.excluded_cells if e.workaround == "P02"}
        self.assertIn("ADNR01", p02_cells)
        self.assertIn("XOR201", p02_cells)


if __name__ == "__main__":
    unittest.main()
