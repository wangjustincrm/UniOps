"""Budget Dashboard department scoping (shared-DB resolver).

Server-side source of truth for "who may see company-wide budget" vs "only the
departments they are responsible for" (their own department plus every department
they are the configured Director of). Reads the shared DB (users / user_roles /
approval_dept_routing / cost_centers). An identical copy lives in
finance-api/app/core/budget_scope.py — it MUST stay byte-identical (each service
pins the role sets with a test).
See docs/superpowers/specs/2026-07-23-budget-scope-director-departments-design.md
"""
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

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

    # Everything below reads the shared DB (user_roles / users /
    # approval_dept_routing / cost_centers). Per spec, ANY resolution error
    # here — not just "no department" — must fail CLOSED to an empty scope,
    # never full-access: e.g. approval_dept_routing is owned by approval-api's
    # migration chain, so it may simply not exist in some environments.
    try:
        assigned = {
            r for (r,) in (await db.execute(
                text("SELECT role_code FROM user_roles WHERE user_id = CAST(:uid AS uuid)"),
                {"uid": str(user_id)},
            )).all()
        }
        if assigned & FULL_ACCESS_ASSIGNED:
            return BudgetScope(full_access=True, cost_center_ids=[])

        own_dept = (await db.execute(
            text("SELECT department_id FROM users WHERE id = CAST(:uid AS uuid)"),
            {"uid": str(user_id)},
        )).scalar_one_or_none()

        # Departments this user DIRECTS. Resolved from the per-department assignment
        # in approval_dept_routing — deliberately NOT from a role string, so it works
        # whether `director` is the primary role, an additional role, or absent.
        directed = [
            r for (r,) in (await db.execute(
                text("SELECT dept_id FROM approval_dept_routing "
                     "WHERE director_user_id = CAST(:uid AS uuid)"),
                {"uid": str(user_id)},
            )).all()
        ]

        dept_ids = {d for d in [own_dept, *directed] if d}
        if not dept_ids:
            return BudgetScope(full_access=False, cost_center_ids=[])

        cc_ids = [
            r for (r,) in (await db.execute(
                text("SELECT id FROM cost_centers "
                     "WHERE department_id = ANY(CAST(:depts AS uuid[])) "
                     "AND is_active IS TRUE"),
                {"depts": [str(d) for d in dept_ids]},
            )).all()
        ]
        return BudgetScope(full_access=False, cost_center_ids=cc_ids)
    except Exception:
        logger.exception(
            "budget scope resolution failed for user_id=%s; failing closed to empty scope",
            user_id,
        )
        return BudgetScope(full_access=False, cost_center_ids=[])


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
