#!/usr/bin/env python3
"""Stage the pinned native-lifecycle guest experiment; never build or install.

The service must be separately packaged with measured configuration and strict
InitData before this agent can authorize storage. This is not the earlier
activation-disabled runtime-adapter patch. No host runtime files are modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    component = root / "components/confidential-storage-kata/native-agent"
    lock = json.loads((component / "lock.json").read_text())
    if digest(args.archive.read_bytes()) != lock["sourceArchiveSha256"]:
        parser.error("downstream source digest mismatch")
    for name, expected in lock["files"].items():
        if digest((component / name).read_bytes()) != expected:
            parser.error("native overlay digest mismatch")
    args.output.mkdir()
    with tarfile.open(args.archive) as archive:
        archive.extractall(args.output, filter="data")
    source = args.output / "kata-containers-3.31.0"
    if sorted(p.name for p in args.output.iterdir()) != [source.name]:
        parser.error("unexpected source archive layout")
    patch = component / "native-lifecycle.patch"
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
    destination = source / "src/agent/src/storage_service.rs"
    with destination.open("xb") as stream:
        stream.write((component / "storage_service.rs").read_bytes())
    print(f"Prepared {source}; not installed, host runtime unchanged")


if __name__ == "__main__":
    main()
