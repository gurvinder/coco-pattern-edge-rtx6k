# Private native-agent guest preparation

Development artifacts only. No image produced here is automatically approved for
keys or installed. The separate test handler now runs this native integration.
The [live RO PVC hash-verification checkpoint](../../../docs/confidential-storage/native-ro-pvc.md)
passed; model inference and the RW example remain incomplete.

`build.py` produces a pinned base plus compressed newc overlay. It replaces only
`/usr/bin/kata-agent`; all other additions must be new paths. Tools, modules and
private AA client are pinned to the previously verified kernel diagnostic inputs.
Agent/service ELF hashes must be supplied explicitly and are recorded in output.
The result needs independent SNP launch measurement, strict InitData/policy,
private-service boundary checks, real attestation and negative cases before
Trustee may release a test key. There is no diagnostic console quote emitter.

`start-service.sh` runs as trusted guest root before container RPC admission. It
loads the matching guest storage modules, creates private tmpfs staging and starts
the daemon. It refuses prior staging/service state and never formats a device.
Read-only opening is owned by the service; RW initialization is not yet connected.

`prepare-kbs.py` creates private short-lived TLS/signing material outside Git.
It configures TLS 1.3, `DenyAll` administration, verified certificate-backed EAR
signatures, offline VCEK lookup and an initially deny-all resource policy. It
creates no resource or model key. The recorded test instance binds compiler port
8444 only; no Kubernetes Service or Route was added. Unauthorized resource and
admin requests return 401; TLS 1.2 is rejected.

The test KBS uses Trustee commit
`71251310045d8be2e95041cf0028691623c73957`, Rust 1.95.0 and its existing Cargo.lock.
To select only SNP, the optional non-s390x `attestation-service` dependency's
feature list in `kbs/Cargo.toml` changes from `all-verifier` to `snp-verifier`.
Build with:

```sh
cargo +1.95.0 build --locked --release -p kbs --bin kbs \
  --no-default-features \
  --features coco-as-builtin-no-verifier,attestation-service/snp-verifier
```

This changes only the isolated build's feature selection. Production Trustee,
its CPU+GPU policy, KServe and Qwen are outside this experiment.

`prepare-policy.py` generates the exact RO verification command and strict
InitData. The tested policy pins the image digest, command, UID/GID, rootfs and
environment; only the measured pause is separately admitted. Its Regorus test
uses actual downstream protobuf serialization, accepts the two intended requests
and rejects image, command, environment, UID, rootfs and raw-device substitutions
plus exec/copy/policy/swap/clock/stdin bypasses. Reproduce it by copying
`policy-tests.rs` to the prepared agent's `src/agent/tests/storage_policy.rs`,
setting `STORAGE_POLICY_TEST_DIR` to the generated output and running:

```sh
cargo +1.94.1 test --locked -p kata-agent --no-default-features \
  --features init-data,agent-policy --test storage_policy
```

The initial workload verifies all nine published file hashes and rejects writes.
It is not an inference server. The strict policy has admitted the live request;
actual activation/key/PVC hash verification has now passed. No broad
policy or diagnostic InitData may be admitted as a fallback when it rejects.

`prepare-authority.py` creates a fresh actual RO owner record from the published
manifest, with the strict policy digest and exact InitData. It generates a public
guest configuration and private authority database/receipt signer outside Git;
it neither authorizes a measurement nor provisions the model key. The authority
and KBS share the isolated TLS identity on separately pinned ports.

The recorded candidate guest rebuilds byte-for-byte. `measure.py` calculates its
SNP launch measurement independently from pinned kernel, firmware and candidate
initrd, using the intended warning-level agent logging command line. Each new image remains
unapproved until live configuration, policy and private boundary checks pass.
The exact recorded v9 image is installed and authorized only for the isolated
verification workload. See public build/measurement metadata under
`../evidence/2026-10-14/native-integration/`.


`chart/` renders the isolated RO probe using the already populated model PVC and
`kata-snp-storage-probe`. It requires generated InitData and workload values,
permits only the test namespace, disables the service-account token and runs UID
1000 with a read-only rootfs, no privilege escalation and all capabilities dropped.
The block attachment is an agent transport input; it must be removed before the
app starts and replaced by the guest's verified read-only `/models` mount.

```sh
python3 prepare-policy.py --output /scratch/model-policy
# Run from this directory; construct only public chart values.
python3 - <<'PYVALUES'
import json
from pathlib import Path
p = Path("/scratch/model-policy")
v = {"initdata": (p / "initdata.value").read_text(),
     "workload": json.loads((p / "workload.json").read_text())}
Path("/scratch/model-probe-values.json").write_text(json.dumps(v))
PYVALUES
helm template storage-model-probe chart --namespace confidential-storage-test \
  -f /scratch/model-probe-values.json > /scratch/model-probe.yaml
# Only after the matching measured image/authority are installed:
oc create -f /scratch/model-probe.yaml
```

Do not treat a pending pod as a passed test. Capture events by pod UID because
recreated pods reuse the name. A failed first activation deliberately retains its
reservation; subsequent `only one measured consumer per guest` errors can be
secondary retries. Inspect the first fixed service-stage denial before deleting
the test pod, and preserve authority state for conservative recovery.


For the eventual exact resource rule, use `prepare-release-policy.py` with the
independently calculated measurement and the raw generated InitData TOML. Run
`kbs-policy-tests.rs` as a `policy-engine` example in the pinned Trustee tree:

```sh
cargo +1.95.0 run --locked --release -p policy-engine \
  --example storage_release_test -- /scratch/release.rego
```

The policy file consumed by KBS is `storage/kbs/resource-policy.rego`. The original
setup omitted `.rego`, causing upstream's default rule to load instead. No key
existed then; this was fixed and live deny-all verified before provisioning.
`test_native_kbs_package.py` prevents regression of this filename contract.
Package the authority's sibling `confidential-storage-guest/profiles` directory,
preserve private directory permissions, and verify the server actually starts.


## CPU inference mode

`prepare-policy.py --mode serve` embeds the exact Qwen server and verified file
inventory. The [live serving guide](../../../docs/confidential-storage/native-model-serving.md)
records the completed SNP/Trustee/OCI-populated PVC inference, actual responses,
negative tests and reproduction. This uses Transformers on CPU; it does not take
a GPU from production. The default `--mode verify` remains the small hash-check
workload. Native RW initialization is still incomplete.
