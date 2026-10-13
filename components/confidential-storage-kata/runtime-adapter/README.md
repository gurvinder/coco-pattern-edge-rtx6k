# Concrete Kata runtime adapter (development only)

This crate implements the guest library's `Runtime` callbacks using the real
`rustjail::LinuxContainer` from experimental Kata commit
`e53adb460e6040e6aa9719a735e17301462c4fa9`. Cargo pins that Git revision and the
complete resolved dependency graph. It is not installed or registered with the
Kata RPC server. The existing confidential-mount preflight still rejects activation.

The adapter owns one runtime container and one initial process for one sandbox
lifetime. It is intended to be owned by `agent_lifecycle::Agent` together with
`LinuxBackend`. No code here obtains authority receipts or keys, registers a
runtime handler, modifies the cluster, or grants privileges to the application.

## Callbacks

| Callback | Concrete operation | Refusal/retention behavior |
| --- | --- | --- |
| Constructor | Check bound container identity, Created state, unused init, matching OCI process, non-root/NNP/explicitly empty capability sets, no hooks/terminal, private mount/PID namespaces, cgroupfs and agent reaper status | Reject a prestarted container, inherited namespaces, process substitution and unsupported runtime configurations |
| Prepare | Hold Kata's `WAIT_PID_LOCKER` across `LinuxContainer::start` and descriptor capture; retain pidfd, proc directory, mount namespace, root and cgroup v2 directory | Partial create/capture failure remains uncertain; it does not authorize storage activation or claim successful cleanup |
| Start | Recheck retained process/cgroup identity; call `LinuxContainer::exec` to release its FIFO | Record Starting before the call; failure is ambiguous and cannot be retried as a fresh start |
| Quiesce | Write to the retained group's `cgroup.kill`; wait for `cgroup.events` to report `populated 0` and the init pidfd to report exit | Covers descendants; bounded five-second wait; errors preserve ownership; root/namespace stay pinned |
| Destroy | Only after coordinator storage cleanup: check quiescence, suppress rustjail's cached numeric-PID signaling, call its remaining destruction path, then verify the pinned cgroup is actually gone | Preserve uncertain state on failure; path replacement is refused; log-only cgroup deletion failure cannot become success |

The adapter temporarily removes the already-dead process map during rustjail's
normal `destroy`, then restores it. This prevents signaling recycled numeric PIDs.
Process objects remain owned for the future IO/reaper integration. The runtime's
normal lazy rootfs detach occurs only after the coordinator has removed encrypted
bind/staging mounts and mappings. It is not used to make encrypted-volume cleanup
succeed.

## Process and cgroup ownership

A pidfd alone cannot make a host-supplied PID trustworthy. Capture is valid only
for the child just created by the owned runtime, while the agent's reaper lock is
held. The constructor requires PID 1 or child-subreaper mode. Future integration
must use the same signal/reaper discipline and must not introduce another waiter
that can reap the child during capture.

The guard requires a single cgroup v2 domain membership that matches the path from
Kata's cgroup manager. It refuses the root cgroup, the service's group/ancestors,
ambiguous membership and groups initially containing other processes or child
groups. Directory traversal uses retained descriptors and `O_NOFOLLOW`; cgroup2
filesystem type is checked. Before startup/kill, the current path must still name
the retained cgroup inode. Deleting or replacing the path cannot redirect a kill
to an unrelated workload.

After startup, `cgroup.kill` covers descendant groups and fork races. Success is
not inferred from signal delivery: both population and init termination must be
observed. Namespace/root handles remain usable after process death so storage can
be unmounted first. Unsupported cgroup v1, threaded groups and missing kill support
fail closed. This is not a cgroup freeze/resume mechanism.

The trusted agent must exclude migration into/out of the group, new exec requests,
namespace changes and other container creation. An application with cgroup write
access would violate the contract. No runtime callback is an authorization API
for an application or an untrusted host.

## Build and validation

Use the [isolated compiler pod](../build-pod.yaml), Rust 1.94.1 and AMD64 Linux:

```sh
cd components/confidential-storage-kata/runtime-adapter
cargo test --locked --all-targets
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo build --locked --release --example process_lab
```

`libc` is pinned to `0.2.186`, the version in the pinned Kata source's Cargo.lock.
Its `xattr 0.2.3` dependency does not compile with `libc 0.2.190` because the latter
removed the old Linux `ENOATTR` alias. The adapter's own Cargo.lock records the
complete tested resolution; it is not a claim that every dependency matches the
upstream workspace lock.

Run the process probe only inside the disposable kernel lab:

```sh
python3 tests/confidential-storage/kernel-lab/run.py \
  --tools /build/debian/tools --work /build/adapter-kernel \
  --runtime-probe /build/components/confidential-storage-kata/runtime-adapter/target/release/examples/process_lab \
  --opener /build/components/confidential-storage-guest/target/release/examples/kernel_opener_lab
```

The probe uses a child namespace and dedicated cgroup in that emulated VM. It tests
wrong-path refusal, cgroup replacement without killing the original process,
termination of a real descendant subtree, repeat quiescence, retained handles and
verified group removal. The runner requires its explicit pass marker before
accepting the initialize boot. The existing storage regression still tests actual
LUKS2/dm-integrity initialization, reopen, wrong keys and corruption.

## Remaining integration gates

This compiles actual Kata callback calls and kernel-tests their process/cgroup
helper. It **does not run a complete rustjail container through the adapter**.
In particular, prepare/FIFO/destroy/IO callbacks have not been exercised together
in a VM. The lab probe creates its child with `unshare`, not Kata.

Before enabling the mount request:

1. Wire the coordinator into all Kata create/start/exec/remove/sandbox teardown
   paths, deferring confidential storage until after namespace creation. Keep the
   single-consumer guest rule and remove the ordinary early bind from rustjail's
   input. Port to an approved downstream source/artifact baseline.
2. Reconcile adapter ownership with Sandbox process lookup, signal reaping, exit
   reporting and IO cleanup. The object must not simultaneously live in the
   ordinary container map. Do not call prepare while holding the sandbox lock:
   Kata's SIGCHLD handler takes the reaper lock before that lock.
3. Exercise the actual `start`/FIFO/destroy callbacks and partial-create cleanup.
   A failed create before process capture currently requires recovery. A partial
   runtime destruction may also remain blocked. Neither path retries creation or
   returns fabricated success. Kata's existing FIFO open is blocking; process death
   at that boundary can stall start and needs a bounded integration test/fix.
4. Connect attested authority/CDH/device providers and the approved guest artifact.
   There is no attestation/channel adapter or production key delivery in this crate.

See [the coordinator guide](../../../docs/confidential-storage/agent-lifecycle.md)
and [test evidence](../../../tests/confidential-storage/evidence/2026-10-13/kata-adapter/README.md).
