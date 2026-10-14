# Standard Trustee request-authentication adapter

**14 October 2026: implemented and unit-tested; not deployed in the GPU workload.**
The implemented adapter obtained an authentication challenge from the running
standard Trustee over verified TLS. Full real SNP+GPU attestation through this
new authority path remains to be tested in the candidate guest.

## Why use RCAR

The running Trustee is the pattern's AllInOne KBS deployment with
`attestation_service.type = coco_as_builtin`. Its AS is embedded in KBS; it is
not the standalone loopback `/attestation` server used by the CPU prototype.

The adapter uses existing KBS protocol 0.4 `POST /kbs/v0/auth` and
`POST /kbs/v0/attest`. It does not deploy another AS, retrieve storage resources,
call administrative endpoints, export the AS signing key or change Trustee code.
The live authentication probe established compatibility with `/auth`, not proof
that every part of the composite `/attest` flow has passed on this exact build.

The storage authority still handles model generation approval, RW ownership and
journaled initialization. Trustee appraises hardware evidence. CDH separately
retrieves LUKS credentials after the storage authority grants permission.

## One operation, one fresh handshake

1. The guest creates the exact initialization/reopen/close request. The authority
   retains its hash and issues a single-use 60-second authority challenge.
2. The authority starts a fresh KBS RCAR session over pinned TLS. It retains the
   KBS cookie and associates the KBS nonce with that exact pending request. The
   guest receives only the nonce and explicit SHA-384 protocol selection.
3. The guest service uses its dedicated P-256 **signing** key as `tee-pubkey`,
   marked `alg: ES256`. This is separate from CDH's resource-decryption key.
   The existing private AA collects NVIDIA evidence over the SHA-384 digest of
   canonical `{nonce, tee-pubkey}`. It then collects SNP evidence over the digest
   of `{nonce, tee-pubkey, additional-evidence}`. The CPU report therefore also
   binds the exact GPU evidence bytes, following the composite KBS protocol.
4. The authority submits that composite evidence through `/attest` using its
   retained cookie and administrator-approved InitData TOML. No guest field can
   choose a KBS URL, policy selector, certificate, InitData file or cookie.
5. The authority verifies the resulting EAR using its pinned AS signer and
   issuer. It requires exactly `cpu0`/SNP and `gpu0`/NVIDIA, expected policy IDs,
   affirming verdicts and the existing strict trust-vector values. It checks
   measured CPU InitData, matching nonce/key runtime claims on both devices,
   and the exact additional evidence bound by the CPU claim.
6. The guest signs the authority audience, authority nonce, exact request hash
   and token hash. Execute accepts only the token from that completed retained
   handshake, verifies the proof and atomically consumes the authority nonce
   before dispatching the durable storage transition.

The standard EAR does **not** acquire our custom `storage-binding` claim. Its
hardware-attested nonce/key are linked to the request by authority-owned state
and the guest's signature. This is an explicit protocol mode, not a weaker
fallback when the older claim is absent. A bearer token alone cannot authorize
an operation. An old token cannot be substituted into another pending challenge.

Each attempted `/attest` consumes that handshake attempt even if its reply is
lost: KBS may already have marked the cookie attested and could return its cached
token. A retry must obtain a fresh challenge/session while preserving the
original storage operation ID and guest signing key. Losing authority challenge
state does not release durable volume ownership. Losing the guest key still
requires explicit recovery.

## Components and configuration

| Component | Implemented change |
| --- | --- |
| Authority `kbs_rcar.py` | Fixed HTTPS endpoint, dedicated CA/hostname verification, bounded auth/attest responses, retained cookie; no resource/admin method |
| Authority `rcar_gateway.py` | Request-to-handshake association, one-attempt attestation, exact token matching and unchanged proof/consume-before-dispatch semantics |
| Authority `attestation.py` | RCAR runtime-claim verification alongside the unchanged direct-AS mode; both retain signer/policy/InitData/freshness checks |
| Guest authority client | Explicit `new_rcar` mode; rejects absent RCAR challenge, unexpected mode or weaker hash selection |
| Guest `private_rcar_aa.rs` | Calls existing `GetTeeType`, `GetAdditionalEvidence` and `GetEvidence` over the root-private AA socket; requires SNP and NVIDIA evidence |
| Private service | Opt-in `trustee-rcar` build feature and measured `authority.trustee_rcar: true` configuration |
| Trustee / AA / CDH servers | No source changes made for this adapter |

