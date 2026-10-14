# Native encrypted RW PVC: live first use and fresh-VM reopen

On **14 October 2026**, the isolated SNP guest initialized a fresh block PVC using
a key delivered by Trustee, exposed an ext4 `/data` mount to an unprivileged
application, and reopened the persisted data from a second confidential VM.
A third VM was denied the key by Trustee and never started its application.

Together with [encrypted Qwen inference from the OCI-populated RO PVC](native-model-serving.md),
this completes the two requested live examples. Both use CPU SNP guests. The
production KServe/vLLM/GPU workloads and their policies were unchanged.

## Hardware, storage and workload

| Item | Actual tested value |
| --- | --- |
| Cluster / node | OpenShift 4.22.15 / `9c-63-c0-dd-06-52` |
| Namespace / runtime | `confidential-storage-test` / `kata-snp-storage-probe` |
| Guest | AMD SEV-SNP, one EPYC-v4 vCPU, 4096 MiB boot memory, no GPU |
| PVC | `storage-audit-data`, 1 GiB raw Block, RWOP, `lvms-vg1` / TopoLVM |
| PVC UID | `fb6965a0-31f6-4bb3-b330-b91505044220` |
| Logical volume / key URI | `storage-rw-native-v1` / `kbs:///storage-test/volume-keys/storage-rw-native-v1` |
| Application | Pinned UBI9 minimal, UID/GID 1000, all capabilities dropped, NoNewPrivs, read-only rootfs |
| Data | Public persistence canary and a reopen counter; no application secrets in evidence |

The key was generated privately in the isolated KBS repository. It was not in the
PVC, application Secret, policy, image or Git. The guest service used fresh SNP
evidence and pinned TLS trust to retrieve it. The exact launch measurement,
InitData, TCB and resource path were enforced by the isolated policies.

## Observed sequence

The first VM verified the administrator-approved plan and blank device, acquired
initialization ownership, consumed a fresh signed permit for each destructive
step, and persisted each completion in the authority's journal:

```text
STORAGE_SERVICE initialization_step_complete LuksFormat
STORAGE_SERVICE initialization_step_complete IntegrityInitialize
STORAGE_SERVICE initialization_step_complete FilesystemCreate
STORAGE_SERVICE initialization_step_complete DurabilityBarrier
STORAGE_SERVICE initialized_ready
STORAGE_SERVICE mounted_rw
```

The formatter created AES-XTS-512 + HMAC-SHA256 LUKS2 with 4096-byte sectors, a
16 MiB header/data offset and Argon2id. It generated and validated metadata in
private guest memory, used a sealed header for activation, verified the actual
kernel mapping dependencies and read the entire HMAC-protected payload before
creating ext4. The 1 GiB device uses a 16,368-sector integrity journal. Ext4 root
ownership is explicitly 1000:1000. Initialization mappings were closed and the
header checked again before Ready; a separate ordinary reopen grant admitted
the application mount.

The first application emitted:

```text
uid=1000 gid=1000 groups=1000
CapEff: 0000000000000000
NoNewPrivs: 1
STORAGE_RW_INITIAL_WRITE_PASS
sentinel: OK
STORAGE_RW_UNPRIVILEGED_PASS
```

The script also asserted that the raw block device, `/dev/mapper/control` and
CDH socket were absent. It wrote the canary, synced both files and the filesystem,
and was then deleted normally. Guest cleanup emitted `STORAGE_SERVICE closed`;
the authority record was Ready with no owner.

A **different pod and VM** consumed the same PVC. Its guest log contained
`mounted_rw` but no initialization steps. The application verified the original
canary SHA-256 and updated the persisted counter:

```text
STORAGE_RW_REOPEN_PASS
sentinel: OK
STORAGE_RW_UNPRIVILEGED_PASS
```

Both successful runs were closed before further testing. The final authority
record still has exactly four Complete initialization steps and no owner.
See [application and guest logs, distinct pod UIDs, policies and boot inputs](../../tests/confidential-storage/evidence/2026-10-14/native-rw/).

## Negative tests and checks

