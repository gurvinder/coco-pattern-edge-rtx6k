# Minimal integration changes and comparison with stock OpenShift CoCo

**Controller update:** namespace reconciliation, signed configuration publication and CR status are now implemented. See [ConfidentialVolume controller](confidential-volume-controller.md). Earlier descriptions of an offline-only compiler below record the pre-controller implementation; trusted package approval and rollout remain explicit.

**Current implementation:** the combined GPU/RO/RW deployment is now validated. Use the [implementation walkthrough and updated gap register](qwen-storage-walkthrough-and-gaps.md) for completed work versus remaining gaps. This document retains the original planning baseline.

Implementation progress is tracked in the [14 October checkpoint](multi-volume-implementation.md);
planning tables below describe the target and original baseline.

**Planning baseline, 14 October 2026.** This narrows the
[GPU integration plan](qwen-kserve-gpu-integration-plan.md). Reuse the working
Qwen GPU guest, its attestation components, standard pattern Trustee, existing
CSI and KServe. Keep a per-component delta so prototype limitations are not
misreported as missing stock-platform capabilities.

## What changes inside the guest

| Component | Minimum action | Reason / boundary |
| --- | --- | --- |
| `kata-agent` | Extend the existing native lifecycle patch and rebuild the agent | Resolve a bounded list of approved devices, wait for private storage activation, insert filesystem mounts, remove raw storage access and clean up after consumer exit |
| Private `confidential-storage-service` | Package the existing daemon; generalize its one-volume state to a per-volume map | Own LUKS/verity/integrity/filesystem operations, authority grants, keys and cleanup; application gets only mounts |
| Agent/service configuration | Replace one fixed binding with the authenticated binding collection | Exact volume/consumer/destination/profile and first-use approval; no Kubernetes API credentials in guest |
| CDH and Attestation Agent | Reuse the installed GPU-capable binaries and private interfaces where possible | The Qwen guest already knows CPU/GPU attestation and standard Trustee key retrieval |
| Storage key adapter | Replace `SnpCpuKbs` usage with a private CDH adapter retaining permit checks | Do not add a second attestation implementation; reconcile ASCII-hex versus raw credential encoding explicitly |
| Guest startup/interface permissions | Start service before container RPCs; restrict its socket and AA/CDH interfaces; disable application-accessible key endpoints | Existing Qwen application fetches its own key over localhost HTTP; storage credentials must remain in trusted infrastructure |
| Userspace tools | Include matching `cryptsetup`, `veritysetup`, `integritysetup`, `dmsetup`, `mkfs.ext4` and required libraries | These are guest infrastructure, not a privileged application/helper container |
| Guest kernel | Inspect the GPU guest for matching device-mapper, crypt, verity, integrity, ext4 and crypto support; add missing matching modules first | A kernel source change is not a prerequisite. Rebuild only if the required configuration/modules are genuinely absent |
| NVIDIA driver, CUDA, GPU attester | Preserve the working baseline and validate coexistence with storage | Change our over-restrictive device/hook handling where necessary, not the working GPU stack indiscriminately |
| InitData and agent policy | Generate exact new workload/binding policy; measure and approve the resulting guest | Mount destinations and key access are security-relevant changes |

The native patch currently changes Kata agent `main.rs` and `rpc.rs` and adds
`storage_service.rs`. Reuse Kata's process/cgroup/namespace lifecycle. The current
patch includes strict admission/diagnostic restrictions as well as storage hooks;
do not describe the integration as a single harmless mount call. Review and record
that full delta when rebasing onto the actual GPU agent source/build features.

Keep cryptographic storage code outside the agent in the private daemon for this
iteration. Moving it into CDH could be an upstream architectural follow-up, but
patching both large components now is not the minimum integration path.

The planning baseline removed all devices and host binds and rejected hooks.
The [GPU candidate](gpu-guest-candidate.md) now preserves bounded guest-generated
NVIDIA CDI while still rejecting host hooks and raw storage exposure. Hardware
validation is pending. Allowing all devices or restoring arbitrary host mounts
would defeat this design.

