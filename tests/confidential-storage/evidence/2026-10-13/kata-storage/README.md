# Combined real Kata and encrypted storage checkpoint — 2026-10-13

**Non-TEE development integration, not deployed confidential-storage acceptance.**
`Agent`, real `KataRuntime` callbacks and `LinuxBackend` now run together in a
disposable QEMU TCG VM. The application uses the actual encrypted filesystem.
Authorization and key delivery remain synthetic lab providers.

| Check | Result | Evidence |
| --- | --- | --- |
| Combined lifecycle | Six initialize scenarios and a seventh after VM reboot passed | `initialize.log`, `reopen.log` |
| Unprivileged app | UID/GID 1000, all capabilities empty, NoNewPrivs; no raw disk/mapper/key/staging in root | `APP:encrypted-*` lines, test source assertions |
| Mount isolation | One ext4 mount at `/data`; rw/nodev/nosuid/noexec; copied executable denied | Application assertions in `examples/callback_lab/storage.rs` |
| Persistence | Write, new-container read and post-reboot read match the expected SHA256 | Application markers in both logs |
| Busy bind | Quiescence succeeds; cleanup retains mappings and denies restart/release until a test-held file closes | `ORDER:encrypted-busy` |
| Lost release reply | Storage already gone; runtime retained; release retry precedes destruction | `ORDER:encrypted-release-retry` |
| Wrong key | One key request, no app start, cleanup succeeds | `ORDER:encrypted-wrong-key` |
| Invalid manifest | Zero key requests, no app start, cleanup succeeds | `ORDER:encrypted-bad-manifest` |
| Duplicate operations | One prepare/start/key fetch on successful exact retries; Closed record rejects restart/reuse | Source assertions |
| Teardown | Cgroup population zero before detach; bind/staging/mappings/raw descriptor absent before release; destruction last | Runtime/provider assertions and ordering traces |
| Prior regressions | Real FIFO/cgroup callbacks, Linux storage backend, initialize/reopen/wrong-key/corruption passed | `storage-summary.log`, four console logs |
| Unit checks | 5 adapter tests (3 repeated in helper target), 43 guest library + 2 receipt tests; 2 external-fixture tests intentionally ignored | `storage-tests.log`, `storage-guest-tests.log` |
| Tool checks | Locked release builds, formatting and Clippy with warnings denied passed | build, fmt and clippy logs |
| Cluster | Builder removed; production pods/resources, RuntimeClasses and GitOps unchanged | `before.json`, `after.json` |

The same application payload hash was observed before and after reboot:

```text
13fb25ecbd02c7a0607db0919e6cf7892db0c35fa47fd4327683efa9447bf75c
```

The normal trace was:

```text
prepared -> resolve -> key -> started -> quiesced -> detach-authorized -> release-attempt -> released -> destroyed
```

The wrong-key trace has no `started` event; the bad-manifest trace has neither
`key` nor `started`. Busy cleanup logs two detach authorizations before release.
The lost-acknowledgement case logs two release attempts before `released` and
`destroyed`. Runtime wrappers delegate every effect to the real adapter; provider
checks require its quiescence result plus actual cgroup population zero.

The combined test passed on its first run. A final run added raw-device-descriptor
release and explicit operation-count assertions, rebuilt the formatted source,
and passed again. These files record that final run. The seven scenarios do not
exercise production agent RPCs, attested authority/CDH, crash/power-loss recovery,
partial-create/destruction recovery, or the installed RHEL guest kernel.
The application remains a simple synthetic file workload, not vLLM/GPU inference.
No activation gate or production Trustee policy was changed.

Tools: pinned Kata `e53adb460e6040e6aa9719a735e17301462c4fa9`, Rust `1.94.1`,
Linux `6.1.0-53-amd64`, cryptsetup `2.6.1`, QEMU `7.2.22`. All 39 packages matched
[the existing package lock](../kernel/packages.txt). The callback VM has 2 GiB RAM.
The restricted compiler pod had no GPU, PVC, host device, KVM, capability or
service-account token. Root operations were confined to its emulated VM.

All source hashes in `storage-source-sha256.txt` match the committed source.
The four raw console hashes match `storage-summary.log`; original and normalized
text hashes are in `log-normalization.json`. Only source, lock files and text
logs were exported. No key, disk, initramfs or signing material was collected.

Reproduction and next steps: [combined lab guide](../../../../../docs/confidential-storage/kata-encrypted-lab.md).
