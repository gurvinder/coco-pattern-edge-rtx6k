# Implementation continuation

Checkpoint: 13 October 2026, branch `dev/confidential-storage-integration`, based
on proposal/handoff commit `e4a3616`. Read the [report](compatibility-report.md)
before making changes. The original six-phase handoff still defines completion.

## Resources left on the lab

- Namespace `confidential-storage-test`.
- Empty raw-block RWOP PVC `storage-audit-data`, 1 GiB, class `lvms-vg1`.
- ConfigMaps `storage-audit-probe` and `storage-audit-no-secrets`.
- No remaining test pod, Job, authority process or new runtime handler.
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

## Next engineering step

Build the guest-managed mount integration in an isolated artifact before installing
anything on the node. The current Go runtime does not construct the experimental
secure-volume metadata from the reviewed Rust runtime branch. Its agent handler
also exposes a decrypted block device, which is insufficient for the chosen
unprivileged application interface.

1. Define the exact versioned runtime/agent contract: authorized volume ID,
   profile digest, operation/activation, source device, container identity and
   destination. Host paths locate devices; measured policy and authenticated
   authority records must authorize them. Avoid inventing a Kubernetes annotation
   and treating it as trusted authorization.
2. Port only the necessary transport/activation pieces to the approved Go runtime
   baseline. Add a guest-agent filesystem mount path before container creation;
   remove raw-device exposure to the application. Implement serialized activation,
   mount ownership/refcounts, cancellation and teardown.
3. Extend the CDH profile implementation with independently authorized first-use
   initialization, exact authenticated-header handling and enforced journaled
   cryptographic integrity options. The prototype's profile name is insufficient.
4. Build a separate AMD64 guest artifact including compatible cryptsetup utilities,
   libraries and dm-integrity support. Pin all inputs and record SBOM/build hashes.
   The workstation's existing Podman VM is ARM64 with 2 GiB RAM; it has not built
   or validated the AMD64 guest components. Select a suitable isolated build
   environment; do not install build tools into the running inference guest.
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
