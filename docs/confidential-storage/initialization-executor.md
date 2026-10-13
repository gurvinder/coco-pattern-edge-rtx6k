# Initialization executor and failure boundaries

The trusted in-process coordinator in
[`executor.py`](../../components/confidential-storage-authority/executor.py)
connects the durable authority journal to a storage backend. Its current concrete
backend is the **disposable non-TEE Linux lab**. There is no production guest
daemon, authenticated RPC endpoint or Trustee key-delivery adapter yet. The Kata
activation gate remains closed.

## Execution contract

An executor is constructed with a trusted authority client, verified peer context
and backend. `AttestedPeer` remains an input type, not an attestation verifier.
The backend's `DeviceBinding` identifies the volume, provisioning request, capacity,
profile and versioned key reference. A future production backend must derive that
binding from trusted provisioning/device resolution and retain the exact device
descriptor. Caller-supplied paths, PVC names or annotations are insufficient.

`initialize(operation_id, generation)` serializes this backend and:

1. Consumes the executor instance before any call with an uncertain outcome.
2. Requires a fresh, non-revoked, administrator-approved volume with no owner or
   step history. Checks peer policy, generation and the complete device binding.
3. Invokes read-only backend preflight. A production implementation must reject
   unexpected signatures and retain the verified device identity. A blank device
   still requires the independent administrative approval.
4. Acquires the authority's initialization ownership. Only the first
   `plan-initialization` response is accepted; an existing owner requires recovery.
5. For each ordered step, obtains a newly committed `execute` decision and checks
   its activation owner, plan digest and generation before invoking the backend.
6. Validates the result schema and stable header identity, binds the evidence hash
   to the session/activation/plan/step, and records completion. Only then requests
   the next step.
7. Commits Ready after the final completion. Ownership is retained; the coordinator
   does not release it or expose a mount to an application.

The backend implements `preflight(context)` and `execute(step, context)`, returning
`Completion(result, evidence)`. These are trusted calls, not deserialization
interfaces. It must implement the fixed approved profile without accepting caller
shell commands, free-form cryptsetup options, alternate keys or arbitrary devices.
The coordinator does not inspect disk effects itself: validating a hash's syntax
does not establish that a disk write or attestation happened.

The four journal steps remain:

| Step | Backend responsibility | Before the next step |
| --- | --- | --- |
| `luks-format` | Format only the explicitly approved new device using the bound profile and key | Return actual complete header identity |
| `integrity-initialize` | Verify initialization performed by cryptsetup and the effective journaled integrity stack | Return the same header identity and verified stack evidence digest |
| `filesystem-create` | Create ext4 once, inside the verified mapping | Return the same header identity and observed filesystem UUID |
| `durability-barrier` | Flush writes, finish required teardown and durably publish the exact manifest outside the guest | Return the same header identity and manifest digest |

## Failure behavior

The executor never retries `execute`, even after a cancellation-like `BaseException`.
A production API adapter must run it in a service-owned worker and let that worker
finish if its client disconnects. It must not move the executor into a cancellable
request task or treat an RPC timeout as permission to start another worker.

| Failure point | Durable authority state | Retry behavior |
| --- | --- | --- |
| Before ownership/preflight fails | Uninitialized, no owner | This executor is consumed; a separately constructed executor still needs all checks |
| After ownership, before the first effect | Initializing, owned | Recovery required; no second initialization |
| After Started but before/during the effect | Current step Started | Recovery required; no second format or mkfs |
| After the effect, before completion commit/response | Started or Complete, depending on the commit | Recovery required; disk appearance alone does not authorize replay |
| All steps Complete, Ready not committed | Initializing with complete history | Explicit reconciliation needed; no destructive operations repeated |
| Ready committed, response lost | Ready | Initialization refused; trusted caller may inspect and follow the normal close/reopen protocol |

A failed worker does not automatically close mappings or release authority
ownership. Cleanup has to prove teardown before release; force/lazy cleanup and
timeout takeover remain forbidden. The present conservative policy can strand a
disposable volume. There is no automatic recovery or reset API.

## Kernel integration test

[`journal.py`](../../tests/confidential-storage/kernel-lab/journal.py) runs the
coordinator and SQLite authority outside a QEMU guest. A fixed serial-console
protocol lets the guest request the next step; it executes only after the
coordinator has committed Started. This protocol is **unauthenticated lab wiring**,
not a candidate production transport. The guest cannot supply arbitrary commands
or a different disk, and the driver creates every scratch image itself.

The successful path creates real LUKS2 + dm-integrity + ext4, writes the fixture as
an unprivileged process, unmounts and closes both targets, publishes the manifest,
and reaches Ready. A subsequent authority-approved reopen boots the same disk and
verifies the data without formatting.

For each of the four steps, a separate case calls `os._exit(77)` immediately after
the guest reports the real operation, before recording its completion. The harness
kills the remaining disposable VM process group, opens a fresh authority instance,
and attempts initialization again. It requires Initializing/Started to survive,
the retry to be denied, and the complete disk hash to remain unchanged by that retry.

This tests actual process death and persistent SQLite decisions. It does **not**
prove power-loss durability of real hardware, consistency of an interrupted
dm-integrity journal, recovery from a partially executed format/mkfs, or protection
against a malicious guest/backend. Unit tests cover sixteen before/after
effect/completion boundaries plus concurrency, revocation and Ready failures.

The lab key is a random initramfs fixture. It never comes from Trustee. The guest
uses a detached tmpfs header copy; the Rust sealed-memfd validator is still a
separate tested component. No production kernel/runtime, GPU or PVC is involved.

## Reproduce

Use the pinned unprivileged compiler pod and a fresh work directory:

```bash
python3 tests/confidential-storage/kernel-lab/fetch.py --work /build/debian
python3 tests/confidential-storage/kernel-lab/journal.py \
  --tools /build/debian/tools --work /build/journal-suite
```

`fetch.py` verifies the previously recorded package SHA256 values before extracting
their payloads; it runs no package installation scripts. Each case has its own new
128 MiB disk and private authority database. Export only reviewed text logs and
authority record snapshots. Delete the builder afterward; never export its key,
initramfs, root tree or disk images.

## Remaining production work

1. Implement a private attested authority/CDH channel and trusted device resolver.
   Keep the authority outside the workload's rollback domain; fence sessions before
   any recovery/takeover. A provisioning ID alone cannot prevent host device aliasing.
2. Implement the actual guest backend: fixed profile, key retrieval, sealed-header
   descriptor passed to cryptsetup, effective stack verification, and mount/teardown
   ownership through the Rust lifecycle controller. Select the production service
   language/integration explicitly; this Python coordinator is currently a reference
   implementation and lab driver, not a shipped Kata agent component.
3. Add failures during each command, partial writes, busy teardown, power-loss and
   journal/header corruption tests. Design explicit reconciliation before enabling
   recovery or retries. Sector integrity alone still permits storage rollback.
4. Build and test a separate guest/runtime artifact before opening the Kata gate.
   Keep the application container unprivileged; storage management belongs in the
   guest system service. Leave the live KServe runtime untouched.
