#!/usr/bin/env python3
"""Build a no-KBS, no-PVC downstream kernel compatibility diagnostic."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import stat
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location(
    "builder", ROOT / "scripts/confidential-storage/build-attestation-probe.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
MODULES = {
    "dm-integrity": "b39f3b16639012e3e76f500ef84ae66b4c68e6885547bdf5bd945aa0cbf9691c",
    "async_tx": "f1e7207addd9501f2c107b69a1f34405ec75189daf4b30428c9e60cfd4025254",
    "async_xor": "f2a8294ef05e53d85232e56e880a1dea802510c5eb45d3857569727608fe83f0",
    "xor": "0ec8d0b5885d37c809d49a36846a13ca4293dbaa3cfce6c8f74ae8cc7995ae73",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "packages", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--overlay-output", type=Path)
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--extra-binary", type=Path)
    parser.add_argument("--extra-binary-sha256")
    args = parser.parse_args()
    if bool(args.extra_binary) != bool(args.extra_binary_sha256):
        parser.error("diagnostic ELF and digest must be supplied together")
    base = args.base.read_bytes()
    if builder.sha256(base) != builder.BASE_SHA256:
        raise ValueError("unexpected base")
    prefix, names, inode = builder.read_archive(gzip.decompress(base))
    if args.overlay_output:
        # Linux supports concatenated compressed newc archives. Keep the exact
        # original base and transfer only the new archive for subsequent probes.
        prefix = b""
    additions = []

    def add(name, mode, data):
        if name in names or name.startswith("/") or ".." in name.split("/"):
            raise ValueError("duplicate or unsafe guest path")
        parent = str(Path(name).parent)
        if parent != "." and parent not in names:
            add(parent, stat.S_IFDIR | 0o755, b"")
        names.add(name)
        additions.append((name, mode, data))

    lock = json.loads((HERE / "tools-inventory.json").read_text())["files"]
    seen = set()
    with tarfile.open(args.packages / "storage-tools.tar") as archive:
        for item in archive:
            if not item.isfile() or item.name not in lock or item.name in seen:
                raise ValueError("unexpected tool payload")
            data = archive.extractfile(item).read()
            if builder.sha256(data) != lock[item.name]:
                raise ValueError("tool digest mismatch")
            add("opt/coco-storage-tools/" + item.name, stat.S_IFREG | 0o700, data)
            seen.add(item.name)
    if seen != set(lock):
        raise ValueError("incomplete tool bundle")
    for name, digest in MODULES.items():
        filename = name + ".ko.xz"
        data = (args.packages / filename).read_bytes()
        if builder.sha256(data) != digest:
            raise ValueError("module digest mismatch")
        add("opt/coco-storage-modules/" + filename, stat.S_IFREG | 0o600, data)
    for name in ("cryptsetup", "veritysetup", "integritysetup", "dmsetup", "mkfs.ext4"):
        wrapper = (
            "#!/bin/sh\nset -eu\n"
            "unset LD_PRELOAD LD_AUDIT GLIBC_TUNABLES LD_LIBRARY_PATH\n"
            "exec /opt/coco-storage-tools/lib/ld-linux-x86-64.so.2 "
            "--library-path /opt/coco-storage-tools/lib "
            f'/opt/coco-storage-tools/bin/{name} "$@"\n'
        ).encode()
        add("usr/sbin/" + name, stat.S_IFREG | 0o700, wrapper)
    add(
        "usr/bin/storage-kernel-probe",
        stat.S_IFREG | 0o700,
        (args.probe or HERE / "probe.sh").read_bytes(),
    )
    if args.extra_binary:
        raw = args.extra_binary.read_bytes()
        if builder.sha256(raw) != args.extra_binary_sha256 or not raw.startswith(
            b"\x7fELF"
        ):
            raise ValueError("unexpected diagnostic ELF")
        add("usr/bin/storage-initializer-probe", stat.S_IFREG | 0o700, raw)
    unit = b"""[Unit]
Description=Disposable RAM disk storage kernel compatibility probe
DefaultDependencies=no
After=kata-agent.service
Requires=kata-agent.service
[Service]
Type=oneshot
ExecStart=/usr/bin/storage-kernel-probe
TimeoutStartSec=360
UMask=0077
LimitCORE=0
StandardOutput=tty
StandardError=tty
TTYPath=/dev/hvc0
RemainAfterExit=yes
"""
    add(
        "usr/lib/systemd/system/storage-kernel-probe.service",
        stat.S_IFREG | 0o644,
        unit,
    )
    add(
        "etc/systemd/system/kata-containers.target.wants/storage-kernel-probe.service",
        stat.S_IFLNK | 0o777,
        b"/usr/lib/systemd/system/storage-kernel-probe.service",
    )
    for name, mode, data in additions:
        prefix += builder.entry(name, mode, data, inode)
        inode += 1
    prefix += builder.entry("TRAILER!!!", 0, b"", inode)
    prefix += bytes(-len(prefix) % 512)
    result = gzip.compress(prefix, mtime=0)
    overlay_sha = None
    if args.overlay_output:
        overlay_sha = builder.sha256(result)
        with args.overlay_output.open("xb") as stream:
            stream.write(result)
        result = base + result
    with args.output.open("xb") as stream:
        stream.write(result)
    print(
        json.dumps(
            {
                "purpose": "no-KBS-no-PVC-kernel-compatibility-probe",
                "approvedForStorageKeys": False,
                "baseSha256": builder.sha256(base),
                "initrdSha256": builder.sha256(result),
                "overlaySha256": overlay_sha,
                "additions": {n: builder.sha256(d) for n, _, d in additions},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
