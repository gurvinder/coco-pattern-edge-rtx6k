# RO model approval and kernel validation

These are **non-TEE library/kernel tests**, not live confidential storage E2E.
The real published Qwen ciphertext disk was used in snapshot-only software VMs.
The original disk digest is unchanged. Production inventory comparison passed.

- `authority-tests.log`: 97 authority/attestation-transport fixture tests.
- `ro-build-tests.log`: 48 Rust unit and 2 receipt tests; strict Clippy; RO normal/corrupt phases.
- `storage-ro-header-test.log`: explicit authenticated-header test on the actual disk.
- `ro-normal.log`: all model hashes with UID 1000/zero capabilities, RO access and teardown.
- `ro-corrupt.log`: modified ciphertext yields EIO from the verified plaintext mapping.
- `rw-regression.log` / `kernel-markers.log`: actual RW format/write/reopen/wrong-key/corruption regression.
- `after.json`: sanitized production comparison, unchanged.

Signing/attestation peers and key providers are synthetic. No real Trustee
storage key was released. No deployment runtime artifact was installed. Public
receipt fixtures include only verification keys; model keys and initrds stay private.
