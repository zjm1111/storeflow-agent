"""Traceable, reproducible StoreFlow replenishment decision layer."""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass



@dataclass
class RiskParameters:
    demand_mean: float = 100.0
    demand_stddev: float = 20.0
    current_inventory: int = 80
    lead_time_days: int = 2
    delay_probability: float = 0.05
    extra_delay_days: float = 0.0
    purchase_cost: float = 10.0
    holding_cost: float = 0.5
    stockout_cost: float = 25.0
    expedite_cost: float = 8.0
    budget: float = 2_000.0
    max_replenishment: int = 200
    target_service_level: float = 0.92
    source_event_ids: list[str] | None = None


def parameters_from_events(events: list[dict], constraints: dict | None = None) -> RiskParameters:
    params = RiskParameters(source_event_ids=[event["event_id"] for event in events])
    for event in events:
        confidence = float(event.get("confidence", 0.5))
        if event.get("event_type") == "logistics_delay":
            params.delay_probability = min(0.7, params.delay_probability + 0.25 * confidence)
            params.extra_delay_days = max(params.extra_delay_days, round(1.5 * confidence, 2))
        elif event.get("event_type") == "demand_surge":
            params.demand_mean *= 1 + 0.35 * confidence
            params.demand_stddev *= 1 + 0.5 * confidence
        elif event.get("event_type") == "inventory_shortage":
            # Inventory risk is evidence that the starting position is tighter
            # than the default, never a reason for an LLM to invent stock.
            params.current_inventory = max(0, int(params.current_inventory * (1 - 0.25 * confidence)))
        elif event.get("event_type") == "supply_disruption":
            params.lead_time_days += max(1, round(2 * confidence))
        elif event.get("event_type") == "price_volatility":
            params.purchase_cost *= 1 + 0.2 * confidence
    for name, value in (constraints or {}).items():
        if name in RiskParameters.__dataclass_fields__ and name != "source_event_ids":
            setattr(params, name, value)
    return params


def _evaluate(name: str, quantity: int, params: RiskParameters, seed: int, samples: int) -> dict:
    rng = random.Random(seed)
    stockouts = delays = 0; total_cost = 0.0; scenario_costs: list[float] = []
    for _ in range(samples):
        demand = max(0, rng.gauss(params.demand_mean, params.demand_stddev))
        delayed = rng.random() < params.delay_probability
        available = params.current_inventory + (0 if delayed else quantity)
        shortage = max(0, demand - available)
        leftover = max(0, available - demand)
        stockouts += shortage > 0; delays += delayed
        cost = quantity * params.purchase_cost + leftover * params.holding_cost + shortage * params.stockout_cost + (params.expedite_cost if delayed else 0)
        total_cost += cost; scenario_costs.append(cost)
    stockout_probability = stockouts / samples
    tail = sorted(scenario_costs)[max(0, int(samples * 0.95)):]
    cvar = sum(tail) / max(1, len(tail))
    feasible = quantity * params.purchase_cost <= params.budget and quantity <= params.max_replenishment and (1 - stockout_probability) >= params.target_service_level
    return {"strategy": name, "replenishment_quantity": quantity, "stockout_probability": round(stockout_probability, 4), "delay_probability": round(delays / samples, 4), "service_level": round(1 - stockout_probability, 4), "expected_total_cost": round(total_cost / samples, 2), "cvar_95_cost": round(cvar, 2), "constraint_feasible": feasible}


def _optimize(params: RiskParameters) -> tuple[int | None, str | None]:
    try:
        from ortools.linear_solver import pywraplp
    except ImportError:
        return None, "OR-Tools is not installed in the decision runtime"
    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        return None, "OR-Tools SCIP solver is unavailable"
    max_by_budget = int(params.budget // params.purchase_cost)
    upper = min(params.max_replenishment, max_by_budget)
    if upper < 0:
        return None, "budget is infeasible"
    quantity = solver.IntVar(0, upper, "replenishment_quantity")
    expected_demand = params.demand_mean * (1 + params.delay_probability * params.extra_delay_days / max(params.lead_time_days, 1))
    shortfall = solver.NumVar(0, solver.infinity(), "expected_shortfall")
    solver.Add(shortfall >= expected_demand - params.current_inventory - quantity)
    solver.Add(params.current_inventory + quantity >= expected_demand * params.target_service_level)
    solver.Minimize(quantity * (params.purchase_cost + params.holding_cost) + shortfall * params.stockout_cost)
    if solver.Solve() != pywraplp.Solver.OPTIMAL:
        return None, "constraints are infeasible for the configured budget and service level"
    return int(round(quantity.solution_value())), None


def make_decision(events: list[dict], seed: int = 20260820, samples: int = 1000, constraints: dict | None = None) -> dict:
    params = parameters_from_events(events, constraints)
    risk_quantity, reason = _optimize(params)
    fixed = min(100, params.max_replenishment, int(params.budget // params.purchase_cost))
    mean = min(params.max_replenishment, int(round(params.demand_mean * params.lead_time_days - params.current_inventory)), int(params.budget // params.purchase_cost))
    strategies = [_evaluate("正常订货", max(0, fixed), params, seed, samples), _evaluate("适度加订", max(0, mean), params, seed, samples)]
    if risk_quantity is not None:
        strategies.append(_evaluate("高保障加订", risk_quantity, params, seed, samples))
    feasible = [item for item in strategies if item["constraint_feasible"]]
    # An explicit objective makes the choice explainable instead of privileging a label.
    for item in strategies:
        item["objective_score"] = round(item["expected_total_cost"] + 0.25 * item["cvar_95_cost"] + 3000 * item["stockout_probability"], 2)
    recommended = min(feasible, key=lambda item: item["objective_score"])["strategy"] if feasible else None
    return {"seed": seed, "samples": samples, "risk_parameters": asdict(params), "applied_constraints": constraints or {}, "strategies": strategies, "recommended_strategy": recommended, "recommendation_reason": "lowest expected cost + CVaR tail-risk + stockout penalty among feasible strategies" if recommended else "no strategy satisfies the configured hard constraints", "infeasibility_reason": reason}
