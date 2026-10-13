# Initialization executor checkpoint — 2026-10-13

This is **non-TEE development evidence**, not confidential-storage E2E acceptance.
The lab used the existing pinned ordinary compiler pod, no PVC, host device, KVM,
GPU or confidential runtime. Its Debian package hashes are unchanged from
[`../kernel/packages.txt`](../kernel/packages.txt), verified by `fetch.py`.

| Test | Result | Evidence |
| --- | --- | --- |
| Authority/coordinator unit suite | 51 tests pass, including 16 failure-boundary subcases | `unit-tests.log` |
| Journal-controlled initialization and reopen | Ready, application data persisted, explicit close/reopen completed | `success/guest.log`, `success/reopen.log`, `success/record.json` |
| Worker death after each of four real operations | Initializing with final Started step survives; fresh initialization denied; retry leaves disk hash unchanged | `after-*/guest.log`, `after-*/worker.log`, `after-*/record.json`, `journal-final-summary.log` |
| Existing kernel suite | Initialize, reopen, wrong key and ciphertext corruption pass | `regression/` |
| Formatting/lint | Black, Flake8, Markdownlint and shell syntax checks pass | Reported by the development run |
| Cluster preservation | Compare production state and confirm builder absent | `before.json`, `after.json` |

Authority records contain synthetic identity hashes and fixture resource URIs.
The actual random test key, disks, initramfs and databases were not exported.
Console logs are normalized to LF without trailing whitespace/blank lines;
`log-normalization.json` preserves original and stored SHA256 values. The regression
runner summary reports the original hashes.

The process-death cases occur **after** the guest reports an operation, before
authority completion. They do not test arbitrary power loss during cryptsetup,
mkfs or integrity-journal updates. Failed volumes remain owned and unavailable;
there is no automatic recovery or reformat. The console is unauthenticated lab
wiring and the peer is synthetic. Trustee/CDH delivery, the production guest
backend, sealed-header activation and Kata integration remain unfinished.

See [the executor guide](../../../../../docs/confidential-storage/initialization-executor.md)
for reproduction and the remaining development steps.
