# Isolated Qwen GPU + RO/RW storage candidate

This prepares the **same consumer** with `/models/qwen` from the previously
OCI-populated encrypted model PVC and `/data` from a new 1 GiB block PVC. It is
verified live on 14 October 2026 with a second KServe predictor and VPN chatbot.
It is a custom prototype, not a stock CoCo feature. See the
[runbook and evidence](../../../docs/confidential-storage/gpu-guest-candidate.md).
The user has now allocated the former encrypted-file demo GPU permanently for a
second Qwen/KServe deployment and a separate chatbot port. Preserve the original
Qwen/KServe/chatbot throughout that handover.

## Preparation inputs and output

`prepare.py` uses the contract compiler and the existing authority implementation;
it is a bounded administrator tool, not a new reconciliation controller. The
ConfidentialVolume declarations in its bundle are approval inputs. They are not
installed CR instances and their status does not authorize initialization.

Supply a private input directory with these **public** files, captured from the
existing deployment/API:

- `aa.toml`, `cdh.toml`: AA/CDH configuration from the current Qwen InitData. The
  generator accepts the existing HTTPS Trustee origin and matching public TLS pin,
  rejects inline credentials, and keeps the AA/KBC configuration. It removes the
  old image-policy/registry-credential resource requests: the candidate uses a public
  digest-pinned image admitted by its measured agent policy. This does not add image
  signature verification.
- `trustee-ca.pem`: the same pinned public certificate; no Trustee private key.
- `qwen-luks-model-v1.json`, `qwen-gpu-data-v1.json`: PVC snapshots containing
  apiVersion/kind, metadata name/namespace/UID, spec and status. The model claim must
  already contain the reviewed publisher artifact. Bound status alone does not
  establish that content; guest dm-verity validates it against the approved root.
- Separately supply the public AS signer certificate from standard Trustee.

```sh
python3 tests/confidential-storage/gpu-guest/prepare.py \
  --inputs /secure/public-inputs \
  --as-certificate /secure/public-inputs/as-signer-certificate.pem \
  --new-data-pvc-uid EXACT_UID_OF_NEW_1GIB_CLAIM \
  --authority-address AUTHORITY_SERVICE_CLUSTER_IP \
  --output /secure/new-candidate-authority
```

Install `components/confidential-storage-authority/requirements.txt` in the Python
3.11+ environment first. The NEW UID argument is an explicit administrator
assertion tied to provisioning history; it is **not** inferred from PVC status.
The guest must still verify the whole device is zero before the approved format
sequence. Never point this command at a previously used data claim to recover it.

The reviewed model publisher descriptor and source are the committed
`evidence/2026-10-13/model-populator` artifacts. This example supports their 2 GiB
RO disk and a new 1 GiB RW disk, UID/GID 1001. The general contract format supports
multiple volumes, but this preparer intentionally produces exactly these two.
Kubernetes scientific-notation capacities are parsed exactly with Decimal.

The output directory is exclusively created with mode 0700; files are private.
It contains the public contract, generated workload and policy, InitData, Pod,
public guest service config, public TLS/signer pins, private TLS/receipt keys and
an authority database. **Do not commit or upload the whole directory.** The tool
never contacts Trustee, installs a runtime, provisions a LUKS key, formats a disk
or starts an authority. An existing directory is never overwritten.

The authority database has one immutable published RO record and one Uninitialized
RW record with a journaled initialization intent bound to the exact new PVC UID.
The two new resource names are `default/model-keys/qwen-gpu-model-v1` and
`default/model-keys/qwen-gpu-data-v1`. Both expect lowercase hex text encoding exactly 32 key bytes, matching the existing
Vault/ESO model-keys secret mount. Before
activation, the RO resource must contain the retained credential for the existing
LUKS disk; a new random key cannot open that disk. The RW credential must be new
and durably retained. Provision through the standard pattern secret backend/ESO,
under separately reviewed CPU+GPU/measurement resource policy. No keys have been
provisioned by this preparation checkpoint.

