"""Ensure Container Apps probes align with the backend warm-pool readiness gate."""

import json
import subprocess
import unittest
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1]
WARM_POOL_WARMUP_TIMEOUT_MAX_SECONDS = 90


class AgentServiceReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = INFRA / "modules" / "agent-service.bicep"
        cls.template = json.loads(
            subprocess.check_output(
                ["az", "bicep", "build", "--file", str(module), "--stdout"],
                text=True,
            )
        )
        cls.parameters = cls.template["parameters"]
        app = next(
            resource
            for resource in cls.template["resources"]
            if resource["type"] == "Microsoft.App/containerApps"
        )
        cls.container = app["properties"]["template"]["containers"][0]
        cls.probes = cls.container["probes"]

    def test_readiness_probe_uses_warmup_endpoint_and_validated_window(self):
        probes = self.container["probes"]
        self.assertIn("health/ready", probes)
        self.assertIn("readinessProbePeriodSeconds", probes)
        self.assertIn("readinessProbeFailureThreshold", probes)
        self.assertIn("readinessProbeTimeoutSeconds", probes)

        window = self.parameters["readinessProbeFailureWindowSeconds"]
        self.assertEqual(91, window["minValue"])
        self.assertEqual(2400, window["maxValue"])
        self.assertEqual(100, window["defaultValue"])
        threshold = self.template["variables"]["readinessProbeFailureThreshold"]
        self.assertEqual(10, threshold)
        self.assertIn(
            "readinessProbeFailureWindowSeconds",
            self.template["variables"]["readinessProbePeriodSeconds"],
        )

        for configured_window in range(window["minValue"], window["maxValue"] + 1):
            period = -(-configured_window // threshold)
            self.assertGreater(
                period * threshold, WARM_POOL_WARMUP_TIMEOUT_MAX_SECONDS
            )
            self.assertGreaterEqual(period * threshold, configured_window)
            self.assertLessEqual(period, 240)

    def test_bootstrap_revision_defers_http_probes_until_application_deploy(self):
        probes = self.container["probes"]
        self.assertIn("if(reference(", probes)
        self.assertIn("outputs.isBootstrap.value", probes)
        self.assertIn("createArray()", probes)
        self.assertIn("createArray(createObject('type', 'Readiness'", probes)
        self.assertIn("createObject('type', 'Liveness'", probes)

    def test_liveness_probe_remains_independent_of_warmup(self):
        self.assertIn("'path', '/health'", self.probes)
        self.assertIn("livenessProbePeriodSeconds", self.probes)
        self.assertIn("livenessProbeFailureThreshold", self.probes)
        self.assertIn("livenessProbeTimeoutSeconds", self.probes)
        self.assertIn("livenessProbeInitialDelaySeconds", self.probes)


if __name__ == "__main__":
    unittest.main()
