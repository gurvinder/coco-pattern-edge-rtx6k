# Controller-managed Qwen sidecar example

`qwen-workload.json` is the actual public `ConfidentialWorkload` intent used in the
edge lab. It contains one stock vLLM application and bindings to `qwen-gpu-model`
at `/models/qwen` and `qwen-gpu-data` at `/data`. The controller generates the
KServe InferenceService from a separately signed sidecar deployment package.
Kata and the vLLM image remain stock; only the guest storage helper is privileged.

The [deployment guide](../../../docs/confidential-storage/controller-sidecar-deployment.md)
contains installation order, one-time requirements, the CR structure and signing
commands. The [sidecar example](../stock-agent-sidecar/qwen-example/)
contains the reviewed public renderer inputs. The
[live evidence](../evidence/2026-10-14/controller-sidecar/)
records adoption, controller restart, service recreation and actual GPU inference.

## Reuse on this lab

The declaration is already installed. Reapplying the identical intent is
idempotent. The existing PVCs, Trustee resources, authority enrollment and signed
public deployment package must remain present. Inspect status with:

```bash
oc get confidentialworkload,confidentialvolume,inferenceservice -n confidential-storage-test
oc get confidentialworkload qwen-storage -n confidential-storage-test -o yaml
python3 tests/confidential-storage/gpu-guest/audit-chat.py \
  --kubeconfig /secure/cluster.kubeconfig --stock-sidecar \
  --output /tmp/chat-validation.json
```

Do not apply the older manually rendered InferenceService on top of this owner.
A new cluster needs its own claim/source UIDs, resource keys, authority enrollment,
reviewed package and approval. Omit `adoptExistingUID` when creating a new target;
the checked-in UID records the exact original service adopted during migration.
It is not a name-based adoption permission. After recreation ownership uses the
ConfidentialWorkload UID, so the old migration UID need not be changed.

No decryption key, private signing key or cluster credential is present in this
example. Key references and public fingerprints are configuration, not key values.
