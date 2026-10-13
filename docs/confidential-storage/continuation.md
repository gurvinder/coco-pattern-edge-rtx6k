# Implementation continuation

Checkpoint: 13 October 2026, branch `dev/confidential-storage-integration`, based
on proposal/handoff commit `e4a3616`. Read the [report](compatibility-report.md)
before making changes. The original six-phase handoff still defines completion.

## Resources left on the lab

**Latest active work (13 October, model population):** the new test-only populator
controller is running and `qwen-luks-model-v1` is a Bound 2 GiB raw-block RWOP
claim populated from the internal registry's encrypted Qwen LUKS/verity image.
The corresponding source CR and isolated publisher/importer RBAC exist. A
temporary unprivileged `storage-compiler` pod is running for the next integration
stage. See the newest checkpoint below; older resource lists describe their
historical checkpoint, not the current lab.

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

## Concrete Kata callback checkpoint

The [runtime adapter](../../components/confidential-storage-kata/runtime-adapter/README.md)
now owns a real pinned `rustjail::LinuxContainer` and implements prepare/start/
quiesce/destroy callbacks. It retains pidfd, namespace/root and cgroup v2 handles,
excludes reaping during capture, kills the cgroup subtree and waits for population
zero before teardown. It refuses replaced cgroup paths and checks actual cgroup
removal after rustjail destruction.

This adapter is compiled against Kata but not wired into agent RPCs, Sandbox
process lookup or reaper/IO handling. The kernel probe tests the real process and
cgroup primitive with an `unshare` child, not the complete rustjail callback flow.
Partial-create recovery and the blocking FIFO-start boundary need further work.
The hard activation gate stays closed and no deployed runtime changed.
Five distinct adapter tests, 45 guest regressions, strict Clippy and all four Linux
VM boots passed. The compiler was removed and production state is unchanged.
Evidence is under `tests/confidential-storage/evidence/2026-10-13/kata-adapter/`.

## Real Kata callback checkpoint

The runtime adapter now bounds its FIFO readiness handshake and the disposable
lab exercises real rustjail create/start/quiesce/destroy callbacks. Normal startup,
a dead prepared child, FIFO replacement, cancellation before start and a foreign
cgroup overmount all have explicit checks. Cleanup refusal retains the namespace
handles; removing the overmount permits successful quiescence/destruction. Repeat
quiesce/destroy succeeds and premature/repeated startup is refused.

The application runs in its own root/mount/PID namespaces as UID/GID 1000, with all
five capability sets empty and NoNewPrivs enabled. It cannot see the fixture key
or raw disk. The FIFO test also covers a missing reader and reader death after
handshake, before the start write. Five adapter unit tests, 45 guest regressions,
formatting, strict Clippy and all four storage VM boots passed. See
`tests/confidential-storage/evidence/2026-10-13/kata-callback/`.

At this checkpoint the callback application used ordinary test storage. The encrypted
backend regression still uses its separate synthetic runtime. These two tested
pieces must next be connected through `Agent<LinuxBackend, KataRuntime>` in the
lab, with real mount attachment and teardown ordered around the paused Kata init.
Then integrate Sandbox/reaper/IO/RPC ownership. Partial-create/destruction recovery
and attested providers remain required; no live runtime or activation gate changed.

## Combined Kata and encrypted RW storage checkpoint

The [combined lab](kata-encrypted-lab.md) connects `Agent`, real `KataRuntime`
callbacks and `LinuxBackend`. A UID/GID 1000 application with zero capabilities
and NoNewPrivs writes/reads encrypted `/data`. Its only ext4 mount is `/data`, with
nodev/nosuid/noexec; the raw disk, mapper, key and private staging are absent.
A fresh VM boot and new Kata container verify the persisted payload hash.

Six initial scenarios and one after reboot cover writes, reads, busy references,
lost release acknowledgements, wrong keys and invalid manifests. Traces enforce
quiescence before bind removal and release before destruction. No raw device
reference remains at release. Exact retries do not repeat create/start/key fetch.
All four boots, five adapter unit tests, 45 guest regressions, formatting and
strict Clippy passed; evidence is in
`tests/confidential-storage/evidence/2026-10-13/kata-storage/`.

This supersedes the separate-runtime/backend limitation above. The providers are
still synthetic; the test uses a disposable non-TEE VM and has not enabled agent
RPCs or changed the live runtime. KServe and production configuration are unchanged.

