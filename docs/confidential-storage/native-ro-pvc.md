# Live native-agent encrypted model PVC checkpoint

On 14 October 2026, the isolated SNP guest retrieved the test key from Trustee,
opened the OCI-populated Qwen PVC through dm-verity and read-only LUKS, and exposed
only `/models` to an unprivileged container. All nine publisher file hashes passed.
This verifies **confidential RO PVC access**, not model inference or RW storage.
Those two final examples are still incomplete.

## Actual workload and evidence

- Cluster: OpenShift 4.22.15, node `9c-63-c0-dd-06-52`.
- Namespace/runtime: `confidential-storage-test` / `kata-snp-storage-probe`.
- Successful pod UID: `20c87860-02a0-46e8-85f7-07b3cd3ef118`.
- Claim: `qwen-luks-model-v1`, 2 GiB, raw Block, RWOP, `lvms-vg1`.
- Model: Qwen3-0.6B, revision `c1899de289a04d12100db370d81485cdf75e47ca`.
- Internal OCI artifact manifest:
  `sha256:49d4cc9827fc214fa339206c660656d8523dc8c2de1e95a9c1d9f7992934cd25`.
- The existing custom populator had already populated the encrypted disk; this
  probe consumed that same PVC. It did not import plaintext or reformat it.
- Guest: one EPYC-v4 vCPU, SNP, no GPU assignment. Both production GPUs stayed
  occupied by their original workloads. This does not test CPU+GPU composite
  attestation or alter production GPU key policy.

The private guest service emitted:

```text
STORAGE_SERVICE ready
STORAGE_SERVICE mounted_ro
STORAGE_SERVICE closed
```

The application emitted:

```text
uid=1000 gid=1000 groups=1000
CapEff: 0000000000000000
NoNewPrivs: 1
LICENSE: OK
config.json: OK
generation_config.json: OK
merges.txt: OK
model.safetensors: OK
publisher-source.json: OK
tokenizer.json: OK
tokenizer_config.json: OK
vocab.json: OK
STORAGE_MODEL_RO_HASHES_PASS
```

The exact [application log](../../tests/confidential-storage/evidence/2026-10-14/native-integration/model.log)
is authoritative. The fixed command also checks that the raw block device,
device-mapper control and CDH socket are absent, and that a write attempt fails.
An administrator's `oc exec ... /usr/bin/id` was rejected by the measured agent
policy: `ExecProcessRequest is blocked by policy`.

The authority journal returned to `Ready` with no owner after both the denied
activation and the successful mount were closed. Production pod UIDs, readiness,
restart counts, resource specifications, runtime classes and GitOps sources
matched the baseline. CRI-O remained PID 694089; this checkpoint required no
additional reload or restart.

Public evidence, exact agent/KBS policies, image hashes and independently
calculated launch measurement are in
[`native-integration/`](../../tests/confidential-storage/evidence/2026-10-14/native-integration/).
Build metadata records an initially unapproved candidate; `checks.json` records
subsequent live authorization and validation. No storage key or raw attestation
token is committed.

## What releases the key

The measured native agent boots root-private AA/CDH and the storage service.
There is no REST attestation/key helper. The agent policy pins the exact UBI image
digest, verification command, namespace, UID/GID, environment and OCI namespace
shape. It admits one measured pause and one consumer. The private service verifies
fresh SNP evidence through the authority, a request-bound signed ownership grant,
and the copied model header/manifest before requesting the key from KBS.

The isolated KBS verifies a fresh attestation and encrypts its response to the
requester's TEE key. Its resource policy permits only
`kbs:///storage-test/model-keys/qwen-luks-v1`, with the exact v9 SNP measurement,
InitData SHA-256, pinned TCB/policy fields and an affirming CPU appraisal. Its
admin API remains `DenyAll`. The existing 32-byte test model key was provisioned
privately only after the no-key attestation and deny-all tests passed.

The service creates the dm-verity → read-only LUKS → filesystem stack. The agent
removes the raw block device and host file bind mounts before creating the app,
then exposes only the verified read-only mount. The app cannot call the private
key service. Cleanup destroys the consumer, unmounts and closes mappings, then
closes authority ownership. A definitive activation denial is cleaned immediately
before returning to the host; uncertain/busy failures keep ownership.

CDH logs that its optional image-security filter and runtime measurement extension
are not configured. Image admission here is enforced by the **measured agent's
exact image digest and command policy**. Do not describe this checkpoint as
signature-based CDH image admission or an image event-log attestation test.

