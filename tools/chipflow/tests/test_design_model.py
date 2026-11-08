from __future__ import annotations

from types import SimpleNamespace
import unittest

from chipflow.runner.results import CheckStatus
from chipflow.stages.design_model import (
    check_design,
    load_design,
    parse_make_deps,
)


class DesignModelTests(unittest.TestCase):
    def test_parse_make_deps_valid_and_escapes(self):
        text = "top.json: elab.ys inc0/nested.vh inc0/width.vh top\\ file.v\n"
        target, prereqs = parse_make_deps(text)
        self.assertEqual(target, "top.json")
        self.assertEqual(prereqs, ("elab.ys", "inc0/nested.vh", "inc0/width.vh", "top file.v"))

    def test_parse_make_deps_continuation(self):
        text = "target.json : \\\n  a.v \\\n  b.v\n"
        target, prereqs = parse_make_deps(text)
        self.assertEqual(target, "target.json")
        self.assertEqual(prereqs, ("a.v", "b.v"))

    def test_parse_make_deps_invalid_cases(self):
        # Two rules
        with self.assertRaises(ValueError):
            parse_make_deps("a: b\nc: d\n")
        # Multiple colons in one line
        with self.assertRaises(ValueError):
            parse_make_deps("a: b: c\n")
        # Forbidden characters
        with self.assertRaises(ValueError):
            parse_make_deps("a: $(VAR)\n")
        with self.assertRaises(ValueError):
            parse_make_deps("a: b # comment\n")
        # Empty or garbage
        with self.assertRaises(ValueError):
            parse_make_deps("")
        with self.assertRaises(ValueError):
            parse_make_deps("no colon here")

    def test_load_design_ports_and_indexing(self):
        data = {
            "modules": {
                "top": {
                    "ports": {
                        "clk": {"direction": "input", "bits": [2]},
                        "upto_bus": {"direction": "input", "bits": [10, 11, 12, 13], "offset": 0, "upto": 1},
                        "downto_bus": {"direction": "output", "bits": [20, 21, 22, 23, 24, 25, 26, 27], "offset": 1},
                    },
                    "cells": {
                        "$dff1": {
                            "type": "$dff",
                            "parameters": {"CLK_POLARITY": "1", "WIDTH": "00000000000000000000000000000001"},
                            "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
                            "connections": {"CLK": [2], "D": [10], "Q": [20]},
                            "attributes": {"src": "staged/top.v:10.1"},
                        }
                    },
                    "netnames": {},
                }
            }
        }
        design = load_design(data, "top")
        self.assertTrue(design.top_present)
        self.assertEqual(design.ports["clk"].canonical_bits, ("clk",))
        # [0:3] upto: 1. HDL indices: 0+(4-1-i) => [3, 2, 1, 0]
        self.assertEqual(
            design.ports["upto_bus"].canonical_bits,
            ("upto_bus[3]", "upto_bus[2]", "upto_bus[1]", "upto_bus[0]"),
        )
        # [8:1] downto: offset 1, upto: 0. HDL indices: 1+i => [1, 2, 3, 4, 5, 6, 7, 8]
        self.assertEqual(
            design.ports["downto_bus"].canonical_bits,
            tuple(f"downto_bus[{i}]" for i in range(1, 9)),
        )
        cell = design.cells["$dff1"]
        self.assertEqual(cell.parameters["CLK_POLARITY"], 1)
        self.assertEqual(cell.parameters["WIDTH"], 1)

    def test_s8_shaped_clock_usage_with_and_without_derived_outputs(self):
        # Model S8: clk feeds dff.CLK and or_gate.A; or_gate feeds not_gate; not_gate feeds sck
        # reset feeds dff.D (via mux/logic)
        data = {
            "modules": {
                "s8": {
                    "ports": {
                        "clk": {"direction": "input", "bits": [1]},
                        "rst": {"direction": "input", "bits": [2]},
                        "idle": {"direction": "input", "bits": [3]},
                        "sck": {"direction": "output", "bits": [4]},
                    },
                    "cells": {
                        "dff_inst": {
                            "type": "$dff",
                            "parameters": {"CLK_POLARITY": "1", "WIDTH": "1"},
                            "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
                            "connections": {"CLK": [1], "D": [2], "Q": [5]},
                            "attributes": {"src": "0000_s8.v:50.1"},
                        },
                        "or_gate": {
                            "type": "$or",
                            "parameters": {},
                            "port_directions": {"A": "input", "B": "input", "Y": "output"},
                            "connections": {"A": [1], "B": [3], "Y": [6]},
                            "attributes": {"src": "0000_s8.v:183.1"},
                        },
                        "not_gate": {
                            "type": "$not",
                            "parameters": {},
                            "port_directions": {"A": "input", "Y": "output"},
                            "connections": {"A": [6], "Y": [4]},
                            "attributes": {"src": "0000_s8.v:184.1"},
                        },
                    },
                    "netnames": {},
                }
            }
        }
        design = load_design(data, "s8")
        clock = SimpleNamespace(port="clk")
        reset = SimpleNamespace(port="rst")
        path_map = {"0000_s8.v": "rtl/s8.v"}

        # Without derived_outputs: must FAIL elab.clock_usage and cite rtl/s8.v:183.1
        rows = {r.id: r for r in check_design(design, clock, reset, derived_outputs=(), path_map=path_map)}
        self.assertEqual(rows["elab.clock_usage"].status, CheckStatus.FAIL)
        self.assertIn("rtl/s8.v:183.1", rows["elab.clock_usage"].message)

        # With derived_outputs: must PASS
        derived = [{"port": "sck", "check": "s8.sck_gate"}]
        rows = {r.id: r for r in check_design(design, clock, reset, derived_outputs=derived, path_map=path_map)}
        self.assertEqual(rows["elab.clock_usage"].status, CheckStatus.PASS)
        self.assertEqual(rows["elab.state_bits"].status, CheckStatus.PASS)
        self.assertEqual(rows["elab.state_bits"].message, "1 state bits")

    def test_clock_cone_reaches_dff_fails(self):
        # If clock cone feeds a $dff D pin
        data = {
            "modules": {
                "top": {
                    "ports": {
                        "clk": {"direction": "input", "bits": [1]},
                        "rst": {"direction": "input", "bits": [2]},
                        "sck": {"direction": "output", "bits": [4]},
                    },
                    "cells": {
                        "dff_inst": {
                            "type": "$dff",
                            "parameters": {"CLK_POLARITY": "1", "WIDTH": "1"},
                            "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
                            "connections": {"CLK": [1], "D": [6], "Q": [5]},
                            "attributes": {"src": "top.v:30.1"},
                        },
                        "or_gate": {
                            "type": "$or",
                            "parameters": {},
                            "port_directions": {"A": "input", "B": "input", "Y": "output"},
                            "connections": {"A": [1], "B": [2], "Y": [6]},
                            "attributes": {"src": "top.v:40.1"},
                        },
                    },
                    "netnames": {},
                }
            }
        }
        design = load_design(data, "top")
        clock = SimpleNamespace(port="clk")
        reset = SimpleNamespace(port="rst")
        derived = [{"port": "sck"}]
        rows = {r.id: r for r in check_design(design, clock, reset, derived_outputs=derived)}
        self.assertEqual(rows["elab.clock_usage"].status, CheckStatus.FAIL)
        self.assertIn("reaches $dff", rows["elab.clock_usage"].message)

    def test_negative_rule_checks(self):
        clock = SimpleNamespace(port="clk")
        reset = SimpleNamespace(port="rst")

        # Top missing
        d_notop = load_design({"modules": {}}, "missing_top")
        rows = {r.id: r for r in check_design(d_notop, clock, reset)}
        self.assertEqual(rows["elab.top"].status, CheckStatus.FAIL)

        def make_base_mod(extra_ports=None, extra_cells=None, extra_netnames=None):
            ports = {
                "clk": {"direction": "input", "bits": [1]},
                "rst": {"direction": "input", "bits": [2]},
                "out": {"direction": "output", "bits": [3]},
            }
            if extra_ports:
                ports.update(extra_ports)
            cells = {
                "dff": {
                    "type": "$dff",
                    "parameters": {"CLK_POLARITY": "1", "WIDTH": "1"},
                    "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
                    "connections": {"CLK": [1], "D": [2], "Q": [3]},
                    "attributes": {"src": "top.v:10"},
                }
            }
            if extra_cells:
                cells.update(extra_cells)
            netnames = {}
            if extra_netnames:
                netnames.update(extra_netnames)
            return {"modules": {"top": {"ports": ports, "cells": cells, "netnames": netnames}}}

        # Inout
        d = load_design(make_base_mod(extra_ports={"io": {"direction": "inout", "bits": [9]}}), "top")
        self.assertEqual(check_design(d, clock, reset)[1].id, "elab.inout")
        self.assertEqual(check_design(d, clock, reset)[1].status, CheckStatus.FAIL)

        # Tristate z
        d = load_design(make_base_mod(extra_ports={"tri": {"direction": "output", "bits": ["z"]}}), "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.tristate"].status, CheckStatus.FAIL)

        # Blackbox
        d = load_design(make_base_mod(extra_cells={"bb": {"type": "RAM32", "port_directions": {}, "connections": {}}}), "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.blackbox"].status, CheckStatus.FAIL)

        # Unsupported cell type
        d = load_design(make_base_mod(extra_cells={"bb": {"type": "$memrd", "port_directions": {}, "connections": {}}}), "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.cell_types"].status, CheckStatus.FAIL)

        # Async / latch
        d = load_design(make_base_mod(extra_cells={"l": {"type": "$dlatch", "port_directions": {}, "connections": {}}}), "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.async_or_latch"].status, CheckStatus.FAIL)

        # Clock polarity
        d = load_design(make_base_mod(extra_cells={"dff2": {
            "type": "$dff",
            "parameters": {"CLK_POLARITY": "0", "WIDTH": "1"},
            "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
            "connections": {"CLK": [1], "D": [2], "Q": [8]},
        }}), "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.clock_polarity"].status, CheckStatus.FAIL)

        # Single clock (different clock bit)
        d = load_design(make_base_mod(extra_cells={"dff2": {
            "type": "$dff",
            "parameters": {"CLK_POLARITY": "1", "WIDTH": "1"},
            "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
            "connections": {"CLK": [99], "D": [2], "Q": [8]},
        }}), "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.single_clock"].status, CheckStatus.FAIL)

        # Init values
        d = load_design(make_base_mod(extra_netnames={"reg_r": {"attributes": {"init": "1"}}}), "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.init_values"].status, CheckStatus.FAIL)

        # Reset not used
        d_no_rst = make_base_mod()
        d_no_rst["modules"]["top"]["cells"]["dff"]["connections"]["D"] = [99]
        d = load_design(d_no_rst, "top")
        rows = {r.id: r for r in check_design(d, clock, reset)}
        self.assertEqual(rows["elab.reset_used"].status, CheckStatus.FAIL)

    @staticmethod
    def _clock_rows(ports, cells, derived=()):
        design = load_design({"modules": {"t": {"ports": ports, "cells": cells, "netnames": {}}}}, "t")
        rows = check_design(design, SimpleNamespace(port="clk"), SimpleNamespace(port="rst"), derived_outputs=derived)
        return {r.id: r for r in rows}

    FLOP = {"type": "$dff", "parameters": {"CLK_POLARITY": "1", "WIDTH": "1"},
            "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
            "connections": {"CLK": [2], "D": [3], "Q": [9]}}

    def test_clock_feedthrough_to_output_is_a_clock_use(self):
        # `assign co = clk;`: Yosys gives the output port the clock's own bit, with no cell.
        ports = {"clk": {"direction": "input", "bits": [2]}, "rst": {"direction": "input", "bits": [3]},
                 "co": {"direction": "output", "bits": [2]}}
        rows = self._clock_rows(ports, {"ff": self.FLOP})
        self.assertEqual(rows["elab.clock_usage"].status, CheckStatus.FAIL)
        self.assertIn("drives output bit(s) directly: co", rows["elab.clock_usage"].message)
        declared = self._clock_rows(ports, {"ff": self.FLOP}, [{"port": "co", "check": "t.co_gate"}])
        self.assertEqual(declared["elab.clock_usage"].status, CheckStatus.PASS)

    def test_derived_bus_must_be_clock_derived_on_every_bit(self):
        ports = {"clk": {"direction": "input", "bits": [2]}, "rst": {"direction": "input", "bits": [3]},
                 "o": {"direction": "output", "bits": [4, 9]}}
        inv = {"type": "$not", "parameters": {}, "port_directions": {"A": "input", "Y": "output"},
               "connections": {"A": [2], "Y": [4]}}
        rows = self._clock_rows(ports, {"inv": inv, "ff": self.FLOP}, [{"port": "o", "check": "t.o_gate"}])
        self.assertEqual(rows["elab.clock_usage"].status, CheckStatus.FAIL)
        self.assertIn("expected ['o[0]', 'o[1]']", rows["elab.clock_usage"].message)


if __name__ == "__main__":
    unittest.main()
