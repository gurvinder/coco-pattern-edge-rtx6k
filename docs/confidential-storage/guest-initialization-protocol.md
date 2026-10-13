# Signed guest initialization protocol

Status: development implementation, 13 October 2026. This connects the Rust
guest client to the authority's first-use journal through the existing TLS and
attestation protocol. It is not deployed in a confidential VM. The test AS is
synthetic, and these protocol tests perform no disk operations or key retrieval.
The Kata activation gate remains closed.

## Trust and placement

The client belongs in the measured guest system service. The application gets
neither this interface nor the private attestation-agent socket, raw device,
volume key, mount privileges or `CAP_SYS_ADMIN`. The authority is administered
separately from the workload. Its SQLite database and signing key must not be
stored on the untrusted volume whose use it authorizes.

An administrator first registers the immutable volume, policy/profile, versioned
credential URI and `/data` binding, then approves the exact new-volume plan.
The plan digest includes capacity, provisioning ID, LUKS UUID, filesystem,
header/data offsets and those registered identities. The guest API exposes no
registration, approval, reset, forced takeover, key administration or format
command. A missing or corrupt header never authorizes initialization.

## Request timeline

Each row uses a new server challenge, private-AA evidence bound to that challenge
and the guest session key, a verified Trustee AS result, and a guest signature
over the exact request. The same guest session, initialization operation ID and
guest nonce persist across the rows. The same protocol already handles reopen.

| Guest action | Authority effect | Guest acceptance |
| --- | --- | --- |
| `initialization-plan` | Read approved public metadata | Verify signature, session, request and independent plan digest |
| `initialization-begin` | Durably acquire exclusive ownership and set Initializing | Accept only the bound activation and approved plan; this is not permission to format |
| `initialization-step` | Durably write Started before issuing one `execute` decision | Convert a fresh signed decision into a non-cloneable execute permit |
| `initialization-complete` | Record the trusted guest's verified result for that same step | Require the exact step ID, result and evidence digest in the acknowledgment |
| `initialization-ready` | Require four completed steps and commit header/manifest hashes | Independently derive the manifest and compare its exact bytes and barrier hash |
| `close` | Release ownership after trusted guest teardown | Require the matching activation acknowledgment; errors do not imply release |
| `reopen` | Authorize the initialized volume under a new operation ID | Validate an ordinary signed reopen grant before opening or mounting |

The four ordered steps remain `luks-format`, `integrity-initialize`,
`filesystem-create`, and `durability-barrier`. Their actual Linux operation
contract is described in [first-use initialization](first-use-initialization.md)
and [the executor guide](initialization-executor.md).

Initialization decisions use an Ed25519 JWS with type
`confidential-storage-initialization+jwt`, the pinned issuer/key ID and audience
`confidential-storage-guest/v1`. Claims cover the exact action, request SHA-256,
server challenge, guest session, workload policy and result. Lifetime is at most
60 seconds. TLS uses the measured dedicated trust store and hostname verification;
redirects and environment proxies are disabled.

The Rust verifier rejects malformed, unknown or duplicate typed fields, invalid
signatures, stale/future claims and binding mismatches. Public `Record` values are
opaque: callers cannot construct or deserialize an accepted record. Execute
permits cannot be cloned or deserialized, and consuming one checks its deadline.
The future disk executor must consume the resulting execution object exactly
once, bind it to the retained device/plan context and serialize local effects.
These types do not themselves perform formatting or prove physical exactly-once
execution.

## Public manifest recovery

The public RW manifest is derived from the immutable registered record, approved
layout and committed header digest. The guest independently derives its canonical
bytes before reporting the durability barrier. Ready commits their digest, and
subsequent authenticated metadata responses reconstruct those exact bytes.

This avoids requiring the running guest to write public metadata through a KBS
administrator API. `manifest_uri` remains an immutable logical identity in the
mount contract; this initialization API does not claim to upload a resource to
that URI. A consumer must use the authenticated returned manifest and verify it
against its fresh reopen receipt. The secret credential still resides in Trustee
and requires the separate, unfinished private CDH retrieval path.

## Interrupted initialization

Started is committed before signing the decision. A signing failure, lost reply,
expired permit or process crash therefore leaves a Started step. Requesting it
again returns `reconcile`, never another execute permit. A completed step returns
`complete`, and a Ready volume cannot receive formatting permission.

Reconciliation must prove the original effect and durability before acknowledging
completion. It must not rerun `luksFormat` or `mkfs` merely because a response was
lost. A new guest session cannot take over uncertain ownership. The conservative
outcome can be a stranded disposable PVC; there is no automatic destructive
recovery path. Restoring an old authority database still needs independent
rollback/epoch protection.

The authority verifies attestation and message bindings, but cannot inspect guest
disk effects. The measured guest executor must validate actual header/layout,
effective dm-crypt/integrity stack, filesystem and flush results. A signed
completion message or evidence digest alone is not proof of those operations.

## Validation and remaining work

Evidence is in
[`tests/confidential-storage/evidence/2026-10-13/guest-initialization/`](../../tests/confidential-storage/evidence/2026-10-13/guest-initialization/).

- Full authority suite: 106 tests passed, including concurrent requests, durable
  signing-failure recovery, owner/plan/profile mismatch, revocation, step ordering,
  Ready recovery and refusal to accept arbitrary manifest geometry.
- Rust-to-Python TLS suite: 16 tests passed. It exercises signed initialization,
  all four journal steps, Ready, close and reopen; lost execute decisions are not
  reissued. Validly signed but expired or mismatched responses are rejected.
- Rust unit test, strict Clippy, formatting and Python lint checks passed.

Reproduction commands are in the
[client README](../../components/confidential-storage-client/README.md). The
`transport_fixture` is explicitly test-only and must not enter a production guest
artifact or become an approved measurement.

Remaining integration is private CDH credential retrieval, trusted disk execution
of these decisions, production Kata lifecycle/device resolution, and real
SNP/Trustee interoperability. Neither requested confidential-workload E2E is
complete. Existing Qwen/KServe workloads and production policies stay unchanged.
