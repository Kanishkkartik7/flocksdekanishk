"""Test helpers: a fake Urja portal that behaves like the real one (see PROTOCOL.md), so tests run offline."""

import hmac
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.portal.client import SESSION_COOKIE
from app.portal.signing import sign_request

FIXTURES = Path(__file__).parent / "fixtures"
PORTAL_URL = "https://portal.test"
USERNAME = "operator@urja.local"
PASSWORD = "test-password"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakePortal:
    """Mimics the portal's protocol: form-action login, cookie sessions, HMAC-signed export, JSON/HTML 404s."""

    def __init__(self):
        self.export_records = load_fixture("export_sample.json")
        self.dts = load_fixture("dts_sample.json")
        self.energy = {"J100000": load_fixture("energy_J100000.json")["data"]}
        self.secret = "fake-signing-secret"
        self.valid_tokens: set[str] = set()
        self.logins = 0
        self.calls: Counter = Counter()
        self.failures: dict[str, list[int]] = defaultdict(list)  # path -> statuses to return before succeeding
        self.logged_out_style = "401"  # the real portal's answer; "redirect" and "html" exercise the precautions

    # --- test controls ---
    def fail(self, path: str, *statuses: int) -> None:
        self.failures[path].extend(statuses)

    def expire_sessions(self) -> None:
        self.valid_tokens.clear()

    # --- transport ---
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        if self.failures[path]:
            return httpx.Response(self.failures[path].pop(0), text="Bad Gateway")
        if path == "/login" and request.method == "POST":
            return self._login(request)
        if not self._authorised(request):
            return self._logged_out()
        if path == "/portal/keys":
            return httpx.Response(200, json={"data": {"signingSecret": self.secret}})
        if path == "/portal/export":
            return self._export(request)
        if path == "/portal/dts":
            page = int(request.url.params.get("page", "1"))
            items = self.dts[(page - 1) * 20 : page * 20]
            return httpx.Response(200, json={"data": items, "total": len(self.dts), "page": page, "pageSize": 20})
        match = re.fullmatch(r"/portal/meters/([^/]+)/energy", path)
        if match:
            rows = self.energy.get(match.group(1))
            if rows is None:
                return httpx.Response(404, json={"error": "not_found", "message": "Meter not found"})
            return httpx.Response(200, json={"data": rows})
        return httpx.Response(404, text="<!doctype html><title>Not found</title>", headers={"content-type": "text/html"})

    def _login(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("origin") != PORTAL_URL:
            return httpx.Response(403, text="Cross-site POST form submissions are forbidden")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("email") != USERNAME or form.get("password") != PASSWORD:
            # The real answer: a 200 whose ActionResult says "failure", with `data` as a devalue-encoded string.
            data = json.dumps([{"email": 1, "error": 2}, form.get("email", ""), "Invalid email or password."])
            return httpx.Response(200, json={"type": "failure", "status": 401, "data": data})
        self.logins += 1
        token = f"token-{self.logins}"
        self.valid_tokens.add(token)
        cookie = f"{SESSION_COOKIE}={token}; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax"
        return httpx.Response(
            200, json={"type": "redirect", "status": 303, "location": "/meters"}, headers={"set-cookie": cookie}
        )

    def _authorised(self, request: httpx.Request) -> bool:
        cookies = dict(part.strip().split("=", 1) for part in request.headers.get("cookie", "").split(";") if "=" in part)
        return cookies.get(SESSION_COOKIE) in self.valid_tokens

    def _logged_out(self) -> httpx.Response:
        if self.logged_out_style == "401":
            return httpx.Response(401, json={"error": "unauthorized", "message": "A valid session is required."})
        if self.logged_out_style == "html":
            return httpx.Response(200, text="<!doctype html><form>Login</form>", headers={"content-type": "text/html"})
        return httpx.Response(303, headers={"location": "/login"})

    def _export(self, request: httpx.Request) -> httpx.Response:
        query = request.url.query.decode()
        timestamp = request.headers.get("x-timestamp", "")
        expected = sign_request(self.secret, "GET", request.url.path, query, int(timestamp or 0))
        fresh = timestamp.isdigit() and abs(time.time() - int(timestamp)) <= 300
        if not fresh or not hmac.compare_digest(expected, request.headers.get("x-signature", "")):
            return httpx.Response(401, json={"error": "invalid_signature"})
        page = int(request.url.params.get("page", "1"))
        items = self.export_records if page == 1 else []
        return httpx.Response(200, json={"data": items, "total": len(self.export_records)})


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        portal_url=PORTAL_URL,
        portal_username=USERNAME,
        portal_password=SecretStr(PASSWORD),
        portal_retry_backoff_seconds=0,
        warm_cache_on_startup=False,
    )


@pytest.fixture
def api(portal: FakePortal, settings: Settings):
    app = create_app(settings, portal_transport=httpx.MockTransport(portal.handler))
    with TestClient(app) as client:
        yield client
