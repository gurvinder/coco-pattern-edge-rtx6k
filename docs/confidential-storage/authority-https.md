# Storage authority HTTPS and private attestation integration

**Development checkpoint, 13 October 2026.** The HTTPS adapter and fixed-policy
Trustee relay are implemented and tested with signed synthetic appraisal results.
Neither encrypted-storage workload has passed hardware E2E acceptance yet.
This service currently authorizes reopen/close only; initialization, publication
approval and grant-bound key delivery still need their guest-facing integration.

## Request path

The private guest client uses one ephemeral P-256 signing key for its lifetime.
Only trusted guest code may access that key and the AA/CDH Unix sockets. The
application must receive the mounted filesystem alone.

1. POST the exact operation JSON to `/v1/challenge`. The authority retains its
   SHA-256 and returns a random one-use nonce, fixed audience and 60-second TTL.
2. Construct `{"storage-binding":{...}}` as specified in the
   [gateway protocol](attested-authority-gateway.md). Hash its canonical JSON with
   SHA-256. Send the **raw 32 hash bytes** to AA `GetEvidence` through the private
   ttRPC socket; do not send the printable hex digest or hash a second time.
3. POST `{"nonce":...,"key":<public JWK>,"evidence":<base64url JSON evidence>}`
   to `/v1/attest`. The authority reconstructs the structured runtime data from
   its own challenge. The requester cannot choose the AS URL, policy, InitData
   expectation, TEE type or hash algorithm.
4. The relay calls the isolated AS `/attestation` on loopback. AS checks SNP
   evidence and its runtime/InitData binding, then signs an EAR token. The
   authority independently verifies its pinned signer, issuer, appraisals,
   InitData, freshness, nonce, request hash and guest public key before returning
   the token. No volume ownership or key is released by this endpoint.
5. Sign the domain-separated request proof over the challenge and token hash.
   POST `{"nonce":...,"request":<base64url exact operation bytes>,"token":...,
   "proof":...}` to `/v1/execute`. Successful verification atomically consumes
   the challenge before the durable transition and signed receipt.

All base64url fields are unpadded and canonical. A lost response requires a fresh
challenge/proof and the same operation ID/receipt nonce. Connection expiry does
not cancel an in-progress SQLite transaction or release an active volume.

The relay currently supports one explicitly configured SNP appraisal. Startup
rejects GPU-required configurations; it cannot downgrade them to CPU-only. This
test configuration must never replace the existing CPU+GPU production policy.

## TLS and deployment boundary

`components/confidential-storage-authority/serve.py` runs the service with an
explicit administrator configuration file:

```sh
python3 -m pip install -r requirements.txt
python3 serve.py --config /private/authority/server.json
```

The configuration has exactly these fields (paths shown are deployment inputs,
not files created by this guide):

```json
{
  "listen": {"host": "0.0.0.0", "port": 8443},
  "database": "/private/authority/state/authority.db",
  "tls_certificate": "/private/authority/tls/server.crt",
  "tls_key": "/private/authority/tls/server.key",
  "as_loopback_port": 8089,
  "as_signer": "/private/authority/pins/as-signer.pub.pem",
  "as_issuer": "storage-test-as",
  "audience": "https://storage-authority.example.internal/v1",
  "init_data": "REPLACE_WITH_APPROVED_INITDATA_HEX_DIGEST",
  "policy_sha256": "REPLACE_WITH_APPROVED_WORKLOAD_POLICY_SHA256",
  "appraisals": [{"submod": "cpu0", "tee": "snp", "policy_id": "storage-v1"}],
  "receipt_key": "/private/authority/signing/receipt.pem",
  "receipt_issuer": "storage-authority",
  "receipt_key_id": "storage-test-v1"
}
```

The placeholder digests deliberately fail validation. The guest must pin the TLS
CA/hostname, authority audience and receipt public key in measured configuration.
AS uses a separate P-256 signer; receipts use Ed25519. Private key files must be
regular files with no group/other permissions; symlinks are refused. The SQLite
directory must be 0700 and outside the workload storage rollback domain.

TLS 1.3 is mandatory. There is no plaintext authority mode. Each connection gets
one HTTP request, a ten-second wall-clock deadline and one of 16 bounded worker
slots, including TLS handshakes. Framing is restricted to one Content-Length and
JSON body; transfer/content encodings, ambiguous lengths and Expect are rejected.
Challenge requests are limited to 8 KiB; tokens/evidence have independent bounds.
Responses are non-cacheable. Errors do not reflect request contents and the
HTTP server does not log tokens, proofs or receipts. This is bounded concurrency,
not a distributed rate-limiting or production observability solution.

The AS listener **must remain bound to 127.0.0.1** in the same trusted network
namespace as the authority. Upstream REST AS also exposes an unauthenticated
policy-management API. Do not publish that listener as a Kubernetes Service or
Route. The authority exposes only the three POST routes above and never forwards
administrative paths or redirects. AS error bodies are not returned to the guest.

## Built upstream tools and installed-guest result

[The build lock](../../components/confidential-storage-authority/attestation-build-lock.json)
records source commits, Cargo lock hashes, toolchains, compiler image and output
hashes. Reproduce builds in clean checkouts at those commits:

```sh
# Trustee, Rust 1.95.0
cargo build --locked --release -p attestation-service --no-default-features \
  --features restful-bin,snp-verifier --bin restful-as

# Guest components, Rust 1.90.0
cargo build --locked --release -p attestation-agent --no-default-features \
  --features bin,ttrpc,rust-crypto,snp-attester --bin ttrpc-aa-client
```

Both builds succeeded in the isolated, unprivileged compiler pod. AS runs only
on that pod's loopback with a separate temporary signer. No production Trustee
key, policy or reference value was read or changed for these builds. The client
requires glibc symbols through 2.34, libgcc_s and libm; actual new-guest packaging
and execution remain to be tested.

A new UID/GID 1000 CPU-only `kata-cc` diagnostic, with all capabilities dropped,
no PVC, no GPU, no service-account token and no KBS credentials, returned:

```text
GET /aa/evidence?runtime_data=diagnostic
Attestation Feature Not Enabled
HTTP 404
```

The base64 variant and token route returned the same result. This is a disabled
public API, **not a failed hardware appraisal**. Both diagnostic pods were removed.
Keep that public interface disabled for storage consumers. Package the private
ttRPC client into the new measured guest and restrict its socket to trusted guest
code; do not mount the socket into the application or grant it extra capabilities.

## Verification and remaining gates

Ninety authority tests pass, including real TLS handshakes, signed request/retry/
close, replay and bad-proof denial, untrusted certificates, TLS-version rejection,
framing/size/deadline bounds, absent administrative routes, pinned AS request
construction, AS redirect/error denial, and connection loss during a durable
transition. Their attestation claims are synthetic; they do not prove SNP key
release. [Evidence](../../tests/confidential-storage/evidence/2026-10-13/authority-https/)
also records the live diagnostic and unchanged production inventory.

Next: run the private collector in the isolated measured guest, appraise its real
SNP report, connect authorization to CDH key delivery and the RO/RW agent paths,
then exercise both live PVC examples and their negative cases. Keep the existing
activation gate closed until those dependencies are connected and tested.