The receipt signer, TLS key and authority database must be retained together in
the trusted authority deployment. The generated TLS certificate is valid for 30
days, has `CA=false` and `serverAuth` EKU, and its public DER is embedded in the candidate initrd. Changing that pin
requires rebuilding/reapproving the guest. `server.json` currently uses absolute
local paths: relocate these deliberately for deployment. The default preparer expects an issuer-bearing EAR with configuration score 2.
The installed Trustee 1.2 was verified to omit `iss`, use configuration score 3,
and export NVIDIA nonce-match rather than GPU `runtime_data_claims`. For this
verified installation, prepare with `--as-issuer-claim absent-rcar
--as-configuration 3 --as-gpu-binding nvidia-nonce-match`. These are explicit
profiles, not automatic fallbacks. The absent-issuer profile requires absence of
`iss`; it still pins the AS signing key, KBS TLS identity and EAR profile. The GPU
profile requires signed nonce-match and secure GPU claims, plus the CPU's exact
SHA-384 composite report-data binding (including the GPU evidence), and the
same retained RCAR handshake. Unknown fields/failed appraisals remain denials.
Never select these profiles merely to make an unexplained denial disappear.

## Guest admission and workload

The exact command, model inventory, image digest, environment allowlist and
required environment entries are measured in `policy.rego`. Both SCSI transport
paths and a single NVIDIA CDI selector are admitted. Native admission separately
validates VFIO metadata, removes raw storage devices and host binds, and checks
trusted guest CDI edits. Exec, CopyFile, SetPolicy and related bypass RPCs remain
denied. UID/GID are 1001, capabilities are empty, privilege escalation is disabled
and the container root is read-only.

Startup checks the model mount is RO, verifies every publisher file hash, checks
`/data` is writable, creates or verifies/fsyncs a persistence canary, and performs
4096-element CUDA vector addition. It then executes GPU vLLM on `/models/qwen`
with bf16, context 2048, four sequences and 25% GPU-memory utilization. Caches and
temporary files use `/data`. CUDA arithmetic is not an attestation test; storage
opening is separately gated by composite Trustee attestation in trusted guest
infrastructure. Application code never obtains a LUKS credential or runs cryptsetup.

The canary is a persistence test, **not** a freshness counter. RW dm-integrity does
not prevent whole-volume rollback. Model freshness is pinned by approved immutable
publication/generation and retained authority state, independently of the registry.

## Helm and admission

The chart creates only an explicitly requested data claim/internal authority
Service by default; the workload is disabled. Render the prepared Pod with:

```sh
helm template gpu-storage tests/confidential-storage/gpu-guest/chart \
  -n confidential-storage-test --set createDataClaim=false \
  --set workloadEnabled=true \
  --set-file candidatePodJson=/secure/new-candidate-authority/pod.json
```

Do not apply until runtime, authority persistence, exact launch references and
resource policies are ready. The candidate RuntimeClass now aliases the isolated storage test handler.
The direct InitData annotation uses the hypervisor prefix. The candidate sets
`coco.io/skip-initdata: "true"` to use the existing webhook exclusion: this avoids
its unconditional ConfigMap lookup and preserves the directly supplied measured
InitData. It does not disable guest policy or attestation.

A server dry-run using the **existing** GPU class only for admission passed with
SCC `anyuid`, UID/GID1001, `privileged=false`, no capabilities and no privilege
escalation. That does not establish admission under a future service account or
successful launch of the candidate class. No SCC or shared webhook was changed.

## Verification and remaining activation

Run:

```sh
python3 -m unittest discover -s components/confidential-volume-contract -v
python3 -m unittest discover -s tests/confidential-storage/gpu-guest -v
```

For policy tests, copy `policy-tests.rs` to `src/agent/tests/storage_gpu_policy.rs`
in the pinned prepared Kata source and copy public `policy.rego`/`workload.json`
into a test directory. Run `cargo +1.94.1 test --locked --features
init-data,agent-policy --test storage_gpu_policy` with `STORAGE_POLICY_TEST_DIR`
pointing there. The test uses actual protobuf serialization and downstream
Regorus, including negative command/image/environment/capability/device cases.

Current evidence is under `evidence/2026-10-14/gpu-candidate-prepared`. Next:

1. Completed: retire the separate encrypted-file demo through its GitOps source;
   its GPU is now dedicated to this second instance. Preserve the original Qwen.
2. Install the isolated guest/runtime and independently calculate its launch
   reference using the exact GPU CPU-count, command line, kernel and firmware.
   Do not reuse the one-vCPU CPU-prototype measurement.
3. The authority is deployed with persistent retained state; complete a genuine
   standard Trustee RCAR round trip and denial cases before enabling storage keys.
4. Provision the two scoped credentials and exact release policy; exercise first
   initialization, all model hashes, CUDA inference, and reopen in a fresh VM.
5. Put the proven pod template behind a second KServe InferenceService; expose
   its chatbot on a distinct free port, while verifying the original endpoint.

