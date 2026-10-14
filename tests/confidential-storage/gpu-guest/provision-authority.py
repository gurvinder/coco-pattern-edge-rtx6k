#!/usr/bin/env python3
"""Persist this prepared authority's credentials in the existing pattern Vault.

No model keys, policy edits or storage operations. Existing different credentials
are an error, never an implicit rotation. Credentials travel through stdin only.
The retained authority database is transferred separately and never regenerated.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess


def provision(args):
    oc = ["oc", "--kubeconfig", str(args.kubeconfig), "--request-timeout=30s"]
    fields = (
        "tls.pem",
        "tls.key",
        "receipt.key",
        "as-signer.pem",
        "trustee-ca.pem",
        "initdata.toml",
    )
    payload = {name: (args.prepared / name).read_text() for name in fields}
    secret = json.loads(
        subprocess.check_output(
            oc + ["-n", "imperative", "get", "secret", "vaultkeys", "-o", "json"]
        )
    )
    token = json.loads(base64.b64decode(secret["data"]["vault_data_json"]))[
        "root_token"
    ]
    command = oc + ["-n", "vault", "exec", "-i", "vault-0", "--", "sh", "-ec"]
    # First authenticate and read exactly this new path. Neither response nor
    # CLI errors containing credentials are forwarded to user-visible output.
    read = subprocess.run(
        command
        + [
            "read -r VAULT_TOKEN; export VAULT_TOKEN; vault kv get -format=json secret/hub/storageGpuAuthority"
        ],
        input=(token + "\n").encode(),
        capture_output=True,
    )
    if read.returncode == 0:
        existing = json.loads(read.stdout)["data"]["data"]
        if existing != payload:
            raise ValueError("Existing authority credentials differ; refusing rotation")
        print("Existing matching authority credentials retained")
        return
    if b"No value found" not in read.stderr:
        raise RuntimeError("Unable to verify Vault path absence; no write attempted")
    write = subprocess.run(
        command
        + [
            "read -r VAULT_TOKEN; export VAULT_TOKEN; vault kv put -cas=0 -format=json secret/hub/storageGpuAuthority -"
        ],
        input=(token + "\n" + json.dumps(payload)).encode(),
        capture_output=True,
    )
    if write.returncode:
        raise RuntimeError("Vault create failed; credentials not printed")
    print("Authority credentials persisted with Vault CAS=0; no model keys changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    provision(parser.parse_args())
