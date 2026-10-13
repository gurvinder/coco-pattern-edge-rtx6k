# Real Kata callback checkpoint — 2026-10-13

**Development evidence, not installed confidential-storage E2E.** Real rustjail
callbacks from pinned Kata commit `e53adb460e6040e6aa9719a735e17301462c4fa9`
ran inside disposable, non-TEE Linux VMs. The outer compiler pod had no PVC, GPU,
host device, KVM, capabilities or service-account token. No live runtime was changed.

| Check | Result | Evidence |
| --- | --- | --- |
| Real callback lifecycle | Five scenarios exercise `KataRuntime` preparation, FIFO start, cgroup quiescence and destruction | `initialize.log` |
| Application isolation | UID/GID 1000, five empty capability sets, NoNewPrivs; fixture key and raw scratch disk absent from container | `APP:` lines and callback source |
| FIFO boundary | No reader returns promptly; reader death after handshake cannot block the single-byte write; queued data, non-FIFO and symlink refused | `rustjail-fifo-no-child-nonblocking` marker |
| Runtime guards | 5 distinct adapter unit tests passed; 3 helper tests also run in example target | `callback-tests.log` |
| Guest regression | 43 library + 2 receipt tests passed; 2 external-fixture tests intentionally ignored | `callback-guest-tests.log` |
| Source checks | Locked release builds, formatting, Clippy with warnings denied | build, fmt and clippy logs |
| Encrypted storage | Existing Linux backend/coordinator regression and initialize/reopen/wrong-key/corruption boots passed | `callback-summary.log`, four boot logs |
| Cluster preservation | Builder removed; production pods/resources, RuntimeClasses and GitOps unchanged | `before.json`, `after.json` |

The actual callback scenarios are:

- **normal:** prepared application has not run; start releases it once; repeated
  start and wrong activation identity are refused; quiesce/destroy retries succeed.
- **blocked-cleanup:** a foreign cgroup overmount causes quiesce and destruction
  refusal while handles remain retained; removal permits successful cleanup.
- **dead-prepared:** killing the prepared cgroup makes startup fail; cleanup still
  removes runtime state and the cgroup without running the application.
- **replaced-fifo:** replacement is detected before startup; the original waiting
  child is quiesced and cleaned up without executing the application.
- **cancel-prepared:** removal before start kills the waiting init and removes
  runtime state without running the application.

For example, the real application reported:

```text
APP:Uid:        1000    1000    1000    1000
APP:CapInh:     0000000000000000
APP:CapPrm:     0000000000000000
APP:CapEff:     0000000000000000
APP:CapBnd:     0000000000000000
APP:CapAmb:     0000000000000000
APP:NoNewPrivs: 1
COCO_TEST_PASS:rustjail-normal
COCO_TEST_PASS:rustjail-blocked-cleanup
COCO_TEST_PASS:rustjail-dead-prepared
COCO_TEST_PASS:rustjail-replaced-fifo
COCO_TEST_PASS:rustjail-cancel-prepared
COCO_TEST_PASS:rustjail-callback-lifecycle
```

Spacing in this excerpt is expanded; the full console log retains tabs. The
callback application writes ordinary test files, **not the encrypted volume**.
The existing encrypted-backend test still uses its separate synthetic runtime.
Combining these through `Agent<LinuxBackend, KataRuntime>` is the next checkpoint.
No attestation, real Trustee/CDH provider, agent RPC/Sandbox/reaper/IO integration,
partial-create/destruction recovery or production guest artifact was validated.
The runner's general "no Kata integration" footer refers to those remaining
integration gates; the markers above specifically prove rustjail callback execution.

The first attempt exhausted the original 1 GiB VM's initramfs capacity before
callbacks ran. The callback runner now uses 2 GiB. Two subsequent attempts exposed
that the kernel's original initramfs root cannot be pivoted, even with a bind mount.
A separate tmpfs agent root fixed this without enabling `no_pivot_root`. Its first
attempt was correctly rejected because tmpfs's default root permissions were too
broad; explicit `mode=0755` fixed the fixture. The final test also isolates that
root setup in a private mount namespace. No ownership check was relaxed.

The FIFO bridge first opens a nonblocking writer against the pinned FIFO, proving
that the child has a reader. Only then does it acquire the read anchor. Reversing
this order would falsely claim readiness and can strand a late child. Both ends
remain open through Kata's single-byte write; neither anchor consumes data.
Readiness is bounded to five seconds. This is not an application-ready handshake.

All 39 packages matched [the existing pins](../kernel/packages.txt): Linux
`6.1.0-53-amd64`, cryptsetup `2.6.1`, QEMU `7.2.22`; compiler Rust `1.94.1`.
Every hash in `callback-source-sha256.txt` matches the committed tested source.
All four raw console hashes match the runner summary. `log-normalization.json`
records original and stored hashes after CRLF/trailing-whitespace normalization.
Only source, dependency lock and text evidence were exported. No key, disk,
initramfs or signing material was collected.

See the [adapter contract](../../../../../components/confidential-storage-kata/runtime-adapter/README.md).