## What is installed or configured outside the guest

| Component | Minimum action | New runtime service? |
| --- | --- | --- |
| Versioned runtime handler and RuntimeClass | Point at the combined measured GPU/storage guest, retaining the existing handler for rollback | No new controller |
| Kata Go shim, QEMU, CRI-O | Keep binaries; configure the new handler and validate two/multiple block attachments with GPU passthrough | No source changes planned; observed integration failures may change this assessment |
| Existing TopoLVM/LVMS CSI | Use existing Block PVC provisioning and attachment | No new CSI driver |
| Existing internal OCI registry | Reuse it for the complete model-distribution E2E | No new registry |
| Encrypted model populator | Reuse existing implementation, its source CRD, registration/RBAC and temporary import Jobs; enable it for the target namespace when testing population | Existing controller; no importer needed merely to reopen a populated PVC |
| Storage authority | Deploy the existing code persistently with stable TLS, protected signer and retained database; adapt its attestation interface | One persistent custom service; it is not included in stock Trustee |
| Standard pattern Trustee | Provision scoped keys, policies and approved references; retain existing GPU attestation | No replacement KBS/AS and no source fork assumed |
| Proposed ConfidentialVolume API | Add namespaced CRD and configuration compilation; see the two stages below | A new controller is deferred in the smallest first integration |
| KServe and chatbot | Update desired InferenceService, measured startup and PVC bindings; preserve vLLM GPU settings and existing chat endpoint | No new serving operator |

The current populator watches one configured namespace. Reuse its image/code;
deploy/configure the necessary namespace instance rather than accidentally
installing competing controllers for the same source/claim. Preserve the existing
populator's tested namespace restrictions and registry trust configuration.

No CAA/peer-pods CSI component is required for this bare-metal path. No application
or application-side storage helper receives `privileged`, CAP_SYS_ADMIN, mapper
control or keys. Trusted guest root still performs privileged kernel operations.

## Two stages for the new API

### Stage A: minimum running GPU/storage integration

Use the [ConfidentialVolume resource shape](confidential-volume-api-proposal.md)
as declarative input. A deterministic administrator/build-time compiler resolves
the actual claim UIDs and approved manifests, validates the bindings and emits the
exact guest configuration/InitData. The authority independently approves it.

This can run as a CLI or controlled one-shot Job. A CRD alone does not reconcile
anything; clearly label this stage as explicit compilation, not an automated
operator. If CR instances are stored in the API, install the schema, but do not
claim Ready/status automation until its reconciler exists. Deployment requires
the explicitly generated approved contract, not merely creation of the CR.

Retain the authority even in this stage: static configuration does not replace
durable RW first-use/ownership state or approved model freshness. Use the retained
encrypted model disk and original credential for initial testing. Complete the
existing OCI/populator distribution test after the combined guest works.

### Stage B: reconciliation of the same API

Add a small `ConfidentialVolume` reconciler around the same compiler. It watches
declarations and PVC identities, resolves authority approvals, publishes immutable
public contract artifacts and status, and makes missing/invalid approval visible.
It never retrieves volume keys or obtains an unrestricted approval-signing key.

For minimum deployment overhead, it can be an additional reconciler in the
existing populator controller binary after reviewing namespace/RBAC changes.
The responsibilities remain separate: population moves ciphertext; resolution
prepares authorized consumption metadata. Do not colocate the trusted authority
or its private signer in that untrusted controller process.

Keep explicit Helm/GitOps references to generated contracts initially. No mutating
webhook is required. A binding change creates a new approved contract and workload
rollout; the controller must not rewrite a running pod's security configuration.

## Standard Trustee adapter and remaining hardware gate

The [standard Trustee adapter](standard-trustee-adapter.md) now implements
operation authentication through the existing KBS RCAR `/auth` and `/attest`
interfaces. The authority retains a fresh KBS session for each pending request,
verifies signed CPU+GPU claims and requires the guest's request-specific proof.
This preserves request freshness and ownership without adding custom runtime
claims to the standard EAR. No Trustee, AA or CDH server source change was made.

