# Private key-delivery checkpoint

13 October 2026: the guest client now issues opaque key permits for approved
read-only model reopen and owned read-write initialization. The new
[private KBS provider](../../components/confidential-storage-kbs/README.md)
consumes those permits and reuses pinned CoCo RCAR/encrypted-response code to
retrieve the exact allowed credential. It has no application-facing interface.

This is development integration, not a deployed guest or successful hardware E2E.
The installed CDH, runtime handlers, Trustee keys/policies and KServe resources
are unchanged. No real model or PVC key was sent through the new provider.

Follow-up hardware integration inspection found that the installed diagnostic
guest uses SHA-256 InitData while the wrapper assumed SHA-384. The wrapper now
checks the exact raw InitData against the authority-pinned SHA-256/384/512 digest,
independently of the RCAR negotiated runtime-data algorithm. All 16 synthetic
KBS tests pass, including the two additional digest algorithms and an incorrect
pin rejection before any KBS request. See the `initdata-kbs` evidence directory.

## What the tests establish

Evidence is under
[`tests/confidential-storage/evidence/2026-10-13/private-kbs/`](../../tests/confidential-storage/evidence/2026-10-13/private-kbs/).

- 13 key-provider interoperability tests passed. A synthetic AS authenticates
  the Rust authority request; the real authority signs the storage decision.
  A separate temporary HTTPS KBS fixture verifies the actual RCAR runtime hash
  and returns a test credential encrypted to the fresh guest TEE public key.
- Both RO publication and RW initialization obtain usable test credentials.
  Two retrievals produce distinct TEE keys and repeat the attestation handshake.
  Merely approving an unowned RW plan cannot obtain an initialization key permit.
- Denial covers an unlisted resource, changed InitData, GPU-required configuration,
  hostname mismatch, KBS attestation rejection, redirects, oversized responses,
  tampered ciphertext/tag, wrong key length, short wrapped key and short curve
  coordinate. Malformed envelopes return an error without panicking.
- The modified authority client passes three Rust unit tests and all 16 TLS
  regression tests. Ready cannot use the initialization key path; ordinary
  signed reopen can obtain the correct key permit. Rust formatting, local strict
  Clippy and Python lint checks pass.

The fixture signs synthetic attestation results. It does not establish that real
SNP evidence, InitData measurement or GPU assignment matches the policies, that
an application cannot reach another key interface, or that a real disk was opened.
Those properties require the measured artifact and live acceptance tests.

## Required remaining integration

1. Load endpoint/CA, authority pins, policy, full InitData and exact key allowlist
   from verified measured configuration; close every application-accessible
   AA/CDH/broker bypass. Preserve the existing production policies.
2. Connect RO and RW reopen to the retained lifecycle/device context: authenticate
   the manifest and sealed header, retrieve the matching key privately, open the
   verified stack and bind only the filesystem into the unprivileged container.
3. Connect RW first-use permits and journal steps to actual guest formatting,
   integrity/filesystem creation and durability reporting. Errors or missing LUKS
   headers must never become format authorization.
4. Build and validate the downstream guest/agent integration. Run real private-AA,
   Trustee, key-release and negative policy tests in the separate SNP test runtime.
   Its host installation/reload remains subject to the pending explicit approval
   after automatic review rejected the shared-runtime change.
5. Finish both workload E2Es: serve the populated encrypted model and use/reopen
   private RW storage, with logs proving confidential execution and application
   privilege/device/key isolation. CPU-only key-provider tests do not establish
   composite CPU+GPU attestation.

Trustee attests the service and applies resource policy; the trusted guest service
enforces this custom grant before accessing KBS. The grant is not a new field that
an unmodified KBS independently checks. Revocation denies future authorizations
but cannot erase a key already released into an authorized guest.
