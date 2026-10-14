"""Offline candidate checks. No cluster, GPU, storage key or disk access."""

from argparse import Namespace
from contextlib import redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import io
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import prepare
from compiler import Invalid


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = datetime.now(timezone.utc)
        cert = (
            (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(1)
                .not_valid_before(now)
                .not_valid_after(now + timedelta(days=1))
                .sign(key, hashes.SHA256())
            )
            .public_bytes(serialization.Encoding.PEM)
            .decode()
        )
        (self.inputs / "trustee-ca.pem").write_text(cert)
        (self.inputs / "aa.toml").write_text(
            "[token_configs.kbs]\nurl = "
            + json.dumps(prepare.TRUSTEE)
            + "\ncert = "
            + json.dumps(cert)
            + "\n[token_configs.coco_as]\nurl = "
            + json.dumps(prepare.TRUSTEE)
            + "\n"
        )
        (self.inputs / "cdh.toml").write_text(
            'credentials = []\n[kbc]\nname = "cc_kbc"\nurl = '
            + json.dumps(prepare.TRUSTEE)
            + "\nkbs_cert = "
            + json.dumps(cert)
            + "\n[image]\n"
        )
        self.claims = []
        for index, name in enumerate(["qwen-luks-model-v1", "qwen-gpu-data-v1"]):
            claim = {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    "name": name,
                    "namespace": prepare.NAMESPACE,
                    "uid": f"00000000-0000-4000-8000-{index:012d}",
                },
                "spec": {
                    "storageClassName": "lvms-vg1",
                    "volumeMode": "Block",
                    "accessModes": ["ReadWriteOncePod"],
                    "resources": {
                        "requests": {
                            "storage": "2.147483648e+09" if index == 0 else "1Gi"
                        }
                    },
                },
                "status": {"phase": "Bound"},
            }
            self.claims.append(claim)
            prepare.write(self.inputs / (name + ".json"), claim)
        self.args = Namespace(
            inputs=self.inputs,
            output=self.root / "state",
            as_certificate=self.inputs / "trustee-ca.pem",
            new_data_pvc_uid=self.claims[1]["metadata"]["uid"],
            authority_address="172.30.1.2",
        )
        self.workload = {
            "image": "test.invalid/image@sha256:" + "a" * 64,
            "args": ["/usr/bin/python3"],
            "podEnvironment": [],
        }

    def test_real_profiles_and_fresh_intent_no_key_and_no_overwrite(self):
        with redirect_stdout(io.StringIO()):
            prepare.prepare(self.args)
        state = self.args.output
        guest = prepare.read(state / "guest-public.json")
        workload = prepare.read(state / "workload.json")
        environment = dict(item.split("=", 1) for item in workload["environment"])
        self.assertEqual(environment["USER"], "qwen")
        self.assertEqual(environment["LOGNAME"], "qwen")
        self.assertEqual(
            environment["TORCHINDUCTOR_CACHE_DIR"], "/data/cache/torchinductor"
        )
        self.assertTrue(guest["authority"]["trustee_rcar"])
        self.assertEqual(guest["kbs"]["provider"], "private-cdh")
        with sqlite3.connect(state / "authority.db") as db:
            records = [
                json.loads(row[0]) for row in db.execute("SELECT record FROM volumes")
            ]
        self.assertEqual(
            sorted(r["state"] for r in records), ["Ready", "Uninitialized"]
        )
        self.assertTrue(all(r["owner"] is None for r in records))
        rw = next(v for v in guest["volumes"] if v["binding_id"] == "data")
        ro = next(v for v in guest["volumes"] if v["binding_id"] == "model")
        self.assertEqual(
            rw["expected"]["profile_sha256"], prepare.profile_digest("rw-luks2-v2")
        )
        self.assertEqual(ro["expected"]["destination"], "/models/qwen")
        self.assertTrue(rw["initialization_plan_sha256"])
        self.assertTrue(ro["manifest"])
        for path in state.iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        before = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
        with self.assertRaises(FileExistsError):
            prepare.prepare(self.args)
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
        )

    def test_environment_revision_preserves_storage_and_creates_no_credentials(self):
        spec = importlib.util.spec_from_file_location(
            "revision", prepare.HERE / "update-environment.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with redirect_stdout(io.StringIO()):
            prepare.prepare(self.args)
            state = self.args.output
            module.revise(
                state,
                state / "guest-public.json",
                state / "server.json",
                self.root / "revision",
                {"USER": "revised-user"},
            )
        before = prepare.read(state / "guest-public.json")
        after = prepare.read(self.root / "revision/guest-public.json")
        self.assertNotEqual(
            before["authority"]["policy_sha256"], after["authority"]["policy_sha256"]
        )
        for key in ("certificate_der", "receipt_public_key", "address"):
            self.assertEqual(before["authority"][key], after["authority"][key])
        for old, new in zip(before["volumes"], after["volumes"]):
            new["expected"]["policy_sha256"] = old["expected"]["policy_sha256"]
            self.assertEqual(old, new)
        files = {p.name for p in (self.root / "revision").iterdir()}
        self.assertFalse(files.intersection({"tls.key", "receipt.key", "authority.db"}))

    def test_recreated_or_wrong_data_claim_denied(self):
        with self.assertRaises(Invalid):
            prepare.bundle_for(
                self.claims, self.workload, self.claims[0]["metadata"]["uid"]
            )

    def test_unsupported_capacity_namespace_or_sharing_denied(self):
        for change in ["size", "namespace", "access", "unbound", "missing"]:
            claims = copy.deepcopy(self.claims)
            if change == "size":
                claims[1]["spec"]["resources"]["requests"]["storage"] = "2Gi"
            elif change == "namespace":
                claims[1]["metadata"]["namespace"] = "gpu-workload"
            elif change == "access":
                claims[1]["spec"]["accessModes"] = ["ReadWriteMany"]
            elif change == "unbound":
                claims[1]["status"]["phase"] = "Pending"
            else:
                claims.pop()
            with self.subTest(change=change), self.assertRaises(Invalid):
                prepare.bundle_for(claims, self.workload, self.args.new_data_pvc_uid)

    def test_secret_or_endpoint_redirect_not_copied_to_initdata(self):
        aa = self.inputs / "aa.toml"
        aa.write_text(aa.read_text() + '\npassword = "test-value"\n')
        with self.assertRaises(Invalid):
            prepare.public_configs(self.inputs)

    def test_authority_certificate_is_a_server_leaf_not_a_ca(self):
        with redirect_stdout(io.StringIO()):
            prepare.prepare(self.args)
        cert = x509.load_pem_x509_certificate(
            (self.args.output / "tls.pem").read_bytes()
        )
        self.assertFalse(
            cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        )
        from cryptography.x509.oid import ExtendedKeyUsageOID

        self.assertIn(
            ExtendedKeyUsageOID.SERVER_AUTH,
            cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value,
        )

    def test_conflicting_ca_pin_denied(self):
        cdh = self.inputs / "cdh.toml"
        cdh.write_text(cdh.read_text().replace("BEGIN CERTIFICATE", "BEGIN OTHER"))
        with self.assertRaises(Invalid):
            prepare.public_configs(self.inputs)


if __name__ == "__main__":
    unittest.main()
