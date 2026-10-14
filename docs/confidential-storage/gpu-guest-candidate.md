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
resources and tests. The existing populated model PVC is bound by UID. The new
1 GiB Block/RWOP claim `qwen-gpu-data-v1` and internal authority Service are in
`confidential-storage-test`.

The encrypted-file demo has been permanently retired in GitOps commit
`da6e4a9e6b250d18db30062217b5651b399ba86e`. Its GPU is allocated to the second
Qwen/KServe instance. The original Qwen/KServe and chatbot retain their pod UIDs
and remain ready. Port 30081 is the intended second chatbot port; it is not yet
exposed.

The candidate RuntimeClass aliases the existing isolated `kata-snp-storage-probe`
handler. Only that test handler's configuration was switched to a hash-named
NVIDIA guest image, with previous configurations retained. The production GPU
configuration is unchanged; CRI-O was neither reloaded nor restarted. The live
QEMU image/kernel/firmware hashes, eight-vCPU EPYC-v4 configuration and exact
command line match the independently calculated v14 reference; the historical
v12 comparison is retained separately.

During diagnostic hashing, reading the whole 214 MB initrd into memory caused
the VFIO-manager helper container to be OOMKilled at 11:08:23 UTC and restart
once. It recovered; subsequent hashing uses 1 MiB chunks. The original Qwen
and chatbot kept their UIDs, readiness and zero restarts; both HTTP endpoints
returned 200 after recovery.

Guest bring-up admitted the exact non-root process policy and attached both block
PVCs. Real NVIDIA CDI output required bounded compatibility handling: normalize
readonly regular-file binds, omit the private persistence socket/daemon, pinned
firmware files and display modesetting device. Unknown mounts, block devices,
broad device grants and privilege changes still fail closed. The fixed guest libcuda environment addition is admitted. All 23 native tests
and the real Regorus policy tests pass. Live GPU CDI admission now succeeds;
private storage activation is denied and cleanup completes. No application has
started and no GPU storage E2E is claimed.

The authority image passed all 131 tests inside the built image. Its deployment
uses `restricted-v2`, namespace UID 1001100000, no capabilities, no service-account
token and a read-only container root. Vault/ESO supplies its TLS and receipt keys.
Its two-volume database is retained on `storage-gpu-authority-state` at
`/state/retained/authority.db`. Missing state prevents startup rather than creating
a fresh database. A pinned-TLS RCAR challenge against the standard pattern Trustee
passed. The real guest also obtains a fresh challenge and submits composite
CPU/GPU evidence; both CPU and GPU now have affirming appraisals after independently approved
references were loaded. Final end-to-end storage activation remains under test.

The first TLS failure was a generated authority certificate with `CA=true`.
The guest Rust verifier correctly rejected its use as a server leaf
(`CaUsedAsEndEntity`). The preparer now emits `CA=false` with the `serverAuth`
extended key usage. Reissuing only the public certificate preserved the private
TLS key, receipt key, InitData and retained authority database. An exact Rust
client probe and the real guest both verify the new pin successfully.

Failure diagnostics expose only fixed phase/category labels. Authority logs
contain fixed route/status/denial codes and bounded trust-vector values after
pinned-signature verification. Request contents, keys, evidence, session cookies,
tokens and arbitrary exception strings are excluded. Privacy regression tests
cover hostile diagnostic inputs.

**Lab trust boundary:** this authority and the existing Trustee are trusted
services on the same administrative cluster. The PVC retains state across process
restarts; it does not protect the authority history from a hostile cluster/storage
administrator rolling it back. Production freshness requires authority state in a
separate trusted rollback domain, with retained backups and controlled recovery.
Neither dm-integrity nor a same-cluster state PVC supplies that guarantee.

The installed Trustee signs an EAR without `iss` and without GPU
`runtime_data_claims`. The explicit adapter profile verifies its pinned signer,
requires the absence of `iss`, keeps the standard EAR profile and freshness checks,
and expects configuration appraisal 3. GPU freshness uses the signed NVIDIA
nonce-match claim plus the CPU's exact SHA-384 composite report-data binding.
The gateway accepts this only for its retained request-specific standard KBS RCAR
session. Missing GPU claims, wrong report-data and warning appraisals are tested
denials. The stricter original profiles remain the default.

The scoped GitOps update changes only the key-release ConfigMap and RVPS policy.
Each of the two new tags additionally pins exact launch/InitData values; other
keys retain their existing CPU+GPU requirements. All 84 Rego cases and four
reference-merge tests passed. Trustee was rolled twice to load subPath-mounted
references/policy and then startup-converted new resources. Original Qwen/chatbot
pod identities and zero restarts were retained; real inference returned HTTP 200.

## Verified storage and CUDA checkpoint (14 October 2026)

