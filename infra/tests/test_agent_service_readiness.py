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
        cls.probes = {probe["type"]: probe for probe in cls.container["probes"]}

    def test_readiness_probe_uses_warmup_endpoint_and_parameterized_window(self):
        readiness = self.probes["Readiness"]
        self.assertEqual("/health/ready", readiness["httpGet"]["path"])
        self.assertEqual("[parameters('readinessProbePeriodSeconds')]", readiness["periodSeconds"])
        self.assertEqual(
            "[parameters('readinessProbeFailureThreshold')]", readiness["failureThreshold"]
        )
        self.assertEqual(
            "[parameters('readinessProbeTimeoutSeconds')]", readiness["timeoutSeconds"]
        )

        window_seconds = (
            self.parameters["readinessProbePeriodSeconds"]["defaultValue"]
            * self.parameters["readinessProbeFailureThreshold"]["defaultValue"]
        )
        self.assertGreater(window_seconds, WARM_POOL_WARMUP_TIMEOUT_MAX_SECONDS)

    def test_liveness_probe_remains_independent_of_warmup(self):
        liveness = self.probes["Liveness"]
        self.assertEqual("/health", liveness["httpGet"]["path"])
        self.assertEqual("[parameters('livenessProbePeriodSeconds')]", liveness["periodSeconds"])
        self.assertEqual(
            "[parameters('livenessProbeFailureThreshold')]", liveness["failureThreshold"]
        )
        self.assertEqual(
            "[parameters('livenessProbeTimeoutSeconds')]", liveness["timeoutSeconds"]
        )
        self.assertEqual(
            "[parameters('livenessProbeInitialDelaySeconds')]", liveness["initialDelaySeconds"]
        )


if __name__ == "__main__":
    unittest.main()
