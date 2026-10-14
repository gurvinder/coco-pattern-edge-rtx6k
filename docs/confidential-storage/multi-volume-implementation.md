# Multi-volume integration checkpoint — 14 October 2026

**Development implementation; not installed in the GPU guest.** The earlier
single-volume CPU confidential RO-model and RW-storage examples remain the live
E2E baseline. This checkpoint extends their code without changing Qwen, KServe,
Trustee policies, existing runtime handlers, retained disks or keys.

## Implemented

| Component | Change | Validation |
| --- | --- | --- |
| `charts/coco-supported/confidential-volumes` | Namespaced immutable `ConfidentialVolume` schema, including profile/access/initialization constraints | Helm lint and OpenShift server dry-run passed; CRD not installed |
| `components/confidential-volume-contract` | Offline JSON compiler for Pod/KServe bindings, actual PVC UIDs, exact devices and destinations; synthetic four-volume example | 14 tests; deterministic bounded output; no cluster mutations |
| Private storage service | Version 2 configuration and group activation for 1–8 volumes in one consumer | 8 tests in both default CPU and opt-in CDH builds |
| Native Kata overlay | Resolve each SCSI device by approved transport path; validate the complete returned mount set before exposing it | 12 tests against pinned downstream Kata 3.31.0 |
| Guest profiles/authority/client | New hash-distinct v2 profiles with authenticated destinations; RW UID/GID 1001; legacy profile bytes preserved | 55 guest tests passed, 3 hardware/tool-dependent tests skipped; 110 authority and 3 client tests passed |
| Private CDH key adapter | Fixed root-private ttRPC socket, authority-derived permit, explicit raw32/hex64 decoding and bounded retrieval | Compiled; 2 adapter tests passed; no real GPU key retrieval in this checkpoint |

The compiler emits **an approval request**, not an approved service configuration,
InitData, signed grant or automatic Ready status. A deterministic hash cannot
replace independent approval. The explicit resolver/approval packaging between
that request and guest configuration is still to be implemented.

The schema does not install a controller. Existing OCI population code and the
underlying CSI are reused unchanged. It is not necessary to repopulate the retained
model disk to develop the guest integration; the complete OCI/populator route
will be tested after the combined guest works.

## Guest behavior

The measured configuration maps each binding ID to its transport path, volume
identity/generation, exact profile digest, consumer, destination and access.
`/models/qwen`, `/models/embedding`, `/data` and `/checkpoints` are valid examples.
They are explicit destinations, not names from which the guest guesses behavior.

The agent validates all attachments and resolves their actual SCSI devices. The
service reserves all container/device tuples before authority or disk operations.
Each volume follows its existing authenticated RO-open or journaled RW-first-use
state machine. Only a complete successful group returns mounts to the agent.
The application remains non-root, capability-free and without raw storage devices
or access to the private AA/CDH/service sockets.

Failure is not transactional rollback of formatting. A partially initialized disk
stays owned for explicit recovery. Close attempts all children in reverse order;
a failed child keeps ownership, and the group cannot admit a replacement consumer.
No force-unmount, automatic reformat or failed-disk adoption was added.

V1 configuration and profile hashes remain available for reproduction of prior
results. V2 is a distinct approval target: it does not reinterpret an old manifest
or change ownership of an existing filesystem automatically. The RW backend still
supports the tested 1 GiB geometry. The new RW filesystem owner is specifically
1001:1001; arbitrary owners accepted in a declarative *request* are not yet supported
by the backend. Shared model claims and multiple application containers are also
outside this initial implementation.

## Remaining integration gates

1. **Resolve and approve contracts.** Map API aliases to exact guest profile hashes,
   resolve the approved model manifest or RW initialization plan, bind workload
   identity and produce measured configuration/InitData. Reject unsupported owner,
   geometry and unresolved approval. Keep the signer outside the populator/controller.
2. **Standard Trustee operation authentication.** The subsequent
   [RCAR adapter checkpoint](standard-trustee-adapter.md) implements fresh
   request-bound handshakes against existing KBS endpoints. Unit tests and live
   TLS `/auth` passed; genuine CPU+GPU `/attest` and exact claim compatibility
   remain required. CDH key retrieval alone does not authenticate authority
   operations.
3. **GPU-compatible agent path.** The tested overlay still rejects GPU devices/hooks
   and skips NVIDIA CDI in storage guests. Review the actual GPU agent build and
   provisioning order, preserve only its required trusted operations and add
   exact admission tests. Do not deploy this storage-only overlay over Qwen.
4. **Combined guest packaging.** Reuse the working GPU AA/CDH/NVIDIA stack; inventory
   matching kernel modules and storage tools, package the new service/agent and
   configure root-private interfaces. Generate new measurements and scoped Trustee
   policies for the candidate, preserving the current runtime as rollback.
5. **Isolated live validation, then serving handover.** First run combined RO/RW
   attachments with real attestation and negative tests; then use an available GPU
   to verify vLLM, model hash, GPU CC state, `/data` persistence and chatbot health.
   Both GPUs were occupied at this checkpoint. No GPU workload was stopped.

CDH can reuse its upstream KBS attestation session. The adapter checks a fresh
short-lived authority permit, but that is not proof of fresh CPU/GPU evidence on
every `GetResource` call. Establish and document acceptable session lifetime and
resource-policy enforcement during live integration.

## Reproduction and evidence

- [Compiler instructions and synthetic example](../../components/confidential-volume-contract/README.md)
- [Private service build and version 2 protocol](../../components/confidential-storage-service/README.md)
- [Hash-locked native agent reproduction](../../components/confidential-storage-kata/native-agent/README.md)
- [Test record](../../tests/confidential-storage/evidence/2026-10-14/multi-volume-unit-validation.json)
- [Minimum component changes / stock OpenShift gaps](minimal-integration-and-stock-gaps.md)

Builds ran in an isolated non-root CPU compiler pod with no service-account token,
GPU, persistent volume or storage credentials. Agent source preparation also passed
from the pinned clean archive. Tests are source/protocol/lifecycle tests, not new
hardware E2Es. Existing downstream crypto deprecation and `unused_unsafe` warnings
remain.

Repository-wide checks were attempted: `preview-all` has no Make target;
`validate-schema` fails rendering the existing bare-metal values independently
because `global.hardware.profile` is absent. The focused new chart lint/server
validation passes. No changes were made under `common/` or to active GitOps values.

A final read-only check found the original running Qwen predictor UID
`d5cc3080-ae0b-4490-b145-56a216732566` still Running, 2/2 Ready, with zero restarts.
The existing chatbot remained Running/Ready with zero restarts. Historical failed
and completed Qwen pods were left untouched.
