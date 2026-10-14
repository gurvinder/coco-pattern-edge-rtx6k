"""Run the isolated authority using explicit administrator-owned configuration."""

import argparse
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)

from attestation import Appraisal, EarVerifier, read_object
from as_client import AttestationServiceClient
from authority import Authority
from gateway import Gateway
from receipts import ReceiptIssuer
from transport import Server, tls_context
from publication import PROFILE_PATH as RO_PROFILE
from initialization_api import PROFILE as RW_PROFILE


def private_pem(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("private key must be a regular private file")
        data = stream.read(16385)
        if not 0 < len(data) <= 16384:
            raise ValueError("invalid private key size")
        return data


def require_storage_profiles():
    # Package the public profile files beside the authority source. Missing or
    # invalid profiles must prevent startup, not fail after attestation succeeds.
    for profile in (RO_PROFILE, RW_PROFILE):
        read_object(profile.read_bytes(), 64 * 1024)


def configured_server(path):
    require_storage_profiles()
    # Paths and pins here are operator configuration, never request parameters.
    with Path(path).open("rb") as stream:
        config = read_object(stream.read(32769), 32768)
    if set(config) != {
        "listen",
        "as_loopback_port",
        "database",
        "tls_certificate",
        "tls_key",
        "as_signer",
        "as_issuer",
        "audience",
        "init_data",
        "policy_sha256",
        "appraisals",
        "receipt_key",
        "receipt_issuer",
        "receipt_key_id",
    }:
        raise ValueError("explicit authority configuration required")
    listen = config["listen"]
    if (
        not isinstance(listen, dict)
        or set(listen) != {"host", "port"}
        or not isinstance(listen["host"], str)
        or type(listen["port"]) is not int
        or not 1 <= listen["port"] <= 65535
    ):
        raise ValueError("invalid listen address")
    audience = urlsplit(config["audience"])
    if (
        audience.scheme != "https"
        or not audience.hostname
        or audience.username is not None
        or audience.password is not None
        or audience.path != "/v1"
        or audience.query
        or audience.fragment
    ):
        raise ValueError("pin the HTTPS authority audience ending in /v1")
    with Path(config["as_signer"]).open("rb") as stream:
        signer = load_pem_public_key(stream.read(16384))
    verifier = EarVerifier(
        signer,
        config["as_issuer"],
        config["audience"],
        config["init_data"],
        config["policy_sha256"],
        [Appraisal(**a) for a in config["appraisals"]],
    )
    receipt_key = load_pem_private_key(private_pem(config["receipt_key"]), None)
    private_pem(config["tls_key"])
    context = tls_context(config["tls_certificate"], config["tls_key"])
    authority = Authority(config["database"])
    issuer = ReceiptIssuer(
        authority, receipt_key, config["receipt_issuer"], config["receipt_key_id"]
    )
    attester = AttestationServiceClient(verifier, config["as_loopback_port"])
    return Server(
        (listen["host"], listen["port"]),
        Gateway(verifier, issuer, attester=attester),
        context,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    with configured_server(args.config) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
