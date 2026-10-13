#!/usr/bin/env python3
"""Exercise isolated strict EAR appraisal with a previously captured real quote.

No fresh challenge/proof-of-possession is exercised, no authority DB is touched,
and no KBS is contacted. Never approve this diagnostic measurement for keys.
Administrative policy writes target only the isolated loopback verifier.
"""
import argparse
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.request

from cryptography.hazmat.primitives import serialization

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "components/confidential-storage-authority"))
from attestation import Appraisal, EarVerifier  # noqa: E402
from authority import Denied  # noqa: E402

spec = importlib.util.spec_from_file_location("hardware", HERE / "verify-hardware.py")
hardware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardware)
POLICY_ID = "storage-diagnostic-v4"


def install(policy_id, policy):
    request = urllib.request.Request(
        "http://127.0.0.1:8089/policy",
        data=json.dumps(
            {
                "policy_id": policy_id + "_cpu",
                "policy": base64.urlsafe_b64encode(policy.encode())
                .decode()
                .rstrip("="),
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("isolated policy registration failed")


def assess(request, policy_id):
    request = copy.deepcopy(request)
    request["policy_ids"] = [policy_id]
    status, token = hardware.post(request)
    if status != 200:
        raise RuntimeError(f"attestation failed: HTTP {status}: {token[:300]}")
    claims = json.loads(hardware.decode(token.split(".")[1]))
    return token, claims["submods"]["cpu0"]


def verify(request, signer, policy, reference):
    if reference["approvedForStorageKeys"] is not False:
        raise ValueError("diagnostic references required")
    measurement = reference["launchMeasurement"]
    if policy.count('"' + measurement + '"') != 1:
        raise ValueError("policy differs from independently calculated reference")
    item = request["verification_requests"][0]
    quote = json.loads(hardware.decode(item["evidence"]))["attestation_report"]
    if bytes(quote["measurement"]).hex() != measurement:
        raise ValueError("quote measurement differs from pinned build inputs")
    runtime = item["runtime_data"]["structured"]["storage-binding"]
    initdata = bytes(quote["host_data"]).hex()
    policy_hash = hashlib.sha256(policy.encode()).hexdigest()

    def verifier(policy_id=POLICY_ID, digest=initdata):
        return EarVerifier(
            signer,
            "storage-probe-as",
            "https://storage-test.invalid/v1",
            digest,
            policy_hash,
            [Appraisal("cpu0", "snp", policy_id)],
        )

    def accept(token, check):
        check.verify(
            token, runtime["nonce"], runtime["request_sha256"], int(time.time())
        )

    install(POLICY_ID, policy)
    token, appraisal = assess(request, POLICY_ID)
    accept(token, verifier())
    checks = {"calculated_launch_matches": True, "strict_real_ear_accepted": True}
    negative_policies = {
        "wrong_launch_reference": (measurement, "0" * 96),
        "wrong_tcb_reference": (
            "reported_tcb_microcode == 117",
            "reported_tcb_microcode == 0",
        ),
        "wrong_guest_policy": (
            "policy_debug_allowed == false",
            "policy_debug_allowed == true",
        ),
        "wrong_initdata_reference": (initdata, "0" * 64),
    }
    for name, (old, new) in negative_policies.items():
        if policy.count(old) != 1:
            raise ValueError("ambiguous negative policy mutation")
        negative_id = POLICY_ID + "-" + name.replace("_", "-")
        install(negative_id, policy.replace(old, new))
        denied_token, result = assess(request, negative_id)
        if result["ear.status"] == "affirming":
            raise RuntimeError(f"{name} unexpectedly affirmed")
        try:
            accept(denied_token, verifier(negative_id))
        except Denied:
            checks[name + "_denied"] = True
        else:
            raise RuntimeError(f"{name} accepted by authority verifier")
    for name, check in (
        ("wrong_ear_policy", verifier("different-approved-policy")),
        ("wrong_authority_initdata", verifier(digest="0" * 64)),
    ):
        try:
            accept(token, check)
        except Denied:
            checks[name + "_denied"] = True
        else:
            raise RuntimeError(f"{name} accepted")
    try:
        verifier().verify(token, "0" * 64, runtime["request_sha256"], int(time.time()))
    except Denied:
        checks["wrong_runtime_nonce_denied"] = True
    else:
        raise RuntimeError("wrong nonce accepted")
    return {
        "scope": "isolated diagnostic appraisal, not storage authorization",
        "tests": checks,
        "policyId": POLICY_ID,
        "policySha256": policy_hash,
        "launchMeasurement": measurement,
        "appraisal": {
            "status": appraisal["ear.status"],
            "trustVector": appraisal["ear.trustworthiness-vector"],
        },
        "freshChallengeAndPossessionTested": False,
        "storageKeysReleased": False,
        "gpuAttestationTested": False,
        "encryptedStorageE2eComplete": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--signer", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(
        json.loads(args.request.read_text()),
        serialization.load_pem_public_key(args.signer.read_bytes()),
        (HERE / "diagnostic_cpu.rego").read_text(),
        json.loads(args.reference.read_text()),
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
