# ConfidentialVolume and authenticated workload bindings

**Current status, 14 October 2026:** the two-volume Qwen GPU E2E and namespace
ConfidentialVolume controller are implemented and validated. The controller
resolves declarations/bindings, verifies signed deployment packages and publishes
immutable configuration/status; trusted preparation and rollout remain explicit.
See the [current walkthrough](qwen-storage-walkthrough-and-gaps.md),
[controller guide](confidential-volume-controller.md) and
[baseline gap report](minimal-integration-and-stock-gaps.md).

This document retains the API design and intended security requirements that
preceded those implementations. It extends the
[Qwen/KServe integration plan](qwen-kserve-gpu-integration-plan.md); implementation
status and tested limits are recorded in the current guides above. The initial
live workload binds one RO model and one RW data volume to one consumer.

## Resource responsibilities

| Object | Responsibility |
| --- | --- |
| Existing `EncryptedModelSource` | Identifies immutable encrypted OCI content for the custom populator |
| Standard Block PVC | Requests storage; optionally references `EncryptedModelSource` through `dataSourceRef` |
| Proposed `ConfidentialVolume` | Requests the encryption profile, access, key resource and initialization behavior for one claim |
| Proposed workload binding annotation | Requests which container receives each declared volume and its destination |
| Authenticated descriptor and authority records | Authorize the exact configuration, model generation, first use and volume ownership |

The agent must not infer behavior from names such as `model`, `data`, a raw-device
pathname or an arbitrary label. A volume is managed as confidential storage only
when it has an explicit binding covered by an authenticated workload contract.
An encrypted volume's declaration does not automatically enroll it into every pod
that references the claim. For the initial strict workload profile, reject any
raw block attachment that is not accounted for by that contract.

Multiple models are simply several ReadOnly bindings. Multiple writable disks
are several independently owned ReadWrite bindings. The storage layer does not
choose which model vLLM loads or make one vLLM process serve multiple base models.

## Proposed ConfidentialVolume resources

Both declarations and claims are namespaced and must share the consumer's
namespace. `claimRef.uid` pins the actual claim incarnation, preventing accidental
reuse after deletion/recreation; it is administrative identity, not cryptographic
proof of the attached disk. Obtain the UID after provisioning and before approval.
The examples use placeholders and cannot be applied as complete approved resources.

```yaml
apiVersion: storage.confidentialcontainers.org/v1alpha1
kind: ConfidentialVolume
metadata:
  name: qwen-model
  namespace: gpu-workload
spec:
  claimRef:
    name: qwen-model-ro
    uid: "<actual-pvc-uid>"
  access: ReadOnly
  profile: luks-verity-v1
  initialization:
    policy: Never
  content:
    approvedManifestRef: qwen-release-v1
  keyResource: kbs:///default/model-keys/qwen-luks-v1
---
apiVersion: storage.confidentialcontainers.org/v1alpha1
kind: ConfidentialVolume
metadata:
  name: qwen-working-data
  namespace: gpu-workload
spec:
  claimRef:
    name: qwen-data-rw
    uid: "<actual-pvc-uid>"
  access: ReadWrite
  profile: luks-integrity-v1
  initialization:
    policy: OnceIfApproved
    filesystem: ext4
    owner:
      uid: 1001
      gid: 1001
  keyResource: kbs:///default/volume-keys/qwen-data-v1
```

`luks-integrity-v1` is a proposed API profile name. Its implementation must map
to an exact versioned, hashed cryptographic/filesystem profile, including tested
geometry and UID/GID. Do not let the host supply arbitrary cryptsetup arguments.

`approvedManifestRef` is an identifier in the trusted authority's release catalog,
not a proposed additional Kubernetes CRD or an arbitrary URL to fetch. The resolved
approval authenticates model generation, disk/header identity, size, verity root
and key resource. The authority rejects old disallowed generations even when an
old disk is cryptographically intact.

`keyResource` names a Trustee resource, not a Kubernetes Secret containing the key.
It must agree with the approved model manifest or RW authority record. A CR edit
cannot redirect a guest to another key. Use separate model and RW credentials.