## Process exit and IO ownership checkpoint (13 October)

The concrete adapter now exposes an identity-checked `ProcessHandle` without
transferring its `LinuxContainer` or `Process` to a second owner. Real waitpid
results publish retained exit status to concurrent, cancelled and late waiters.
The shared reaper lock is acquired before runtime state during both preparation
and exit dispatch. Output drains independently with a 64 KiB cap per stream;
destruction waits for recorded exit and completed IO, closes pipes and only then
removes the process after successful runtime cleanup.

The real-container VM tests passed: exit 42 with concurrent waiters and 128 KiB
stdout, distinct routing of an unrelated child and Kata's temporary bootstrap
parent, no retained pipe inodes after cleanup, and delayed-reaper refusal/retry
with actual SIGKILL exit 137. All five existing callback cases and seven encrypted
storage cases now assert exit 137 and IO cleanup. All four storage boots, six
adapter unit tests, 45 guest regressions, strict Clippy and formatting passed.
See [the process guide](kata-process-lifecycle.md) and
`tests/confidential-storage/evidence/2026-10-13/kata-process/`.

This was a non-TEE library/lab checkpoint. At that point the actual Sandbox, RPC
server and production SIGCHLD loop were not patched; the next checkpoint adds
source-level routing. Bounded development capture is not
production streaming IO. No runtime activation or attested provider was enabled.

## Actual agent ownership/routing checkpoint (13 October)

The cumulative `confidential-agent-v1.patch` now adds the managed registry to
actual Kata Sandbox state, routes start/wait/remove/stdin-close RPC helpers to the
coordinator/process facade, and dispatches SIGCHLD through the same workspace
rustjail reaper lock. Ordinary create/exec also take that lock before Sandbox.
Managed removal is service-owned and retains failures/tombstones; a failed close
blocks restart. Sandbox shutdown waits for managed cleanup without holding the
Sandbox lock, retaining the shutdown sender on failure. Unsupported managed
streaming IO, exec and ordinary mutable runtime lookups remain refused.

Eight agent routing tests passed, including real managed/ordinary child exits,
concurrent reaping and teardown, timeout, cancellation, wrong IDs and cleanup
retry. The 12 transport/preflight tests, two signal tests, 43 guest library tests
and six adapter unit tests passed. Strict agent Clippy, formatting and compilation
without optional features passed. The workspace lock adds only the two local
packages; both agent and adapter use one local rustjail instance.

The preparation script verifies the cumulative patch and 29 source-overlay files,
rewrites the three adapter dependency paths, and reproduces the tested sources
from a clean pinned checkout. Details are in
[the agent integration guide](kata-agent-integration.md); evidence is in
`tests/confidential-storage/evidence/2026-10-13/kata-agent/`.

The tests use in-memory managed owners and real child processes, not an activated
storage provider or serialized ttrpc connection. No host RPC calls the trusted
installation seam, and the hard activation error remains. The patch is not
installed on the live cluster.

## Attested authority request checkpoint (13 October)

The internal Python gateway now verifies an administrator-pinned ES256 Trustee EAR
result and a dedicated guest-key request proof before calling reopen/close. It
binds the exact request, one-use authority nonce, audience, measured InitData and
configured CPU/GPU appraisal set. Session identity remains stable across fresh
tokens for the same guest key. Durable ownership survives response loss, challenge
expiry and gateway restart; guest callers cannot invoke initialization/admin APIs.

This is real signature verification with synthetic attestation-result fixtures,
not a hardware attestation or key-delivery result. The new `storage-binding`
extension needs a guest collector and authenticated transport. Inspection of
Trustee `71251310045d8be2e95041cf0028691623c73957` found that NVIDIA appraisal
results do not export the verified structured-runtime binding required by this
protocol. GPU-required requests fail closed. Keep that requirement; do not copy
unverified request data into signed claims or accept a CPU-only fallback.

See [the gateway guide](attested-authority-gateway.md) for the exact contract and
`components/confidential-storage-authority/attestation-source-lock.json` for the
inspection pin. No cluster resource, runtime artifact, key or policy was changed.
All 72 authority tests passed, including 21 new gateway tests; see
`tests/confidential-storage/evidence/2026-10-13/authority-gateway/`.

