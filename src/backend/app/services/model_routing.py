"""Central model-routing policy for SDK sessions and auxiliary LLM tasks.

This module deliberately owns model identity. Callers ask for a role or task;
they do not reconstruct deployment names, provider IDs, or dedicated-mode
pinning rules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.config import Settings
from app.models import ModelCatalogue, ModelCatalogueItem, PersonaRoutingConfig

AUTO_SELECTION = "auto"
LOCAL_ROLE_MODELS = {
    "orchestrator": "gpt-6-luna",
    "deep-reasoning": "gpt-6-sol",
    "fast": "gpt-6-astra",
}
DEFAULT_SUBAGENTS = (
    {
        "name": "deep-reasoning-analyst",
        "description": "Handles complex analysis requiring deep, deliberate reasoning.",
        "role": "deep-reasoning",
        "reasoning_effort": "high",
        "infer": True,
        "tools": ["web_search", "rag_search", "code_interpreter", "foundry_agent", "skill"],
        "prompt": "Analyze the delegated problem deeply and return a precise, evidence-based result.",
    },
    {
        "name": "fast-worker",
        "description": "Handles quick extraction, classification, and drafting tasks.",
        "role": "fast",
        "reasoning_effort": "low",
        "infer": True,
        "tools": ["web_search", "rag_search", "code_interpreter", "foundry_agent", "skill"],
        "prompt": "Complete the delegated task quickly and accurately. Be concise.",
    },
)


class ModelRole(StrEnum):
    ORCHESTRATOR = "orchestrator"
    DEEP_REASONING = "deep-reasoning"
    FAST = "fast"


class AuxiliaryTask(StrEnum):
    FOLLOW_UP = "follow-up"
    SKILL_HELPER = "skill-helper"
    WEB_SEARCH = "web-search"
    ADMIN_ANALYSIS = "admin-analysis"
    EVAL_GENERATION = "eval-generation"
    EVAL_JUDGE = "eval-judge"


TASK_ROLES = {
    AuxiliaryTask.FOLLOW_UP: ModelRole.FAST,
    AuxiliaryTask.SKILL_HELPER: ModelRole.FAST,
    AuxiliaryTask.WEB_SEARCH: ModelRole.FAST,
    AuxiliaryTask.ADMIN_ANALYSIS: ModelRole.DEEP_REASONING,
    AuxiliaryTask.EVAL_GENERATION: ModelRole.DEEP_REASONING,
    AuxiliaryTask.EVAL_JUDGE: ModelRole.DEEP_REASONING,
}


@dataclass(frozen=True)
class RoutingPlan:
    selection: str
    model: str
    providers: list[dict[str, Any]] | None
    models: list[dict[str, Any]] | None
    custom_agents: list[dict[str, Any]]
    reasoning_effort: str


@dataclass(frozen=True)
class AuxiliaryTarget:
    task: AuxiliaryTask
    role: ModelRole
    model: str
    deployment: str


class ModelRouting:
    """Resolve one validated model policy for every model-calling surface."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def azure_mode(self) -> bool:
        return not self.settings.is_local_mode

    def role_deployments(self) -> dict[ModelRole, str]:
        if not self.azure_mode:
            return {ModelRole(role): model for role, model in LOCAL_ROLE_MODELS.items()}
        orchestrator = self.settings.model_deployment_orchestrator or self.settings.foundry_model_deployment
        return {
            ModelRole.ORCHESTRATOR: orchestrator,
            ModelRole.DEEP_REASONING: self.settings.model_deployment_deep_reasoning,
            ModelRole.FAST: self.settings.model_deployment_fast,
        }

    def validate(self) -> None:
        if not self.azure_mode:
            return
        endpoint = self.settings.llm_gateway_base_url or self.settings.foundry_endpoint
        missing = [role.value for role, deployment in self.role_deployments().items() if not deployment]
        if not endpoint or missing:
            detail = f"missing roles: {', '.join(missing)}" if missing else "missing endpoint"
            raise RuntimeError(
                "Azure model routing requires an endpoint and every model role "
                f"(MODEL_DEPLOYMENT_ORCHESTRATOR, MODEL_DEPLOYMENT_DEEP_REASONING, "
                f"MODEL_DEPLOYMENT_FAST; FOUNDRY_MODEL_DEPLOYMENT is the legacy "
                f"orchestrator alias): {detail}"
            )

    def catalogue(self) -> ModelCatalogue:
        deployments = self.role_deployments()
        items = [
            ModelCatalogueItem(id=AUTO_SELECTION, name="Auto", provider="routing", displayName="Auto", role="auto")
        ]
        seen: set[str] = set()
        for role, model in deployments.items():
            if model and model not in seen:
                items.append(
                    ModelCatalogueItem(
                        id=model,
                        name=model,
                        provider="azure" if self.azure_mode else "github-copilot",
                        displayName=model,
                        role=role.value,
                    )
                )
                seen.add(model)
        legacy = self.settings.foundry_model_deployment
        if self.azure_mode and legacy and legacy not in seen:
            items.append(
                ModelCatalogueItem(id=legacy, name=legacy, provider="azure", displayName=legacy, role="custom")
            )
        return ModelCatalogue(models=items)

    def validate_selection(self, selection: str | None) -> str:
        selection = selection or AUTO_SELECTION
        known = {item.id for item in self.catalogue().models}
        if selection not in known:
            raise ValueError(f"Unknown model selection {selection!r}; expected one of {sorted(known)}")
        return selection

    def plan(
        self,
        selection: str | None,
        routing: PersonaRoutingConfig | None,
        bearer_token_provider: Callable[..., Any] | None = None,
    ) -> RoutingPlan:
        selection = self.validate_selection(selection)
        roles = self.role_deployments()
        providers = self._providers(bearer_token_provider) if self.azure_mode else None
        models = self._models() if self.azure_mode else None

        def model_for(role: ModelRole) -> str:
            chosen = roles[role] if selection == AUTO_SELECTION else selection
            return self._sdk_model(chosen, role if selection == AUTO_SELECTION else None)

        custom_agents: list[dict[str, Any]] = []
        extra = routing.extraSubagents if routing else []
        for item in (*DEFAULT_SUBAGENTS, *extra):
            if isinstance(item, dict):
                name = item["name"]
                description = item["description"]
                role = ModelRole(item["role"])
                effort = item["reasoning_effort"]
                prompt = item["prompt"]
                tools = item["tools"]
                infer = item["infer"]
            else:
                name = item.name
                description = item.description
                role = ModelRole(item.role)
                effort = item.reasoningEffort
                prompt = item.prompt
                tools = item.tools
                infer = True
            agent = {
                "name": name,
                "description": description,
                "model": model_for(role),
                "reasoning_effort": effort,
                "prompt": prompt,
                "infer": infer,
            }
            if tools is not None:
                agent["tools"] = tools
            custom_agents.append(agent)
        effort = routing.baselineReasoningEffort if routing else "medium"
        return RoutingPlan(
            selection=selection,
            model=model_for(ModelRole.ORCHESTRATOR),
            providers=providers,
            models=models,
            custom_agents=custom_agents,
            reasoning_effort=effort,
        )

    def auxiliary_target(self, task: AuxiliaryTask) -> AuxiliaryTarget:
        role = TASK_ROLES[task]
        deployment = self.role_deployments()[role]
        return AuxiliaryTarget(task=task, role=role, model=self._sdk_model(deployment, role), deployment=deployment)

    def auxiliary_chat_url(self, task: AuxiliaryTask) -> str:
        target = self.auxiliary_target(task)
        base = (self.settings.llm_gateway_base_url or self.settings.foundry_endpoint).rstrip("/")
        if not base:
            raise RuntimeError("Auxiliary model routing requires FOUNDRY_ENDPOINT or LLM_GATEWAY_BASE_URL")
        if not self.settings.llm_gateway_base_url and ".services.ai.azure.com" not in base:
            account = base.split("//", 1)[-1].split(".", 1)[0]
            base = f"https://{account}.services.ai.azure.com"
        return f"{base}/openai/deployments/{target.deployment}/chat/completions?api-version=2024-12-01-preview"

    def _sdk_model(self, deployment: str, preferred_role: ModelRole | None = None) -> str:
        if not self.azure_mode:
            return deployment
        if preferred_role is not None and self.role_deployments()[preferred_role] == deployment:
            provider = preferred_role.value
        else:
            provider = next(name for name, value in self._provider_entries() if value == deployment)
        return f"{provider}/{deployment}"

    def _provider_entries(self) -> list[tuple[str, str]]:
        entries = [(role.value, deployment) for role, deployment in self.role_deployments().items()]
        legacy = self.settings.foundry_model_deployment
        if legacy and legacy not in {deployment for _, deployment in entries}:
            entries.append(("settings", legacy))
        return entries

    def _models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for provider, deployment in self._provider_entries():
            models.append(
                {
                    "id": deployment,
                    "provider": provider,
                    "wire_model": deployment,
                    "model_id": deployment,
                    "name": deployment,
                }
            )
        return models

    def _providers(self, bearer_token_provider: Callable[..., Any] | None) -> list[dict[str, Any]]:
        endpoint = (self.settings.llm_gateway_base_url or self.settings.foundry_endpoint).rstrip("/")
        providers: list[dict[str, Any]] = []
        for name, deployment in self._provider_entries():
            provider: dict[str, Any] = {
                "name": name,
                "type": "azure",
                "base_url": f"{endpoint}/openai/deployments/{deployment}",
                "wire_api": "completions",
                "azure": {"api_version": "2024-10-21"},
            }
            if bearer_token_provider is not None:
                provider["bearer_token_provider"] = bearer_token_provider
            providers.append(provider)
        return providers
