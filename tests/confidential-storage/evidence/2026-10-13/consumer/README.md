# Consumer namespace checkpoint — 2026-10-13

This is **non-TEE development evidence**, not Kata/confidential-storage E2E
acceptance. The restricted ordinary compiler pod ran QEMU TCG with no PVC, host
device, KVM, GPU or service-account token. All 39 package hashes match
[`../kernel/packages.txt`](../kernel/packages.txt): Linux `6.1.0-53-amd64`,
cryptsetup `2.6.1`, QEMU `7.2.22`; Rust `1.94.1` built the example.

| Test | Result | Evidence |
| --- | --- | --- |
| Rust and Clippy | 34 tests pass; two external-fixture tests ignored; Clippy warnings denied | `consumer-tests-final.log`, `consumer-clippy-final.log` |
| Descriptor inheritance | Inheritable runtime descriptors rejected | Rust unit test |
| Consumer admission | Wrong container, service namespace, non-namespace fd and duplicate attach rejected; service namespace unchanged | `initialize.log` |
| Real application bind | Rust creates/detaches the bind; UID 1000 process with zero capabilities/NoNewPrivs verifies read/write, noexec and raw/private-staging denial | `initialize.log` |
| Inherited plaintext | Namespace cloned after activation rejected; an open inherited file retains NeedsClose and blocks release until the reference closes | `initialize.log` |
| Replaced target | Foreign tmpfs overmount not removed by cleanup; ownership retained | `initialize.log` |
| Busy bind | Open file through consumer namespace retains NeedsUnmount; release denied | `initialize.log` |
| Quiescence/ordering/retry | Callback failure retains mounts; bind gone before staging/mapper cleanup and release; lost acknowledgement retried; replay refused | `initialize.log` |
| Kernel regression | Initialize, subsequent reopen, wrong key and ciphertext corruption pass | `consumer-summary-final.log`, four boot logs |
| Cluster preservation | Builder removed; production pods/resources, RuntimeClasses and GitOps unchanged | `before.json`, `after.json` |

The test provider remains synthetic. Lab root code creates the namespace holder;
`nsenter` starts only the unprivileged application for the successful bind. Shell
mount commands are used to introduce/remove the intentional foreign overmount,
not to install the successful application bind. The test waits for the application
process and retains the namespace holder while the library removes its bind.

The inherited-reference test deliberately holds an open file through the copied
staging mount. An earlier test assumption that an idle namespace alone must keep
mapper cleanup busy was false in this lab; no such guarantee is claimed.
The final test checks an actual outstanding plaintext reference.

Logs were normalized to LF with trailing whitespace/blank lines removed.
`log-normalization.json` records original/stored hashes; runner hashes refer to
original bytes. `source-sha256.txt` records six tested source/lockfile hashes, checked
against the local files. No keys, disks, private signing material or initramfs
were exported. Application payload SHA256 remains
`600e75d4d85613ca991023cb570566436355737b2a67b2e0544f057d909153db`.

See the [namespace guide](../../../../../docs/confidential-storage/consumer-namespace.md).
Production Kata callbacks, attested providers, approved guest artifact and crash
recovery remain unfinished. The hard activation gate and live runtime are unchanged.
