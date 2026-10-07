"""Ensure Container Apps probes align with the backend warm-pool readiness gate."""

import json
import subprocess
import unittest
from pathlib import Path

from arm_template import Evaluator

INFRA = Path(__file__).resolve().parents[1]
WARM_POOL_WARMUP_TIMEOUT_MAX_SECONDS = 90
DEPLOYED_IMAGE = "example.azurecr.io/agent-service:deployed"


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
        cls.container_app = next(
            resource for resource in cls.template["resources"] if resource["type"] == "Microsoft.App/containerApps"
        )

    def evaluator(self, exists, parameters=None):
        evaluator = Evaluator(self.template, {"exists": exists, **(parameters or {})})

        def resolve_reference(resource_id):
            if resource_id.resource_type == "Microsoft.App/containerApps":
                return {"template": {"containers": [{"image": DEPLOYED_IMAGE}]}}
            if resource_id.resource_type != "Microsoft.Resources/deployments":
                raise AssertionError(f"Unexpected reference to {resource_id.resource_type}")

            deployment = next(
                resource
                for resource in self.template["resources"]
                if resource["type"] == "Microsoft.Resources/deployments"
                and evaluator.evaluate(resource["name"]) == resource_id.segments[-1]
            )
            module_template = deployment["properties"]["template"]
            module_parameters = {
                name: evaluator.evaluate(value["value"])
                for name, value in deployment["properties"]["parameters"].items()
            }
            module_evaluator = Evaluator(module_template, module_parameters)
            module_evaluator.reference_resolver = resolve_reference
            return {"outputs": {name: {"value": module_evaluator.output(name)} for name in module_template["outputs"]}}

        evaluator.reference_resolver = resolve_reference
        return evaluator

    def resolved_probes(self, exists, parameters=None):
        evaluator = self.evaluator(exists, parameters)
        return evaluator.evaluate(self.container_app["properties"]["template"]["containers"][0]["probes"])

    def test_bootstrap_revision_has_no_http_probes(self):
        self.assertEqual([], self.resolved_probes(exists=False))

    def test_deployed_revision_has_exact_readiness_and_liveness_probes(self):
        probes = self.resolved_probes(exists=True)
        self.assertEqual(
            [
                {
                    "type": "Readiness",
                    "httpGet": {
                        "path": "/health/ready",
                        "port": 8000,
                        "scheme": "HTTP",
                    },
                    "periodSeconds": 10,
                    "failureThreshold": 10,
                    "timeoutSeconds": 5,
                },
                {
                    "type": "Liveness",
                    "httpGet": {"path": "/health", "port": 8000, "scheme": "HTTP"},
                    "initialDelaySeconds": 30,
                    "periodSeconds": 30,
                    "failureThreshold": 3,
                    "timeoutSeconds": 5,
                },
            ],
            probes,
        )

    def test_every_accepted_readiness_window_exceeds_warmup_maximum(self):
        parameter = self.template["parameters"]["readinessProbeFailureWindowSeconds"]
        threshold = self.template["variables"]["readinessProbeFailureThreshold"]
        self.assertEqual(91, parameter["minValue"])
        self.assertEqual(2400, parameter["maxValue"])
        self.assertEqual(100, parameter["defaultValue"])
        self.assertEqual(10, threshold)

        configured_windows = (
            parameter["minValue"],
            99,
            parameter["defaultValue"],
            101,
            parameter["maxValue"],
        )
        for configured_window in configured_windows:
            period = -(-configured_window // threshold)
            with self.subTest(window=configured_window):
                probes = self.resolved_probes(
                    exists=True,
                    parameters={"readinessProbeFailureWindowSeconds": configured_window},
                )
                readiness = probes[0]
                self.assertEqual(period, readiness["periodSeconds"])
                self.assertEqual(threshold, readiness["failureThreshold"])
                self.assertGreater(
                    readiness["periodSeconds"] * readiness["failureThreshold"],
                    WARM_POOL_WARMUP_TIMEOUT_MAX_SECONDS,
                )

        for configured_window in range(parameter["minValue"], parameter["maxValue"] + 1):
            period = -(-configured_window // threshold)
            self.assertGreater(period * threshold, WARM_POOL_WARMUP_TIMEOUT_MAX_SECONDS)

    def test_root_template_exposes_probe_settings_as_azd_inputs(self):
        main = (INFRA / "main.bicep").read_text()
        parameters = json.loads((INFRA / "main.parameters.json").read_text())["parameters"]
        for name, environment_name, module_parameter in (
            (
                "agentServiceReadinessProbeFailureWindowSeconds",
                "AGENT_SERVICE_READINESS_PROBE_FAILURE_WINDOW_SECONDS",
                "readinessProbeFailureWindowSeconds",
            ),
            (
                "agentServiceReadinessProbeTimeoutSeconds",
                "AGENT_SERVICE_READINESS_PROBE_TIMEOUT_SECONDS",
                "readinessProbeTimeoutSeconds",
            ),
            (
                "agentServiceLivenessProbePeriodSeconds",
                "AGENT_SERVICE_LIVENESS_PROBE_PERIOD_SECONDS",
                "livenessProbePeriodSeconds",
            ),
            (
                "agentServiceLivenessProbeFailureThreshold",
                "AGENT_SERVICE_LIVENESS_PROBE_FAILURE_THRESHOLD",
                "livenessProbeFailureThreshold",
            ),
            (
                "agentServiceLivenessProbeTimeoutSeconds",
                "AGENT_SERVICE_LIVENESS_PROBE_TIMEOUT_SECONDS",
                "livenessProbeTimeoutSeconds",
            ),
            (
                "agentServiceLivenessProbeInitialDelaySeconds",
                "AGENT_SERVICE_LIVENESS_PROBE_INITIAL_DELAY_SECONDS",
                "livenessProbeInitialDelaySeconds",
            ),
        ):
            with self.subTest(parameter=name):
                self.assertIn(name, parameters)
                self.assertIn(environment_name, parameters[name]["value"])
                self.assertIn(f"param {name} ", main)
                self.assertIn(f"{module_parameter}: {name}", main)
