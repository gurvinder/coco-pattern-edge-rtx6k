# Live private authority protocol checkpoint

13 October 2026, 22:41 UTC: the isolated SNP guest completed the actual Rust
client → TLS authority → Trustee AS protocol. This is a **no-key diagnostic**,
not either encrypted-storage E2E. Its registered KBS credential intentionally
does not exist. The guest has no PVC, GPU or service-account token.

## Verified flow

1. A root-only service inside the measured guest creates a private P-256 session
   key and asks the TLS authority for a fresh challenge.
2. The fixed, hash-pinned AA client obtains real SNP evidence from the private
   ttRPC socket. The report binds the challenge, exact request digest, audience
   and guest public key. The application cannot access that socket.
3. The isolated AS validates the AMD chain/signature using the approved offline
   VCEK cache and appraises independently calculated launch/TCB/InitData references.
4. The authority verifies the pinned AS signer, exact policy, affirmative claims
   and request binding. The guest proves possession of its private session key.
5. The authority returns an Ed25519 receipt. The guest verifies its signature,
   exact requested volume/profile/binding/session and validity interval.
6. A new challenge/attestation for the same operation returns the same activation.
   Two close requests succeed idempotently. A subsequent fresh, authenticated
   reopen of that closed operation is explicitly denied as `operation replay`.

There are **five distinct challenges, five verified hardware attestations, four
successful proof/operation checks and one explicit replay rejection**. The final
authority record is Ready with no owner and retained last-closed history. No disk
operation or key retrieval occurs, so this diagnostic can close unconditionally;
the real storage provider must prove teardown before releasing ownership.

The app reports UID/GID 1000, zero capabilities and NoNewPrivs=1. The private AA
directory is root-owned 0700, its client is root-owned 0700, and app checks confirm
no AA socket/device-mapper access. `oc exec` fails with
`ExecProcessRequest is blocked by policy`. The app exits 0. Production pod
identities, readiness/restarts, specifications and GitOps sources remain unchanged.

Evidence: [`live-authority/`](../../tests/confidential-storage/evidence/2026-10-13/live-authority/).
The raw quotes, test private signers, TLS keys and authority databases are outside
Git. `guest-public.json` contains only public verification material and diagnostic
request metadata. It cannot recreate the server's private keys.

## Issues found and fixed

The chart originally omitted the Pod namespace. An initial no-key probe landed
in `default`; it was removed. The chart now embeds `confidential-storage-test`,
and the recorded passing repeat uses that namespace. No production pod changed.

Two repeats passed server-side hardware/proof verification but the guest refused
the receipt's time validity. The verifier now distinguishes authenticated
`NotYetValid` from expiry/other validation errors. The client waits up to two
monotonic seconds and re-verifies the **same receipt** at the current guest time.
It never accepts before `iat`, extends `exp`, changes the clock, retries a storage
effect or skips signature/binding checks. Far-future and expired receipts fail.
The failed diagnostic databases retain their ownership records; each diagnostic
retry used a separate database. That is not a recovery/reset procedure for real
storage authorities, which must retain durable ownership and history.

All 19 TLS/client regression tests pass, including one-second future validity,
30-second future rejection and expired-receipt rejection. Guest tests pass:
48 unit tests plus two receipt tests; three tool-dependent cases remain ignored
in this ordinary suite. Strict client Clippy, Python lint and Helm lint pass.
The final initrd rebuild is byte-identical.

## Reproduce in a new isolated test environment

Keep the AS administrative listener on loopback. The TLS authority is a temporary
listener at the compiler pod IP on 8443, with a dedicated short-lived CA/certificate
for `storage-authority.invalid`. The guest pins the CA and explicitly resolves
that hostname to the configured pod IP. No Route, public DNS or production
Trustee change is needed. The recorded TLS authority has been stopped.

Prepare fresh private state alongside the separately built AS:

```sh
python3 tests/confidential-storage/private-aa-probe/setup-live-authority.py \
  --state /private/new-live-authority --as-state /private/isolated-as \
  --address COMPILER_POD_IP
```

Build `components/confidential-storage-client` with its committed Cargo.lock and
Rust 1.94.1: `cargo build --locked --release --example guest_authority_probe`.
Package the binary, pinned private AA client and **public** guest config:

```sh
python3 tests/confidential-storage/private-aa-probe/build-live-authority.py \
  --base /scratch/kata-cc.initrd --client /scratch/ttrpc-aa-client \
  --probe /scratch/guest_authority_probe \
  --config /private/new-live-authority/guest-public.json \
  --output /scratch/authority.initrd > /scratch/build.json
```

Independently calculate SNP measurement using `sev-snp-measure==0.0.13`, the
pinned OVMF/kernel, this new initrd, EPYC-v4, one vCPU, guest features `0x1` and
the exact `kernelCommandLine` in the evidence build record. Do not copy a quote's
measurement into an allowlist. Put the calculated value in a copy of the strict
diagnostic CPU policy and register it as `storage-authority-probe-v1_cpu` on the
**isolated** AS. Pin all remaining InitData/TCB/guest-policy checks unchanged.
The fixed storage-policy identifier digest in the public config is separate from
the AS Rego file digest; the authority pins the AS policy ID and signer explicitly.

Start `serve-live-authority.py --config /private/new-live-authority/server.json`.
The wrapper delegates all verification to the actual gateway and logs only
sanitized events. Install this hash-checked image only in the separate test
handler; do not rerun the clean handler installer over an existing installation.
No second CRI-O restart or reload is needed to change this test config's initrd.
Render the existing probe chart with explicit reviewed diagnostic InitData, start
the read-only console capture for the exact test initrd, then apply in the test
namespace. Require both the guest PASS markers and server-side replay denial;
an arbitrary connection error is not a passing negative test.

The live config currently points to `initrd-authority-v3` under
`/opt/kata-storage-test/private-aa-probe-94354456b0c3/`. Its SHA-256 is
`3eca4600fe773f1955d8e5687a57c2277f681e3b306ec82e03334088e427c72f`.
The measured diagnostic image/InitData must **never** be approved for storage
keys: its permissive no-secret agent policy is deliberately not production policy.

## Remaining acceptance work

The authenticated protocol is now hardware-tested. Real private KBS release,
measured configuration loading with all application bypasses closed, RO/RW
backend/formatter integration into the agent, and downstream runtime packaging
remain unfinished. The populated encrypted Qwen PVC has still not been mounted
by a confidential workload. The blank PVC has still not been initialized by a
confidential guest. Preserve production CPU+GPU policies; this test used CPU SNP
only and provides no GPU-attestation result.
