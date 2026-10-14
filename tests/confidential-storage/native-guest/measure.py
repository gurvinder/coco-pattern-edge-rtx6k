#!/usr/bin/env python3
"""Independently measure the candidate image; no quote-derived reference values.

This calculates the intended isolated handler with warning-level agent logging.
It does not install a handler, validate live QEMU arguments or authorize keys.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "probe_measure", HERE.parent / "private-aa-probe/measure.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def calculate(args):
    build = json.loads(args.build.read_text())
    paths = {}
    digests = {}
    for key in ("kernel", "ovmf_file"):
        name, expected = probe.FILES[key]
        path = args.boot_files / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("unapproved kernel/firmware")
        paths[key] = str(path)
        digests[name] = expected
    digest = hashlib.sha256(args.initrd.read_bytes()).hexdigest()
    if digest != build["initrdSha256"]:
        raise ValueError("candidate image build digest mismatch")
    paths["initrd"] = str(args.initrd)
    digests[args.initrd.name] = digest
    # Runtime generates launch_process_timeout before user kernel_params. Disable
    # [agent.kata].enable_debug and prepend agent.log=warn to kernel_params.
    command = probe.CMDLINE.replace("agent.log=debug ", "").replace(
        "cgroup_no_v1=all", "agent.log=warn cgroup_no_v1=all"
    )
    measurement = probe.snp_calc_launch_digest(
        vcpus=1,
        vcpu_sig=probe.CPU_SIGS["EPYC-v4"],
        guest_features=0x1,
        append=command,
        ovmf_hash_str="",
        **paths
    ).hex()
    return {
        "purpose": "native-private-storage-candidate",
        "approvedForStorageKeys": False,
        "calculator": "sev-snp-measure==0.0.13",
        "files": digests,
        "vcpus": 1,
        "vcpuType": "EPYC-v4",
        "guestFeatures": "0x1",
        "kernelCommandLine": command,
        "launchMeasurement": measurement,
        "reportedTcb": {"bootloader": 3, "tee": 2, "snp": 6, "microcode": 117},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("boot-files", "build", "initrd", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x") as stream:
        stream.write(json.dumps(calculate(args), indent=2) + "\n")
