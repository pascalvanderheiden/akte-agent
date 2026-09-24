"""Cloud-free contract for the Foundry project endpoint emitted by Bicep."""

import json

from tests.test_sre_agent_infra import INFRA, _compile


def test_project_endpoint_comes_from_the_project_service_endpoint() -> None:
    code, out, err = _compile(INFRA / "modules" / "ai-services.bicep")
    assert code == 0, err
    endpoint = json.loads(out)["outputs"]["projectEndpoint"]["value"]
    assert "Microsoft.CognitiveServices/accounts/projects" in endpoint
    assert ".endpoints['AI Foundry API']" in endpoint
    assert "format(" not in endpoint
