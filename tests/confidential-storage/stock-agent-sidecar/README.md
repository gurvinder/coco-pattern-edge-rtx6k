# Stock Kata agent and a privileged storage sidecar

This isolated feasibility probe runs two containers inside a stock `kata-cc`
SEV-SNP VM in **`confidential-sidecar-test`**. Both use the same unchanged,
digest-pinned NVIDIA vLLM image as Qwen. The application runs a diagnostic Python
program instead of starting inference. No GPU, real model, production storage credential,
existing PVC or Trustee policy is used or changed.

The question tested is whether a trusted sidecar can provide read-only and
read-write mounts to an unprivileged application without modifying Kata agent.
The first probe uses private guest tmpfs filesystems to isolate that question from
key provisioning, filesystem tooling and persistent storage.

## Mechanism

1. `shareProcessNamespace: true` provides a shared **pod/guest** PID namespace.
   Host PID/network namespaces and hostPath volumes are not enabled.
2. The application starts as UID/GID 1001, with all capabilities dropped,
   `allowPrivilegeEscalation: false`, a read-only root filesystem and no API token.
   It registers its PID and waits; it does not start the model before mounts exist.
3. The privileged sidecar creates private tmpfs filesystems. It validates the
   diagnostic consumer PID/UID, forks a helper and opens the consumer mount
   namespace and root descriptors.
4. Before entering the consumer namespace, the helper clones the sidecar mounts
   into detached mount descriptors using `open_tree(OPEN_TREE_CLONE)`. After
   entering it with `setns()` and switching root with `chroot()`, `move_mount()`
   attaches them at the pre-created application
   destinations `/models/qwen` and `/data`. It applies RO/RW plus
   `nodev,nosuid,noexec`. This uses guest process/mount namespaces, not host mount
   propagation. The application itself never calls mount or receives SYS_ADMIN.
5. The application checks RO contents and EROFS on writes, writes/fsyncs/reads the
   RW canary, and checks that mapper/raw-device/private-sidecar paths are not
   accessible. The sidecar independently sees the written canary.
6. While the application remains alive, the sidecar removes its injected mounts,
   unmounts its private filesystems, and reports cleanup. The application verifies
   the files are no longer visible through its mount destinations, then exits.

The main storage process remains alive for the entire test; this is a regular
sidecar container, not a one-shot init container. The diagnostic application is
started before the mounts but waits for explicit completion. A real vLLM startup
would need an equivalent trusted startup gate, which could be supplied through
its command without rebuilding the image.

## Reproduce the no-key probe

Review the chart/scripts before applying. This uses a dedicated SCC permitting a
privileged helper, bound to this namespace's test service account. The Pod pins
that SCC using `openshift.io/required-scc`.

```sh
python3 scripts/confidential-storage/probe-initdata.py \
  --namespace confidential-sidecar-test > /tmp/sidecar-initdata.json
python3 - <<'PY'
import json
config = json.load(open('/tmp/sidecar-initdata.json'))
json.dump({'initdata': config['data']['INITDATA']}, open('/tmp/sidecar-values.json', 'w'))
PY
helm template stock-sidecar tests/confidential-storage/stock-agent-sidecar/chart \
  -n confidential-sidecar-test --set workload.enabled=false > /tmp/sidecar-bootstrap.yaml
oc apply -n confidential-sidecar-test -f /tmp/sidecar-bootstrap.yaml
helm template stock-sidecar tests/confidential-storage/stock-agent-sidecar/chart \
  -n confidential-sidecar-test -f /tmp/sidecar-values.json > /tmp/sidecar.yaml
oc apply -n confidential-sidecar-test -f /tmp/sidecar.yaml
oc logs -n confidential-sidecar-test stock-agent-sidecar-mounts -c storage-sidecar
oc logs -n confidential-sidecar-test stock-agent-sidecar-mounts -c application
```

The bootstrap step installs namespace/SA/SCC/RBAC before the Pod. The probe requests 64 GiB
guest RAM, four guest vCPUs and a 300-second per-pod creation timeout to accommodate
the unchanged large vLLM image. No runtime configuration file is edited.

