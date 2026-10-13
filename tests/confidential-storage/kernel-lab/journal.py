#!/usr/bin/env python3
"""NON-TEE lab bridge: trusted local SQLite authority to disposable QEMU console.

The console protocol has NO authentication and must never be a production API.
No arbitrary disk, key, shell command or storage path is accepted from a guest.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
from threading import Thread
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "components/confidential-storage-authority"))
from authority import AttestedPeer, Authority  # noqa: E402
from executor import Completion, DeviceBinding, Executor, RecoveryRequired  # noqa: E402
from initialization import STEPS  # noqa: E402

PROFILE = hashlib.sha256(
    (
        REPO / "components/confidential-storage-guest/profiles/rw-luks2-v1.json"
    ).read_bytes()
).hexdigest()
PEER = AttestedPeer("a" * 64, "b" * 64)  # Synthetic identity, NOT verified evidence.
PLAN = {
    "schema_version": 1,
    "new_volume": True,
    "provisioning_id": "abcdef01-2345-4678-8123-456789abcdef",
    "device_bytes": 134217728,
    "luks_uuid": "01234567-89ab-4cde-8123-456789abcdef",
    "filesystem": "ext4",
    "header_bytes": 16777216,
    "data_offset_bytes": 16777216,
}
BINDING = DeviceBinding(
    "scratch",
    PLAN["provisioning_id"],
    PLAN["device_bytes"],
    PROFILE,
    "kbs:///storage-test/keys/scratch-v1",
)


def command(tools, work, disk, phase="initialize", journal=True):
    return [
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
        f"console=ttyS0 reboot=t panic=-1 coco.storage.lab=1 coco.phase={phase}"
        + (" coco.journal=1" if journal else ""),
        "-drive",
        f"id=scratch,file={disk},format=raw,if=none,cache=none",
        "-device",
        "virtio-blk-device,drive=scratch,serial=coco-scratch-only",
    ]


def environment(tools):
    return os.environ | {
        "LD_LIBRARY_PATH": (
            f"{tools}/lib/x86_64-linux-gnu:" f"{tools}/usr/lib/x86_64-linux-gnu"
        )
    }


def manifest(header):
    # Same insertion order/encoding as the fixed disposable guest fixture.
    return json.dumps(
        {
            "schemaVersion": 1,
            "volumeId": "scratch",
            "generation": 1,
            "profileSha256": PROFILE,
            "credentialUri": BINDING.credential_uri,
            "luksUuid": PLAN["luks_uuid"],
            "deviceBytes": PLAN["device_bytes"],
            "headerSha256": header,
            "headerBytes": PLAN["header_bytes"],
            "dataOffsetBytes": PLAN["data_offset_bytes"],
            "filesystem": "ext4",
        },
        separators=(",", ":"),
    ).encode()


class LabBackend:
    binding = BINDING

    def __init__(self, tools, work, case):
        self.tools, self.work, self.case = tools, work, case
        self.directory = work / case
        self.disk = self.directory / "scratch.raw"
        self.lines = Queue()

    def preflight(self, context):
        if context["initialization_approval"]["plan"] != PLAN:
            raise RuntimeError("lab supports only its fixed fixture plan")
        if not self.disk.is_file() or self.disk.stat().st_size != PLAN["device_bytes"]:
            raise RuntimeError("wrong lab disk")
        with self.disk.open("rb") as disk:
            while chunk := disk.read(1024 * 1024):
                if any(chunk):
                    raise RuntimeError("lab requires a newly created zero-filled file")
        self.log = (self.directory / "guest.log").open("w")
        self.process = subprocess.Popen(
            command(self.tools, self.work, self.disk),
            env=environment(self.tools),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def read_console():
            for line in self.process.stdout:
                line = line.rstrip()
                self.log.write(line + "\n")
                self.log.flush()
                self.lines.put(line)
            self.lines.put(None)

        self.reader = Thread(target=read_console, daemon=True)
        self.reader.start()

    def expect(self, prefix):
        deadline = time.monotonic() + 180
        while True:
            line = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
            if line is None or line.startswith("COCO_TEST_FAIL:"):
                raise RuntimeError("guest failed before completion")
            if line.startswith(prefix):
                return line
            if time.monotonic() >= deadline:
                raise RuntimeError("guest response timeout")

    def execute(self, step, context):
        if self.expect("COCO_JOURNAL_REQUEST:") != f"COCO_JOURNAL_REQUEST:{step}":
            raise RuntimeError("out-of-order guest request")
        self.process.stdin.write(f"execute:{step}\n")
        self.process.stdin.flush()
        response = self.expect("COCO_JOURNAL_RESULT:").split(":")
        if len(response) != 4 or response[1] != step:
            raise RuntimeError("invalid guest completion")
        _, _, header, extra = response
        result = {"header_sha256": header}
        if step == "luks-format" and extra != "-":
            raise RuntimeError("invalid format completion")
        if step == "integrity-initialize":
            result["stack_sha256"] = extra
        elif step == "filesystem-create":
            result["filesystem_uuid"] = extra
        elif step == "durability-barrier":
            data = manifest(header)
            if hashlib.sha256(data).hexdigest() != extra:
                raise RuntimeError("manifest does not match fixture plan")
            self.expect("COCO_TEST_PASS:initialize")
            if self.process.wait(timeout=30):
                raise RuntimeError("guest did not exit cleanly")
            self.reader.join(timeout=5)
            # Publish manifest outside the guest before acknowledging the barrier.
            with (self.directory / "manifest.json").open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            directory = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            result["manifest_sha256"] = extra
        if self.case == f"after-{step}":
            print(f"INJECTED_PROCESS_DEATH:after-{step}", flush=True)
            os._exit(77)  # No exception unwinding or completion acknowledgement.
        return Completion(result, {"source": "unauthenticated-non-TEE-lab-console"})


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--worker", choices=["success", *[f"after-{s}" for s in STEPS]])
    args = parser.parse_args()
    tools, work = args.tools.resolve(), args.work.absolute()
    if args.worker:
        authority = Authority(work / args.worker / "authority.db")
        result = Executor(
            authority, PEER, LabBackend(tools, work, args.worker)
        ).initialize("init-1", 1)
        assert result["state"] == "Ready"
        return
    subprocess.run(
        [
            sys.executable,
            str(HERE / "build.py"),
            "--tools",
            str(tools),
            "--work",
            str(work),
        ],
        check=True,
    )
    for case in ["success", *[f"after-{s}" for s in STEPS]]:
        directory = work / case
        directory.mkdir(mode=0o700)
        disk = directory / "scratch.raw"
        with disk.open("xb") as target:
            target.truncate(PLAN["device_bytes"])
        authority = Authority(directory / "authority.db")
        authority.register(
            "scratch",
            PEER.policy_sha256,
            PROFILE,
            BINDING.credential_uri,
            mount_binding={
                "manifest_uri": "kbs:///storage-test/records/scratch-v1",
                "container_name": "app",
                "destination": "/data",
                "access": "rw",
            },
        )
        authority.approve_initialization("scratch", PLAN)
        with (directory / "worker.log").open("w") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    __file__,
                    "--tools",
                    str(tools),
                    "--work",
                    str(work),
                    "--worker",
                    case,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                status = process.wait(timeout=600)
            finally:
                # Kill the disposable VM too, including after coordinator death.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if status != (0 if case == "success" else 77):
            raise RuntimeError(f"unexpected worker status {status}: {case}")
        restored = Authority(directory / "authority.db")
        record = restored.inspect("scratch")
        before = hashlib.sha256(disk.read_bytes()).hexdigest()
        try:
            Executor(restored, PEER, LabBackend(tools, work, case)).initialize(
                "init-1", 1
            )
        except RecoveryRequired:
            pass
        else:
            raise RuntimeError("initialization retry unexpectedly accepted")
        assert hashlib.sha256(disk.read_bytes()).hexdigest() == before
        if case == "success":
            assert record["state"] == "Ready"
            restored.close("scratch", PEER, record["owner"]["activation_id"])
            opened = restored.begin("scratch", PEER, "reopen-1", "reopen", 1)
            with (directory / "reopen.log").open("w") as log:
                subprocess.run(
                    command(tools, work, disk, "reopen", False),
                    env=environment(tools),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                    check=True,
                )
            log = (directory / "reopen.log").read_text()
            assert "COCO_TEST_PASS:reopen" in log and "COCO_TEST_FAIL:" not in log
            restored.close("scratch", PEER, opened["owner"]["activation_id"])
        else:
            assert record["state"] == "Initializing"
            assert record["initialization_steps"][-1]["step"] == case.removeprefix(
                "after-"
            )
            assert record["initialization_steps"][-1]["status"] == "Started"
        (directory / "record.json").write_text(
            json.dumps(restored.inspect("scratch"), indent=2) + "\n"
        )
        print(f"PASS {case}: journal durable; retry caused no disk change", flush=True)
    print(
        "PASS journal/kernel integration; synthetic peer "
        "and unauthenticated lab transport only"
    )


if __name__ == "__main__":
    main()
