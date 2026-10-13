#!/usr/bin/env python3
"""Prepare a disposable no-key authority next to the isolated loopback AS.

Only a diagnostic RO record is registered. Its KBS credential does not exist.
The generated private state must stay outside Git. This script does not start
the server or register an AS policy; build/measure the guest artifact first.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "components/confidential-storage-authority"))
from authority import Authority  # noqa: E402
from publication import from_publisher, profile_sha256  # noqa: E402


def prepare(state, as_state, address):
    ipaddress.ip_address(address)
    os.umask(0o077)
    state.mkdir(mode=0o700)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    tls_key = ec.generate_private_key(ec.SECP256R1())
    receipt_key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Storage probe CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=8))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    tls = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "storage-authority.invalid")]
            )
        )
        .issuer_name(name)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=8))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("storage-authority.invalid")]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    for label, key in (("ca", ca_key), ("tls", tls_key), ("receipt", receipt_key)):
        (state / (label + ".key")).write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    (state / "tls.pem").write_bytes(tls.public_bytes(serialization.Encoding.PEM))
    policy_digest = hashlib.sha256(b"no-key-live-authority-probe-v1").hexdigest()
    init_data = "e2a4bfe74cf95d85efe1d72e63dec8aec51a0d3c7e8f322070a123d0b95d6cf0"
    audience = "https://storage-authority.invalid:8443/v1"
    binding = {
        "manifest_uri": "kbs:///diagnostic-only/records/qwen",
        "container_name": "model",
        "destination": "/models",
        "access": "ro",
    }
    database = state / "authority.db"
    authority = Authority(database)
    record = authority.register(
        "diagnostic-qwen",
        policy_digest,
        profile_sha256(),
        "kbs:///diagnostic-only/nonexistent-key/qwen",
        mount_binding=binding,
    )
    evidence = ROOT / "tests/confidential-storage/evidence/2026-10-13/model-populator"
    manifest = from_publisher(
        record,
        json.loads((evidence / "guest-manifest.unsigned.json").read_text()),
        json.loads((evidence / "source.json").read_text()),
    )
    authority.approve_published_model("diagnostic-qwen", manifest)
    server = {
        "listen": {"host": "0.0.0.0", "port": 8443},
        "as_loopback_port": 8089,
        "database": str(database),
        "tls_certificate": str(state / "tls.pem"),
        "tls_key": str(state / "tls.key"),
        "as_signer": str(as_state / "signer.pub.pem"),
        "as_issuer": "storage-probe-as",
        "audience": audience,
        "init_data": init_data,
        "policy_sha256": policy_digest,
        "appraisals": [
            {"submod": "cpu0", "tee": "snp", "policy_id": "storage-authority-probe-v1"}
        ],
        "receipt_key": str(state / "receipt.key"),
        "receipt_issuer": "diagnostic-authority",
        "receipt_key_id": "diagnostic-v1",
    }
    guest = {
        "purpose": "no-key-live-authority-probe",
        "audience": audience,
        "certificate_der": list(ca.public_bytes(serialization.Encoding.DER)),
        "address": address + ":8443",
        "policy_sha256": policy_digest,
        "init_data": init_data,
        "receipt_public_key": list(receipt_key.public_key().public_bytes_raw()),
        "receipt_issuer": server["receipt_issuer"],
        "receipt_key_id": server["receipt_key_id"],
        "expected": {
            "volume_id": "diagnostic-qwen",
            "generation": 1,
            "policy_sha256": policy_digest,
            "profile_sha256": profile_sha256(),
            "session_sha256": "0" * 64,
            "operation_id": "diagnostic-live-v1",
            "nonce": secrets.token_hex(32),
            **binding,
        },
    }
    (state / "server.json").write_text(json.dumps(server, indent=2) + "\n")
    (state / "guest-public.json").write_text(json.dumps(guest, indent=2) + "\n")
    print(
        "Prepared isolated authority and public guest configuration; no KBS key exists"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--as-state", required=True, type=Path)
    parser.add_argument("--address", required=True)
    args = parser.parse_args()
    prepare(args.state, args.as_state, args.address)
