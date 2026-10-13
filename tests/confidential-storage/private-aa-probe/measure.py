#!/usr/bin/env python3
"""Calculate the diagnostic launch reference from pinned files, never a quote.

Requires sev-snp-measure==0.0.13. This no-secret image is not approved for keys.
The command line is fixed from the separately configured test handler. The
firmware/kernel/TCB baseline is docs/edge-rtx6k/cpu-baseline, not TOFU evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path

from sevsnpmeasure.guest import snp_calc_launch_digest
from sevsnpmeasure.vcpu_types import CPU_SIGS

FILES = {
    "ovmf_file": (
        "OVMF.amdsev.fd",
        "1aa196fe94e56809aa668fc4eabfc50923131288e018bcf0c6ab4dd3581bd850",
    ),
    "kernel": (
        "vmlinuz",
        "0317da8a23853124ed4e4da6c56254f40a741ff4507e1afbbcb7f32e5c097206",
    ),
    "initrd": (
        "private-aa-probe-v4.initrd",
        "d949fefa0716061ea88e1afc7ee66cca1b92302022af60a2e2f5faea6287290e",
    ),
}
CMDLINE = (
    "tsc=reliable no_timer_check rcupdate.rcu_expedited=1 "
    "i8042.direct=1 i8042.dumbkbd=1 i8042.nopnp=1 i8042.noaux=1 "
    "noreplace-smp reboot=k cryptomgr.notests net.ifnames=0 "
    "pci=lastbus=0 console=hvc0 console=hvc1 quiet panic=1 "
    "nr_cpus=1 selinux=0 scsi_mod.scan=none agent.log=debug "
    "agent.launch_process_timeout=6 cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1"
)


def calculate(root):
    paths = {}
    for key, (name, expected) in FILES.items():
        path = root / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"unapproved {key} input")
        paths[key] = str(path)
    measurement = snp_calc_launch_digest(
        vcpus=1,
        vcpu_sig=CPU_SIGS["EPYC-v4"],
        guest_features=0x1,
        append=CMDLINE,
        ovmf_hash_str="",
        **paths,
    ).hex()
    return {
        "schemaVersion": 1,
        "scope": "no-secret diagnostic; never approve for storage keys",
        "calculator": "sev-snp-measure==0.0.13",
        "files": {name: digest for name, digest in FILES.values()},
        "vcpus": 1,
        "vcpuType": "EPYC-v4",
        "guestFeatures": "0x1",
        "kernelCommandLine": CMDLINE,
        "launchMeasurement": measurement,
        "reportedTcb": {"bootloader": 3, "tee": 2, "snp": 6, "microcode": 117},
        "approvedForStorageKeys": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-files", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(calculate(args.boot_files), indent=2) + "\n")
