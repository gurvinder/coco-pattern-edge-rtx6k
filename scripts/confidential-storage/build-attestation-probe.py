#!/usr/bin/env python3
"""Build a separate no-secret private-AA diagnostic initrd; never install it.

The probe emits hardware evidence to the guest console. Its payload is embedded
at build time; it exposes no signing/evidence request interface to applications.
Never approve this diagnostic guest measurement for real storage keys.
"""

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import stat

BASE_SHA256 = "1f9b3a90c60ec7a94985ae966fc66ecb4d04055ca3471b9f0074068f4dac8b62"
CLIENT_SHA256 = "1a9ea738602ccaebf7141e9e01ced3adfb3125e05a63b1332b930898a86509e5"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_archive(raw):
    """Keep the original records byte-for-byte; append only before the trailer."""
    offset = 0
    names = set()
    inodes = []
    while offset + 110 <= len(raw):
        header_end = offset + 110
        header = raw[offset:header_end]
        if header[:6] != b"070701":
            raise ValueError("expected one newc archive")
        values = [int(header[i:][:8], 16) for i in range(6, 110, 8)]
        size, name_size = values[6], values[11]
        if not 0 < name_size <= 4096:
            raise ValueError("invalid cpio pathname")
        name_end = offset + 110 + name_size
        last = name_end - 1
        if raw[last:name_end] != b"\0":
            raise ValueError("invalid cpio pathname termination")
        name = raw[header_end:last].decode("utf-8")
        start = (name_end + 3) & ~3
        end = (start + size + 3) & ~3
        if end > len(raw) or name in names:
            raise ValueError("truncated archive or duplicate pathname")
        if name == "TRAILER!!!":
            if size or any(raw[end:]):
                raise ValueError("unexpected additional archive contents")
            return raw[:offset], names, max(inodes, default=0) + 1
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError("unsafe archive pathname")
        names.add(name)
        inodes.append(values[0])
        offset = end
    raise ValueError("missing cpio trailer")


def entry(name, mode, data, inode):
    encoded = name.encode() + b"\0"
    values = [inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0]
    result = b"070701" + b"".join(f"{v:08x}".encode() for v in values) + encoded
    result += bytes(-len(result) % 4)
    result += data
    result += bytes(-len(result) % 4)
    return result


def runtime_data(path):
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise ValueError("diagnostic runtime data too large")
    value = json.loads(raw)
    if set(value) != {"storage-binding"}:
        raise ValueError("expected diagnostic storage binding")
    binding = value["storage-binding"]
    if set(binding) != {"protocol", "audience", "nonce", "request_sha256", "key"}:
        raise ValueError("invalid storage binding fields")
    if binding["protocol"] != "confidential-storage-request/v1":
        raise ValueError("invalid protocol")
    for field in ("nonce", "request_sha256"):
        if not re.fullmatch("[0-9a-f]{64}", binding[field]):
            raise ValueError("invalid diagnostic digest/nonce")
    if binding["audience"] != "https://storage-test.invalid/v1":
        raise ValueError(
            "only the deliberately unreachable diagnostic audience is allowed"
        )
    key = binding["key"]
    if set(key) != {"kty", "crv", "x", "y"} or (key["kty"], key["crv"]) != (
        "EC",
        "P-256",
    ):
        raise ValueError("public P-256 diagnostic key required")
    for field in ("x", "y"):
        if not re.fullmatch("[A-Za-z0-9_-]{43}", key[field]):
            raise ValueError("invalid public coordinate")
        decoded = base64.urlsafe_b64decode(key[field] + "=")
        if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != key[field]:
            raise ValueError("noncanonical coordinate")
    # This deliberately narrow, ASCII string/object schema canonicalizes in the
    # same way as Trustee's RFC 8785 serializer; no numbers or floats are allowed.
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("ascii")


def build(base, client, runtime):
    if sha256(base) != BASE_SHA256 or sha256(client) != CLIENT_SHA256:
        raise ValueError("unapproved base or private client binary")
    prefix, names, inode = read_archive(gzip.decompress(base))
    report_data = base64.b64encode(hashlib.sha256(runtime).digest()).decode()
    script = f"""#!/bin/sh
set -eu
umask 077
socket=/run/confidential-containers/attestation-agent/attestation-agent.sock
output=/run/storage-attestation-evidence.json
i=0
while [ "$i" -lt 60 ]; do
    if [ -S "$socket" ] && /usr/bin/storage-attestation-probe-client \\
        --attestation_sock "unix://$socket" get-evidence \\
        --runtime-data '{report_data}' > "$output" 2>/dev/null; then
        printf '\\nSTORAGE_ATTESTATION_PROBE_BEGIN\\n'
        cat "$output"
        printf '\\nSTORAGE_ATTESTATION_PROBE_END\\n'
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
printf '\\nSTORAGE_ATTESTATION_PROBE_FAILED\\n'
exit 1
""".encode()
    unit = b"""[Unit]
Description=No-secret private AA attestation probe
After=kata-agent.service
Requires=kata-agent.service

[Service]
Type=oneshot
ExecStart=/usr/bin/storage-attestation-probe
TimeoutStartSec=90
UMask=0077
StandardOutput=tty
StandardError=tty
TTYPath=/dev/console
RemainAfterExit=yes
"""
    additions = [
        ("usr/bin/storage-attestation-probe-client", stat.S_IFREG | 0o700, client),
        ("usr/bin/storage-attestation-probe", stat.S_IFREG | 0o700, script),
        (
            "usr/lib/systemd/system/storage-attestation-probe.service",
            stat.S_IFREG | 0o644,
            unit,
        ),
        (
            "etc/systemd/system/kata-containers.target.wants/"
            "storage-attestation-probe.service",
            stat.S_IFLNK | 0o777,
            b"/usr/lib/systemd/system/storage-attestation-probe.service",
        ),
    ]
    for name, mode, data in additions:
        if name in names or str(Path(name).parent) not in names:
            raise ValueError("unexpected base layout or existing probe")
        prefix += entry(name, mode, data, inode)
        inode += 1
    prefix += entry("TRAILER!!!", 0, b"", inode)
    prefix += bytes(-len(prefix) % 512)
    return gzip.compress(prefix, mtime=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--runtime-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime = runtime_data(args.runtime_data)
    result = build(args.base.read_bytes(), args.client.read_bytes(), runtime)
    # Never replace an existing artifact, especially the supplied boot image.
    with args.output.open("xb") as stream:
        stream.write(result)
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "purpose": "no-secret-private-aa-probe",
                "baseSha256": BASE_SHA256,
                "clientSha256": CLIENT_SHA256,
                "runtimeDataSha256": sha256(runtime),
                "initrdSha256": sha256(result),
                "bytes": len(result),
                "approvedForStorageKeys": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
