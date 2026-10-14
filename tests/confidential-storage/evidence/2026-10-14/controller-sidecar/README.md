# Controller-managed stock-sidecar Qwen validation — 2026-10-14

The controller deployed the existing Qwen storage workload from a
`ConfidentialWorkload` declaration and independently signed public package. Stock
Kata, kernel/initrd/agent and the original NVIDIA vLLM image were retained. The
original `gpu-workload/encrypted-qwen` and port 30080 stayed ready and unchanged.
The tested chatbot remains `http://10.14.202.14:30081` on the VPN.

## Executed checks

- A tampered approval signature produced `Blocked / InvalidApproval`; the existing
  InferenceService spec, UID and ownership remained unchanged.
- Restoring the valid approval let the controller publish measured configuration
  and adopt only the explicitly approved service UID. It injected the sidecar
  setup without a manual InferenceService patch.
- The replacement VM verified all model hashes on `/models/qwen`, reopened the
  existing `/data` canary, passed a CUDA operation and served a fresh chatbot
  request. The test traced EndpointSlice target UID to that exact model pod and
  observed vLLM's success counter increase.
- Restarting the controller preserved the InferenceService UID, spec and resource
  version. Subsequent resyncs reported `Unchanged`.
- Deleting only the generated InferenceService caused clean sidecar shutdown and
  recreation from the same declaration/approval. A new service UID and VM/pod UID
  again passed hashes, persistence, CUDA and real chatbot inference.
- Authority generation 1 and initialization history stayed unchanged. The second
  transition added exactly two close and two reopen events; no formatting,
  re-enrollment, ownership recovery or key changes occurred.
- Administrator `oc exec ... -- id` was denied by guest policy in both containers.
  Controller RBAC denied Secret reads, PVC patching, exec and updates/deletes of
  unrelated targets. An out-of-allowlist service creation was denied in server
  dry-run by the admission policy.
- Live stock kernel/initrd/OVMF hashes matched the prior verified stock artifacts.
  Authority logs reported CPU and GPU `affirming` with hardware 2, configuration 3,
  executables 3. No arbitrary attestation tokens or credentials were recorded.

## Problems exposed and limitations

Adoption initially failed because Kubernetes requires delete permission on a
dependent when setting its owner reference. The Role now grants update/delete
only for the explicitly enabled target names; the controller issues no DELETE.
The live RBAC test includes this boundary.

Foreground deletion was blocked by two pre-existing completed pod API records.
Both containers had exited zero and a node CRI inventory contained no corresponding
sandboxes. Those two exact stale records were removed administratively. The current
VM closed storage and terminated normally; controller recreation then proceeded.
This is recorded in `stale-object-cleanup.json`, not hidden as fully unattended
infrastructure recovery. No active VM was force-stopped and no authority state
was reset.

An initial render lacked explicit namespace metadata and placed controller objects
in `default`; those newly created objects were removed before approval deployment.
The chart now explicitly sets the release namespace on its namespaced objects.
Final inspection found no leftover controller objects in `default`.

This run reuses a model PVC already populated from the internal OCI registry and
an already initialized RW PVC. It does not re-test import or blank-disk formatting.
Trusted package preparation/signing, renewal and volume/key enrollment remain
outside the controller. The 24-hour test approval's expiry is recorded; running
inference continues after expiry, but automatic recreation needs a renewed approval.

Local validation passed 29 controller and 15 contract tests, Black, Helm lint,
server dry-run and CEL type checking. The image build ran the then-current 27
controller cases; two additional ownership regression cases were added locally
without changing runtime source. Repository-wide `preview-all` is not a Make
target and `validate-schema` fails on the existing missing
`global.hardware.profile` in the shared clustergroup chart.

See `validation.json` for pinned identities, images, test scope and approval
expiry; `deployment.json` for the bound request and before/after service UIDs;
`chat-after-*.json` for actual inference; and the compact public application,
helper, authority and shutdown logs for the storage transitions.
