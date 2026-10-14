#!/usr/bin/env python3
"""Build a private native-agent storage guest overlay; never install or approve.

Only the explicitly selected agent replaces a base path. All other added paths
must be new. The original initrd, modules/tools and AA client are hash-pinned.
Output is base + compressed newc overlay, both recorded for independent measure.
"""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import stat
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


builder = module(
    "builder", ROOT / "scripts/confidential-storage/build-attestation-probe.py"
)
kernel = module("kernel", HERE.parent / "downstream-guest/build.py")


def build(args):
    raw = {
        name: getattr(args, name).read_bytes()
        for name in ("base", "agent", "service", "client", "config")
    }
    if (
        builder.sha256(raw["base"]) != builder.BASE_SHA256
        or builder.sha256(raw["client"]) != builder.CLIENT_SHA256
    ):
        raise ValueError("unexpected base/private AA client")
    for name in ("agent", "service"):
        if builder.sha256(raw[name]) != getattr(args, name + "_sha256") or not raw[
            name
        ].startswith(b"\x7fELF"):
            raise ValueError("unexpected reviewed ELF input")
    config = json.loads(raw["config"])
    if (
        config.get("purpose") != "isolated-confidential-storage/v1"
        or len(raw["config"]) > 256 * 1024
    ):
        raise ValueError("invalid public guest configuration")
    required_fields = {
        "schema_version",
        "purpose",
        "authority",
        "kbs",
        "expected",
        "manifest",
    }
    if set(config) not in (
        required_fields,
        required_fields | {"initialization_plan_sha256"},
    ):
        raise ValueError("unexpected config fields; never put keys in guest image")
    _, names, inode = builder.read_archive(gzip.decompress(raw["base"]))
    records = []
    replaced = set()

    def add(name, mode, data, replace=False):
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError("unsafe path")
        if name in names:
            if not replace or name != "usr/bin/kata-agent" or name in replaced:
                raise ValueError("unexpected path replacement")
            replaced.add(name)
        elif replace:
            raise ValueError("missing original replacement target")
        parent = str(Path(name).parent)
        if parent != "." and parent not in names:
            add(parent, stat.S_IFDIR | 0o755, b"")
        names.add(name)
        records.append((name, mode, data))

    lock = json.loads(
        (HERE.parent / "downstream-guest/tools-inventory.json").read_text()
    )["files"]
    seen = set()
    with tarfile.open(args.packages / "storage-tools.tar") as archive:
        for item in archive:
            if not item.isfile() or item.name not in lock or item.name in seen:
                raise ValueError("unexpected tool bundle entry")
            data = archive.extractfile(item).read()
            if builder.sha256(data) != lock[item.name]:
                raise ValueError("tool digest mismatch")
            add("opt/coco-storage-tools/" + item.name, stat.S_IFREG | 0o700, data)
            seen.add(item.name)
    if seen != set(lock):
        raise ValueError("incomplete tools")
    for name, expected in kernel.MODULES.items():
        filename = name + ".ko.xz"
        data = (args.packages / filename).read_bytes()
        if builder.sha256(data) != expected:
            raise ValueError("module digest mismatch")
        add("opt/coco-storage-modules/" + filename, stat.S_IFREG | 0o600, data)
    for name in ("cryptsetup", "veritysetup", "integritysetup", "dmsetup", "mkfs.ext4"):
        data = (
            "#!/bin/sh\nset -eu\n"
            "unset LD_PRELOAD LD_AUDIT GLIBC_TUNABLES LD_LIBRARY_PATH\n"
            "exec /opt/coco-storage-tools/lib/ld-linux-x86-64.so.2 "
            "--library-path /opt/coco-storage-tools/lib "
            f'/opt/coco-storage-tools/bin/{name} "$@"\n'
        ).encode()
        add("usr/sbin/" + name, stat.S_IFREG | 0o700, data)
    add("usr/bin/kata-agent", stat.S_IFREG | 0o700, raw["agent"], replace=True)
    add("usr/bin/confidential-storage-service", stat.S_IFREG | 0o700, raw["service"])
    add("usr/bin/storage-aa-client", stat.S_IFREG | 0o700, raw["client"])
    add(
        "usr/bin/storage-service-start",
        stat.S_IFREG | 0o700,
        (HERE / "start-service.sh").read_bytes(),
    )
    add("etc/confidential-storage/service.json", stat.S_IFREG | 0o600, raw["config"])
    stream = b""
    for name, mode, data in records:
        stream += builder.entry(name, mode, data, inode)
        inode += 1
    stream += builder.entry("TRAILER!!!", 0, b"", inode)
    stream += bytes(-len(stream) % 512)
    overlay = gzip.compress(stream, mtime=0)
    result = raw["base"] + overlay
    with args.overlay.open("xb") as output:
        output.write(overlay)
    with args.output.open("xb") as output:
        output.write(result)
    return {
        "purpose": "native-private-storage-guest",
        "approvedForStorageKeys": False,
        "inputsSha256": {name: builder.sha256(data) for name, data in raw.items()},
        "initrdSha256": builder.sha256(result),
        "overlaySha256": builder.sha256(overlay),
        "replaced": sorted(replaced),
        "records": {name: builder.sha256(data) for name, _, data in records},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "base",
        "agent",
        "service",
        "client",
        "config",
        "packages",
        "overlay",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("agent", "service"):
        parser.add_argument("--" + name + "-sha256", required=True)
    print(json.dumps(build(parser.parse_args()), indent=2))
