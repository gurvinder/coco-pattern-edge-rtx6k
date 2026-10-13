# Agent lifecycle checkpoint — 2026-10-13

**Non-TEE development evidence.** The restricted, ordinary compiler pod ran QEMU
TCG with no GPU, PVC, host device, KVM or service-account token. All 39 tools packages
matched the existing [package hashes](../kernel/packages.txt). Inputs remain Linux
`6.1.0-53-amd64`, cryptsetup `2.6.1`, QEMU `7.2.22` and Rust `1.94.1`.

| Check | Result | Evidence |
| --- | --- | --- |
| Rust | 43 library tests and 2 receipt tests passed; 2 external-fixture tests intentionally ignored | `agent-tests.log` |
| Formatting and lint | Formatting passed; Clippy warnings denied | `agent-fmt.log`, `agent-clippy.log` |
| Build | Release kernel example built | `agent-build.log` |
| Start barrier | Ready precedes application execution; exact prepare/start retries do not repeat effects | Unit tests and `initialize.log` |
| Failure handling | Prepare/attach/start errors, panic, every cleanup failure, cancellation and start/close races covered | `agent-tests.log` |
| Real storage adapter | Paused namespace created before opening; Rust-managed bind; UID 1000, zero capabilities and NoNewPrivs; root destroyed after mapping cleanup/release | `initialize.log` |
| Kernel regressions | Initialize, subsequent reopen, wrong-key refusal and corruption detection passed | `agent-summary.log`, four boot logs |
| Production preservation | Builder removed; production pods/resources, RuntimeClasses and GitOps unchanged | `before.json`, `after.json` |

The new kernel pass marker is:

```text
COCO_TEST_PASS:agent-start-barrier-real-storage-cleanup
```

The lab runtime uses an inert namespace holder and a synchronous application. It
is not a Kata container; it does not prove FIFO integration, PID/descriptor binding,
cgroup quiescence or shutdown of a long running application. Providers and signing
receipts are synthetic. No attestation, Trustee delivery, live runtime integration,
process-crash recovery or encrypted-storage E2E acceptance is claimed.

Eight tested source/lockfile hashes in `agent-source-sha256.txt` match the committed
files. The four raw boot-log hashes matched the runner summary before logs were
normalized to LF and trailing whitespace removed. `log-normalization.json` records
original/stored hashes. No keys, disks, initramfs or signing secrets were exported.

See the [agent lifecycle guide](../../../../../docs/confidential-storage/agent-lifecycle.md)
for the production adapter obligations and the single-consumer limitation.
