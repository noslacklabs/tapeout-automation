"""Tests for semantic validation (Phase 7: G4, D1)."""

from pathlib import Path
import tempfile
import unittest

from chipflow.cli import snapshot_project
from chipflow.config import ConfigError, load_project
from chipflow.config.pads import PadConfig, PadSlotConfig
from chipflow.runner.results import CheckStatus, RELEASE_REQUIRED_CHECKS
from chipflow.stages.design_model import Design, PortInfo
from chipflow.technologies.profile import load_profile
from chipflow.validation import validate_project_configuration


PROJECT_BASE = """
schema_version = 1
[project]
name = "my_chip"
top = "my_top"
sources = ["rtl.v"]
[technology]
id = "scl_c1d"
frame = "scl-c1d-2mm"
profile_version = "0.1.0"
top_cell = "OS0000"
mode = "development"
development_identifiers = true
[clock]
port = "clk"
period_ns = 100.0
waveform_ns = [0.0, 50.0]
transition_ns = 2.5
uncertainty_ns = 0.0
[reset]
port = "rst"
kind = "synchronous"
active = "high"
[io]
corners = ["min", "typ", "max"]
inputs = [
    { port = "rst", min_ns = 1.0, max_ns = 3.0 },
    { port = "data_in", min_ns = 1.0, max_ns = 3.0 }
]
outputs = [
    { port = "data_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }
]
[pads]
mapping = "pads.toml"
[build]
root = "builds"
seed = 42
threads = 1
timeout_seconds = 60
[verification]
required_checks = ["behavior"]
"""

PADS_TOML = """
schema_version = 1
frame = "scl-c1d-2mm"
profile_version = "0.1.0"
slot = [
    { slot = 1, port = "data_out", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 2, port = "rst", pad_cell = "IPAD2D", driver = "none" },
    { slot = 3, port = "p3", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 4, port = "clk", pad_cell = "IPAD30", driver = "none" },
    { slot = 5, port = "p5", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 6, port = "p6", pad_cell = "IPAD3D", driver = "none" },
    { slot = 7, port = "p7", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 8, port = "data_in", pad_cell = "IPAD2D", driver = "none" },
    { slot = 9, port = "p9", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 10, port = "p10", pad_cell = "IPAD2D", driver = "none" },
    { slot = 11, port = "p11", pad_cell = "OPAD04", driver = "INVR04" },
    { slot = 12, port = "p12", pad_cell = "IPAD2D", driver = "none" }
]
"""


