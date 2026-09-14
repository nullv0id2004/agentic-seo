"""Budget governor. Runs before dispatch, never after. Fails closed.

Projected cost = mean of the last three completed runs of the same workflow for this project, or the
workflow's configured estimate when there is no history. Remaining = monthly cap minus month-to-date
spend across every workflow. Projection above remaining halts the run with status halted_budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from db.connection import ProjectScope

DEFAULT_ESTIMATE_USD: dict[str, float] = {
    "post_deploy_audit": 0.50, "weekly_monitor": 0.75, "monthly_full": 6.00, "quarterly_keyword": 3.00,
    "content_pipeline": 2.50,
}


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    projected_usd: float
    remaining_usd: float
    reason: str


class BudgetError(RuntimeError):
    pass


def month_to_date_spend(scope: ProjectScope) -> float:
    row = scope.fetchone(
        "select coalesce(sum(cost_usd), 0) as spent from runs where project_id = %(project_id)s and started_at >= date_trunc('month', now())"
    )
    return float(row["spent"] or 0)


def project_cost(scope: ProjectScope, workflow: str) -> float:
    rows = scope.fetchall(
        "select cost_usd from runs where project_id = %(project_id)s and workflow = %(workflow)s and status = 'done' order by started_at desc limit 3",
        {"workflow": workflow},
    )
    if not rows:
        return DEFAULT_ESTIMATE_USD.get(workflow, 5.0)
    return float(sum(Decimal(str(r["cost_usd"])) for r in rows) / len(rows))


def decide(scope: ProjectScope, workflow: str, monthly_cap_usd: float) -> BudgetDecision:
    try:
        spent = month_to_date_spend(scope)
        projected = project_cost(scope, workflow)
    except Exception as e:  # cannot compute: fail closed
        return BudgetDecision(False, 0.0, 0.0, f"budget check failed: {type(e).__name__}: {e}")
    remaining = float(monthly_cap_usd) - spent
    if projected > remaining:
        return BudgetDecision(False, projected, remaining, f"projected {projected:.2f} USD exceeds remaining {remaining:.2f} USD of {monthly_cap_usd} cap")
    return BudgetDecision(True, projected, remaining, "ok")
