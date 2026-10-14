"""Render real Helm/ACM templates; mock only ACM's cluster lookups."""

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
HELM = os.environ.get("HELM", "helm")
BINDING = {"launchMeasurement": "a" * 96, "initData": "b" * 64}


def render(bindings):
    values = {
        "global": {"coco": {"secured": True}},
        "kbs": {"resourcePolicy": {"modelKeyBindings": bindings}},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
        json.dump(values, f)
        f.flush()
        return list(
            yaml.safe_load_all(
                subprocess.check_output(
                    [
                        HELM,
                        "template",
                        "trustee",
                        str(ROOT / "charts/hub/trustee-edge"),
                        "-f",
                        f.name,
                    ],
                    stderr=subprocess.PIPE,
                )
            )
        )


def references(documents, firmware):
    policy = next(
        d
        for d in documents
        if d and d.get("kind") == "Policy" and d["metadata"]["name"] == "rvps-policy"
    )
    template = policy["spec"]["policy-templates"][0]["objectDefinition"]["spec"][
        "object-templates-raw"
    ]
    for cm in ("initdata", "debug-initdata"):
        for field in ("PCR8_HASH", "RAW_HASH"):
            template = template.replace(
                f'fromConfigMap "imperative" "{cm}" "{field}"',
                json.dumps(cm + "-" + field),
            )
    template = template.replace(
        '(lookup "v1" "Secret" "trustee-operator-system" "pcr-stash")', "(dict)"
    )
    template = template.replace(
        '(lookup "v1" "Secret" "trustee-operator-system" "firmware-reference-values")',
        ".Values.firmware",
    )
    # ACM and Helm use different names for the same base64 codec.
    template = template.replace("base64enc", "b64enc").replace("base64dec", "b64dec")
    stash = {"data": {"json": base64.b64encode(json.dumps(firmware).encode()).decode()}}
    with tempfile.TemporaryDirectory() as directory:
        p = Path(directory)
        (p / "templates").mkdir()
        (p / "Chart.yaml").write_text(
            "apiVersion: v2\nname: rvps-test\nversion: 0.1.0\n"
        )
        (p / "values.yaml").write_text(json.dumps({"firmware": stash}))
        (p / "templates/output.yaml").write_text(
            '{{- define "rvps" -}}\n'
            + template
            + '{{- end -}}\nresult:{{ include "rvps" . | nindent 2 }}\n'
        )
        rendered = yaml.safe_load(subprocess.check_output([HELM, "template", str(p)]))
    raw = rendered["result"][0]["objectDefinition"]["data"]["reference_value"]
    return {
        k: json.loads(base64.urlsafe_b64decode(v))["value"]
        for k, v in json.loads(raw).items()
    }


class TrusteeBindingTests(unittest.TestCase):
    def test_approved_pairs_append_without_losing_existing_references(self):
        docs = render({"model-v1": BINDING, "data-v1": BINDING})
        refs = references(
            docs, {"snp_launch_measurement": ["existing"], "snp_microcode": [117]}
        )
        self.assertEqual(refs["snp_launch_measurement"], ["existing", "a" * 96])
        self.assertEqual(refs["init_data"][-1], "b" * 64)
        self.assertEqual(len(refs["init_data"]), 7)
        self.assertEqual(refs["snp_microcode"], [117])
        policy = next(
            d
            for d in docs
            if d
            and d.get("kind") == "ConfigMap"
            and d["metadata"]["name"] == "resource-policy"
        )["data"]["resource-policy.rego"]
        self.assertIn('"data-v1"', policy)
        self.assertIn("evidence.init_data == expected.initData", policy)

    def test_empty_bindings_preserve_old_reference_sets(self):
        refs = references(render({}), {"snp_launch_measurement": ["existing"]})
        self.assertEqual(refs["snp_launch_measurement"], ["existing"])
        self.assertEqual(len(refs["init_data"]), 6)

    def test_bindings_work_without_firmware_measurements(self):
        refs = references(render({"model-v1": BINDING}), {})
        self.assertEqual(refs["snp_launch_measurement"], ["a" * 96])

    def test_malformed_approvals_fail_render(self):
        for bad in (
            {**BINDING, "initData": ""},
            {**BINDING, "extra": True},
            {**BINDING, "launchMeasurement": "unknown"},
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                render({"model-v1": bad})
