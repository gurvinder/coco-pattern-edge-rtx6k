#!/usr/bin/env python3
"""Prepare an isolated GPU candidate offline; never activate or provision keys.

Requires public AA/CDH settings and live PVC snapshots. The NEW data UID is an
explicit administrator assertion, not inferred from Bound/Ready status. Reusing
an output directory is forbidden. Do not rerun this to recover existing RW state.
Publisher metadata comes from the reviewed, previously verified repository E2E.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sys
import tomllib
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [
    str(ROOT / "components/confidential-volume-contract"),
    str(ROOT / "components/confidential-storage-authority"),
]
from compiler import (  # noqa: E402
    ANNOTATION,
    API,
    canonical,
    compile_contract,
    parse,
    require,
    size,
)
from authority import Authority  # noqa: E402
from publication import from_publisher  # noqa: E402
from storage_profiles import profile_digest  # noqa: E402

NAMESPACE = "confidential-storage-test"
HOST = "storage-gpu-authority.confidential-storage-test.svc"
TRUSTEE = "https://kbs.apps.dell-rtx6kbse-3.bmas-001.lab.rdu2.dc.redhat.com"
PUBLISHER = ROOT / "tests/confidential-storage/evidence/2026-10-13/model-populator"


def read(path):
    return parse(path.read_bytes())


def write(path, value):
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bundle_for(claims, workload, new_uid):
    require(str(uuid.UUID(new_uid)) == new_uid, "canonical new PVC UID required")
    require(len(claims) == 2, "exact model and data claims required")
    declarations, volumes, devices, bindings = [], [], [], []
    for index, (claim, access, destination) in enumerate(
        zip(claims, ["ReadOnly", "ReadWrite"], ["/models/qwen", "/data"])
    ):
        ro = access == "ReadOnly"
        name = "model" if ro else "data"
        require(claim.get("status", {}).get("phase") == "Bound", "bound claim required")
        require(
            claim["spec"].get("storageClassName") == "lvms-vg1",
            "unexpected storage class",
        )
        require(
            size(claim["spec"]["resources"]["requests"]["storage"])
            == (2 if ro else 1) * 1024**3,
            "unsupported candidate capacity",
        )
        require(ro or claim["metadata"]["uid"] == new_uid, "new data UID mismatch")
        spec = {
            "claimRef": {k: claim["metadata"][k] for k in ("name", "uid")},
            "access": access,
            "profile": "luks-verity-v1" if ro else "luks-integrity-v1",
            "initialization": (
                {"policy": "Never"}
                if ro
                else {
                    "policy": "OnceIfApproved",
                    "filesystem": "ext4",
                    "owner": {"uid": 1001, "gid": 1001},
                }
            ),
            "keyResource": f"kbs:///default/model-keys/qwen-gpu-{name}-v1",
        }
        if ro:
            spec["content"] = {"approvedManifestRef": "qwen-luks-e2e-v1"}
        declarations.append(
            {
                "apiVersion": API,
                "kind": "ConfidentialVolume",
                "metadata": {"namespace": NAMESPACE, "name": "qwen-gpu-" + name},
                "spec": spec,
            }
        )
        volumes.append(
            {
                "name": name,
                "persistentVolumeClaim": {
                    "claimName": claim["metadata"]["name"],
                    "readOnly": ro,
                },
            }
        )
        devices.append({"name": name, "devicePath": f"/dev/coco-volume-{index}"})
        bindings.append(
            {
                "volume": name,
                "confidentialVolume": "qwen-gpu-" + name,
                "container": "kserve-container",
                "mountPath": destination,
            }
        )
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "qwen-gpu-storage-candidate",
            "namespace": NAMESPACE,
            "labels": {
                "app.kubernetes.io/name": "qwen-gpu-storage-candidate",
                "coco.io/skip-initdata": "true",
            },
            "annotations": {ANNOTATION: canonical(bindings).decode()},
        },
        "spec": {
            "runtimeClassName": "kata-snp-nvidia-gpu-storage-candidate",
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "hostname": "qwen-gpu-storage-candidate",
            "containers": [
                {
                    "name": "kserve-container",
                    "image": workload["image"],
                    "command": workload["args"],
                    "workingDir": "/",
                    "env": workload["podEnvironment"],
                    "ports": [{"name": "http", "containerPort": 8000}],
                    "securityContext": {
                        "runAsUser": 1001,
                        "runAsGroup": 1001,
                        "runAsNonRoot": True,
                        "privileged": False,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {
                            "cpu": "4",
                            "memory": "16Gi",
                            "nvidia.com/pgpu": 1,
                        },
                        "limits": {"cpu": "8", "memory": "48Gi", "nvidia.com/pgpu": 1},
                    },
                    "volumeDevices": devices,
                }
            ],
            "volumes": volumes,
        },
    }
    result = {
        "namespace": NAMESPACE,
        "workload": pod,
        "declarations": declarations,
        "claims": claims,
    }
    compile_contract(result)  # UID/namespace/access/duplicate/collision validation.
    return result


def public_configs(inputs):
    aa_raw = (inputs / "aa.toml").read_text()
    aa = tomllib.loads(aa_raw)
    cdh = tomllib.loads((inputs / "cdh.toml").read_text())
    require(
        set(aa) == {"token_configs"} and set(aa["token_configs"]) == {"kbs", "coco_as"},
        "unexpected AA configuration",
    )
    for config in aa["token_configs"].values():
        require(
            set(config) <= {"url", "cert"} and config.get("url") == TRUSTEE,
            "unexpected AA endpoint or credentials",
        )
    require(
        cdh.get("credentials") == []
        and cdh["kbc"]["url"] == TRUSTEE
        and cdh["kbc"]["name"] == "cc_kbc",
        "unexpected CDH configuration",
    )
    require(set(cdh["kbc"]) == {"url", "name", "kbs_cert"}, "unexpected KBC fields")
    ca = (inputs / "trustee-ca.pem").read_text()
    require(
        cdh["kbc"]["kbs_cert"] == ca and aa["token_configs"]["kbs"].get("cert") == ca,
        "inconsistent Trustee TLS pins",
    )
    x509.load_pem_x509_certificate(ca.encode())
    # No authenticated image pull or permissive image policy resource is needed.
    # Exact image digest and OCI process are enforced by the measured agent policy.
    cdh_raw = 'socket = "unix:///run/confidential-containers/cdh.sock"\ncredentials = []\n[kbc]\n'
    cdh_raw += "".join(f"{k} = {json.dumps(v)}\n" for k, v in cdh["kbc"].items())
    return aa_raw, cdh_raw + "[image]\n", ca


def prepare(args):
    ipaddress.ip_address(args.authority_address)
    aa, cdh, ca = public_configs(args.inputs)
    descriptor, source = read(PUBLISHER / "guest-manifest.unsigned.json"), read(
        PUBLISHER / "source.json"
    )
    image = read(HERE.parent / "native-guest/serving-image.json")
    program = (
        (HERE / "workload.py.in")
        .read_text()
        .replace("__INVENTORY_JSON__", repr(descriptor["files"]))
    )
    compile(program, "<candidate-startup>", "exec")
    overrides = {
        "PATH": dict(item.split("=", 1) for item in image["environment"])["PATH"],
        "INFERENCE_SERVICE_NAME": "qwen-storage",
        "PYTHONUNBUFFERED": "1",
        "NVIDIA_VISIBLE_DEVICES": "void",
        "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
        "CUDA_VISIBLE_DEVICES": "0",
        "TMPDIR": "/data/tmp",
        "HOME": "/data/home",
        "USER": "qwen",
        "LOGNAME": "qwen",
        "TORCHINDUCTOR_CACHE_DIR": "/data/cache/torchinductor",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HOME": "/data/hf",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "VLLM_CACHE_ROOT": "/data/vllm",
        "VLLM_NO_USAGE_STATS": "1",
        "DO_NOT_TRACK": "1",
        "OMP_NUM_THREADS": "1",
        "TRITON_CACHE_DIR": "/data/triton",
        "XDG_CACHE_HOME": "/data/cache",
        "TOKENIZERS_PARALLELISM": "false",
        "LD_LIBRARY_PATH": "/usr/lib64:/usr/local/lib/python3.12/dist-packages/torch/lib:"
        "/usr/local/lib/python3.12/dist-packages/torch_tensorrt/lib:"
        "/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
    }
    env = dict(item.split("=", 1) for item in image["environment"])
    env.update(overrides)
    env.update(HOSTNAME="qwen-gpu-storage-candidate", TERM="xterm", container="oci")
    # CRI-O injects the Kubernetes service even with enableServiceLinks=false.
    # These are exact cluster values, never an arbitrary environment prefix.
    env.update(
        {
            "NSS_SDB_USE_CACHE": "no",
            "KUBERNETES_SERVICE_HOST": "172.30.0.1",
            "KUBERNETES_SERVICE_PORT": "443",
            "KUBERNETES_SERVICE_PORT_HTTPS": "443",
            "KUBERNETES_PORT": "tcp://172.30.0.1:443",
            "KUBERNETES_PORT_443_TCP": "tcp://172.30.0.1:443",
            "KUBERNETES_PORT_443_TCP_PROTO": "tcp",
            "KUBERNETES_PORT_443_TCP_PORT": "443",
            "KUBERNETES_PORT_443_TCP_ADDR": "172.30.0.1",
        }
    )
    workload = {
        "container": "kserve-container",
        "image": image["image"],
        "args": [image["python"], "-c", program],
        "environment": sorted(
            [f"{k}={v}" for k, v in env.items()]
            + ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]
        ),
        "requiredEnvironment": sorted(f"{k}={v}" for k, v in overrides.items()),
        "podEnvironment": [{"name": k, "value": v} for k, v in overrides.items()],
    }
    claims = [
        read(args.inputs / (n + ".json"))
        for n in ("qwen-luks-model-v1", "qwen-gpu-data-v1")
    ]
    bundle = bundle_for(claims, workload, args.new_data_pvc_uid)
    contract = compile_contract(bundle)
    policy = (
        (HERE / "policy.rego.in")
        .read_text()
        .replace("__WORKLOAD_JSON__", canonical(workload).decode())
    )
    data = {
        "aa.toml": aa,
        "cdh.toml": cdh,
        "policy.rego": policy,
        "confidential-volume-contract.json": canonical(contract).decode(),
    }
    initdata = 'version = "0.1.0"\nalgorithm = "sha256"\n[data]\n'
    initdata += "".join(f"{json.dumps(k)} = {json.dumps(v)}\n" for k, v in data.items())
    require(len(initdata.encode()) <= 65536, "InitData too large")
    require(tomllib.loads(initdata)["data"] == data, "InitData serialization mismatch")
    policy_sha, init_sha = sha(policy.encode()), sha(initdata.encode())
    # All output is private by default. Publish only selected public artifacts.
    os.umask(0o077)
    args.output.mkdir(mode=0o700)  # Exclusive: never reset retained owner history.
    out = args.output
    for name, raw in [
        ("policy.rego", policy),
        ("initdata.toml", initdata),
        ("trustee-ca.pem", ca),
    ]:
        (out / name).write_text(raw)
    write(out / "bundle.json", bundle)
    write(out / "approval-request.json", contract)
    write(out / "workload.json", workload)
    cert = x509.load_pem_x509_certificate(args.as_certificate.read_bytes())
    (out / "as-signer.pem").write_bytes(
        cert.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    tls_key = ec.generate_private_key(ec.SECP256R1())
    receipt = ed25519.Ed25519PrivateKey.generate()
    for name, key in [("tls.key", tls_key), ("receipt.key", receipt)]:
        (out / name).write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.now(timezone.utc)
    tls = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(tls_key, hashes.SHA256())
    )
    (out / "tls.pem").write_bytes(tls.public_bytes(serialization.Encoding.PEM))
    authority = Authority(out / "authority.db")
    volumes, resources = [], {}
    for item in contract["volumes"]:
        ro = item["access"] == "ReadOnly"
        volume_id = "qwen-gpu-" + item["bindingId"] + "-v1"
        binding = {
            "manifest_uri": "kbs:///default/volume-records/" + volume_id,
            "container_name": item["container"],
            "destination": item["mountPath"],
            "access": "ro" if ro else "rw",
        }
        profile = profile_digest("ro-luks2-verity-v2" if ro else "rw-luks2-v2")
        record = authority.register(
            volume_id, policy_sha, profile, item["keyResource"], mount_binding=binding
        )
        volume = {
            "binding_id": item["bindingId"],
            "transport_path": item["devicePath"],
            "expected": {
                "volume_id": volume_id,
                "generation": 1,
                "policy_sha256": policy_sha,
                "profile_sha256": profile,
                "session_sha256": "",
                "operation_id": "",
                "nonce": "",
                **binding,
            },
            "manifest": [],
        }
        if ro:
            manifest = from_publisher(record, descriptor, source)
            authority.approve_published_model(volume_id, manifest)
            volume["manifest"] = list(manifest)
        else:
            approval = authority.approve_initialization(
                volume_id,
                {
                    "schema_version": 1,
                    "new_volume": True,
                    "provisioning_id": args.new_data_pvc_uid,
                    "device_bytes": item["requestedBytes"],
                    "luks_uuid": str(uuid.uuid4()),
                    "filesystem": "ext4",
                    "header_bytes": 16777216,
                    "data_offset_bytes": 16777216,
                },
            )
            volume["initialization_plan_sha256"] = approval["plan_sha256"]
        volumes.append(volume)
        resources[item["keyResource"]] = "lower-hex64"
    audience = "https://" + HOST + ":8443/v1"
    pinned = {
        "audience": audience,
        "policy_sha256": policy_sha,
        "init_data": init_sha,
        "receipt_issuer": "storage-gpu-candidate",
        "receipt_key_id": "gpu-v1",
    }
    guest = {
        "schema_version": 2,
        "purpose": "confidential-storage-guest/v2",
        "authority": {
            **pinned,
            "trustee_rcar": True,
            "address": args.authority_address + ":8443",
            "certificate_der": list(tls.public_bytes(serialization.Encoding.DER)),
            "receipt_public_key": list(receipt.public_key().public_bytes_raw()),
        },
        "kbs": {"provider": "private-cdh", "resources": resources},
        "volumes": volumes,
    }
    state_path = str(out.resolve())
    server = {
        **pinned,
        "listen": {"host": "127.0.0.1", "port": 0},
        "database": state_path + "/authority.db",
        "tls_certificate": state_path + "/tls.pem",
        "tls_key": state_path + "/tls.key",
        "as_signer": state_path + "/as-signer.pem",
        "as_issuer": "CoCo-Attestation-Service",
        "as_issuer_claim": getattr(args, "as_issuer_claim", "required"),
        "as_gpu_binding": getattr(args, "as_gpu_binding", "runtime-claims"),
        "receipt_key": state_path + "/receipt.key",
        "appraisals": [
            {
                "submod": "cpu0",
                "tee": "snp",
                "policy_id": "default",
                "configuration": getattr(args, "as_configuration", 2),
            },
            {
                "submod": "gpu0",
                "tee": "nvidia",
                "policy_id": "default",
                "configuration": getattr(args, "as_configuration", 2),
            },
        ],
        "trustee_rcar": {
            "origin": TRUSTEE,
            "ca_file": state_path + "/trustee-ca.pem",
            "init_data_file": state_path + "/initdata.toml",
            "address": None,
            "policy_selector": None,
        },
    }
    # This file is a deployment input, not auto-started. Relocate paths explicitly.
    server["listen"] = {"host": "0.0.0.0", "port": 8443}
    write(out / "server.json", server)
    write(out / "guest-public.json", guest)
    pod = bundle["workload"]
    pod["metadata"]["annotations"][
        "io.katacontainers.config.hypervisor.cc_init_data"
    ] = base64.b64encode(gzip.compress(initdata.encode(), mtime=0)).decode()
    write(out / "pod.json", pod)
    report = {
        "purpose": "prepared-gpu-candidate/v1",
        "policySha256": policy_sha,
        "initDataSha256": init_sha,
        "initDataBytes": len(initdata.encode()),
        "guestConfigSha256": sha(canonical(guest) + b"\n"),
        "claims": [d["spec"]["claimRef"] for d in bundle["declarations"]],
        "newDataPvcUid": args.new_data_pvc_uid,
        "modelSource": source,
        "authorityAddress": args.authority_address,
        "keyProvisioned": False,
        "runtimeActivated": False,
        "hardwareValidated": False,
        "earIssuerAndPolicyIds": "source-default expectations; live composite verification pending",
    }
    write(out / "preparation.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["inputs", "output", "as-certificate"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--new-data-pvc-uid", required=True)
    parser.add_argument("--authority-address", required=True)
    parser.add_argument(
        "--as-issuer-claim", choices=("required", "absent-rcar"), default="required"
    )
    parser.add_argument("--as-configuration", type=int, choices=(2, 3), default=2)
    parser.add_argument(
        "--as-gpu-binding",
        choices=("runtime-claims", "nvidia-nonce-match"),
        default="runtime-claims",
    )
    prepare(parser.parse_args())
