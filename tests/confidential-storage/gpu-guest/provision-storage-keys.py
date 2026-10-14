#!/usr/bin/env python3
"""CAS-merge two scoped credentials after checking the loaded KBS release policy.

Retain the existing RO LUKS key. Create/preserve a private RW key file before the
Vault write, so uncertain outcomes never cause rotation. Never print credentials.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess

MODEL = "qwen-gpu-model-v1"
DATA = "qwen-gpu-data-v1"


def read_key(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size != 32:
            raise ValueError("Key must be an existing private 32-byte regular file")
        return stream.read(33).hex()


def merge(existing, model, data):
    if not isinstance(existing, dict):
        raise ValueError("Existing model-key map required")
    added = {MODEL: model, DATA: data}
    for tag, value in added.items():
        if tag in existing and existing[tag] != value:
            raise ValueError("Existing scoped credential differs; refusing rotation")
    return {**existing, **added}


def provision(args):
    oc = ["oc", "--kubeconfig", str(args.kubeconfig), "--request-timeout=30s"]
    expected = args.approved_policy.read_bytes().rstrip(b"\n")
    if not all(tag.encode() in expected for tag in (MODEL, DATA)):
        raise ValueError("Reviewed scoped resource policy required")
    actual = subprocess.check_output(
        oc
        + [
            "-n",
            "trustee-operator-system",
            "exec",
            "deployment/trustee-deployment",
            "-c",
            "kbs",
            "--",
            "cat",
            "/opt/confidential-containers/storage/kbs/resource-policy.rego",
        ]
    ).rstrip(b"\n")
    if actual != expected:
        raise ValueError("Loaded Trustee release policy differs; no credential write")
    model = read_key(args.model_key)
    parent = args.data_key.parent.stat()
    if parent.st_mode & 0o077:
        raise ValueError("RW key parent must be private")
    if not args.data_key.exists():
        fd = os.open(args.data_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())
    data = read_key(args.data_key)
    secret = json.loads(
        subprocess.check_output(
            oc
            + [
                "-n",
                "imperative",
                "get",
                "secret",
                "vaultkeys",
                "-o",
                "json",
            ]
        )
    )
    token = json.loads(base64.b64decode(secret["data"]["vault_data_json"]))[
        "root_token"
    ]
    command = oc + ["-n", "vault", "exec", "-i", "vault-0", "--", "sh", "-ec"]
    read = subprocess.run(
        command
        + [
            "read -r VAULT_TOKEN; export VAULT_TOKEN; "
            "vault kv get -format=json secret/hub/modelKeys"
        ],
        input=(token + "\n").encode(),
        capture_output=True,
    )
    if read.returncode:
        raise RuntimeError("Could not read existing model-key map; no write attempted")
    retained = json.loads(read.stdout)["data"]
    existing = retained["data"]
    updated = merge(existing, model, data)
    if updated == existing:
        print("Matching scoped storage credentials already retained; no write")
        return
    version = retained["metadata"]["version"]
    if type(version) is not int or version < 1:
        raise ValueError("Invalid Vault CAS version")
    write = subprocess.run(
        command
        + [
            "read -r VAULT_TOKEN; export VAULT_TOKEN; "
            f"vault kv put -cas={version} -format=json secret/hub/modelKeys -"
        ],
        input=(token + "\n" + json.dumps(updated)).encode(),
        capture_output=True,
    )
    if write.returncode:
        raise RuntimeError(
            "CAS write failed; retain key file and retry after inspection"
        )
    print("Scoped storage credentials persisted; existing credentials preserved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("kubeconfig", "approved-policy", "model-key", "data-key"):
        parser.add_argument("--" + name, type=Path, required=True)
    provision(parser.parse_args())
