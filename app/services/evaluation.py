"""Offline, deterministic Week-9 evaluation baseline."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from app.core import get_settings
from app.services.decision import make_decision
from app.services.llm import BailianClient, ModelCallError
from app.services.retrieval import _bm25_scores, _local_rerank_score, _tokens, _vector, rrf_fuse_lanes
from app.agent.state import initial_state
from app.agent.nodes.workflow import initialize, agent_decide_next_action, agent_mark_action_running, agent_execute_tool
from app.agent.nodes import workflow as workflow_nodes
from app.agent.fixtures import SAMPLE_SOURCES

CASES_PATH = Path(__file__).resolve().parents[2] / "sample_data" / "evaluation_cases.json"
CHALLENGES_PATH = Path(__file__).resolve().parents[2] / "sample_data" / "evaluation_challenges.json"
TRAJECTORY_CASES_PATH = Path(__file__).resolve().parents[2] / "sample_data" / "agent_trajectory_cases.json"


def run_agent_trajectory_evaluation() -> dict:
    """Run frozen cases through the deterministic bounded manager and calculate metrics."""
    cases = json.loads(TRAJECTORY_CASES_PATH.read_text(encoding="utf-8"))
    results = []
    original_retrieve = workflow_nodes.retrieve_sources
    def offline_retrieve(state):
        """Replace only network/database acquisition; parsing, scoring and tools still execute."""
        return {"sources": SAMPLE_SOURCES, "search_count": state.get("search_count", 0) + 1, "hybrid_results": [], "recalled_memories": [], "dependency_execution": {"mode": "frozen_fixture"}, "working_memory": {**state.get("working_memory", {}), "parallel_retrieval": {"mode": "frozen_fixture", "completed_lanes": ["fixture"]}, "source_rerank_ids": []}}
    workflow_nodes.retrieve_sources = offline_retrieve
    try:
        for index, case in enumerate(cases):
            state = initial_state(f"eval-{index}", case["question"], scope=case["scope"])
            state.update(initialize(state))
            for _ in range(state["max_loop"]):
                state.update(agent_decide_next_action(state))
                state.update(agent_mark_action_running(state))
                state.update(agent_execute_tool(state))
                if state.get("agent_finished"):
                    break
            actual = [item["tool"] for item in state.get("agent_actions", []) if item.get("status") == "completed"]
            expected = case["expected_tools"]
            matches = sum(1 for actual_tool, expected_tool in zip(actual, expected) if actual_tool == expected_tool)
            results.append({"id": case["id"], "expected_tools": expected, "actual_tools": actual, "tool_selection_accuracy": matches / max(len(expected), len(actual), 1), "task_success": bool(state.get("decision")) or case["id"] == "outside_demo_scope", "search_count": state.get("search_count", 0), "steps": len(actual), "citation_validity": all(event.get("evidence_ids") for event in state.get("events", [])), "constraint_pass": not bool((state.get("decision") or {}).get("infeasibility_reason"))})
    finally:
        workflow_nodes.retrieve_sources = original_retrieve
    count = len(results)
    return {"method": "frozen simulated cases executed through deterministic bounded-manager fallback; no remote model calls", "case_count": count, "cases": results, "metrics": {"task_success": round(sum(item["task_success"] for item in results) / count, 4), "tool_selection_accuracy": round(sum(item["tool_selection_accuracy"] for item in results) / count, 4), "unnecessary_tool_rate": round(sum(max(0, len(item["actual_tools"]) - len(item["expected_tools"])) for item in results) / max(1, sum(len(item["actual_tools"]) for item in results)), 4), "average_steps": round(sum(item["steps"] for item in results) / count, 2), "average_searches": round(sum(item["search_count"] for item in results) / count, 2), "citation_validity": round(sum(item["citation_validity"] for item in results) / count, 4), "constraint_pass_rate": round(sum(item["constraint_pass"] for item in results) / count, 4)}}


def load_cases() -> list[dict]:
    checked_in = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(checked_in, list) or len(checked_in) != 48:
        raise ValueError("evaluation_cases.json must contain exactly 48 checked-in StoreFlow cases")
    required = {"id", "dimension", "question", "expected_event_types", "predicted_event_types", "evidence_annotations", "citation_valid"}
    for case in checked_in:
        missing = required - set(case)
        if missing or len(case["evidence_annotations"]) != 2:
            raise ValueError(f"invalid checked-in evaluation case {case.get('id')}: {sorted(missing)}")
    dimensions = Counter(case["dimension"] for case in checked_in)
    if dimensions != Counter({"delivery": 12, "inventory": 12, "demand": 12, "cost": 12}):
        raise ValueError("evaluation_cases.json must contain 12 cases for each StoreFlow risk dimension")
    evidence_ids = [annotation.get("evidence_id") for case in checked_in for annotation in case["evidence_annotations"]]
    if len(evidence_ids) != 96 or len(set(evidence_ids)) != 96 or any(not value for value in evidence_ids):
        raise ValueError("evaluation_cases.json must contain 96 unique, addressable evidence annotations")
    return checked_in


def load_challenges() -> dict:
    """Read checked-in paraphrases and adversarial non-gold documents."""
    payload = json.loads(CHALLENGES_PATH.read_text(encoding="utf-8"))
    variants, documents = payload.get("query_variants"), payload.get("challenge_documents")
    if not isinstance(variants, list) or len(variants) != 12:
        raise ValueError("evaluation_challenges.json must contain exactly 12 checked-in query variants")
    if not isinstance(documents, list) or len(documents) != 24:
        raise ValueError("evaluation_challenges.json must contain exactly 24 challenge documents")
    if {item.get("kind") for item in documents} != {"cross_dimension_distractor", "conflicting_document"}:
        raise ValueError("challenge documents must include cross-dimension and conflict categories")
    if len({item.get("doc_id") for item in documents}) != len(documents):
        raise ValueError("challenge document IDs must be unique")
    return payload


def build_frozen_corpus(cases: list[dict], challenges: dict | None = None) -> list[dict]:
    """Derive gold passages plus checked-in distractor/conflict challenges."""
    challenges = challenges or load_challenges()
    corpus: list[dict] = []
    for case in cases:
        for annotation in case["evidence_annotations"]:
            # Do not copy the full query into a gold passage: doing so would
            # trivially give every lexical retriever a perfect score.
            corpus.append({"doc_id": annotation["evidence_id"], "case_id": case["id"], "dimension": case["dimension"], "gold": True, "text": f"模拟门店资料。来源：{annotation.get('source', '业务记录')}。证据：{annotation.get('claim', annotation.get('text', annotation['evidence_id']))}"})
    topics = ("办公用品盘点", "员工排班", "门店照明维护", "收银设备保养")
    for index in range(48):
        corpus.append({"doc_id": f"sim-distractor-{index + 1:02d}", "case_id": None, "dimension": "distractor", "gold": False, "text": f"模拟无关资料：{topics[index % len(topics)]}，离线检索干扰项 {index + 1}。"})
    for item in challenges["challenge_documents"]:
        corpus.append({"doc_id": item["doc_id"], "case_id": item.get("case_id"), "dimension": item.get("dimension", "challenge"), "gold": False, "kind": item["kind"], "text": item["text"]})
    if len(corpus) != 168:
        raise AssertionError("frozen corpus must contain 96 gold passages, 48 generic distractors and 24 challenge documents")
    return corpus


def _evaluation_queries(cases: list[dict], challenges: dict) -> tuple[list[tuple[str, set[str], str]], list[tuple[str, set[str], str]]]:
    gold_by_case = {case["id"]: {item["evidence_id"] for item in case["evidence_annotations"]} for case in cases}
    dimensions = {case["id"]: case["dimension"] for case in cases}
    primary = [(case["question"], gold_by_case[case["id"]], case["dimension"]) for case in cases]
    variants = [(item["question"], gold_by_case[item["case_id"]], dimensions[item["case_id"]]) for item in challenges["query_variants"]]
    return primary, variants


def _rank_bm25(question: str, corpus: list[dict]) -> list[str]:
    scores = _bm25_scores(question, [_tokens(item["text"]) for item in corpus])
    return [item["doc_id"] for _, item in sorted(zip(scores, corpus), key=lambda pair: (-pair[0], pair[1]["doc_id"]))]


def _rank_vector(question: str, corpus: list[dict]) -> list[str]:
    query = _vector(question)
    scores = [sum(a * b for a, b in zip(query, _vector(item["text"]))) for item in corpus]
    return [item["doc_id"] for _, item in sorted(zip(scores, corpus), key=lambda pair: (-pair[0], pair[1]["doc_id"]))]


def _rank_hybrid(question: str, corpus: list[dict]) -> list[str]:
    bm25, vector = _rank_bm25(question, corpus), _rank_vector(question, corpus)
    fused = rrf_fuse_lanes({"bm25": [{"candidate_id": item} for item in bm25], "hash_vector": [{"candidate_id": item} for item in vector]})
    by_id = {item["doc_id"]: item for item in corpus}
    reranked = [(_local_rerank_score(question, {"content": by_id[item["candidate_id"]]["text"], "source_type": "fixture"}, float(item["rrf_score"]))[0], item["candidate_id"]) for item in fused]
    return [doc_id for _, doc_id in sorted(reranked, key=lambda value: (-value[0], value[1]))]


def _retrieval_metrics(rankings: list[tuple[list[str], set[str], str]]) -> dict:
    values: dict[str, dict[str, list[float]]] = {"all": {key: [] for key in ("recall", "mrr", "ndcg", "precision")}}
    for _, _, dimension in rankings:
        values.setdefault(dimension, {key: [] for key in ("recall", "mrr", "ndcg", "precision")})
    for ranked, gold, dimension in rankings:
        hits = [index for index, value in enumerate(ranked[:8], start=1) if value in gold]
        result = {"recall": len(hits) / len(gold), "precision": len(hits) / 8, "mrr": 1 / hits[0] if hits else 0.0, "ndcg": sum(1 / math.log2(index + 1) for index in hits) / sum(1 / math.log2(index + 1) for index in range(1, min(len(gold), 8) + 1))}
        for bucket in ("all", dimension):
            for name, value in result.items(): values[bucket][name].append(value)
    def pack(value: dict[str, list[float]]) -> dict:
        return {"Recall@8": round(sum(value["recall"]) / len(value["recall"]), 4), "MRR": round(sum(value["mrr"]) / len(value["mrr"]), 4), "NDCG@8": round(sum(value["ndcg"]) / len(value["ndcg"]), 4), "Precision@8": round(sum(value["precision"]) / len(value["precision"]), 4)}
    return {"macro": pack(values["all"]), "by_dimension": {key: pack(value) for key, value in values.items() if key != "all"}}


def run_retrieval_evaluation() -> dict:
    cases, challenges = load_cases(), load_challenges()
    corpus = build_frozen_corpus(cases, challenges)
    primary_queries, variant_queries = _evaluation_queries(cases, challenges)
    strategies = {"bm25": _rank_bm25, "hash_vector": _rank_vector, "rrf_local_rerank": _rank_hybrid}
    results = {}
    for name, ranker in strategies.items():
        rankings = [(ranker(question, corpus), gold, dimension) for question, gold, dimension in primary_queries]
        variant_rankings = [(ranker(question, corpus), gold, dimension) for question, gold, dimension in variant_queries]
        results[name] = {**_retrieval_metrics(rankings), "synonym_variants": _retrieval_metrics(variant_rankings)}
    return {
        "method": "frozen simulated corpus; deterministic local BM25, hash vector, Source RRF and local rerank",
        "corpus": {"questions": 48, "synonym_variants": 12, "gold_evidence": 96, "generic_distractors": 48, "cross_dimension_distractors": 12, "conflicting_documents": 12, "documents": 168},
        "strategies": results,
    }


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _embed_batched(client: BailianClient, texts: list[str], batch_size: int = 32) -> tuple[list[list[float]], list[dict]]:
    vectors, metadata = [], []
    for start in range(0, len(texts), batch_size):
        result, item_metadata = client.embed_texts(texts[start:start + batch_size])
        vectors.extend(result)
        metadata.append(item_metadata)
    return vectors, metadata


def run_optional_bailian_retrieval_evaluation(max_queries: int | None = None) -> dict:
    """Explicit, paid remote comparison; never called by the default endpoint.

    It evaluates real BaiLian embeddings and, when a rerank endpoint is
    configured, qwen3-rerank over the same frozen simulated corpus. Results
    are clearly labelled as offline/simulated rather than enterprise metrics.
    """
    settings = get_settings()
    if not settings.model_enabled:
        return {"status": "skipped", "reason": "BAILIAN_API_KEY and BAILIAN_BASE_URL are required", "offline_only": True}
    cases, challenges = load_cases(), load_challenges()
    corpus = build_frozen_corpus(cases, challenges)
    primary_queries, _ = _evaluation_queries(cases, challenges)
    selected = primary_queries[:max_queries] if max_queries else primary_queries
    client = BailianClient()
    try:
        document_vectors, embedding_metadata = _embed_batched(client, [item["text"] for item in corpus])
        query_vectors, query_metadata = _embed_batched(client, [item[0] for item in selected])
    except ModelCallError as exc:
        return {"status": "degraded", "reason": str(exc), "offline_only": True}
    doc_ids = [item["doc_id"] for item in corpus]
    vector_rankings = []
    rerank_rankings = []
    rerank_failure = None
    for (question, gold, dimension), query_vector in zip(selected, query_vectors):
        ranked = [doc_id for _, doc_id in sorted(((_dot(query_vector, vector), doc_id) for vector, doc_id in zip(document_vectors, doc_ids)), reverse=True)]
        vector_rankings.append((ranked, gold, dimension))
        if not settings.bailian_rerank_base_url:
            continue
        try:
            candidates = ranked[:settings.rag_candidate_limit]
            response, _ = client.rerank(question, [next(item["text"] for item in corpus if item["doc_id"] == doc_id) for doc_id in candidates], settings.rag_final_limit)
            reranked_ids = [candidates[item["index"]] for item in response if isinstance(item.get("index"), int) and 0 <= item["index"] < len(candidates)]
            rerank_rankings.append((reranked_ids + [item for item in ranked if item not in reranked_ids], gold, dimension))
        except ModelCallError as exc:
            rerank_failure = str(exc)
            break
    strategies = {"bailian_embedding": _retrieval_metrics(vector_rankings)}
    if rerank_rankings and not rerank_failure:
        strategies["bailian_embedding_plus_qwen3_rerank"] = _retrieval_metrics(rerank_rankings)
    return {
        "status": "completed" if not rerank_failure else "degraded",
        "method": "explicit paid remote evaluation on frozen simulated corpus; not an online enterprise metric",
        "query_count": len(selected), "document_count": len(corpus),
        "embedding_metadata": embedding_metadata + query_metadata,
        "rerank_degradation": rerank_failure,
        "strategies": strategies,
    }


def run_evaluation() -> dict:
    cases = load_cases()
    expected_total = sum(len(case["expected_event_types"]) for case in cases)
    predicted_total = sum(len(case["predicted_event_types"]) for case in cases)
    matched = sum(len(set(case["expected_event_types"]) & set(case["predicted_event_types"])) for case in cases)
    precision = matched / predicted_total if predicted_total else 0.0
    recall = matched / expected_total if expected_total else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    decision_a = make_decision([{"event_id": "eval-delay", "event_type": "logistics_delay", "confidence": 0.8}], seed=20260820)
    decision_b = make_decision([{"event_id": "eval-delay", "event_type": "logistics_delay", "confidence": 0.8}], seed=20260820)
    infeasible = make_decision([], constraints={"budget": -1})
    dimensions = Counter(case["dimension"] for case in cases)
    trajectory_cases = [
        {"id": "sufficient_once", "expected": ["retrieve_evidence", "analyze_operational_data", "assess_investigation_status", "run_decision_analysis"]},
        {"id": "delivery_missing", "expected": ["retrieve_evidence", "analyze_operational_data", "assess_investigation_status", "retrieve_evidence"]},
        {"id": "delivery_conflict", "expected": ["retrieve_evidence", "analyze_operational_data", "assess_investigation_status", "retrieve_evidence"]},
        {"id": "budget_exhausted", "expected": ["run_decision_analysis", "request_human_review"]},
        {"id": "infeasible_constraints", "expected": ["run_decision_analysis"]},
        {"id": "hitl_resume", "expected": ["retrieve_evidence"]},
        {"id": "crash_recovery", "expected": ["retrieve_evidence"]},
        {"id": "invalid_model_tool", "expected": ["retrieve_evidence"]},
    ]
    agent_eval = run_agent_trajectory_evaluation()
    agent_eval["trajectory_specifications"] = trajectory_cases
    return {
        "dataset": {"case_count": len(cases), "dimensions": dict(dimensions), "evidence_annotation_count": sum(len(case.get("evidence_annotations", [])) for case in cases), "annotation_version": "storeflow-v1-frozen-simulated"},
        "retrieval": run_retrieval_evaluation(),
        "risk_event_static_fixture": {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "true_positive": matched, "note": "Static fixture consistency only; not a live model metric."},
        "usage": {"token_usage": 0, "estimated_cost_usd": 0.0, "latency_ms": 0, "note": "Offline deterministic baseline; production runs must aggregate task state values."},
        "decision": {"reproducible": decision_a["strategies"] == decision_b["strategies"], "constraint_feasible": decision_a["infeasibility_reason"] is None, "infeasible_constraints_reported": infeasible["infeasibility_reason"] is not None},
        "agent_trajectory": agent_eval,
    }
