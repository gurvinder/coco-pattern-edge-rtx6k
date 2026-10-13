# Implementation continuation

Checkpoint: 13 October 2026, branch `dev/confidential-storage-integration`, based
on proposal/handoff commit `e4a3616`. Read the [report](compatibility-report.md)
before making changes. The original six-phase handoff still defines completion.

## Resources left on the lab

- Namespace `confidential-storage-test`.
- Empty raw-block RWOP PVC `storage-audit-data`, 1 GiB, class `lvms-vg1`.
- ConfigMaps `storage-audit-probe` and `storage-audit-no-secrets`.
- No remaining test pod, Job, authority process or new runtime handler. The
  temporary AMD64 compiler pod used for the transport checkpoint was removed.
- No production key, Trustee resource policy, reference value, runtime artifact,
  KServe resource, live GitOps branch, GPU allocation or route changed.

The earlier copied InitData ConfigMaps were deleted. The final public-image
diagnostic contacts no production Trustee endpoint. Do not reuse its policy for
secret-bearing workloads. Test keys and initialized encrypted disks do not exist.

## Code and checks

Implemented the opt-in diagnostic chart, no-secret InitData generator, sanitized
read-only inventory/comparison tool, and SQLite authority transition core with
no network listener. Sixteen authority tests and five diagnostic deployment tests
pass. Targeted Helm lint/render and server admission were exercised. Actual raw
device access failed in the unprivileged guest, as captured in the evidence.
The core is not integrated with an attestation verifier, Trustee or guest service.

The first repository-wide preview/schema attempts timed out during the utility
image download. After the download completed, the retry reached the actual checks:
`preview-all` fails because this checkout has no such make target; `validate-schema`
fails for the existing baremetal/hub/spoke values because `global.hardware.profile`
is missing when the shared override path is rendered. These are not passing checks.
The new isolated chart's targeted lint/render/tests pass. No unrelated values or
the read-only `common/` subtree were changed.

## Runtime/agent transport checkpoint

The [Kata experiment](../../components/confidential-storage-kata/README.md) now
contains a hash-pinned patch for `e53adb460e6040e6aa9719a735e17301462c4fa9` of
Manuel's experimental Kata source. This is **not** a match for the installed
Kata 3.31 downstream source and is not an approved runtime replacement.

Implemented the Go adapter, versioned JSON contract, shared parser fixtures,
Go-to-Rust protobuf interoperability check and guest preflight against measured
InitData grants. The adapter removes the selected raw block device from the
application configuration and substitutes a filesystem bind request. The guest
checks container identity, profile/destination/access/operation, unprivileged
process settings, fixed mount options and raw-device/alias rejection.

Seven Go tests (including race instrumentation and 42 subcases), compilation of
the actual runtime call site, and 12 Rust agent tests passed on isolated AMD64
hardware. The patch was checked and applied to a clean checkout at the pinned
SHA. See [test evidence](../../tests/confidential-storage/evidence/2026-10-13/transport).
The compiler pod had no GPU, PVC, host access or service-account token. The final
inventory confirms production pods, resource specifications, RuntimeClasses and
GitOps sources were unchanged.

**Activation remains deliberately unavailable**, even when validation succeeds.
The current CDH prototype API cannot enforce the requested profile and authority
session binding. No persistent filesystem mount handler, new key delivery,
initialization, teardown integration or deployable guest artifact was completed.
This checkpoint is transport/security-preflight code, not an encrypted-storage E2E.

## Next engineering step

Build the guest-managed mount integration in an isolated artifact before installing
anything on the node. The installed Go runtime still lacks the new adapter. The
patched experimental agent rejects the new request before device handling and storage activation.
Do not remove that hard gate to reuse the prototype raw-device handler.

1. Extend the tested versioned contract with authenticated authority receipts:
   authorized volume ID, profile digest, operation/activation, source device, container identity and
   destination. Host paths locate devices; measured policy and authenticated
   authority records must authorize them. Avoid inventing a Kubernetes annotation
   and treating it as trusted authorization.
2. Port the tested transport patch to the approved downstream Go runtime
   baseline once its exact source is located. Add a guest-agent filesystem mount
   path before container creation;
   remove raw-device exposure to the application. Implement serialized activation,
   mount ownership/refcounts, cancellation and teardown.
3. Extend the CDH profile implementation with independently authorized first-use
   initialization, exact authenticated-header handling and enforced journaled
   cryptographic integrity options. The prototype's profile name is insufficient.
4. Build a separate AMD64 guest artifact including compatible cryptsetup utilities,
   libraries and dm-integrity support. Pin all inputs and record SBOM/build hashes.
   The isolated AMD64 compiler has now built and tested the patched agent source,
   but no guest initrd or CDH integration artifact exists. Recreate the disposable
   builder for the next build; do not install tools into the inference guest.
5. Add the attested transport/verifier around the authority core and test receipts
   before enabling key delivery. Keep the authority outside the workload storage
   rollback domain. Existing initialization failures require conservative recovery,
   not another format grant.
6. Register a distinct test shim/config/initrd and RuntimeClass only after artifact
   tests. Confirm CRI-O reload behavior, package ownership and recovery before any
   node change. If isolation requires replacing shared files or interrupting the
   server, prepare the exact maintenance change for approval.
7. Run RW01–RW09/SEC/OPS against disposable synthetic data, then RO/publisher/
   populator gates. Both GPUs currently remain occupied, so GPU integration must
   wait for capacity or an approved change to an existing workload.

Do not reinterpret the authority unit tests or the PVC's `Bound` status as evidence
of confidential storage. No acceptance-matrix encrypted-storage E2E gate has passed.
