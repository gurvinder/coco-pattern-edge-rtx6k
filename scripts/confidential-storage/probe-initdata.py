#!/usr/bin/env python3
"""Generate public-image, no-KBS diagnostic InitData. Not a workload policy."""

import argparse
import base64
import gzip
import hashlib
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="confidential-storage-test")
    parser.add_argument("--name", default="storage-audit-no-secrets")
    args = parser.parse_args()
    allowed = """AddARPNeighbors AddSwap CloseStdin CopyFile CreateContainer
CreateSandbox DestroySandbox GetMetrics GetOOMEvent GuestDetails ListInterfaces
ListRoutes MemHotplugByProbe OnlineCPUMem PauseContainer PullImage ReadStream
RemoveContainer RemoveStaleVirtiofsShareMounts ReseedRandomDev ResumeContainer
SetGuestDateTime SignalProcess StartContainer StartTracing StatsContainer
StopTracing TtyWinResize UpdateContainer UpdateEphemeralMounts UpdateInterface
UpdateRoutes WaitProcess""".split()
    policy = "package agent_policy\n\n"
    policy += "\n".join(f"default {name}Request := true" for name in allowed)
    policy += (
        "\ndefault ExecProcessRequest := false\n"
        "default WriteStreamRequest := false\ndefault SetPolicyRequest := false\n"
    )
    # No resource policy URI, registry credentials or Trustee address. The public
    # test image is pinned in the chart; this guest must never hold real secrets.
    raw = (
        'algorithm = "sha256"\nversion = "0.1.0"\n\n[data]\n'
        '"cdh.toml" = \'\'\'\nsocket = "unix:///run/confidential-containers/cdh.sock"\n'
        'credentials = []\n[kbc]\nname = "cc_kbc"\n'
        "url = \"http://127.0.0.1:1\"\n[image]\n'''\n"
        "\"aa.toml\" = '''\n[token_configs]\n'''\n"
        "\"policy.rego\" = '''\n" + policy + "'''\n"
    ).encode()
    result = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": args.name,
            "namespace": args.namespace,
            "labels": {"app.kubernetes.io/part-of": "confidential-storage-test"},
            "annotations": {
                "storage-test/initdata-sha256": hashlib.sha256(raw).hexdigest()
            },
        },
        "data": {"INITDATA": base64.b64encode(gzip.compress(raw, mtime=0)).decode()},
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