Treat `spec` as immutable in this first API version. Changing a release, profile,
key identity or claim requires a new reviewed declaration and workload contract.
It must not reset the underlying authority record or grant first-use approval.
RW key rotation/adoption requires an explicit future transition protocol rather
than a casual edit to `keyResource`.

## Workload attachment and container bindings

The proposed annotation belongs on the actual predictor pod template, through
KServe's predictor annotations. A controller validates it before generating the
authenticated descriptor. Annotation presence alone is not authorization.

```yaml
spec:
  predictor:
    annotations:
      storage.confidentialcontainers.org/bindings: |
        [
          {"volume":"base-model", "confidentialVolume":"qwen-model", "container":"kserve-container", "mountPath":"/models/qwen"},
          {"volume":"embedding-model", "confidentialVolume":"embedding-model", "container":"kserve-container", "mountPath":"/models/embedding"},
          {"volume":"working-data", "confidentialVolume":"qwen-working-data", "container":"kserve-container", "mountPath":"/data"},
          {"volume":"checkpoints", "confidentialVolume":"qwen-checkpoints", "container":"kserve-container", "mountPath":"/checkpoints"}
        ]
    containers:
      - name: kserve-container
        # Remaining serving configuration omitted.
        volumeDevices:
          - name: base-model
            devicePath: /dev/coco-volume-0
          - name: embedding-model
            devicePath: /dev/coco-volume-1
          - name: working-data
            devicePath: /dev/coco-volume-2
          - name: checkpoints
            devicePath: /dev/coco-volume-3
    volumes:
      - name: base-model
        persistentVolumeClaim:
          claimName: qwen-model-ro
          readOnly: true
      - name: embedding-model
        persistentVolumeClaim:
          claimName: embedding-model-ro
          readOnly: true
      - name: working-data
        persistentVolumeClaim:
          claimName: qwen-data-rw
      - name: checkpoints
        persistentVolumeClaim:
          claimName: qwen-checkpoints-rw
```

The embedding/checkpoint claims and declarations must also exist. The fragment is
illustrative, not a complete deployable InferenceService. Raw attachment paths are
transport identifiers; the guest strips these storage devices before the process
starts and supplies only the approved filesystem mounts. GPU devices use their
separately validated runtime path and must not be stripped indiscriminately.

Start with one consuming application container and one binding per logical volume.
Supporting arbitrary sidecars or sharing a filesystem among several containers
requires additional reference counting, access and shutdown rules. Reject that
configuration initially rather than guessing. Generalizing the list of volumes
does not require generalizing the number of consumers in the same change.

## Trusted contract and device identification

The controller compiles a deterministic descriptor containing:

- Schema version, workload/policy identity and exact consumer identity.
- Each binding ID, declaration identity, PVC incarnation and transport attachment.
- Logical volume ID, approved generation, geometry and profile digest.
- Exact key resource, access, destination and mount flags.
- Authenticated RO manifest identity or RW initialization approval identity.
- Required attestation profile and the authority/Trustee trust configuration.

The descriptor bytes, or their digest with verified external content, must be
covered by measured InitData and approved by the trusted verifier/authority. An
optional signed descriptor must use a pinned trusted approval key and freshness
rules. A controller running under the untrusted cluster administrator must not
possess a signer that can authorize arbitrary requests without independent checks.
Merely hashing host-provided YAML does not make that YAML authorized.

The agent need not query the Kubernetes API. It verifies the contract delivered
to the guest, matches attachments against that contract, retains device FDs and
verifies the actual disk/header/content identity before activation. A malicious
host can lie about device paths, PVC UIDs and attachment metadata; cryptographic
disk checks and authority approval still decide whether the volume may open.

A truly blank RW disk has no prior cryptographic identity. First use therefore
requires an explicit approved logical-volume enrollment, size/profile checks and
a complete blank scan. Initialization creates and durably registers its identity.
Do not claim the blank scan proves the host attached a particular physical disk.

## Initialization and subsequent access

`OnceIfApproved` is a request for a permitted first-use transition, never an
instruction to run `luksFormat` whenever a header is absent.

