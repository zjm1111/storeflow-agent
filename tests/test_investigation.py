from app.agent.nodes.workflow import _targeted_query, assess_investigation_status
from app.agent.state import initial_state
from app.services.anomaly_analysis import OperationalDataAnalyzer
from app.services.evaluation import run_agent_trajectory_evaluation


def test_operational_analysis_is_deterministic_and_flags_demo_anomalies():
    first = OperationalDataAnalyzer().analyze()
    second = OperationalDataAnalyzer().analyze()
    assert first == second
    assert first["dataset"] == "simulated_retail_operational_dataset"
    assert {item["metric"] for item in first["results"]} == {"demand", "inventory", "delivery", "promotion"}
    assert all(item["anomaly"] for item in first["results"][:3])


def test_assessment_exposes_unknown_hypotheses_and_targeted_delivery_query():
    state = initial_state("task-test", "调查门店缺货风险")
    state["sources"] = [{"content": "库存门店销量促销"}]
    state["evidence"] = [{"evidence_id": "ev-delivery", "source_id": "source-1", "quote": "配送延迟与配送正常通知相互矛盾", "conflict_status": "pending_review", "conflict_group": "delivery-status"}]
    state["analysis_snapshot"] = OperationalDataAnalyzer().analyze()
    assessed = assess_investigation_status(state)
    delivery = next(item for item in assessed["hypotheses"] if item["hypothesis_id"] == "delivery")
    assert delivery["status"] == "conflicting"
    assert "中央仓" in _targeted_query({**state, **assessed}, "delivery")
    assert assessed["investigation_status"]["ready_for_decision"] is False


def test_operational_analysis_rejects_non_demo_scope():
    result = OperationalDataAnalyzer().analyze(scope={"region": "北京", "store": "北京朝阳门店", "sku": "瓶装饮料"})
    assert result["available"] is False
    assert result["series"] == []


def test_trajectory_evaluation_calculates_metrics_from_executed_actions():
    report = run_agent_trajectory_evaluation()
    assert report["case_count"] == 2
    assert report["metrics"]["tool_selection_accuracy"] == 0.8
    assert all("actual_tools" in item for item in report["cases"])
