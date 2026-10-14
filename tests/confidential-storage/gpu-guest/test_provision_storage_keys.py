"""Credential rotation/conflict regression tests; synthetic material only."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "keys", Path(__file__).with_name("provision-storage-keys.py")
)
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


class StorageKeyTests(unittest.TestCase):
    def test_merge_preserves_existing_values_and_is_idempotent(self):
        original = {"legacy": "retained", "another": "unchanged"}
        merged = keys.merge(original, "a" * 64, "b" * 64)
        self.assertEqual(original, {"legacy": "retained", "another": "unchanged"})
        self.assertEqual(keys.merge(merged, "a" * 64, "b" * 64), merged)
        self.assertEqual(merged["legacy"], "retained")

    def test_conflicting_model_or_data_key_never_rotates(self):
        for tag in (keys.MODEL, keys.DATA):
            with self.assertRaises(ValueError):
                keys.merge({tag: "existing"}, "a" * 64, "b" * 64)

    def test_key_files_must_be_private_regular_exact_length(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "key"
            p.write_bytes(bytes(32))
            p.chmod(0o600)
            self.assertEqual(keys.read_key(p), "0" * 64)
            p.chmod(0o644)
            with self.assertRaises(ValueError):
                keys.read_key(p)
            p.chmod(0o600)
            p.write_bytes(bytes(31))
            with self.assertRaises(ValueError):
                keys.read_key(p)
            link = p.with_name("link")
            link.symlink_to(p)
            with self.assertRaises(OSError):
                keys.read_key(link)
