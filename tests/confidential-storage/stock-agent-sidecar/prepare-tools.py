#!/usr/bin/env python3
"""Create a public, hash-checked diagnostic tool bundle; never include keys."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--packages", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "downstream-guest/tools-inventory.json").read_text())["files"]
modules = {
    "dm-integrity": "b39f3b16639012e3e76f500ef84ae66b4c68e6885547bdf5bd945aa0cbf9691c",
    "async_tx": "f1e7207addd9501f2c107b69a1f34405ec75189daf4b30428c9e60cfd4025254",
    "async_xor": "f2a8294ef05e53d85232e56e880a1dea802510c5eb45d3857569727608fe83f0",
    "xor": "0ec8d0b5885d37c809d49a36846a13ca4293dbaa3cfce6c8f74ae8cc7995ae73",
}
files = {}
with tarfile.open(a.packages / "storage-tools.tar") as archive:
    for member in archive:
        assert member.isfile() and member.name in lock and member.name not in files
        data = archive.extractfile(member).read()
        assert hashlib.sha256(data).hexdigest() == lock[member.name]
        files[member.name] = data
assert set(files) == set(lock)
for name, digest in modules.items():
    data = (a.packages / (name + ".ko.xz")).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest
    files["modules/" + name + ".ko.xz"] = data
with tarfile.open(a.output, "w") as archive:
    for name, data in sorted(files.items()):
        entry = tarfile.TarInfo(name)
        entry.size = len(data)
        entry.mode = 0o700
        archive.addfile(entry, io.BytesIO(data))
print(
    json.dumps(
        {
            "bundleSha256": hashlib.sha256(a.output.read_bytes()).hexdigest(),
            "bytes": a.output.stat().st_size,
            "files": len(files),
        }
    )
)
