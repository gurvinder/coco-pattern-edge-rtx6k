# Authority request gateway evidence — 13 October 2026

The full authority suite passed **72 tests**, including **21 new gateway tests**
with negative subcases. `authority-tests.log` contains the complete result;
`validation.json` records the source/log hashes, interpreter, platform and pinned
cryptography version. The tests run locally with temporary SQLite databases and
ephemeral signing keys. No private key or attestation token is saved in this evidence.

The new tests exercise real ES256 signature verification, guest-key proofs,
Ed25519 receipt issuance and durable authority transitions. EAR claims are
synthetic: this does not verify hardware evidence, test AS interoperability or
demonstrate Trustee key release.

Coverage includes:

- Verified session binding through reopen, exact retry and idempotent close.
- One dispatch for concurrent copies of a challenge; durable operation replay
  rejection after release.
- Lost response after ownership commit, restart and challenge expiration without
  releasing ownership; fresh challenge retry preserves the activation.
- Body/action/token/key substitution; attacker-supplied signer JWK; missing proof;
  malformed EC points, duplicate JSON fields and algorithm changes.
- Missing, stale or adverse appraisals, wrong measured InitData, wrong audience
  and request binding, and expiry during verification.
- Required-GPU absence, missing verified runtime binding and mismatched device
  binding. A synthetic complete GPU binding is a parser/protocol test only.
- Rejection of client-supplied peer identity, administrative operations and
  initialization; revocation still enforced by the durable core.

Black 25.1.0 and Flake8 7.2.0 checks cover the new Python files; Markdownlint covers
the changed guides. No Rust, Kata patch, guest artifact or deployment configuration
changed, so the previous Kata results were not rerun or relabeled as new results.
This checkpoint made no cluster calls and created no builder or test workload.

See the [protocol and remaining gates](../../../../../docs/confidential-storage/attested-authority-gateway.md).
The inspected upstream NVIDIA result lacks the required exported runtime binding;
GPU-required requests fail closed. No confidential-storage activation is enabled.
