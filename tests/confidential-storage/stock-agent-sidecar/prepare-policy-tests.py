#!/usr/bin/env python3
"""Produce actual-stock-request positive tests and security-boundary mutations."""
import copy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "components/confidential-storage-sidecar"))
from policy import render, settings

fixtures = {
    role: json.loads((HERE / "fixtures" / (role + ".json")).read_text())
    for role in ["pause", "helper", "application"]
}
fixtures["helper"]["sandbox_pidns"] = True
profile = {
    "namespace": "confidential-storage-test",
    "gpuCount": 0,
    **{role: settings(r) for role, r in fixtures.items()},
}
cases = [
    {"name": role, "endpoint": "CreateContainerRequest", "input": r, "allowed": True}
    for role, r in fixtures.items()
]


def mutation(name, role, path, value):
    request = copy.deepcopy(fixtures[role])
    parent = request
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value
    cases.append(
        {
            "name": name,
            "endpoint": "CreateContainerRequest",
            "input": request,
            "allowed": False,
        }
    )


mutation("wrong-image", "helper", ["storages", 0, "source"], "attacker:latest")
mutation("helper-shell", "helper", ["OCI", "Process", "Args"], ["/bin/sh"])
mutation("application-shell", "application", ["OCI", "Process", "Args"], ["/bin/sh"])
mutation("application-root", "application", ["OCI", "Process", "User", "UID"], 0)
mutation(
    "application-root-group",
    "application",
    ["OCI", "Process", "User", "AdditionalGids"],
    [0],
)
mutation(
    "application-sys-admin",
    "application",
    ["OCI", "Process", "Capabilities", "Effective"],
    ["CAP_SYS_ADMIN"],
)
mutation(
    "application-escalation",
    "application",
    ["OCI", "Process", "NoNewPrivileges"],
    False,
)
mutation("writable-application-root", "application", ["OCI", "Root", "Readonly"], False)
mutation(
    "preload-code",
    "helper",
    ["OCI", "Process", "Env"],
    fixtures["helper"]["OCI"]["Process"]["Env"] + ["LD_PRELOAD=/configuration/evil.so"],
)
mutation(
    "replace-supervisor",
    "helper",
    ["OCI", "Mounts", 0, "destination"],
    "/opt/storage-sidecar/runtime.py",
)
mutation(
    "outside-shared-root",
    "helper",
    ["OCI", "Mounts", 7, "source"],
    "/run/confidential-containers",
)
mutation(
    "guest-core-command",
    "application",
    ["OCI", "Linux", "Sysctl"],
    {"kernel.core_pattern": "|/bin/sh"},
)
mutation(
    "guest-snp-device",
    "application",
    ["OCI", "Linux", "Devices"],
    [{"Path": "/dev/sev-guest", "Type": "c"}],
)
mutation(
    "disguised-snp-device",
    "application",
    ["OCI", "Linux", "Devices"],
    [{"Path": "/dev/vfio/evil", "Type": "c", "Major": 10, "Minor": 122}],
)
mutation("string-user-override", "application", ["string_user"], "root")
mutation(
    "application-raw-disk",
    "application",
    ["devices"],
    [{"type_": "scsi", "container_path": "/dev/coco-volume-0"}],
)
mutation(
    "pause-extra-device",
    "pause",
    ["OCI", "Linux", "Devices"],
    [{"Path": "/dev/sev-guest", "Type": "c"}],
)
mutation("pause-shell", "pause", ["OCI", "Process", "Args"], ["/bin/sh"])
for endpoint in [
    "ExecProcessRequest",
    "SetPolicyRequest",
    "WriteStreamRequest",
    "AddSwapRequest",
    "UpdateContainerRequest",
]:
    cases.append(
        {"name": endpoint, "endpoint": endpoint, "input": {}, "allowed": False}
    )
prefix = (
    "/run/kata-containers/shared/containers/"
    + "a" * 64
    + "-"
    + "b" * 16
    + "-configuration"
)
for name, path, kind, target, allowed in [
    ("public-config-file", prefix + "/initdata.toml", "Regular", None, True),
    (
        "config-map-link",
        prefix + "/initdata.toml",
        "Symlink",
        "..data/initdata.toml",
        True,
    ),
    ("absolute-link", prefix + "/initdata.toml", "Symlink", "/usr/bin/python3", False),
    ("parent-link", prefix + "/initdata.toml", "Symlink", "../../rootfs/file", False),
    ("top-level-link", prefix, "Symlink", "rootfs", False),
    (
        "copy-rootfs",
        "/run/kata-containers/" + "a" * 64 + "/rootfs/usr/bin/python3",
        "Regular",
        None,
        False,
    ),
    ("copy-traversal", prefix + "/../../../etc/file", "Regular", None, False),
]:
    cases.append(
        {
            "name": name,
            "endpoint": "CopyFileRequest",
            "input": {
                "path": path,
                "file_type": kind,
                "symlink_target": target,
                "file_size": 1024,
            },
            "allowed": allowed,
        }
    )
output = Path(sys.argv[1])
output.mkdir(exist_ok=True)
(output / "policy.rego").write_text(render(profile))
(output / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
(output / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
print(f"{len(cases)} policy cases prepared")
