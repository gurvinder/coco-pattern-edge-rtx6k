#!/usr/bin/env python3
"""Prepare a measured environment revision without creating keys or authority state.

Input is the previous preparation plus the current public guest/server configs.
Output requires explicit authority policy update and exact KBS launch bindings.
"""
import argparse
import base64
import copy
import gzip
import json
import os
from pathlib import Path
import tomllib
from prepare import canonical, compile_contract, read, require, sha, write, HERE


def revise(previous, guest_path, server_path, output, additions):
    workload = read(previous / "workload.json")
    bundle = read(previous / "bundle.json")
    guest, server = read(guest_path), read(server_path)
    old_policy = sha((previous / "policy.rego").read_bytes())
    require(
        guest["authority"]["policy_sha256"] == server["policy_sha256"] == old_policy,
        "Prior measured policy mismatch",
    )
    for field in ("environment", "requiredEnvironment"):
        # The allowlist can contain several explicitly approved values for one
        # name (e.g. image PATH and CRI PATH). Preserve unrelated alternatives.
        workload[field] = sorted(
            [item for item in workload[field] if item.split("=", 1)[0] not in additions]
            + [f"{k}={v}" for k, v in additions.items()]
        )
    # CRI-O can append its default PATH even alongside the explicit image PATH.
    # Both exact values were observed; arbitrary PATH prefixes remain forbidden.
    workload["environment"] = sorted(
        set(workload["environment"])
        | {"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    )
    env = {item["name"]: item["value"] for item in workload["podEnvironment"]}
    env.update(additions)
    workload["podEnvironment"] = [{"name": k, "value": v} for k, v in env.items()]
    bundle["workload"]["spec"]["containers"][0]["env"] = workload["podEnvironment"]
    contract = compile_contract(bundle)
    policy = (
        (HERE / "policy.rego.in")
        .read_text()
        .replace("__WORKLOAD_JSON__", canonical(workload).decode())
    )
    data = tomllib.loads((previous / "initdata.toml").read_text())["data"]
    data.update(
        {
            "policy.rego": policy,
            "confidential-volume-contract.json": canonical(contract).decode(),
        }
    )
    initdata = 'version = "0.1.0"\nalgorithm = "sha256"\n[data]\n'
    initdata += "".join(f"{json.dumps(k)} = {json.dumps(v)}\n" for k, v in data.items())
    require(len(initdata.encode()) <= 65536, "InitData too large")
    new_policy, init_sha = sha(policy.encode()), sha(initdata.encode())
    require(new_policy != old_policy, "No policy change")
    for config in (guest["authority"], server):
        config.update(policy_sha256=new_policy, init_data=init_sha)
    for volume in guest["volumes"]:
        require(
            volume["expected"]["policy_sha256"] == old_policy, "Volume policy mismatch"
        )
        volume["expected"]["policy_sha256"] = new_policy
    pod = copy.deepcopy(bundle["workload"])
    pod["metadata"]["annotations"][
        "io.katacontainers.config.hypervisor.cc_init_data"
    ] = base64.b64encode(gzip.compress(initdata.encode(), mtime=0)).decode()
    os.umask(0o077)
    output.mkdir(mode=0o700)
    for name, raw in [("policy.rego", policy), ("initdata.toml", initdata)]:
        (output / name).write_text(raw)
    for name, obj in [
        ("bundle", bundle),
        ("approval-request", contract),
        ("workload", workload),
        ("guest-public", guest),
        ("server", server),
        ("pod", pod),
    ]:
        write(output / (name + ".json"), obj)
    report = {
        "oldPolicySha256": old_policy,
        "policySha256": new_policy,
        "initDataSha256": init_sha,
        "environmentChanges": additions,
        "keysOrDatabaseCreated": False,
    }
    write(output / "revision.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("previous", "guest", "server", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--env", action="append", required=True)
    args = parser.parse_args()
    revise(
        args.previous,
        args.guest,
        args.server,
        args.output,
        dict(x.split("=", 1) for x in args.env),
    )
