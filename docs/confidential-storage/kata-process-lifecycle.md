# Kata process ownership, exit reporting and IO — development adapter

The adapter owns the `LinuxContainer` and its init `Process` exactly once. An
identity-checked `ProcessHandle` exposes exit observations, bounded output,
stdin closure and IO cleanup status without returning mutable runtime objects.
The storage coordinator retains exclusive control of prepare/start/quiesce/destroy.

This is a library and disposable-VM checkpoint, **not a deployed Kata agent**.
A separate [agent integration patch](kata-agent-integration.md) now wires the
actual Sandbox map, RPC handlers and SIGCHLD loop; it is not deployed.
The experimental activation gate remains closed; no attested provider is added.

## Ownership and lock ordering

Before handing `KataRuntime` to `Agent`, obtain its process handle using the same
bound `Identity`. A future Sandbox entry must retain the coordinator plus this
handle instead of also inserting the container into `Sandbox.containers`.
Do not clone, move or reconstruct a second `LinuxContainer` for process lookup.

The lock order is `WAIT_PID_LOCKER` followed by runtime state, or by the ordinary
Sandbox lock for unmanaged children. Preparation now acquires the shared reaper
lock **before** runtime state and holds it through child creation, Process
publication and descriptor capture. Never call preparation or acquire `Reaper`
while holding the Sandbox/runtime lock.

`Reaper::next_exit()` obtains real nonblocking `waitpid(-1, WNOHANG | __WALL)`
results. A `ChildExit` cannot be constructed by a caller or outlive its `Reaper`
lock guard. While that guard remains held, dispatch the event through
`ProcessHandle::observe`; an unmatched event must go to its ordinary process
owner. Normal exits retain their code; signal exits use `128 + signal`.
Exited tombstones no longer match a subsequently reused numeric PID.

The test also uses `Reaper::poll_owned()` to reap only the adapter-owned init,
leaving synchronous test helper commands to their own waiters. This is a harness
integration mode. It cannot safely coexist with an unmodified `waitpid(-1)` loop
that consumes and discards managed exits. Production needs one unified dispatcher.

## Exit is separate from storage cleanup

| Event | Effect | What remains owned |
| --- | --- | --- |
| Process exits | Reaper records the actual status and notifies watchers | Process object, IO, namespace/root, storage lifecycle |
| `wait()` completes | Returns the same retained code to every waiter | All ownership remains; cancellation only drops that waiter |
| Runtime quiesces | Confirms cgroup population zero and pidfd exit | Root/namespace remain available for storage detach |
| Storage closes | Removes bind/staging/mappings; acknowledges authority release | Runtime remains until destruction succeeds |
| Runtime destroys | Waits for recorded exit and drained output, closes process streams, destroys runtime, verifies cgroup removal | Immutable exit/output observations remain through retained handles |

A successful `wait()` does not mean descendants have stopped, storage is detached,
or the authority lease can be released. The existing quiescence checks and
storage ordering still apply. A destroyed process retains its status for late
and concurrent waiters without another `waitpid` or mutable process lookup.

Destruction releases runtime state before waiting for the reaper; otherwise the
reaper could never acquire it to publish the exit. Exit and output waits each
have a five-second deadline. Expiry returns an error and retains the process and
runtime for cleanup retry. It does not invent an exit code, discard IO ownership,
or re-run creation. The coordinator's existing service-owned task protects these
effects from an RPC waiter being cancelled.

## IO scope and limitations

This checkpoint supports nonterminal legacy pipes only. The constructor rejects
passfd/vsock `ProcessIo` and extra files; production streaming IO is still a gate.
Pass a fresh, exclusively owned `Process::new` without externally cloned stream
handles. The adapter registers both output pipes with rustjail's stream owners,
starts independent drains, and preserves at most **64 KiB per stream** while
counting all bytes. Excess output is drained and discarded, avoiding pipe
backpressure and unbounded retention. `Output::truncated()` explicitly reports it.
This capture interface must not silently replace production `ReadStream` semantics.

`close_stdin()` drops the owned stdin writer and is idempotent. The pumps hold no
runtime lock while reading. The reaper publishes exit without waiting for output
under the global reaper lock. After quiescence and successful drain, destruction
calls rustjail stream cleanup and removes the process only after runtime cleanup
succeeds. Failed destruction retains state. The pinned upstream `start()` has
already closed the child-side `Stdio` descriptors; their stale numeric fields are
cleared rather than closed again, which could close unrelated reused descriptors.

Captured output can contain application data. The adapter does not print or
export it automatically. The synthetic tests check known data and byte counts;
production access to stdout/stderr still needs measured policy and authenticated
RPC/channel handling. Guest crash recovery, failed creation before guarded
capture and every partial upstream destruction boundary remain unimplemented.

## Disposable VM checks

The callback lab exercises real rustjail containers and the same storage backend:

- A UID 1000 process waits for stdin EOF, emits 131,072 stdout bytes and a stderr
  marker, then exits 42. Two concurrent waiters and a later waiter get 42; cancelling
  another waiter has no effect. Only the first 65,536 stdout bytes are retained.
- The unified reaper dispatches an unrelated child exit 17 and Kata's temporary
  bootstrap parent exit 0 separately. Concurrent preparation and reaping complete
  with the corrected lock order.
- After runtime destruction, `/proc/self/fd` no longer contains any of the newly
  acquired pipe inodes, even while the observer and namespace handles remain.
- With the reaper deliberately delayed, destruction refuses to finish and retains
  runtime/IO. Publishing the real SIGKILL exit 137 enables cleanup retry.
- The existing five callback scenarios and seven encrypted storage scenarios
  assert SIGKILL status 137 and completed IO closure after teardown. Storage
  regressions still cover reboot persistence, busy mounts, lost release replies,
  wrong keys and invalid manifests.

Use the unchanged [kernel-lab commands](kata-encrypted-lab.md#reproduce-in-the-isolated-builder).
The runner requires the process/IO and delayed-reaper pass markers in the initialize
boot in addition to the encrypted-storage markers.

## Next integration boundary

The [agent patch](kata-agent-integration.md) provides source-level
Sandbox/RPC/SIGCHLD routing on the experimental baseline. Port it to the approved
downstream baseline and exercise the complete agent with real storage providers. Managed IDs must resolve to the coordinator and observation
handle; ordinary create/exec/remove/teardown paths must not bypass that ownership.
Reaper dispatch must preserve existing handling for unmanaged and helper children.
Replace the bounded development capture with authenticated production IO semantics
and add tests for partial creation/destruction and channel cancellation. Keep
activation disabled until the attested authority/CDH bindings, artifact and isolated
runtime deployment are reviewed and tested.
