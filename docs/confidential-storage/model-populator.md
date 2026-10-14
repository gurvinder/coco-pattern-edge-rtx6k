# Encrypted model publisher and volume populator

**Current status:** the populated model PVC is now consumed by the validated Qwen GPU workload alongside guest-initialized RW storage. See the [current walkthrough](qwen-storage-walkthrough-and-gaps.md). Statements below about remaining guest integration describe this component's earlier checkpoint.

This development component implements the **ciphertext distribution** part of the
storage proposal. It prepares Qwen3-0.6B as a real LUKS2/ext4 image with dm-verity
over the complete header and ciphertext, publishes it to the internal OpenShift
registry, and populates an ordinary CSI raw-block PVC. It does not yet provide
the attested guest mount or confidential model-serving E2E. The application must
remain unprivileged when those pieces are connected.

Source: [`components/encrypted-model-populator`](../../components/encrypted-model-populator/).
Deployment: [`encrypted-model-populator` chart](../../charts/coco-supported/encrypted-model-populator/).

## Trust and layout

The publisher is trusted with plaintext and a separate 32-byte LUKS keyslot
credential. The registry, populator, CSI storage and host receive only ciphertext
and public metadata. Neither the controller nor importer has a model-key input
or permission to read Kubernetes Secrets.

The raw image has no partition table:

1. A fixed 16 MiB LUKS2 header and AES-XTS encrypted ext4 filesystem.
2. A dm-verity SHA-256 tree after the complete LUKS region. Its root covers the
   LUKS metadata, keyslots and encrypted filesystem, including free space.
3. Reserved trailing space, included in the transport digest but never mapped
   into the model filesystem.

The publisher reopens **verity → read-only LUKS → ext4 ro,noload,nodev,nosuid,noexec**,
reads the entire protected region, verifies every model file, and requires a write
attempt to fail. This immutable profile does not use dm-integrity: dm-verity
authenticates the fixed ciphertext. The private writable profile separately uses
LUKS2 plus authenticated dm-integrity and does not use dm-verity.

`guest-manifest.unsigned.json` records the verity root/geometry, LUKS UUID, full
header hash and model file inventory. **It is unsigned publisher output**, not an
authorization token. The independent release authority must approve and bind it
to the current release, measured guest policy and versioned key before a workload
receives a key. A mutable tag or a successful population is insufficient.

The registry artifact uses Docker schema 2, supported by the integrated registry.
Its sole gzip/tar layer contains exactly `volume.raw` and `release.json`. The
latter contains only the format, complete disk length and SHA-256. The image
configuration has the actual uncompressed layer digest; it is a data artifact,
not a runnable model container. A content-derived tag keeps it reachable by image
pruning; consumers use only its immutable manifest digest.

## Population lifecycle

1. An administrator creates an immutable `EncryptedModelSource` from the
   publisher's `source.json` and a new Block PVC with matching `dataSourceRef`.
2. The controller observes the claim. The CSI library creates a temporary prime
   PVC, and the existing CSI provisioner allocates its storage.
3. An isolated importer attaches that prime PVC. It authenticates the registry
   with TLS and its pull identity, verifies the pinned OCI manifest, streams the
   ciphertext layer, verifies all lengths/hashes, flushes the device, and reads
   the disk back to verify its hash again.
4. On success the controller records a completion binding on the prime PVC,
   deletes the importer, and waits until that pod is gone. Only then does the
   library rebind the PV to the workload claim and remove the prime claim.
5. An already populated, Bound target is reused. Reconciliation or a controller
   restart must not create another importer. A new release requires a new source
   and claim; source specifications are immutable.

On any import failure the target remains unpopulated and the failed importer is
retained for diagnosis. The controller does not continuously recreate a failed
writer. The temporary disk may contain partial ciphertext, but is never handed
to the workload by a successful reconciliation. Deleting a failed test target
invokes scoped cleanup. Never remove finalizers manually to fake completion.

This first implementation requires exact matching image/PVC/device sizes,
64 MiB–64 GiB, and RWO or RWOP block attachments. It is scoped to one namespace,
one storage class, and one registry origin; repository names must belong to that
namespace. Shared immutable-model attachments need storage-backend support and
separate guest lifecycle work. RWO alone is not cross-VM sharing enforcement.

## Privileges and upstream integration

The controller runs non-root with all capabilities dropped, a read-only root
filesystem and no privilege escalation. Mutation RBAC is namespaced except for
the PV patch permission required for rebind. It watches PVs, storage classes and
source custom resources. It does not need host mounts, devices or keys.

The **infrastructure importer** runs UID 0 so it can access its one assigned raw
block device, but has no added capabilities, no privilege escalation, no host
namespace/mounts and `privileged: false`. A dedicated SCC grants that identity
only to its service account. This is not a confidential application container.
The eventual serving application gets a directory mount and no raw device,
credential, mapper interface, privileged sidecar or `CAP_SYS_ADMIN`.

