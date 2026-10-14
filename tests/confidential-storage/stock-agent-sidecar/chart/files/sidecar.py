"""Guest-privileged diagnostic only: no host paths, KBS or existing claims."""

import ctypes
import json
import os
import shutil
import time
from pathlib import Path

libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_ulong,
    ctypes.c_char_p,
]
libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.setns.argtypes = [ctypes.c_int, ctypes.c_int]
libc.syscall.restype = ctypes.c_long


def check(result):
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def mount(source, target, kind=None, flags=0, data=None):
    print(
        json.dumps({"mountSource": source, "mountTarget": target, "flags": flags}),
        flush=True,
    )
    check(
        libc.mount(
            source.encode() if source else None,
            target.encode(),
            kind.encode() if kind else None,
            flags,
            data.encode() if data else None,
        )
    )


shared = Path("/rendezvous")
for _ in range(240):
    if shared.joinpath("application.json").exists():
        break
    time.sleep(1)
else:
    raise RuntimeError("application did not register")
app = json.loads(shared.joinpath("application.json").read_text())
pid = app["pid"]
assert type(pid) is int and pid > 1 and app["uid"] == 1001
process_status = Path(f"/proc/{pid}/status").read_text()
assert (
    next(
        line for line in process_status.splitlines() if line.startswith("Uid:")
    ).split()[1:]
    == ["1001"] * 4
)
assert "application.json" in Path(f"/proc/{pid}/cmdline").read_bytes().decode()
print(
    json.dumps(
        {
            "sidecarPid": os.getpid(),
            "applicationPid": pid,
            "sidecarMountNS": os.readlink("/proc/self/ns/mnt"),
            "applicationMountNS": os.readlink(f"/proc/{pid}/ns/mnt"),
            "tools": {
                x: shutil.which(x)
                for x in ["cryptsetup", "dmsetup", "mkfs.ext4", "mount"]
            },
        }
    ),
    flush=True,
)
for name in ["ro", "rw", "private"]:
    Path("/mnt/" + name).mkdir(parents=True, exist_ok=True)
if os.environ.get("ENCRYPTED_PROBE") == "true":
    close_encrypted = globals()["setup_encrypted"](mount, check, libc)
else:
    close_encrypted = None
    for name in ["ro", "rw", "private"]:
        mount("tmpfs", "/mnt/" + name, "tmpfs", 2 | 4 | 8, "size=4m,mode=0700")
    Path("/mnt/private/test-key").write_bytes(os.urandom(32))
    Path("/mnt/ro/model.txt").write_text("synthetic model, no secrets\n")
    os.chmod("/mnt/ro", 0o755)
    os.chown("/mnt/rw", 1001, 1001)
    os.chmod("/mnt/rw", 0o700)
helper_pid = os.getpid()


# Enter only this pod consumer's mount namespace, then its root. The paths to
# sidecar mounts remain reachable through the shared *guest* PID namespace.
def consumer_mounts(remove=False):
    child = os.fork()
    if child == 0:
        try:
            trees = {}
            if not remove:
                assert os.uname().machine == "x86_64"
                for source in ["ro", "rw"]:
                    tree = libc.syscall(
                        ctypes.c_long(428),
                        ctypes.c_int(-100),
                        ctypes.c_char_p(("/mnt/" + source).encode()),
                        ctypes.c_uint(1 | os.O_CLOEXEC),
                    )
                    if tree < 0:
                        check(-1)
                    trees[source] = tree
            ns = os.open(f"/proc/{pid}/ns/mnt", os.O_RDONLY)
            root = os.open(f"/proc/{pid}/root", os.O_RDONLY | os.O_DIRECTORY)
            print("ENTER_CONSUMER_MOUNT_NS", flush=True)
            check(libc.setns(ns, 0))
            print("ENTER_CONSUMER_ROOT", flush=True)
            os.fchdir(root)
            os.chroot(".")
            os.chdir("/")
            print("CONSUMER_ROOT_ENTERED", flush=True)
            for source, target, readonly in [
                ("ro", "/models/qwen", True),
                ("rw", "/data", False),
            ]:
                if remove:
                    check(libc.umount2(target.encode(), 0))
                else:
                    print("ATTACH_DETACHED_MOUNT " + target, flush=True)
                    check(
                        libc.syscall(
                            ctypes.c_long(429),
                            ctypes.c_int(trees[source]),
                            ctypes.c_char_p(b""),
                            ctypes.c_int(-100),
                            ctypes.c_char_p(target.encode()),
                            ctypes.c_uint(4),
                        )
                    )
                    os.close(trees[source])
                    mount(
                        None,
                        target,
                        flags=4096 | 32 | 2 | 4 | 8 | (1 if readonly else 0),
                    )
            os._exit(0)
        except BaseException as error:
            print("MOUNT_NAMESPACE_ERROR: " + repr(error), flush=True)
            os._exit(1)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0, "consumer namespace operation failed"


consumer_mounts()
shared.joinpath("mounted.json").write_text(json.dumps({"sidecarPid": helper_pid}))
for _ in range(120):
    if shared.joinpath("result.json").exists():
        break
    time.sleep(1)
else:
    raise RuntimeError("application checks did not finish")
assert Path("/mnt/rw/canary").read_text() == "unprivileged writer\n"
consumer_mounts(remove=True)
if close_encrypted:
    close_encrypted()
else:
    for name in ["ro", "rw", "private"]:
        check(libc.umount2(("/mnt/" + name).encode(), 0))
shared.joinpath("cleanup-done").write_text("done")
print("SIDECAR_MOUNT_SHARE_AND_CLEANUP_PASS", flush=True)
