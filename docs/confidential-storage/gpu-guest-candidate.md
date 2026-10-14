# GPU and storage guest candidate

**14 October 2026: source, packaging and concrete configuration prepared; not installed.**
The candidate adds an explicit NVIDIA path to the previously storage-only agent.
The current Qwen/KServe runtime, Trustee policies, model PVC and retained RW state
are unchanged. This checkpoint does not establish GPU inference from a LUKS PVC.

## Reused installed components

The [base inventory](../../tests/confidential-storage/gpu-guest/base-inventory.json)
records the exact public GPU initrd copied from `9c-63-c0-dd-06-52`, verified against
its host SHA-256 before inspection. It contains NVIDIA 595.58.03, AA and CDH,
Kata agent and kernel modules for dm-crypt and dm-verity. dm-integrity is absent.
The kernel is `6.12.0-211.16.1.el10_2.x86_64`; the existing tested matching signed
module bundle supplies dm-integrity and its xor/async dependencies.

`nvidia-cdi.service` runs before `kata-agent.service`. Its existing guest script
loads the driver, starts persistence, generates `/var/run/cdi/nvidia.yaml` and
sets the GPU ready state. The candidate preserves that script, NVIDIA tools,
modules and AA/CDH binaries byte-for-byte. Ready state alone is not attestation;
key release still requires the approved CPU+GPU appraisals.

The installed runtime uses cold-plug VFIO and `guest-kernel` mode. The inspected
Kata 3.31 source strips host VFIO nodes from the application OCI specification and
adds `cdi.k8s.io/vfioN: nvidia.com/gpu=0` for a single GPU. Read-only CRI inspection
confirmed Qwen's host VFIO device and absence of host hooks. **That inspection is
not a capture of the guest's generated CDI file.** Exact live CDI compatibility
remains an acceptance test.

## Agent changes and trust boundary

The separate root-owned 0600 `/etc/confidential-storage/gpu-profile` contains
exactly `nvidia-single-gpu-guest-cdi/v1` followed by a newline. It is embedded in
the measured image, not selected by an annotation. Its absence keeps GPU requests
disabled. GPU mode requires the multi-volume configuration schema.

The consumer create path is:

1. Remove host bind mounts and supply private guest `/dev/shm` as before. Reject
   host hooks, privileged processes, extra containers and overlapping mounts.
2. Require exactly one GPU CDI selector, index 0. Optional transport metadata must
   describe one `vfio-pci-gk` device; user-space VFIO, extra devices, wildcard CDI
   selection and arbitrary PCI locators are rejected.
3. Independently validate all approved SCSI block attachments. Resolve their guest
   nodes and reserve the consumer before asynchronous operations.
4. Remove raw block nodes and all host positive device-cgroup grants from the
   application specification. Preserve attachment identities only in trusted code.
5. Read only the root-owned, non-writable guest `nvidia.yaml` from the dedicated
   CDI directory. Reject additional CDI files. Copy its bounded bytes into a new
   root-private cache under `/run/coco`; invoke the existing Kata CDI injector
   against that cache. The host cannot supply arbitrary CDI edits or cache paths.
6. Check the result: only bounded NVIDIA character nodes and matching explicit
   character-device grants; only read-only NVIDIA/CUDA library or tool file
   mounts; no changed process identity, capabilities, arguments or unrelated OCI
   fields. NVIDIA's measured `nvidia-cdi-hook` is allowed only in its container
   creation/start stages. Host-provided hooks remain prohibited. This hook runs
   as trusted guest infrastructure; it does not grant the app CAP_SYS_ADMIN.
7. The private storage service performs fresh authority authentication and
   permitted CDH key retrieval, then verifies/opens RO storage and initializes or
   reopens approved RW storage. Publish all mounts only after the full response
   validates. Continue Kata's normal process lifecycle and existing cleanup.

The NVIDIA generator and hook are part of the measured guest TCB. This patch is
not a general CDI sandbox. The initial file-mount allowlist intentionally excludes
writable mounts, whole library directories, sockets, private service paths and
graphics configuration. If the real generated spec needs additional entries,
inspect and test them explicitly; do not broaden to arbitrary CDI mounts/hooks.

## Packaging

The existing native builder now supports `--gpu`. It requires the exact installed
GPU base digest, the v2 binding collection, `authority.trustee_rcar: true`, and an
explicit `private-cdh` resource/credential-encoding map. It adds the fixed GPU
profile. Legacy CPU packaging remains available without this switch.