Key retrieval remains a separate private CDH operation authorized by an opaque
storage permit. The authority adapter never requests a storage key from KBS.

The implemented TLS client passed a live `/auth` probe; signed synthetic claim
and replay tests passed. Real composite `/attest` interoperability, exact live
claim shape and key retrieval still require the GPU-compatible candidate guest.
Do not enable initialization or key permits for that candidate until those tests
pass. The isolated CPU AS mode remains available only for its existing tests.

## What stock OpenShift already provides

The current [OSC 1.13 bare-metal configuration guide, section 4.9.2](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_confidential_containers_on_bare-metal_servers/configure-cc-overview_metal-cc)
documents raw-block LUKS storage inside the TEE using `osc-storage-helper` and a
Trustee-provided sealed passphrase. Its published example uses privileged helper
and application containers, shared process namespace and a postStart hook.
Therefore, “stock CoCo has no encrypted persistent storage” would be inaccurate.

That current example references helper version 1.13.1. Our recorded installed
baseline was OSC 1.13.0; compare exact installed artifacts before asserting feature
parity. Documentation review is not a live verification of the helper on this GPU
guest, nor proof that other upstream implementations cannot provide more.

## Gap register: classify rather than overclaim

| Requirement / issue | Classification | How to establish it |
| --- | --- | --- |
| Filesystem mounts prepared by guest infrastructure for a capability-free app | Product integration gap relative to the documented privileged-helper recipe | Show that our workload has no privileged app/init/sidecar and identify the required agent hooks |
| Authenticated RO verity root plus independently approved model generation | Additional model-security feature | Demonstrate tamper/old-release rejection; do not infer stock support or absence from basic LUKS documentation |
| Keyed integrity and controlled first-use formatting with durable recovery state | Additional storage-security/lifecycle feature | Audit the exact stock helper and installed guest; compare its behavior with our tested requirements |
| Declarative ConfidentialVolume and per-container authenticated bindings | New API/controller feature | Record CRD/compiler/controller additions separately from Kata changes |
| OCI encrypted-disk population | Add-on distribution component | Existing custom populator/source CRD; stock CSI still performs provisioning |
| Matching dm-integrity/tools in the GPU initrd | Guest packaging item to verify | Inventory exact GPU image, kernel configuration and modules; CPU-image results are not sufficient |
| CPU-only KBS wrapper, one-volume service, fixed RW geometry, UID 1000 | Our prototype limitations | Fix our code; do not report these as stock OpenShift limitations |
| Custom authority protocol versus AllInOne Trustee interface | Our protocol compatibility gap | Verify a supported adapter using real evidence and signed claims |
| Crash recovery, fencing and operator upgrades | Operational work still required | Failure/restart tests and explicit recovery runbook; no force-takeover shortcut |
| Whole-disk RW rollback | Unsolved by LUKS/dm-integrity alone | Separate externally anchored freshness design if required |

For the eventual stock-gap report, record for every item: exact installed version,
documented behavior, source inspection, reproduced behavior, our added files or
configuration, tests, and likely upstream owner. Distinguish “not documented”,
“not tested”, “prototype does not support”, and “demonstrated absent”.

## Delivery order

1. Inventory exact GPU guest modules/interfaces and pin its current source baseline.
2. Resolve private CDH key access and authority compatibility; preserve current
   GPU evidence verification and resource policy.
3. Generalize the existing service/agent for multiple volumes, package a separate
   measured GPU guest and generate static authenticated bindings.
4. Prove Qwen with the available RO disk and a fresh RW claim, with no application
   privileges, then prove fresh-VM persistence and denial/cleanup.
5. Exercise OCI/populator distribution and add the CRD reconciler using the same
   descriptor format. Preserve a clear report of which stages are implemented.

This gives a small initial integration surface: one patched guest agent, one
packaged private storage daemon, guest dependencies/configuration, one persistent
authority and deployment manifests. Reuse the existing populator when needed;
automate the new API only after the guest contract is working.
