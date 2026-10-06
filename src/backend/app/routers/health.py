"""Health check endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "kratos-agent-service"}


@router.get("/health/ready", response_model=None)
async def readiness_check(request: Request) -> dict[str, str] | JSONResponse:
    proxy = getattr(request.app.state, "foundry_proxy", None)
    state = getattr(proxy, "warmup_state", "ready")
    if state == "warming":
        return JSONResponse(status_code=503, content={"status": "warming"})
    if state == "degraded":
        return {
            "status": "degraded",
            "detail": "Warm pool did not fully initialize; conversations can still start with isolated sandboxes.",
        }
    return {"status": "ready"}
