# Private AA probe: prepared, not installed

This no-secret diagnostic tests the real SNP evidence path through the trusted
guest's private attestation-agent ttRPC socket. It is **not** either requested
encrypted-storage E2E. Never approve its measurement for a storage/model key.

The original CPU initrd/kernel/firmware were copied read-only and independently
matched their documented release hashes. `build.json` records the separately
built initrd digest. `runtime.json` contains only a diagnostic public key,
nonce/request hash and deliberately unreachable audience; no private key exists
in this artifact. It authorizes nothing.

Build from the hash-pinned original image and client described in
[the authority guide](../../../docs/confidential-storage/authority-https.md):

```sh
python3 scripts/confidential-storage/build-attestation-probe.py \
  --base /scratch/kata-cc.initrd \
  --client /scratch/ttrpc-aa-client \
  --runtime-data tests/confidential-storage/private-aa-probe/runtime.json \
  --output /scratch/private-aa-probe.initrd
```

The output preserves all original CPIO records byte-for-byte and adds four
entries: a root-only client, root-only fixed probe script, systemd unit and target
symlink. After AA starts, the service requests evidence for the embedded runtime
hash and emits it between console markers. There is no application-accessible
HTTP/signing/evidence endpoint. A missing private socket or failed request times
out; the application gets no capabilities, device or key to work around failure.
Treat captured evidence as platform metadata and retain it privately.

Verified locally: two independent builds are identical, the original archive
prefix is unchanged, exactly four entries were added, the client dependencies
exist in the original image, shell syntax passes and three input/archive rejection
tests pass. **The image has not booted and no real SNP quote has been accepted.**

## Proposed node change requiring approval

Automatic approval review rejected executing `install-host.sh` because it modifies
the shared production runtime configuration and reloads CRI-O. Explicit approval
has been requested. No installer command ran and none of the paths below were
created by it. Do not retry that change through another deployment mechanism.

The prepared script expects base64 of the exact image recorded in `build.json`.
It refuses an existing destination and changed baseline configuration hashes.
Its complete proposed effects are:

1. Add `/opt/kata-storage-test/private-aa-probe-94354456b0c3/` with the verified
   probe initrd and a local copy of the CPU configuration whose only change is
   its initrd path. The original file is not edited or exported.
2. Add `/etc/crio/crio.conf.d/99-kata-storage-probe` defining the distinct
   `kata-snp-storage-probe` handler. It uses the installed, unchanged shim and
   separate configuration path.
3. Restore SELinux labels on these new files, then `systemctl reload crio`.
   The installed unit reports `CanReload=yes`, using SIGHUP. The script checks
   that the CRI-O PID and both original configuration hashes stay unchanged.

A reload is not a planned restart, but it touches a shared runtime and therefore
still carries disruption risk. No RuntimeClass or probe Pod is created by this
script. After approval/install verification, create an isolated CPU probe class,
use the existing **no-secret** diagnostic InitData, run a non-root pod with no
PVC/GPU/token, inspect its private console evidence and compare production state.
The earlier dry-run was rejected because the new RuntimeClass does not yet exist.

The live model PVC/populator, KServe/GPU workloads, existing RuntimeClasses,
kernel/initrd files and production Trustee policies/keys remain unchanged.