## Encrypted model publication/population checkpoint (13 October)

The [model publisher/populator](model-populator.md) now prepares an actual
encrypted Qwen3-0.6B disk, publishes ciphertext to the internal registry and
populates a raw-block CSI claim through `EncryptedModelSource`/`dataSourceRef`.
The publisher verifies the complete verity region, every model file and read-only
mount behavior. The importer has no key and checks the complete artifact,
flushes and reads back the disk before handoff. The populated claim survived a
controller restart without another import; a wrong digest left the separate test
target unbound. Negative test resources were cleaned up.

The Go controller uses pinned CSI library v3.2.0 with a documented opt-in
same-namespace patch covering both informer events and reconciliation. Upstream
and local tests pass. The deployed controller image digest is
`sha256:46dc94bd59c9cd8e08437eb35326f740e9327153c970b8e107923e2bfe961ded`.
Evidence is under `tests/confidential-storage/evidence/2026-10-13/model-populator`.
Production audit comparison passed. No live runtime or production Trustee state
changed.

Current retained resources: source/PVC `qwen-luks-model-v1`, internal image
repository `confidential-storage-test/qwen-luks-e2e`, controller, its Helm-defined
RBAC/SCC/CA/build resources and the temporary compiler. The model source file in
evidence records the exact OCI and disk digests. The separate test key is privately
preserved outside this Git checkout; it is not provisioned to Trustee yet. Do not
reuse a production key or replace the running inference service to complete this.

This checkpoint proves ciphertext distribution, **not confidential serving or
writable guest initialization**. The user requested continued iteration until both
isolated examples actually run. Keep the overall goal active: the signed/fresh
guest authorization, attested key-delivery channel, RO opener and RW first-use
integration remain required before deploying an isolated confidential runtime.

## Next engineering step

### HTTPS/private attestation checkpoint (13 October)

The [HTTPS authority adapter](authority-https.md) is runnable from explicit
administrator configuration. It exposes challenge/attest/execute over TLS 1.3,
caps connections and message sizes, enforces an overall connection deadline and
never publishes administrator routes. The fixed loopback SNP AS relay chooses
policy/InitData/runtime data itself and independently verifies the returned EAR
and guest key. GPU-required configurations cannot use this CPU test adapter.
Ninety tests pass, including real TLS/HTTP with synthetic attestation claims and
connection expiry during durable mutation. This is not a hardware acceptance.

Pinned real `restful-as` and `ttrpc-aa-client` binaries were built in the retained
compiler. Source/toolchain/Cargo/output hashes are in
`components/confidential-storage-authority/attestation-build-lock.json`. The AS
currently listens on compiler loopback `127.0.0.1:8089` with its own temporary
signer under `/build/as-private`; there is no Service/Route. Do not expose its
unauthenticated policy endpoint. Guest components are under
`/build/guest-components`, AS source under `/build/trustee-as`. The private client
requires glibc symbols through 2.34; actual guest packaging is outstanding.
Compiler `/build` currently uses approximately 12 GiB of its 16 GiB limit; preserve
needed outputs and prune rebuildable caches before another large build.

Two temporary UID/GID 1000 CPU confidential probes found
`HTTP 404: Attestation Feature Not Enabled` on the existing guest's public AA
routes. No quote was obtained or key requested. Both probe pods were removed;
the model PVC/populator remain. The next collector must use the guest-private AA
ttRPC socket, not turn on an application-accessible attestation/signing oracle.
The final audit reports production unchanged. Evidence is under
`tests/confidential-storage/evidence/2026-10-13/authority-https/`.

**Immediate next work:** package the private collector into a separate measured
guest artifact and verify actual SNP evidence against the isolated AS. Then wire
the collector/TLS gateway and grant-bound CDH provider into the existing guest
storage lifecycle. The network gateway still only executes reopen/close; RW
initialization needs attested journal calls, and publisher-approved RO readiness
needs its own immutable authority transition. The model's unsigned publisher
descriptor must become a bound authenticated guest manifest before opening it.
Do not interpret these missing interfaces as permission to enable the Kata gate.

### Remaining integration

