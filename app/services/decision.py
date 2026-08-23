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


@dataclass(frozen=True)
class RiskProfile:
    key: str
    strategy: str
    target_offset: float
    cvar_weight: float
    stockout_penalty: float


RISK_PROFILES = (
    RiskProfile("cost_first", "正常订货", -0.05, 0.05, 1_000.0),
    RiskProfile("balanced", "适度加订", 0.0, 0.25, 3_000.0),
    RiskProfile("service_first", "高保障加订", 0.03, 0.50, 6_000.0),
)


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


def _evaluate(name: str, quantity: int, params: RiskParameters, seed: int, samples: int, service_target: float | None = None) -> dict:
    rng = random.Random(seed)
    stockouts = delays = 0; total_cost = 0.0; scenario_costs: list[float] = []
    delayed_arrival_fraction = max(0.0, min(1.0, 1 - params.extra_delay_days / max(params.lead_time_days, 1)))
    for _ in range(samples):
        demand = max(0, rng.gauss(params.demand_mean, params.demand_stddev))
        delayed = rng.random() < params.delay_probability
        # A delay does not automatically mean an entire order disappears.  In
        # this single-cycle approximation, the part of the lead-time horizon
        # remaining after the delay can still contribute inventory.  A delay
        # as long as the lead time naturally reduces this fraction to zero.
        available = params.current_inventory + quantity * (delayed_arrival_fraction if delayed else 1.0)
        shortage = max(0, demand - available)
        leftover = max(0, available - demand)
        stockouts += shortage > 0; delays += delayed
        cost = quantity * params.purchase_cost + leftover * params.holding_cost + shortage * params.stockout_cost + (params.expedite_cost if delayed else 0)
        total_cost += cost; scenario_costs.append(cost)
    stockout_probability = stockouts / samples
    tail = sorted(scenario_costs)[max(0, int(samples * 0.95)):]
    cvar = sum(tail) / max(1, len(tail))
    target = params.target_service_level if service_target is None else service_target
    feasible = quantity * params.purchase_cost <= params.budget and quantity <= params.max_replenishment and (1 - stockout_probability) >= target
    return {"strategy": name, "replenishment_quantity": quantity, "stockout_probability": round(stockout_probability, 4), "delay_probability": round(delays / samples, 4), "delayed_arrival_fraction": round(delayed_arrival_fraction, 4), "service_level": round(1 - stockout_probability, 4), "expected_total_cost": round(total_cost / samples, 2), "cvar_95_cost": round(cvar, 2), "constraint_feasible": feasible}


def _optimize(params: RiskParameters, service_target: float, risk_weight: float) -> tuple[int | None, str | None]:
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
    solver.Add(params.current_inventory + quantity >= expected_demand * service_target)
    solver.Minimize(quantity * (params.purchase_cost + params.holding_cost) + shortfall * (params.stockout_cost + risk_weight))
    if solver.Solve() != pywraplp.Solver.OPTIMAL:
        return None, "constraints are infeasible for the configured budget and service level"
    return int(round(quantity.solution_value())), None


