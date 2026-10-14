# Attested inference from the encrypted model PVC

On 14 October 2026 the isolated **read-only model-serving E2E passed** on
`9c-63-c0-dd-06-52`, OpenShift 4.22.15. The application loaded Qwen3-0.6B from the
actual OCI-populated LUKS/dm-verity PVC, after real SNP attestation and Trustee key
release, and answered HTTP inference requests inside the confidential VM.

The **native read/write initializer and blank-PVC E2E remain incomplete**. This is
an experimental storage integration, not a production CSI implementation.

## Actual path

1. The [publisher and populator](model-populator.md) had already imported the
   encrypted disk from the internal OpenShift OCI registry into the 2 GiB raw
   block, RWOP `qwen-luks-model-v1` PVC. Serving reused that populated claim; the
   populator did not run again for each pod.
2. `kata-snp-storage-probe` launched the separately measured native guest. Its
   private storage service authenticated the model manifest and sealed header,
   obtained an authority-bound permit and retrieved the retained test key from
   the isolated Trustee KBS using fresh SNP attestation.
3. Trusted guest code opened dm-verity/LUKS and mounted ext4 read-only. The agent
   exposed `/models` to the application and removed its raw block-device access.
4. The measured application checked all nine publisher file hashes, UID/GID 1000,
   zero effective capabilities, NoNewPrivs, a read-only mount and absence of the
   raw device, mapper control and CDH socket. It loaded the safetensors locally
   with `trust_remote_code=False`, then served bounded chat requests.

The server used **Transformers on CPU with BF16**, in the pinned NVIDIA vLLM
image; it did **not** run the vLLM engine or allocate a GPU. Both existing GPUs
remained with production. One vCPU and 48 GiB guest boot memory were used, mostly
to accommodate the large image's download/unpack footprint. This is not a claim
that Qwen's weights require 48 GiB. The weights file is 1,503,300,328 bytes; the
serving image has 84 layers and approximately 9.01 GiB compressed data.

## Evidence and limitations

[Machine-readable checks](../../tests/confidential-storage/evidence/2026-10-14/native-serving/checks.json)
and [actual requests/responses](../../tests/confidential-storage/evidence/2026-10-14/native-serving/inference.json)
record these results:

```text
STORAGE_SERVICE mounted_ro
STORAGE_MODEL_SERVER_HASHES_PASS uid=1000 capabilities=0 readonly=true
STORAGE_MODEL_SERVER_READY port=8080 device=cpu
STORAGE_MODEL_INFERENCE_PASS
STORAGE_SERVICE closed
```

- “What is the capital of France?” → “The capital of France is **Paris**.”,
  10 completion tokens, 1.636 seconds.
- “What is 1 + 1?” → “1 + 1 equals 2.”, 9 completion tokens, 1.568 seconds.
- The first arithmetic canary, “Calculate 2 + 2. Reply only with the number.”,
  incorrectly returned `2`; its assertion failed. That response is retained in
  [the failed canary](../../tests/confidential-storage/evidence/2026-10-14/native-serving/arithmetic-canary-failed.json).
  The exact-word `hello` instruction was also not followed literally. This is a
  storage/inference execution test, not a model-quality acceptance result.
- A request selecting another model received HTTP 400. Admin `oc exec` received
  `PermissionDenied: ExecProcessRequest is blocked by policy`.
- With the same approved guest but KBS deny-all, real attestation reached key
  retrieval, KBS returned `PolicyDeny`, and the guest closed ownership safely.
- Normal shutdown also closed ownership. All previous authority records and
  events were retained; none was reset or silently taken over.

The test endpoint was pod-local port 8080, called from the isolated compiler pod.
It had no Service, Route or external exposure. Only public canary prompts used
plain HTTP. This does not demonstrate TLS termination in the TEE or protection of
private prompts. The temporary authority/KBS placement is still a lab arrangement.

Image admission uses the strict measured agent policy's exact image digest,
command and allowed environment. CDH's optional image-signature filter is not
configured; do not describe this as signature-based image policy.

[Final production audit](../../tests/confidential-storage/evidence/2026-10-14/native-serving/production-audit.json)
reports unchanged pod identities/readiness/restarts, resource specifications,
RuntimeClasses and GitOps sources. CRI-O stayed at PID 694089; there was no further
reload/restart. The successful test pod was removed after evidence capture.

## Reproduce the serving workload