## Encrypted follow-up

`encrypted.enabled=true` runs the same mount-transfer test on synthetic encrypted
filesystems: LUKS + dm-verity for RO, LUKS + keyed dm-integrity for RW. Both disk
images are new 128 MiB regular files on a private guest tmpfs. Keys are random,
ephemeral and never sent to the tool server. RW close/reopen is tested in the same
VM. This mode does not allocate or format a PVC.

Use the hash-pinned tool/module inputs from the existing downstream-guest build;
`prepare-tools.py` rejects any file whose bytes differ from the recorded inventory.

```sh
python3 tests/confidential-storage/stock-agent-sidecar/prepare-tools.py \
  --packages /path/to/verified/storage-downstream-packages \
  --output /tmp/sidecar-public-tools.tar > /tmp/sidecar-bundle.json
helm template stock-sidecar tests/confidential-storage/stock-agent-sidecar/chart \
  -n confidential-sidecar-test --set workload.enabled=false --set tools.enabled=true \
  > /tmp/sidecar-tools.yaml
oc apply -n confidential-sidecar-test -f /tmp/sidecar-tools.yaml
oc wait -n confidential-sidecar-test pod/sidecar-public-tools \
  --for=condition=Ready --timeout=120s
oc exec -n confidential-sidecar-test -i sidecar-public-tools -- \
  python3 -c 'import pathlib,sys; pathlib.Path("/srv/bundle.tar").write_bytes(sys.stdin.buffer.read())' \
  < /tmp/sidecar-public-tools.tar
python3 - <<'PYTHON'
import json
values = json.load(open('/tmp/sidecar-values.json'))
values['encrypted'] = {
    'enabled': True,
    'toolsSha256': json.load(open('/tmp/sidecar-bundle.json'))['bundleSha256'],
}
json.dump(values, open('/tmp/sidecar-encrypted-values.json', 'w'))
PYTHON
helm template stock-sidecar tests/confidential-storage/stock-agent-sidecar/chart \
  -n confidential-sidecar-test -f /tmp/sidecar-encrypted-values.json \
  --set podName=stock-agent-sidecar-encrypted > /tmp/sidecar-encrypted.yaml
oc apply -n confidential-sidecar-test -f /tmp/sidecar-encrypted.yaml
```

The helper loads the matching integrity module/dependencies into the guest kernel;
the agent, kernel image and initrd are not rebuilt. This helper needs module-loading
and device-node privileges in addition to mounting. In a maintained deployment,
package reviewed userspace/modules in the helper image instead of a tool server.

After collecting logs, remove only the test pods and tool server/service in this
namespace. The namespace-scoped role and dedicated SCC are test scaffolding too.
Do not use a broad cleanup command against either existing Qwen namespace.

## What this does not establish

- Neither mode tests GPU inference, Trustee key release or fresh-VM PVC persistence.
  The encrypted mode tests LUKS/verity/integrity with ephemeral guest-RAM disks;
  the simpler tmpfs mode tests mount transfer only.
- Diagnostic InitData deliberately permits broad container creation and denies
  exec/policy replacement; it points KBS to an unreachable loopback endpoint.
  It is **not** a reviewed production workload policy and must not receive keys.
- The sidecar is part of the trusted computing base. Its privileges allow it to
  inspect/modify the application. This is not isolation from the sidecar itself.
- PID exchange and success-file coordination are diagnostic scaffolding, not the
  authenticated volume contract or storage-authority protocol.
- Clean cleanup is tested; application/sidecar crashes, restarts, PID reuse,
  readiness gating and ambiguous ownership still need lifecycle work.
- The current agent's other admission/hardening changes are not reproduced merely
  by using a privileged sidecar. Private AA/CDH access and exact measured policy
  require separate validation before integrating real encrypted volumes.

The existing GPU services in `gpu-workload` and `confidential-storage-test` are
outside this experiment. Neither is scaled, restarted or given different storage.
