#!/usr/bin/env python3
"""Run the disposable no-key authority with sanitized protocol evidence.

Delegates all verification/authorization to the actual gateway. Logs no nonce,
key, proof, quote, token, receipt or chip identity. Never use for production.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "components/confidential-storage-authority"))
from authority import Denied  # noqa: E402
from gateway import request_body  # noqa: E402
from serve import configured_server  # noqa: E402


class Audit:
    def __init__(self, gateway):
        self.gateway = gateway
        self.nonces = set()
        self.lock = threading.Lock()

    def event(self, **fields):
        print(json.dumps(fields, sort_keys=True), flush=True)

    def challenge(self, raw):
        result = self.gateway.challenge(raw)
        with self.lock:
            fresh = result["nonce"] not in self.nonces
            self.nonces.add(result["nonce"])
        self.event(event="challenge", fresh=fresh)
        return result

    def attest(self, nonce, key, evidence):
        result = self.gateway.attest(nonce, key, evidence)
        self.event(event="hardware_attestation_verified")
        return result

    def execute(self, nonce, raw, token, proof):
        action = request_body(raw)["action"]
        try:
            result = self.gateway.execute(nonce, raw, token, proof)
        except Denied as error:
            self.event(
                event="execute_denied",
                action=action,
                operation_replay=str(error) == "operation replay",
            )
            raise
        timing = {}
        if "receipt" in result:
            payload = result["receipt"].split(".")[1]
            claims = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            timing = {"receipt_iat": claims["iat"], "receipt_exp": claims["exp"]}
        self.event(event="proof_and_execution_verified", action=action, **timing)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    with configured_server(args.config) as server:
        server.gateway = Audit(server.gateway)
        server.serve_forever()
