# Kata agent ownership integration evidence — 13 October 2026

This checkpoint tests the cumulative development patch against experimental Kata
commit `e53adb460e6040e6aa9719a735e17301462c4fa9`. It connects actual agent
Sandbox/RPC-helper/SIGCHLD paths to managed ownership. Activation remains disabled;
no patched runtime or guest image was installed. See the
[integration guide](../../../../../docs/confidential-storage/kata-agent-integration.md)
for the design, reproduction commands and remaining gates.

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Agent managed routing | 8 passed | `agent-routing-tests.log` |
| Transport and measured-policy preflight | 12 passed | `agent-preflight-tests.log` |
| Existing signal status tests | 2 passed | `agent-signal-tests.log` |
| Guest library | 43 passed; 2 external-fixture tests intentionally ignored | `agent-library-tests.log` |
| Runtime adapter library | 6 passed | `agent-library-tests.log` |
| Strict agent Clippy, all targets | Passed with `-D warnings` | `agent-clippy-final.log` |
| Agent formatting | Passed; successful command produced no output | `agent-fmt.log` |
| Agent without optional features | Compiled | `agent-no-features.log` |
| Shared reaper lock dependency | One local workspace rustjail instance | `agent-rustjail-tree.log` |
| Fresh checkout reproduction and negative preparation checks | Passed | `preparation-checks.json` |
| Production comparison and builder cleanup | All four comparisons true; no test pods | `before.json`, `after.json` |

The routing tests exercise actual agent helpers with in-memory managed owners.
Real OS child exits cover managed exit 37, ordinary exit 23 and an unknown helper.
Concurrent wait and Sandbox teardown exercise SIGCHLD without a Sandbox-lock
deadlock. Other cases cover cancellation, removal timeout, retained failed cleanup,
restart refusal, wrong IDs, conflicting owners and late/repeated waits.

These tests do not exercise a serialized ttrpc/vsock request, real attestation or
an encrypted volume through the patched agent. Prior disposable-VM evidence tests
the concrete runtime and encrypted backend as libraries; it is a separate result.
The guest-only installation seam is not called by CreateContainer. Production
streaming IO, authenticated providers and full resource lifecycle integration
remain outstanding.

## Source and tooling

The isolated, unprivileged `storage-compiler` pod used Rust 1.94.1 on Linux AMD64
with `init-data,agent-policy`. `agent-source-sha256.txt` records the tested source;
the preparation script reproduced every recorded hash from a clean pinned checkout.
The cumulative patch and 29 overlay input/output hashes are locked in
`components/confidential-storage-kata/agent-integration-lock.json`.

Preparation checks reject modified overlay input, a modified patch, a dirty target
and repeat application, without overwriting the checkout. Python checks passed
with Black 25.1.0 and Flake8 7.2.0 (88-character line limit).

`agent-clippy.log` preserves the initial lint failure: three existing preflight
`map_or` expressions needed their equivalent modern forms. The final Clippy log
records success after that correction. Cargo's warning that a member package's
profile is ignored is retained; the workspace profile controls the build.
`agent-version-final.log` records successful generation of the version source.

`agent-binaries.json` contains only test executable hashes and sizes, not binaries
or approved guest measurements. Logs have normalized line endings and whitespace;
`log-normalization.json` records their original and stored hashes. No keys, private
credentials, VM disks, initramfs images or executable artifacts are included.

## Cluster isolation

The read-only final audit reports unchanged production pods, production resources,
runtime definitions and GitOps sources. The disposable compiler was removed and
`test_pods` is empty. KServe/Qwen and the live deployment branch were not changed.
This comparison is a configuration/pod preservation check, not a new inference or
attestation acceptance test.
