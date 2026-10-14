#!/usr/bin/env python3
"""Offline test-only RO policy transition, preserving authority history and keys.

Stop the isolated authority and deny KBS release before applying. This registers
one new logical RO access record for the same immutable ciphertext, revokes the
closed previous record, and updates public measured configuration. Never resets
an initialized record, changes the disk, generates a key or grants RW recovery.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = next(
    (
        p
        for p in Path(__file__).resolve().parents
        if (p / "components/confidential-storage-authority").is_dir()
    ),
    None,
)
if ROOT is not None:
    sys.path.insert(0, str(ROOT / "components/confidential-storage-authority"))
from authority import Authority  # noqa: E402
from publication import from_publisher  # noqa: E402


def upgrade(state, guest_file, policy, evidence, new_id):
    os.umask(0o077)
    guest = json.loads(guest_file.read_text())
    server_path = state / "server.json"
    server = json.loads(server_path.read_text())
    authority = Authority(state / "authority.db")
    previous = authority.inspect(guest["expected"]["volume_id"])
    if (
        previous["state"] != "Ready"
        or previous["owner"] is not None
        or previous["revoked"]
        or previous["mount_binding"]["access"] != "ro"
    ):
        raise ValueError("previous record must be closed, Ready and RO")
    digest = hashlib.sha256((policy / "policy.rego").read_bytes()).hexdigest()
    initdata = hashlib.sha256((policy / "initdata.toml").read_bytes()).hexdigest()
    if digest == previous["policy_sha256"]:
        raise ValueError("policy has not changed")
    if (
        server["policy_sha256"] != previous["policy_sha256"]
        or guest["authority"]["policy_sha256"] != previous["policy_sha256"]
    ):
        raise ValueError("old public configuration does not match authority")
    record = authority.register(
        new_id,
        digest,
        previous["profile_sha256"],
        previous["credential_uri"],
        mount_binding=previous["mount_binding"],
    )
    manifest = from_publisher(
        record,
        json.loads((evidence / "guest-manifest.unsigned.json").read_text()),
        json.loads((evidence / "source.json").read_text()),
    )
    authority.approve_published_model(new_id, manifest)
    authority.revoke(previous["volume_id"])
    server.update(policy_sha256=digest, init_data=initdata)
    guest["authority"].update(policy_sha256=digest, init_data=initdata)
    guest["expected"].update(volume_id=new_id, policy_sha256=digest)
    guest["manifest"] = list(manifest)
    for path, data in ((server_path, server), (state / "guest-public.json", guest)):
        temporary = path.with_suffix(".upgrade.tmp")
        with temporary.open("x") as out:
            out.write(json.dumps(data, indent=2) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    print(
        json.dumps(
            {
                "previousRecord": previous["volume_id"],
                "previousRevoked": True,
                "newRecord": new_id,
                "state": "Ready",
                "owner": None,
                "policySha256": digest,
                "initDataSha256": initdata,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state", "guest-file", "policy", "evidence"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--new-id", required=True)
    args = parser.parse_args()
    upgrade(args.state, args.guest_file, args.policy, args.evidence, args.new_id)
