"""RW registration preserves previous records and refuses duplicate registration."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import test_native_ro_upgrade as ro_fixture

Denied = ro_fixture.Denied


class RwAdminTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ro_fixture.UpgradeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        guest = json.loads(f.guest.read_text())
        guest["kbs"] = {"allowed_resources": ["previous-resource"]}
        f.guest.write_text(json.dumps(guest))
        spec = importlib.util.spec_from_file_location(
            "rw_admin", Path(__file__).parent / "native-rw/prepare-authority.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.args = SimpleNamespace(
            state=f.state,
            previous_guest=f.guest,
            policy=f.policy,
            volume_id="rw-after",
            provisioning_id="fb6965a0-31f6-4bb3-b330-b91505044220",
            device_bytes=1073741824,
        )

    def test_preserves_old_record_and_refuses_registration_replay(self):
        f = self.fixture
        before = f.authority.inspect("before")
        self.module.prepare(self.args)
        self.assertEqual(f.authority.inspect("before"), before)
        after = f.authority.inspect("rw-after")
        self.assertEqual(after["state"], "Uninitialized")
        self.assertIsNone(after["owner"])
        guest = json.loads((f.state / "guest-rw-public.json").read_text())
        self.assertEqual(guest["manifest"], [])
        self.assertEqual(
            guest["initialization_plan_sha256"],
            after["initialization_approval"]["plan_sha256"],
        )
        self.assertEqual(guest["authority"]["receipt_key_id"], "retained")
        self.assertEqual(
            guest["kbs"]["allowed_resources"],
            ["kbs:///storage-test/volume-keys/rw-after"],
        )
        with self.assertRaises(Denied):
            self.module.prepare(self.args)
        self.assertEqual(f.authority.inspect("rw-after"), after)

    def test_refuses_active_owner_before_registering(self):
        f = self.fixture
        f.authority.begin("before", f.peer, "active", "reopen", 1)
        before = f.authority.inspect("before")
        with self.assertRaises(ValueError):
            self.module.prepare(self.args)
        self.assertEqual(f.authority.inspect("before"), before)
        with self.assertRaises(Denied):
            f.authority.inspect("rw-after")

    def test_refuses_unsupported_geometry_before_registering(self):
        self.args.device_bytes = 2147483648
        with self.assertRaises(ValueError):
            self.module.prepare(self.args)
        with self.assertRaises(Denied):
            self.fixture.authority.inspect("rw-after")
