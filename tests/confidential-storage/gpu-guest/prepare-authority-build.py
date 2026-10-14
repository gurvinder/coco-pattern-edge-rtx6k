#!/usr/bin/env python3
"""Create a public-only, offline-wheel authority build context; never add state."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[3]
AUTHORITY = ROOT / "components/confidential-storage-authority"


def prepare(args):
    wheel_lock = json.loads((AUTHORITY / "runtime-wheels.json").read_text())
    sources = []
    for name, digest in wheel_lock.items():
        path = args.wheels / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("wheel hash mismatch")
        sources.append((path, "wheels/" + name))
    for path in sorted(AUTHORITY.glob("*.py")):
        if not path.name.startswith("test_"):
            sources.append(
                (path, "components/confidential-storage-authority/" + path.name)
            )
    sources.append(
        (
            AUTHORITY / "runtime-requirements.txt",
            "components/confidential-storage-authority/runtime-requirements.txt",
        )
    )
    for path in sorted(
        (ROOT / "components/confidential-storage-guest/profiles").glob("*.json")
    ):
        sources.append(
            (path, "components/confidential-storage-guest/profiles/" + path.name)
        )
    sources.append((AUTHORITY / "Containerfile", "Containerfile"))
    with args.output.open("xb") as output, tarfile.open(
        fileobj=output, mode="w:gz"
    ) as archive:
        for path, name in sources:
            archive.add(path, arcname=name, recursive=False)
    print(
        json.dumps(
            {
                "publicOnly": True,
                "files": {
                    name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path, name in sources
                },
                "archiveSha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args())
