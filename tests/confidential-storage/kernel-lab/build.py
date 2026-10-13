#!/usr/bin/env python3
"""Assemble a disposable Linux initramfs from explicitly supplied unpacked tools.

Run unprivileged on AMD64 Linux. No mounting, loop devices, host /dev access,
network access, package installation or KVM. Output includes a random test key;
never publish the initramfs or scratch disk. Use only the sanitized text logs.
"""
import argparse
import gzip
import os
from pathlib import Path
import shutil
import subprocess


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tools", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    a = p.parse_args()
    tools = a.tools.resolve(strict=True)
    work = a.work.absolute()
    work.mkdir(mode=0o700)  # Must be a new directory; never reuse user disks.
    root = work / "root"
    root.mkdir()
    root.chmod(0o755)
    for name in [
        "bin",
        "sbin",
        "usr/bin",
        "usr/sbin",
        "dev",
        "proc",
        "sys",
        "run",
        "tmp",
        "data",
        "etc",
    ]:
        (root / name).mkdir(parents=True, exist_ok=True)
    library_env = os.environ | {
        "LD_LIBRARY_PATH": (
            f"{tools}/lib/x86_64-linux-gnu:" f"{tools}/usr/lib/x86_64-linux-gnu"
        )
    }

    def copy_binary(source, dest):
        destination = root / dest.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=True)
        result = subprocess.run(
            ["ldd", str(source)], env=library_env, text=True, capture_output=True
        )
        if "not found" in result.stdout:
            raise RuntimeError("missing runtime library: " + str(source))
        if result.returncode and "not a dynamic executable" not in result.stderr:
            raise RuntimeError("ldd failed: " + str(source))
        for line in result.stdout.splitlines():
            for word in line.split():
                if word.startswith("/"):
                    lib = Path(word)
                    relative = (
                        lib.relative_to(tools)
                        if lib.is_relative_to(tools)
                        else Path(word.lstrip("/"))
                    )
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(lib, target, follow_symlinks=True)
                    break

    busybox = tools / "bin/busybox"
    shutil.copy2(busybox, root / "bin/busybox")
    for applet in subprocess.check_output(
        [str(busybox), "--list"], text=True
    ).splitlines():
        if applet != "busybox":
            (root / "bin" / applet).symlink_to("busybox")
    (root / "sbin/modprobe").symlink_to("/bin/modprobe")
    for src, dest in [
        ("sbin/cryptsetup", "/usr/sbin/cryptsetup"),
        ("sbin/dmsetup", "/usr/sbin/dmsetup"),
        ("sbin/mke2fs", "/usr/sbin/mkfs.ext4"),
        ("usr/bin/setpriv", "/usr/bin/setpriv"),
    ]:
        copy_binary(tools / src, dest)
    shutil.copy2(tools / "etc/mke2fs.conf", root / "etc/mke2fs.conf")
    kernels = list((tools / "boot").glob("vmlinuz-*"))
    if len(kernels) != 1:
        raise RuntimeError("expected exactly one unpacked kernel")
    version = kernels[0].name.removeprefix("vmlinuz-")
    shutil.copy2(kernels[0], work / "vmlinuz")
    shutil.copytree(tools / "lib/modules" / version, root / "lib/modules" / version)
    subprocess.run(
        ["depmod", "-b", str(root), version],
        executable=str(tools / "bin/kmod"),
        env=library_env,
        check=True,
    )
    here = Path(__file__).resolve().parent
    for name in ["init", "app-test"]:
        shutil.copy2(here / name, root / name)
        (root / name).chmod(0o700)
    # The application script itself must be readable by the unprivileged guest UID.
    (root / "app-test").chmod(0o755)
    (root / "test-key").write_bytes(os.urandom(32))
    (root / "test-key").chmod(0o600)
    with (work / "scratch.raw").open("xb") as disk:
        disk.truncate(128 * 1024 * 1024)
    for directory, _, _ in os.walk(root):
        Path(directory).chmod(0o755)
    # Archive ownership is guest root; no host chown or mknod is needed.
    paths = subprocess.check_output(["find", ".", "-print0"], cwd=root)
    archive = subprocess.run(
        [str(tools / "bin/cpio"), "--null", "-o", "-H", "newc", "--owner=0:0"],
        input=paths,
        cwd=root,
        capture_output=True,
        check=True,
    )
    with gzip.open(work / "initrd.gz", "wb", compresslevel=1) as output:
        output.write(archive.stdout)
    print(f"Built disposable non-TEE kernel lab with Linux {version}")


if __name__ == "__main__":
    main()
