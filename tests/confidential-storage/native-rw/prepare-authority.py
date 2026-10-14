#!/usr/bin/env python3
"""Offline admin registration of one explicitly NEW disposable RW test PVC.

Stop the isolated authority and deny KBS release first. Preserves its database,
signer, previous records and model key. Never resets or reformats existing state.
Does not provision a key or modify any disk; the attested guest executes the plan.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

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
from initialization_api import PROFILE  # noqa: E402


def prepare(args):
    os.umask(0o077)
    if args.device_bytes != 1073741824:
        raise ValueError("this live example supports only a new 1 GiB PVC")
    if str(uuid.UUID(args.provisioning_id)) != args.provisioning_id:
        raise ValueError("the PVC provisioning identity must be a canonical UUID")
    server_path = args.state / "server.json"
    server = json.loads(server_path.read_text())
    guest = json.loads(args.previous_guest.read_text())
    with sqlite3.connect(args.state / "authority.db") as database:
        if any(
            json.loads(r[0])["owner"] is not None
            for r in database.execute("SELECT record FROM volumes")
        ):
            raise ValueError(
                "all isolated owners must be closed before verifier transition"
            )
    policy = hashlib.sha256((args.policy / "policy.rego").read_bytes()).hexdigest()
    initdata = hashlib.sha256((args.policy / "initdata.toml").read_bytes()).hexdigest()
    profile = hashlib.sha256(PROFILE.read_bytes()).hexdigest()
    binding = {
        "container_name": "app",
        "destination": "/data",
        "access": "rw",
        "manifest_uri": "kbs:///storage-test/volume-records/" + args.volume_id,
    }
    credential = "kbs:///storage-test/volume-keys/" + args.volume_id
    authority = Authority(args.state / "authority.db")
    authority.register(
        args.volume_id, policy, profile, credential, mount_binding=binding
    )
    approval = authority.approve_initialization(
        args.volume_id,
        {
            "schema_version": 1,
            "new_volume": True,
            "provisioning_id": args.provisioning_id,
            "device_bytes": args.device_bytes,
            "luks_uuid": str(uuid.uuid4()),
            "filesystem": "ext4",
            "header_bytes": 16777216,
            "data_offset_bytes": 16777216,
        },
    )
    server.update(
        policy_sha256=policy,
        init_data=initdata,
        appraisals=[
            {"submod": "cpu0", "tee": "snp", "policy_id": "storage-native-rw-v1"}
        ],
    )
    guest["authority"].update(policy_sha256=policy, init_data=initdata)
    guest["kbs"]["allowed_resources"] = [credential]
    guest["expected"].update(
        volume_id=args.volume_id,
        generation=1,
        policy_sha256=policy,
        profile_sha256=profile,
        **binding
    )
    guest["manifest"] = []
    guest["initialization_plan_sha256"] = approval["plan_sha256"]
    for path, data in (
        (server_path, server),
        (args.state / "guest-rw-public.json", guest),
    ):
        temporary = path.with_suffix(".rw.tmp")
        with temporary.open("x") as output:
            output.write(json.dumps(data, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    print(
        json.dumps(
            {
                "volumeId": args.volume_id,
                "state": "Uninitialized",
                "planSha256": guest["initialization_plan_sha256"],
                "noKeyProvisioned": True,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state", "previous-guest", "policy"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--volume-id", required=True)
    parser.add_argument("--provisioning-id", required=True)
    parser.add_argument("--device-bytes", type=int, required=True)
    prepare(parser.parse_args())
