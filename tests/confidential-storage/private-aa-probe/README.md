# Private AA hardware probe

This **no-secret diagnostic** boots a separate SNP guest, calls the guest's
private attestation-agent ttRPC socket, and verifies its evidence with an isolated
Trustee Attestation Service. It is not either encrypted-storage E2E. Never approve
this diagnostic guest measurement for a storage/model key.

## Verified on 13 October 2026 (UTC)

The later [live authority protocol probe](../../../docs/confidential-storage/live-authority-probe.md)
adds fresh hardware challenges/proofs, signed receipts, close and explicit replay
denial. It is still a no-key diagnostic. Its separate image is now selected in the
test handler; the fixed-evidence v4 image below is retained as an earlier checkpoint.

- Real SNP report version 5, VMPL 0. The report binds the fixed diagnostic runtime
  object and the explicitly supplied no-secret InitData.
- The pinned-source AS verifies the AMD endorsement chain/report signature and
  both bindings. Its ES256 EAR signature is verified with the pinned test signer.
- Changed runtime data, changed InitData and a modified report signature are each
  rejected for their specific verification error. A quote from an earlier boot
  without InitData was rejected with `Host Data Mismatch`.
- The application reports UID/GID 1000, `CapEff=0`, `NoNewPrivs=1`, no private AA
  socket and no device-mapper control. Guest policy rejects `oc exec` with
  `ExecProcessRequest is blocked by policy`.
- The unchanged default appraisal is **contraindicated**, not approved: executables=33,
  hardware=97, configuration=3. No approved launch/TCB reference set is registered
  in this isolated verifier. No storage key was released; no GPU was assigned.

The subsequent **isolated diagnostic** policy test is affirming: hardware=2,
executables=3, configuration=2. `measure.py` independently computes the exact
launch measurement from the pinned firmware/kernel/v4 initrd and fixed command
line; it never reads a hardware quote. `measurement.json` records those inputs.
The test policy also pins the diagnostic InitData, the recorded CPU-baseline TCB
and the configured SNP guest policy. This approves recognition of the diagnostic
only; its permissive no-secret agent policy is unsuitable for releasing keys.
The policy is installed only in the disposable loopback AS. Production reference
values, AS policies and KBS resource policies remain unchanged.

Nine checks pass in `appraisal-validation.json`, including rejection for wrong
launch measurement, TCB, guest policy and InitData references, and authority-side
rejection of wrong EAR policy, InitData and runtime nonce. The real signed EAR
passes the actual authority verifier. This re-appraises a previously captured
quote: it does **not** prove a fresh live authority challenge, guest-key possession,
key delivery or storage activation. Those remain necessary integration tests.

Reproduce the independent calculation and isolated appraisal:

```sh
python3 -m pip install sev-snp-measure==0.0.13
python3 tests/confidential-storage/private-aa-probe/measure.py \
  --boot-files /scratch/pinned-boot-files --output /scratch/measurement.json
# Run alongside the dedicated loopback AS, with the authority Python dependencies.
python3 tests/confidential-storage/private-aa-probe/verify-appraisal.py \
  --request /private/bound-request.json --signer /private/signer.pub.pem \
  --reference /scratch/measurement.json --output /scratch/appraisal-validation.json
```

`diagnostic_cpu.rego` explicitly accepts the handler's SMT setting and does not
require host-wide TSME; SNP private-memory protection is independent of TSME.
The four exposed TCB SVN fields use the existing recorded baseline. The pinned
SNP verifier validates the VCEK/report FMC match for this Turin report but does
not export FMC as an appraisal claim. This diagnostic baseline is not a review
of current AMD advisories or a production TCB approval. The diagnostic policy
and permissive agent policy must never be promoted to a storage-key allowlist.

Evidence is in [`../evidence/2026-10-13/private-aa-hardware/`](../evidence/2026-10-13/private-aa-hardware/).
Raw quotes, chip IDs, certificates and test signer private material are retained
outside Git. The compiler/AS is unprivileged, disposable and has no host/PVC/GPU
access. AS listens on loopback only; its administrative routes are not published.

## Build and deployment

Build from the pinned original image/client in the
[authority build lock](../../../components/confidential-storage-authority/attestation-build-lock.json):

```sh
python3 scripts/confidential-storage/build-attestation-probe.py \
  --base /scratch/kata-cc.initrd \
  --client /scratch/ttrpc-aa-client \
  --runtime-data tests/confidential-storage/private-aa-probe/runtime.json \
  --output /scratch/private-aa-probe.initrd
```

`build.json` records the current v4 image. The builder preserves all original
archive records and adds four entries: a root-only client, fixed script, systemd
unit and target symlink. `DefaultDependencies=no` avoids ordering through
`basic.target`; evidence goes to `/dev/hvc0` after a 20-second capture delay.
The embedded audience is deliberately unreachable and the public diagnostic key
has no associated signing service. No generic evidence interface is added.