- Administrator `oc exec ... /usr/bin/id` returned `ExecProcessRequest is blocked
  by policy`. This was guest policy enforcement, not a Kubernetes RBAC denial.
- After the Ready volume closed, effective isolated KBS deny-all produced a real
  attestation HTTP 200 followed by resource HTTP 403 / `PolicyDeny`. The guest
  failed at `kbs-retrieve`, did not mount or start the application, and closed
  its authority ownership. No record or disk was reset.
- The guest-RAM formatter probe rejected repeated formatting and a nonblank disk
  after successful format/integrity/ext4/barrier checks.
- 52 guest-library tests passed; three separately gated tests remained ignored.
  Nine native-agent tests, three service protocol tests and 19 Rust/Python signed
  transport tests passed. The latter include wrong signer/plan and lost-response
  replay cases; these synthetic protocol tests are distinct from live attestation.
- The actual Kata policy engine passed the exact workload plus 16 negative cases.
  The actual Trustee policy engine passed the exact resource plus 15 negatives
  and deny-all. Clean native source staging and public guest packaging were checked.
- The guest image and workload InitData reproduced byte-for-byte, and Helm lint
  passed. Six native packaging/administrator tests passed, including refusal of
  active-owner changes, duplicate registration and unsupported geometry.
- [Final production audit](../../tests/confidential-storage/evidence/2026-10-14/native-rw/production-audit.json)
  reports unchanged production workload identities, readiness/restarts, resource
  specifications, runtime classes and GitOps sources. CRI-O remained PID 694089;
  there was no additional reload, restart or node reboot.

The repository-wide checks still have existing failures: `preview-all` is absent
from the Makefile, and `validate-schema` encounters an undefined
`global.hardware.profile` in bare-metal values. These were not changed by the
isolated test; the targeted checks above passed.

The first two PVC attempts stopped before Begin because the freshly booted guest
clock trailed the receipt issue time. The final client verifies the signature,
waits at most two seconds for a small future issue time, re-reads its clock, and
applies the original strict validity checks. It does not extend receipt expiry
or accept a future-dated receipt. All 19 transport tests passed after this change.

## Reproduce and retain state

Follow the [RW test guide](../../tests/confidential-storage/native-rw/README.md)
for the administrator, policy, guest build, key provisioning, Helm and two-VM
sequence. It builds on the isolated AS/KBS/private-AA setup in
[native RO integration](native-ro-pvc.md). Use your own keys and trust roots;
calculate and approve your resulting launch measurement, rather than copying
the reference from this run.

Current tested artifacts:

- Initrd `initrd-native-rw-v3`, SHA-256
  `f102bbdf287027f7bc54d9e209e39a58c8350024a4b0f1aa1e35b94f1f62ad77`.
- Agent SHA-256 `bfc6e9b59783fcae2954f05f6dc8d0589b8e11ab3e2901729e4445fdabe68df3`.
- Service SHA-256 `5211fd98b72ee16bd3f9f4465e1f3aaf51c55665ef691310c73a195b36cd69f5`.
- [Independent SNP measurement](../../tests/confidential-storage/evidence/2026-10-14/native-rw/measurement.json):
  `a248eb38d39e9027b866cb6b6ef86e48fb13eabace3d69d56d71e50872bc769f46df57a95ce04635bc37c9716f12d49e`.

All test consumers were removed after evidence capture. The PVCs remain. Isolated
authority/KBS services were stopped after a consistent private backup with both
owners closed; KBS was left deny-all. Preserve that latest database and both
volume keys. Never restore an older pre-Ready database, generate a replacement
RW key, or reapprove formatting to recover access. Restarting temporary services
on a new IP or with new certificates changes measured guest configuration and
requires rebuilding and independently approving the new image.

This remains an isolated development integration, not a production CSI/operator
release. Writable storage is exclusive and the demonstrated geometry is 1 GiB.
HMAC detects tampering on reads; it does not prevent replay of an entire older RW
disk snapshot. Interrupted initialization retains uncertain ownership and needs
deliberate recovery. Neither application privileges nor attestation checks were
relaxed to complete these tests.
