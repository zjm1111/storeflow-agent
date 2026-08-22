import os

os.environ["MYSQL_URL"] = "sqlite://"

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_readiness_expose_operational_probes():
    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")
    assert health.status_code == 200
    assert ready.json()["status"] == "ready"
    assert health.headers["X-Request-ID"]


def test_protected_routes_require_configured_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "production-test-key")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            denied = client.get("/tasks/evaluations/run")
            allowed = client.get("/tasks/evaluations/run", headers={"X-API-Key": "production-test-key"})
        assert denied.status_code == 401
        assert allowed.status_code == 200
    finally:
        monkeypatch.delenv("API_KEY", raising=False)
        get_settings.cache_clear()
