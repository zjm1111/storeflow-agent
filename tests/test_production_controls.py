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
