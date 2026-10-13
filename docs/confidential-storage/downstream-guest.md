# Downstream guest packaging and agent port

This development checkpoint targets the installed package
`kata-containers-3.31.0-5.rhaos4.22.el9`. It does not enable the encrypted PVC
E2Es. The guest-only agent patch still refuses storage activation; the live
kernel diagnostic uses the original packaged agent and a fresh RAM-backed disk.
Neither diagnostic is eligible for Trustee storage keys.

## Source and kernel provenance

The node reports that both its host shim and guest kernel belong to the above
package. Its Source0 was extracted from the matching SRPM downloaded over HTTPS
from the internal Red Hat build repository. The SRPM and source archive hashes
are pinned in `components/confidential-storage-kata/downstream-lock.json`.
The source archive is a build baseline, not proof that our new binary reproduces
the packaged prebuilt initrd agent. No host shim is built or installed by this port.
The SRPM's runtime security patches must be preserved in any future host build;
this work leaves that installed runtime unchanged.

The guest kernel is `6.12.0-211.16.1.el10_2.x86_64`. Its existing initrd includes
dm-crypt, dm-verity, dm-mod, dm-bufio, loop and ext4, but lacks dm-integrity and
its async XOR dependency closure. The matching **signed** kernel-modules-core
RPM was downloaded from:

```text
https://download.devel.redhat.com/brewroot/packages/kernel/6.12.0/211.16.1.el10_2/data/signed/release4,release2,ima/x86_64/kernel-modules-core-6.12.0-211.16.1.el10_2.x86_64.rpm
SHA256: 6b4db27a280ce40f8d9c2ecefc0c5eb4222a75c61d964ca55e35bf4700d4eed9
```

`rpm -Kv` on the node verified its existing Red Hat key `fd431d51`, header and
payload digests. This was verification only: no RPM was installed and no module
was loaded into the host. The diagnostic adds `dm-integrity`, `async_xor`,
`async_tx` and `xor` to the separate guest image. Their bytes and hashes are fixed
in the builder. The dependency order is xor → async_tx → async_xor → dm-integrity,
with the existing dm-mod/dm-bufio loaded first.

## Reproduce the agent port

Extract `kata-containers-3.31.0.tar.gz` from the pinned SRPM, then:

```sh
python3 scripts/confidential-storage/prepare-downstream-agent.py \
  --archive /scratch/kata-containers-3.31.0.tar.gz \
  --output /new/downstream-build
```

The script requires a new directory, verifies source/patch/overlay hashes,
applies only guest/workspace changes and copies the existing tested guest and
runtime-adapter sources. The reproducible patch handles the downstream main,
Sandbox, RPC and reaper differences. It does not copy the experimental host Go
adapter or unrelated experimental features. Its managed exit dispatcher reports
signal termination as `128 + signal`; this differs from the old downstream
ordinary dispatcher, which returned the signal number alone.

Build in the disposable `storage-agent-compiler` pod, using
`components/confidential-storage-kata/agent-build-pod.yaml`. It has no host mount,
PVC, GPU, service-account token, elevated capabilities or writable rootfs.
Set `RUSTUP_TOOLCHAIN=1.94.1`, `CARGO_HOME=/build/cargo` and
`RUSTUP_HOME=/build/rustup`; do not change HOME. Install the pinned toolchain
with rustup self-update disabled, then use the commands in the
[agent integration guide](kata-agent-integration.md) against the new tree.

Recorded results: eight managed lifecycle tests, twelve transport/policy tests,
one signal test, 48 guest library tests and six adapter tests pass. Three
tool-dependent guest tests are ignored by this unit invocation, as before.
Agent formatting passes; Clippy completes with two pre-existing unnecessary
unsafe warnings in `kata-sys-util::protection`. The patch itself has not been
installed or boot-tested. Its hard activation error remains intact.

## Reproduce the kernel diagnostic

Run the existing `tests/confidential-storage/kernel-lab/fetch.py` in the pinned
compiler. It verifies the recorded Debian package digests and extracts payloads
without package scripts. Then run `downstream-guest/bundle-tools.py` with that
tools directory. Its result is checked against `downstream-guest/tools-inventory.json`.
The diagnostic uses cryptsetup 2.6.1 and the already tested userspace profile.
The ELF loader/libraries live under `/opt/coco-storage-tools`, separate from the
downstream guest's libraries; fixed wrappers invoke that loader. This mixed
userspace test artifact is not a supported distribution or production release.