Keep the existing authority configuration fields for receipt signer, database,
TLS identity, exact workload digest, InitData, AS public signer and appraisals.
For the new server mode, replace `as_loopback_port` with this explicit object:

```json
{
  "trustee_rcar": {
    "origin": "https://kbs.example.internal:8080",
    "ca_file": "/etc/storage-authority/kbs-ca.pem",
    "init_data_file": "/etc/storage-authority/approved-initdata.toml",
    "address": null,
    "policy_selector": null
  }
}
```

This is a configuration fragment with placeholders, not a deployable manifest.
An optional static address is `["IP", port]`; TLS still checks the origin hostname.
The InitData file's digest must match the separately pinned expected digest.
The AS verification key is a public key; no AS private key is needed here.
Pin actual EAR policy IDs and issuer after validating the candidate's results.
Do not copy an example policy ID merely because it is named `default`.

The new mode requires both CPU and GPU appraisals and cannot be used as a CPU-only
fallback. The old `as_loopback_port` mode remains for reproducing the CPU tests;
configuration containing both modes is rejected. The new guest collector fails
closed when unavailable or when AA returns no NVIDIA evidence. Its parent and AA
socket directories must be root-owned 0700. Application access stays disabled.

Build after staging the pinned guest-components sources with the existing helper:

```sh
python3 components/confidential-storage-kbs/prepare.py \
  --upstream /scratch/guest-components --output /scratch/rcar-build \
  --with-service --with-test-fixtures
cd /scratch/rcar-build/components/confidential-storage-service
cargo +1.94.1 test --locked --features trustee-rcar
cargo +1.94.1 build --locked --release --features trustee-rcar
```

This does not install a runtime or authorize measurements. Select the private CDH
key provider in the combined GPU configuration. Its resource-credential encoding
remains explicit and independent of the authority signing key.

## Verification and remaining gates

The [validation record](../../tests/confidential-storage/evidence/2026-10-14/standard-trustee-rcar-validation.json)
records source hashes, test scope, live probe and unchanged workload identities.

- 121 authority tests passed, including signed synthetic composite EARs, missing
  GPU, wrong policy/nonce/InitData, changed request, bad proof, cross-challenge token,
  concurrent execute, expiry, restart and lost upstream response cases.
- Guest client: 5 tests with `private-rcar-aa`, 4 in the legacy build.
- Service: 8 tests in both `trustee-rcar` and legacy builds.
- KBS client: 2 private-CDH tests plus a wire round-trip test against pinned
  `kbs-types` 0.15.0. The latter confirms envelope/ES256-key serialization, not
  acceptance of actual evidence by the live KBS binary.
- Implemented adapter → live KBS `/auth`: passed with TLS certificate verification;
  a deliberately wrong hostname was rejected. No hardware evidence, model data,
  key request or policy mutation was sent. The temporary loopback forward closed.

The live image was
`registry.redhat.io/build-of-trustee/trustee-rhel9@sha256:85312d29d6ea7b96396b567c96893ae2527d83b9fc7a459044ba610c98593fae`.
The public signer certificate uses P-256. Source inspection used Trustee commit
`71251310045d8be2e95041cf0028691623c73957`, guest-components commit
`0ab1fb1be6e46293f29600085f929ae035ca3739` and the pinned KBS types. This inspection
is not a claim that the inspected Trustee checkout exactly matches the image.

Next: package the GPU-compatible candidate, approve its measurements and run the
full `/attest`/proof/receipt path with genuine CPU+GPU evidence. Verify exact EAR
claim shape and ES256 signing-key interoperability, then denied GPU appraisal and
replay cases. Keep the running Qwen workload unchanged until that candidate is
ready. Neither these unit tests nor the `/auth` probe establishes live GPU storage
integration or permission to weaken its existing security policy.