The private-AA diagnostic initrd is now built and locally checked; the exact
reproducer, public runtime binding, image hash and proposed node installer are in
`tests/confidential-storage/private-aa-probe/`. Its hash is
`94354456b0c3aff0704ff8eb0cbaf47023d6be66933fb2530d147b7e714a4678`.
Boot inputs and extracted artifact/client files are retained under
`/private/tmp/storage-guest-boot-base/`; the latest image is
`private-aa-probe-v2.initrd`. The builder preserves all original archive entries.

**Pending explicit approval:** automatic approval review rejected installing the
distinct handler and SIGHUP-reloading shared CRI-O. The installer did not run.
An async approval question was sent covering the exact node/handler/reload. Do not
retry or route around that rejection until the user approves. Continue independent
guest/authority implementation while it is pending. A separate earlier rejection
of broad host configuration export was resolved by copying only three boot
binaries and reading whitelisted configuration fields; no configuration directory
was exported. See the probe README for the concrete node changes and limitations.

The host confirmed CRI-O `CanReload=yes` and SIGHUP `ExecReload`. Existing CPU
handler already uses `/usr/bin/containerd-shim-kata-v2` with
`runtime_config_path`, so a new shim binary or wrapper is unnecessary for this
diagnostic. The production CPU config and CRI-O drop-in hashes are pinned by the
installer. No new RuntimeClass exists; creating its pod with server dry-run was
therefore rejected, as expected. The next live step depends on the approval.

Connect authorized CreateContainer preparation to the tested guest-only installation
seam, with authenticated authority/CDH/device providers and ownership of rootfs,
shared mounts and remaining generic resources. Add policy-controlled production
streaming IO and the required managed process RPC semantics. Then test the patched
agent and real storage backend together in the disposable VM through serialized
RPCs, including partial-create/destruction recovery. Keep the activation gate
closed until those providers and the approved artifact exist.

For the authority, first connect the tested HTTPS gateway to a measured private
guest collector and isolated AS. Implement/test the GPU verified-runtime binding
and device association for a GPU-required profile, then add grant-bound CDH delivery.
The internal gateway does not itself complete any of those integration gates.

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


## Published-model approval checkpoint

Added immutable administrator approval for an existing RO model disk, distinct
from the blank RW volume initialization journal. The publisher descriptor becomes
an exact manifest bound to the registered policy/profile, credential, OCI digests,
verity root and file inventory. Rust verifies the signed receipt and manifest,
strict RO LUKS profile and authenticated sealed header. RW integrity requirements
remain unchanged. See [the guide](published-model-approval.md).

The actual published Qwen disk passed header validation in the isolated compiler;
modified header bytes were rejected. Authority/signature fixtures remain synthetic.
The RO opener and common mount/consumer integration now pass the disposable
kernel test against the actual Qwen disk: wrong key, all file hashes under UID
1000 with zero capabilities, RO enforcement, busy teardown, and ciphertext
corruption returning EIO. These remain synthetic-authorization, non-TEE tests. The node probe installation approval remains pending, and production
runtime configuration has not been changed.


The latest RO implementation is `ro_opener.rs` plus the RO variant of the common
Linux backend, guest mount and consumer manager. Authority tests: 97; guest unit
tests: 48 plus 2 receipt tests, with 3 environment-dependent tests ignored by the
ordinary suite. The actual published-header test was run separately and passed.
Rustfmt, strict Clippy and Python formatting/lint checks pass. The cumulative
Kata overlay lock includes the new sources and passes clean-source verification.
See `tests/confidential-storage/evidence/2026-10-13/model-approval/`.

Immediate remaining work: real private-AA/AS interoperability (node probe approval
pending), attested profile-bound key and initialization transport, production Kata
activation/teardown integration, and the two full live examples. Do not treat these
kernel tests as evidence that a confidential workload has mounted either PVC.


The shared-backend RW kernel regression also passed initialization, reopen,
wrong-key rejection and HMAC corruption detection. Original Qwen disk SHA-256
remains `c350a0f6ea363219ab750a3a3c7d309f7d35fc009b5171d417acde33c69fa8b7`.
Final live inventory comparison reports `production_unchanged: true`.
Compiler scratch: `/build/storage-ro`, `/build/ro-kernel-v3`,
`/build/rw-ro-regression-v1`. The compiler pod still has the private test key and
publisher outputs; generated initrds must not be published. Its temporary sleep
expires around 20:37 UTC on 13 October; preserve required private artifacts
before deleting it. Do not restart or replace it blindly.
