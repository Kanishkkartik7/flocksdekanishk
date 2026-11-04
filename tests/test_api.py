"""End-to-end tests of the HTTP API against the fake portal (no network)."""

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app


def test_list_meters_with_filters(api):
    body = api.get("/meters", params={"status": "decommissioned"}).json()
    assert body["total"] >= 1
    assert all(m["status"] == "decommissioned" for m in body["items"])
    assert body["items"][0]["network"]["dt"]["code"]


def test_pagination(api):
    first = api.get("/meters", params={"limit": 2, "offset": 0}).json()
    second = api.get("/meters", params={"limit": 2, "offset": 2}).json()
    assert len(first["items"]) == 2 and first["total"] == second["total"]
    assert first["items"][0]["meter_id"] != second["items"][0]["meter_id"]


def test_get_meter_is_case_insensitive(api):
    response = api.get("/meters/j100000")
    assert response.status_code == 200
    assert response.json()["meter_id"] == "J100000"


def test_unknown_meter_is_404_without_asking_the_portal(api, portal):
    response = api.get("/meters/J999999/readings")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert portal.calls["/portal/meters/J999999/energy"] == 0


def test_readings_and_consumption(api):
    readings = api.get("/meters/J100000/readings").json()
    assert readings["count"] == 8
    assert readings["readings"][0] == {
        "timestamp": "2026-06-23T23:30:00+05:30",
        "energy_kwh": 48438.74,
        "apparent_energy_kvah": 52313.84,
        "voltage_r_v": 226.0,
    }
    usage = api.get("/meters/J100000/consumption").json()
    assert len(usage["intervals"]) == 7
    assert usage["intervals"][0]["energy_kwh"] == 0.42

    window = api.get("/meters/J100000/readings", params={"start": "2026-06-24T00:00:00", "end": "2026-06-24T01:00:00"})
    assert [r["timestamp"][11:16] for r in window.json()["readings"]] == ["00:00", "00:30", "01:00"]


def test_readings_are_cached(api, portal):
    api.get("/meters/J100000/readings")
    api.get("/meters/J100000/consumption")
    assert portal.calls["/portal/meters/J100000/energy"] == 1


def test_export_is_signed_and_metadata_cached(api, portal):
    api.get("/meters")
    api.get("/network")
    assert portal.calls["/portal/export"] == 1  # the fake rejects bad signatures with 401
    assert portal.calls["/portal/keys"] == 1


def test_network_and_transformers(api):
    network = api.get("/network").json()
    assert network["levels"][0] == "zone" and network["levels"][-1] == "dt"
    assert any(i["kind"] == "conflicting_names" and i["code"] == "DT-007" for i in network["issues"])
    transformers = api.get("/transformers").json()
    dt001 = next(t for t in transformers["items"] if t["code"] == "DT-001")
    assert dt001["meter_count"] >= 1 and dt001["capacity_kva"]


def test_near_search(api):
    meter = api.get("/meters/J100000").json()
    loc = meter["location"]
    body = api.get("/meters", params={"near_lat": loc["latitude"], "near_lng": loc["longitude"], "radius_km": 0.01}).json()
    assert body["items"][0]["meter_id"] == "J100000"
    assert body["items"][0]["distance_km"] == 0


def test_transient_502s_are_retried(api, portal):
    portal.fail("/portal/export", 502, 502)
    assert api.get("/meters").status_code == 200
    assert portal.calls["/portal/export"] == 3


@pytest.mark.parametrize("logged_out_style", ["401", "redirect", "html"])
def test_expired_session_triggers_one_relogin(api, portal, logged_out_style):
    # The real portal answers 401 (observed). A redirect or an HTML page are handled too, as a precaution.
    assert api.get("/meters").status_code == 200
    assert portal.logins == 1
    portal.logged_out_style = logged_out_style
    portal.expire_sessions()
    assert api.get("/meters/J100000/consumption").status_code == 200
    assert portal.logins == 2


def test_portal_down_gives_a_clean_502_and_degraded_health(api, portal):
    portal.fail("/login", 502, 502, 502)
    response = api.get("/meters")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "portal_unavailable"
    health = api.get("/health").json()
    assert health["status"] == "degraded" and health["metadata"]["loaded"] is False


def test_a_failed_first_load_is_not_retried_on_every_request(api, portal):
    portal.fail("/login", 502, 502, 502)
    assert api.get("/meters").status_code == 502
    second = api.get("/meters/J100000")
    assert second.status_code == 502
    assert second.json()["error"]["code"] == "portal_unavailable"  # the remembered error, same code as the first
    assert portal.calls["/login"] == 3  # only the first request's three attempts
    api.app.state.service._last_failure_at -= 60  # pretend the 30 s cooldown has passed
    assert api.get("/meters").status_code == 200


def test_stale_data_is_served_when_refresh_fails(api, portal):
    assert api.get("/meters").status_code == 200
    service = api.app.state.service
    service._snapshot.loaded_at -= 10_000  # pretend the cache expired
    portal.fail("/portal/export", 502, 502, 502)
    assert api.get("/meters").status_code == 200  # served from the stale snapshot
    assert api.get("/health").json()["status"] == "degraded"


def test_wrong_credentials_are_reported_and_not_retried(portal, settings):
    wrong = settings.model_copy(update={"portal_password": SecretStr("wrong")})
    with TestClient(create_app(wrong, portal_transport=httpx.MockTransport(portal.handler))) as client:
        response = client.get("/meters")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "portal_auth_failed"
    assert portal.calls["/login"] == 1  # retrying a wrong password could only lock the account


def test_validation_errors_use_the_error_envelope(api):
    response = api.get("/meters", params={"limit": 0})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    lone_lat = api.get("/meters", params={"near_lat": 26.9})
    assert lone_lat.status_code == 422


def test_unknown_route_uses_the_error_envelope(api):
    response = api.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
