# Kata agent ownership and RPC integration — development patch

The cumulative `confidential-agent-v1.patch` connects the tested storage
coordinator/process facade to the **actual Kata agent source**: `Sandbox`, RPC
helpers and the SIGCHLD dispatcher. It includes the earlier Go transport and
Rust preflight patch, and adds the guest libraries to the pinned Kata workspace.
It is not installed on the cluster and does not enable confidential storage.

The source remains the experimental Kata commit
`e53adb460e6040e6aa9719a735e17301462c4fa9`, not the installed downstream runtime.
The preflight still returns:

```text
confidential filesystem activation unavailable: profile-bound authority/CDH API is required
```

## What is wired

`Sandbox.managed_storage` holds a separate registry of managed entries. Each
entry owns an erased coordinator interface; the concrete implementation delegates
to `Agent<B, KataRuntime>` and its `ProcessHandle`. The `LinuxContainer` never
also enters the ordinary container map. Entries remain after failed cleanup and
as closed tombstones. This MVP reserves the guest for one managed owner for its
remaining lifetime, including after removal; IDs and the guest are not recycled.

| Agent path | Managed behavior |
| --- | --- |
| CreateContainer | Existing measured-policy/preflight checks remain; activation still fails. Ordinary creation is blocked once a managed entry is reserved or shutdown begins |
| Trusted guest installation seam | Requires an already verified grant and matching guest session; excludes ordinary containers, records the owner, then invokes coordinator preparation |
| StartContainer | Routes to coordinator start; refuses starts after removal begins or Sandbox shutdown starts |
| WaitProcess | Routes to retained process status, accepting only the owned init ID; concurrent and late waits do not remove the process |
| RemoveContainer | Routes to coordinator close. Failure, timeout and caller cancellation retain ownership; retry resumes cleanup. Successful closure leaves a tombstone |
| CloseStdin / empty WriteStream | Routes to the owned init's stdin closure after existing RPC policy checks |
| ExecProcess | Refused for the reserved guest; cannot create a process around the coordinator |
| ReadStream / nonempty WriteStream | Explicitly unavailable for managed entries. The bounded development capture is not exposed as production streaming IO |
| Signal, TTY, cgroup update/pause/resume/stats | No managed mutable Process/LinuxContainer is returned through ordinary lookups; these operations remain unsupported |
| DestroySandbox | Marks shutdown, closes managed entries outside the Sandbox lock, then runs ordinary destruction and signals shutdown. Failure retains entries and the shutdown sender |
| Guest paths/mount mutation | CreateSandbox, ephemeral mount updates, stale-share removal, CopyFile and swap additions are rejected for a reserved managed guest or teardown |
| SIGCHLD | One reaper dispatches real exit events to managed observers first, then ordinary process bookkeeping; unknown bootstrap/helper exits are consumed once |

The guest-only `install()` function is **not called by a host RPC**. It is the
future provider integration seam, not an attestation implementation. No annotation
becomes a `VerifiedGrant`. There is no test-provider build switch or runtime flag
that opens activation. Routing tests insert test owners only under `cfg(test)`.

## Concurrency and retained cleanup

An operation gate serializes admission, namespace-changing operations and teardown.
Callers clone it under the Sandbox lock, release that lock, then acquire the gate.
The ordering is operation gate, reaper exclusion, then Sandbox/runtime state;
paths that do not need all locks skip them. The reaper never needs the operation
gate. Ordinary create/exec now also exclude the reaper before locking Sandbox and
creating/publishing their child, matching the managed adapter's ordering.

Managed start and close run in service-owned tasks with a per-entry operation lock.
An RPC deadline or a disconnected caller stops waiting; it does not abandon an
ongoing storage operation. Once close begins, restart stays denied even when close
fails. Only a successful coordinator close marks the entry Closed. The concrete
coordinator still enforces quiescence, bind/staging/mapping removal and authority
release before runtime destruction.

Wait and reaper observation do not take that operation lock. DestroySandbox releases
the Sandbox lock before awaiting managed close, allowing SIGCHLD to publish an exit
needed by cleanup. The ordinary `Sandbox::destroy` itself refuses to run while a
managed entry is not Closed. A failed shutdown leaves admission closed and can be
retried; it does not notify the agent to exit prematurely.

