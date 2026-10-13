# Kata confidential filesystem transport experiment

**Development checkpoint, not an installable encrypted-storage runtime.** The
patch adds a Go runtime adapter and an agent-side preflight. Activation deliberately
returns an error until the profile-bound CDH/authority API and safe lifecycle are
implemented. There is no switch that bypasses this gate. No real key is fetched,
no disk is formatted, and no confidential filesystem is mounted by this patch.

The source pin and patch digest are in `source-lock.json`. The baseline is Manuel's
experimental Kata branch, not the installed downstream Kata 3.31 binary. Its
additional changes have not been approved for deployment. Never replace the live
shim or guest initrd with an artifact from this experiment.

## Implemented contract

The annotation `io.katacontainers.confidential-mount.v1` carries one JSON object
for one named container. It is an **untrusted request**, not authorization:

```json
{
  "schemaVersion": 1,
  "volumeId": "scratch",
  "profileDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "manifestUri": "kbs:///default/volume-records/scratch",
  "containerName": "app",
  "devicePath": "/dev/encrypted-data",
  "destination": "/data",
  "access": "rw",
  "operation": "reopen"
}
```

The digest above is synthetic. This version accepts existing ext4 filesystems:
`rw` at `/data`, or `ro` at `/models`. It rejects initialization, arbitrary paths,
unknown/duplicate fields, unsupported schemas and ambiguous device assignments.
Only virtio-SCSI transport is implemented, matching the inspected lab configuration.
An annotation on a pod must actually reach the target OCI container spec; this has
not yet been tested through the installed CRI-O admission/runtime path.

The Go adapter runs before `CreateContainerRequest` serialization. It:

1. Finds exactly one attached SCSI device matching the requested raw device path.
2. Rejects other OCI block devices and overlapping application mounts.
3. Removes that device from the agent's device list and the application's OCI
   device list, and removes positive block/wildcard device-cgroup grants.
4. Adds storage driver `confidential-scsi-v1` and an OCI filesystem bind from
   `/run/kata-containers/confidential-mounts/<container-id>/<volume-id>`.
5. Uses fixed `nosuid,nodev,noexec` and requested read-only/read-write options.

These mutations are atomic with respect to validation: rejected requests leave
OCI unchanged. Other containers and requests without the annotation are left alone.
The hypervisor still owns the encrypted raw attachment; the app-facing spec does
not. This assertion is tested on generated requests, not yet on a running guest.

The new driver name ensures an older agent fails with an unsupported-driver error.
It never falls back to an ordinary block mount or ephemeral encrypted emptyDir.

## Guest authorization boundary

The agent preflight runs before processing devices or mounting storage. It requires
`agent-policy` and `init-data` build features. The existing agent policy checks
`CreateContainerRequest` first; that policy must also restrict the workload image,
capabilities, mounts, process arguments, exec and policy mutation.

InitData must contain `data["confidential-mounts.json"]`, whose string value is:

```json
{
  "schemaVersion": 1,
  "grants": [
    {
      "volumeId": "scratch",
      "profileDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "manifestUri": "kbs:///default/volume-records/scratch",
      "containerName": "app",
      "destination": "/data",
      "access": "rw",
      "operation": "reopen"
    }
  ]
}
```

The guest compares every grant field and the actual OCI container name. The source
`devicePath` and SCSI address locate an untrusted disk; they are deliberately not
identities for granting a key. Duplicate grants for the selected volume fail.
Trustee must independently require the approved InitData digest in verified
attestation evidence. Merely loading this JSON does not verify attestation.

The guest requires a non-root application UID, no-new-privileges and empty Linux
capability sets. It also checks the exact storage path, ext4, fixed options, no host-provided
FSGroup or shared flag, the matching OCI bind and absence of exposed raw devices.
Reserved guest mount paths cannot be bound without a typed storage request.

Even a request passing those checks currently fails with:

```text
confidential filesystem activation unavailable: profile-bound authority/CDH API is required
```

The prototype `ActivateVolume` RPC takes only a device ID and manifest URI. It does
not carry or enforce the authority operation/session and requested profile digest.
Dropping those bindings to make the demo mount would defeat the intended contract.
The existing prototype also needs teardown changes: an unmount or mapping-close
failure must retain its activation record and prevent ownership release. This
patch does not pretend those fixes or the attested authority service already exist.

## Reproduce the compilation checks

Use a disposable AMD64 Linux development environment. The supplied `build-pod.yaml`
is an ordinary unprivileged compiler pod in `confidential-storage-test`, with no
service-account token, GPU, PVC or host mount. Its InitData exclusion applies only
to this non-confidential, public-source compiler; do not copy it to a workload.
The builder runs for at most four hours and should be deleted after collecting
results. The compiler image is digest-pinned; Go 1.25.11's official archive checksum
and the exact Rust compiler version are recorded in the lock.

Prepare a clean checkout at the exact source pin (requires GitHub access):

```sh
git init /path/to/kata
git -C /path/to/kata remote add origin https://github.com/manuelh-dev/kata-containers.git
git -C /path/to/kata fetch --depth=1 origin e53adb460e6040e6aa9719a735e17301462c4fa9
git -C /path/to/kata checkout --detach FETCH_HEAD
```

From the pattern repository, verify and apply the patch:

```sh
python3 scripts/confidential-storage/apply-kata-patch.py /path/to/kata --check
python3 scripts/confidential-storage/apply-kata-patch.py /path/to/kata --apply
```

Then, in the patched Kata checkout:

```sh
cd src/runtime
go test -race -count=1 -v ./pkg/confidentialmount
go test -run '^TestDoesNotExist$' ./virtcontainers
cd ../..
export RUSTUP_TOOLCHAIN=1.94.1
make -C src/agent src/version.rs
CARGO_BUILD_JOBS=4 cargo test --locked \
  -p kata-agent --no-default-features --features init-data,agent-policy \
  confidential_mount -- --nocapture
```

The second Go command checks compilation of the call site; it intentionally runs
no runtime tests. The Go and Rust parsers consume the same positive/negative JSON
fixtures. Rust tests additionally check measured-policy field substitution and
transport tampering. A deterministic protobuf fixture generated by the Go adapter
is decoded and validated by the Rust agent. These checks require neither root nor
storage privileges.
The recorded test executable hash is not a guest attestation reference value.
These tests do not establish cryptographic integrity, attestation, crash recovery,
application isolation on a real VM, or any encrypted-storage E2E acceptance gate.

## Next integration gate

Implement the CDH/authority binding and serialized guest mount lifecycle before
removing the hard activation error. The interface must bind profile, volume,
operation, activation and authenticated guest session; validate the exact copied
header and integrity parameters; retain failed cleanup state; and release ownership
only after successful unmount and mapping close. Initialization requires the
separate durable `Initializing` protocol. Build and verify a distinct guest artifact
with cryptsetup and dm-integrity before proposing any node installation.
