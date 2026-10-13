# Technical handoff for confidential model and private writable storage

Prepared: 13 October 2026. Design baseline: proposal commit `a0ffe04` on `codex/encrypted-model-storage-proposal`.

## Objective and scope

Implementation checkpoint (13 October): see the [live compatibility report](confidential-storage/compatibility-report.md), [diagnostic runbook](confidential-storage/installation.md), and [transactional authority core](../components/confidential-storage-authority/README.md). The [runtime/agent transport experiment](../components/confidential-storage-kata/README.md) also compiles and passes Go/Rust validation tests; activation is deliberately disabled pending the CDH/authority integration. The [signed-receipt and guest lifecycle core](../components/confidential-storage-guest/README.md) is also tested, with injected storage effects and synthetic peers. RO/RW mount backends now pass separate non-TEE kernel tests, and the internal-registry model PVC has been populated. The signed guest initialization protocol is also tested, but real key delivery, production agent activation and both confidential-workload E2Es remain unfinished. See the latest [continuation checkpoint](confidential-storage/continuation.md); compilation, kernel emulation and PVC population must not be described as completed confidential storage deployment.

Implement and validate two storage profiles for confidential workloads on the edge-rtx6k OpenShift lab. Keep application containers unprivileged for storage operations. First prove private persistent read-write storage in an isolated test workload; then implement immutable encrypted model distribution and integrate both profiles in a separate Qwen/KServe test.

This is a portable implementation plan, not a report that the features are deployed. Read the [design proposal](encrypted-model-volume-proposal.md) for the threat model and complete rationale. This handoff supplies execution order, deliverables, gates and recovery instructions. It does not authorize disruption of the existing KServe workload.

| Profile | Interface | Disk protection | Preparation |
| --- | --- | --- | --- |
| Immutable model | `/models`, read-only | LUKS2 plus dm-verity covering the complete LUKS header and ciphertext region | Trusted publisher builds; OCI registry distributes; custom populator copies ciphertext |
| Private persistent state | `/data`, read-write, one VM at a time | LUKS2 authenticated dm-crypt/dm-integrity, journaled updates and authenticated detached header | CSI provisions; trusted guest service performs explicitly authorized initialization once |

Both use CSI for allocation and attachment, and Kata guest services/CDH for activation and mounting. A populator does not replace CSI. A new private writable volume does not require a populator. Do not add dm-verity to a changing read-write filesystem.

## Instructions to the receiving model or harness

Paste the following as the starting task, attaching this document and the design proposal or supplying their repository links:

> Continue the confidential-storage work in pmatouse/coco-pattern-edge-rtx6k. Read docs/confidential-storage-implementation-handoff.md, docs/encrypted-model-volume-proposal.md and applicable AGENTS.md instructions. First establish a current read-only compatibility baseline. Then implement the gated milestones in the handoff using separate test resources. Preserve the live KServe service and existing CPU/GPU key policies. Target guest-managed /data and /models mounts with no application storage privileges. Prefer reuse of upstream Kata/CDH code, but verify exact source status and runtime compatibility. Record source commits, design decisions, test evidence and unresolved risks. Do not describe proposals, compilation, or mocked tests as successful end-to-end deployment. Never publish credentials. Do not push or sync the deployment branch or apply old repository manifests to repair live drift. If progress requires replacing shared runtime files, draining/rebooting the node or taking the only GPU from the live workload, prepare a concrete maintenance plan and report that constraint before proceeding.

The harness may use its own tool names and execution environment. The required capabilities are Git/GitHub read access, local editing/building, `oc` access over the lab network, and later an approved way to build/test guest components. This plan does not depend on Codex-specific APIs, Slack or email access.

## Repository and evidence inventory

