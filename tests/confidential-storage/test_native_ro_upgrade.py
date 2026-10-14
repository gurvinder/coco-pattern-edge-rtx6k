"""RO policy upgrades preserve history and refuse an outstanding key owner."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "components/confidential-storage-authority"))
from authority import Authority, AttestedPeer, Denied  # noqa: E402
from publication import from_publisher, profile_sha256  # noqa: E402


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "upgrade", Path(__file__).parent / "native-guest/upgrade-ro-policy.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.authority = Authority(self.state / "authority.db")
        self.peer = AttestedPeer("a" * 64, "b" * 64)
        self.binding = {
            "access": "ro",
            "destination": "/models",
            "container_name": "model",
            "manifest_uri": "kbs:///storage-test/manifests/qwen-luks-v1",
        }
        record = self.authority.register(
            "before",
            self.peer.policy_sha256,
            profile_sha256(),
            "kbs:///storage-test/model-keys/qwen-luks-v1",
            mount_binding=self.binding,
        )
        self.evidence = (
            ROOT / "tests/confidential-storage/evidence/2026-10-13/model-populator"
        )
        manifest = from_publisher(
            record,
            json.loads((self.evidence / "guest-manifest.unsigned.json").read_text()),
            json.loads((self.evidence / "source.json").read_text()),
        )
        self.authority.approve_published_model("before", manifest)
        self.guest = self.state / "guest-public.json"
        self.guest.write_text(
            json.dumps(
                {
                    "expected": {"volume_id": "before"},
                    "authority": {
                        "policy_sha256": "a" * 64,
                        "receipt_key_id": "retained",
                    },
                    "manifest": list(manifest),
                }
            )
        )
        (self.state / "server.json").write_text(
            json.dumps(
                {
                    "policy_sha256": "a" * 64,
                    "receipt_key": "retained",
                    "database": "retained",
                }
            )
        )
        self.policy = self.state / "policy"
        self.policy.mkdir()
        (self.policy / "policy.rego").write_text("public test policy")
        (self.policy / "initdata.toml").write_text("public test InitData")

    def upgrade(self):
        self.module.upgrade(self.state, self.guest, self.policy, self.evidence, "after")

    def test_preserves_history_header_and_credential(self):
        before = self.authority.inspect("before")
        self.upgrade()
        after = self.authority.inspect("after")
        self.assertTrue(self.authority.inspect("before")["revoked"])
        self.assertEqual(after["state"], "Ready")
        self.assertIsNone(after["owner"])
        for field in ("header_sha256", "credential_uri", "profile_sha256"):
            self.assertEqual(after[field], before[field])
        with sqlite3.connect(self.state / "authority.db") as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM volumes").fetchone()[0], 2
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM events WHERE volume_id='before'"
                ).fetchone()[0],
                3,
            )
        server = json.loads((self.state / "server.json").read_text())
        self.assertEqual(server["receipt_key"], "retained")
        self.assertEqual(server["database"], "retained")
        with self.assertRaises((ValueError, Denied)):
            self.upgrade()

    def test_refuses_owned_record_without_mutation(self):
        self.authority.begin("before", self.peer, "active", "reopen", 1)
        before = self.authority.inspect("before")
        with self.assertRaises(ValueError):
            self.upgrade()
        self.assertEqual(self.authority.inspect("before"), before)
        with self.assertRaises(Denied):
            self.authority.inspect("after")


if __name__ == "__main__":
    unittest.main()