The controller uses CSI `lib-volume-populator` v3.2.0. Its upstream default ignores
target PVCs in the working namespace. A small, explicit opt-in extension allows
colocation so PVC/pod watchers and mutations remain namespaced. Prime PVCs lack
`dataSourceRef` and cannot recursively populate themselves. Original source
digests, Apache license, unchanged upstream tests and the extra regression are
under `components/encrypted-model-populator/third_party/lib-volume-populator`.
This local extension is not an upstream release or a support statement.

## Build and deploy the isolated controller

Use the development branch, not the live GitOps branch. Commands assume an
authenticated `oc`, Helm 3 and Go 1.25.5 or newer. Set `KUBECONFIG` through your
private environment; never place credentials in values files or this repository.

```sh
cd components/encrypted-model-populator
go test -race github.com/kubernetes-csi/lib-volume-populator/v3/populator-machinery ./...
python3 -m unittest -v test_publish.py
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath \
  -ldflags='-s -w' -o dist/encrypted-model-populator .
cd ../..
helm template encrypted-model-populator charts/coco-supported/encrypted-model-populator \
  -n confidential-storage-test --include-crds \
  -f overrides/values-encrypted-model-populator-edge.yaml > /tmp/model-populator.yaml
oc apply --dry-run=server -n confidential-storage-test -f /tmp/model-populator.yaml
oc apply -n confidential-storage-test -f /tmp/model-populator.yaml
oc start-build encrypted-model-populator -n confidential-storage-test \
  --from-dir=components/encrypted-model-populator/dist --wait --follow
```

The namespace must already exist. Record the pushed **digest** from the build,
then render/apply again with `controller.enabled=true` and `controller.image`
set to that full digest reference. The default controller is disabled until this
reference exists. The edge override selects `lvms-vg1`, the internal registry,
OpenShift CA injection, importer SCC and binary image build.

## Trusted publication

Run the publisher in an isolated trusted AMD64 Linux environment. The existing
unprivileged [`storage-compiler`](../../components/confidential-storage-kata/build-pod.yaml)
can host it for development. It needs no host device, PVC, GPU, KVM or elevated
capability. QEMU TCG runs a disposable **non-TEE publisher appliance**; it is not
the workload runtime. Use a fresh private working directory and enough scratch
space for plaintext, the encrypted disk and the OCI layer.

```sh
python3 tests/confidential-storage/kernel-lab/fetch.py --work /build/debian
python3 components/encrypted-model-populator/fetch_qwen.py --model /build/qwen-model
# KEY_FILE is a separately provisioned, private mode-0600 32-byte credential.
python3 components/encrypted-model-populator/prepare.py \
  --tools /build/debian/tools --model /build/qwen-model --key "$KEY_FILE" \
  --work /build/qwen-luks --disk-mib 2048
```

The fetcher reuses the pinned revision and input digests in the existing Qwen
producer without importing its old key/encryption code. The package fetcher
verifies the existing Debian package lock before extracting tools. These are
publisher tools; they do not establish compatibility with the deployed RHEL guest.

On success, the preparer deletes its plaintext filesystem, key-bearing initramfs
and extracted root tree. The input model directory and input key remain owned by
the trusted publisher. On failure private scratch is retained for diagnosis.
Never publish or commit that scratch, the key or a key-bearing initramfs.

Enable `publisher.enabled=true` and choose `publisher.repository` to render the
test ImageStream and a publisher service account permitted to push only there.
Supply a short-lived token and the injected service CA privately:

```sh
python3 components/encrypted-model-populator/publish.py \
  --disk /build/qwen-luks/volume.raw --work /build/qwen-oci \
  --registry https://image-registry.openshift-image-registry.svc:5000 \
  --repository confidential-storage-test/qwen-luks-e2e \
  --ca /build/registry-ca.crt --token /build/registry-token
```

`publish.py` accepts no key, refuses symlink/non-regular inputs and registry
redirects, checks the disk did not change during packaging, and reads the
published manifest back by digest. It emits public `source.json`. It does not
prove the disk is a valid authorized model; that is a separate guest check.

## Request a populated model claim

Use publisher output as `.example.source`, set `.example.enabled=true` and a fresh
`.example.name`, then render `templates/example.yaml`. The chart emits the source
CR and a raw-block RWOP claim. For example, the values document has this shape:

```yaml
example:
  enabled: true
  name: qwen-luks-model-v1
  source:
    format: luks-verity-v1
    repository: confidential-storage-test/qwen-luks-e2e
    manifestDigest: sha256:<actual-published-manifest>
    diskDigest: sha256:<actual-complete-disk>
    diskBytes: 2147483648
```

The placeholder digests are intentionally invalid. Use actual publisher output.
Watch the target PVC, prime PVC and importer. A Bound model PVC proves population,
not confidential decryption, attestation, model freshness or serving.

The remaining E2E integration must mount this disk through the authenticated
guest service, fetch the separately provisioned key only after approved evidence,
and serve Qwen from `/models` with no application storage privileges. The second
E2E must initialize and reopen a different blank private RW PVC through the guest
authority journal. Neither acceptance gate is satisfied by this component alone.
