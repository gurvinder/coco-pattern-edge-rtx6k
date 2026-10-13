# Real Kata containers with encrypted RW storage — development lab

The disposable kernel lab now connects `Agent`, `KataRuntime` and `LinuxBackend`
in one process. A real rustjail-created application reads and writes an ext4
filesystem protected by LUKS2, dm-crypt and journaled dm-integrity. The application
needs no storage privileges, raw device, key or privileged sidecar.

This is **non-TEE integration evidence**. Synthetic signed receipts and a random
fixture key replace the attested authority/CDH providers. No Kata RPC server,
OpenShift runtime, Trustee policy or production workload is changed. The activation
gate remains closed.

## Tested lifecycle

1. The harness creates a new 128 MiB scratch disk and random key. Its trusted init
   performs the existing explicit LUKS2/integrity/ext4 initialization. This is not
   application-side formatting or a fallback after failed unlock.
2. The test constructs a fresh signed grant that binds the real header digest,
   manifest, fixed profile, activation and container name. `Agent` reserves it.
3. `KataRuntime` creates the real root/mount/PID namespaces and retains the init
   behind its FIFO. The test checks that no mapping exists before preparation.
4. `LinuxBackend` resolves the scratch device, authenticates the manifest/header,
   obtains the fixture key, opens the verified kernel stack and mounts privately.
5. The consumer manager attaches the filesystem at `/data` through the retained
   namespace/root descriptors. Only then does the agent allow Kata startup.
6. The UID/GID 1000 application checks zero capabilities and NoNewPrivs, writes or
   reads `kata-probe`, synchronizes it, verifies its contents and reports its hash.
   It sees one ext4 mount, at `/data`, with `rw,nodev,nosuid,noexec`. Executing a
   copied binary from that mount fails. The raw disk, mapper directory, fixture
   key and private staging path are absent from its root.
7. Close kills the application's cgroup and waits for actual quiescence. Only then
   can the backend remove the consumer bind, staging mount and both mappings.
   Release checks that the raw-device descriptor is gone and plaintext is no
   longer reachable through the retained root. Runtime destruction follows the
   acknowledged release.
8. A separate VM boot opens the same encrypted disk and starts a new Kata
   container that verifies the persisted file through the same lifecycle.

The test's `ObservedRuntime` delegates every runtime effect to `KataRuntime` and
records ordering assertions. Its provider quiescence check requires both the
successful runtime callback and `cgroup.events` population zero; it never returns
unconditional success. The lab now runs a concurrent, lock-ordered process reaper.
Its [process facade](kata-process-lifecycle.md) checks real exit codes, repeated
waiters and IO cleanup. These hooks do not enable production RPCs or authorization.

## Cases and acceptance checks

| Case | Required result |
| --- | --- |
| Write | No application execution before attachment; one start despite exact prepare/start retries; expected payload hash |
| Read | New container reopens the file without replacing or reformatting the volume |
| Reboot | New VM and new Kata container read the same persisted file |
| Busy mount | External test file reference keeps cleanup in `NeedsStorageClose`; application is stopped, mapping retained, release withheld; closing the reference allows cleanup |
| Lost release acknowledgement | Bind, staging and mappings already gone; runtime remains retained and restart denied; retry acknowledges release before destruction |
| Wrong key | No application startup; one key request; failed mapping is cleaned and runtime destroyed |
| Invalid manifest | No application startup and no key request; retained runtime is quiesced and cleaned |

Every final Closed record rejects startup and activation reuse. The test verifies
one runtime preparation, the expected number of starts/key requests/releases,
absence of remaining mappings/cgroups and destruction last in the event trace.
The earlier FIFO, cgroup replacement, wrong-key and corruption regressions also run.
The busy reference is deliberately held by test code, not granted to the app.

Expected SHA256 for `kata-encrypted-persistent-v1` followed by a newline:

```text
13fb25ecbd02c7a0607db0919e6cf7892db0c35fa47fd4327683efa9447bf75c
```

## Reproduce in the isolated builder

Use the existing [restricted builder](../../components/confidential-storage-kata/build-pod.yaml)
and copy only the source, package lock and kernel-lab scripts into `/build`.
Build on AMD64 Linux with Rust 1.94.1:

```sh
cd /build/components/confidential-storage-kata/runtime-adapter
cargo build --locked --release --examples
cargo test --locked --all-targets
cargo clippy --locked --all-targets -- -D warnings
cargo fmt --check

cd /build/components/confidential-storage-guest
cargo build --locked --release --example kernel_opener_lab
cargo test --locked

cd /build
python3 tests/confidential-storage/kernel-lab/fetch.py --work /build/debian
python3 tests/confidential-storage/kernel-lab/run.py \
  --tools /build/debian/tools --work /build/kata-encrypted-test \
  --runtime-probe /build/components/confidential-storage-kata/runtime-adapter/target/release/examples/process_lab \
  --callback-probe /build/components/confidential-storage-kata/runtime-adapter/target/release/examples/callback_lab \
  --opener /build/components/confidential-storage-guest/target/release/examples/kernel_opener_lab
```

Both fetch/work directories must be new. Follow the existing builder procedure
for a writable rustup home if installing rustfmt/Clippy. The runner uses QEMU TCG,
2 GiB RAM and hash-pinned Debian tools; it needs no host privileges or KVM.
It requires the encrypted-storage pass marker on both initialize and reopen boots.
Never run either callback mode on the cluster node or a production guest.

The callback program handles the `init` re-exec before starting Tokio. Its trusted
lab bootstrap uses a private mount namespace and tmpfs agent root; it makes the
fixture key available only to that agent root. Application roots are separate.
No changes to the consumer privilege model or the cryptographic profile were
needed to combine the existing runtime and backend implementations.

Export only source, source hashes and normalized text logs. Do not export the
initramfs, scratch disk, key or temporary signing material. Delete the builder and
compare the production inventory against its baseline afterward.

## Remaining work

The next implementation boundary is ownership of real Kata Sandbox/process state:
route create/start/exec/remove and sandbox teardown through the coordinator;
connect the tested process facade to the production SIGCHLD loop and streaming IO
without racing PID capture or duplicating container ownership. Add partial-create/destruction recovery tests. Keep the
single-consumer guest restriction and prohibit ordinary entry points from bypassing
it or inheriting private staging mounts.

Attested authority/CDH/device providers, the approved downstream Kata baseline,
artifact measurements, isolated runtime deployment and storage acceptance gates
remain separate requirements. These tests do not prove replay protection,
power-loss recovery, GPU attestation or compatibility with the installed RHEL
kernel. No encrypted-storage production E2E acceptance gate is marked complete.

Source: [combined test](../../components/confidential-storage-kata/runtime-adapter/examples/callback_lab/storage.rs).
Results: [recorded evidence](../../tests/confidential-storage/evidence/2026-10-13/kata-storage/README.md).
