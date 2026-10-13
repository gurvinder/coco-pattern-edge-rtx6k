#!/usr/bin/env python3
"""Exercise an isolated population, optionally checking restart/no-repopulation.

This checks ciphertext distribution only, never attestation or confidential use.
The source/PVC must already be created from the example Helm template.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="confidential-storage-test")
    parser.add_argument("--claim", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-digest-failure", action="store_true")
    parser.add_argument("--restart-controller", action="store_true")
    args = parser.parse_args()
    if args.namespace != "confidential-storage-test" or not args.claim.startswith(
        "qwen-luks-"
    ):
        raise ValueError(
            "test harness only operates on isolated qwen-luks-* test claims"
        )
    if args.output.exists() or args.expect_digest_failure and args.restart_controller:
        raise ValueError("use a fresh output path and one test mode")
    expected = json.loads(args.source.read_text())

    def oc(*command, optional=False):
        result = subprocess.run(
            ["oc", "-n", args.namespace, *command],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if result.returncode and not (optional and "NotFound" in result.stderr):
            raise RuntimeError(result.stderr)
        return result.stdout if result.returncode == 0 else None

    def get(kind, name, optional=False):
        raw = oc("get", kind, name, "-o", "json", optional=optional)
        return json.loads(raw) if raw else None

    source = get("encryptedmodelsource", args.claim)
    if source["spec"] != expected:
        raise ValueError("source does not match exact publisher output")
    initial = get("pvc", args.claim)
    uid = initial["metadata"]["uid"]
    prime_name, importer_name = "prime-" + uid, "model-import-" + uid
    evidence = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "namespace": args.namespace,
        "claim": args.claim,
        "claimUid": uid,
        "source": expected,
        "observations": [],
        "confidentialGuestTest": False,
    }
    deadline, last = time.monotonic() + 600, None
    while time.monotonic() < deadline:
        claim = get("pvc", args.claim)
        if claim["metadata"]["uid"] != uid:
            raise RuntimeError("target claim was replaced")
        pod, prime = get("pod", importer_name, True), get("pvc", prime_name, True)
        state = {
            "target": claim.get("status", {}).get("phase"),
            "prime": prime.get("status", {}).get("phase") if prime else None,
            "importer": pod.get("status", {}).get("phase") if pod else None,
        }
        if state != last:
            evidence["observations"].append(state)
            print(json.dumps(state), flush=True)
            last = state
        if pod:
            evidence["importerUid"] = pod["metadata"]["uid"]
            evidence["importerSecurityContext"] = pod["spec"]["containers"][0][
                "securityContext"
            ]
            evidence["importerServiceAccount"] = pod["spec"]["serviceAccountName"]
            if state["importer"] in ("Succeeded", "Failed"):
                evidence["importerLog"] = oc("logs", importer_name)
        if state["target"] == "Bound":
            if args.expect_digest_failure:
                raise RuntimeError("invalid digest was handed to the workload")
            if not pod and not prime:
                break
        if state["importer"] == "Failed":
            if (
                not args.expect_digest_failure
                or "digest mismatch" not in evidence["importerLog"]
            ):
                raise RuntimeError(
                    "unexpected importer failure: " + evidence["importerLog"]
                )
            evidence["result"] = "wrong-digest-refused-no-handoff"
            break
        time.sleep(1)
    else:
        raise RuntimeError("population did not reach its expected terminal state")
    if not args.expect_digest_failure:
        pv = get("pv", claim["spec"]["volumeName"])
        ref = pv["spec"]["claimRef"]
        if (ref["namespace"], ref["name"], ref["uid"]) != (
            args.namespace,
            args.claim,
            uid,
        ):
            raise RuntimeError("PV is not bound to the exact target identity")
        evidence["volume"] = pv["metadata"]["name"]
        evidence["result"] = "ciphertext-populated-prime-and-importer-removed"
        if args.restart_controller:
            deployment = get("deployment", "encrypted-model-populator")
            if deployment["spec"]["selector"]["matchLabels"] != {
                "app": "encrypted-model-populator"
            }:
                raise RuntimeError("unexpected controller deployment")
            oc("rollout", "restart", "deployment/encrypted-model-populator")
            oc(
                "rollout",
                "status",
                "deployment/encrypted-model-populator",
                "--timeout=45s",
            )
            # Observe longer than the library's informer resync interval.
            for _ in range(35):
                again = get("pvc", args.claim)
                if (
                    again["metadata"]["uid"] != uid
                    or again["status"]["phase"] != "Bound"
                    or again["spec"]["volumeName"] != evidence["volume"]
                    or get("pod", importer_name, True)
                    or get("pvc", prime_name, True)
                ):
                    raise RuntimeError(
                        "controller restart repopulated or changed the claim"
                    )
                time.sleep(1)
            evidence["restartWithoutRepopulation"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("POPULATION_TEST_PASS " + evidence["result"], flush=True)


if __name__ == "__main__":
    main()
