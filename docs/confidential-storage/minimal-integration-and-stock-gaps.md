# Implemented storage extensions and gaps against CoCo / the Validated Pattern

**As implemented and validated on 14 October 2026:** Qwen GPU storage revision v19
(`eb07abd`) plus ConfidentialVolume controller (`018ae67`). This replaces the
original planning checklist. It records existing components and remaining gaps;
it is not a claim that a new clean-cluster installation is already automated.

Read the [Qwen implementation walkthrough](qwen-storage-walkthrough-and-gaps.md)
for actual CR/PVC examples, data and authorization diagrams, and test evidence.
The [controller guide](confidential-volume-controller.md) describes its precise
reconciliation boundary.

## Comparison scope

“CoCo” and “the CoCo Validated Pattern” are different layers. CoCo/Kata supplies
the confidential guest, attestation/key-retrieval components and policy enforcement.
OSC packages and operates the OpenShift runtime. The Validated Pattern composes
operators, configuration, secrets and examples through Helm/GitOps. Adding a Helm
chart to the pattern cannot by itself add a missing guest-agent capability.

| Baseline | What was inspected | Limits of this comparison |
| --- | --- | --- |
| Official Validated Pattern | Pinned upstream tree [`aa04a093268be8ac92174313848a4c44298fbc41`](https://github.com/validatedpatterns/coco-pattern/tree/aa04a093268be8ac92174313848a4c44298fbc41), especially `values-baremetal.yaml`, `values-global.yaml` and `charts/` | This is the project baseline, not a claim about today's upstream HEAD |
| Installed OpenShift runtime | OpenShift 4.22.15, OSC 1.13.0, Trustee 1.2.0, downstream Kata 3.31.0, kernel `6.12.0-211.16.1.el10_2.x86_64`; existing `topolvm.io` / `lvms-vg1` Block CSI | Versions recorded in the [compatibility inventory](compatibility-report.md); its early test-status sections are historical |
| Actual GPU guest | [Base inventory](../../tests/confidential-storage/gpu-guest/base-inventory.json), preserved AA/CDH/NVIDIA binaries and added storage dependencies | Conclusions apply to this measured guest, not every upstream CoCo build |
| Documented stock storage path | [OSC 1.13 block-volume recipe, section 4.9.2](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_confidential_containers_on_bare-metal_servers/configure-cc-overview_metal-cc) | Documents raw-block LUKS and a Trustee-supplied passphrase, with privileged helper/application containers, shared process namespace and hooks; helper example is 1.13.1, not our installed 1.13.0 inventory |

Stock encrypted persistent storage therefore already exists as a documented
recipe. We did not validate that helper on our GPU workload. Our specific addition
is an approved RO model and managed RW filesystem delivered by guest infrastructure
to an **unprivileged application**, with explicit release/initialization history.
This comparison does not establish that all other upstream approaches lack those
capabilities. Confidential `emptyDir`, encrypted container-image storage and
peer-pods storage are related work, but are not the persistent block-volume path
implemented here. See the [upstream reuse discussion](../encrypted-model-volume-proposal.md).

**Separate sidecar experiment:** [stock-agent mount transfer](stock-agent-sidecar-experiment.md)
subsequently demonstrated RO/RW mounts into an unprivileged container without an
agent patch. It is an isolated no-key test, not a replacement for the validated
Qwen deployment or its lifecycle/attestation guarantees.

## What we reuse without a source fork

| Existing layer | Role in the implemented path | Configuration we supplied |
| --- | --- | --- |
| Kubernetes and LVMS CSI | Provision Block PVCs, enforce supported attachment rules and attach ciphertext disks | Model 2 GiB and data 1 GiB, `ReadWriteOncePod`, `lvms-vg1`; model has a custom `dataSourceRef` |
| OpenShift internal registry | Store/distribute the encrypted model disk artifact | Pinned OCI manifest/disk digests and namespace-restricted registry access |
| Kata host shim, QEMU and CRI-O | Launch the SEV-SNP VM and transport block/GPU devices | Isolated candidate handler and RuntimeClass pointing to the combined guest |
| Guest AA and CDH | Gather CPU/GPU evidence and retrieve KBS resources | Private interfaces, approved InitData and exact resource policy; original binaries preserved |
| NVIDIA guest driver / GPU stack | Confidential GPU access and CUDA execution | Existing GPU stack retained; native agent admission adapted to its bounded guest-generated CDI |
| Standard Trustee KBS / AS | Appraise CPU/GPU evidence, issue signed attestation results and gate keys | Separate model/data resources with scoped launch/InitData requirements; existing CPU/GPU requirements retained |
| Pattern Vault / ESO | Deliver private credentials to Trustee and authority secrets to the authority | Existing provisioning mechanism, new scoped resources; no secrets committed |
| KServe and vLLM | Reconcile serving workload and run GPU inference | Exact reviewed predictor template, local model path and `/data` caches; one replica, Recreate |

“No source fork” does not mean “no configuration change.” In particular, the
candidate runtime, key policies and workload InitData differ from the original
Qwen service. Neither a custom CSI driver nor a replacement Trustee server was
needed. The storage authority described below is a separate service, not a new
Trustee component.

## What we added or changed

| Addition / modification | Implementation location | What it does today |
| --- | --- | --- |
| Encrypted model publication and distribution | [Populator component](../../components/encrypted-model-populator/), [chart and source CRD](../../charts/coco-supported/encrypted-model-populator/) | Publisher creates the encrypted disk/verity artifact; `EncryptedModelSource` pins its identity; controller/importer populate a prime PVC and rebind its PV to the target. Includes a local namespace-colocation change to `lib-volume-populator` v3.2.0 |
| Declarative volume contract | [Compiler](../../components/confidential-volume-contract/), [ConfidentialVolume CRD/chart](../../charts/coco-supported/confidential-volumes/) | Immutable profile/access/key-reference/initialization declarations bound to actual claim UIDs, workload/container identities and destinations |
| ConfidentialVolume controller and public package approval | [Controller and signing tool](../../components/confidential-volume-controller/), [public deployment values](../../overrides/values-confidential-volume-controller-edge.yaml) | Deployed namespace-scoped resync loop resolves inputs, publishes requests, verifies separately signed Ed25519 approvals and publishes immutable artifacts/status. No workload writes, key reads or formatting |
| Kata agent changes | [Native agent patch and modules](../../components/confidential-storage-kata/native-agent/) | Admission/create/cleanup hooks; actual block-device resolution; private storage-service activation; approved mounts into app; raw storage nodes/grants removed; bounded NVIDIA CDI retained |
| Trusted guest storage daemon and backend | [Service](../../components/confidential-storage-service/), [backend](../../components/confidential-storage-guest/) | RO verity/LUKS validation and mount; RW approved blank-device initialization, keyed integrity, reopen, journal and cleanup. Runs as guest root, outside application containers |
| Private key adapter | [CDH adapter](../../components/confidential-storage-kbs/src/private_cdh.rs) | Enforces authority-derived permits and exact key-resource allowlist; uses existing private CDH ttRPC; explicitly converts stored hex credential to raw bytes |
| Storage operation authentication and state | [Authority](../../components/confidential-storage-authority/), [guest client](../../components/confidential-storage-client/) | Request-bound CPU/GPU authentication through standard KBS RCAR; signed grants; retained model-generation, ownership, initialization and policy history. Authority holds no volume keys |
| Combined measured GPU/storage guest | [Guest assembly](../../tests/confidential-storage/native-guest/build.py), [Qwen preparer](../../tests/confidential-storage/gpu-guest/prepare.py) | Packages patched agent, daemon, workload-specific public config, cryptsetup/verity/integrity/filesystem tools and missing matching modules. AA/CDH/NVIDIA binaries preserved; kernel source unchanged |
| Scoped Trustee configuration | [GPU/model-key override](../../overrides/values-trustee-gpu-model-keys.yaml) | Adds two retained credentials and exact approved guest/workload identities through existing KBS policy and Vault/ESO mechanisms |
| Serving and demonstration integration | [Qwen test chart](../../tests/confidential-storage/gpu-guest/chart/), [chatbot chart](../../charts/coco-supported/qwen-chat/) | Reviewed KServe template, narrow UID-1001 SCC, startup verification, two mounts and separate chatbot port 30081 |

The privileged operations are in the **guest kernel and trusted guest daemon**.
vLLM is UID/GID 1001, drops all capabilities, has a read-only root filesystem and
receives no raw storage devices, key sockets or service-account token. There is
no privileged application-side storage helper. Removing application privileges
does not remove the trusted guest infrastructure's need to create device-mapper
mappings and mount filesystems.

The existing GPU image had dm-crypt and dm-verity support but lacked the required
dm-integrity module. The combined image adds matching `dm-integrity`, `xor`,
`async_tx` and `async_xor` modules plus userspace dependencies. That is a concrete
image-packaging delta, not evidence that OpenShift storage generally lacks integrity.

## Gaps in the pinned Validated Pattern

The upstream baseline already composes OSC/Trustee, Vault/ESO, bare-metal runtime
configuration, backing storage, InitData policies and example workloads. Its
[`values-baremetal.yaml`](https://github.com/validatedpatterns/coco-pattern/blob/aa04a093268be8ac92174313848a4c44298fbc41/values-baremetal.yaml)
also contains GPU chart wiring, commented out in that baseline. We are not adding
confidential GPU execution, CSI storage or key brokering from scratch.

The following integrated storage use case is absent from that pinned pattern.
Some pieces now exist in our fork, but are not a default pattern installation.

| Missing pattern integration | What exists in our implementation | Remaining integration boundary / likely maintainer |
| --- | --- | --- |
| OCI model-volume distribution | Working publisher, source CRD, populator Helm chart and proven import | Pattern: package images, install CRD/controller/RBAC in ordered applications, configure registry trust and document publication/import lifecycle |
| ConfidentialVolume API and reconciliation | Deployed schema/controller with signed package validation | Pattern: wire chart/public signer trust into cluster-group configuration and upgrades; establish who supplies approved packages |
| Combined GPU + persistent-storage guest | Working custom measured initrd and RuntimeClass | CoCo/Kata/OSC: maintain agent/service interfaces and guest packaging; pattern: select supported artifact and manage runtime/reference rollout |
| Storage lifecycle authority | Restricted service, retained state PVC and Vault/ESO credentials | Define ownership of this additional service and trusted state; package deployment, enrollment, backup and recovery. It is not covered merely by installing Trustee |
| Scoped storage-key and measurement enrollment | Working standard Trustee configuration for these two resources | Pattern/Trustee integration: reviewed credential/reference provisioning and upgrade/revocation workflow; CR creation must not authorize its own key release |
| Unprivileged KServe storage example | Working test chart and real model/data mounts with measured process policy | Pattern: supported example/application values, policy-generation and KServe mutation compatibility, readiness evidence and user-facing runbook |
| Integrated validation and lifecycle | Retained RO/RW/GPU/controller positive and denial evidence | Pattern/OSC: clean-cluster replay, supported upgrade matrix, failure/recovery tests and retention rules across component upgrades |

The added charts are not yet wired into the normal top-level cluster-group
applications as a complete storage feature. The Qwen integration chart remains
under `tests/`, and guest preparation, authority enrollment and rollout use
explicit tooling. The controller's publication is real; it does not turn this
fork into a “declare volumes and workload, then everything deploys” installation.

## Gaps at the CoCo / Kata interface

These are the parts pattern YAML alone cannot supply:

- **Guest storage activation before application startup.** Our downstream agent
  patch resolves devices, invokes trusted storage code and supplies only mounts.
  A maintained upstream interface/build is needed to avoid carrying this patch.
- **Authenticated, reusable volume configuration.** Our service configuration is
  currently embedded in a workload-specific measured guest, with matching InitData.
  Generic configuration delivery needs an authenticated contract covering volume,
  consumer, destination, profile, key reference and initialization policy.
- **Supported private AA/CDH and attestation-result contracts.** We reuse binaries
  but add adapters, including an explicit installed EAR compatibility profile.
  Versioned interoperability tests are needed across guest and Trustee upgrades.
- **Integrated lifecycle semantics.** First formatting, identity retention,
  ownership, cleanup and recovery span agent, storage service and authority.
  They need defined interfaces and maintained tests, not only a mount command.

These are integration needs for the behavior demonstrated here, not assertions
that every upstream proposal lacks overlapping features. Our custom
`storage.confidentialcontainers.org/v1alpha1` APIs are not upstream standards.

## Limits still present in our implementation

| Limit | Current behavior / implication |
| --- | --- |
| Approval is not deployment | Controller publishes public artifacts and status. Guest build, trusted enrollment, Trustee updates and KServe rollout remain explicit. No admission gate automatically ties KServe launch to CR status |
| Approval is not runtime revocation | Expiry withdraws controller approval status; it does not stop an existing VM or erase a key already released. Validation used a 24-hour lab approval |
| Limited tested profiles | Live pair: 2 GiB RO model and 1 GiB RW ext4, one application container/replica, exclusive RWOP claims. Compiler supports up to eight bindings; this does not prove eight-volume hardware coverage |
| Crash recovery and fencing | Clean close/fresh-VM reopen tested; uncertain effects retain ownership for explicit recovery. Authority ownership is not physical fencing of an already unlocked VM |
| Authority trust and rollback | Retained state survives restarts, but same-cluster storage/admin control cannot provide hostile-admin rollback resistance. Production freshness needs an independently trusted state domain |
| RW replay protection | Keyed dm-integrity detects unauthenticated changes, not replay of old valid sectors or snapshots. Full RW freshness remains a separate design requirement |
| Key and artifact lifecycle | No complete automated rotation/adoption/revocation/restore workflow; no controller artifact garbage collection/finalizers. Retain used volume keys and authority history |
| Confidential transport | Chat port 30081 and backend are HTTP; the chatbot is outside the VM. Protected model/data storage does not imply protected client-to-TEE prompts |
| Installation/support coverage | Successful lab integration is not a qualified stock OSC feature or a replay-verified clean-cluster procedure |

## Evidence for what is complete

- [GPU startup log](../../tests/confidential-storage/evidence/2026-10-14/gpu-live-bringup/kserve-startup-v19.log): RO model hash verification, RW reopen, CUDA probe and vLLM startup.
- [VM/device mapping](../../tests/confidential-storage/evidence/2026-10-14/gpu-live-bringup/kserve-vm-v19.json) and [launch verification](../../tests/confidential-storage/evidence/2026-10-14/gpu-live-bringup/live-launch-v19.json): actual confidential VM and GPU attachment.
- [Authority history](../../tests/confidential-storage/evidence/2026-10-14/gpu-live-bringup/authority-state-v19.json): retained Ready volumes and initialization journal.
- [Chat audit](../../tests/confidential-storage/evidence/2026-10-14/gpu-live-bringup/chat-audit-v19.json): real response through the separate chatbot/predictor path.
- [Controller validation](../../tests/confidential-storage/evidence/2026-10-14/volume-controller/validation.json) and [signature rejection](../../tests/confidential-storage/evidence/2026-10-14/volume-controller/signature-denial.json): both real declarations, signed publication, negative approval test and restart persistence; 21 controller plus 15 compiler tests recorded.

These are retained test results, not a new live health check. The controller was
validated against the already running v19 workload; it did not perform its rollout.
