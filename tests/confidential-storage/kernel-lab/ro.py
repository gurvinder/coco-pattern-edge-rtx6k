#!/usr/bin/env python3
"""Non-TEE kernel test of the actual Qwen disk with disposable QEMU snapshots.

The private initrd contains the explicit TEST model key. Never publish it. No
host device, mount, CAP_SYS_ADMIN, network or KVM is used. The original disk is
never modified; the corruption phase writes only QEMU's temporary overlay.
"""
import argparse
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "work", "opener", "disk", "key"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    tools = a.tools.resolve(strict=True)
    disk = a.disk.resolve(strict=True)
    if not disk.is_file() or disk.stat().st_size != 2147483648:
        p.error("requires the published 2 GiB test Qwen image")
    if (
        a.key.is_symlink()
        or not a.key.is_file()
        or a.key.stat().st_size != 32
        or a.key.stat().st_mode & 0o077
    ):
        p.error("requires the private 32-byte TEST key")
    here = Path(__file__).resolve().parent
    work = a.work.absolute()
    subprocess.run(
        [
            sys.executable,
            str(here / "build.py"),
            "--tools",
            str(tools),
            "--work",
            str(work),
            "--opener",
            str(a.opener.resolve(strict=True)),
        ],
        check=True,
    )
    root = work / "root"
    shutil.copy2(a.key, root / "test-key")
    (root / "test-key").chmod(0o600)
    for source, target, mode in (
        ("ro-init", "init", 0o700),
        ("ro-app-test", "ro-app-test", 0o755),
    ):
        shutil.copy2(here / source, root / target)
        (root / target).chmod(mode)
    paths = subprocess.check_output(["find", ".", "-print0"], cwd=root)
    packed = subprocess.run(
        [str(tools / "bin/cpio"), "--null", "-o", "-H", "newc", "--owner=0:0"],
        input=paths,
        cwd=root,
        capture_output=True,
        check=True,
    )
    with gzip.open(work / "initrd.gz", "wb", compresslevel=1) as stream:
        stream.write(packed.stdout)
    env = os.environ | {
        "LD_LIBRARY_PATH": f"{tools}/lib/x86_64-linux-gnu:{tools}/usr/lib/x86_64-linux-gnu",
        "TMPDIR": str(work),
    }
    for phase in ("normal", "corrupt"):
        command = [
            str(tools / "usr/bin/qemu-system-x86_64"),
            "-L",
            str(tools / "usr/share/qemu"),
            "-M",
            "microvm",
            "-accel",
            "tcg,thread=multi",
            "-cpu",
            "max",
            "-m",
            "1024",
            "-smp",
            "2",
            "-nodefaults",
            "-no-user-config",
            "-display",
            "none",
            "-serial",
            "stdio",
            "-monitor",
            "none",
            "-no-reboot",
            "-kernel",
            str(work / "vmlinuz"),
            "-initrd",
            str(work / "initrd.gz"),
            "-append",
            "console=ttyS0 reboot=t panic=-1 coco.ro.lab=1"
            + (" coco.corrupt=1" if phase == "corrupt" else ""),
            "-drive",
            f"id=model,file={disk},format=raw,if=none,snapshot=on",
            "-device",
            "virtio-blk-device,drive=model,serial=coco-ro-snapshot",
        ]
        with (work / f"{phase}.log").open("wb") as log:
            result = subprocess.run(
                command,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=600,
                check=False,
            )
        text = (work / f"{phase}.log").read_text(errors="replace")
        required = ["COCO_TEST_PASS:ro-kernel-lab"]
        if phase == "normal":
            required += [
                "COCO_TEST_PASS:ro-model-hashes-uid1000-no-capabilities",
                "COCO_TEST_PASS:ro-unprivileged-consumer-and-busy-teardown",
                "COCO_TEST_PASS:ro-wrong-key-cleanup",
            ]
        else:
            required += ["COCO_TEST_PASS:ro-ciphertext-corruption-eio"]
        if (
            result.returncode
            or "COCO_TEST_FAIL:" in text
            or any(x not in text for x in required)
        ):
            raise RuntimeError(f"{phase} failed; inspect {work / (phase + '.log')}")
        print(
            f"PASS {phase}: real RO kernel stack, synthetic authorization", flush=True
        )


if __name__ == "__main__":
    main()