- Repository: <https://github.com/pmatouse/coco-pattern-edge-rtx6k>.
- Documentation branch: `codex/encrypted-model-storage-proposal`; baseline `a0ffe04` was retrieved on 13 October 2026.
- Live deployment branch historically used: `codex/edge-rtx6k`. Treat it as potentially watched by ArgoCD. Do not push there during development.
- Current design: [encrypted-model-volume-proposal.md](encrypted-model-volume-proposal.md).
- Earlier model flow: [encrypted-qwen-inference.md](encrypted-qwen-inference.md).
- Earlier key policy: [model-key-policy.md](model-key-policy.md).
- GPU smoke-test flow: [encrypted-gpu-test.md](encrypted-gpu-test.md).
- Rebuild documentation: [edge-rtx6k/README.md](edge-rtx6k/README.md).
- Recorded evidence: `tests/encrypted-inference/`, especially `validation-2026-10-07.json`, GPU security reports and shared-chat routing audits. These are historical tests, not evidence for the proposed disk-storage implementation.
- Existing code to inspect before reuse: `scripts/test-encrypted-inference.py`, `scripts/audit-qwen-chat.py`, `charts/coco-supported/`, related `overrides/` and their values. Identify scripts with write/admin effects before running them.

Read current repository instructions. At preparation time they require Helm v2 charts, conventional commits, no edits under `common/`, no committed secrets, and values/overrides rather than hardcoded platform behavior. Guest runtime changes may belong in pinned upstream forks; the pattern repository packages those artifacts and configuration.

A portable checkout starts with:

```sh
git clone --branch codex/encrypted-model-storage-proposal \
  https://github.com/pmatouse/coco-pattern-edge-rtx6k.git
cd coco-pattern-edge-rtx6k
# Read AGENTS.md and confirm that no Argo application watches the development branch.
git status --short
git log -1 --oneline
# Create a separate implementation branch before code changes.
git switch -c dev/confidential-storage-integration
```

Use an existing suitable development branch if the work has already started. Never reset someone else's changes to match this handoff. Documentation revisions after `a0ffe04` may be useful; compare them rather than assuming this baseline is the latest implementation.

## Recorded lab state and access

The following comes from earlier work and must be checked before implementation. No fresh cluster audit was performed while preparing this handoff.

| Item | Recorded value or observation |
| --- | --- |
| Cluster API | `https://api.dell-rtx6kbse-3.bmas-001.lab.rdu2.dc.redhat.com:6443`, historically `10.14.202.14` |
| OpenShift | Reported version `4.22.15` |
| Hardware | AMD SEV-SNP and NVIDIA RTX PRO 6000 Blackwell Server Edition |
| RuntimeClass | `kata-cc-nvidia-gpu` |
| Workload | KServe InferenceService `encrypted-qwen` in namespace `gpu-workload`; changed by a colleague after the original standalone deployment |
| Model | `Qwen/Qwen3-0.6B`, revision `c1899de289a04d12100db370d81485cdf75e47ca` |
| Weight size | `model.safetensors`: 1,503,300,328 bytes; total artifact and GPU allocations are different quantities |
| Original vLLM image | `nvcr.io/nvidia/vllm:25.09-py3`, digest recorded in the runbook/test results; verify the live predictor |
| Original storage | Encrypted files fetched from internal OCI registry, decrypted to guest tmpfs; not persistent confidential model disks |
| Internal registry | `image-registry.openshift-image-registry.svc:5000`; original repository `gpu-workload/encrypted-qwen3-06b` |
| Existing Qwen key | `default/model-keys/qwen3-06b`; do not reuse or rotate for storage experiments |
| Historical CSI class | `lvms-vg1` used for registry storage; this does not prove guest raw-block or RWOP compatibility |

The prior local kubeconfig path was `/Users/petrmatousek/Documents/Codex/2026-10-07/can-x20/work/installer-home/cluster.kubeconfig`. It is a location hint for the same workstation, not a portable credential. Obtain authorized credentials through a secure channel on another harness. Do not print, commit or attach kubeconfig contents, bearer tokens, private keys, Vault data or model keys.