Guest pod `1273e795-9149-418f-b040-2ade09ef2e81` completed standard Trustee
composite CPU/GPU attestation, RO model verification, first-use RW initialization,
and application-side CUDA vector addition. Its logs contain:

```text
STORAGE_MODEL_RO_HASHES_PASS
STORAGE_DATA_FIRST_WRITE_PASS
{"event":"CUDA_GPU_PROBE_PASS","device":"NVIDIA RTX PRO 6000 Blackwell Server Edition","torch":"2.9.0a0+50eac811a6.nv25.09","cuda":"13.0"}
```

All four RW journal steps are Complete. Both volume records are Ready, and the
failed application closed both owners cleanly. The disk is now used: never repeat
blank initialization, replace its key, or restore the original seed database.

vLLM subsequently failed during import because `getpass.getuser()` could not find
UID 1001 in the image's passwd database. The next measured revision sets `USER`,
`LOGNAME` and `TORCHINDUCTOR_CACHE_DIR` explicitly. The application remains UID/GID
1001 with no capabilities, no privilege escalation, and a read-only root.
This checkpoint proves storage and CUDA execution, **not successful vLLM serving**.

### Updating a measured workload without resetting its storage

`gpu-guest/update-environment.py` derives revised policy, InitData, contract,
pod and public guest/server configuration from the previous preparation. It
creates neither keys nor a database. Preserve the existing TLS/receipt keys,
model/data credentials, volume records and disk contents.

The administrator-only `confidential-storage-authority/update_policy.py` updates
an explicit set of volume policy bindings atomically. Every record must be
nonrevoked, Ready, unowned and match the exact previous policy. It retains disk
headers, authenticated manifests, generation, initialization journal, operation
IDs and events, records the reviewed evidence digest, and permanently retires the
old policy. Concurrent reopen and update cannot both win. No HTTP route exposes
this operation. It cannot recover a live owner or an interrupted format.

Separately update the public authority InitData through Vault CAS/ESO, its server
configuration, the measured guest, and the two scoped Trustee launch/InitData
bindings. Verify the actual replacement Trustee pod and loaded configuration;
rollout status alone does not prove that startup-only resources were reloaded.

### Initialization identity across workload upgrades

The original RW approval hashes the policy that authorized first formatting.
It must not be recalculated using a later application's policy. The authority
retains `initialization_policy_sha256` alongside the current `policy_sha256`.
It reconstructs the original identity only when the immutable approval hash
matches an entry in retained policy history. An inconsistent history is denied.

The signed Ready response includes both identities. The guest independently
checks the original approval digest and complete four-step journal, then requests
an ordinary reopen grant under the current workload identity. A distinct initial
identity is rejected for Uninitialized or Initializing records; it cannot authorize
formatting. Header, layout, manifest, key and initialization journal stay unchanged.
Regression tests exercise wrong initial/current identities, incomplete journals,
corrupt history and racing ownership. The upgrade authority image passed 135 tests;
the guest service and client suites passed 8 tests each.

CRI-O was observed supplying both the exact image PATH and its default PATH.
Environment revisions preserve these explicit alternatives. The candidate also
sets the exact `INFERENCE_SERVICE_NAME=qwen-storage` that KServe injects, keeps its
fixed hostname, and permits neither arbitrary environment values nor shell exec.

## Remaining acceptance work

- Reopen the same RO and RW disks in a fresh VM, verify the persistent canary,
  independently compare the new live launch, and complete vLLM inference.
- Deploy a second KServe InferenceService and chatbot on port 30081, preserving
  the original endpoint. Exercise OCI population for a new model PVC as well.

See the [live bring-up record](../../tests/confidential-storage/evidence/2026-10-14/gpu-live-bringup/progress.json).

### Unstarted initialization recovery

The first fully attested attempt obtained authority decisions, but standard KBS
returned resource-not-found. The second `rollout restart` command had reported
success without a replacement pod: the operator retained the old template/pod.
A checked deletion of that specific stale Trustee pod caused its replacement to
import both new keys; its new UID and conversion logs were verified. A successful
rollout-status command alone is insufficient for resource readiness here.

The authority correctly retained the RW owner in `Initializing`. Its step journal
was empty: **no destructive formatting permit had been issued**. Recovery now has
a narrowly scoped administrator-only operation, `recover_unstarted.py`. Before
using it, stop the failed guest and retain a consistent private database backup
and audit evidence. It checks the exact activation, nonrevoked state, original
approval and empty step journal atomically, retires that session, and returns the
record to `Uninitialized`. Operation IDs, approval, generation and all events are
retained. A new guest must repeat the complete blank-device check. A concurrent
step issuance and recovery cannot both succeed. Any Started step, including one
with a lost response, remains fenced; this is not general interrupted-format or
rollback recovery. No HTTPS/application route exposes this administration method.
