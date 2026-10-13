# Confidential storage compatibility report

Observed on 13 October 2026. Implementation branch: `dev/confidential-storage-integration`.
This is the first implementation checkpoint. Encrypted persistent `/data`, model
population and KServe storage integration are **not deployed**.

## Live platform

| Component | Observed value | Consequence |
| --- | --- | --- |
| OpenShift / Kubernetes | 4.22.15 / v1.35.6 | Existing cluster reached with verified TLS |
| Node | `9c-63-c0-dd-06-52`, single node, 256 CPUs, approximately 752 GiB memory | CPU-only diagnostics are feasible |
| OSC / Trustee | 1.13.0 / 1.2.0 | Existing operators retained |
| Kata shim | Go runtime 3.31.0, commit `84780cbb6f1ac9be55bae9eac132f6e2b88c690f` | Rust runtime prototype is not a drop-in patch |
| CPU confidential handler | `kata-cc` → `kata-snp` | Used for isolated diagnostic |
| GPU confidential handler | `kata-cc-nvidia-gpu` → `kata-snp-nvidia-gpu` | Unchanged |
| Guest kernel | `6.12.0-211.16.1.el10_2.x86_64` | Verified from diagnostic container |
| CSI | `topolvm.io`, class `lvms-vg1`, Immediate binding | Raw block + RWOP PVC successfully provisioned |
| GPUs | Two `nvidia.com/pgpu`; both occupied | Qwen and encrypted-file demo remain running |
| KServe | Argo chart v0.20.0, `encrypted-qwen` Ready | No inference manifests changed |
| Live predictor | `encrypted-qwen-predictor-c68947bb9-gw9ml`, 2/2, zero restarts at audit | Original vLLM digest preserved |
| GitOps | Pattern applications watch `codex/edge-rtx6k` | Development branch is not watched |

CPU guest artifact SHA-256 values:

```text
kata-cc.initrd  1f9b3a90c60ec7a94985ae966fc66ecb4d04055ca3471b9f0074068f4dac8b62
kata-cc.kernel 0317da8a23853124ed4e4da6c56254f40a741ff4507e1afbbcb7f32e5c097206
```

Both installed confidential handler configurations contain
`disable_block_device_use = true` and `block_device_driver = "virtio-scsi"`.
Despite that setting, the raw `volumeDevices` diagnostic received a block device:
do not infer that the setting forbids every block-device delivery path.
The CPU initrd listing contains dm-crypt and dm-verity modules, but no matching
dm-integrity module or cryptsetup/veritysetup/integritysetup executable. This is
an artifact-content observation, not proof that no kernel facility is built in.
Exact CDH/AA build revisions and an effective authenticated-integrity profile
remain to be established before a guest build is approved.

## Hardware diagnostic

The Helm chart provisions a new 1 GiB raw-block RWOP claim, runs a CPU-only pod,
and only attempts a 4 KiB read into `/dev/null`. It never formats, mounts, writes
data or retrieves an unlock key. The final test used a digest-pinned public UBI
minimal image and dedicated InitData with no production Trustee address or
registry-credential reference. Exec and policy replacement are denied; diagnostic
stdout is allowed. This policy is not suitable for confidential application data.

Actual guest output:

```text
encrypted_storage_proven=false
6.12.0-211.16.1.el10_2.x86_64
uid=1001100000(1001100000) gid=0(root) groups=0(root),1001100000
CapEff: 0000000000000000
NoNewPrivs: 1
Seccomp: 0
module_dm_crypt=visible
module_dm_integrity=not-visible
module_dm_verity=visible
device_type=block special file mode=0 uid=0 gid=6
dd: failed to open '/dev/storage-probe': Permission denied
```

The attachment reached the guest; application raw-device access failed. This is
not a passing encrypted-volume test. Do not fix it by granting the application
root, `CAP_SYS_ADMIN`, a privileged sidecar or raw-device permissions. The target
interface remains a filesystem mounted by privileged guest infrastructure before
application startup. The observed `Seccomp: 0` also means the requested
`RuntimeDefault` profile was not demonstrated inside this container; do not claim
seccomp enforcement from the pod YAML alone.

Earlier attempts recorded image unpack errors with the larger Python image and
a resource-provider error after changing the copied InitData. Neither error is
evidence of disk encryption or a successful attestation-denial test. The final
reproducer removes the production KBS dependency entirely.

Evidence: [probe log](../../tests/confidential-storage/evidence/2026-10-13/raw-block-probe.log),
[structured result](../../tests/confidential-storage/evidence/2026-10-13/raw-block-result.json),
and sanitized baseline/after inventories in the same directory. Baseline collection
occurred after initial diagnostic attempts; the automated comparison covers the
remaining work and cleanup interval, not the complete earlier session.

## Upstream reuse checked against source

| Source | Exact revision / status | Remaining work |
| --- | --- | --- |
| [CDH persistent LUKS #1648](https://github.com/confidential-containers/guest-components/pull/1648) | `0aa86474c6d3d49c23f6033b49d2591d831fa79d`, open/unmerged | Initialization and header recovery primitive; does not implement independent authority or ciphertext integrity |
| [CDH integrity #1663](https://github.com/confidential-containers/guest-components/pull/1663) | `90c307995fa94a5e468ff817893c73e031e2c117`, closed/unmerged | Review journaled integrity prototype; no assumption of released support |
| [Manuel's CDH RW prototype](https://github.com/manuelh-dev/guest-components/tree/0ab1fb1be6e46293f29600085f929ae035ca3739) | `0ab1fb1be6e46293f29600085f929ae035ca3739` | Reopens using an authenticated header copy; lacks our authority initialization protocol and complete profile enforcement |
| [Manuel's Kata prototype](https://github.com/manuelh-dev/kata-containers/tree/e53adb460e6040e6aa9719a735e17301462c4fa9) | `e53adb460e6040e6aa9719a735e17301462c4fa9` | Runtime-rs constructs secure-volume metadata; agent returns a decrypted block device rather than the required application filesystem mount |

Do not deploy the prototype simply because its manifest names a profile
`luks2-integrity-rw`. The inspected activation path authenticates the header and
checks UUID/access; it still needs explicit enforcement of the approved cipher,
integrity and journal configuration and the full lifecycle/authority integration.
No upstream source was modified or represented as a finished implementation.

## Implemented and remaining

| Milestone | State | Evidence / next gate |
| --- | --- | --- |
| Isolated CSI/device diagnostic | Implemented and exercised; read fails as recorded | Block RWOP provisioning and device delivery observed; no application privileges added |
| Transactional authority core | Implemented; unit tested | Durable initialization intent, one owner, immutable Ready/header, no reset or automatic takeover |
| Authenticated authority service | Not implemented | Verifier, channel-bound protocol, receipts and independent recovery/epoch protection required |
| Guest mount integration | Not implemented | Go runtime metadata, guest agent mount placement, CDH activation/close and approved build required |
| RW storage E2E | Blocked on guest integration | No LUKS volume initialized; RW01–RW09 not passed |
| RO publisher/populator/model attestation | Not started | Follow successful RW guest integration |
| Isolated Qwen storage integration | Not started | Requires guest gates and available GPU; live GPUs retained |

New handlers need distinct versioned shim/config/initrd paths and fresh test
measurements. CRI-O 1.35 documents runtime-table reload support, so a restart or
node reboot is not assumed necessary; safety of adding the new handler still needs
validation on the actual packaged runtime. Do not replace an existing handler or
apply a node-wide MachineConfig as a shortcut. See the
[CRI-O configuration reference](https://github.com/cri-o/cri-o/blob/release-1.35/docs/crio.conf.5.md).
