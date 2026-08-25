import os

os.environ["MYSQL_URL"] = "sqlite://"

from app.services.context import build_context_telemetry, build_evidence_context_pack, build_risk_context, context_budget_policy, estimate_tokens, truncate_to_token_budget


def test_evidence_context_pack_keeps_citations_diversity_and_top_eight():
    evidence = [
        {
            "evidence_id": f"ev-{index}", "source_id": f"source-{index % 10}",
            "event_id": f"event-{index % 3}", "quote": f"Evidence {index} about delivery delay. " * 30,
            "overall_score": 1 - index / 100,
        }
        for index in range(12)
    ]
    pack = build_evidence_context_pack(evidence, budget_tokens=10_000)
    assert pack["kind"] == "current_evidence"
    assert len(pack["items"]) == 8
    assert all(item["summary"].startswith(f"[证据: {item['evidence_id']}]") for item in pack["items"])
    assert pack["selection"]["source_count"] >= 8
    assert pack["used_tokens"] <= pack["budget_tokens"]


def test_evidence_context_pack_respects_hard_token_budget():
    evidence = [{"evidence_id": "ev-1", "source_id": "s-1", "quote": "x" * 500, "overall_score": 1}]
    pack = build_evidence_context_pack(evidence, budget_tokens=10)
    assert pack["items"] == []
    assert pack["used_tokens"] == 0


def test_shared_token_estimator_is_conservative_for_chinese_and_truncates_to_budget():
    chinese = "暴雨导致中央仓配送延迟，门店需要提高安全库存。"
    english = "Heavy rain delays central warehouse delivery."
    assert estimate_tokens(chinese) > len(chinese) // 4
    assert estimate_tokens(english) >= len(english) // 5
    truncated = truncate_to_token_budget(chinese * 5, 16)
    assert truncated.endswith("…")
    assert estimate_tokens(truncated) <= 16


def test_context_budget_reserves_memory_working_state_and_output_before_evidence():
    settings = type("Settings", (), {
        "model_context_token_budget": 5000,
        "system_context_token_budget": 600,
        "working_state_context_token_budget": 700,
        "memory_context_token_budget": 900,
        "model_output_reserve_tokens": 1000,
        "evidence_context_token_budget": 4000,
    })()
    policy = context_budget_policy(settings)
    assert policy["evidence_budget"] == 1800
    assert policy["allocated_tokens"] <= policy["model_context_budget"]
    assert policy["evidence_budget_clamped"] is True


def test_context_telemetry_tracks_selection_drop_and_fact_boundary():
    state = {
        "evidence": [{"evidence_id": f"ev-{index}"} for index in range(12)],
        "evidence_context_pack": {
            "selection": {"candidate_count": 12},
            "items": [{"evidence_id": f"ev-{index}", "source_id": f"s-{index}", "summary": f"[证据: ev-{index}] 当前证据"} for index in range(8)],
        },
        "recalled_memories": [{"memory_id": "m-1", "content": "历史经验", "summary": "历史"}],
        "question": "测试", "scope": {},
    }
    projection = build_risk_context(state)
    metric = build_context_telemetry(state, projection, system_prompt="system", mode="remote")
    assert metric["candidate_evidence"] == 12
    assert metric["selected_evidence"] == 8
    assert metric["dropped_evidence"] == 4
    assert metric["memory_is_current_evidence"] is False
    assert metric["evidence_within_budget"] is True
