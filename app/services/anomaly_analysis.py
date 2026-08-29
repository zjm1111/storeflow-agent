"""Deterministic operational analysis used by the bounded investigation agent."""
from __future__ import annotations

import json
from pathlib import Path

from app.agent.schemas import AnalysisResult

_DATASET = Path(__file__).resolve().parents[2] / "sample_data" / "operational_metrics.json"


class OperationalDataAnalyzer:
    DEMO_SCOPE = {"region": "上海", "store": "上海浦东门店", "sku": "瓶装饮料"}

    def analyze(self, *, scope: dict | None = None) -> dict:
        payload = json.loads(_DATASET.read_text(encoding="utf-8"))
        scope = scope or self.DEMO_SCOPE
        mismatches = {key: {"expected": value, "received": scope.get(key)} for key, value in self.DEMO_SCOPE.items() if scope.get(key) != value}
        if mismatches:
            return {"dataset": payload["dataset"], "description": payload["description"], "available": False, "scope": self.DEMO_SCOPE, "mismatches": mismatches, "series": [], "results": []}
        rows = payload["rows"]
        baseline_rows, current_rows = rows[-14:-3], rows[-3:]
        baseline_sales = sum(row["sales"] for row in baseline_rows) / len(baseline_rows)
        current_sales = sum(row["sales"] for row in current_rows) / len(current_rows)
        baseline_lead = sum(row["lead_time"] for row in baseline_rows) / len(baseline_rows)
        current_lead = sum(row["lead_time"] for row in current_rows) / len(current_rows)
        current_inventory = current_rows[-1]["inventory"]
        baseline_inventory = baseline_rows[0]["inventory"]
        dos = current_inventory / current_sales
        baseline_dos = baseline_inventory / baseline_sales
        promotion_rows = [row for row in rows if row["promotion_flag"]]
        promotion_sales = sum(row["sales"] for row in promotion_rows) / len(promotion_rows)
        results = [
            AnalysisResult(analysis_id="AN-demand", metric="demand", baseline=round(baseline_sales, 1), current=round(current_sales, 1), change_ratio=round(current_sales / baseline_sales - 1, 3), anomaly=current_sales / baseline_sales >= 1.2, severity="high", summary=f"近三日日销量较基线提升 {current_sales / baseline_sales - 1:.1%}。"),
            AnalysisResult(analysis_id="AN-inventory", metric="inventory", baseline=round(baseline_dos, 1), current=round(dos, 1), change_ratio=round(dos / baseline_dos - 1, 3), anomaly=dos < 2, severity="high", summary=f"当前库存覆盖 {dos:.1f} 天，低于常态 {baseline_dos:.1f} 天。"),
            AnalysisResult(analysis_id="AN-delivery", metric="delivery", baseline=round(baseline_lead, 1), current=round(current_lead, 1), change_ratio=round(current_lead / baseline_lead - 1, 3), anomaly=current_lead / baseline_lead >= 1.2, severity="high", summary=f"预计提前期较基线延长 {current_lead / baseline_lead - 1:.1%}。"),
            AnalysisResult(analysis_id="AN-promotion", metric="promotion", baseline=round(baseline_sales, 1), current=round(promotion_sales, 1), change_ratio=round(promotion_sales / baseline_sales - 1, 3), anomaly=promotion_sales / baseline_sales >= 1.15, severity="medium", summary=f"促销窗口销量较基线提升 {promotion_sales / baseline_sales - 1:.1%}。"),
        ]
        return {"dataset": payload["dataset"], "description": payload["description"], "available": True, "scope": self.DEMO_SCOPE, "series": rows, "results": [result.model_dump() for result in results]}
