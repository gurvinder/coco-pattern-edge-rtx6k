#!/usr/bin/env python3
"""Prepare a fresh isolated RO owner record and public measured guest config.

Does not start a service, approve an SNP measurement or provision a storage key.
Shares the isolated KBS TLS certificate on a different pinned port. All private
output stays outside Git; guest-public.json contains no private key material.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "components/confidential-storage-authority"))
from authority import Authority  # noqa: E402
from publication import from_publisher, profile_sha256  # noqa: E402


def prepare(args):
    ipaddress.ip_address(args.address)
    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    state = args.output
    guest_path = args.guest_path
    policy_digest = hashlib.sha256(
        (args.policy / "policy.rego").read_bytes()
    ).hexdigest()
    init_digest = hashlib.sha256(
        (args.policy / "initdata.toml").read_bytes()
    ).hexdigest()
    receipt = ed25519.Ed25519PrivateKey.generate()
    (state / "receipt.key").write_bytes(
        receipt.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    for name in ("tls.key", "tls.pem"):
        shutil.copyfile(args.kbs_private / name, state / name)
    audience = "https://storage-kbs.invalid:8443/v1"
    binding = {
        "manifest_uri": "kbs:///storage-test/manifests/qwen-luks-v1",
        "container_name": "model",
        "destination": "/models",
        "access": "ro",
    }
    credential = "kbs:///storage-test/model-keys/qwen-luks-v1"
    authority = Authority(state / "authority.db")
    record = authority.register(
        "qwen-luks-model-v1",
        policy_digest,
        profile_sha256(),
        credential,
        mount_binding=binding,
    )
    evidence = ROOT / "tests/confidential-storage/evidence/2026-10-13/model-populator"
    manifest = from_publisher(
        record,
        json.loads((evidence / "guest-manifest.unsigned.json").read_text()),
        json.loads((evidence / "source.json").read_text()),
    )
    authority.approve_published_model("qwen-luks-model-v1", manifest)
    server = {
        "listen": {"host": "0.0.0.0", "port": 8443},
        "as_loopback_port": 8089,
        "database": f"{guest_path}/authority.db",
        "tls_certificate": f"{guest_path}/tls.pem",
        "tls_key": f"{guest_path}/tls.key",
        "as_signer": "/build/private-as-state/signer.pub.pem",
        "as_issuer": "storage-probe-as",
        "audience": audience,
        "init_data": init_digest,
        "policy_sha256": policy_digest,
        "appraisals": [
            {"submod": "cpu0", "tee": "snp", "policy_id": "storage-native-ro-v1"}
        ],
        "receipt_key": f"{guest_path}/receipt.key",
        "receipt_issuer": "storage-native-test",
        "receipt_key_id": "native-ro-v1",
    }
    ca = list((args.kbs_private / "ca.der").read_bytes())
    guest = {
        "schema_version": 1,
        "purpose": "isolated-confidential-storage/v1",
        "authority": {
            "audience": audience,
            "certificate_der": ca,
            "address": args.address + ":8443",
            "policy_sha256": policy_digest,
            "init_data": init_digest,
            "receipt_public_key": list(receipt.public_key().public_bytes_raw()),
            "receipt_issuer": server["receipt_issuer"],
            "receipt_key_id": server["receipt_key_id"],
        },
        "kbs": {
            "origin": "https://storage-kbs.invalid:8444",
            "certificate_der": ca,
            "address": args.address + ":8444",
            "allowed_resources": [credential],
            "require_gpu": False,
        },
        "expected": {
            "volume_id": "qwen-luks-model-v1",
            "generation": 1,
            "policy_sha256": policy_digest,
            "profile_sha256": profile_sha256(),
            "session_sha256": "0" * 64,
            "operation_id": "replaced-in-guest",
            "nonce": "0" * 64,
            **binding,
        },
        "manifest": list(manifest),
    }
    for name, value in (("server.json", server), ("guest-public.json", guest)):
        (state / name).write_text(json.dumps(value, indent=2) + "\n")
    print("Prepared RO authority record and public guest config; no key provisioned")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "kbs-private", "policy"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--guest-path", default="/build/storage-authority-private")
    prepare(parser.parse_args())
