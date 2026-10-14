"""Regression: KBS's Regorus storage key includes the .rego suffix."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


class KbsPackageTests(unittest.TestCase):
    def test_deny_all_is_present_at_the_effective_upstream_storage_key(self):
        path = Path(__file__).parent / "native-guest/prepare-kbs.py"
        spec = importlib.util.spec_from_file_location("native_kbs_package", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "kbs"
            module.prepare(root, "/private-test")
            effective = root / "storage/kbs/resource-policy.rego"
            self.assertEqual(
                effective.read_text(),
                "package policy\nimport rego.v1\ndefault allow := false\n",
            )
            self.assertFalse((root / "storage/kbs/resource-policy").exists())
            self.assertFalse((root / "storage/repository").exists())


if __name__ == "__main__":
    unittest.main()
