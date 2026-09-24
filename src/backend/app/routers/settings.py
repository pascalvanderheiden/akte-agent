"""Settings endpoint — lets users view AI service configuration."""

import logging

from fastapi import APIRouter, Request

from app.config import get_settings
from app.models import AIServiceSettings, AIServiceStatus
from app.services.model_routing import ModelRouting

logger = logging.getLogger(__name__)

router = APIRouter()


def _status(settings) -> AIServiceStatus:
    routing = ModelRouting(settings)
    return AIServiceStatus(
        configured=not routing.azure_mode or bool(settings.foundry_endpoint or settings.llm_gateway_base_url),
        foundryEndpoint=settings.foundry_endpoint,
        foundryModelDeployment=settings.foundry_model_deployment,
    )


@router.get("", response_model=AIServiceStatus)
async def get_settings_endpoint(request: Request) -> AIServiceStatus:
    """Return current AI service config status."""
    settings = get_settings()
    return _status(settings)


@router.post("", response_model=AIServiceStatus)
async def update_settings_endpoint(body: AIServiceSettings, request: Request) -> AIServiceStatus:
    """Preserve the existing singular BYOK settings contract and reset sessions."""
    settings = get_settings()
    settings.foundry_endpoint = body.foundryEndpoint.strip()
    settings.foundry_model_deployment = body.foundryModelDeployment.strip()
    ModelRouting(settings).validate()

    cosmos = getattr(request.app.state, "cosmos_service", None)
    if cosmos is not None:
        await cosmos.delete_all_session_mappings()
    logger.info("AI service settings updated — session mappings reset")
    return _status(settings)
