"""API smoke + behaviour tests through FastAPI's TestClient (every dashboard endpoint)."""
import pytest
from fastapi.testclient import TestClient

from studio.api import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/app.css", "/api/overview", "/api/status", "/api/streams",
                                  "/api/streamers", "/api/rights", "/api/monitor/sessions", "/api/candidates", "/api/projects",
                                  "/api/presets", "/api/automation", "/api/costs", "/api/analytics", "/api/calendar",
                                  "/api/notifications", "/api/jobs", "/api/audit", "/api/growth"])
def test_get_endpoints(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.text[:300])


def test_watchlist_add_never_records(client):
    r = client.post("/api/streamers", json={"platform": "twitch", "ref": "https://www.twitch.tv/somestreamer", "priority": 2})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert not r.json()["active_monitoring"]
    assert client.patch(f"/api/streamers/{sid}", json={"custom_keywords": ["leonida drift"]}).json()["custom_keywords"] == ["leonida drift"]


def test_rights_validation(client):
    r = client.post("/api/rights", json={"source_url": "https://x", "state": "AUTHORIZED_FOR_CLIPPING"})
    assert r.status_code == 400 and "evidence" in r.json()["detail"]
    r = client.post("/api/rights", json={"source_url": "https://x", "state": "AUTHORIZED_FOR_CLIPPING", "evidence": "https://terms",
                                         "recording_allowed": True, "editing_allowed": True})
    assert r.status_code == 200
    assert client.post("/api/rights", json={"state": "OWNED"}).status_code == 400


def test_automation_validation(client):
    assert client.put("/api/automation", json={"nope": 1}).status_code == 400
    assert client.put("/api/automation", json={"spike_sensitivity": 4.0}).json()["spike_sensitivity"] == 4.0
    client.put("/api/automation", json={"spike_sensitivity": 3.5})


def test_discovery_reports_not_configured(client):
    r = client.post("/api/discovery/run", json={}).json()
    assert r["youtube"]["status"] == "NOT_CONFIGURED" and r["twitch"]["status"] == "NOT_CONFIGURED"


def test_connection_status_is_real(client):
    conns = {c["platform"]: c["status"] for c in client.get("/api/status").json()["connections"]}
    assert conns["YouTube Data API key"] == "NOT_CONFIGURED"
    assert conns["Anthropic (Claude)"] == "NOT_CONFIGURED"


def test_media_route_blocks_path_traversal(client):
    assert client.get("/media/../../RULES.md").status_code == 404


def test_api_token_guard(client, monkeypatch):
    from studio.config import settings
    monkeypatch.setitem(settings.env, "STUDIO_API_TOKEN", "secret")
    assert client.put("/api/automation", json={"spike_sensitivity": 3.5}).status_code == 401
    assert client.put("/api/automation", json={"spike_sensitivity": 3.5}, headers={"X-Studio-Token": "secret"}).status_code == 200