The durable local output directory is `/Users/petrmatousek/Documents/Codex/2026-10-07/can-x20/outputs`. Prior `/private/tmp/coco-storage-*` checkouts were temporary and are no longer reliable. Clone source by its URL and commit rather than relying on scratch paths.

Cluster DNS previously required VPN/internal resolution or local mappings. Check routing, TLS trust and DNS first; do not silently disable TLS verification. Do not publish a public endpoint or modify DNS, routes or the shared chat service as part of storage implementation.

## Non-disruption and security boundaries

1. Inventory the live InferenceService, predictor, services, chat routing, runtime and Argo ownership before any mutation. Preserve their current configuration. Old Git manifests may recreate the obsolete standalone deployment or undo the colleague's KServe changes.
2. Use a dedicated namespace, proposed `confidential-storage-test`, dedicated PVCs, test key resources, registry repository and service accounts. Confirm names are unused before creating them. Do not point global chart values or Argo applications at the experiment.
3. Never overwrite an active Kata handler, guest image, node configuration or production Trustee policy. Build separate versioned runtime/guest artifacts and a separate RuntimeClass where feasible. A new RuntimeClass name alone is not isolation if it shares the same mutable handler files.
4. Check spare CPU, memory, storage and GPU capacity. Begin disk tests without a GPU if a spare suitable CPU confidential runtime exists. Those tests cannot certify GPU attestation or GPU inference. If only the live GPU/runtime can support a test, stop at that gate and propose a maintenance window.
5. Application storage setup must not require a privileged application, privileged init/sidecar, `CAP_SYS_ADMIN`, `/dev/mapper/control`, raw backing-device access or access to privileged CDH/agent storage APIs. Guest system services may perform privileged operations inside the attested VM.
6. CSI host plugins are outside the confidentiality boundary. They may require their ordinary host privileges for storage provisioning, but must never receive plaintext, unlock keys or decrypted mounts.
7. No formatting of model-consumer disks. No formatting of initialized writable disks after header/key/open failures. Use only disposable, explicitly identified volumes for corruption tests; do not use example `/dev/sdX` paths blindly.
8. Preserve CPU/GPU policy requirements for the inference test. A CPU-only storage test uses separate credentials/policy, not a relaxation of the production model-key rule. Updated guest measurements must be independently approved; never accept every measurement to make a test pass.

## Phase 0 Read-only compatibility audit

Produce a dated `compatibility-report.md` before choosing runtime patches. Inventory only necessary fields; avoid unredacted resource dumps that might expose credentials in annotations or environment variables.

Example read-only starting commands, using an authorized kubeconfig:

```sh
oc whoami
oc get clusterversion version
oc get nodes -o wide
oc get csv -A
oc get runtimeclasses
oc get sc
oc get csidrivers
oc get csinodes
oc get pvc -A
oc api-resources --api-group=populator.storage.k8s.io
oc get volumepopulators
oc get inferenceservices.serving.kserve.io -n gpu-workload
oc get pods,deployments,services -n gpu-workload -o wide
oc get applications.argoproj.io -A
```

Discover CRDs/operators before issuing provider-specific queries. Authentication or RBAC failure is a prerequisite problem, not permission to bypass security. Do not run a live container exec merely to identify versions if manifests, immutable image references or artifacts provide the answer.

Record the following with evidence and unknowns:

- OpenShift/Kubernetes, OSC/operator, Kata shim/runtime type (Go or runtime-rs), guest kernel, agent, CDH, AA, Trustee and KServe versions; runtime/guest image digests and attested identity.
- CSI driver and provisioner versions; `volumeMode: Block`, topology, expansion, snapshots/clones, RWOP and raw attachment behavior. Distinguish documented capabilities from tests on this runtime.
- Guest availability of dm-crypt, dm-integrity, dm-verity, ext4, cryptsetup and veritysetup; supported algorithms and journal options. Inspect build configuration or a separate diagnostic guest. Do not modify the live guest to install tools.
- Agent policy, Init-Data binding, storage API access controls, support for an independent test handler and whether activation/cleanup APIs exist.
- Pod ownership and service routing: actual KServe mode, predictor image/runtime, model init flow, PVCs/memory volumes and registry references. Record differences from historical runbooks.
- Existing Argo applications' source repos/branches/paths and auto-sync behavior. Determine how to keep experiment resources outside live reconciliation.
- Available resources and whether a non-disruptive test is feasible on the single server.

