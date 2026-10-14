#!/usr/bin/env python3
"""Prepare strict RO verification-workload InitData, with no storage key.

This is a file-hash/RO verification workload, not yet a model inference server.
Admission must be tested with the exact downstream Regorus and live agent.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import shlex

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
IMAGE = (
    "registry.access.redhat.com/ubi9/ubi-minimal@sha256:"
    "b966f21589d418d1e02159cb8e03e261fc8c4ee9061258893f5e4e33986aac4c"
)


def prepare(output):
    output.mkdir()
    descriptor = json.loads(
        (
            ROOT
            / "tests/confidential-storage/evidence/2026-10-13/model-populator"
            / "guest-manifest.unsigned.json"
        ).read_text()
    )
    commands = [
        "set -eu",
        "id",
        "grep -E '^(CapEff|NoNewPrivs):' /proc/self/status",
        "grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status",
        "grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status",
        "test ! -e /dev/encrypted-data",
        "test ! -e /dev/mapper/control",
        "test ! -e /run/confidential-containers/cdh.sock",
        "cd /models",
    ]
    for item in descriptor["files"]:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", item["path"]) or not re.fullmatch(
            r"[a-f0-9]{64}", item["sha256"]
        ):
            raise ValueError("unexpected public inventory")
        expected = shlex.quote(item["sha256"] + "  " + item["path"])
        commands.append(f"printf '%s\\n' {expected} | sha256sum -c -")
    commands.extend(
        [
            "if touch /models/should-not-write 2>/dev/null; then exit 1; fi",
            "echo STORAGE_MODEL_RO_HASHES_PASS",
            "sleep 120",
        ]
    )
    workload = {
        "image": IMAGE,
        "args": ["/bin/sh", "-c", "; ".join(commands)],
        "environment": [
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TERM=xterm",
            "HOSTNAME=storage-model-ro",
            "NSS_SDB_USE_CACHE=no",
            "container=oci",
            "KUBERNETES_PORT_443_TCP_ADDR=172.30.0.1",
            "KUBERNETES_SERVICE_HOST=172.30.0.1",
            "KUBERNETES_SERVICE_PORT=443",
            "KUBERNETES_SERVICE_PORT_HTTPS=443",
            "KUBERNETES_PORT=tcp://172.30.0.1:443",
            "KUBERNETES_PORT_443_TCP=tcp://172.30.0.1:443",
            "KUBERNETES_PORT_443_TCP_PROTO=tcp",
            "KUBERNETES_PORT_443_TCP_PORT=443",
        ],
    }
    policy = (
        (HERE / "policy.rego.in")
        .read_text()
        .replace("__WORKLOAD_JSON__", json.dumps(workload, sort_keys=True))
    )
    data = {
        "cdh.toml": (
            'socket = "unix:///run/confidential-containers/cdh.sock"\n'
            'credentials = []\n[kbc]\nname = "cc_kbc"\n'
            'url = "http://127.0.0.1:1"\n[image]\n'
        ),
        "aa.toml": "[token_configs]\n",
        "policy.rego": policy,
    }
    # JSON quoted strings are also valid TOML basic strings for this ASCII input.
    initdata = 'version = "0.1.0"\nalgorithm = "sha256"\n\n[data]\n'
    initdata += "".join(f"{json.dumps(k)} = {json.dumps(v)}\n" for k, v in data.items())
    raw = initdata.encode()
    (output / "initdata.toml").write_bytes(raw)
    (output / "initdata.value").write_text(
        base64.b64encode(gzip.compress(raw, mtime=0)).decode()
    )
    (output / "policy.rego").write_text(policy)
    (output / "workload.json").write_text(json.dumps(workload, indent=2) + "\n")
    print(
        json.dumps(
            {
                "initDataSha256": hashlib.sha256(raw).hexdigest(),
                "policySha256": hashlib.sha256(policy.encode()).hexdigest(),
                "keyApproved": False,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output)
