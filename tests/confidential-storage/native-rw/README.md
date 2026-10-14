# Native confidential RW storage test

This is the writable companion to the [encrypted model serving test](../../../docs/confidential-storage/native-model-serving.md).
It uses one **new, disposable 1 GiB raw-block PVC**, a measured guest service,
real SNP attestation, an isolated Trustee KBS, and a signed first-use journal.
The application is UID/GID 1000, has no capabilities, cannot escalate privileges,
and receives only the mounted `/data` directory. It never receives the block
device, key, mapper control device, AA socket, or a formatting command.

The native formatter passed the disposable guest-RAM kernel probe, followed by
[real-PVC first use, fresh-VM reopen and Trustee denial](../../../docs/confidential-storage/native-rw-storage.md).
Both application PASS markers and closed authority state are recorded.

## Components and responsibilities

| Component | Responsibility |
| --- | --- |
| Administrator | Approves the exact new PVC identity, size, LUKS UUID, workload binding and profile; provisions a random 32-byte key privately in the isolated KBS. |
| Authority | Authenticates fresh SNP-backed requests; durably journals one-shot Plan/Begin/Step/Complete/Ready transitions and exclusive ownership. |
| Measured guest service | Scans the complete blank device, consumes signed step permits, runs cryptsetup/mkfs, verifies the resulting kernel stack, closes initialization mappings and separately acquires a reopen grant. |
| Kata agent | Admits the measured application, retains the attached device inside the trusted guest, replaces the raw attachment with a private mount and closes it after destroying the consumer. |
| Application | Writes a public canary, syncs it, and verifies the same bytes in a subsequent VM; never formats or retrieves a key. |

`probe.sh` and `initializer-lab.rs` exercise the same Rust formatter on a fresh
1 GiB file in guest-private tmpfs. They do **not** use a PVC, KBS, or attest a key
request. The probe verifies complete HMAC reads, ext4 creation, durability/close,
refusal of repeated formatting, rejection of a nonblank disk, and cleanup.

## First-use sequence

1. Generate strict workload InitData with `prepare-policy.py --output /scratch/rw-policy`.
   Test it with the native Kata policy fixture and the required `init-data,agent-policy`
   build features. The exact image, command, environment and application identity
   must pass; exec, policy replacement, elevated privileges and altered commands
   must fail.
2. Create a new 1 GiB `volumeMode: Block` PVC with the test storage class and
   exclusive access. Record its actual Kubernetes UID. Do not reuse a disk with
   unknown contents or reset an old initialization record.
3. Stop the isolated authority and put the isolated KBS into effective deny-all.
   Run `prepare-authority.py` with its existing private state, previous public
   guest configuration, generated policy directory, a new logical volume ID,
   the PVC UID as `--provisioning-id`, and `--device-bytes 1073741824`.
   This preserves previous records, keys and signing identity. It registers the
   new volume and immutable first-use plan; it does not touch storage or create
   a key. Keep its database, signer and backups outside Git.
4. Provision a separate random 32-byte key in the isolated KBS repository at
   `kbs:///storage-test/volume-keys/<volume-id>`. Never put it in a Kubernetes
   application Secret, Helm values, InitData, command argument or image layer.
5. Build the native agent and storage service from the locked source. Package
   `guest-rw-public.json` with `native-guest/build.py`; independently calculate
   the SNP launch measurement using `native-guest/measure.py`. Approve that
   measurement and exact InitData/TCB in the isolated AS and KBS CPU policies.
   Generate the resource policy with `prepare-release-policy.py --resource
   storage-test/volume-keys/<volume-id>` and run `kbs-policy-tests.rs` with that
   same resource argument before enabling release. Production policy is separate.
6. Install the image only in the isolated test handler while it has no VM.
   The small UBI application uses 4096 MiB boot memory and one vCPU here.
   Preserve the measured kernel, firmware, command line and strict InitData.
   Capture the console before creating the pod; follow sandbox retries.
7. Render `chart/` using the generated `initdata.value` and `workload.json` as
   `initdata` and `workload` values. Set `claim` to the new PVC. Review with
   `helm template` and server-side dry run, then create the test pod.
8. Require all four `STORAGE_SERVICE initialization_step_complete` markers,
   `initialized_ready`, `mounted_rw`, and application
   `STORAGE_RW_INITIAL_WRITE_PASS` / `STORAGE_RW_UNPRIVILEGED_PASS`.
   Kubernetes readiness alone does not establish these properties.