No successful GPU storage boot, fresh-VM persistence, inference or second chatbot
is claimed by these preparation results.

## Authority deployment checkpoint

`prepare-authority-build.py` packages only public source, profiles and hash-locked
wheels. The disabled-by-default Helm `authorityRuntime` option creates a dedicated
state PVC, Vault-backed ExternalSecret, public configuration and either a seed pod
or the authority Deployment. Set `authorityRuntime.uid` to the namespace's admitted
SCC range; this deployment does not need anyuid or privileged SCC grants.

`provision-authority.py` writes only the prepared authority credentials to the new
Vault path with CAS=0. An existing different value is an error, never rotation. The
model decryption keys are not included. Seed the private database once using an
exclusive create, verify its digest and integrity, then remove the seed pod and
start the Deployment. Preserve the database, TLS pin and receipt signer together.
Do not rerun preparation/seed to recover a used RW volume.

The running state directory is `/state/retained`, mode 0700, with database mode
0600. On attachment the launcher restores these permissions on the exact owned
paths because Kubernetes fsGroup can add group permission bits. Private key copies
are in a per-process directory on memory-backed `/tmp`. Missing state is fatal.
The initial unused `/state/private` copy was made before choosing the restricted
SCC UID; it has zero operations and is not the running database.

This same-cluster lab deployment trusts the control plane and authority storage
administrators, as it trusts the colocated Trustee. A retained PVC alone does not
provide protection against rollback of authority history. Production deployment
must place that history outside the adversary's rollback domain.

## Scoped storage-key provisioning

`kbs.resourcePolicy.modelKeyBindings` maps each new `default/model-keys` tag to
its approved `launchMeasurement` and `initData`. The chart appends those references
to RVPS without removing existing references. A bound tag additionally requires
both exact values in the verified CPU claims, as well as all existing CPU/GPU
security checks. Review the measured artifacts independently before changing this
map. The example values are host/image-specific approval records, not portable
reference values for arbitrary rebuilds.

After GitOps reconciles both policies, load them into Trustee (its subPath mounts
require a pod rollout). Only then provision the keys:

```sh
python3 tests/confidential-storage/gpu-guest/provision-storage-keys.py \
  --kubeconfig /secure/cluster.kubeconfig \
  --approved-policy /secure/reviewed-rendered-resource-policy.rego \
  --model-key /secure/retained-ro-luks.key \
  --data-key /secure/private-candidate/new-data.key
```

This checks the loaded KBS policy against the reviewed file before writing. It
reads the existing private 32-byte model key, exclusively creates or reuses the
private 32-byte RW key, and CAS-merges only the two named Vault entries. Different
existing entries are errors; unrelated credentials are preserved. Retain the RW
file across retries and back up the Vault state. Verify ESO reconciliation, then
reload Trustee's existing startup secret converter before admitting initialization.
Verify a new pod UID and both explicit conversion records: an operator may revert
a rollout-restart annotation before the deployment controller acts on it.
Never launch a formatting attempt with a missing credential: uncertain initialization
ownership deliberately remains fenced and requires explicit recovery.


For a failed first-use attempt with **no step permit ever issued**, the authority
administrator can use `components/confidential-storage-authority/recover_unstarted.py`
after retaining a consistent DB backup, stopping the failed guest and recording
evidence. Supply the existing DB, exact volume/activation IDs and evidence hash.
The transactional guard retires the old session and preserves the operation ledger.
It cannot recover a Started step or initialized disk, and is not exposed by the
workload-facing gateway. See the detailed candidate guide before using recovery.

### Existing initialized volumes and measured environment changes

Use `update-environment.py` with the previous public preparation and current
public guest/server configuration. It emits new policy/InitData and preserves
volume manifests, initialization-plan hashes, receipt/TLS public pins and key
references; it never generates credentials or a new database. Explicitly review
the new workload, independently measure its guest, and update only its scoped
Trustee release bindings. Back up retained authority state before invoking the
administrator-only `update_policy.py` for closed Ready records. Preserve the
original initialization policy and journal; never seed or format an existing disk.

The chart's `kserveEnabled=true` renders the isolated `qwen-storage` Standard
InferenceService with one replica and a Recreate strategy. It is mutually exclusive
with `workloadEnabled`; stop the standalone test and verify both volume owners
closed before switching. The reviewed pod's fixed hostname, exact environment,
InitData, unprivileged security context and block attachments are retained.
