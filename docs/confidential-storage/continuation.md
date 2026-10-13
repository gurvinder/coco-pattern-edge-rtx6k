# Implementation continuation

Checkpoint: 13 October 2026, branch `dev/confidential-storage-integration`, based
on proposal/handoff commit `e4a3616`. Read the [report](compatibility-report.md)
before making changes. The original six-phase handoff still defines completion.

## Resources left on the lab

- Namespace `confidential-storage-test`.
- Empty raw-block RWOP PVC `storage-audit-data`, 1 GiB, class `lvms-vg1`.
- ConfigMaps `storage-audit-probe` and `storage-audit-no-secrets`.
- No remaining test pod, Job, authority process or new runtime handler. The
  temporary AMD64 compiler pod used for the transport checkpoint was removed.
- No production key, Trustee resource policy, reference value, runtime artifact,
  KServe resource, live GitOps branch, GPU allocation or route changed.

The earlier copied InitData ConfigMaps were deleted. The final public-image
diagnostic contacts no production Trustee endpoint. Do not reuse its policy for
secret-bearing workloads. Test keys and initialized encrypted disks do not exist.

## Code and checks

Implemented the opt-in diagnostic chart, no-secret InitData generator, sanitized
read-only inventory/comparison tool, and SQLite authority transition core with
no network listener. Sixteen authority tests and five diagnostic deployment tests
pass. Targeted Helm lint/render and server admission were exercised. Actual raw
device access failed in the unprivileged guest, as captured in the evidence.
The core is not integrated with an attestation verifier, Trustee or guest service.

The first repository-wide preview/schema attempts timed out during the utility
image download. After the download completed, the retry reached the actual checks:
`preview-all` fails because this checkout has no such make target; `validate-schema`
fails for the existing baremetal/hub/spoke values because `global.hardware.profile`
is missing when the shared override path is rendered. These are not passing checks.
The new isolated chart's targeted lint/render/tests pass. No unrelated values or
the read-only `common/` subtree were changed.

## Runtime/agent transport checkpoint

The [Kata experiment](../../components/confidential-storage-kata/README.md) now
contains a hash-pinned patch for `e53adb460e6040e6aa9719a735e17301462c4fa9` of
Manuel's experimental Kata source. This is **not** a match for the installed
Kata 3.31 downstream source and is not an approved runtime replacement.

Implemented the Go adapter, versioned JSON contract, shared parser fixtures,
Go-to-Rust protobuf interoperability check and guest preflight against measured
InitData grants. The adapter removes the selected raw block device from the
application configuration and substitutes a filesystem bind request. The guest
checks container identity, profile/destination/access/operation, unprivileged
process settings, fixed mount options and raw-device/alias rejection.

Seven Go tests (including race instrumentation and 42 subcases), compilation of
the actual runtime call site, and 12 Rust agent tests passed on isolated AMD64
hardware. The patch was checked and applied to a clean checkout at the pinned
SHA. See [test evidence](../../tests/confidential-storage/evidence/2026-10-13/transport).
The compiler pod had no GPU, PVC, host access or service-account token. The final
inventory confirms production pods, resource specifications, RuntimeClasses and
GitOps sources were unchanged.

**Activation remains deliberately unavailable**, even when validation succeeds.
The current CDH prototype API cannot enforce the requested profile and authority
session binding. No persistent filesystem mount handler, new key delivery,
initialization, teardown integration or deployable guest artifact was completed.
This checkpoint is transport/security-preflight code, not an encrypted-storage E2E.

## Signed receipt and lifecycle checkpoint

The [guest-service core](../../components/confidential-storage-guest/README.md)
now verifies authority-signed, session/nonce-bound reopen receipts. Registration
has immutable mount bindings, and bound initialization commits pin both header
and manifest digests. Exact request retries preserve the activation; changed
nonces or operation replay are denied. Legacy records cannot issue new receipts.

The Rust controller serializes exclusive activation, records possible effects
before invoking a backend, retains failed cleanup phases, and releases ownership
only after unmount and mapping close succeed. Closed activation tombstones cannot
release a new owner. Service-owned tasks survive a cancelled RPC waiter; ambiguous
mounted results remain recorded for explicit retry/close. The backend interface
has no format operation.

