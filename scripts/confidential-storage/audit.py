#!/usr/bin/env python3
"""Read selected nonsecret cluster fields; never fetch Secret resources."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def get(*args):
    result = subprocess.run(
        ["oc", "--request-timeout=20s", "get", *args, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"oc get {' '.join(args)} failed: {result.stderr[:400]}")
    return json.loads(result.stdout)


def fingerprint(spec):
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def pod_summary(pod):
    spec = pod["spec"]
    status = pod.get("status", {})
    return {
        "name": pod["metadata"]["name"],
        "uid": pod["metadata"]["uid"],
        "runtime": spec.get("runtimeClassName"),
        "phase": status.get("phase"),
        "spec_sha256": fingerprint(spec),
        "containers": [
            {
                "name": c["name"],
                "image": c["image"],
                "resources": c.get("resources", {}),
            }
            for c in spec["containers"]
        ],
        "status": [
            {
                "name": c["name"],
                "ready": c["ready"],
                "restarts": c["restartCount"],
                "image_id": c.get("imageID"),
            }
            for c in status.get("containerStatuses", [])
        ],
    }


def collect(namespace):
    result = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "evidence": "live-read-only",
    }
    result["openshift_version"] = get("clusterversion", "version")["status"]["desired"][
        "version"
    ]
    result["nodes"] = [
        {
            "name": n["metadata"]["name"],
            "allocatable": n["status"]["allocatable"],
            "node_info": n["status"]["nodeInfo"],
        }
        for n in get("nodes")["items"]
    ]
    result["runtimes"] = [
        {
            "name": r["metadata"]["name"],
            "handler": r["handler"],
            "overhead": r.get("overhead"),
            "scheduling": r.get("scheduling"),
        }
        for r in get("runtimeclasses")["items"]
    ]
    result["storage_classes"] = [
        {
            "name": s["metadata"]["name"],
            "provisioner": s["provisioner"],
            "binding": s.get("volumeBindingMode"),
        }
        for s in get("storageclasses")["items"]
    ]
    result["drivers"] = [
        {"name": s["metadata"]["name"], "spec": s["spec"]}
        for s in get("csidrivers")["items"]
    ]
    result["production_pods"] = [
        pod_summary(p) for p in get("pods", "-n", "gpu-workload")["items"]
    ]
    result["production_resources"] = []
    for kind in ("inferenceservices.serving.kserve.io", "deployments", "services"):
        for resource in get(kind, "-n", "gpu-workload")["items"]:
            result["production_resources"].append(
                {
                    "kind": resource["kind"],
                    "name": resource["metadata"]["name"],
                    "uid": resource["metadata"]["uid"],
                    "spec_sha256": fingerprint(resource["spec"]),
                }
            )
    result["gitops_sources"] = [
        {
            "name": a["metadata"]["name"],
            "sources": [
                {k: s.get(k) for k in ("repoURL", "targetRevision", "path", "chart")}
                for s in a["spec"].get("sources", [a["spec"].get("source", {})])
            ],
        }
        for a in get("applications.argoproj.io", "-A")["items"]
    ]
    if namespace:
        result["test_pods"] = [
            pod_summary(p) for p in get("pods", "-n", namespace)["items"]
        ]
        result["test_pvcs"] = [
            {
                "name": p["metadata"]["name"],
                "spec": p["spec"],
                "status": p.get("status"),
            }
            for p in get("pvc", "-n", namespace)["items"]
        ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-namespace", default="")
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = collect(args.test_namespace)
    unchanged = True
    if args.compare:
        previous = json.loads(args.compare.read_text())
        # Order-independent comparison; restart count/UID/Ready are included.
        comparison = {}
        for key in (
            "production_pods",
            "production_resources",
            "runtimes",
            "gitops_sources",
        ):
            before = sorted(previous[key], key=lambda x: (x.get("kind", ""), x["name"]))
            after = sorted(result[key], key=lambda x: (x.get("kind", ""), x["name"]))
            comparison[key] = before == after
        result["unchanged_since_baseline"] = comparison
        unchanged = all(comparison.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "production_unchanged": unchanged if args.compare else None,
            }
        )
    )
    return 0 if unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