| Authority state | Guest behavior |
| --- | --- |
| Unregistered / no valid initialization approval | Deny first use |
| Registered and explicitly approved | Verify contract and blank device; acquire ownership and begin journaled initialization |
| Initializing / uncertain prior operation | Refuse a new format or takeover; require deliberate recovery |
| Ready | Verify authenticated header/profile and reopen; never format |
| Revoked / conflicting owner | Deny access |

Retain the existing four journaled phases: LUKS format, integrity initialization,
filesystem creation and durability barrier. The guest closes initialization
mappings and completes Ready before obtaining its ordinary reopen permission.
Application mounting and key retrieval remain bound to the active grant/session.

`Never` allows no destructive initialization. It fits immutable model disks and,
with an appropriate approved profile, preexisting RW volumes. Adopting existing
encrypted storage requires its original key and authority record, or an explicit
reviewed adoption procedure; creating a new declaration does not authorize it.

Each volume has independent state, credential and cleanup. Acquire in a canonical
logical-volume order. Admit the application only when all required volumes are
mounted. If one fails, close successfully opened peers where safe and retain any
uncertain initialization/cleanup ownership. Never reset a completed volume to
Blank because a different mount failed.

Consumer restart does not rerun the populator or formatting. New RO releases get
new source/claim/approval identities. Clean RW reopen retains data. Abrupt loss
requires ownership/fencing recovery; a timeout alone must not grant a second writer.

## Validation, status and deletion

Admission/controller checks and independent guest checks must reject:

- Missing, cross-namespace or recreated claims; non-Block PVCs; unsupported sizes.
- Unknown profiles or contradictory access/initialization/content fields.
- Duplicate logical volumes/devices, missing bindings and unbound raw attachments.
- Duplicate or nested destinations, traversal, and destinations over guest control
  paths, `/dev`, `/proc`, `/sys`, application root or required runtime directories.
- Container mismatches, access escalation, arbitrary host bind mounts and key URIs
  outside the approved resource allowlist.
- Oversized descriptors. Set and test a bounded volume count and serialized-size
  limit within the guest's current 64 KiB InitData constraint; do not promise an
  unlimited number of volumes.

Proposed status fields are `observedGeneration`, `resolvedClaimUID`,
`contractDigest`, `authorityVolumeID` and conditions such as `Resolved`,
`Approved`, `StorageReady` and `Blocked`. These are operational summaries, not
authorization inputs. The guest verifies the authority's authenticated response;
it never trusts a host-editable `status.phase: Ready`.

Deleting a declaration must not wipe the disk, delete its key or erase authority
history. Retain claims/state by default. Revocation, backup, deliberate data
destruction and cryptographic erasure are separate explicit operations. Removing
a CR also cannot revoke plaintext or a key already held by a running guest.

## Implementation and verification

1. Add a structural CRD schema, immutable-spec rules, namespaced RBAC and opt-in
   Helm resources. Keep the existing `EncryptedModelSource` API unchanged.
2. Implement resolution and canonical descriptor generation with the independent
   approval boundary. Preserve actual KServe mutations in the measured contract.
3. Generalize guest configuration, RPCs, device resolution and service state from
   one volume to a bounded map. Retain existing opener/initializer safeguards.
4. Reuse the working GPU guest's private CDH/AA/standard Trustee key path; keep
   application key access disabled and authority-grant checks enforced.
5. Exercise one RO plus one RW volume, then two RO plus two RW volumes. Test
   swapped attachments, duplicate identities, stale approvals, partial activation,
   denial of one key, failed cleanup and fresh-VM persistence independently.

The first integration can consume the retained encrypted model disk/claim and its
matching credential. Respect PVC namespace constraints; importing the same local
ciphertext into a new claim is distinct from republishing or re-encrypting it.
The eventual OCI/populator E2E remains a separate required distribution check.

This API is configuration and lifecycle integration, not a new CSI driver. Existing
CSI attaches ciphertext; trusted guest code opens the encrypted filesystems and
exposes the declared paths to unprivileged applications.