Build the hash-locked native agent with `init-data,agent-policy`, and build the
private service with `trustee-rcar`, as described in the
[standard Trustee guide](standard-trustee-adapter.md). Then:

```sh
python3 tests/confidential-storage/native-guest/build.py --gpu \
  --base /scratch/installed-gpu.initrd \
  --agent /scratch/kata-agent --agent-sha256 REVIEWED_AGENT_SHA256 \
  --service /scratch/confidential-storage-service --service-sha256 REVIEWED_SERVICE_SHA256 \
  --client /scratch/storage-aa-client \
  --config /scratch/approved-public-service.json \
  --packages /scratch/matching-storage-packages \
  --overlay /scratch/gpu-storage.cpio.gz --output /scratch/gpu-storage.initrd
```

This is an assembly command, not installation or approval. The only replaced base
path is `/usr/bin/kata-agent`; other overlay paths must be new. Binaries, tools,
modules and the baseline are hash-checked. AA/CDH/NVIDIA stay in the base. The
builder reports `approvedForStorageKeys: false`; generate independent measurements
and exact policies for a real candidate before authorizing resources.

The committed [packaging fixture](../../tests/confidential-storage/gpu-guest/packaging-fixture.json)
has an unreachable `.invalid` authority, empty CA, fake public receipt verifier
and invalid manifest. **It cannot run the E2E and must never be approved for keys.**
It only tests deterministic assembly without embedding credentials. Replace it
with approved public configuration generated from actual volume contracts.

## Validation results

The [validation record](../../tests/confidential-storage/evidence/2026-10-14/gpu-candidate/validation.json),
[build inputs and outputs](../../tests/confidential-storage/evidence/2026-10-14/gpu-candidate/build.json)
and [assembly checks](../../tests/confidential-storage/evidence/2026-10-14/gpu-candidate/checks.json)
record 17 passing native agent tests, four packaging admission tests, both release
builds and clean hash-locked source preparation. Two GPU assemblies were
byte-identical. Rebuilding the previous CPU v12 image with its original inputs
also reproduced its original bytes exactly. Required ELF libraries are present
in the GPU base; that is not a substitute for a guest boot test.

The fixture image SHA-256 is
`9abfd0b6ebfa53dcbddf5bcad21bfe32cff03ebba64c778232806228fe1665e8`.
It is an assembly-test identity, **not an approved attestation reference**.
The repository-wide checks retain their existing baseline failures: no
`preview-all` Make target and missing bare-metal `global.hardware.profile` during
schema rendering. No chart/value configuration was changed in this checkpoint.

## Concrete candidate preparation

The [two-volume candidate guide](../../tests/confidential-storage/gpu-guest/README.md)
now provides a bounded preparer, strict policy, unprivileged vLLM startup, Helm
resources and tests. The existing populated model PVC is bound by UID. A separate
new 1 GiB Block/RWOP claim `qwen-gpu-data-v1` and an internal authority Service
have been created in `confidential-storage-test`; the authority and guest have
not been deployed. Private preparation produced real TLS/receipt pins and a new
authority database without changing older state or provisioning any storage key.

The user has dedicated the encrypted-file demo's GPU permanently to the second
Qwen/KServe instance, with a different chatbot port. This supersedes the earlier
plan to borrow and restore that GPU. The existing Qwen/KServe/chatbot remain the
baseline to preserve. GitOps retirement and hardware activation are still pending.

## Remaining acceptance work

- Deploy the prepared authority with durable state and finish exact standard
  Trustee resource policy/key provisioning. Preserve the retained RO key and RW
  history; initialization is approved only for the new claim, still subject to
  the guest zero-device check. Validate the expected EAR issuer/policy IDs live.
- Verify the release binaries' runtime dependencies and the combined guest boot.
- Validate the actual guest CDI output, NVIDIA readiness and unprivileged CUDA.
- Complete standard Trustee composite `/attest`, signed request proof, private
  CDH key retrieval and denial tests with genuine CPU+GPU evidence.
- Serve the local encrypted Qwen model through the RO mount and verify durable
  `/data`, then repeat from a fresh confidential VM and exercise failure cleanup.
- Finally exercise OCI population for a new model PVC and the chatbot handover.

Both GPUs were occupied during preparation. No production workload was stopped,
no CRI-O reload/restart was performed and no new runtime handler was installed.
