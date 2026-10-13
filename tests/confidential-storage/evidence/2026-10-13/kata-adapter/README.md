# Concrete Kata adapter checkpoint — 2026-10-13

**Development evidence, not a deployed Kata storage E2E.** The builder was an
ordinary restricted pod with no GPU, PVC, host device, KVM or service-account
token. QEMU TCG ran the root-only process/cgroup probe inside a disposable Linux
VM. No host cgroup was created by the probe.

| Check | Result | Evidence |
| --- | --- | --- |
| Pinned Kata compilation | Actual `LinuxContainer::start`, `exec`, `destroy` and guest `Runtime` implementation compiled against `e53adb460e6040e6aa9719a735e17301462c4fa9` | Adapter Cargo.lock, `adapter-tests.log` |
| Adapter guards | 5 distinct unit tests passed; the 3 helper tests also run in the example target | `adapter-tests.log` |
| Formatting and lint | Formatting passed; Clippy warnings denied | `adapter-fmt.log`, `adapter-clippy.log` |
| Guest regression | 43 library + 2 receipt tests passed; 2 external-fixture tests intentionally ignored | `adapter-guest-regression.log` |
| Real process/cgroup probe | Wrong-path and foreign-overmount refusal; original process retained; real descendant subtree killed; population zero and init exit observed; handles retained; removed group verified | `initialize.log` |
| Encrypted storage regression | Real coordinator/backend test plus initialize/reopen/wrong-key/corruption boots passed | `adapter-summary.log`, four boot logs |
| Cluster preservation | Builder removed; production pods/resources, RuntimeClasses and GitOps unchanged | `before.json`, `after.json` |

The kernel probe pass marker is:

```text
COCO_TEST_PASS:pinned-process-cgroup-subtree-quiescence
```

The probe owns an `unshare` child. It uses the same process guard source as the
adapter, but **does not invoke the complete Kata prepare/FIFO/destroy callback
flow**. Full runtime/reaper/IO integration, partial-create recovery, attested
providers and a deployable artifact remain unfinished. The transport activation
gate is unchanged.

An initial probe attempted to rename a cgroup directory; this kernel returned
EPERM. The final probe substitutes a foreign cgroup with a bind overmount, checks
refusal, then removes the overmount to continue. No implementation identity check
was relaxed. Initial compilation also required pinning `libc 0.2.186`, as used by
the Kata source lock, for compatibility with its `xattr 0.2.3` dependency.

All 39 tool packages matched [the existing hashes](../kernel/packages.txt): Linux
`6.1.0-53-amd64`, cryptsetup `2.6.1`, QEMU `7.2.22`; Rust `1.94.1` compiled the code.
The 13 hashes in `adapter-source-sha256.txt` record the kernel-test build inputs.
A final admission-only change then required all five capability sets to be
explicitly empty. Locked adapter tests, Clippy and formatting were rerun; the
updated `runtime.rs` hash is in `adapter-admission-source-sha256.txt`. The other
12 inputs, including the kernel helper/probe, are unchanged. These final hashes
match the committed files. The complete Kata callback flow remains untested.
All four original console-log hashes matched the runner before LF/whitespace
normalization; `log-normalization.json` records original and stored hashes.
Only text evidence was exported. No keys, disks, initramfs or signing material
were collected.

See the [adapter contract](../../../../../components/confidential-storage-kata/runtime-adapter/README.md).
