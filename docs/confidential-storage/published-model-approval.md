# Approval of a published read-only model

Status: implemented authority, guest validation and RO opener/lifecycle libraries;
**not yet a live confidential model mount**. The internal registry publication and PVC population
are real. Receipt tests use synthetic peers and temporary signing keys.

The RO path consumes a disk prepared by the trusted publisher. It never asks the
guest to format the disk. The RW path instead requires an administrator-approved
first-use plan and the durable initialization journal. Neither path can substitute
for the other's approval.

## Trust and lifecycle

1. The trusted publisher verifies the intended plaintext model and builds its
   LUKS2 image and dm-verity tree. Keep the model key in the separately administered
   key service. The importer and populator receive only ciphertext and metadata.
2. The administrator reviews the publisher's provenance and output. OCI digests
   establish content identity, not permission to run a model. Register a new
   immutable volume/generation, workload policy, RO profile, versioned credential
   URI and `/models` mount binding in the authority.
3. `publication.from_publisher(record, descriptor, source)` produces canonical
   manifest bytes. It validates the descriptor and source schema; it does not
   authenticate the publisher. `Authority.approve_published_model(volume_id, raw)`
   commits the exact manifest/header hashes, OCI source and verity root in one
   SQLite transaction. This is an administrator library operation, with no guest
   HTTP endpoint.
4. The volume populator copies the digest-pinned encrypted disk from OCI into its
   prime PVC, verifies the complete disk digest and completes the PVC handoff.
   A Bound PVC does not prove attestation, key release or decryption.
5. The intended attested guest requests a fresh session/nonce-bound reopen
   receipt. The authority accepts only the approved immutable manifest and
   registered policy/profile/mount binding. Existing exclusive ownership rules
   still apply; concurrent RO sharing is not implemented by this controller.
6. The guest verifies the signed receipt, manifest, device geometry and copied
   16 MiB LUKS header before requesting the credential. `PreparedRoHeader` retains
   those authenticated bytes in a sealed memfd, so cryptsetup need not reparse a
   mutable host-backed header.
7. `RoOpener` creates and verifies the exact stack: raw ciphertext
   device → dm-verity → read-only dm-crypt → ext4 mounted
   `ro,noload,nodev,nosuid,noexec`. Bind only that filesystem into the unprivileged
   application. No raw disk, mapper, header fd or key goes into the container.

Package the exact `profiles/ro-luks2-verity-v1.json` bytes with both services:
the profile's SHA-256 is part of the registration and receipt. The format fixes
AES-XTS, 4096-byte sectors, a 16 MiB data offset, format-1 SHA-256 verity without
a superblock, and bounded Argon2id parameters. The RO header has no dm-integrity
segment. Its whole header and ciphertext are covered by verity. The RW profile
continues to require journaled HMAC-SHA256 integrity; the RO parser cannot weaken
that requirement.

## Model versions and replay

Approval is immutable, including after close. Changing an OCI digest, root,
header, file inventory or even the canonical manifest bytes requires a new
registration and explicit workload policy selection. Receipt nonces and sessions
prevent reuse of another activation's authorization. Revocation denies subsequent
opens; it does not erase a key already released to a running guest.

This implements exact-version authorization, not a global “latest model” rule.
A release catalog/minimum accepted version and policy rollout are still needed
to retire old, separately authorized records. Keeping an old record authorized
intentionally permits that version. The authority database must live outside the
untrusted workload storage rollback domain.

## Evidence and limits

[Evidence](../../tests/confidential-storage/evidence/2026-10-13/model-approval)
records authority, cross-language signature and guest profile tests. The guest
also validated the actual published Qwen image's header and rejected modification
of its bytes. These checks do not mount the model or prove real SNP attestation.
The fixture generator uses a temporary database and ephemeral signing key; its
public fixture is deliberately unsuitable as live authorization.

Pending: real attested providers, production Kata lifecycle integration, deployment
and complete RO/RW acceptance tests. The diagnostic runtime
installation remains subject to the outstanding explicit node-change approval.


## Guest implementation

`RoOpener` retains the resolved raw block fd and sealed header. It creates a single
read-only dm-verity target with exact signed geometry, root and salt, followed by
a read-only LUKS mapping. It verifies mapping UUIDs, read-only flags, dependency
links, verity table, segment counts and cryptsetup status. It never queries a
key-bearing dm-crypt table. The verity target has no corruption-ignore,
check-once or discard options. An integrity failure returns I/O failure.

`LinuxBackend` selects the RO/RW opener from the verified grant. For RO it mounts
ext4 with `MS_RDONLY` and `noload`; `Consumer` places a private, read-only cloned
mount at `/models` in the application namespace. The RW `/data` path retains its
existing flags. Both paths require the same no-force teardown and durable owner
release ordering. A busy bind prevents release; a partly created mapping remains
tracked for explicit cleanup.

The disposable [RO kernel harness](../../tests/confidential-storage/kernel-lab/ro.py)
uses the real published Qwen disk with QEMU snapshot overlays. Its initrd contains
the explicit test key and must remain private. Its receipts and providers are
synthetic: passing this harness is storage-backend evidence, never evidence of
Trustee release or confidential execution.
