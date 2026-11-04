"""FastAPI application: wiring, lifecycle and the single JSON error shape.

Run with:  uvicorn app.main:app
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings
from app.errors import ApiError
from app.portal.client import PortalClient
from app.routers import health, meters, network
from app.service import MeterService

DESCRIPTION = """
A clean, documented API in front of the **Urja Meter Ops** portal, which has no API of its own.

* **Meters**: details, location and network position for every meter, with filters and a "near me" search
  the portal itself doesn't offer.
* **Readings and consumption**: the portal's 30-minute register readings, plus derived usage per interval or
  per day, with power factor and data-quality flags.
* **Network**: the distribution hierarchy (zone → … → transformer) rebuilt from the portal's data, with its
  inconsistencies listed rather than hidden.

Times are ISO-8601 in IST (+05:30), as the portal's own (offset-less) times are interpreted. Every error has the
shape `{"error": {"code", "message", "details"}}`. How the portal works underneath is described in `PROTOCOL.md`.
"""


def create_app(settings: Settings | None = None, portal_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`settings` and `portal_transport` are for tests; normally both come from the environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings or Settings()  # reads env vars / .env; fails fast if credentials are missing
        client = PortalClient(config, transport=portal_transport)
        service = MeterService(client, config)
        app.state.service = service
        warm_task = asyncio.create_task(service.warm()) if config.warm_cache_on_startup else None
        try:
            yield
        finally:
            if warm_task:
                warm_task.cancel()
            await client.aclose()

    app = FastAPI(title="Urja Meter API", version="0.1.0", description=DESCRIPTION, lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_request", "Invalid request parameters", jsonable_encoder(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _error(exc.status_code, code, str(exc.detail))

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    app.include_router(meters.router)
    app.include_router(network.router)
    app.include_router(health.router)
    return app


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}})


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
