# Guest mount backend checkpoint — 2026-10-13

This is **non-TEE development evidence**, not confidential-storage E2E acceptance.
The ordinary restricted compiler pod ran QEMU TCG with no PVC, host device, KVM,
GPU, service-account token or confidential runtime. All 39 Debian package hashes
match [`../kernel/packages.txt`](../kernel/packages.txt): Linux `6.1.0-53-amd64`,
cryptsetup `2.6.1`, QEMU `7.2.22`; Rust `1.94.1` built the example.

| Test | Result | Evidence |
| --- | --- | --- |
| Rust suite | 33 tests pass; two external-fixture tests intentionally ignored | `mount-tests-final.log` |
| Clippy/release example | Pass; warnings denied | `mount-clippy-final.log`, `mount-build-final.log` |
| Private mount root | Unsafe permissions and shared propagation refused; existing symlink/directory not adopted | `initialize.log` |
| Authentication order | Changed manifest fails before key provider invocation | `initialize.log` |
| Failed open | Wrong key cleaned up before release; closed activation cannot replay | `initialize.log` |
| Application process | Separate mount namespace, UID 1000, zero capabilities, NoNewPrivs; filesystem writable, execution denied, raw devices and private staging inaccessible | `initialize.log` |
| Detach failure | Staging remains mounted; no authority release | `initialize.log` |
| Busy unmount | Open application file keeps NeedsUnmount and mapping ownership; no authority release | `initialize.log` |
| Release ordering/retry | Release called only with target and mappings absent and raw-device descriptor count restored; lost acknowledgement retains NeedsRelease; retry closes once and tombstone prevents replay | `initialize.log` |
| Four-boot regression | Initialize, subsequent reopen, wrong-key and ciphertext corruption rejection pass | `mount-summary-final.log`, four boot logs |
| Production preservation | Builder removed; production resources, pod state, RuntimeClasses and GitOps sources unchanged | `before.json`, `after.json` |

The lifecycle backend performs real mount/unmount and opener calls. Device/key/
release providers use synthetic local fixtures. The application namespace setup
is lab-only root code; the process is then unprivileged. The sole consumer exits
and is waited for before cleanup; this is not the production Kata bind manager.
The first boot also reruns the earlier opener's header mutation, collision and
busy-close cases. Subsequent boots use the shell regression harness on the same
disk/key. Application payload SHA256 remains
`600e75d4d85613ca991023cb570566436355737b2a67b2e0544f057d909153db`.

Only reviewed text logs and sanitized inventories were exported. Logs are
normalized to LF without trailing whitespace/blank lines. Original and stored
SHA256 values are in `log-normalization.json`; runner hashes refer to original
logs. No keys, scratch disks, private signing material or initramfs were exported.
`source-sha256.txt` records the tested backend/example sources, lockfile and
application permission probe; all six hashes were checked against the local files.

See [the backend guide](../../../../../docs/confidential-storage/guest-mount-backend.md).
Production providers, Kata namespace integration, crash recovery, blocked-I/O
handling and the approved guest artifact remain unfinished. No live runtime gate
or Trustee policy changed.
