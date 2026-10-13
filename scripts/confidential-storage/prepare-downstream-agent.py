#!/usr/bin/env python3
"""Prepare the hash-pinned downstream guest-only, activation-disabled agent port.

Supply Source0 extracted from the recorded SRPM. Output must be a new directory;
failed preparation must not be reused. This script does not build or install.
"""
import argparse
import hashlib
import importlib.util
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
    component = root / "components/confidential-storage-kata"
    lock = json.loads((component / "downstream-lock.json").read_text())
    patch = component / lock["patch"]["file"]
    overlay_path = component / lock["overlayLock"]
    if (
        digest(args.archive.read_bytes()) != lock["sourceArchiveSha256"]
        or digest(patch.read_bytes()) != lock["patch"]["sha256"]
        or digest(overlay_path.read_bytes()) != lock["overlayLockSha256"]
    ):
        parser.error("source, patch or overlay lock digest mismatch")
    spec = importlib.util.spec_from_file_location(
        "overlay", root / "scripts/confidential-storage/apply-kata-agent.py"
    )
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    copies = []
    for item in json.loads(overlay_path.read_text())["overlay"]:
        data = (root / item["source"]).read_bytes()
        output = overlay.overlay_content(item["source"], data)
        if (
            digest(data) != item["sourceSha256"]
            or digest(output) != item["outputSha256"]
        ):
            parser.error("guest library overlay digest mismatch")
        copies.append((item["destination"], output))
    args.output.mkdir()  # Refuse overwrite/reuse, including partial attempts.
    with tarfile.open(args.archive) as archive:
        archive.extractall(args.output, filter="data")
    source = args.output / "kata-containers-3.31.0"
    if sorted(p.name for p in args.output.iterdir()) != [source.name]:
        parser.error("unexpected source archive layout")
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
    for name, data in copies:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    print(f"Prepared {source}; activation disabled, host runtime unchanged")


if __name__ == "__main__":
    main()
