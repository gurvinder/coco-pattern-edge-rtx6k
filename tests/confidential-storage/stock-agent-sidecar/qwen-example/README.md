# Qwen sidecar deployment example

These are public, cluster-specific artifacts for `confidential-storage-test/qwen-storage`
on the edge RTX PRO 6000 lab. They contain no storage keys. The immutable ConfigMap
and KServe annotation contain the same attested InitData. Applying this manifest
alone does not enroll a volume or authorize a new identity.

The full integration, validation and limitations are documented in
[the Qwen add-on guide](../../../../docs/confidential-storage/stock-kata-qwen-storage-addon.md).
The [component README](../../../../components/confidential-storage-sidecar/README.md)
describes building the image and the guest privilege boundary.

To reproduce the rendered candidate from the checked-in public inputs, from the
repository root:

```bash
python3 components/confidential-storage-sidecar/prepare-qwen.py \
  --current-public-directory tests/confidential-storage/stock-agent-sidecar/qwen-example/public-inputs \
  --kserve-manifest tests/confidential-storage/stock-agent-sidecar/qwen-example/public-inputs/base-kserve.yaml \
  --bootstrap-configmap tests/confidential-storage/stock-agent-sidecar/qwen-example/public-inputs/bootstrap-configmap.json \
  --sidecar-image image-registry.openshift-image-registry.svc:5000/confidential-storage-test/confidential-storage-sidecar@sha256:aff5a4874e7edf39cd67bf03b7f5c31df70a4a2191bb24e7423e4f65bb798aba \
  --sidecar-build-name confidential-storage-sidecar-11 \
  --output /tmp/qwen-sidecar-rendered
```

`identity.json` intentionally retains the renderer's `rendered-only` label.
Live acceptance is recorded separately in the evidence directory. A new image,
startup command, volume binding, or stock runtime version changes the approval
inputs. Do not reuse these lab hashes as an approval for another deployment.

Install the Helm admission guard **before** granting the dedicated service account
its SCC (`identityEnabled`). `admission-values.json` pins the final reviewed image.
The optional anonymous registry permission applies only to this public add-on's
ImageStream, never the model repository. Review its build inputs first.

The existing RO/RW PVCs, authority database, keys and source object must already
exist. Follow the volume provisioning guide for a new installation. Migrate only
closed, Ready records; never reformat an existing RW claim. Update the specific
Trustee key bindings and retained authority policy after reviewing the new
measurement, InitData and agent policy. Then apply `configuration.json` and
`kserve.json`. The application remains the original NVIDIA vLLM image.

The prepared public inputs retain the earlier agent-integrated example only as
renderer input. Do not apply `public-inputs/base-kserve.yaml` as the sidecar workload.
