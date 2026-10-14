# Native agent lifecycle experiment

This is a separate guest-only integration against the installed Kata 3.31.0
source baseline. It is **development code, not installed or key-approved**. It
keeps Kata's normal rustjail process, signal, cgroup and namespace lifecycle.
It supersedes the earlier adapter assumptions for this live experiment; do not
apply both patches. No Go shim, CRI-O or production runtime changes are included.

The earlier live request contained a pause container before the application,
systemd cgroups and no incoming PID namespace. The native integration explicitly
admits one trusted pause plus one measured consumer instead of routing either
through the earlier custom process manager.

Before normal device handling, the agent resolves the one SCSI attachment,
contacts the private guest storage service, removes raw block devices and
positive block/wildcard device grants, then inserts the service's read-only
`/models` bind mount. The application must have a read-only rootfs, non-root UID,
NoNewPrivs and empty capability sets. Additional block storage, namespace paths,
hooks, private-tree aliases and alternate CDI/CDH privileged helpers are denied.
All host-provided file bind mounts are removed before native mounting, including
`/etc/hosts`, passwd/group injection and termination-log mounts. `/dev/shm` is
a fresh guest tmpfs. This narrow test intentionally has no host-file consumption;
future supported file mounts need descriptor-based safe resolution, not merely
lexical prefix checks.
The final image/command/mount policy remains mandatory; this structural validator
is not a replacement for a complete workload policy.

Create/start/remove/destroy are serialized. Admission closes on removal; storage
cleanup follows native consumer destruction. An absent container after a failed
create still asks the service for cleanup. Busy mappings preserve ownership.
Exec, CopyFile, policy replacement, swap, guest clock changes, random reseeding,
resource updates, stdin writes and other mount/diagnostic mutations are rejected
in the configured storage guest. Ordinary guests without the fixed measured
configuration retain their original behavior. Only the specifically measured
storage image and strict InitData may be authorized for real keys.

At boot the agent forces private AA/CDH startup, restricts their parent directory
to 0700 and does not start the REST helper, even if host configuration requests
it. It starts the private storage service and waits for its socket before
accepting container RPCs. Existing private state cannot be silently adopted.

## Reproduce

```sh
python3 scripts/confidential-storage/prepare-native-agent.py \
  --archive /scratch/kata-containers-3.31.0.tar.gz \
  --output /scratch/native-agent
cd /scratch/native-agent/kata-containers-3.31.0
RUSTUP_TOOLCHAIN=1.94.1 make -C src/agent src/version.rs
cargo +1.94.1 fmt -p kata-agent -- --check
cargo +1.94.1 test --locked -p kata-agent --no-default-features \
  --features init-data,agent-policy storage_service::tests
cargo +1.94.1 build --locked --release -p kata-agent --no-default-features \
  --features init-data,agent-policy
```

Six tests pass, covering the actual runtime shape, independent privilege escape
attempts, mount/path aliases, sandbox hooks/modules/recreation and cleanup
admission. The source preparation is hash-locked and verified against a clean
copy. Existing downstream `unused_unsafe` warnings remain. These are compilation
and structural/lifecycle unit tests, not completed confidential storage tests.
