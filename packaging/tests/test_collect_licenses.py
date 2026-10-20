import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "collect_licenses.py"
PROJECT_LICENSE = SCRIPT.parent.parent / "LICENSE"


class CollectLicensesTests(unittest.TestCase):
    def run_collector(self, license_text):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheels = root / "wheels"
            wheels.mkdir()
            with zipfile.ZipFile(wheels / "example-1.0-py3-none-any.whl", "w") as wheel:
                wheel.writestr("example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\nLicense: MIT\n")
                wheel.writestr("example-1.0.dist-info/AUTHORS.rst", "Example authors\n")
                if license_text is not None:
                    wheel.writestr("example-1.0.dist-info/LICENSE", license_text)
            supplements = root / "supplements"
            supplements.mkdir()
            sources = root / "sources.json"
            sources.write_text("[]\n")
            result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--platform", "macos",
                "--wheels", str(wheels), "--tcl-tk-version", "8.6.18", "--supplements", str(supplements),
                "--sources", str(sources), "--output", str(root / "output")], capture_output=True, text=True)
            inventory = json.loads((root / "output/inventory.json").read_text())
            return result, inventory

    def test_authors_alone_is_not_a_license(self):
        result, inventory = self.run_collector(None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(inventory["missing_notices"]), 1)
        self.assertFalse(inventory["components"][0]["license_body_present"])

    def test_original_license_passes_coverage(self):
        result, inventory = self.run_collector(PROJECT_LICENSE.read_bytes())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(inventory["missing_notices"], [])
        self.assertTrue(inventory["components"][0]["license_body_present"])


if __name__ == "__main__":
    unittest.main()
