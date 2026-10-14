# Native agent lifecycle experiment

This is a separate guest-only integration against the installed Kata 3.31.0
source baseline. It is **development code**. The earlier single-volume build is
installed only in the isolated test handler; the current multi-volume overlay is
unit-tested and not installed.
Only the explicitly measured RO/RW test images and strict policies are authorized. It
keeps Kata's normal rustjail process, signal, cgroup and namespace lifecycle.
It supersedes the earlier adapter assumptions for this live experiment; do not
apply both patches. No Go shim, CRI-O or production runtime changes are included.

The earlier live request contained a pause container before the application,
systemd cgroups and no incoming PID namespace. The native integration explicitly
admits one trusted pause plus one measured consumer instead of routing either
through the earlier custom process manager.

Before normal device handling, the agent resolves the approved SCSI attachments by their transport paths,
contacts the private guest storage service, removes raw block devices and
positive block/wildcard device grants, then inserts the service's read-only
`/models` or read-write `/data` bind mount (version 1), or up to eight explicitly
bound mounts (version 2). The application must have a read-only rootfs, non-root UID,
NoNewPrivs and empty capability sets. Unlisted block storage, namespace paths,
host hooks, private-tree aliases and alternate privileged helpers are denied.
The opt-in GPU profile permits only bounded guest-generated NVIDIA CDI edits.
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
Exec, file-writing CopyFile, policy replacement, swap, guest clock changes, random reseeding,
resource updates, stdin writes and other mount/diagnostic mutations are rejected
in the configured storage guest. Bounded CopyFile chunks beneath the discarded
shared-file tree are acknowledged without opening, following, creating or writing
any path; this permits the existing shim to continue after file mounts were
removed. All other copy paths are rejected. Ordinary guests without the fixed measured
configuration retain their original behavior. Only the specifically measured
storage image and strict InitData may be authorized for real keys.

At boot the agent forces private AA/CDH startup, restricts their parent directory
and the attestation-agent socket directory to 0700 and does not start the REST helper, even if host configuration requests
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
  --features init-data,agent-policy storage_
cargo +1.94.1 build --locked --release -p kata-agent --no-default-features \
  --features init-data,agent-policy
```

Seventeen tests pass, covering the actual runtime shape, independent privilege escape
attempts, mount/path aliases, sandbox hooks/modules/recreation and cleanup
admission, discarded-copy bounds and exact Kubernetes label selection. The name
check uses `io.kubernetes.container.name`, matching the strict policy; CRI-O
provides a composite value in `io.kubernetes.cri-o.ContainerName`. No alternate
label is accepted as a fallback. The source preparation is hash-locked and verified against a clean
copy. Existing downstream `unused_unsafe` warnings remain. These are compilation
and structural/lifecycle unit tests, not completed confidential storage tests.

The [live RO PVC checkpoint](../../../docs/confidential-storage/native-ro-pvc.md)
passed actual key retrieval, hash verification, denied admin exec and cleanup.
A completed service denial is closed before returning to the host, while guest
networking is available; a transport error or failed cleanup retains ownership.
[Model inference](../../../docs/confidential-storage/native-model-serving.md) and
[RW service integration](../../../docs/confidential-storage/native-rw-storage.md) now pass.
The additional RW test rejects application capabilities and overlapping mounts.

Version 2 accepts one consumer with multiple model/data volumes. It rejects
missing/duplicate SCSI locators, unexpected raw devices, overlapping targets,
swapped service responses and duplicate activation sources. It publishes no app
mount until the complete response validates. Mount targets are restricted to
approved paths under `/models`, `/data` and `/checkpoints`.

**GPU candidate, not installed:** an explicit measured profile now enables bounded
NVIDIA CDI processing before storage key retrieval. Host hooks, raw block-device
access and application capabilities remain prohibited. See the
[GPU candidate guide](../../../docs/confidential-storage/gpu-guest-candidate.md)
for its exact trust boundary, tested scope, packaging and remaining hardware gates.
The NVIDIA-generated CDI file and hooks are trusted guest infrastructure; this is
not permission to inject arbitrary host-selected CDI edits. GPU hardware and
combined RO/RW inference have not yet been validated with this build.