The populator mechanism is GA in Kubernetes 1.33 and OpenShift 4.20+, including 4.22. OpenShift supplies the data-source validator, not our custom OCI/model population logic. Register the source kind and implement the controller. GA is not a support statement for custom code. See [upstream GA](https://kubernetes.io/blog/2025/05/08/kubernetes-v1-33-volume-populators-ga/) and [OpenShift documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html-single/storage/index).

**Exit gate:** a component matrix identifies each usable primitive, required patch/build, test resource and blocker. Select a supported existing CSI driver if possible. Do not invent a new CSI driver before demonstrating the attachment or metadata gap.

## Phase 1 Select and define the guest integration

Inspect upstream source and status afresh. The statuses below were checked on 8 October, not guaranteed current:

| Reference | Recorded status | Inspect for |
| --- | --- | --- |
| [Kata #10559](https://github.com/kata-containers/kata-containers/pull/10559) | Merged | Native confidential emptyDir and application-unprivileged guest mounting |
| [CAA #3155](https://github.com/confidential-containers/cloud-api-adaptor/pull/3155) | Merged | Guest interception before container creation, CDH calls and teardown |
| [Kata RFC #13638](https://github.com/kata-containers/kata-containers/issues/13638) | Open | Typed persistent-volume contract and measured policy |
| [CDH #1648](https://github.com/confidential-containers/guest-components/pull/1648) | Open, unmerged | Authenticated headers, safe reopen, initialization recovery |
| [CDH #1663](https://github.com/confidential-containers/guest-components/pull/1663) | Closed, unmerged | Journaled integrity prototype/tests; closure does not mean merged |
| [Kata #13637](https://github.com/kata-containers/kata-containers/pull/13637) | Closed, unmerged | runtime-rs storage authorization; do not assume Go runtime compatibility |
| [Manuel's CDH branches](https://github.com/confidential-containers/guest-components/pull/1648#issuecomment-5272774626) | Developer prototypes | Read-only verity/LUKS and writable profiles, activation/deactivation API |
| [CDH #1379](https://github.com/confidential-containers/guest-components/issues/1379) | Open | Secure-storage close/state lifecycle |

Pinned read-only prototype source: [secure_volume.rs at 6acb695](https://github.com/manuelh-dev/guest-components/blob/6acb6957adeabae8b4211c7378d4ade2daeba9f4/confidential-data-hub/hub/src/storage/secure_volume.rs). Related [Kata branch](https://github.com/manuelh-dev/kata-containers/tree/mahuber/confidential-ro-volume) needs build/runtime compatibility review. Record chosen exact SHAs, patches, licenses and reproducible build instructions. Do not blindly cherry-pick an entire experimental branch.

Define a versioned request/response contract before implementing either profile. The following names are proposed semantics, not an existing API:

- Input: volume ID, profile version, authorized manifest/volume-record reference, requested access, operation (`initialize` or `reopen`), guest device identifier, target container and destination.
- Authority binding: measured agent policy and authenticated record constrain profile, access, operation and destination. Host identifiers only locate devices; they do not authenticate data or authorize formatting.
- Output: activation handle, effective access, protected mapping identity and mount ownership; never the key. Map creation and filesystem mount may be separate operations between CDH and the agent.
- Lifecycle: sandbox-scoped idempotence, serialized activation for the same volume, cancellation cleanup, reference counting, safe deactivation only after final authorized use, and error propagation that blocks application startup.
- Boundary: applications cannot call initialize/activate/deactivate or redirect a mount. Exposing a plaintext block mapper through `volumeDevices` is not completion; the guest service must mount it for the application.

Reuse CAA's division of responsibility, not its first-six-byte format decision or unreviewed header assumptions. For local Kata, implement the appropriate agent/runtime integration rather than deploying CAA cloud components without need. Evaluate standard block CSI first, then direct-volume metadata adaptation if required.

**Exit gate:** reviewed contract, component ownership diagram and isolated build/test strategy. Mocked KBS or loop-device unit tests are useful development evidence but do not satisfy attested end-to-end gates.

## Phase 2 Private writable volume

### Trusted record and initialization

Use one logical volume per workload instance. Provision a small disposable raw-block PVC using the selected CSI driver and RWOP where supported. There is no `dataSourceRef` for this new empty volume. Keep test credential resources outside the existing model key names.

Develop the independently administered volume authorization record/service described in the proposal. It may share infrastructure with release authorization, but is not ordinary untrusted Kubernetes status. Trustee stores/releases the credential; do not claim it already implements the required transactional state machine.

Record volume identity, allowed guest/workload policy, exact crypto/filesystem profile, credential reference/version, authenticated header identity/version, initialization operation generation and state. Define persistent authority recovery and rollback handling. A development mock must be explicitly marked as such.

Implement this ordering:

1. Attest and authenticate the guest. Atomically record `Initializing` and bind one operation to this volume/generation/session before permitting writes.
2. Validate the resolved device and the allowed initial state. A zero scan is a sanity check, not proof against host substitution. Unknown nonempty data is an error.
3. Create LUKS2, its protected header, the selected authenticated integrity stack and filesystem. Complete required integrity initialization. Do not use ephemeral no-journal/no-wipe settings without validating their safety for this writable profile.
4. Persist authenticated initialization phases and flush in defined order. Commit the finalized header identity and `Ready` state to the authority before exposing normal application access.
5. On an interrupted step, resume only the same authorized operation using verified phase records. Ambiguous state stops; it never triggers a fresh format attempt.
6. On `Ready` reopen, retrieve approved credentials, authenticate the copied header in guest-private tmpfs, and use that exact detached header with cryptsetup. Validate all allowed parameters. Missing/corrupted header or wrong key fails closed.
7. Recover journals according to the tested profile, mount ext4 read-write in the guest, and place `/data` in the application namespace before startup.

Select concrete supported algorithms/options during the compatibility audit; the plan does not prescribe an untested cryptsetup command. Use cryptographic authentication, not CRC-only tags. Validate integrity superblock/journal handling and flush/FUA behavior. Filesystem journaling and dm-integrity journaling address different failure modes. See [kernel dm-integrity documentation](https://docs.kernel.org/admin-guide/device-mapper/dm-integrity.html) and [header attack analysis](https://blog.trailofbits.com/2025/10/30/vulnerabilities-in-luks2-disk-encryption-for-confidential-vms/).

### Privilege and persistence proof

Run a small application with no storage-related capabilities, no privilege escalation, and no raw device or storage API access. It writes synthetic test data to `/data`, fsyncs files and directories as appropriate, and records nonsecret expected hashes outside the volume in the test harness. Replace the pod and compare contents through the new guest. Show that the authority remains `Ready` and no formatting occurred. Record the mount owner and effective device/filesystem options from controlled guest-service diagnostics, without enabling unrestricted production exec/debug policy.

Normal teardown stops consumers, flushes, unmounts, closes mappings and releases the attachment. An unknown previous-owner state stops automatic takeover. A second pod must be refused under normal cluster scheduling/CSI behavior. RWOP/key-release controls do not fence a malicious host retaining an old guest with keys.

Writable rollback is an explicit non-goal in this version: old valid sectors/tags or snapshots may reopen. Do not describe authenticated headers or initialization generation as protection for every application write. Security-sensitive counters/transactions need a separate external commit/freshness design. Defer online resize, live migration and in-place rotation; define ciphertext backup/restore retention without promising latest-state recovery.

**Exit gate:** full writable acceptance matrix below passes except explicitly documented rollback/fencing non-guarantees. The application stays unprivileged. Restore/cleanup procedures are reproducible.

## Phase 3 Immutable model image and guest activation

Build a deterministic small synthetic dataset first; use fresh test credentials. The publisher is trusted and may handle plaintext. An ordinary importer is not a publisher.

1. Produce an exact file manifest and a clean ext4 filesystem inside a bounded LUKS2 region.
2. Unmount and close LUKS. Build dm-verity over the entire finalized LUKS region, including header/keyslots and ciphertext; place the tree after that region.
3. Sign an unambiguous versioned descriptor containing file hashes, protected byte length, tree geometry/root, algorithms, image digest/length, key reference/version and approved guest/profile identity. Verify against an independent trusted signing key. Avoid circular enclosing-manifest digests.
4. Open the image through verity, then LUKS read-only, then a read-only filesystem. Reject anything requiring initialization or recovery writes. Never open an unverified alternate path.
5. Test on a guest-attached PVC, including late corruption after startup to prove verification applies to reads, not only initial hashing.

No dm-integrity is required in this preferred immutable format. Keep the earlier inner-verity/authenticated-sector format only as a separately versioned alternative if there is a concrete reason to implement it. Do not mix layouts. Any header/keyslot edit changes protected bytes and requires a new tree, descriptor and release authorization.

**Exit gate:** synthetic data reads correctly, writes fail, wrong root/geometry/header/data fail, and no formatting fallback exists. Model verification covers tokenizer/configuration/adapters/code as well as weights.

## Phase 4 OCI publication and custom population

Use a dedicated internal registry repository. Verify OCI media types, large blobs and signature/referrer behavior on this registry. If necessary, use a compatible image layer for the raw disk artifact and an independently authenticated descriptor. Do not disable registry TLS verification. Use minimal read-only registry credentials for importers; keep them separate from Trustee secrets.

Implement immutable `EncryptedModelSource` objects referencing a digest, verification policy and versioned format. Keep source, PVC and secret references in one namespace initially. Register the source kind with `VolumePopulator`; reuse SIG Storage populator machinery compatible with the deployed Kubernetes version.

The controller must:

1. Observe matching PVC `dataSourceRef` and validate source immutability, namespace and requested capacity.
2. Create/track the prime PVC with the correct storage class, block mode, access modes and topology. Exercise WaitForFirstConsumer scheduling rather than assuming an import job can attach anywhere.
3. Run a worker that verifies producer authorization/digests and copies bounded ciphertext bytes to the assigned device. Enforce length limits, short-write handling, flush and verification of the copied range. No LUKS open, mkfs, model key or plaintext is needed.
4. Hand over the populated PV only after success, using supported populator lifecycle semantics. Never fabricate `Bound` status or manually race binder ownership.
5. Reconcile worker/controller restarts and deletion at every stage; clean only owned temporary objects and preserve successful data. Original PVC cancellation must not delete unrelated PVs.
6. Export nonsecret progress and failure conditions. Register source kind before advertising readiness. Avoid busy retries and unbounded temporary storage.

Population occurs once per newly populated PVC. Additional consumers of that PVC do not trigger another import. New release means a new source/PVC or supported clone, never overwriting a mounted model volume. Multiple guests require validated read-only attachment or per-replica PVCs; Kubernetes access modes alone do not prove guest compatibility.

**Exit gate:** Pending → populated → Bound → guest readable, with topology/retry/deletion tests; no importer key access and no publication or import of plaintext.

## Phase 5 Separate Qwen and KServe integration

Do not apply old standalone deployment templates to the live predictor. Create a separately named InferenceService only after confirming spare compatible GPU capacity. Derive its schema/mode from the actual installed KServe version, not a remembered manifest.

- Convert the pinned Qwen inputs to the approved prepared-volume format in a trusted publisher. The existing encrypted bundle cannot become a LUKS image merely by copying its ciphertext into a PVC.
- Mount `/models` read-only and optional private `/data` read-write through the guest service; no application mount setup or raw-device access.
- Keep offline local-model loading. Do not silently fetch Hugging Face weights when files are missing. `/data` must not override approved tokenizer/model/adapter/code inputs without renewed verification.
- Preserve CPU/GPU attestation requirements and validate GPU confidential mode before loading plaintext. GPU visibility or a CUDA result alone is insufficient proof.
- Keep the guest serving gate closed until model files are verified, loading succeeds and required serving authorization is obtained. Kubernetes readiness is only an operational indicator.
- Test the actual inference endpoint with fixed synthetic prompts; verify routing to the test InferenceService and capture no sensitive prompt content.
- Measure cold-start/import/load durations, guest memory, image-layer memory, disk usage, integrity overhead and inference performance. Separate weight bytes, image-layer footprint, GPU KV cache and runtime allocation.

Current-release checks and the second model-evidence exchange are not supplied automatically by KBS unlock. Implement the protocol in the proposal: approved loader, fresh challenge, descriptor/root/file-manifest identity, endpoint-key binding and independently maintained release authorization. First guarantee freshness at launch/new session; renewable running authorization and trustworthy expiry are a separate validated stage. Do not overwrite KBS protocol report-data bindings with custom model fields.

**Exit gate:** correct isolated inference, persistence on `/data`, no application storage privilege, required attestation and model authorization failures deny serving, and the existing service remains unchanged and healthy.

## Acceptance matrix and evidence

| ID | Test | Required result |
| --- | --- | --- |
| RW01 | First-use initialization with valid authority | One recorded initialization, durable Ready state, usable `/data` |
| RW02 | Missing/invalid initialize authorization | No formatting or application start |
| RW03 | Write, fsync, replace pod, reopen | Identical expected data; no reformat; fresh authorized key retrieval |
| RW04 | Crash at each initialization/authority boundary | Verified resume of same operation or explicit recovery stop; no silent reset |
| RW05 | Ready volume blank, wrong key or modified header | Reject before unsafe activation; preserve evidence |
| RW06 | Data/tag/journal corruption | Verified failure according to fault case; no tag regeneration/bypass |
| RW07 | Crash after normal writes | Filesystem/integrity recovery obeys documented durability; no blanket promise beyond fsync/application semantics |
| RW08 | Second pod and uncertain prior ownership | Normal scheduling/CSI exclusive-use behavior; automatic unsafe takeover refused |
| RW09 | Restore an older valid writable snapshot | Document possible acceptance; explicitly no anti-rollback claim |
| RO01 | Approved image and root | Exact expected files, read-only mapping/mount |
| RO02 | Wrong key/root/geometry, changed header or ciphertext | Activation or affected read fails; never format |
| RO03 | Corrupt backing data after initial verification | Later affected read fails through verity; serving cannot ignore the failure |
| RO04 | Valid but retired model release | Current authority denies new unlock/serving as defined |
| POP01 | Valid source/PVC on selected CSI topology | One complete population and correct PV handover |
| POP02 | Interrupted worker/controller; partial writes | Safe retry, no early handover or leaked unrelated resources |
| POP03 | PVC deletion during import; source substitution | Owned cleanup; source identity mismatch rejected |
| POP04 | Second consumer/restart; registry unavailable after import | No reimport of existing bound PVC; guest policy still required |
| SEC01 | Application attempts mount/raw-device/storage-API use | Denied while ordinary authorized file I/O works |
| SEC02 | Denied CPU/GPU/workload evidence or unavailable KBS | No key and no application access/serving |
| SEC03 | Substitute key/volume/profile/destination from host | Measured policy and authenticated record reject |
| SEC04 | Stale evidence, reused nonce, wrong endpoint key | Model-serving authorization rejects |
| OPS01 | Failed activation/cancellation/last consumer exit | No orphan decrypted mapping; owned mount/key state cleaned |
| OPS02 | Live service before/after experiment | No unintended manifest, runtime, policy or routing change |

Every result records UTC time, source/build SHAs, image digests, test profile, nonsecret volume/key-resource IDs, expected/actual outcome and sanitized evidence locations. Distinguish real hardware E2E, guest integration, loop-device, unit and mocked tests. An expected limitation is not a passing security guarantee. Never record unlock material, raw secrets, credentials or plaintext models in reports.

Use synthetic data for corruption/crash tests. Review guest diagnostic logs before committing them; cryptsetup command arguments, environment dumps and broad cluster exports can leak secrets. Pass secrets through controlled memory/stdin interfaces, not command-line arguments.

## Deliverables and proposed repository layout

Adapt names to the repository after inspection; these are proposed additions, not existing APIs:

```text
docs/confidential-storage/
  compatibility-report.md
  architecture-decisions.md
  installation.md
  writable-volume-runbook.md
  model-publication-runbook.md
  recovery-and-cleanup.md
charts/coco-supported/confidential-storage-test/   # isolated Helm deployment
scripts/confidential-storage/                      # publisher/test helpers only where needed
tests/confidential-storage/
  unit/
  integration/
  evidence/<date-and-build>/                       # sanitized reports only
```

Keep custom controller source in a clearly owned component/repository with an image build, pinned dependencies and API tests. Keep Kata/CDH patches in their appropriate forks and record build inputs in the packaging repo. Pin deployed images by digest. Do not commit large disk images, downloaded model weights or test credentials.

Track each milestone as not-started, in-progress, blocked or passed, with exact blocker and next action. Maintain a short continuation note after each work session: branch/commit, resources created, active jobs, runtime versions, tests performed, known failures and cleanup remaining. This enables another harness to resume without conversation history.

## Validation, rollback and completion

For documentation, run Markdown lint and `git diff --check`. Repository instructions also require `./pattern.sh make preview-all` and `./pattern.sh make validate-schema`. Prior attempts had environment/tooling failures or stalled utility-image pulls; do not treat those as passes. Bound infrastructure troubleshooting and report the exact blocker. Do not change unrelated chart values to make a documentation check green.

For implementation, run component unit/integration tests and targeted chart rendering/schema checks, then hardware gates above. A compile-only result is insufficient. After source changes affecting trust, regenerate guest policy/measurements deliberately and keep test reference values isolated from live ones.

Before deployment, inventory exact test resources and ownership labels. Cleanup removes only test InferenceServices, pods/jobs, mounts, controller registrations, PVCs/PVs per their retention settings, registry test artifacts and test key records after recovery needs are settled. Never delete shared CRDs, storage classes, production keys or runtime handlers because their names look similar. Removing a VM does not justify deleting a PVC needed to prove persistence.

If an isolated runtime change fails, select the previous test handler/artifact and remove only the experiment registration; do not globally reset the node. If a change cannot be isolated, stop and prepare a maintenance plan with affected workloads, backup/recovery, expected downtime and rollback steps. Do not improvise a node reboot or GPU reassignment.

Completion means a clean-cluster reproducible installation, both storage profiles tested end-to-end with unprivileged application containers, model-populator lifecycle validated, security limitations documented, sanitized evidence committed, and the live KServe workload preserved. Writable rollback resistance and hostile-host fencing remain explicitly outside the first version unless a separate design and test suite establish them.

## First work session checklist

1. Read repository instructions and both design documents; check remote branch history.
2. Establish secure cluster access and collect only the read-only inventory.
3. Confirm live KServe/GitOps ownership, spare resources and a safe test-runtime path.
4. Inspect upstream source/status and write the compatibility/patch matrix.
5. Define the guest activation/mount contract and identify who implements each part.
6. Prepare isolated writable-volume test artifacts; proceed to deployment only when the non-disruption prerequisites are met.

Do not start by implementing the OCI operator: guest-managed private writable storage and raw-device compatibility are the first engineering uncertainties to resolve.