The node installer consumes base64 of the exact image. It refuses changed
original configuration hashes or an existing destination/drop-in. It creates a
separate configuration and `kata-snp-storage-probe` handler, labels the test
initrd like the original boot artifact, and reloads CRI-O. It changes neither the
original handler nor original boot files. **Shared-runtime installation/reload
requires approval.** On this node `/opt` resolves to `/var/opt`; blindly using
`restorecon` gives the image `var_t`, which QEMU cannot read. The installer uses
the original image's `usr_t` context for this file alone. SELinux stays Enforcing.
A full filesystem relabel would require reapplying this diagnostic label;
production packaging needs an appropriate persistent file-context mapping.

CRI-O 1.35.8-11.rhaos4.22.giteaf45bc.el9 exhibited
[upstream issue #10202](https://github.com/cri-o/cri-o/issues/10202): reload showed
the handler in configuration but sandbox creation still used the old runtime map.
The user separately approved **one restart**. `restart-host.sh` checks the pinned
files and restarts once, without rebooting or deleting pods. Never infer restart
approval from reload approval. The actual restart changed PID 11809 to 694089;
production pod identities/readiness/restart counts, resource specifications and
GitOps sources matched before and after. No second restart was needed.

The original installed directory is
`/opt/kata-storage-test/private-aa-probe-94354456b0c3/`; its current test config
references `initrd-v4`. Clean installations with the current installer use
`private-aa-probe-d949fefa0716/initrd`. Both use the exact current `build.json`
image. The existing live handler is already installed: do not rerun the clean
installer over it. Updates to its test configuration are separate from production.

## InitData and application

The existing webhook did **not** inject InitData for this custom RuntimeClass.
The chart therefore requires explicit reviewed `io.katacontainers.config.hypervisor.cc_init_data`.
Missing InitData must be treated as failure, not a reason to loosen appraisal.

```sh
oc get cm storage-audit-no-secrets -n confidential-storage-test \
  -o jsonpath='{.data.INITDATA}' > /scratch/diagnostic-initdata.value
helm lint tests/confidential-storage/private-aa-probe/chart \
  --namespace confidential-storage-test \
  --set-file initdata=/scratch/diagnostic-initdata.value
helm template storage-private-aa tests/confidential-storage/private-aa-probe/chart \
  --namespace confidential-storage-test \
  --set-file initdata=/scratch/diagnostic-initdata.value > /scratch/probe.yaml
oc apply -n confidential-storage-test -f /scratch/probe.yaml
```

Admission can briefly report a newly created RuntimeClass missing; after cache
propagation, reapply the same manifest. That differs from CRI-O's handler-map bug.
The application has no PVC, GPU, service-account token or elevated capability.
The current chart makes all isolation checks fail the container on error.

## Console and verifier

Kata's console watcher emits at Debug while this CRI-O starts the shim at Info.
The diagnostic config disables only the hypervisor console watcher; the probe
writes to its virtio console for direct, bounded read-only capture.
`capture-host.sh` takes the exact QEMU-resolved test image path, waits for the
corresponding QEMU process, and reads its console without sending input. Start
capture before creating the pod. The bounded `socat` timeout can return nonzero
after successful capture: require both evidence markers and parse the JSON
between them. Never put the raw console or quote in Git.

Build the isolated AS with the pinned command from the authority guide. The
rebuilt binary SHA-256 is
`f60eaaed611d5a4bcc907ccbc1f18d6b734f2fb16b35529b940ed782470e9865`.
Its embedded build timestamp changes the output hash between builds.

Construct an AS request with `tee: snp`, base64url JSON evidence, the structured
`runtime.json`, `runtime_data_hash_algorithm: sha256`, the exact InitData TOML,
and **`policy_ids: ["default"]`**. AS appends `_cpu` itself. Configure a separate
P-256 signer with issuer `storage-probe-as`, 1-minute tokens and loopback port 8089.

Repeated online KDS fetches failed intermittently. With explicit user approval,
the AMD VCEK was downloaded once into private test scratch and the AS configured
with `verifier_config.snp_verifier.vcek_sources: [{type: OfflineStore, path: ...}]`.
Use the upstream `kds-store/vcek/<hwid>/vcek.der` layout. This changes certificate
lookup only: AMD chain, report signature, chip/TCB association and bindings remain
verified. The download sends chip ID and TCB versions to AMD; do not conceal that.

```sh
python3 tests/confidential-storage/private-aa-probe/verify-hardware.py \
  --request /private/diagnostic-request.json \
  --signer /private/as-signer.pub.pem \
  --output /private/hardware-validation.json
```

This test distinguishes cryptographic verification from an affirmative appraisal.
Next work is independently approved launch/TCB references, measured private guest
configuration and key/lifecycle integration, followed by both actual storage E2Es.
CPU-only evidence does not establish composite CPU/GPU attestation.