Twenty-five Python authority/receipt tests and 17 Rust lifecycle/interoperability
tests passed, including Python-generated Ed25519 signatures, 28 invalid signed
receipt vectors, hash mismatches, failure injection, concurrency, cancellation,
panic and a queued-worker expiry test. Rust formatting and Clippy with warnings as
errors passed. The 5 diagnostic chart regression tests also passed. See
[test evidence](../../tests/confidential-storage/evidence/2026-10-13/lifecycle).

**This code is not connected to the Kata gate or real CDH disk operations.** There
is still no attestation verifier/channel adapter, private storage RPC endpoint,
cryptsetup/mount backend, first-use initialization or deployable guest image.
Storage effects in tests are injected backends; test peers are synthetic.
No real key was issued, no encrypted disk was mounted, and no new runtime was
installed. The compiler was isolated from GPUs/PVCs and removed after validation.

## Authenticated header preparation checkpoint

The guest core now implements the candidate `rw-luks2-v1` profile and a strict
signed-manifest binding. It authenticates the entire 16 MiB LUKS2 header before
parsing, validates both metadata copies and the supported crypto/KDF/layout, and
stores the accepted bytes in a Linux memfd sealed against writes and resizing.
See the [profile contract](../../components/confidential-storage-guest/profiles/README.md).
The original source can change after preparation without altering the sealed copy.

Twenty-four regular Rust tests and one explicitly enabled cryptsetup tool test
pass. The latter uses real cryptsetup 2.6.1 to parse a sealed synthetic header and
create/unlock an ordinary LUKS2 regular-file image that the RW profile then rejects
because it does not meet the authenticated-storage requirements. The synthetic
positive header is not a usable encrypted disk. No device-mapper target was opened.
Evidence is under `tests/confidential-storage/evidence/2026-10-13/header/`.

This adds header preparation, **not an opener or a mounted filesystem**. The fixed
profile remains a candidate until the effective kernel stack, journal behavior and
corruption/recovery tests pass. Device resolution, real attested authority/CDH
transport, key retrieval, cryptsetup activation, first-use initialization and the
private guest-service endpoint remain unfinished. The Kata activation gate is
unchanged. No production key or runtime was modified.

## First-use authorization checkpoint

The authority now requires an immutable administrator-approved new-volume plan
before `begin(initialize)`. Ownership alone returns `plan-initialization`, not a
format decision. Four ordered steps record Started before the sole execute decision:
LUKS creation, integrity initialization/verification, filesystem creation and a
durability barrier. Retried Started steps require reconciliation; completed steps
cannot execute again. Ready requires matching final header/manifest digests.

Forty-one Python tests pass, including concurrent first-use requests, lost
responses, authority restart, transaction failures, revocation and rejection of
unapproved/Ready/read-only volumes. Old development records lacking the step journal
cannot issue new signed reopen receipts. See
[the first-use guide](first-use-initialization.md) and
`tests/confidential-storage/evidence/2026-10-13/initialization/`.

This is durable authorization bookkeeping with synthetic completion evidence.
No formatter, guest executor, attestation adapter or recovery routine was deployed;
no cluster resource or live PVC was changed in this checkpoint. The Kata activation
gate remains closed. Actual format/open/mount and crash/corruption tests are still
required before the storage is usable.

## Real kernel storage checkpoint

The [disposable kernel lab](../../tests/confidential-storage/kernel-lab/README.md)
now passes real LUKS2 + dm-crypt + journaled dm-integrity operations in a software
emulated Linux guest: fresh initialization, ext4 application writes without
capabilities, reopen after reboot, wrong-key denial and ciphertext modification
detection. A separate Rust test accepts the actual generated header through the
authenticated profile and sealed-memfd preparation path. Package pins, console
logs and cluster comparison are under
`tests/confidential-storage/evidence/2026-10-13/kernel/`.