Place the tool tar and four modules extracted from the verified RPM in a scratch
directory, then:

```sh
python3 tests/confidential-storage/downstream-guest/build.py \
  --base /scratch/kata-cc.initrd --packages /scratch/packages \
  --output /scratch/storage-kernel.initrd
```

The builder refuses existing outputs, altered inputs, duplicate paths and
overwrites of files in the original archive. It adds a root-only diagnostic
service with no public API. The service formats only a newly created 128 MiB
file inside its own tmpfs, using a fresh random key generated in the guest.
There is no PVC, KBS request, model key or persistent disk in this test.

For later iterations, `--overlay-output /scratch/additions.cpio.gz` emits an
additions-only archive and constructs the full image as the exact base plus that
archive. Linux supports [concatenated compressed initramfs archives](https://docs.kernel.org/driver-api/early-userspace/buffer-format.html).
Transfer only the additions, concatenate with the hash-checked base already on
the node, and verify the **full image** hash before switching the isolated test
configuration. Never overwrite the production base or change an active guest.

The diagnostic checks module loading, LUKS2 formatting, wrong-key refusal,
journaled 4096-byte HMAC integrity, ext4 write/close/reopen and cleanup. Require
`STORAGE_KERNEL_PROBE_PASS` in the guest console; application exit status alone
does not prove the service passed. The unprivileged app separately checks UID,
capabilities and absence of raw storage/AA access.

## Remaining integration

The installed agent's existing trusted-storage hook is an ephemeral SecureMount
path with `sourceType=empty`; it also returns success when CDH is unavailable.
It must not be reused unchanged for persistent RO/RW storage. Its placement before
ordinary mounts is useful, but the existing verified receipts, header/profile
checks, retained lifecycle and initialization journal must control activation.

Next, connect the private authority/KBS providers and measured configuration to
the agent. Standard raw block transport may let the host shim remain unchanged:
the agent must resolve the attachment, remove the application's raw device and
device-cgroup grants, authenticate its identity/profile, then provide only the
filesystem bind. That path is not implemented by this checkpoint. Finally run
the actual populated Qwen PVC and blank RW PVC acceptance matrices; neither has
yet been mounted by a confidential workload.

The captured no-key live RPC requests also expose assumptions that the adapter
must handle before activation: CRI-O creates a root-owned sandbox/pause container
before the UID-1000 app; incoming specs have IPC/UTS/mount namespaces but no PID
namespace entry, and use a systemd-form cgroup path. The current registry excludes
all ordinary containers, and the adapter requires a private PID namespace and
non-systemd cgroups. Do not disable these checks to make a demo run. Implement
and verify a narrowly admitted, measured sandbox process plus guest-owned app
namespace/cgroup handling, or use an equivalent validated native agent lifecycle.
Arbitrary extra containers must remain excluded while plaintext storage exists.

## Live result

13 October 2026, 23:25 UTC: the corrected diagnostic printed all six PASS markers,
including `STORAGE_KERNEL_PROBE_PASS`, on the isolated SNP runtime. The first
attempt reached formatting, wrong-key denial and verified integrity, then failed
because `sync` was absent from the minimal guest. Calling the bundled BusyBox
fixed that harness error; the second run completed write/reopen and teardown.
No PVC or real key was involved in either attempt. The second artifact and its
small overlay rebuilt byte-for-byte. CRI-O remained PID 694089, and the production
runtime-file hashes and full workload/GitOps audit remained unchanged.

Public logs, tool/module provenance, source-port tests and build hashes are in
[`downstream-guest/`](../../tests/confidential-storage/evidence/2026-10-13/downstream-guest/).
The current test artifact's SHA-256 is
`d3f34830e55bd65fd7993311046ff87751cc9afdd8fa7d6619bd38c48f1611cf`.
Its hardware measurement has not been authorized for storage keys. This result
is kernel compatibility evidence, not a Trustee key-release or PVC E2E result.