The integrated dependency graph uses the **same workspace rustjail instance** for
both the agent and the adapter. A second Git-derived rustjail would provide a
separate `WAIT_PID_LOCKER` and different runtime types, invalidating this contract.
The preparation script rewrites only the adapter's three local dependency paths;
all inputs and transformed outputs are hash-locked. The workspace lock adds the
two local packages without upgrading existing dependencies.

## Reproduce

Start with a clean checkout of the pinned source. Use this cumulative patch
instead of first applying `confidential-mount-v1.patch`:

```sh
python3 scripts/confidential-storage/apply-kata-agent.py /path/to/kata --check
python3 scripts/confidential-storage/apply-kata-agent.py /path/to/kata --apply
```

The script verifies the source commit, clean tree, patch digest and every copied
library file. Existing overlay directories or modified inputs are refused before
patching. It copies only the listed source files, with local dependency rewrites;
no target directories or standalone crate lockfiles are copied. A failed/partial
preparation requires a fresh checkout. `agent-integration-lock.json` records the
cumulative patch and overlay hashes.

Build only in the existing [isolated compiler](../../components/confidential-storage-kata/build-pod.yaml)
or another disposable AMD64 Linux environment. In the compiler, prepare a writable
rustup home and then run:

```sh
cp -a /usr/local/rustup /build/rustup
export RUSTUP_HOME=/build/rustup RUSTUP_TOOLCHAIN=1.94.1 CARGO_BUILD_JOBS=4
rustup component add rustfmt clippy
cd /path/to/kata
make -C src/agent src/version.rs
cargo test --locked -p kata-agent --no-default-features \
  --features init-data,agent-policy managed_storage -- --nocapture --test-threads=1
cargo test --locked -p kata-agent --no-default-features \
  --features init-data,agent-policy confidential_mount -- --nocapture --test-threads=1
cargo test --locked -p kata-agent --no-default-features \
  --features init-data,agent-policy signal::tests -- --nocapture --test-threads=1
cargo test --locked -p confidential-storage-guest -p confidential-storage-kata-adapter --lib
cargo clippy --locked -p kata-agent --no-default-features \
  --features init-data,agent-policy --all-targets -- -D warnings
cargo fmt -p kata-agent --check
cargo check --locked -p kata-agent --no-default-features
cargo tree --locked -p kata-agent -i rustjail
```

The agent tests use actual RPC helpers, Sandbox state and SIGCHLD dispatch, with
in-memory test owners. Real OS children exercise managed exit 37, ordinary exit 23
and unknown helper handling. Other tests cover route selection, conflicting
ownership, wrong exec IDs, unsupported operations, repeated waits/removal,
cleanup failure, restart refusal, cancellation, timeout and teardown ordering.
The tests do not open a ttrpc/vsock connection or run policy evaluation against a
real host request. The existing policy calls remain at the RPC boundaries.

## Remaining activation gates

This patch closes the source-level ownership/routing gap. It does **not** complete
an activated confidential-storage guest:

1. Implement and authenticate authority/CDH/device providers. Bind the verified
   guest session, policy/profile, volume, operation and activation; do not derive
   authorization from transport annotations.
2. Connect authorized CreateContainer preparation to the trusted installation
   seam, including rootfs/device/resource ownership and deferred encrypted binds.
   Generic rootfs/shared-mount bookkeeping and all partial-create/destruction
   recovery boundaries still need integration tests. Do not fall back to the
   ordinary storage-before-namespace path or log-and-forget cleanup.
3. Implement policy-controlled streaming IO and the required managed process and
   cgroup RPC semantics. Current explicit refusals are intentional limitations.
4. Run the patched agent with real `KataRuntime` and `LinuxBackend` inside the
   disposable VM, including serialized host RPCs and failure injection. The prior
   real-Kata encrypted-volume VM evidence covers the libraries, not this full
   patched agent path. Test crash recovery separately.
5. Port/review against the approved downstream source and build a separately
   measured guest artifact before proposing isolated node deployment.

KServe/Qwen, GPU assignments, runtime configuration, Trustee policy and the live
GitOps branch remain untouched. This checkpoint is not an encrypted-storage
production acceptance result.
