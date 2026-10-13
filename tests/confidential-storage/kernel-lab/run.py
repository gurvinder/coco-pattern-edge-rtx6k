#!/usr/bin/env python3
"""Run real storage operations only in a fresh, disposable non-TEE QEMU guest."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--opener", type=Path)
    parser.add_argument("--runtime-probe", type=Path)
    parser.add_argument("--callback-probe", type=Path)
    args = parser.parse_args()
    tools = args.tools.resolve(strict=True)
    work = args.work.absolute()
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("build.py")),
            "--tools",
            str(tools),
            "--work",
            str(work),
        ]
        + (["--opener", str(args.opener.resolve(strict=True))] if args.opener else [])
        + (
            ["--runtime-probe", str(args.runtime_probe.resolve(strict=True))]
            if args.runtime_probe else []
        )
        + (["--callback-probe", str(args.callback_probe.resolve(strict=True))] if args.callback_probe else []),
        check=True,
    )
    env = os.environ | {
        "LD_LIBRARY_PATH": (
            f"{tools}/lib/x86_64-linux-gnu:" f"{tools}/usr/lib/x86_64-linux-gnu"
        )
    }
    for phase in ["initialize", "reopen", "wrong-key", "corruption"]:
        disk = work / "scratch.raw"
        if phase == "corruption":
            # Preserve the good disk. Mutate one byte well beyond header/journal.
            damaged = work / "corrupt.raw"
            shutil.copyfile(disk, damaged)
            disk = damaged
            with disk.open("r+b") as target:
                target.seek(64 * 1024 * 1024)
                original = target.read(1)
                target.seek(-1, os.SEEK_CUR)
                target.write(bytes([original[0] ^ 1]))
                target.flush()
                os.fsync(target.fileno())
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
            "2048" if args.callback_probe else "1024",
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
            f"console=ttyS0 reboot=t panic=-1 coco.storage.lab=1 coco.phase={phase}"
            + (" coco.opener=1" if args.opener and phase == "initialize" else ""),
            "-drive",
            f"id=scratch,file={disk},format=raw,if=none,cache=none",
            "-device",
            "virtio-blk-device,drive=scratch,serial=coco-scratch-only",
        ]
        log = work / f"{phase}.log"
        with log.open("wb") as output:
            result = subprocess.run(
                command,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
                check=False,
            )
        text = log.read_text(errors="replace")
        if (
            result.returncode
            or "COCO_TEST_FAIL:" in text
            or f"COCO_TEST_PASS:{phase}" not in text
            or (args.callback_probe and phase == "initialize"
                and "COCO_TEST_PASS:rustjail-callback-lifecycle" not in text)
            or (
                args.runtime_probe and phase == "initialize"
                and "COCO_TEST_PASS:pinned-process-cgroup-subtree-quiescence" not in text
            )
        ):
            raise RuntimeError(f"{phase} failed; inspect {log}")
        print(
            f"PASS {phase}; log SHA256 {hashlib.sha256(log.read_bytes()).hexdigest()}",
            flush=True,
        )
    print(
        "PASS non-TEE kernel lab; no attestation, Trustee, "
        "Kata integration or crash recovery tested"
    )


if __name__ == "__main__":
    main()
