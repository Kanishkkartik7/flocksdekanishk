"""HTTP client for the Urja Meter Ops portal. Everything that knows the portal's protocol lives here.

What it handles (details in PROTOCOL.md):
- Login is a SvelteKit form action: POST /login (form-encoded) returns HTTP 200 with a JSON ActionResult;
  success is {"type": "redirect", ...} and sets the `__Secure-better-auth.session_token` cookie (1 h lifetime);
  a wrong password gives {"type": "failure", "status": 401, ...}.
- Data endpoints only need that cookie. Without a valid session they answer 401 with JSON. We log in again a little
  before the hour is up, and also whenever a response looks logged out: 401/403, or, as a precaution, a redirect or HTML
  where JSON was expected.
- The portal sometimes returns 502s, and rate-limits bursts with 429, so requests are retried with exponential backoff on
  timeouts, connection errors and 429/502/503/504 (a short `Retry-After` is honoured). A 429 that persists is passed on as 429.
- GET /portal/export must be HMAC-signed with a secret from GET /portal/keys (see signing.py).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.errors import (
    PortalAuthError,
    PortalError,
    PortalNotFound,
    PortalProtocolError,
    PortalRateLimitedError,
    PortalTimeoutError,
    PortalUnavailableError,
)
from app.portal.signing import sign_request

log = logging.getLogger(__name__)

SESSION_COOKIE = "__Secure-better-auth.session_token"
RETRYABLE_STATUS = {429, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 10.0  # never sleep longer than this because of a Retry-After header
MAX_PAGES = 500  # safety stop for paginated endpoints


class PortalClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._origin = settings.portal_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._origin,
            timeout=settings.portal_timeout_seconds,
            follow_redirects=False,  # a redirect from a data endpoint means "log in", so we want to see it
            headers={"user-agent": "urja-meter-api/0.1"},
            transport=transport,
        )
        self._session_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(settings.portal_max_concurrency)
        self._logged_in_at: float | None = None
        self._session_generation = 0
        self._signing_secret: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Public data methods ----------------------------------------------------------------------------------------

    async def fetch_export(self) -> list[dict[str, Any]]:
        """All meters with hierarchy and location, via the signed bulk export."""
        return await self._collect_pages(lambda page: self.get_json(f"/portal/export?page={page}", signed=True))

    async def fetch_transformers(self) -> list[dict[str, Any]]:
        """All distribution transformers (20 per page on the portal)."""
        return await self._collect_pages(lambda page: self.get_json(f"/portal/dts?page={page}"))

    async def fetch_energy(self, meter_id: str) -> list[dict[str, Any]]:
        """Raw register readings for one meter. Raises PortalNotFound for unknown meters."""
        body = await self.get_json(f"/portal/meters/{quote(meter_id, safe='')}/energy")
        return _data_list(body)

    async def get_json(self, url: str, *, signed: bool = False) -> Any:
        """GET a portal JSON endpoint, logging in (again) when needed. `url` includes its query string."""
        await self._ensure_session()
        generation = self._session_generation
        response = await self._send("GET", url, signed=signed)

        if _looks_logged_out(response):
            log.info("Portal response to %s looks logged out (HTTP %s); logging in again", url, response.status_code)
            await self._relogin(generation)
            response = await self._send("GET", url, signed=signed)
            if _looks_logged_out(response):
                raise PortalAuthError(f"Portal still refuses {url} after logging in again (HTTP {response.status_code})")

        if response.status_code == 404:
            raise PortalNotFound(_error_message(response) or f"{url} not found")
        if response.status_code >= 400:
            raise PortalProtocolError(f"Portal returned HTTP {response.status_code} for {url}")
        body = _json_or_none(response)
        if body is None:
            raise PortalProtocolError(f"Portal returned a non-JSON body for {url}")
        return body

    # --- Session ----------------------------------------------------------------------------------------------------

    def _session_is_fresh(self) -> bool:
        return (
            self._logged_in_at is not None
            and time.monotonic() - self._logged_in_at < self._settings.portal_session_max_age_seconds
        )

    async def _ensure_session(self) -> None:
        if self._session_is_fresh():
            return
        async with self._session_lock:
            if not self._session_is_fresh():
                await self._login()

    async def _relogin(self, seen_generation: int) -> None:
        """Log in again unless another request already did since `seen_generation`."""
        async with self._session_lock:
            if self._session_generation == seen_generation:
                await self._login()

    async def _login(self) -> None:
        self._http.cookies.clear()
        self._signing_secret = None  # the secret may be tied to the session; fetch it again
        response = await self._send(
            "POST",
            "/login",
            data={"email": self._settings.portal_username, "password": self._settings.portal_password.get_secret_value()},
            headers={
                "accept": "application/json",  # ask SvelteKit for the JSON ActionResult instead of an HTML page
                "x-sveltekit-action": "true",
                "origin": self._origin,  # SvelteKit's CSRF check compares Origin with the site
            },
        )
        result = _json_or_none(response)
        result_type = result.get("type") if isinstance(result, dict) else None
        if result_type == "failure":
            # Wrong credentials (observed): {"type": "failure", "status": 401, ...}. Not retried.
            raise PortalAuthError(
                f"Portal rejected the login (status {result.get('status')}). Check PORTAL_USERNAME and PORTAL_PASSWORD."
            )
        if response.status_code != 200 or result_type != "redirect":
            raise PortalAuthError(f"Unexpected login response (HTTP {response.status_code}, result type {result_type!r})")
        if not self._http.cookies.get(SESSION_COOKIE):
            raise PortalAuthError("Portal login returned success but set no session cookie")
        self._logged_in_at = time.monotonic()
        self._session_generation += 1
        log.info("Logged in to the portal")

    # --- Transport --------------------------------------------------------------------------------------------------

    async def _send(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        signed: bool = False,
    ) -> httpx.Response:
        """One logical request, retried on transient failures. Signed requests are re-signed on every attempt."""
        attempts = self._settings.portal_max_attempts
        last_error: PortalError = PortalUnavailableError(f"No attempt made for {method} {url}")
        retry_after = 0.0
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                await asyncio.sleep(max(self._settings.portal_retry_backoff_seconds * 2 ** (attempt - 2), retry_after))
            retry_after = 0.0
            request_headers = dict(headers or {})
            if signed:
                request_headers.update(await self._signature_headers(method, url))
            try:
                async with self._semaphore:
                    response = await self._http.request(method, url, data=data, headers=request_headers)
            except httpx.TimeoutException:
                last_error = PortalTimeoutError(f"Portal timed out on {method} {url}")
            except httpx.TransportError as exc:
                last_error = PortalUnavailableError(f"Could not reach the portal ({type(exc).__name__}) on {method} {url}")
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    return response
                if response.status_code == 429:
                    retry_after = _retry_after_seconds(response)
                    last_error = PortalRateLimitedError(f"Portal rate-limited {method} {url} (HTTP 429); retry later")
                else:
                    last_error = PortalUnavailableError(f"Portal returned HTTP {response.status_code} for {method} {url}")
            log.warning("Portal request failed (attempt %d/%d): %s", attempt, attempts, last_error.message)
        raise last_error

    async def _signature_headers(self, method: str, url: str) -> dict[str, str]:
        if self._signing_secret is None:
            body = await self.get_json("/portal/keys")
            try:
                self._signing_secret = str(body["data"]["signingSecret"])
            except (KeyError, TypeError) as exc:
                raise PortalProtocolError("Unexpected response from /portal/keys") from exc
        path, _, query = url.partition("?")
        timestamp = int(time.time())
        signature = sign_request(self._signing_secret, method, path, query, timestamp)
        return {"x-timestamp": str(timestamp), "x-signature": signature}

    @staticmethod
    async def _collect_pages(fetch_page: Callable[[int], Awaitable[Any]]) -> list[dict[str, Any]]:
        """Request pages 1, 2, ... until we have `total` items or a page comes back empty."""
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            body = await fetch_page(page)
            batch = _data_list(body)
            items.extend(batch)
            total = body.get("total") if isinstance(body, dict) else None
            if not batch or not isinstance(total, int) or len(items) >= total:
                break
        return items


def _looks_logged_out(response: httpx.Response) -> bool:
    if response.status_code in (401, 403) or response.is_redirect:
        return True
    # A 200 with HTML where JSON was expected is most likely the login page.
    return response.status_code == 200 and "json" not in response.headers.get("content-type", "")


def _retry_after_seconds(response: httpx.Response) -> float:
    """The `Retry-After` header in seconds, capped. The portal sent no such header when it rate-limited (observed), so
    this is usually 0 and the normal backoff applies."""
    try:
        return min(max(float(response.headers.get("retry-after", "0")), 0.0), MAX_RETRY_AFTER_SECONDS)
    except ValueError:  # an HTTP-date form; not worth parsing
        return 0.0


def _json_or_none(response: httpx.Response) -> Any:
    if "json" not in response.headers.get("content-type", ""):
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _error_message(response: httpx.Response) -> str | None:
    body = _json_or_none(response)
    return body.get("message") if isinstance(body, dict) else None


def _data_list(body: Any) -> list[dict[str, Any]]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise PortalProtocolError("Expected a JSON object with a `data` list from the portal")
    return data
