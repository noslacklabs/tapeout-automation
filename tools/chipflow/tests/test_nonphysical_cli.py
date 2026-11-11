from pathlib import Path
import subprocess
import sys
import unittest

import chipflow


class NonphysicalCliTests(unittest.TestCase):
    def test_help_imports_without_any_site_packages(self):
        # -S removes all optional EDA packages, including on the configured
        # Linux machine. Use the installed package's location in wheel checks.
        parent = str(Path(chipflow.__file__).resolve().parent.parent)
        code = f"import sys; sys.path.insert(0, {parent!r}); from chipflow.cli import main; main(['--help'])"
        result = subprocess.run([sys.executable, "-I", "-S", "-c", code],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"doctor", result.stdout)
