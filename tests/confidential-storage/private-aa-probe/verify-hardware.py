#!/usr/bin/env python3
"""Verify a diagnostic SNP quote against isolated loopback AS; never release keys."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def post(request):
    req = urllib.request.Request(
        "http://127.0.0.1:8089/attestation",
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json"},
    )
    # The AS has administrative routes: it must remain loopback-only.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=40) as result:
            return result.status, result.read(256 * 1024).decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read(64 * 1024).decode()


def verify(request, signer):
    status, token = post(request)
    if status != 200:
        raise RuntimeError(
            f"positive hardware verification failed: HTTP {status}: {token[:500]}"
        )
    header, payload, signature = token.split(".")
    if json.loads(decode(header))["alg"] != "ES256":
        raise ValueError("unexpected EAR signer algorithm")
    sig = decode(signature)
    if len(sig) != 64:
        raise ValueError("invalid EAR signature size")
    signer.verify(
        encode_dss_signature(
            int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
        ),
        f"{header}.{payload}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    claims = json.loads(decode(payload))
    if (
        claims["iss"] != "storage-probe-as"
        or not claims["iat"] <= time.time() < claims["exp"]
    ):
        raise ValueError("invalid diagnostic EAR issuer or validity")
    changed_runtime = copy.deepcopy(request)
    changed_runtime["verification_requests"][0]["runtime_data"]["structured"][
        "storage-binding"
    ]["nonce"] = ("00" * 32)
    changed_initdata = copy.deepcopy(request)
    changed_initdata["verification_requests"][0]["init_data"] = {
        "init_data_digest": encode(bytes(32))
    }
    changed_signature = copy.deepcopy(request)
    item = changed_signature["verification_requests"][0]
    evidence = json.loads(decode(item["evidence"]))
    evidence["attestation_report"]["signature"]["r"][0] ^= 1
    item["evidence"] = encode(json.dumps(evidence).encode())
    checks = {"real_snp_signature_and_bindings": True, "pinned_ear_signature": True}
    for name, bad, expected in (
        ("changed_runtime_denied", changed_runtime, "Report Data Mismatch"),
        ("changed_initdata_denied", changed_initdata, "Host Data Mismatch"),
        (
            "changed_signature_denied",
            changed_signature,
            "Report signature verification",
        ),
    ):
        status, body = post(bad)
        if status not in (400, 500) or expected not in body:
            raise RuntimeError(
                f"{name} did not fail for the expected verifier reason: HTTP {status}"
            )
        checks[name] = True
    appraisals = {
        name: {
            "status": value["ear.status"],
            "trust_vector": value["ear.trustworthiness-vector"],
        }
        for name, value in claims["submods"].items()
    }
    return {
        "scope": "diagnostic SNP verifier interoperability; not storage authorization",
        "tests": checks,
        "appraisals": appraisals,
        "evidence_sha256": hashlib.sha256(
            decode(request["verification_requests"][0]["evidence"])
        ).hexdigest(),
        "storage_keys_released": False,
        "gpu_attestation_tested": False,
        "encrypted_storage_e2e_complete": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--signer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(
        json.loads(args.request.read_text()),
        serialization.load_pem_public_key(args.signer.read_bytes()),
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
