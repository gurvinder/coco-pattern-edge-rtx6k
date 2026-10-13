# Attested authority request gateway — development checkpoint

`components/confidential-storage-authority/attestation.py` and `gateway.py` add a
cryptographic request boundary in front of the existing durable authority core.
The gateway verifies an AS-signed attestation result and proof of possession of a
guest-held key before issuing a reopen receipt or acknowledging close.

**This is an internal library, not a deployed service or a hardware attestation
acceptance result.** Tests generate synthetic EAR claims and real ephemeral signing
keys in memory. No key delivery, formatting, mount, cluster change or Kata
activation is performed. The trusted core still exposes internal administrator
methods; those must never be exposed directly as guest endpoints.

## Trust configuration

The administrator constructs `EarVerifier` with:

- A pinned P-256 AS public key and exact issuer. Embedded JWKs and certificate URLs
  in a token never select the verification key; no network key discovery occurs.
- An explicit authority audience and exact verified CPU InitData digest. The
  administrator maps that measured configuration to the authority's workload
  `policy_sha256`; the request cannot choose that mapping. Do not hash extracted
  `agent_policy_claims` and assume it is the original policy's digest.
- An exact device appraisal set, beginning with `cpu0` using SNP or TDX. A GPU
  workload additionally requires explicitly configured NVIDIA submodules. No
  sample attester, missing GPU fallback or caller-selectable verification profile
  exists. Extra device submodules are also rejected by this initial strict profile.
- An exact appraisal policy ID for each device. Use immutable, reviewed versioned
  policies: the inspected EAR code emits the policy ID, not its content hash.
  Pinning this ID alone cannot detect an administrator overwriting that policy.

Each appraisal must be `affirming`, with hardware/configuration/executables equal
to 2/2/3. Other supplied trust-vector values must be integers between 0 and 3;
missing required claims, adverse values and booleans substituted for integers fail.
These are admission constraints on the AS result, not a replacement for reviewing
the CPU/GPU appraisal policies, reference values and device association checks.

The JWS algorithm is fixed to ES256. Tokens must have a maximum 300-second lifetime,
be at most 60 seconds old and be currently valid. Duplicate JSON members, unsupported
critical JWS parameters, malformed keys, oversized messages and algorithm changes
are rejected. The authority needs a trustworthy wall clock; challenge expiry also
uses a monotonic clock. The verifier supports the inspected EAR shape only, with
no legacy-token fallback.

## Request flow

| Step | Actor and action |
| --- | --- |
| 1 | Measured guest service generates a dedicated P-256 storage signing key inside the guest. It retains this key for that guest session; applications never receive it or a signing API |
| 2 | Guest constructs the exact UTF-8 JSON request body: reopen with volume/generation/profile/mount binding/operation/receipt nonce, or close with volume/activation |
| 3 | `Gateway.challenge(body)` validates the schema, records SHA-256 of those exact bytes and generates a random 32-byte nonce. It returns the nonce, audience, request digest and 60-second lifetime |
| 4 | Guest attests runtime data containing the storage binding below. Trustee AS must verify the hardware evidence, measured InitData and required device appraisals before signing an EAR result |
| 5 | Guest signs the domain-separated proof below with its dedicated storage key and submits body, nonce, EAR token and proof |
| 6 | Gateway verifies the pinned AS signature, configured appraisals, measured configuration and identical request binding on every required device. It verifies the guest signature with the key from those authenticated runtime claims |
| 7 | Gateway rechecks expiry and atomically consumes the challenge before dispatch. Concurrent copies cannot both dispatch |
| 8 | For reopen, the existing receipt issuer durably acquires/checks ownership before signing its Ed25519 grant. For close, the existing authority verifies the same session/activation before releasing ownership |

The new runtime-data extension is:

```json
{
  "storage-binding": {
    "protocol": "confidential-storage-request/v1",
    "audience": "https://storage-authority.example/v1",
    "nonce": "<authority challenge, 64 lowercase hex characters>",
    "request_sha256": "<SHA-256 of exact request bytes>",
    "key": {
      "kty": "EC",
      "crv": "P-256",
      "x": "<32-byte coordinate, unpadded base64url>",
      "y": "<32-byte coordinate, unpadded base64url>"
    }
  }
}
```

It must appear under each required submodule's
`ear.veraison.annotated-evidence.runtime_data_claims`. It is not an annotation
trusted merely because a caller supplies it. The guest/AS adapter must bind the
runtime bytes to verified hardware report data. The stock KBS handshake chooses
its own nonce and has a fixed runtime-data layout; this extension is **not** a
drop-in request to that endpoint. A direct AS evidence adapter or a reviewed KBS
protocol extension is required.

