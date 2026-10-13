# Reproduce the first confidential-storage checkpoint

This installs only a disposable raw-device diagnostic. It does not install
encrypted storage or the authority service. Read the [compatibility report](compatibility-report.md)
and [authority implementation limits](../../components/confidential-storage-authority/README.md).

## Prerequisites

Use the development branch, an authorized kubeconfig, `oc`, Helm 3 and Python 3.9+.
Confirm the dedicated namespace is unused, inspect live GitOps targets and choose
an existing CPU confidential runtime and raw-block CSI class. The edge override
selects `kata-cc` and `lvms-vg1`; adapt it to another cluster deliberately.
Do not run a pattern-wide install or sync the live inference chart.

```sh
python3 scripts/confidential-storage/audit.py --output /tmp/storage-before.json
oc get namespace confidential-storage-test  # Expect NotFound on first installation.
oc create namespace confidential-storage-test
python3 scripts/confidential-storage/probe-initdata.py > /tmp/storage-initdata.json
oc create -f /tmp/storage-initdata.json
helm template storage-audit charts/coco-supported/confidential-storage-test \
  -n confidential-storage-test \
  -f overrides/values-confidential-storage-test-edge.yaml > /tmp/storage-probe.yaml
oc apply --dry-run=server -n confidential-storage-test -f /tmp/storage-probe.yaml
oc apply -n confidential-storage-test -f /tmp/storage-probe.yaml
oc get pod,pvc -n confidential-storage-test
oc logs -n confidential-storage-test storage-audit-block-probe
```

This uses `helm template` followed by `oc apply`, not a Helm-managed release.
The keep annotation protects claims for future Helm use; with `oc apply`, cleanup
is explicit. The pod has a three-minute active deadline, no GPU request, no API
service-account token, zero requested capabilities and no privilege escalation.
The claim has no populator source and contains no model or application data.

The generator intentionally configures no real Trustee endpoint or unlock
resource. Its public-image policy is diagnostic only. Do not add its measurement
to an existing model-key allowlist. Do not reuse it for a confidential workload.
On this cluster, the final pod exits 1 because the delivered device is not readable
by the application UID. Preserve that failure as a compatibility result.

To repeat the same attachment test, delete only the finished probe pod, wait for
termination, then reapply the rendered file. The existing claim is reused. Do not
delete the PVC as a way to recover an initialized confidential volume later.

```sh
oc delete pod storage-audit-block-probe -n confidential-storage-test --wait=true
oc apply -n confidential-storage-test -f /tmp/storage-probe.yaml
```

## Local component checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s components/confidential-storage-authority -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/confidential-storage -p 'test_*.py' -v
helm lint charts/coco-supported/confidential-storage-test \
  --namespace confidential-storage-test \
  -f overrides/values-confidential-storage-test-edge.yaml
git diff --check
```

The authority library must not be deployed behind an unauthenticated API. Its
unit tests pass trusted peer inputs directly; they are not attestation tests.
No database or keys belong in Git or the workload namespace.

## Cleanup and nondisruption check

First save the log and selected pod/PVC status. Delete only the named diagnostic
pod. The October 13 checkpoint retains the 1 GiB empty PVC, probe ConfigMap and
no-secret InitData for the next stage. No diagnostic workload remains running.

```sh
oc delete pod storage-audit-block-probe -n confidential-storage-test \
  --ignore-not-found --wait=true
python3 scripts/confidential-storage/audit.py \
  --compare /tmp/storage-before.json --test-namespace confidential-storage-test \
  --output /tmp/storage-after.json
```

For complete disposal, after confirming these are still the disposable resources
from this test and no consumers remain, delete their explicit names:

```sh
oc delete configmap storage-audit-probe storage-audit-no-secrets \
  -n confidential-storage-test
oc delete pvc storage-audit-data -n confidential-storage-test
```

PVC deletion may delete its backing LV. Never apply this cleanup to a volume
holding confidential application data without its own retention/recovery decision.
