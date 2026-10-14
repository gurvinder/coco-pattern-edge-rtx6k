#!/usr/bin/env python3
"""Prepare an isolated, initially deny-all KBS. Does not provision any resource.

Private output must stay outside Git. Launch only the recorded SNP-only KBS
build. A later explicit policy installation must bind measured guest artifacts.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def prepare(output, guest_path):
    os.umask(0o077)
    output.mkdir(mode=0o700)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Storage test KBS CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=8))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, None, None),
            True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (output / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (output / "ca.der").write_bytes(ca.public_bytes(serialization.Encoding.DER))
    for label, common_name, server in (
        ("tls", "storage-kbs.invalid", True),
        ("signer", "storage-kbs-as", False),
    ):
        key = ec.generate_private_key(ec.SECP256R1())
        builder = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
            )
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=8))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.KeyUsage(
                    True, False, False, False, False, False, False, None, None
                ),
                True,
            )
        )
        if server:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(common_name)]), False
            ).add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False
            )
        cert = builder.sign(ca_key, hashes.SHA256())
        (output / f"{label}.pem").write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        (output / f"{label}.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    # No CA private key is retained: the exact short-lived chain is pinned.
    config = {
        "http_server": {
            "sockets": ["0.0.0.0:8444"],
            "insecure_http": False,
            "worker_count": 2,
            "payload_request_size": 1,
            "private_key": f"{guest_path}/tls.key",
            "certificate": f"{guest_path}/tls.pem",
            "tls_profile": "modern",
        },
        "admin": {"authorization_mode": "DenyAll"},
        "attestation_token": {
            "insecure_header_jwk": False,
            "trusted_certs_paths": [f"{guest_path}/ca.pem"],
        },
        "attestation_service": {
            "type": "coco_as_builtin",
            "timeout": 60,
            "policy_id_map": {},
            "rvps_config": {"type": "BuiltIn"},
            "attestation_token_broker": {
                "duration_min": 1,
                "issuer_name": "storage-kbs-as",
                "signer": {
                    "key_path": f"{guest_path}/signer.key",
                    "cert_path": f"{guest_path}/signer.pem",
                },
            },
            "verifier_config": {
                "snp_verifier": {
                    "vcek_sources": [
                        {
                            "type": "OfflineStore",
                            "path": "/build/private-as-state/kds-store",
                        }
                    ]
                }
            },
        },
        "storage_backend": {
            "storage_type": "LocalFs",
            "backends": {"local_fs": {"dir_path": f"{guest_path}/storage"}},
        },
        "plugins": [{"name": "resource", "storage_backend_type": "kvstorage"}],
    }
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (output / "storage/kbs").mkdir(parents=True)
    (output / "storage/kbs/resource-policy.rego").write_text(
        "package policy\nimport rego.v1\ndefault allow := false\n"
    )
    print(
        "Prepared TLS 1.3 KBS with closed admin API, "
        "offline VCEK and deny-all resources"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guest-path", default="/build/storage-kbs-private")
    args = parser.parse_args()
    prepare(args.output, args.guest_path)