The proof signs a compact JSON object containing `protocol`, `audience`, `nonce`,
`request_sha256` and `attestation_sha256` (SHA-256 of the exact compact token's ASCII
bytes). `proof_message()` defines the wire bytes: sorted keys, no extra whitespace,
JSON ASCII escaping. ES256 signatures are 64-byte `r || s`, encoded as unpadded
base64url. This prevents moving a proof to a different body, action, authority,
challenge or attestation token. The key is separate from the KBC's encryption key.

An authenticated session is the SHA-256 of the canonical protocol, audience,
dedicated public key, pinned workload policy digest and InitData digest. It stays
stable across fresh challenges and EAR tokens from the same guest. It is not a
bearer credential: every operation still requires fresh evidence and a key proof.
Losing the guest key leaves existing ownership blocked for explicit recovery.

## Retries, release and initialization

There are two independent nonces: the one-use authority challenge and the guest's
receipt nonce in the reopen body. A lost response requires a new challenge and
fresh attestation, while retaining the original operation ID, receipt nonce and
guest key. The core then returns the same durable activation. Changing the receipt
nonce cannot reuse that operation.

Signing/delivery failure never rolls back ownership. Gateway restart discards
pending challenges, not volume records. Challenge expiry never releases a volume.
Concurrent submission consumes one challenge only once; a failed durable transition
still consumes that challenge. A bounded pending registry prevents unbounded
in-memory growth, but deployment still needs network rate and connection limits.

Close requires a fresh proof for the same session/activation. As before, the
measured guest service must only request it after consumer quiescence, bind/staging
unmount and mapper closure. The signature proves who requested close; it cannot
independently prove those kernel effects occurred. The close response needs
authenticated TLS before a real guest can trust it. No client/provider consumes
this response yet.

Only reopen and close are admitted. Registration, approval, initialization steps,
reset, force-takeover and key retrieval are unavailable through this gateway.
The existing first-use initialization journal therefore remains a separate trusted
integration task. No missing-header condition grants permission to format.

## Upstream compatibility and GPU gate

Inspection is pinned to Trustee
[`71251310045d8be2e95041cf0028691623c73957`](https://github.com/confidential-containers/trustee/tree/71251310045d8be2e95041cf0028691623c73957),
with file hashes in `components/confidential-storage-authority/attestation-source-lock.json`.
This is a source inspection pin, not the installed Trustee version or an approved
deployment baseline.

- The [EAR broker](https://github.com/confidential-containers/trustee/blob/71251310045d8be2e95041cf0028691623c73957/attestation-service/src/ear_token/broker.rs)
  signs ES256 results and exports structured runtime claims when the verifier
  returns validated `report_data`; it similarly exports verified InitData.
- The [AS evaluation path](https://github.com/confidential-containers/trustee/blob/71251310045d8be2e95041cf0028691623c73957/attestation-service/src/lib.rs)
  hashes structured runtime data and supplies the expected report data to the
  hardware verifier. Our custom storage-binding still needs a real guest collector
  and AS interoperability test.
- The [NVIDIA verifier](https://github.com/confidential-containers/trustee/blob/71251310045d8be2e95041cf0028691623c73957/deps/verifier/src/nvidia/mod.rs)
  validates the expected nonce, but the inspected result path does not emit the
  same `report_data`/structured-runtime claim binding required here. A successful
  GPU appraisal alone is insufficient for this new request contract. GPU-required
  requests remain denied until the verified binding is exported and tested; do
  not synthesize it in the authority from an untrusted request.

Matching challenges alone do not prove physical GPU assignment or prevent device
relay. The measured guest/device provider and reviewed composite-attestation
policy must establish the required association and confidential mode. This
checkpoint makes no new claim about the live inference GPU.

## Validation and next integration

Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v` from
`components/confidential-storage-authority/`. The new tests use real ES256 token
and proof signatures, the real SQLite authority and existing Ed25519 receipt issuer.
Negative claim tests re-sign the guest proof over each modified token, so they
exercise claim rejection rather than merely a stale proof.

All 72 authority tests passed, including 21 new gateway tests. See the
[recorded evidence](../../tests/confidential-storage/evidence/2026-10-13/authority-gateway/README.md).

Before activating storage:

1. Implement the measured guest collector/private signing interface and verify
   actual SNP/TDX evidence through an isolated AS. Applications must not access AA,
   CDH or a generic signing/attestation oracle that can authorize arbitrary requests.
2. Export and verify the required GPU runtime binding and device association;
   test missing, stale, mismatched and non-confidential devices against real AS.
3. Add authenticated TLS transport, authority signer/configuration provisioning,
   independent durable deployment, rate limits and restart/recovery tests. Keep
   administrator methods on a separately authenticated interface.
4. Implement session/grant/profile-bound CDH key delivery and the measured device
   resolver, then connect authorized CreateContainer preparation to the existing
   agent installation seam. Do not substitute ordinary CDH GetResource for this
   missing grant enforcement.
5. Exercise real storage and serialized agent RPCs together in the isolated VM,
   including failures and cleanup. The existing activation error remains intact.
