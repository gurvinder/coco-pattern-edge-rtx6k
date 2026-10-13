# Kata process/IO checkpoint — 2026-10-13

**Non-TEE development tests; not deployed encrypted-storage acceptance.**
The concrete runtime adapter now owns process exit reporting and legacy pipe IO
through a restricted observation facade. The test uses real rustjail-created
containers in the disposable QEMU TCG VM, with synthetic storage authorization.

| Check | Result | Evidence |
| --- | --- | --- |
| Real exit dispatch | Application exits 42; unrelated child exits 17; Kata bootstrap parent exits 0, dispatched separately | `initialize.log`, callback source assertions |
| Waiters | Two concurrent and one late waiter get 42; cancelled waiter has no effect; no status before actual exit | `rustjail-process-exit-42-concurrent-waiters-bounded-io` marker |
| Output | 131,072 stdout bytes drained; 65,536 retained with truncation reported; exact stderr marker retained; stdin closes idempotently | Same marker and `process_observations()` assertions |
| IO ownership | Newly acquired pipe inodes absent from `/proc/self/fd` after destroy while observers remain alive | Same source assertions |
| Delayed reaper | Destroy times out without fabricated status or IO closure; real exit 137 enables cleanup retry | `rustjail-delayed-reaper-retains-ownership-retry` marker |
| Existing callbacks | Five cases now verify actual exit 137 and IO closure, including prepared cancellation and refused cleanup retry | `initialize.log` |
| Encrypted backend | Six initial scenarios and one after reboot pass with concurrent owned-init reaping and final IO checks | `initialize.log`, `reopen.log`, ordering traces |
| Kernel regression | Initialize, reopen, wrong-key and corruption boots all passed | `process-vm-final.log`, four console logs |
| Rust checks | Six adapter unit tests, three repeated helper tests; 43 guest library and two receipt tests pass; two external-fixture tests intentionally ignored | `process-tests.log`, `process-guest-tests.log` |
| Build/lint | Locked release builds, strict Clippy and formatting pass | Build, Clippy and formatting logs |
| Cluster | Disposable builder removed; production comparison recorded separately | `before.json`, `after.json` |

The initial run passed exit/output/descriptor checks but its final assertion
incorrectly expected only the explicitly spawned unrelated child. It also
received the pinned rustjail temporary bootstrap parent's successful exit. The
fixture was corrected to assert both events; `initial-assertion-failure.log`
preserves the failed attempt. The full corrected run then passed all four boots.
No production runtime was involved in either attempt.

The encrypted payload still matches before and after reboot:

```text
13fb25ecbd02c7a0607db0919e6cf7892db0c35fa47fd4327683efa9447bf75c
```

Tools: Kata source `e53adb460e6040e6aa9719a735e17301462c4fa9`, Rust 1.94.1,
Linux 6.1.0-53-amd64, cryptsetup 2.6.1, QEMU 7.2.22, 2 GiB emulated RAM and
39 hash-pinned packages. The builder has no GPU, PVC, host device, KVM, capability
or service-account token. All root operations are inside the emulated VM.

`process-source-sha256.txt` matches the tested committed source. The raw console
hashes match `process-vm-final.log`; `log-normalization.json` records raw and stored
text hashes. CRLF, trailing whitespace and surplus EOF blank lines are normalized.
Only source and text logs were collected, never keys, disks or initramfs files.

The production Sandbox/RPC/SIGCHLD integration and streaming IO are not implemented.
The new output API intentionally caps capture; it is not production ReadStream.
Attested authority/CDH, partial-create/destruction recovery, guest crash recovery,
GPU storage tests and the installed downstream guest kernel remain untested.
No activation gate, Trustee policy or production workload was changed.

Guide: [process lifecycle and next integration boundary](../../../../../docs/confidential-storage/kata-process-lifecycle.md).