First follow [native RO integration](native-ro-pvc.md) for the pinned agent/service,
private AA, real SNP verifier, isolated KBS, public guest configuration, measured
image and existing encrypted PVC. Keep private keys and authority state outside
Git. Fresh signing/TLS material changes the guest measurement; calculate and
approve your own resulting reference values rather than copying this run's values.

From the repository root, generate the exact server and strict InitData:

```sh
python3 tests/confidential-storage/native-guest/prepare-policy.py \
  --mode serve --output /scratch/serving-policy
```

The generator embeds [the server](../../tests/confidential-storage/native-guest/model-server.py)
and the publisher inventory in the exact admitted command. It pins image metadata
from `serving-image.json`, uses offline/local model loading and requests no GPU.
The resulting raw InitData must fit the guest's 64 KiB limit.

For a **fresh authority**, use `prepare-authority.py` with this policy directory.
For the existing isolated RO test, stop its authority and put its KBS into deny-all
first. `upgrade-ro-policy.py` registers a new logical RO access record for the
same immutable disk and revokes the old closed record. It preserves the database,
receipt signer, credential and event history. It refuses a record with an owner.
This is an offline administrator operation, not an automatic upgrade/recovery API.
Do not use it for RW storage or run it concurrently with the authority service.

Rebuild with `build.py`, calculate SNP references with `measure.py`, generate the
exact KBS rule with `prepare-release-policy.py`, and run both pinned policy-engine
tests. Install only the separate test initrd/config; do not replace production
artifacts. In the stopped test handler set `default_memory = 49152` and
`create_container_timeout = 600`; SNP hotplug did not supply the required memory.
Verify the actual QEMU command, including `-m 49152M`, against the measured inputs.

Render the Helm chart with generated workload/InitData and explicit resources:

```sh
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/scratch/serving-policy')
values = {
    'workload': json.loads((p / 'workload.json').read_text()),
    'initdata': (p / 'initdata.value').read_text(),
    'memoryRequest': '48Gi', 'memoryLimit': '48Gi', 'deadlineSeconds': 900,
}
Path('/scratch/serving-values.json').write_text(json.dumps(values))
PY
helm template storage-native tests/confidential-storage/native-guest/chart \
  --namespace confidential-storage-test -f /scratch/serving-values.json \
  > /scratch/serving.yaml
oc apply --dry-run=server -f /scratch/serving.yaml
```

Run effective KBS deny-all first, inspect the actual `PolicyDeny` and clean owner
close, then remove that test pod. Install the matching exact release policy and
create the same pod. The application starts only after key retrieval and mounting;
its first image pull/unpack took roughly two minutes here. Wait for the application
log marker `STORAGE_MODEL_SERVER_READY`, not merely Kubernetes pod readiness.

From an ordinary isolated test client inside the cluster, run:

```sh
python3 tests/confidential-storage/native-guest/test-inference.py \
  --base-url http://POD_IP:8080 --output /scratch/inference.json
```

That client sends a public Paris canary and checks rejection of a different model.
It does not exec into the confidential container. Also verify admin exec denial,
capture application logs and the production audit, remove the test pod normally,
and verify both guest mapping cleanup and authority owner release. Preserve a
consistent private authority/KBS backup before destroying temporary services.

## This run's references

- Successful pod UID: `bf8073ef-9f2f-4d53-a798-3702c7b2436a`.
- Image: `initrd-native-v12`, SHA-256
  `c8fb65e0013a2715d07559501dd56a54442d81c3d13c243f87ce308a7174fee1`.
- InitData SHA-256:
  `3e51184404f1258d128f3b0b51fc08ce62131d71e7b94f5a1bc14dcfc8ed04d6`.
- Agent policy SHA-256:
  `71818751ba7fee4d56f056778d14325dae8483b850da56091958f4cd05fcdd15`.
- [Independent launch measurement](../../tests/confidential-storage/evidence/2026-10-14/native-serving/measurement.json)
  and [actual boot inputs](../../tests/confidential-storage/evidence/2026-10-14/native-serving/live-boot.json).

The strict policy initially rejected CRI-O's rewritten `PATH`. A separate,
key-ineligible diagnostic confirmed all other admission predicates. The final
allowlist accepts only the known image/runtime PATH values; there is no permissive
fallback. Policy tests passed in the actual downstream Regorus engines: one agent
positive with 16 negatives, and one KBS positive with 15 negatives plus deny-all.
Two upgrade tests cover retained history/credentials and refusal of an active owner.
