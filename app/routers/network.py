from fastapi import APIRouter

from app.models import NetworkResponse, TransformerList
from app.routers.deps import UPSTREAM_ERRORS, ServiceDep

router = APIRouter(tags=["network"])


@router.get("/network", response_model=NetworkResponse, summary="Network hierarchy tree", responses=UPSTREAM_ERRORS)
async def get_network(service: ServiceDep) -> NetworkResponse:
    """Zone → circle → division → subdivision → substation → feeder → transformer, with meter counts, rebuilt from each
    meter's reported path. The portal's hierarchy data is inconsistent, so nodes are keyed by their full path (a code
    can appear under more than one parent) and every inconsistency found is listed in `issues`."""
    return await service.network()


@router.get(
    "/transformers", response_model=TransformerList, summary="Distribution transformers", responses=UPSTREAM_ERRORS
)
async def list_transformers(service: ServiceDep) -> TransformerList:
    """Every distribution transformer with its capacity, feeder (from the portal's transformer list) and the number
    of meters connected to it. Use `GET /meters?dt=DT-001` for the meters themselves."""
    return await service.transformers()
