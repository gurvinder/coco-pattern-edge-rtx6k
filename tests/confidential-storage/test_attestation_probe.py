"""Archive/input rejection tests for the isolated no-secret probe builder."""

import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "probe_builder", ROOT / "scripts/confidential-storage/build-attestation-probe.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ProbeBuilderTests(unittest.TestCase):
    def test_changed_base_and_client_are_rejected_before_unpack(self):
        with self.assertRaisesRegex(ValueError, "unapproved"):
            builder.build(b"untrusted initrd", b"untrusted executable", b"{}")

    def test_archive_parser_preserves_prefix_and_rejects_ambiguity(self):
        record = builder.entry("usr", stat.S_IFDIR | 0o755, b"", 1)
        trailer = builder.entry("TRAILER!!!", 0, b"", 2)
        prefix, names, inode = builder.read_archive(record + trailer + bytes(32))
        self.assertEqual((prefix, names, inode), (record, {"usr"}, 2))
        bad = [
            record + record + trailer,
            record + trailer + b"unexpected second archive",
            record[:-1],
            builder.entry("../outside", stat.S_IFREG | 0o644, b"data", 1) + trailer,
            builder.entry("/outside", stat.S_IFREG | 0o644, b"data", 1) + trailer,
        ]
        for raw in bad:
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                builder.read_archive(raw)

    def test_only_fixed_diagnostic_public_binding_is_accepted(self):
        value = {
            "storage-binding": {
                "protocol": "confidential-storage-request/v1",
                "audience": "https://storage-test.invalid/v1",
                "nonce": "a" * 64,
                "request_sha256": "b" * 64,
                "key": {
                    "kty": "EC",
                    "crv": "P-256",
                    "x": "axfR8uEsQkf4vOblY6RA8ncDfYEt6zOg9KE5RdiYwpY",
                    "y": "T-NC4v4af5uO5-tKfA-eFivOM1drMV7Oy7ZAaDe_UfU",
                },
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"
            path.write_text(json.dumps(value, indent=2))
            canonical = builder.runtime_data(path)
            self.assertEqual(json.loads(canonical), value)
            value["storage-binding"]["key"]["d"] = "never-include-private-key"
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                builder.runtime_data(path)
            del value["storage-binding"]["key"]["d"]
            value["storage-binding"]["audience"] = "https://production.example/v1"
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                builder.runtime_data(path)


if __name__ == "__main__":
    unittest.main()
