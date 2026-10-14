#!/usr/bin/env python3
"""Generate a strict unprivileged RW canary command; no format tools or keys."""
import argparse
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent


def prepare(output):
    spec = importlib.util.spec_from_file_location(
        "ro_policy", HERE.parent / "native-guest/prepare-policy.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        module.prepare(base)
        workload = json.loads((base / "workload.json").read_text())
    sentinel = hashlib.sha256(
        b"confidential-storage-public-rw-persistence-canary-v1\n"
    ).hexdigest()
    script = (HERE / "workload.sh").read_text().replace("__SENTINEL_SHA256__", sentinel)
    workload.update(container="app", args=["/bin/sh", "-c", script])
    workload["environment"] = [
        v.replace("HOSTNAME=storage-model-ro", "HOSTNAME=storage-rw")
        for v in workload["environment"]
    ]
    template = (
        (HERE.parent / "native-guest/policy.rego.in")
        .read_text()
        .replace(
            'input.OCI.Annotations["io.kubernetes.container.name"] == "model"',
            'input.OCI.Annotations["io.kubernetes.container.name"] == "app"',
        )
    )
    policy = template.replace("__WORKLOAD_JSON__", json.dumps(workload, sort_keys=True))
    data = {
        "cdh.toml": (
            'socket = "unix:///run/confidential-containers/cdh.sock"\n'
            'credentials = []\n[kbc]\nname = "cc_kbc"\n'
            'url = "http://127.0.0.1:1"\n[image]\n'
        ),
        "aa.toml": "[token_configs]\n",
        "policy.rego": policy,
    }
    raw = (
        'version = "0.1.0"\nalgorithm = "sha256"\n\n[data]\n'
        + "".join(f"{json.dumps(k)} = {json.dumps(v)}\n" for k, v in data.items())
    ).encode()
    assert len(raw) <= 65536
    output.mkdir()
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
                "sentinelSha256": sentinel,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output)
