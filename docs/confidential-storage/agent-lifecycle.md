# Trusted-agent start and teardown coordinator

Development checkpoint: 13 October 2026. This implements the runtime/storage
ordering barrier in `components/confidential-storage-guest/src/agent_lifecycle.rs`
and connects it to `LinuxBackend`. It is **not wired into Kata RPCs**, does not
supply an attestation verifier or a CDH client, and does not open the experimental
Kata activation gate.

## Why another lifecycle boundary is needed

The storage controller knows whether mappings and mounts exist. It does not know
whether the container has started, whether an exec request is racing removal, or
whether the runtime is about to dismantle the root filesystem. Those decisions
must be serialized with storage attachment and cleanup.

Inspection of the pinned experimental Kata source
[`e53adb460e6040e6aa9719a735e17301462c4fa9`](https://github.com/manuelh-dev/kata-containers/tree/e53adb460e6040e6aa9719a735e17301462c4fa9)
identified the concrete integration points:

- [`rpc.rs`](https://github.com/manuelh-dev/kata-containers/blob/e53adb460e6040e6aa9719a735e17301462c4fa9/src/agent/src/rpc.rs):
  `do_create_container_inner` calls `add_storages` before `ctr.start(p)` creates
  the application namespace. Opening this volume there would let the new namespace
  inherit guest plaintext staging. The confidential storage request must be
  deferred until after namespace creation; its ordinary OCI bind must not be
  processed by rustjail before the source exists.
- [`container.rs`](https://github.com/manuelh-dev/kata-containers/blob/e53adb460e6040e6aa9719a735e17301462c4fa9/src/agent/rustjail/src/container.rs):
  `start(p)` prepares the init process; it waits on an exec FIFO before launching
  application code. `exec()` releases it. That boundary can provide the paused
  consumer, subject to hook restrictions and verified descriptor acquisition.
- `destroy()` kills processes, destroys the cgroup and lazily unmounts the root.
  `do_remove_container` calls it before `remove_container_resources`. The new path
  needs a separate **quiesce while preserving the root/namespace** operation,
  followed by storage cleanup, then final runtime destruction. A successful signal
  call alone is not proof that all application activity has stopped.
- The prototype secure-volume cleanup removes its activation list before CDH
  acknowledgement and logs deactivation failures. This path cannot replace the
  retained cleanup state required here.

This source is an experimental baseline, not the installed downstream Kata 3.31.
No upstream code or live runtime was modified in this checkpoint.

## Implemented flow

`Agent::new` takes ownership of a backend and a trusted runtime implementation.
It owns its storage controller and does not expose it to RPC handlers. Runtime
container IDs come from trusted agent bookkeeping; signed receipts bind the
container name, volume, activation, session, profile and destination. An ID supplied
by the host is not sufficient authorization.

| Step | Coordinator action | Required runtime/storage behavior |
| --- | --- | --- |
| Admit | Reserve the verified receipt/device in `Controller`, with no disk effects | Reject invalid session/device, conflicting ownership, replay and capacity exhaustion |
| Prepare | Record `Preparing`, call `Runtime::prepare` | Create private namespace/root; retain the exact init object behind a start barrier; no application or untrusted hook executes |
| Activate | Call the existing controller's `activate` | Authenticate header/manifest, obtain key, open verified mappings, mount private staging |
| Attach | Pass runtime-owned handles to `LinuxBackend` | Install and verify the fixed application bind through the retained namespace/root descriptors |
| Ready | Record `Ready`; return to the trusted agent | The application still has not started |
| Start | Record `Starting`, invoke runtime start once, then record `Running` | Release only that init process's start barrier |
| Quiesce | Record `NeedsQuiesce`; stop application activity | Prevent concurrent exec/start/namespace mutation; preserve root and namespace |
| Storage close | Record `NeedsStorageClose`; call the existing controller's close | Remove consumer bind, staging and mappings; acknowledge authority release only after storage teardown |
| Destroy | Record `NeedsDestroy`, destroy runtime object, then `Closed` | Release remaining container resources only after successful storage cleanup |

The Linux backend retains its own idempotent provider quiescence check. A production
provider and runtime adapter must agree on the same quiescence state; do not
replace that check with an unconditional success in a deployable artifact.

Exact prepare retries return the existing Ready/Running state without creating
another namespace or attaching twice. Start retries in Running do not release the
FIFO twice. An error from start is ambiguous: cleanup quiesces the application;
startup is never retried. Panic leaves the pre-effect phase for explicit cleanup.
Closed container/activation identities remain tombstones, with capacity bounded by
the controller's registry.

Each operation runs in a service-owned Tokio task. Cancelling the RPC waiter does
not cancel an in-progress prepare/start/close. A mutex serializes those operations;
close winning the lock prevents a later start. A hung runtime/provider callback
blocks further operations; this MVP does not implement timeouts that abandon
ongoing effects, a durable guest journal or process-crash recovery.

## Isolation and adapter contract

This MVP admits **one non-closed confidential consumer per guest**, including
containers awaiting cleanup. It refuses to create a second namespace while an
existing plaintext mount could be inherited. It is not a sharing/refcount design.
An ordinary container created through another entry point could still inherit
staging: the production Kata adapter must also prohibit those creates during
confidential storage use, or first implement and validate separate mount isolation.

The real Kata adapter must:

1. Preserve existing measured-policy and unprivileged-OCI checks. Remove the
   deferred confidential bind from ordinary storage/rustjail setup, with no raw
   device exposure or fallback mount.
2. Bind the runtime container ID to the policy-authorized name; acquire namespace
   and root descriptors from the exact paused runtime object. Verify process
   identity/liveness and prohibit PID substitution, hooks and namespace mutation.
   The current library accepts trusted descriptors; it does not prove how Kata
   acquired them.
3. Route create/start/exec/remove and sandbox teardown through the same ownership
   boundary. Keep a consistent lock order; callbacks must not re-enter the same
   coordinator or reacquire a sandbox lock held by their caller.
4. Split quiescence from `destroy()`. Confirm application tasks have stopped and
   exclude new tasks while retaining namespace/root resources for bind removal.
   Do not equate cgroup signaling, a timeout, or a dropped RPC with completion.
5. Return cleanup failures while keeping ownership and retry state. Never use the
   prototype's log-and-forget deactivation path for these volumes.
6. Supply attested authority/CDH/device providers, including the required
   session/profile/operation bindings, before enabling activation.

`Runtime` is a privileged guest implementation interface, not an application
extension point. No application container receives `CAP_SYS_ADMIN`, a raw mapper,
a key, a storage-service socket or a privileged sidecar.

## Validation

The Rust tests cover reservation without disk effects, exact retries, identity and
device conflicts, single-consumer admission, errors at preparation/attachment,
each cleanup failure, ambiguous start, panic, cancellation, start/close races and
bounded tombstones.

The real kernel lab also drives `Agent` with `LinuxBackend`, synthetic signed
receipts and the random fixture key. Its runtime creates an inert namespace holder
before storage activation; it launches a synchronous UID 1000 application only
through `Agent::start`. The application has zero capabilities and NoNewPrivs,
reads/writes `/data`, and cannot access raw devices or private staging. Cleanup
removes the bind/mappings and releases authority ownership before killing the
namespace holder. The test checks duplicate start and closed-record rejection.

The lab runtime is **not Kata** and does not test Kata FIFO handling, process
identity acquisition, cgroup quiescence, attestation, RPC authorization or a long
running application's shutdown. Existing initialize/reopen/wrong-key/corruption
boots remain regression checks, not confidential-storage E2E acceptance.

Evidence: [agent lifecycle checkpoint](../../tests/confidential-storage/evidence/2026-10-13/agent-lifecycle/README.md).

A [concrete Kata callback adapter](../../components/confidential-storage-kata/runtime-adapter/README.md)
now runs real rustjail prepare/start/quiesce/destroy callbacks in the disposable
kernel lab. A bounded FIFO-reader handshake protects the start write from child
death. The application stays unprivileged; negative cases cover dead init, replaced
FIFO, cancelled startup and a replaced cgroup path that blocks cleanup until fixed.
See [callback evidence](../../tests/confidential-storage/evidence/2026-10-13/kata-callback/README.md).

The [combined Kata/storage lab](kata-encrypted-lab.md) now connects that runtime
to `Agent` and `LinuxBackend`. A real unprivileged container reads/writes encrypted
`/data` and a new container reads it after a VM reboot. The test verifies busy-bind
retention, lost-release acknowledgement retry, wrong-key/invalid-manifest startup
denial, duplicate-operation handling and cleanup before runtime destruction.
Synthetic providers are still used. Sandbox/reaper/IO/RPC integration, partial
creation/destruction recovery and attested provider gates remain open.

## Process observation checkpoint

The concrete adapter now provides an identity-checked `ProcessHandle` alongside
its sole runtime owner. Real waitpid events publish retained exit status; multiple
and cancelled waiters cannot remove the process. Bounded output pumps prevent pipe
backpressure. Final destruction requires a recorded exit and completed drain,
then closes streams and removes the process. Preparation and reaping both follow
reaper-before-runtime lock ordering. See the
[process lifecycle guide](kata-process-lifecycle.md) for the API contract,
disposable-VM checks and limits. Actual Sandbox/RPC/SIGCHLD registration and
production streaming IO remain required; the activation gate is unchanged.
