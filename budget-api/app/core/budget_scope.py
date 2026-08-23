"""Budget Dashboard department scoping (shared-DB resolver).

Server-side source of truth for "who may see company-wide budget" vs "only the
departments they are responsible for". Both are driven by the Access Control
matrix (Portal Admin -> Access Control), NOT by a hardcoded role list:

    finance.budget.view_all   -> company-wide
    finance.budget.view_dept  -> own department, every department they DIRECT,
                                 and (for the OPM) every department routed to
                                 the OPM
    neither                   -> nothing (fail-closed)

Admission goes through the shared uniops_authz package, so a grant counts
whether it sits on the caller's primary role or on an additional role, and
system_admin is admitted without consulting the matrix.

Reads the shared DB (identity's role_defs / role_permissions matrix, users,
user_roles, approval_dept_routing, cost_centers). An identical copy lives in
finance-api/app/core/budget_scope.py — it MUST stay byte-identical (each
service pins the behaviour with a test).
See docs/superpowers/specs/2026-07-23-budget-scope-director-departments-design.md
"""
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uniops_authz import has_permission, user_role_codes

logger = logging.getLogger(__name__)

# Access Control matrix keys, registered and seeded by identity migration
# 0010_budget_view_scope_perms.
PERM_VIEW_ALL = "finance.budget.view_all"
PERM_VIEW_DEPT = "finance.budget.view_dept"


@dataclass
class BudgetScope:
    full_access: bool
    cost_center_ids: list[uuid.UUID]  # meaningful only when full_access is False


async def resolve_budget_scope(
    db: AsyncSession, user_id: uuid.UUID, primary_role: str | None,
) -> BudgetScope:
    # Everything here reads the shared DB (identity's matrix, then user_roles /
    # users / approval_dept_routing / cost_centers). Per spec, ANY resolution
    # error — not just "no department" — must fail CLOSED to an empty scope,
    # never full-access: approval_dept_routing is owned by approval-api's
    # migration chain and the matrix tables by identity's, so either may simply
    # not exist in some environments, and a service deployed ahead of identity's
    # migration must not hand out company-wide numbers.
    try:
        role = primary_role or ""
        if await has_permission(db, user_id, role, PERM_VIEW_ALL):
            return BudgetScope(full_access=True, cost_center_ids=[])
        if not await has_permission(db, user_id, role, PERM_VIEW_DEPT):
            return BudgetScope(full_access=False, cost_center_ids=[])

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

        # Departments the OPM is responsible for. Unlike `director`, the OPM is a
        # POST (identity migration 0003 keeps it a singleton) and
        # approval_dept_routing records only WHICH POST — 'gm' or 'opm' — owns a
        # department's gm_or_opm approval step, with no user column to match on.
        # So here, and only here, the role string is the link back to the person.
        opm_depts: list[uuid.UUID] = []
        if "opm" in await user_role_codes(db, user_id, role):
            opm_depts = [
                r for (r,) in (await db.execute(
                    text("SELECT dept_id FROM approval_dept_routing "
                         "WHERE gm_or_opm = 'opm'"),
                )).all()
            ]

        dept_ids = {d for d in [own_dept, *directed, *opm_depts] if d}
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