This replaces neither the guest executor nor its authorization integration. The
lab uses a random fixture key and its own new scratch file, with no TEE, Trustee,
Kata container, PVC, GPU or host device. The kernel is Debian 6.1, not the installed
RHEL guest kernel. Crash/recovery and replay tests remain outstanding. No encrypted
storage E2E gate has passed; the Kata activation gate remains closed.

## Journal-to-kernel executor checkpoint

The [trusted initialization coordinator](initialization-executor.md) now connects
the authority journal to a bound backend, records Started before each operation,
validates completion before advancing, and refuses reuse or uncertain initialization
history. Fifty-one authority tests pass, including sixteen before/after
effect/completion boundaries, concurrency, binding mismatches, revocation and lost
Ready responses.

The concrete non-TEE lab backend runs the real Linux operations under this
coordinator. Success reaches Ready and reopens the same data. Four additional cases
kill the worker after each actual step but before completion acknowledgement, then
kill its disposable VM. A fresh authority/coordinator observes Initializing/Started
and refuses initialization; the disk hash is unchanged by that retry. Evidence is
under `tests/confidential-storage/evidence/2026-10-13/executor/`.

This is an in-process reference coordinator plus an **unauthenticated lab console
bridge**. It has not been installed in Kata and does not fetch Trustee keys. The
production device resolver, attested transport, sealed-header backend integration,
partial-write/power-loss recovery and guest service remain unfinished. No storage
E2E acceptance gate has passed, and the live runtime gate remains closed.

## Descriptor-based opener checkpoint

The [Linux guest opener](guest-opener.md) now connects signed receipts and the
authenticated sealed header to real cryptsetup activation. It retains a resolved
block-device descriptor, forwards that descriptor and the sealed header only to
the trusted child, and supplies the key through an anonymous pipe. Effective
kernel dependencies, target identities, journaled integrity options and crypto
status must match before the mapping is exposed to the guest service. There is
no formatting fallback. Failed or busy cleanup retains NeedsClose; both targets
must be absent before teardown succeeds.

Thirty-one Rust tests and Clippy pass. The real Linux lab additionally verifies
wrong-key refusal, sealed-header activation after source keyslot mutation,
collision ownership, an unprivileged application process, busy-close retention
and complete cleanup. Four boots pass, including subsequent reopen and ciphertext
corruption rejection. Evidence is under
`tests/confidential-storage/evidence/2026-10-13/opener/`.

This is a reopen/close primitive, not the complete lifecycle backend. The lab
mounts with separate commands and uses synthetic signatures and a random fixture
key. Device resolution, private mounts/application namespace binding, attested
authority/CDH delivery, service hardening and crash recovery remain unfinished.
The compiler was removed after testing and the production inventory is unchanged.
No live runtime, Trustee policy or encrypted-storage E2E gate was enabled.

## Linux staging backend checkpoint

The latest [Linux backend checkpoint](guest-mount-backend.md) connects the lifecycle
controller and opener to actual private ext4 staging mounts. Trusted provider
interfaces cover device/manifest resolution, key delivery, application detach and
authority release. Only synthetic lab providers exist. The production namespace
adapter and attested authority/CDH implementations remain required.

Thirty-three Rust tests and Clippy pass. Real Linux tests cover private-root
permissions/propagation/target collision, authentication before key fetch,
wrong-key cleanup and tombstones, an unprivileged namespace consumer, detach
failure, busy unmount, release-after-teardown and lost acknowledgement retry.
The existing four-boot persistence/wrong-key/corruption regression also passes.
Evidence is under `tests/confidential-storage/evidence/2026-10-13/mount-backend/`.
The compiler was removed and production state is unchanged. These remain non-TEE
tests and do not enable any storage E2E gate.

## Consumer namespace checkpoint

The [consumer namespace manager](consumer-namespace.md) now installs and detaches
the real application bind using retained namespace/root descriptors and Linux
descriptor-based mount APIs. It checks identity, fixed flags, target ownership,
namespace separation and inherited plaintext aliases. Cleanup records retain busy
or replaced mounts. The provider hook is now `quiesce_consumers`: the trusted
agent stops application activity, then the backend detaches its own recorded bind.

