#!/usr/bin/env python3
"""Trace the second VPN chatbot through KServe and test a fresh inference request.

Read-only Kubernetes inspection plus a synthetic chat request. Never records the
chat CSRF token, credentials, attestation tokens, model keys or arbitrary prompts.
Use alongside independent live-QEMU measurement and signed Trustee validation.
"""
import argparse
import datetime
import gzip
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--url", default="http://10.14.202.14:30081")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stock-sidecar", action="store_true")
    args = parser.parse_args()
    namespace = "confidential-storage-test"
    oc = ["oc", "--kubeconfig", str(args.kubeconfig), "--request-timeout=30s"]

    def get(*parts):
        return json.loads(
            subprocess.check_output(oc + ["-n", namespace, "get", *parts, "-o", "json"])
        )

    def ready(selector):
        pods = [
            p
            for p in get("pods", "-l", selector)["items"]
            if not p["metadata"].get("deletionTimestamp")
        ]
        assert len(pods) == 1
        p = pods[0]
        assert all(c["ready"] for c in p["status"]["containerStatuses"])
        return p

    def endpoint(service, pod):
        slices = get("endpointslices", "-l", "kubernetes.io/service-name=" + service)[
            "items"
        ]
        endpoints = [
            e
            for s in slices
            for e in s.get("endpoints", [])
            if e.get("conditions", {}).get("ready")
        ]
        assert len(endpoints) == 1
        assert endpoints[0]["targetRef"]["uid"] == pod["metadata"]["uid"]
        assert pod["status"]["podIP"] in endpoints[0]["addresses"]

    def counter():
        path = (
            "/api/v1/namespaces/"
            + namespace
            + "/services/qwen-storage-predictor:80/proxy/metrics"
        )
        raw = subprocess.check_output(oc + ["get", "--raw", path]).decode()
        values = [
            float(s.rsplit(" ", 1)[1])
            for s in raw.splitlines()
            if s.startswith("vllm:request_success_total{")
        ]
        assert values
        return sum(values)

    model = ready("app=isvc.qwen-storage-predictor")
    chat = ready("app=qwen-chat")
    endpoint("qwen-chat", chat)
    endpoint("qwen-storage-predictor", model)
    isvc = get("inferenceservice", "qwen-storage")
    assert any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in isvc["status"]["conditions"]
    )
    containers = model["spec"]["containers"]
    application = next(c for c in containers if c["name"] == "kserve-container")
    if args.stock_sidecar:
        assert model["spec"]["runtimeClassName"] == "kata-cc-nvidia-gpu"
        assert len(containers) == 2 and model["spec"]["shareProcessNamespace"]
        helper = next(c for c in containers if c["name"] == "storage-sidecar")
        assert helper["securityContext"]["privileged"]
        assert helper["securityContext"]["runAsUser"] == 0
        assert len(helper["volumeDevices"]) == 2
        assert not application.get("volumeDevices")
        mounts = {m["mountPath"] for m in application["volumeMounts"]}
        assert {"/models/qwen", "/data"} <= mounts
    else:
        assert (
            model["spec"]["runtimeClassName"] == "kata-snp-nvidia-gpu-storage-candidate"
        )
        assert len(containers) == 1
    sc = application["securityContext"]
    assert sc["runAsUser"] == sc["runAsGroup"] == 1001
    assert sc["capabilities"]["drop"] == ["ALL"] and not sc["allowPrivilegeEscalation"]
    assert sc["readOnlyRootFilesystem"] and not sc.get("privileged", False)
    assert not model["spec"]["automountServiceAccountToken"]
    env = {e["name"]: e.get("value") for e in chat["spec"]["containers"][0]["env"]}
    assert (
        env["QWEN_BACKEND"]
        == "http://qwen-storage-predictor.confidential-storage-test.svc:80"
    )
    assert env["QWEN_MODEL"] == "qwen3-0.6b"
    service = get("service", "qwen-chat")
    assert (
        service["spec"]["type"] == "NodePort"
        and service["spec"]["ports"][0]["nodePort"] == 30081
    )
    source = get("configmap", "qwen-chat")["data"]["qwen-chat.py"]
    expected = (
        Path(__file__).resolve().parents[3]
        / "charts/coco-supported/qwen-chat/files/qwen-chat.py"
    )
    assert source == expected.read_text()
    base = args.url.rstrip("/")
    page = urllib.request.urlopen(base, timeout=20).read().decode()
    token = re.search("const token='([^']+)'", page)[1]
    marker = "STORAGE-GPU-" + secrets.token_hex(6)
    prompt = {
        "messages": [
            {
                "role": "user",
                "content": "Reply with exactly this text and nothing else: " + marker,
            }
        ]
    }
    before = counter()
    request = urllib.request.Request(
        base + "/api/chat",
        data=json.dumps(prompt).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Chat-Token": token,
            "Origin": base,
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        assert response.status == 200
        result = json.load(response)
    after = counter()
    assert marker in result["message"] and after >= before + 1
    initdata = gzip.decompress(
        base64.b64decode(
            model["metadata"]["annotations"][
                "io.katacontainers.config.hypervisor.cc_init_data"
            ]
        )
    )
    record = {
        "checkedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "url": base,
        "backend": env["QWEN_BACKEND"],
        "model": env["QWEN_MODEL"],
        "chatPod": chat["metadata"]["name"],
        "chatUid": chat["metadata"]["uid"],
        "modelPod": model["metadata"]["name"],
        "modelUid": model["metadata"]["uid"],
        "inferenceServiceUid": isvc["metadata"]["uid"],
        "runtimeClass": model["spec"]["runtimeClassName"],
        "scc": model["metadata"]["annotations"]["openshift.io/scc"],
        "initDataSha256": hashlib.sha256(initdata).hexdigest(),
        "chatSourceSha256": hashlib.sha256(source.encode()).hexdigest(),
        "applicationSecurityContext": sc,
        "storageIntegration": (
            "stock-kata-sidecar" if args.stock_sidecar else "agent-integrated"
        ),
        "applicationImage": application["image"],
        "syntheticRequest": prompt,
        "reply": result,
        "vllmSuccessBefore": before,
        "vllmSuccessAfter": after,
        "note": (
            "Endpoint/UID trace and live inference; use with separate "
            "CPU/GPU attestation and QEMU evidence."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(
        (
            "PASS: VPN NodePort -> separate chatbot -> exact KServe pod; "
            "fresh inference and counter increase"
        )
    )


if __name__ == "__main__":
    main()
