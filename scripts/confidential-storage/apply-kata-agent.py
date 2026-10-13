#!/usr/bin/env python3
"""Prepare the pinned, activation-disabled Kata agent ownership experiment."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def git(source, *args):
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def overlay_content(path, data):
    if path != "components/confidential-storage-kata/runtime-adapter/Cargo.toml":
        return data
    text = data.decode()
    kata_url = "https://github.com/manuelh-dev/kata-containers.git"
    changes = [
        (
            'path = "../../confidential-storage-guest"',
            'path = "../confidential-storage-guest"',
        ),
        (
            f'rustjail = {{ git = "{kata_url}", '
            'rev = "e53adb460e6040e6aa9719a735e17301462c4fa9" }',
            'rustjail = { path = "../../agent/rustjail" }',
        ),
        (
            f'runtime-spec = {{ git = "{kata_url}", '
            'rev = "e53adb460e6040e6aa9719a735e17301462c4fa9" }',
            'runtime-spec = { path = "../runtime-spec" }',
        ),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError("Unexpected adapter dependency manifest")
        text = text.replace(before, after)
    return text.encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Clean pinned Kata checkout")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    component = root / "components" / "confidential-storage-kata"
    lock = json.loads((component / "agent-integration-lock.json").read_text())
    patch = component / lock["patch"]["file"]
    if digest(patch.read_bytes()) != lock["patch"]["sha256"]:
        parser.error("Agent patch digest differs from lock")
    if git(args.source, "rev-parse", "HEAD") != lock["sourceCommit"]:
        parser.error("Source HEAD is not the pinned experimental Kata baseline")
    if git(args.source, "status", "--porcelain", "--untracked-files=all"):
        parser.error("Source checkout must be clean; use a new checkout")
    for name in ["confidential-storage-guest", "confidential-storage-kata-adapter"]:
        if (args.source / "src/libs" / name).exists():
            parser.error("Overlay destination already exists")
    copies = []
    for item in lock["overlay"]:
        data = (root / item["source"]).read_bytes()
        if digest(data) != item["sourceSha256"]:
            parser.error(f"Overlay source changed: {item['source']}")
        output = overlay_content(item["source"], data)
        if digest(output) != item["outputSha256"]:
            parser.error(f"Overlay transformation changed: {item['source']}")
        copies.append((args.source / item["destination"], output))
    git(args.source, "apply", "--check", str(patch))
    if args.apply:
        git(args.source, "apply", str(patch))
        for path, data in copies:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        print("Prepared agent ownership experiment; activation remains unavailable.")
    else:
        print("Clean source, cumulative patch and all overlay hashes verified.")


if __name__ == "__main__":
    main()
