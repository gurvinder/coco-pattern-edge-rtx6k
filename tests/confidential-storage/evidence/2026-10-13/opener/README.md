# Guest opener checkpoint — 2026-10-13

This is **non-TEE development evidence**, not confidential-storage E2E acceptance.
The ordinary compiler pod ran QEMU TCG without a PVC, host device, KVM, GPU,
service-account token or confidential runtime. Package inputs match
[`../kernel/packages.txt`](../kernel/packages.txt): Linux `6.1.0-53-amd64`,
cryptsetup `2.6.1`, QEMU `7.2.22`; Rust `1.94.1` built the guest example.

| Test | Result | Evidence |
| --- | --- | --- |
| Rust unit/interoperability suite | 31 pass, two external-fixture tests intentionally ignored | `opener-tests-final.log` |
| Clippy and release example | Pass, warnings denied | `opener-clippy.log`, `opener-build-final.log` |
| Wrong key and retry | NeedsClose retained; same-handle retry refused | `initialize.log` |
| Sealed header | Changing an active encrypted keyslot byte rejects fresh preparation; retained sealed header still opens | `initialize.log` |
| Mapping collision | Second handle cannot open over or remove existing mapping | `initialize.log` |
| Application permissions | UID 1000, no capabilities, no new privileges; filesystem writable, existing mapper node unreadable | `initialize.log` |
| Busy close and cleanup | Mounted close fails and retains state; after unmount both mappings removed | `initialize.log` |
| Four-boot regression | Initialize, subsequent reopen, wrong key and ciphertext corruption pass | `opener-final-summary.log`, four boot logs |
| Cluster preservation | Production state unchanged; builder removed | `before.json`, `after.json` |

The Rust example uses synthetic signatures and an ephemeral random key. The
existing harness formats a new scratch image; the opener cannot format. Mounts
in this example are lab commands, not the unfinished production lifecycle backend.
Application permissions were tested on a process, not a Kata container namespace.
Subsequent boots use the existing shell harness against the same disk and key.

Only reviewed text logs and sanitized cluster inventories were exported. Console
logs are normalized to LF without trailing whitespace/blank lines;
`log-normalization.json` records original and stored SHA256 values. Runner hashes
refer to original logs. Keys, disks and initramfs were not exported.

See [the opener guide](../../../../../docs/confidential-storage/guest-opener.md).
The device resolver, lifecycle mount integration, attested authority/CDH key
delivery, service hardening and approved guest artifact remain unfinished.
The Kata activation gate remains closed; no inference runtime changed.