9. Delete the pod normally after collecting logs. Require guest `closed`, no
   surviving test VM, and no authority owner. Preserve the Ready record and key.
10. Create a fresh pod/VM with the same PVC, policy, logical volume and key.
    The signed Ready manifest must select ordinary reopen, never formatting.
    Require `STORAGE_RW_REOPEN_PASS`, matching canary hash and no initialization
    steps in this second VM. Check administrator exec is denied, then close it.

## Command outline

Run from the repository root after creating the isolated namespace and trust
services described in the RO guide. Use a fresh release name on a clean cluster;
`probe.enabled=false` creates only the blank PVC, not a diagnostic container.

```sh
helm template storage-audit charts/coco-supported/confidential-storage-test \
  --namespace confidential-storage-test --set storageClassName=lvms-vg1 \
  --set probe.enabled=false > /scratch/blank-pvc.yaml
oc create -n confidential-storage-test -f /scratch/blank-pvc.yaml
python3 tests/confidential-storage/native-rw/prepare-policy.py \
  --output /scratch/rw-policy
```

With the isolated authority stopped and KBS deny-all, register the administrator
intent. `/secure/authority` and `/secure/guest-public.json` come from the private
trust-service setup; they are not application configuration or repository files.

```sh
python3 tests/confidential-storage/native-rw/prepare-authority.py \
  --state /secure/authority --previous-guest /secure/guest-public.json \
  --policy /scratch/rw-policy --volume-id rw-example-v1 \
  --provisioning-id "$(oc get pvc storage-audit-data -n confidential-storage-test \
    -o jsonpath='{.metadata.uid}')" --device-bytes 1073741824
```

Provision the volume key through your KBS administrator backend, then package
`/secure/authority/guest-rw-public.json`, measure and approve the image as above.
Do not use `oc exec` to inject the key into the application. Generate the Helm
values from the exact admitted workload:

```sh
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/scratch/rw-policy')
values = {
    'initdata': (p / 'initdata.value').read_text(),
    'workload': json.loads((p / 'workload.json').read_text()),
    'claim': 'storage-audit-data',
}
Path('/scratch/rw-values.json').write_text(json.dumps(values))
PY
helm template storage-rw tests/confidential-storage/native-rw/chart \
  --namespace confidential-storage-test -f /scratch/rw-values.json \
  > /scratch/rw.yaml
oc create --dry-run=server -f /scratch/rw.yaml
# Only after the matching guest image, authority and KBS policy are installed:
oc create -f /scratch/rw.yaml
oc logs -n confidential-storage-test storage-rw
```

Wait for `STORAGE_RW_INITIAL_WRITE_PASS`, save logs and the pod UID, then delete
the pod normally. Confirm guest/authority close before creating the same manifest
again. Require a different pod UID and `STORAGE_RW_REOPEN_PASS`. Preserve the PVC,
key, Ready journal and manifest between runs. The default 600-second deadline
is a test guard; it is not a durable service deployment.

## Format and failure semantics

The trusted formatter uses a private anonymous header file, fixed AES-XTS-512,
HMAC-SHA256, 4096-byte sectors, Argon2id, a 16 MiB header/data offset, and ext4.
It copies the new header to disk only after validating it, seals an authenticated
copy for activation, and verifies actual kernel dependencies and cryptographic
parameters. Integrity is journaled; discards, recovery modes and unkeyed checksums
are rejected. The 1 GiB geometry has a 16,368-sector integrity journal; the earlier
128 MiB fixture has 1,848. Other geometries have not been validated.

Every destructive step consumes a fresh signed permit first. A failure after
Begin keeps the record uncertain/Initializing; neither retry nor pod recreation
authorizes formatting again. Do not delete the database, reset ownership, change
the plan, disable attestation, or recreate a key to make a failing test pass.
The key belongs to the volume: generating a different key later loses access.

HMAC detects unauthorized modifications when affected sectors are read. This
does not promise protection against replay of an entire older RW disk snapshot;
that requires an external freshness design. Likewise a malicious storage host can
deny service. Storage hardware is treated as untrusted, not separately attested.

Run negative tests against Ready/reopen or dedicated disposable negative volumes.
Denying KBS after Begin on the only fresh volume deliberately leaves uncertain
initialization and must not be followed by an automatic reset. Keep actual
key-release negatives distinct from synthetic policy/transport tests.
