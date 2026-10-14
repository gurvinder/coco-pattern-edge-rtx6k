#!/usr/bin/env python3
"""Generate exact test-only KBS policy; never install it or provision a key."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def prepare(
    measurement, initdata, output, resource="storage-test/model-keys/qwen-luks-v1"
):
    m = json.loads(measurement.read_text())
    digest = m["launchMeasurement"]
    if not re.fullmatch(r"[a-f0-9]{96}", digest):
        raise ValueError("invalid independently calculated SNP measurement")
    if m["vcpus"] != 1 or m["vcpuType"] != "EPYC-v4" or m["guestFeatures"] != "0x1":
        raise ValueError("unexpected isolated hardware profile")
    expected = {
        "measurement": digest,
        "init_data": hashlib.sha256(initdata.read_bytes()).hexdigest(),
        "tcb": m["reportedTcb"],
    }
    template = Path(__file__).with_name("kbs-resource-policy.rego.in").read_text()
    if resource != "storage-test/model-keys/qwen-luks-v1":
        if not re.fullmatch(
            r"storage-test/volume-keys/[a-z0-9][a-z0-9-]{0,62}", resource
        ):
            raise ValueError("unsupported isolated storage key resource")
        template = template.replace(
            '["storage-test", "model-keys", "qwen-luks-v1"]',
            json.dumps(resource.split("/")),
        )
    with output.open("x") as stream:
        stream.write(
            template.replace("__EXPECTED_JSON__", json.dumps(expected, sort_keys=True))
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("measurement", "initdata", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--resource", default="storage-test/model-keys/qwen-luks-v1")
    args = parser.parse_args()
    prepare(args.measurement, args.initdata, args.output, args.resource)
