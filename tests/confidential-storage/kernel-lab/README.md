# Disposable Linux storage lab

This is a **non-TEE integration test**, not a guest storage service. It exercises
real Linux device-mapper and cryptsetup without granting the outer pod privileges,
mounting a host device, allocating a PVC or using KVM. QEMU TCG emulates a separate
Linux guest. Root in that disposable guest manages storage; a UID 1000 process
with no capabilities consumes the mounted filesystem.

## What the four boots test

| Boot | Operation | Required result |
| --- | --- | --- |
| `initialize` | Format a new 128 MiB scratch file as LUKS2 with AES-XTS and HMAC-SHA256 integrity; create ext4; write as UID 1000 | Journaled integrity target, application write succeeds, raw-device reads denied, clean unmount and both mappings removed |
| `reopen` | Reopen the same disk with the same test key, without formatting; read the whole mapped device; mount ext4 | All sectors readable, application payload and SHA256 unchanged, clean teardown |
| `wrong-key` | Attempt opening the same disk with a fresh incorrect key | Open fails and neither mapper target exists; no formatting fallback |
| `corruption` | Copy the good disk and flip one byte at physical offset 64 MiB; open and read without mounting | Read fails and the kernel reports `INTEGRITY AEAD ERROR`; clean teardown |

`run.py` requires a **new** work directory. `build.py` creates the only disk used
for initialization; it does not accept an existing disk path. The guest additionally
checks the lab command-line guard, fixed disk size and synthetic device serial.
Never install this `/init` in a workload guest or copy its direct format path into
the production service. These guards prevent accidental lab misuse; they are not
authenticated authority grants.

The guest requires the effective dm-integrity table to have 32-byte tags,
4096-byte blocks and mode `J`. It rejects discard, recalculation and `internal_hash`
options. The upper dm-crypt target provides keyed authentication tags. This is
sector authentication, **not rollback protection**. The LUKS metadata still declares
`journal_encryption: none` and `journal_integrity: none`; do not describe this test
as proving authenticated journal metadata or crash-safe recovery.

## Reproduce

Use the pinned ordinary compiler pod in
[`build-pod.yaml`](../../../components/confidential-storage-kata/build-pod.yaml),
or an equivalent AMD64 Debian 12 environment. The pod has no host devices, host
mounts, elevated capabilities, Kubernetes token, GPU or confidential runtime.
The test consumes about 1 GiB guest RAM plus initramfs/build memory and several
GiB of temporary files. No inference workload changes are needed.

Prepare an unpacked Debian tools tree, without installing packages into the host:

`python3 tests/confidential-storage/kernel-lab/fetch.py --work /build/debian`
automates the pinned download/hash verification/extraction below. Then pass
`--tools /build/debian/tools` to the runners. It requires a fresh work directory.

1. Create private package, tools and apt-state directories under `/build`.
2. Run `apt-get update` with `Dir::State::lists` and `Dir::Cache` pointing to those
   writable directories. Keep normal Debian repository signature checks enabled.
3. Download each exact `Package=Version` from
   [`packages.txt`](../evidence/2026-10-13/kernel/packages.txt) using
   `apt-get download`. Verify every `.deb` against its recorded SHA256 before
   extraction. A different version or hash is a new test input requiring review.
4. Extract each verified package's `data.tar.xz` with `ar` and `tar` into the tools
   tree. This does not run maintainer scripts. The recorded packages provide the
   kernel, QEMU, BusyBox, cryptsetup, device-mapper, ext4 and capability utilities.
   Remaining shared libraries come from the pinned compiler image; the builder
   uses `ldd` to copy dependencies and fails on unresolved libraries.
5. From the repository root, run:

```bash
python3 tests/confidential-storage/kernel-lab/run.py \
  --tools /build/tools --work /build/kernel-suite
```

Each boot is bounded to ten minutes. Success requires both a zero QEMU exit status
and the expected guest pass marker, with no fail marker. A guest failure may reboot
with QEMU status zero, so the marker checks are necessary. If a run fails, inspect
its log; rerun the suite only with a **new** work directory and fresh scratch disk.

The initramfs contains an ephemeral random test key. **Never publish the initramfs,
its root tree, scratch disk or corrupted copy.** Delete the disposable builder after
collecting reviewed text logs. No Trustee or production credential is used.

To also verify that the actual generated header satisfies the Rust profile:

```bash
cd components/confidential-storage-guest
COCO_TEST_KERNEL_LAB_DISK=/build/kernel-suite/scratch.raw \
  cargo test --locked --lib \
  profile::tests::kernel_generated_integrity_header_matches_profile \
  -- --ignored --exact
```

That test signs a synthetic local grant, authenticates the complete header and
manifest, validates the profile, and prepares a sealed memfd. It also checks that
a changed keyslot-area byte fails header authentication. The shell lab itself uses
a detached header copy in guest tmpfs; it does **not** integrate that sealed Rust
descriptor into cryptsetup activation.

The optional `--opener` runner argument now tests the Rust primitive during the
initialize boot instead. Build `kernel_opener_lab` and pass its release executable;
see [the opener guide](../../../docs/confidential-storage/guest-opener.md) for the
exact commands. That path exercises the actual sealed descriptor, wrong-key and
collision refusal, busy-close state, and removal of both mapper targets. The
remaining three boots retain the original regression harness. This option is
separate from journal-controlled execution and cannot be combined with it.
The same example now also exercises the
[guest mount lifecycle backend](../../../docs/confidential-storage/guest-mount-backend.md)
with synthetic providers, a namespace consumer, busy unmount and release retry.
The [consumer manager](../../../docs/confidential-storage/consumer-namespace.md)
now installs/removes that bind through namespace/root descriptors. Tests reject
inherited plaintext staging aliases and retain ownership while a bind is busy or
covered by an unrelated mount.

## Recorded result and limits

On 2026-10-13 all four boots passed with Linux `6.1.0-53-amd64`, cryptsetup `2.6.1`
and QEMU `7.2.22`. The payload SHA256 on both successful mounts was:

```text
600e75d4d85613ca991023cb570566436355737b2a67b2e0544f057d909153db
```

The modified disk produced:

```text
device-mapper: crypt: dm-0: INTEGRITY AEAD ERROR, sector 95680
COCO_TEST_PASS:corruption
```

See [evidence](../evidence/2026-10-13/kernel/). Console logs have CRLF and trailing
whitespace normalized for Git; `log-normalization.json` records original and stored
hashes. The runner summary contains the original hashes. The real-header regression
passed, as did 24 regular Rust tests and Clippy. The builder was deleted; the final
audit found production pod identities, readiness, restarts, resource specifications,
RuntimeClasses and GitOps sources unchanged. This does not establish any of the
following:

- Attestation, Trustee/CDH delivery, or first-use authority journal integration.
- Kata agent mounting or hiding device nodes in a container's mount namespace.
  The application test checks Unix device permissions and zero capabilities in
  the test guest; it is a process, not a Kata application container.
- Power-loss consistency, journal/header tampering, interrupted initialization,
  service cancellation, hostile device substitution, or replay/rollback rejection.
- Compatibility with the installed RHEL guest kernel or a production guest initrd.
- Any encrypted-storage end-to-end acceptance gate.

Journal-controlled lab execution is covered below. Next, implement the production
guest backend using the sealed-header/lifecycle code and test partial operations
and recovery. A real attested authority/CDH channel and an isolated runtime
artifact are still required before enabling the Kata activation path. Keep its
fail-closed gate in place.

References: [QEMU microvm](https://www.qemu.org/docs/master/system/i386/microvm.html)
and [kernel dm-integrity documentation](https://docs.kernel.org/admin-guide/device-mapper/dm-integrity.html).

## Journal-controlled operations

`journal.py` connects the authority's initialization coordinator to the same guest
operations through an explicitly unauthenticated lab-only console protocol. It
tests success/reopen plus process death after each of the four real operations,
before completion acknowledgement. A new coordinator must refuse initialization,
with no additional disk change. The authority database survives outside the VM.

See [the executor guide](../../../docs/confidential-storage/initialization-executor.md)
for the exact boundaries, invocation and production limitations. `coco.journal=1`
is exclusively a test-guest switch, never an attestation or production authorization
mechanism. Both runners require new scratch files and never reuse user volumes.

The example also drives the [trusted-agent coordinator](../../../docs/confidential-storage/agent-lifecycle.md)
with the actual Linux backend. A lab runtime creates its namespace before storage,
starts a synchronous unprivileged application only after attachment, and destroys
the holder only after storage cleanup/release. It does not exercise Kata's FIFO or
cgroup implementation, and no production runtime is installed.
