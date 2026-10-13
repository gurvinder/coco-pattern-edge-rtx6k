#!/usr/bin/env python3
"""Build a separate no-key authority-protocol guest; never install it.

Pins the original guest/private AA client. The supplied Rust probe and public
configuration are recorded in build output and covered by the SNP measurement.
No generic API, KBS credentials or private keys are installed.
"""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "builder", ROOT / "scripts/confidential-storage/build-attestation-probe.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def build(base, client, probe, config):
    if (
        builder.sha256(base) != builder.BASE_SHA256
        or builder.sha256(client) != builder.CLIENT_SHA256
    ):
        raise ValueError("unexpected boot inputs")
    value = json.loads(config)
    if value.get("purpose") != "no-key-live-authority-probe" or len(config) > 32768:
        raise ValueError("public diagnostic configuration required")
    if not probe.startswith(b"\x7fELF") or not 0 < len(probe) < 16 * 1024 * 1024:
        raise ValueError("bounded compiled probe required")
    prefix, names, inode = builder.read_archive(gzip.decompress(base))
    script = b"""#!/bin/sh
set -eu
umask 077
ulimit -c 0
directory=/run/confidential-containers/attestation-agent
i=0
while [ ! -S "$directory/attestation-agent.sock" ]; do
    [ "$i" -lt 60 ] || exit 1
    i=$((i+1))
    sleep 1
done
[ ! -L "$directory" ]
chown 0:0 "$directory"
chmod 0700 "$directory"
# Give the read-only console collector time to attach before results are emitted.
sleep 20
exec /usr/bin/storage-authority-probe
"""
    unit = b"""[Unit]
Description=No-key live private authority protocol probe
DefaultDependencies=no
After=kata-agent.service
Requires=kata-agent.service

[Service]
Type=oneshot
ExecStart=/usr/bin/storage-authority-probe-start
TimeoutStartSec=150
UMask=0077
LimitCORE=0
StandardOutput=tty
StandardError=tty
TTYPath=/dev/hvc0
RemainAfterExit=yes
"""
    additions = [
        ("usr/bin/storage-aa-client", stat.S_IFREG | 0o700, client),
        ("usr/bin/storage-authority-probe", stat.S_IFREG | 0o700, probe),
        ("usr/bin/storage-authority-probe-start", stat.S_IFREG | 0o700, script),
        ("etc/storage-authority-probe.json", stat.S_IFREG | 0o600, config),
        (
            "usr/lib/systemd/system/storage-authority-probe.service",
            stat.S_IFREG | 0o644,
            unit,
        ),
        (
            "etc/systemd/system/kata-containers.target.wants/storage-authority-probe.service",
            stat.S_IFLNK | 0o777,
            b"/usr/lib/systemd/system/storage-authority-probe.service",
        ),
    ]
    for name, mode, data in additions:
        if name in names or str(Path(name).parent) not in names:
            raise ValueError("unexpected archive layout")
        prefix += builder.entry(name, mode, data, inode)
        inode += 1
    prefix += builder.entry("TRAILER!!!", 0, b"", inode)
    prefix += bytes(-len(prefix) % 512)
    return gzip.compress(prefix, mtime=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "client", "probe", "config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    raw = {
        name: getattr(args, name).read_bytes()
        for name in ("base", "client", "probe", "config")
    }
    result = build(**raw)
    with args.output.open("xb") as stream:
        stream.write(result)
    print(
        json.dumps(
            {
                "purpose": "no-key-live-authority-probe",
                "approvedForStorageKeys": False,
                "inputsSha256": {
                    name: builder.sha256(value) for name, value in raw.items()
                },
                "initrdSha256": builder.sha256(result),
                "bytes": len(result),
            },
            indent=2,
        )
    )
