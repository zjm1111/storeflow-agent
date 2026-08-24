from fastapi.testclient import TestClient

from app.main import app


def test_create_task_uses_eager_celery_worker_and_reaches_review():
    """The API scheduler uses the production task entrypoint in test mode."""
    with TestClient(app) as client:
        created = client.post("/tasks", json={"question": "暴雨叠加促销时，门店饮料应订多少？"})
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        result = client.get(f"/tasks/{task_id}/result")

    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "awaiting_review"
    assert body["decision"] is not None
    assert any(action["tool"] == "request_human_review" for action in body["agent_actions"])
