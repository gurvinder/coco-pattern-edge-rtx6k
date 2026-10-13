#!/usr/bin/env python3
"""Fetch the already reviewed Qwen revision for the trusted LUKS publisher."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import urllib.request


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    # Reuse immutable model pins, without importing the old encryption/key code.
    source = (
        Path(__file__).resolve().parents[2]
        / "charts/coco-supported/encrypted-inference/files/producer.py"
    )
    pins = {}
    for node in ast.parse(source.read_text()).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name = node.targets[0].id
            if name in ("MODEL", "REVISION", "FILES"):
                pins[name] = ast.literal_eval(node.value)
    args.model.mkdir(mode=0o700)
    records = []
    for name, (size, expected) in pins["FILES"].items():
        algorithm, wanted = expected.split(":", 1)
        check = hashlib.sha256() if algorithm == "sha256" else hashlib.sha1()
        if algorithm == "git":
            check.update(f"blob {size}\0".encode())
        sha, total = hashlib.sha256(), 0
        url = (
            f'https://huggingface.co/{pins["MODEL"]}/resolve/{pins["REVISION"]}/{name}'
        )
        with urllib.request.urlopen(url, timeout=180) as response, (
            args.model / name
        ).open("xb") as output:
            for chunk in iter(lambda: response.read(8 << 20), b""):
                total += len(chunk)
                if total > size:
                    raise ValueError("model exceeded pinned size")
                check.update(chunk)
                sha.update(chunk)
                output.write(chunk)
        if total != size or check.hexdigest() != wanted:
            raise ValueError("model source differs from reviewed revision")
        (args.model / name).chmod(0o444)
        records.append({"path": name, "bytes": size, "sha256": sha.hexdigest()})
        print(f"MODEL_SOURCE_VERIFIED {name} bytes={size}", flush=True)
    (args.model / "publisher-source.json").write_text(
        json.dumps(
            {"model": pins["MODEL"], "revision": pins["REVISION"], "files": records},
            sort_keys=True,
        )
        + "\n"
    )
    (args.model / "publisher-source.json").chmod(0o444)


if __name__ == "__main__":
    main()
