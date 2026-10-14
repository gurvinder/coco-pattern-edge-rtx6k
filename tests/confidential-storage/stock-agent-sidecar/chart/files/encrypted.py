# Synthetic encrypted-storage extension. Ephemeral keys and RAM-backed files only.
import ctypes
import json
import os
from pathlib import Path
import hashlib
import io
import lzma
import re
import stat
import subprocess
import tarfile
import urllib.request


def setup_encrypted(mount, check, libc):
    expected = os.environ["TOOLS_SHA256"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(
        "http://sidecar-public-tools:8080/bundle.tar", timeout=30
    ) as stream:
        bundle = stream.read(20 * 1024 * 1024)
    assert hashlib.sha256(bundle).hexdigest() == expected
    tools = Path("/opt/probe-tools")
    tools.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(bundle)) as archive:
        for member in archive:
            assert (
                member.isfile()
                and not member.name.startswith("/")
                and ".." not in Path(member.name).parts
            )
            target = tools / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(0o700)
    env = dict(os.environ, MKE2FS_CONFIG=str(tools / "etc/mke2fs.conf"))
    for name in ["LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "GLIBC_TUNABLES"]:
        env.pop(name, None)

    def tool(name, *args, ok=True):
        result = subprocess.run(
            [
                str(tools / "lib/ld-linux-x86-64.so.2"),
                "--library-path",
                str(tools / "lib"),
                str(tools / "bin" / name),
                *args,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if ok and result.returncode:
            raise RuntimeError(name + " failed: " + result.stderr[:1500])
        return result

    assert os.uname().release == "6.12.0-211.16.1.el10_2.x86_64"
    # All disks are newly created regular files on this guest-private tmpfs.
    mount("tmpfs", "/mnt/private", "tmpfs", 2 | 4 | 8, "size=384m,mode=0700")
    key = "/mnt/private/test-key"
    Path(key).write_bytes(os.urandom(32))
    Path(key).chmod(0o600)
    for name in ["model", "data"]:
        with open("/mnt/private/" + name + ".luks", "xb") as handle:
            handle.truncate(128 * 1024 * 1024)
    # These device nodes address this VM's kernel only, never host devices.
    Path("/dev/mapper").mkdir(exist_ok=True)
    for path, mode, major, minor in [
        ("/dev/loop-control", stat.S_IFCHR, 10, 237),
        ("/dev/mapper/control", stat.S_IFCHR, 10, 236),
    ] + [(f"/dev/loop{i}", stat.S_IFBLK, 7, i) for i in range(8)]:
        if not Path(path).exists():
            os.mknod(path, mode | 0o600, os.makedev(major, minor))
    print(
        json.dumps(
            {
                "loopModuleLoaded": Path("/sys/module/loop").exists(),
                "deviceMapperLoaded": Path("/sys/module/dm_mod").exists(),
            }
        ),
        flush=True,
    )
    fmt = [
        "luksFormat",
        "--batch-mode",
        "--type",
        "luks2",
        "--cipher",
        "aes-xts-plain64",
        "--key-size",
        "512",
        "--pbkdf",
        "argon2id",
        "--pbkdf-memory",
        "65536",
        "--pbkdf-parallel",
        "1",
        "--pbkdf-force-iterations",
        "4",
        "--key-file",
        key,
    ]
    opening = [
        "open",
        "--type",
        "luks2",
        "--disable-external-tokens",
        "--disable-keyring",
        "--key-file",
        key,
    ]
    tool("cryptsetup", *fmt, "/mnt/private/model.luks")
    tool("cryptsetup", *opening, "/mnt/private/model.luks", "sidecar-model")
    tool("mkfs.ext4", "-q", "-F", "/dev/mapper/sidecar-model")
    mount("/dev/mapper/sidecar-model", "/mnt/ro", "ext4", 2 | 4 | 8)
    Path("/mnt/ro/model.txt").write_text("synthetic model, no secrets\n")
    os.chmod("/mnt/ro", 0o755)
    os.sync()
    check(libc.umount2(b"/mnt/ro", 0))
    tool("cryptsetup", "close", "sidecar-model")
    hashed = tool(
        "veritysetup", "format", "/mnt/private/model.luks", "/mnt/private/verity.hash"
    ).stdout
    root = re.search(r"Root hash:\s+([a-f0-9]{64})", hashed).group(1)
    tool(
        "veritysetup",
        "open",
        "/mnt/private/model.luks",
        "sidecar-verity",
        "/mnt/private/verity.hash",
        root,
    )
    tool(
        "cryptsetup",
        *opening,
        "--readonly",
        "/dev/mapper/sidecar-verity",
        "sidecar-model",
    )
    mount("/dev/mapper/sidecar-model", "/mnt/ro", "ext4", 1 | 2 | 4 | 8, "noload")
    # dm-verity has loaded dm-bufio; load only the pinned missing module set.
    for name in ["xor", "async_tx", "async_xor", "dm-integrity"]:
        normalized = name.replace("-", "_")
        if Path("/sys/module/" + normalized).exists():
            continue
        raw = lzma.decompress((tools / "modules" / (name + ".ko.xz")).read_bytes())
        buffer = ctypes.create_string_buffer(raw)
        check(
            libc.syscall(
                ctypes.c_long(175),
                ctypes.cast(buffer, ctypes.c_void_p),
                ctypes.c_ulong(len(raw)),
                ctypes.c_char_p(b""),
            )
        )
    tool(
        "cryptsetup",
        *fmt,
        "--integrity",
        "hmac-sha256",
        "--sector-size",
        "4096",
        "/mnt/private/data.luks",
    )
    tool("cryptsetup", *opening, "/mnt/private/data.luks", "sidecar-data")
    tool(
        "mkfs.ext4",
        "-q",
        "-F",
        "-E",
        "root_owner=1001:1001",
        "/dev/mapper/sidecar-data",
    )
    mount("/dev/mapper/sidecar-data", "/mnt/rw", "ext4", 2 | 4 | 8)
    # Print status only: never dm-crypt tables or key-bearing command output.
    status = tool("cryptsetup", "status", "sidecar-data").stdout
    assert "hmac(sha256)" in status
    print("SIDECAR_LUKS_VERITY_AND_KEYED_INTEGRITY_OPEN_PASS", flush=True)

    def close_encrypted():
        for name in ["ro", "rw"]:
            check(libc.umount2(("/mnt/" + name).encode(), 0))
        tool("cryptsetup", "close", "sidecar-data")
        tool("cryptsetup", *opening, "/mnt/private/data.luks", "sidecar-data")
        mount("/dev/mapper/sidecar-data", "/mnt/rw", "ext4", 2 | 4 | 8)
        assert Path("/mnt/rw/canary").read_text() == "unprivileged writer\n"
        check(libc.umount2(b"/mnt/rw", 0))
        tool("cryptsetup", "close", "sidecar-data")
        tool("cryptsetup", "close", "sidecar-model")
        tool("veritysetup", "close", "sidecar-verity")
        check(libc.umount2(b"/mnt/private", 0))
        print("SIDECAR_ENCRYPTED_REOPEN_CLEANUP_PASS", flush=True)

    return close_encrypted
