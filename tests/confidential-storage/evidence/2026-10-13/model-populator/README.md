# Real encrypted Qwen publication and population

This checkpoint exercised the actual OpenShift 4.22.15 cluster and internal
registry in `confidential-storage-test`. **It is not a confidential workload E2E.**

Validated:

- Fetched Qwen/Qwen3-0.6B revision
  `c1899de289a04d12100db370d81485cdf75e47ca` and verified all pinned input hashes.
- Built a 2 GiB LUKS2 disk in an isolated, non-TEE software publisher VM. The
  outer pod had no PVC, GPU, host device, elevated capabilities or KVM.
- Built dm-verity over 2,113,929,216 bytes covering the complete LUKS header and
  ciphertext. Reopened verity → read-only LUKS → ext4, verified every model file
  and refused a write. The console result is in `publisher.log`.
- Published the ciphertext artifact to
  `confidential-storage-test/qwen-luks-e2e`; immutable digests are in `source.json`.
- Actual CSI prime PVC creation, raw-device import, hash/length verification,
  flush/readback, importer removal, PV rebind and prime cleanup passed.
- The populated claim `qwen-luks-model-v1` remained Bound to the same PV through
  a test controller restart, without another importer or prime PVC.
- A separate source with a deliberately wrong disk digest failed with
  `model population failed: ciphertext disk digest mismatch`. Its target remained
  Pending. The failed importer, prime PVC, target and source were then removed.
- Server-side dry-run rejected mutation of the source spec. Both controller and
  importer service accounts were denied `get secrets` by the cluster.
- Unchanged upstream library tests, the same-namespace event/reconcile regression,
  Go importer/controller tests with race instrumentation, publisher/importer
  cross-language archive test, three Python publisher tests, Helm lint and actual
  server admission passed.
- The audit comparison reported `production_unchanged: true` for production pod
  identities/readiness/restarts, resource specifications, RuntimeClasses and
  GitOps sources. No live runtime, KServe resource, GPU allocation or Trustee
  key/policy was changed.

The importer was UID 0 for its assigned raw block device, but `privileged: false`,
with all capabilities dropped, no escalation and a read-only root filesystem.
It is isolated infrastructure; no application has been granted storage privileges.

The original library ignored both informer events and reconciliation for targets
in its working namespace. The local opt-in fix covers both; original upstream
tests are unchanged. `build-and-admission.json` records the successful third
controller build. Earlier builds did not populate a disk and were replaced only
in the test namespace.

Remaining: approve/sign and freshly authorize the guest manifest, deliver the
test key through real attestation, open and serve this model inside the confidential
guest, and initialize/reopen a separate writable PVC through trusted guest code.
`guest-manifest.unsigned.json` is public publisher output, not a signed grant.
Keys, tokens, plaintext, scratch disks and key-bearing initramfs files are absent
from this evidence directory. The prepared ciphertext exists in the registry/PVC.
