#!/usr/bin/env python3
"""Prepare, never install, an isolated GPU runtime and planned SNP reference.

Reuse the already registered storage-test CRI handler only after verifying it has
no active guests. Keep a byte-for-byte backup of its old configuration. The new
RuntimeClass is an alias of that test handler; no production runtime edit or
CRI-O reload/restart is needed. Do not apply this to the production GPU handler.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tomllib

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "cpu_measure", HERE.parent / "native-guest/measure.py"
)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


def prepare(args):
    raw = args.base_config.read_text()
    base = tomllib.loads(raw)
    hv = base["hypervisor"]["qemu"]
    if not (
        hv["confidential_guest"]
        and hv["sev_snp_guest"]
        and hv["cold_plug_vfio"] == "root-port"
        and base["runtime"]["vfio_mode"] == "guest-kernel"
    ):
        raise ValueError("expected installed SNP NVIDIA guest-kernel configuration")
    if not re.fullmatch(
        r"/opt/kata-storage-test/gpu-candidate-[a-f0-9]{12}/initrd", args.image_path
    ):
        raise ValueError("use a new dedicated hash-named candidate directory")
    command_tail = "agent.log=warn " + hv["kernel_params"]
    replacements = {
        "initrd": json.dumps(args.image_path),
        "default_vcpus": "8",
        "default_maxvcpus": "8",
        "default_memory": "65536",
        "kernel_params": json.dumps(command_tail),
        "enable_annotations": '["cc_init_data"]',
    }
    seen = set()
    lines = []
    section = ""
    for line in raw.splitlines():
        if line.startswith("["):
            section = line
        matched = re.match(r"^([a-z_]+)\s*=", line)
        key = matched[1] if matched else None
        if section == "[hypervisor.qemu]" and key in replacements:
            if key in seen:
                raise ValueError("duplicate runtime setting")
            seen.add(key)
            line = key + " = " + replacements[key]
        if key == "enable_debug":
            line = "enable_debug = false"
        lines.append(line)
    if seen != replacements.keys():
        raise ValueError("missing expected runtime field")
    config = "\n".join(lines) + "\n"
    parsed = tomllib.loads(config)
    if parsed["agent"]["kata"]["debug_console_enabled"]:
        raise ValueError("debug console must be disabled")
    args.output.mkdir()
    (args.output / "configuration.toml").write_text(config)
    paths, hashes = {}, {}
    for key, (name, expected) in measure.probe.FILES.items():
        if key == "initrd":
            continue
        path = args.boot_files / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("unapproved kernel or firmware")
        paths[key], hashes[name] = str(path), actual
    build = json.loads(args.build.read_text())
    digest = hashlib.sha256(args.initrd.read_bytes()).hexdigest()
    if digest != build["initrdSha256"] or digest[:12] not in args.image_path:
        raise ValueError("candidate image/hash directory mismatch")
    paths["initrd"] = str(args.initrd)
    hashes["candidate.initrd"] = digest
    # Derive the fixed runtime-generated prefix from the already tested source.
    # Validate the real QEMU arguments before approving/releasing any key.
    prefix = measure.probe.CMDLINE.split("agent.log=debug")[0].replace(
        "nr_cpus=1 ", "nr_cpus=8 "
    )
    timeout = parsed["agent"]["kata"]["launch_process_timeout"]
    command = prefix + f"agent.launch_process_timeout={timeout} " + command_tail
    digest = measure.probe.snp_calc_launch_digest(
        vcpus=8,
        vcpu_sig=measure.probe.CPU_SIGS["EPYC-v4"],
        guest_features=0x1,
        append=command,
        ovmf_hash_str="",
        **paths,
    ).hex()
    result = {
        "scope": "independent planned GPU reference; live QEMU comparison pending",
        "approvedForStorageKeys": False,
        "calculator": "sev-snp-measure==0.0.13",
        "launchMeasurement": digest,
        "files": hashes,
        "vcpus": 8,
        "vcpuType": "EPYC-v4",
        "guestFeatures": "0x1",
        "kernelCommandLine": command,
        "baseRuntimeSha256": hashlib.sha256(raw.encode()).hexdigest(),
        "candidateRuntimeSha256": hashlib.sha256(config.encode()).hexdigest(),
    }
    (args.output / "measurement-planned.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    runtime = {
        "apiVersion": "node.k8s.io/v1",
        "kind": "RuntimeClass",
        "metadata": {"name": "kata-snp-nvidia-gpu-storage-candidate"},
        "handler": "kata-snp-storage-probe",
        "overhead": {
            "podFixed": {"cpu": "250m", "memory": "350Mi", "sev-snp.amd.com/esids": "1"}
        },
        "scheduling": {"nodeSelector": {"kubernetes.io/hostname": "9c-63-c0-dd-06-52"}},
    }
    (args.output / "runtimeclass.json").write_text(json.dumps(runtime, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["base-config", "boot-files", "build", "initrd", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--image-path", required=True)
    prepare(parser.parse_args())
