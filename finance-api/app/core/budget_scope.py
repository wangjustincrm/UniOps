"""Budget Dashboard department scoping (shared-DB resolver).

Server-side source of truth for "who may see company-wide budget" vs "only their
department's cost centers". Reads the shared DB (users / user_roles /
cost_centers). An identical copy lives in finance-api/app/core/budget_scope.py —
the role-set constants below MUST stay in sync (each service pins them with a
test). See docs/superpowers/specs/2026-07-22-budget-dashboard-department-scoping-design.md
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Faithful port of BudgetDashboard.tsx's FULL_ACCESS_ROLES (primary role) ∪
# SPECIAL_ROLE_CODES (primary∪additional) ∪ the finance_bp-assigned check.
FULL_ACCESS_PRIMARY = frozenset({
    "gm", "opm", "finance_manager", "ap_clerk",
    "system_admin", "cfo", "auditor", "procurement_manager",
})
FULL_ACCESS_ASSIGNED = frozenset({
    "gm", "opm", "finance_manager", "procurement_manager", "finance_bp",
})


@dataclass
class BudgetScope:
    full_access: bool
    cost_center_ids: list[uuid.UUID]  # meaningful only when full_access is False


async def resolve_budget_scope(
    db: AsyncSession, user_id: uuid.UUID, primary_role: str | None,
) -> BudgetScope:
    if primary_role in FULL_ACCESS_PRIMARY:
        return BudgetScope(full_access=True, cost_center_ids=[])

    assigned = {
        r for (r,) in (await db.execute(
            text("SELECT role_code FROM user_roles WHERE user_id = CAST(:uid AS uuid)"),
            {"uid": str(user_id)},
        )).all()
    }
    if assigned & FULL_ACCESS_ASSIGNED:
        return BudgetScope(full_access=True, cost_center_ids=[])

    dept = (await db.execute(
        text("SELECT department_id FROM users WHERE id = CAST(:uid AS uuid)"),
        {"uid": str(user_id)},
    )).scalar_one_or_none()
    if not dept:
        return BudgetScope(full_access=False, cost_center_ids=[])

    cc_ids = [
        r for (r,) in (await db.execute(
            text("SELECT id FROM cost_centers "
                 "WHERE department_id = CAST(:dept AS uuid) AND is_active IS TRUE"),
            {"dept": str(dept)},
        )).all()
    ]
    return BudgetScope(full_access=False, cost_center_ids=cc_ids)


def scoped_cc_ids(
    scope: BudgetScope, requested: uuid.UUID | None,
) -> list[uuid.UUID] | None:
    """The cost-center id list to filter a query by.

    None -> no filter (full access: honour `requested` as the single CC, or all).
    []   -> fail-closed empty result.
    [..] -> restrict to these cost centers.
    """
    if scope.full_access:
        return [requested] if requested else None
    if not scope.cost_center_ids:
        return []
    if requested and requested in scope.cost_center_ids:
        return [requested]
    return list(scope.cost_center_ids)  # omitted or out-of-scope -> whole dept