def _maximum_order_quantity(params: RiskParameters) -> tuple[int, bool, bool]:
    """Return the hard order ceiling and which commercial limit binds it."""
    if params.purchase_cost <= 0:
        return 0, False, False
    budget_ceiling = int(params.budget // params.purchase_cost)
    if budget_ceiling < 0:
        return 0, True, False
    upper = min(params.max_replenishment, budget_ceiling)
    return max(0, upper), budget_ceiling < params.max_replenishment, params.max_replenishment <= budget_ceiling


def _minimum_simulated_service_quantity(params: RiskParameters, seed: int, samples: int, lower: int, upper: int, service_target: float) -> int | None:
    """Find the smallest integer quantity meeting hard constraints in simulation.

    Every simulation restarts with the same seed, so candidate quantities see the
    same demand and delay scenarios.  That makes the service-level comparison
    reproducible and monotonic enough for a binary search over this single-SKU
    prototype.
    """
    if lower > upper:
        return None
    if not _evaluate("candidate", upper, params, seed, samples, service_target)["constraint_feasible"]:
        return None
    left, right = lower, upper
    while left < right:
        middle = (left + right) // 2
        if _evaluate("candidate", middle, params, seed, samples, service_target)["constraint_feasible"]:
            right = middle
        else:
            left = middle + 1
    return left


def _infeasibility_summary(params: RiskParameters, ceiling: int, ceiling_result: dict, budget_binds: bool, quantity_binds: bool) -> tuple[str, list[str], list[str]]:
    target = params.target_service_level
    maximum = ceiling_result["service_level"]
    blockers: list[str] = []
    remedies: list[str] = []
    if params.budget < 0:
        blockers.append("预算小于 0，连零补货方案也不满足预算约束")
    if maximum < target:
        blockers.append(f"最大允许补货量 {ceiling} 下的仿真服务水平为 {maximum:.1%}，低于目标 {target:.1%}")
        if params.delay_probability > 0:
            delayed_arrival_fraction = max(0.0, min(1.0, 1 - params.extra_delay_days / max(params.lead_time_days, 1)))
            if delayed_arrival_fraction == 0:
                blockers.append("配送延迟覆盖整个补货周期，本周期补货无法及时到店")
            else:
                blockers.append(f"配送延迟会使当期可用补货降至约 {delayed_arrival_fraction:.0%}，限制服务水平")
        remedies.extend([
            f"将目标服务水平调整至 {maximum:.1%} 或以下",
            "在补货周期开始前提前调拨或提高期初库存",
            "缩短中央仓提前期，或启用加急/备用配送",
        ])
    if budget_binds:
        blockers.append(f"预算最多支持订货 {ceiling} 件")
        remedies.append("在审核后提高预算，或降低单件采购成本")
    if quantity_binds:
        blockers.append(f"最大订货量限制为 {ceiling} 件")
        remedies.append("在审核后提高最大订货量，或拆分为后续补货周期")
    if not blockers:
        blockers.append("当前硬约束下没有通过仿真的订货方案")
    reason = "；".join(blockers)
    return reason, blockers, list(dict.fromkeys(remedies))


def make_decision(events: list[dict], seed: int = 20260820, samples: int = 1000, constraints: dict | None = None) -> dict:
    params = parameters_from_events(events, constraints)
    ceiling, budget_binds, quantity_binds = _maximum_order_quantity(params)
    strategies = []
    for profile in RISK_PROFILES:
        service_target = min(0.99, max(0.0, params.target_service_level + profile.target_offset))
        solver_quantity, _ = _optimize(params, service_target, profile.stockout_penalty)
        lower = max(0, solver_quantity or 0)
        simulated_quantity = _minimum_simulated_service_quantity(params, seed, samples, lower, ceiling, service_target)
        # If the exact simulated target is impossible, show the best permitted
        # contingency quantity with an explicit infeasibility flag.
        quantity = simulated_quantity if simulated_quantity is not None else ceiling
        result = _evaluate(profile.strategy, quantity, params, seed, samples, service_target)
        result.update({
            "risk_profile": profile.key,
            "service_target": round(service_target, 4),
            "objective_score": round(result["expected_total_cost"] + profile.cvar_weight * result["cvar_95_cost"] + profile.stockout_penalty * result["stockout_probability"], 2),
        })
        strategies.append(result)
    feasible = [item for item in strategies if item["constraint_feasible"]]
    balanced = next((item for item in strategies if item["risk_profile"] == "balanced" and item["constraint_feasible"]), None)
    recommended = (balanced or min(feasible, key=lambda item: item["objective_score"], default=None))
    recommended = recommended["strategy"] if recommended else None
    ceiling_result = _evaluate("maximum permitted", ceiling, params, seed, samples)
    reason = None
    blockers: list[str] = []
    remedies: list[str] = []
    if not feasible:
        reason, blockers, remedies = _infeasibility_summary(params, ceiling, ceiling_result, budget_binds, quantity_binds)
    return {
        "seed": seed,
        "samples": samples,
        "risk_parameters": asdict(params),
        "applied_constraints": constraints or {},
        "strategies": strategies,
        "recommended_strategy": recommended,
        "recommendation_reason": "balanced profile satisfies the requested service target" if balanced else "lowest risk-adjusted objective among feasible strategies" if recommended else "no strategy satisfies the configured hard constraints",
        "infeasibility_reason": reason,
        "feasibility_summary": {
            "target_service_level": params.target_service_level,
            "max_achievable_service_level": ceiling_result["service_level"],
            "max_replenishment_quantity": ceiling,
            "blocking_constraints": blockers,
            "remediation_options": remedies,
        },
    }