def make_design(ports_dict: dict[str, tuple[str, int, int, bool]]) -> Design:
    ports = {}
    for name, (direction, width, offset, upto) in ports_dict.items():
        if width == 1 and offset == 0 and not upto:
            cbits = (name,)
        else:
            cbits = tuple(f"{name}[{offset + (width - 1 - i if upto else i)}]" for i in range(width))
        ports[name] = PortInfo(
            name=name,
            direction=direction,
            width=width,
            offset=offset,
            upto=upto,
            signed=False,
            bits=tuple(range(width)),
            canonical_bits=cbits,
        )
    return Design(top="my_top", top_present=True, ports=ports, cells={}, netnames_with_init=())


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "rtl.v").write_text("module my_top; endmodule\n", encoding="utf-8")
        (self.root / "pads.toml").write_text(PADS_TOML, encoding="utf-8")
        self.chip_file = self.root / "chip.toml"
        self.chip_file.write_text(PROJECT_BASE, encoding="utf-8")
        self.profile = load_profile()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_derived_outputs_parsing(self) -> None:
        # Valid derived output
        text = PROJECT_BASE.replace(
            "transition_ns = 2.5",
            'transition_ns = 2.5\nderived_outputs = [{ port = "data_out", check = "my.derived_check" }]'
        )
        self.chip_file.write_text(text, encoding="utf-8")
        cfg = load_project(self.chip_file)
        self.assertEqual(len(cfg.clock.derived_outputs), 1)
        self.assertEqual(cfg.clock.derived_outputs[0].port, "data_out")
        self.assertEqual(cfg.clock.derived_outputs[0].check, "my.derived_check")
        self.assertIn("my.derived_check", cfg.verification.required_checks)

        # Derived output port not in outputs
        bad_text = PROJECT_BASE.replace(
            "transition_ns = 2.5",
            'transition_ns = 2.5\nderived_outputs = [{ port = "unknown_port", check = "my.check" }]'
        )
        self.chip_file.write_text(bad_text, encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_project(self.chip_file)

        # Duplicate derived output port
        dup_text = PROJECT_BASE.replace(
            "transition_ns = 2.5",
            'transition_ns = 2.5\nderived_outputs = [{ port = "data_out", check = "c1" }, { port = "data_out", check = "c2" }]'
        )
        self.chip_file.write_text(dup_text, encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_project(self.chip_file)

    def test_clock_and_reset_validation(self) -> None:
        run_dir = self.root / "run"
        run_dir.mkdir()
        cfg = load_project(self.chip_file)
        snapshot_project(cfg, run_dir)

        # 1. Missing clock in design
        design = make_design({"rst": ("input", 1, 0, False), "data_in": ("input", 1, 0, False), "data_out": ("output", 1, 0, False)})
        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        self.assertEqual(res.status, CheckStatus.FAIL)
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["config.clock"].status, CheckStatus.FAIL)

        # 2. Clock is output
        design = make_design({"clk": ("output", 1, 0, False), "rst": ("input", 1, 0, False), "data_in": ("input", 1, 0, False), "data_out": ("output", 1, 0, False)})
        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        self.assertEqual(res.status, CheckStatus.FAIL)
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["config.clock"].status, CheckStatus.FAIL)

        # 3. Clock is multibit
        design = make_design({"clk": ("input", 2, 0, False), "rst": ("input", 1, 0, False), "data_in": ("input", 1, 0, False), "data_out": ("output", 1, 0, False)})
        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["config.clock"].status, CheckStatus.FAIL)

        # 4. Reset is multibit
        design = make_design({"clk": ("input", 1, 0, False), "rst": ("input", 2, 0, False), "data_in": ("input", 1, 0, False), "data_out": ("output", 1, 0, False)})
        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["config.reset"].status, CheckStatus.FAIL)

    def test_derived_outputs_require_separate_nonreserved_checks(self) -> None:
        reserved = (*RELEASE_REQUIRED_CHECKS, "snapshot", "implementation", "elab.clock_usage",
                    "config.clock", "pads.schema", "netlist.sequential_clock")
        for check in reserved:
            with self.subTest(check=check):
                text = PROJECT_BASE.replace(
                    "transition_ns = 2.5",
                    f'transition_ns = 2.5\nderived_outputs = [{{ port = "data_out", check = "{check}" }}]'
                )
                self.chip_file.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, "separate project check"):
                    load_project(self.chip_file)

    def test_derived_outputs_cannot_share_one_check(self) -> None:
        text = PROJECT_BASE.replace(
            "transition_ns = 2.5",
            'transition_ns = 2.5\nderived_outputs = [{ port = "data_out", check = "my.gate" }, '
            '{ port = "clock_out", check = "my.gate" }]'
        ).replace(
            '{ port = "data_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }',
            '{ port = "data_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },\n'
            '    { port = "clock_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }'
        )
        self.chip_file.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "duplicate check 'my.gate'"):
            load_project(self.chip_file)

    def test_derived_check_may_also_be_explicitly_required(self) -> None:
        text = PROJECT_BASE.replace(
            "transition_ns = 2.5",
            'transition_ns = 2.5\nderived_outputs = [{ port = "data_out", check = "my.gate" }]'
        ).replace('required_checks = ["behavior"]', 'required_checks = ["behavior", "my.gate"]')
        self.chip_file.write_text(text, encoding="utf-8")
        cfg = load_project(self.chip_file)
        self.assertEqual(cfg.verification.required_checks.count("my.gate"), 1)

    def test_io_coverage_validation(self) -> None:
        run_dir = self.root / "run"
        run_dir.mkdir()
        cfg = load_project(self.chip_file)
        snapshot_project(cfg, run_dir)

        # Design with extra unconstrained input
        design = make_design({
            "clk": ("input", 1, 0, False),
            "rst": ("input", 1, 0, False),
            "data_in": ("input", 1, 0, False),
            "extra_in": ("input", 1, 0, False),
            "data_out": ("output", 1, 0, False),
        })
        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["config.io_coverage"].status, CheckStatus.FAIL)
        self.assertIn("extra_in", row_map["config.io_coverage"].message)

        # Design with multibit output missing bus index
        design = make_design({
            "clk": ("input", 1, 0, False),
            "rst": ("input", 1, 0, False),
            "data_in": ("input", 1, 0, False),
            "data_out": ("output", 2, 0, False),
        })
        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["config.io_coverage"].status, CheckStatus.FAIL)
        self.assertIn("missing bit index", row_map["config.io_coverage"].message)

    def test_full_validation_pass_and_artifacts(self) -> None:
        run_dir = self.root / "run"
        run_dir.mkdir()
        # Design matching PADS_TOML and PROJECT_BASE perfectly
        ports = {
            "clk": ("input", 1, 0, False),
            "rst": ("input", 1, 0, False),
            "data_in": ("input", 1, 0, False),
            "data_out": ("output", 1, 0, False),
            "p3": ("output", 1, 0, False),
            "p5": ("output", 1, 0, False),
            "p6": ("input", 1, 0, False),
            "p7": ("output", 1, 0, False),
            "p9": ("output", 1, 0, False),
            "p10": ("input", 1, 0, False),
            "p11": ("output", 1, 0, False),
            "p12": ("input", 1, 0, False),
        }
        design = make_design(ports)

        # Update chip.toml to cover all inputs and outputs
        full_project = PROJECT_BASE.replace(
            'inputs = [\n    { port = "rst", min_ns = 1.0, max_ns = 3.0 },\n    { port = "data_in", min_ns = 1.0, max_ns = 3.0 }\n]',
            '''inputs = [
    { port = "rst", min_ns = 1.0, max_ns = 3.0 },
    { port = "data_in", min_ns = 1.0, max_ns = 3.0 },
    { port = "p6", min_ns = 1.0, max_ns = 3.0 },
    { port = "p10", min_ns = 1.0, max_ns = 3.0 },
    { port = "p12", min_ns = 1.0, max_ns = 3.0 }
]'''
        ).replace(
            'outputs = [\n    { port = "data_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }\n]',
            '''outputs = [
    { port = "data_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p3", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p5", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p7", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p9", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p11", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }
]'''
        )
        self.chip_file.write_text(full_project, encoding="utf-8")
        cfg = load_project(self.chip_file)
        snapshot_project(cfg, run_dir)

        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        self.assertEqual(res.status, CheckStatus.PASS)
        self.assertTrue((run_dir / "validation.json").is_file())
        self.assertTrue((run_dir / "pad-plan.json").is_file())

    def test_not_run_when_pad_unqualified(self) -> None:
        run_dir = self.root / "run"
        run_dir.mkdir()
        # Change a pad slot to an unqualified slot (e.g. IPAD30 in slot 12, which is only qualified in slot 4)
        bad_pads = PADS_TOML.replace(
            '{ slot = 12, port = "p12", pad_cell = "IPAD2D", driver = "none" }',
            '{ slot = 12, port = "p12", pad_cell = "IPAD30", driver = "none" }'
        )
        (self.root / "pads.toml").write_text(bad_pads, encoding="utf-8")
        full_project = PROJECT_BASE.replace(
            'inputs = [\n    { port = "rst", min_ns = 1.0, max_ns = 3.0 },\n    { port = "data_in", min_ns = 1.0, max_ns = 3.0 }\n]',
            '''inputs = [
    { port = "rst", min_ns = 1.0, max_ns = 3.0 },
    { port = "data_in", min_ns = 1.0, max_ns = 3.0 },
    { port = "p6", min_ns = 1.0, max_ns = 3.0 },
    { port = "p10", min_ns = 1.0, max_ns = 3.0 },
    { port = "p12", min_ns = 1.0, max_ns = 3.0 }
]'''
        ).replace(
            'outputs = [\n    { port = "data_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }\n]',
            '''outputs = [
    { port = "data_out", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p3", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p5", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p7", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p9", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 },
    { port = "p11", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }
]'''
        )
        self.chip_file.write_text(full_project, encoding="utf-8")
        cfg = load_project(self.chip_file)
        snapshot_project(cfg, run_dir)
        ports = {
            "clk": ("input", 1, 0, False),
            "rst": ("input", 1, 0, False),
            "data_in": ("input", 1, 0, False),
            "data_out": ("output", 1, 0, False),
            "p3": ("output", 1, 0, False),
            "p5": ("output", 1, 0, False),
            "p6": ("input", 1, 0, False),
            "p7": ("output", 1, 0, False),
            "p9": ("output", 1, 0, False),
            "p10": ("input", 1, 0, False),
            "p11": ("output", 1, 0, False),
            "p12": ("input", 1, 0, False),
        }
        design = make_design(ports)
        res, rows = validate_project_configuration(cfg, run_dir, design, self.profile)
        row_map = {r.id: r for r in rows}
        self.assertEqual(row_map["pads.qualification"].status, CheckStatus.NOT_RUN)
        self.assertEqual(res.status, CheckStatus.NOT_RUN)


if __name__ == "__main__":
    unittest.main()
