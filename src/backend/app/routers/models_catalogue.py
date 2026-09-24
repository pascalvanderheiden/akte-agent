"""Model catalogue exposed to chat clients."""

from fastapi import APIRouter, Request

from app.config import get_settings
from app.models import ModelCatalogue
from app.services.model_routing import ModelRouting

router = APIRouter()


@router.get("/api/models", response_model=ModelCatalogue)
async def list_models(request: Request) -> ModelCatalogue:
    return ModelRouting(getattr(request.app.state, "settings", get_settings())).catalogue()