The authority/KBS run in the isolated compiler pod for this integration test.
A production deployment needs custody outside the workload administrator's trust
and rollback domain. This colocated test is not proof of that operational separation.

## Reproduction order

Use the pinned source/build instructions in the
[native agent](../../components/confidential-storage-kata/native-agent/README.md),
[private service](../../components/confidential-storage-service/README.md) and
[guest packaging](../../tests/confidential-storage/native-guest/README.md) guides.
Keep production runtime classes and manifests untouched.

1. Prepare the encrypted OCI artifact and populator claim using the existing
   publisher/populator example. Retain the publisher's test key outside Git.
2. Prepare the isolated KBS with `prepare-kbs.py`. Its effective storage key is
   **`storage/kbs/resource-policy.rego`**, including the suffix. Verify deny-all
   at that exact path before any resource exists.
3. Package the authority Python modules **and both public profile files** at
   sibling path `confidential-storage-guest/profiles/`. The server now refuses
   startup if a profile is missing or invalid. Preserve private directory mode
   0700 and private file mode 0600 when copying generated state; an archive that
   omits its parent directory can otherwise recreate that directory as 0755.
4. Generate strict InitData with `prepare-policy.py`, then a fresh authority
   record/public guest configuration with `prepare-authority.py`. Do not use
   fresh state as recovery for an initialized volume or a released key.
5. Build the agent/service and guest overlay, then calculate the SNP measurement
   independently with `measure.py`. Check actual QEMU command line, CPU count,
   CPU model, firmware/kernel/initrd hashes, SNP policy and host-data against it.
6. Start the isolated authority and KBS; verify they are listening successfully.
   Pin the measured CPU appraisal. Run the Helm verification pod with no key and
   effective deny-all. Require real attestation, KBS `PolicyDeny`, service
   `closed`, and authority `Ready`/no owner before enabling release.
7. Generate the exact release policy with `prepare-release-policy.py`. Run
   `kbs-policy-tests.rs` as a `policy-engine` example in the pinned Trustee source.
   The current suite has one positive, 15 negative variants and deny-all coverage.
   Install the generated policy at the effective `.rego` path, then privately
   provision only the retained test-model key. Do not put it in the pod, chart,
   command line, environment, repository or logs.
8. Render/run `native-guest/chart` as described in its README. Check every file
   hash, application privilege assertions, denied admin exec and clean teardown.
   Save sanitized evidence and repeat the production nondisruption audit.

KBS's pinned LocalFs backend stores `/` in resource names as literal `\x2F`;
its resource namespace is `repository`. This is a version-specific test backend
implementation detail, not a portable provisioning API. Administration stays
closed; trusted test setup writes the private backend directly. Production
provisioning must use its configured custodian/backend workflow.

Use bounded artifact transfers and compare full SHA-256 values before installing.
Two large direct `oc exec ... cat` downloads were truncated despite exit status
zero; neither mismatched artifact was installed.

## Failures found and corrected

The live iterations found a missing standalone `id` in the minimal guest, CRI-O
annotation differences, rejected host-file copies, a private AA subdirectory
mode mismatch, missing authority profile files and a failed-activation cleanup
ordering problem. The bootstrap now uses the bundled BusyBox; policy/native name
checks use Kubernetes labels; discarded file copies perform no filesystem writes;
AA directories are 0700; profile packaging fails early; definitive denials close
before network teardown.

The original KBS setup wrote deny-all without `.rego`, so KBS loaded its bundled
TEE-accepting default. **The earlier deny-all claim was incorrect.** No storage
key existed during those runs. This was corrected and a real `PolicyDeny` was
observed before key provisioning. The packaging regression test checks the
upstream storage-key contract.

The first failed no-key authority retained an owner after network teardown.
Its full private state was archived and retired after verifying no test VM and no
KBS key existed; its ownership record was not silently cleared. The next test
used a new authority signing identity and newly measured guest. This exception
for an unprovisioned test does not establish a recovery procedure for real volumes.

## Remaining work

The strict current application only verifies file hashes. It does not serve an
inference endpoint. Next connect a model server to this RO mount and test an
actual request/response. Then wire the signed first-use journal into the native
service for the separate blank RW PVC, validate formatting/persistence/reopen,
and finish the two reproducible examples with tamper/replay and recovery cases.
