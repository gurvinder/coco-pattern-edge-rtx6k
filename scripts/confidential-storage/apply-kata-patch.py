#!/usr/bin/env python3
"""Verify and apply the pinned, fail-closed Kata transport experiment."""

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Clean pinned Kata checkout")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    component = root / "components" / "confidential-storage-kata"
    lock = json.loads((component / "source-lock.json").read_text())
    patch = component / "confidential-mount-v1.patch"
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    if digest != lock["patch"]["sha256"]:
        parser.error("Patch digest does not match source lock")
    if git(args.source, "rev-parse", "HEAD") != lock["source"]["commit"]:
        parser.error("Source HEAD does not match the pinned experimental baseline")
    if git(args.source, "status", "--porcelain", "--untracked-files=all"):
        parser.error("Source checkout must be clean, including untracked files")
    git(args.source, "apply", "--check", str(patch))
    if args.apply:
        git(args.source, "apply", str(patch))
        print("Applied development patch; activation remains unavailable.")
    else:
        print("Source SHA, clean state, patch SHA-256 and applicability verified.")


if __name__ == "__main__":
    main()
