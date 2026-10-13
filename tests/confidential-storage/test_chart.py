"""Check the deployment boundary and generated no-secret diagnostic configuration."""

import base64
import gzip
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/coco-supported/confidential-storage-test"
OVERRIDE = ROOT / "overrides/values-confidential-storage-test-edge.yaml"


class ChartTests(unittest.TestCase):
    def render(self, namespace="confidential-storage-test", *extra):
        return subprocess.run(
            [
                "helm",
                "template",
                "storage-audit",
                str(CHART),
                "-n",
                namespace,
                "-f",
                str(OVERRIDE),
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_live_namespace_rejected(self):
        result = self.render("gpu-workload")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dedicated", result.stderr)

    def test_floating_image_rejected(self):
        self.assertNotEqual(
            self.render(
                "confidential-storage-test", "--set", "image=busybox:latest"
            ).returncode,
            0,
        )

    def test_rwo_fallback_rejected(self):
        self.assertNotEqual(
            self.render(
                "confidential-storage-test", "--set", "accessMode=ReadWriteOnce"
            ).returncode,
            0,
        )

    def test_valid_diagnostic_renders(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("privileged: false", result.stdout)
        self.assertIn("drop: [ALL]", result.stdout)
        self.assertNotIn("nvidia.com/pgpu", result.stdout)

    def test_initdata_has_no_production_key_client(self):
        path = ROOT / "scripts/confidential-storage/probe-initdata.py"
        result = subprocess.check_output(["python3", str(path)], text=True)
        document = json.loads(result)
        raw = gzip.decompress(base64.b64decode(document["data"]["INITDATA"])).decode()
        self.assertNotIn("kbs:///", raw)
        self.assertNotIn("image_security_policy_uri", raw)
        self.assertIn('url = "http://127.0.0.1:1"', raw)
        self.assertIn("default ExecProcessRequest := false", raw)
        self.assertIn("default SetPolicyRequest := false", raw)
        self.assertIn("default ReadStreamRequest := true", raw)


if __name__ == "__main__":
    unittest.main()
