from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.models import AgentRequest, ConversationCreate, ConversationUpdate, PersonaRoutingConfig
from app.services.copilot_agent import CopilotAgent
from app.services.model_routing import ModelRouting


def _azure_settings(**updates) -> Settings:
    values = {
        "local_mode": False,
        "foundry_endpoint": "https://example.services.ai.azure.com",
        "foundry_model_deployment": "luna-deployment",
        "model_deployment_deep_reasoning": "sol-deployment",
        "model_deployment_fast": "fast-deployment",
    }
    values.update(updates)
    return Settings(**values)


def test_auto_uses_roles_and_always_includes_default_subagents():
    plan = ModelRouting(_azure_settings()).plan("auto", None, lambda *_args: "token")

    assert plan.model == "orchestrator/luna-deployment"
    assert {agent["name"] for agent in plan.custom_agents} == {
        "deep-reasoning-analyst",
        "fast-worker",
    }
    assert plan.custom_agents[0]["model"] == "deep-reasoning/sol-deployment"
    assert plan.custom_agents[1]["model"] == "fast/fast-deployment"
    assert all(agent["infer"] is True for agent in plan.custom_agents)
    assert all(agent["tools"] for agent in plan.custom_agents)
    assert plan.providers and all("bearer_token_provider" in item for item in plan.providers)


def test_dedicated_selection_pins_defaults_and_persona_extras():
    routing = PersonaRoutingConfig.model_validate(
        {
            "extraSubagents": [
                {
                    "name": "legal-analyst",
                    "description": "Legal analysis",
                    "role": "deep-reasoning",
                    "prompt": "Analyze.",
                }
            ]
        }
    )
    plan = ModelRouting(_azure_settings()).plan("sol-deployment", routing)

    assert plan.model == "deep-reasoning/sol-deployment"
    assert {agent["model"] for agent in plan.custom_agents} == {"deep-reasoning/sol-deployment"}


def test_local_mode_uses_capi_ids_without_byok_providers():
    plan = ModelRouting(Settings(local_mode=True)).plan("auto", None)

    assert plan.model == "gpt-6-luna"
    assert plan.providers is None
    assert plan.models is None
    assert plan.custom_agents[1]["model"] == "gpt-6-astra"


def test_unknown_selection_is_rejected():
    with pytest.raises(ValueError, match="Unknown model selection"):
        ModelRouting(_azure_settings()).plan("not-in-catalogue", None)


def test_frontend_model_selection_contract_is_accepted():
    create = ConversationCreate.model_validate({"selectedModelId": "gpt-6-sol"})
    update = ConversationUpdate.model_validate({"modelId": "gpt-6-astra"})
    chat = AgentRequest.model_validate(
        {"conversationId": "conversation", "message": "hello", "selectedModelId": "gpt-6-luna"}
    )

    assert create.selectedModelId == "gpt-6-sol"
    assert update.modelId == "gpt-6-astra"
    assert chat.selectedModelId == "gpt-6-luna"


def test_named_role_providers_use_gateway_base_and_shared_callback():
    def callback(*_args):
        return "token"

    plan = ModelRouting(_azure_settings(llm_gateway_base_url="https://gateway.example")).plan("auto", None, callback)

    assert {provider["name"] for provider in plan.providers or []} == {
        "orchestrator",
        "deep-reasoning",
        "fast",
    }
    assert all(provider["base_url"].startswith("https://gateway.example/") for provider in plan.providers or [])
    assert all(provider["bearer_token_provider"] is callback for provider in plan.providers or [])


def test_legacy_orchestrator_alias_and_all_azure_roles_required():
    routing = ModelRouting(_azure_settings())
    assert routing.role_deployments()["orchestrator"] == "luna-deployment"

    with pytest.raises(RuntimeError, match="deep-reasoning"):
        ModelRouting(_azure_settings(model_deployment_deep_reasoning="")).validate()


def test_persona_cannot_replace_default_subagent():
    with pytest.raises(ValueError, match="cannot replace or remove"):
        PersonaRoutingConfig.model_validate(
            {
                "extraSubagents": [
                    {
                        "name": "fast-worker",
                        "description": "Override",
                        "role": "fast",
                        "prompt": "Override.",
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_selection_change_resumes_existing_sdk_history_with_new_config():
    agent = CopilotAgent(Settings(local_mode=True))
    first = AsyncMock()
    first.session_id = "sdk-session"
    resumed = AsyncMock()
    resumed.session_id = "sdk-session"
    agent._client = AsyncMock()
    agent._client.create_session.return_value = first
    agent._client.resume_session.return_value = resumed

    agent._conversation_model_selections["conversation"] = "auto"
    assert await agent._get_or_create_session("conversation") is first
    agent._conversation_model_selections["conversation"] = "gpt-6-sol"
    assert await agent._get_or_create_session("conversation") is resumed

    agent._client.resume_session.assert_awaited_once()
    (session_id,) = agent._client.resume_session.await_args.args
    assert session_id == "sdk-session"
    assert agent._client.resume_session.await_args.kwargs["model"] == "gpt-6-sol"