Thirty-four Rust tests and Clippy pass. The real Linux example covers successful
unprivileged access through the Rust-managed bind, wrong identities/namespaces,
replay, foreign overmounts, busy consumer references and inherited staging
rejection. An open inherited file keeps release blocked until that reference is
closed. Four regression boots pass. Evidence is under
`tests/confidential-storage/evidence/2026-10-13/consumer/`.

This is still a library integration, not a production Kata adapter. The inspected
CDH protobuf at the pinned source revision lacks our required session/profile/
operation binding. Production paused-container descriptor acquisition, quiescence,
attested device/key/authority providers and crash recovery remain unfinished.
The builder was removed and production state is unchanged; activation stays gated.

## Agent start/teardown checkpoint

The [trusted-agent coordinator](agent-lifecycle.md) now reserves ownership without
disk effects, prepares the runtime namespace before activation, attaches the
verified filesystem and gates application startup. It serializes startup/removal,
survives cancelled waiters and retains failed cleanup phases. Runtime destruction
follows successful storage teardown and authority release. Only one non-closed
confidential consumer per guest is admitted in this MVP.

The library connects to `LinuxBackend` and is exercised with a lab runtime in the
real Linux test. Production Kata RPC wiring is still absent. Source inspection
identified storage-before-create and root-destruction-before-storage-cleanup as
ordering changes required in the adapter. Descriptor acquisition, cgroup quiescence,
all-entry-point enforcement and attested providers still need implementation.
Forty-five Rust tests, strict Clippy, formatting and the four real Linux regression
boots passed. The builder was removed and production state is unchanged. See
`tests/confidential-storage/evidence/2026-10-13/agent-lifecycle/` for checks.

## Next engineering step

Build the guest-managed mount integration in an isolated artifact before installing
anything on the node. The installed Go runtime still lacks the new adapter. The
patched experimental agent rejects the new request before device handling and storage activation.
Do not remove that hard gate to reuse the prototype raw-device handler.

1. Connect the tested signed receipts to a real attested authority channel and
   guest-owned request/nonce context. Bind the volume, profile, operation/activation,
   source device, container identity and destination. Host paths locate devices; measured policy and authenticated
   authority records must authorize them. Avoid inventing a Kubernetes annotation
   and treating it as trusted authorization.
2. Port the tested transport patch to the approved downstream Go runtime
   baseline once its exact source is located. Add a guest-agent filesystem mount
   path before container creation;
   remove raw-device exposure to the application. Integrate the tested lifecycle
   controller and Linux staging backend with application bind ownership and teardown.
   The current controller is
   exclusive; sharing/refcounts need a separate implementation.
3. Extend the CDH profile implementation with independently authorized first-use
   initialization, exact authenticated-header handling and enforced journaled
   cryptographic integrity options. The prototype's profile name is insufficient.
4. Build a separate AMD64 guest artifact including compatible cryptsetup utilities,
   libraries and dm-integrity support. Pin all inputs and record SBOM/build hashes.
   The isolated AMD64 compiler has now built and tested the patched agent source,
   but no production guest initrd or CDH integration artifact exists. The disposable
   non-TEE lab initramfs is not a deployment artifact. Recreate the disposable
   builder for the next build; do not install tools into the inference guest.
5. Add the attested transport/verifier around the authority core and test receipts
   before enabling key delivery. Keep the authority outside the workload storage
   rollback domain. Existing initialization failures require conservative recovery,
   not another format grant.
6. Register a distinct test shim/config/initrd and RuntimeClass only after artifact
   tests. Confirm CRI-O reload behavior, package ownership and recovery before any
   node change. If isolation requires replacing shared files or interrupting the
   server, prepare the exact maintenance change for approval.
7. Run RW01–RW09/SEC/OPS against disposable synthetic data, then RO/publisher/
   populator gates. Both GPUs currently remain occupied, so GPU integration must
   wait for capacity or an approved change to an existing workload.

Do not reinterpret the authority unit tests or the PVC's `Bound` status as evidence
of confidential storage. No acceptance-matrix encrypted-storage E2E gate has passed.
