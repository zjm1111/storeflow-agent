"""Run the V2 portfolio acceptance path against the Compose web gateway.

It is deliberately a smoke test, not a production load test.  It verifies the
whole reviewer-visible path without printing secrets or evidence bodies.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from uuid import uuid4
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def request(base: str, path: str, *, method: str = "GET", payload: dict | bytes | None = None, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
    merged = {"Accept": "application/json", **(headers or {})}
    if isinstance(payload, dict): merged["Content-Type"] = "application/json"
    try:
        with urlopen(Request(f"{base}{path}", data=body, headers=merged, method=method), timeout=30) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc


def wait_for_result(base: str, task_id: str, headers: dict[str, str], timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = request(base, f"/tasks/{task_id}/result", headers=headers)
        if result.get("status") in {"completed", "awaiting_review", "approved", "rejected"}:
            return result
        time.sleep(1)
    raise TimeoutError(f"task {task_id} did not complete within {timeout_seconds}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    # Use the Compose-published API port by default.  This keeps verification
    # independent of another local development server that may already occupy
    # the browser port.  Pass http://127.0.0.1:5173/api to verify Nginx too.
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--token", default=os.getenv("STOREFLOW_TOKEN", os.getenv("SUPPLYMIND_TOKEN", "")), help="JWT only when JWT_SECRET is configured")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    ready = request(base, "/ready", headers=headers)
    assert ready["status"] == "ready"
    pdf = (ROOT / "output" / "pdf" / "storeflow_demo_operations_pack.pdf").read_bytes()
    upload = request(base, "/tasks/knowledge/upload", method="POST", payload=pdf, headers={**headers, "Content-Type": "application/pdf", "X-Filename": "storeflow_demo_operations_pack.pdf"})
    print(f"[ok] internal PDF: {'deduplicated' if upload.get('duplicate') else 'uploaded'}")

    run_id = uuid4().hex[:10]
    scope = {"region": "上海", "warehouse": "华东中央仓", "store": "上海浦东门店", "category": "快消饮料", "sku": "瓶装饮料", "time_window": "week"}
    constraints = {"current_inventory": 144, "demand_mean": 96, "lead_time_days": 2, "budget": 1500, "max_replenishment": 200, "target_service_level": 0.92}
    first = request(base, "/tasks", method="POST", payload={"question": "本周末暴雨叠加饮料促销，上海浦东门店当前库存只够 1.5 天，应订多少？", "scope": scope, "constraints": constraints}, headers={**headers, "Idempotency-Key": f"storeflow-demo-risk-{run_id}"})
    result = wait_for_result(base, first["task_id"], headers, args.timeout)
    assert result["status"] == "completed", result.get("errors")
    assert result.get("context_pack", {}).get("items"), "context pack is empty"
    assert result.get("events"), "risk events are empty"
    assert result.get("agent_actions"), "agent action history is empty"
    assert result.get("hybrid_results"), "RRF retrieval funnel is empty"
    assert any(entry.get("provider") == "bailian" and entry.get("mode") == "remote" and entry.get("success", True) for entry in result.get("model_execution", [])), "BaiLian did not complete a remote model call"
    print(f"[ok] task {first['task_id'][:8]}: {len(result['evidence'])} evidence, {len(result['events'])} risk events")

    decision = request(base, f"/tasks/{first['task_id']}/decision", method="POST", headers=headers)
    assert decision["status"] == "awaiting_review"
    assert len(decision.get("strategies", [])) == 3
    approved = request(base, f"/tasks/{first['task_id']}/review?action=approve_and_remember", method="POST", payload={"comment": "StoreFlow demo: approved as a regional purchase draft."}, headers=headers)
    assert approved["status"] == "approved"
    assert approved.get("approved_memory", {}).get("status") == "approved"
    print("[ok] decision reviewed and approved memory persisted")

    second = request(base, "/tasks", method="POST", payload={"question": "同一门店本周末仍有暴雨和饮料促销时，应如何复核订货缓冲？", "scope": scope, "constraints": constraints}, headers={**headers, "Idempotency-Key": f"storeflow-demo-memory-{run_id}"})
    follow_up = wait_for_result(base, second["task_id"], headers, args.timeout)
    assert follow_up["status"] == "completed", follow_up.get("errors")
    assert follow_up.get("recalled_memories"), "approved memory was not recalled"
    print(f"[ok] follow-up task recalled {len(follow_up['recalled_memories'])} approved memory item(s)")
    print("StoreFlow end-to-end acceptance: PASS")


if __name__ == "__main__":
    main()
