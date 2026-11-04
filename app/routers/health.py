from fastapi import APIRouter

from app.models import HealthResponse
from app.routers.deps import ServiceDep

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health and cache state")
async def health(service: ServiceDep) -> HealthResponse:
    """Doesn't call the portal. `degraded` means the last refresh from the portal failed and older data is being served."""
    return service.health()
