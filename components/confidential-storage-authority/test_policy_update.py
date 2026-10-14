"""Policy upgrades preserve initialized storage and cannot take over an owner."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from authority import AttestedPeer, Denied
from test_receipts import initialized


class PolicyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a, self.peer, _, _ = initialized(Path(self.tmp.name) / "authority.db")

    def update(self, names=None, old="a", new="f"):
        return self.a.update_workload_policy(
            names or ["scratch"], old * 64, new * 64, "e" * 64
        )

    def test_preserves_disk_identity_and_history_and_fences_old_policy(self):
        before = self.a.inspect("scratch")
        after = self.update()[0]
        for key in before:
            if key != "policy_sha256":
                self.assertEqual(before[key], after[key], key)
        with self.assertRaises(Denied):
            self.a.begin("scratch", self.peer, "old", "reopen", 1)
        new = AttestedPeer("f" * 64, "1" * 64)
        r = self.a.begin("scratch", new, "new", "reopen", 1)
        self.a.close("scratch", new, r["owner"]["activation_id"])
        with self.assertRaises(Denied):
            self.update(old="f", new="a")
        with self.assertRaises(Denied):
            self.a.begin("scratch", new, "init-1", "reopen", 1)

    def test_owned_revoked_unready_and_stale_records_rejected(self):
        r = self.a.begin("scratch", self.peer, "open", "reopen", 1)
        with self.assertRaises(Denied):
            self.update()
        self.a.close("scratch", self.peer, r["owner"]["activation_id"])
        with self.assertRaises(Denied):
            self.update(old="b")
        self.a.register("blank", "a" * 64, "c" * 64, "kbs:///test/keys/blank")
        with self.assertRaises(Denied):
            self.update(["scratch", "blank"])
        self.assertEqual(self.a.inspect("scratch")["policy_sha256"], "a" * 64)
        self.a.revoke("scratch")
        with self.assertRaises(Denied):
            self.update()

    def test_update_racing_reopen_has_only_one_winner(self):
        def run(fn):
            try:
                fn()
                return True
            except Denied:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    run,
                    [
                        self.update,
                        lambda: self.a.begin(
                            "scratch", self.peer, "racing-open", "reopen", 1
                        ),
                    ],
                )
            )
        self.assertEqual(sum(outcomes), 1)
