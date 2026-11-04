from typing import Annotated

from fastapi import Depends, Request

from app.models import ErrorResponse
from app.service import MeterService


def get_service(request: Request) -> MeterService:
    return request.app.state.service


ServiceDep = Annotated[MeterService, Depends(get_service)]

# Documented error responses, shared by the routes (all use the same ErrorResponse shape).
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Unknown meter"}}
UPSTREAM_ERRORS = {
    422: {"model": ErrorResponse, "description": "Invalid query parameters"},
    502: {"model": ErrorResponse, "description": "The portal failed, rejected our login, or answered unexpectedly"},
    504: {"model": ErrorResponse, "description": "The portal timed out"},
}
