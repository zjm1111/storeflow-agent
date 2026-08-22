import os

os.environ["MYSQL_URL"] = "sqlite://"

from fastapi.testclient import TestClient

from app.api.tasks import service
from app.agent.nodes.workflow import retrieve_sources
from app.agent.state import initial_state
from app.main import app
from app.services import retrieval
from app.services.decision import make_decision


def test_no_key_state_machine_uses_fixture_and_completes():
    with TestClient(app) as client:
        created = client.post("/tasks", json={"question": "How could heavy rain delay online grocery delivery?"})
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        body = client.get(f"/tasks/{task_id}/result").json()
    assert body["status"] == "completed"
    assert body["sources"]
    completed_nodes = [entry["node"] for entry in body["trace"] if entry["status"] == "completed"]
    assert {"initialize", "plan_research", "assess_coverage", "complete"}.issubset(completed_nodes)


def test_sse_history_contains_every_node_transition():
    with TestClient(app) as client:
        response = client.post("/tasks", json={"question": "Fixture-only demo of last-mile delivery delay"})
        task_id = response.json()["task_id"]
    events = service.events.history(task_id)
    trace_events = [event["payload"] for event in events if event["type"] == "trace"]
    transitions = [(event["node"], event["status"]) for event in trace_events]
    assert ("initialize", "started") in transitions
    assert ("assess_coverage", "completed") in transitions
    assert transitions[-1] == ("complete", "completed")


def test_schema_failure_is_recorded_and_repaired_with_valid_plan():
    with TestClient(app) as client:
        response = client.post("/tasks", json={"question": "[schema-error] last-mile delivery scenario"})
        task_id = response.json()["task_id"]
        result = client.get(f"/tasks/{task_id}/result").json()
    assert result["status"] == "completed"
    assert result["plan"]["sub_questions"] == ["Identify supported store inventory, demand and delivery risks"]
    assert any("schema repair" in error for error in result["errors"])
    assert any(item["node"] == "plan_research" and item["status"] == "error" for item in result["trace"])


def test_pdf_ingestion_marks_a_second_identical_upload_as_duplicate(monkeypatch):
    class Reader:
        pages = [type("Page", (), {"extract_text": lambda self: "Rain delays rider delivery and raises refund cost."})()]

    class FakeRetriever(retrieval.HybridRetriever):
        def __init__(self):
            self.points = set()

        def _ensure_collection(self):
            pass

        def _qdrant(self, path, body=None, method="POST"):
            if method == "GET" and path.startswith("/collections/supplymind_knowledge/points/"):
                point_id = path.rsplit("/", 1)[-1]
                if point_id not in self.points:
                    raise RuntimeError("point not found")
                return {"result": {"id": point_id}}
            if method == "PUT":
                self.points.add(str(body["points"][0]["id"]))
                return {"result": {"status": "completed"}}
            raise AssertionError(f"Unexpected Qdrant request: {method} {path}")

    monkeypatch.setattr(retrieval, "PdfReader", lambda _: Reader())
    client = FakeRetriever()
    first = client.ingest_pdf("risk.pdf", b"%PDF-1.7 same file")
    second = client.ingest_pdf("renamed-risk.pdf", b"%PDF-1.7 same file")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["source_id"] == first["source_id"]


def test_retrieve_sources_includes_matching_internal_knowledge(monkeypatch):
    class FakeRetriever:
        def retrieve_knowledge(self, query):
            source = {"source_id": "upload-123", "title": "internal.pdf", "url": "https://local.supplymind/uploads/upload-123", "content": "Heavy rain delays delivery and adds refund cost.", "retrieved_at": "2026-08-20T00:00:00+00:00", "source_type": "internal", "content_hash": "abc"}
            return [source], [{"source_id": "upload-123", "title": "internal.pdf", "url": source["url"], "bm25_score": 1.0, "vector_score": 1.0, "rerank_score": 1.0}]

        def retrieve(self, query):
            return [], [], ["external search unavailable"]

    monkeypatch.setattr("app.agent.nodes.workflow.HybridRetriever", FakeRetriever)
    state = initial_state("task-1", "How does heavy rain delay delivery?")
    result = retrieve_sources(state)
    assert result["sources"][0]["source_id"] == "upload-123"
    assert result["hybrid_results"][0]["source_id"] == "upload-123"


def test_low_value_search_pages_are_not_accepted_as_risk_sources():
    assert retrieval._is_low_value_source("https://dictionary.cambridge.org/dictionary/english/delay")
    assert retrieval._is_low_value_source("https://github.com/example/project")
    assert retrieval._is_low_value_source("https://www.walmart.com/shop/deals/flash-deals")
    assert not retrieval._is_low_value_source("https://www.weather.gov/safety/flood")


def test_week7_decision_is_reproducible_and_returns_three_strategies():
    events = [{"event_id": "risk-rain", "event_type": "logistics_delay", "confidence": 0.8}]
    first = make_decision(events, seed=7, samples=1000)
    second = make_decision(events, seed=7, samples=1000)
    assert first["strategies"] == second["strategies"]
    strategies = [item["strategy"] for item in first["strategies"]]
    assert strategies == ["正常订货", "适度加订", "高保障加订"]
    assert [item["replenishment_quantity"] for item in first["strategies"]] == sorted(item["replenishment_quantity"] for item in first["strategies"])
    if first["infeasibility_reason"] is None:
        assert first["recommended_strategy"] in strategies
    else:
        assert first["recommended_strategy"] is None
        assert first["feasibility_summary"]["blocking_constraints"]


def _reviewable_task(client: TestClient) -> str:
    created = client.post("/tasks", json={"question": "How could heavy rain delay online grocery delivery?"})
    task_id = created.json()["task_id"]
    decision = client.post(f"/tasks/{task_id}/decision")
    assert decision.status_code == 200
    assert decision.json()["status"] == "awaiting_review"
    return task_id


def test_week8_approve_generates_final_report_and_audit_record():
    with TestClient(app) as client:
        task_id = _reviewable_task(client)
        response = client.post(f"/tasks/{task_id}/review/approve", json={"comment": "Approved after KPI review."})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert "已批准" in body["final_report"]["markdown"]
    assert body["audit_trail"][-1]["action"] == "approve"


def test_week8_modify_constraints_reoptimizes_and_remains_awaiting_review():
    with TestClient(app) as client:
        task_id = _reviewable_task(client)
        response = client.post(f"/tasks/{task_id}/review/modify-constraints", json={"comment": "Cap spend.", "constraints": {"budget": 500, "max_replenishment": 30}})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_review"
    assert body["decision"]["applied_constraints"]["budget"] == 500
    assert body["audit_trail"][-1]["action"] == "modify_constraints"


def test_week8_need_more_evidence_replans_and_preserves_audit_trail():
    with TestClient(app) as client:
        task_id = _reviewable_task(client)
        response = client.post(f"/tasks/{task_id}/review/need-more-evidence", json={"comment": "Need warehouse capacity evidence."})
        result = client.get(f"/tasks/{task_id}/result").json()
    assert response.status_code == 200
    assert any(item["action"] == "need_more_evidence" for item in result["audit_trail"])
    assert any(item["node"] == "plan_research" for item in result["trace"])
    assert result["status"] == "completed"


def test_week8_reject_terminates_and_illegal_transition_is_rejected():
    with TestClient(app) as client:
        task_id = _reviewable_task(client)
        rejected = client.post(f"/tasks/{task_id}/review/reject", json={"comment": "Risk too high."})
        illegal = client.post(f"/tasks/{task_id}/review/approve", json={"comment": "Too late."})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert illegal.status_code == 409
