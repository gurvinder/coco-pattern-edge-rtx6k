"""Packaging admission tests; no keys, disk operations or live runtime changes."""

import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "native_build", Path(__file__).with_name("build.py")
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class GpuBuildTests(unittest.TestCase):
    def config(self):
        return {
            "schema_version": 2,
            "purpose": "confidential-storage-guest/v2",
            "authority": {"trustee_rcar": True},
            "kbs": {
                "provider": "private-cdh",
                "resources": {"kbs:///default/test/key": "raw32"},
            },
            "volumes": [
                {
                    "binding_id": "model",
                    "transport_path": "/dev/coco-volume-0",
                    "expected": {},
                    "manifest": [],
                }
            ],
        }

    def test_gpu_requires_explicit_rcar_and_private_cdh(self):
        build.validate_config(self.config(), 1024, True)
        for field, value in [
            ("authority", {}),
            ("authority", {"trustee_rcar": False}),
            ("kbs", {"provider": "cpu"}),
            ("kbs", {"provider": "private-cdh", "resources": {}}),
            ("kbs", {"provider": "private-cdh", "resources": {"key": "auto"}}),
        ]:
            c = self.config()
            c[field] = value
            with self.assertRaises(ValueError):
                build.validate_config(c, 1024, True)

    def test_rejects_unknown_fields_and_unbounded_groups(self):
        for change in [
            {"secret": "never-package"},
            {"volumes": []},
            {"volumes": self.config()["volumes"] * 9},
            {"schema_version": 3},
        ]:
            c = self.config()
            c.update(change)
            with self.assertRaises(ValueError):
                build.validate_config(c, 1024, True)
        c = self.config()
        c["volumes"][0]["key"] = "never-package"
        with self.assertRaises(ValueError):
            build.validate_config(c, 1024, True)
        with self.assertRaises(ValueError):
            build.validate_config(self.config(), 256 * 1024 + 1, True)

    def test_legacy_stays_cpu_only(self):
        c = self.config()
        del c["volumes"]
        c.update(
            schema_version=1,
            purpose="isolated-confidential-storage/v1",
            expected={},
            manifest=[],
        )
        build.validate_config(c, 1024, False)
        with self.assertRaises(ValueError):
            build.validate_config(c, 1024, True)

    def test_validation_does_not_mutate_configuration(self):
        c = self.config()
        before = copy.deepcopy(c)
        build.validate_config(c, 1024, True)
        self.assertEqual(c, before)


if __name__ == "__main__":
    unittest.main()
