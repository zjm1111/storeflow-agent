"""Offline, deterministic Week-9 evaluation baseline."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from app.services.decision import make_decision
from app.services.retrieval import _bm25_scores, _local_rerank_score, _tokens, _vector, rrf_fuse_lanes

CASES_PATH = Path(__file__).resolve().parents[2] / "sample_data" / "evaluation_cases.json"


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


def build_frozen_corpus(cases: list[dict]) -> list[dict]:
    """Derive 96 gold passages and 48 stable distractors from checked-in labels."""
    corpus: list[dict] = []
    for case in cases:
        for annotation in case["evidence_annotations"]:
            # Do not copy the full query into a gold passage: doing so would
            # trivially give every lexical retriever a perfect score.
            corpus.append({"doc_id": annotation["evidence_id"], "case_id": case["id"], "dimension": case["dimension"], "gold": True, "text": f"模拟门店资料。来源：{annotation.get('source', '业务记录')}。证据：{annotation.get('claim', annotation.get('text', annotation['evidence_id']))}"})
    topics = ("办公用品盘点", "员工排班", "门店照明维护", "收银设备保养")
    for index in range(48):
        corpus.append({"doc_id": f"sim-distractor-{index + 1:02d}", "case_id": None, "dimension": "distractor", "gold": False, "text": f"模拟无关资料：{topics[index % len(topics)]}，离线检索干扰项 {index + 1}。"})
    if len(corpus) != 144:
        raise AssertionError("frozen corpus must contain 96 gold passages and 48 distractors")
    return corpus


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
    cases, corpus = load_cases(), build_frozen_corpus(load_cases())
    strategies = {"bm25": _rank_bm25, "hash_vector": _rank_vector, "rrf_local_rerank": _rank_hybrid}
    results = {}
    for name, ranker in strategies.items():
        rankings = [(ranker(case["question"], corpus), {item["evidence_id"] for item in case["evidence_annotations"]}, case["dimension"]) for case in cases]
        results[name] = _retrieval_metrics(rankings)
    return {"method": "frozen simulated corpus; deterministic local BM25, hash vector, RRF and local rerank", "corpus": {"questions": 48, "gold_evidence": 96, "distractors": 48, "documents": 144}, "strategies": results}


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
    return {
        "dataset": {"case_count": len(cases), "dimensions": dict(dimensions), "evidence_annotation_count": sum(len(case.get("evidence_annotations", [])) for case in cases), "annotation_version": "storeflow-v1-frozen-simulated"},
        "retrieval": run_retrieval_evaluation(),
        "risk_event_static_fixture": {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "true_positive": matched, "note": "Static fixture consistency only; not a live model metric."},
        "usage": {"token_usage": 0, "estimated_cost_usd": 0.0, "latency_ms": 0, "note": "Offline deterministic baseline; production runs must aggregate task state values."},
        "decision": {"reproducible": decision_a["strategies"] == decision_b["strategies"], "constraint_feasible": decision_a["infeasibility_reason"] is None, "infeasible_constraints_reported": infeasible["infeasibility_reason"] is not None},
    }
