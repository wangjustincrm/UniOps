# Budget Dashboard Department Scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope the Budget Dashboard's data server-side so a non-full-access viewer sees only their department's budget/actuals, while full-access roles keep seeing company-wide.

**Architecture:** A shared-DB scope resolver (identical copy in budget-api and finance-api) computes `{full_access, cost_center_ids}` from `users`/`user_roles`/`cost_centers`. All six pre-aggregating dashboard endpoints apply the resolved cost-center set as a soft `IN (…)` filter (never 403, clamp out-of-scope requests, fail-closed to empty). The frontend drops its hard-coded role logic and consumes a new `GET /actuals/scope`.

**Tech Stack:** FastAPI + SQLAlchemy async (budget-api, finance-api), React + TanStack Query + TS 6 (epms frontend).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-22-budget-dashboard-department-scoping-design.md`.
- Full-access role sets (verbatim, must be identical in both services):
  - `FULL_ACCESS_PRIMARY = {gm, opm, finance_manager, ap_clerk, system_admin, cfo, auditor, procurement_manager}`
  - `FULL_ACCESS_ASSIGNED = {gm, opm, finance_manager, procurement_manager, finance_bp}`
- Soft filter only — **no endpoint returns 403** for scope. Out-of-scope requested CC → clamp to department set. Unresolvable scope → **empty result** (fail-closed), never company-wide.
- No DB migration. Reads existing shared tables only.
- UI copy: English only.
- Frontend typecheck: `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` (TS 6.0.3).
- Test DB discipline: run one suite at a time; override `POSTGRES_*` to the local docker `uniops_postgres` (password via `docker exec`). Never run two epms/budget/finance suites concurrently.
- Raw SQL against `users`/`user_roles`/`cost_centers` uses `CAST(:x AS uuid)` for uuid binds (asyncpg).
- Do NOT push or deploy. Commit to `feature/budget-dashboard-dept-scoping` only.

---

## File Structure

**budget-api**
- Create: `budget-api/app/core/budget_scope.py` — resolver + effective-CC helper.
- Modify: `budget-api/app/crud/balance.py` — add `cc_ids` filter to the two summary functions.
- Modify: `budget-api/app/api/v1/actual.py` — apply scope on 2 endpoints, add `GET /actuals/scope`.
- Modify: `budget-api/app/schemas/actual.py` — add scope response schema.
- Create: `budget-api/tests/test_budget_scope.py`, `budget-api/tests/test_actuals_scoping.py`.

**finance-api**
- Create: `finance-api/app/core/budget_scope.py` — identical resolver + helper.
- Modify: `finance-api/app/crud/account_balance.py` — add `cc_ids` filter to 4 nc_* functions.
- Modify: `finance-api/app/api/v1/account_balance.py` — apply scope on 4 endpoints.
- Create: `finance-api/tests/test_budget_scope.py`, `finance-api/tests/test_nc_scoping.py`.

**epms frontend**
- Modify: `epms/src/services/budget.ts` — add `getActualsScope`.
- Modify: `epms/src/hooks/useBudget.ts` — add `useActualsScope`.
- Modify: `epms/src/pages/budget/BudgetDashboard.tsx` — consume scope, drop local role logic.

---

## Task 1: budget-api scope resolver

**Files:**
- Create: `budget-api/app/core/budget_scope.py`
- Test: `budget-api/tests/test_budget_scope.py`

**Interfaces:**
- Produces:
  - `FULL_ACCESS_PRIMARY: frozenset[str]`, `FULL_ACCESS_ASSIGNED: frozenset[str]`
  - `@dataclass BudgetScope: full_access: bool; cost_center_ids: list[uuid.UUID]`
  - `async resolve_budget_scope(db: AsyncSession, user_id: uuid.UUID, primary_role: str | None) -> BudgetScope`
  - `scoped_cc_ids(scope: BudgetScope, requested: uuid.UUID | None) -> list[uuid.UUID] | None`
    (`None` = no filter / aggregate all; `[]` = fail-closed empty; non-empty = restrict)

- [ ] **Step 1: Write the failing test**

```python
# budget-api/tests/test_budget_scope.py
import uuid
import pytest
from app.core.budget_scope import (
    FULL_ACCESS_PRIMARY, FULL_ACCESS_ASSIGNED,
    BudgetScope, resolve_budget_scope, scoped_cc_ids,
)


def test_role_sets_are_pinned():
    # Guards against silent drift vs finance-api's copy and the frontend.
    assert FULL_ACCESS_PRIMARY == {
        "gm", "opm", "finance_manager", "ap_clerk",
        "system_admin", "cfo", "auditor", "procurement_manager",
    }
    assert FULL_ACCESS_ASSIGNED == {
        "gm", "opm", "finance_manager", "procurement_manager", "finance_bp",
    }


def test_scoped_cc_ids_logic():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    full = BudgetScope(full_access=True, cost_center_ids=[])
    dept = BudgetScope(full_access=False, cost_center_ids=[a, b])
    empty = BudgetScope(full_access=False, cost_center_ids=[])

    # full access: unchanged (None = aggregate all, or the single requested CC)
    assert scoped_cc_ids(full, None) is None
    assert scoped_cc_ids(full, a) == [a]
    # dept viewer, no request -> whole department
    assert scoped_cc_ids(dept, None) == [a, b]
    # dept viewer, in-scope CC -> that CC
    assert scoped_cc_ids(dept, a) == [a]
    # dept viewer, out-of-scope CC -> clamp to department (NOT 403)
    assert scoped_cc_ids(dept, c) == [a, b]
    # no resolvable scope -> fail-closed empty
    assert scoped_cc_ids(empty, None) == []
    assert scoped_cc_ids(empty, a) == []


@pytest.mark.asyncio
async def test_resolve_primary_full_access(db_session):
    uid = uuid.uuid4()
    scope = await resolve_budget_scope(db_session, uid, "finance_manager")
    assert scope.full_access is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd budget-api && python -m pytest tests/test_budget_scope.py::test_role_sets_are_pinned -v`
Expected: FAIL — `ModuleNotFoundError: app.core.budget_scope`.

- [ ] **Step 3: Write the resolver**

```python
# budget-api/app/core/budget_scope.py
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd budget-api && python -m pytest tests/test_budget_scope.py -v`
Expected: PASS (the async DB test uses the existing `db_session` fixture; if the fixture name differs in conftest, match it).

- [ ] **Step 5: Commit**

```bash
git add budget-api/app/core/budget_scope.py budget-api/tests/test_budget_scope.py
git commit -m "feat(budget-api): department budget-scope resolver"
```

---

## Task 2: budget-api CRUD cc_ids filter

**Files:**
- Modify: `budget-api/app/crud/balance.py:136-199` (`get_actuals_summary`), `:202-281` (`get_monthly_actuals_summary`)
- Test: `budget-api/tests/test_actuals_scoping.py` (CRUD-level portion)

**Interfaces:**
- Consumes: nothing from Task 1 (pure CRUD).
- Produces:
  - `get_actuals_summary(db, *, cost_center_id=None, fiscal_year, cc_ids: list[uuid.UUID] | None = None)`
  - `get_monthly_actuals_summary(db, *, cost_center_id=None, fiscal_year, cc_ids: list[uuid.UUID] | None = None)`
  - Semantics: `cc_ids is None` → unchanged; `cc_ids == []` → empty `accounts`; non-empty → aggregate restricted to `cost_center_id IN cc_ids`.

- [ ] **Step 1: Write the failing test**

```python
# budget-api/tests/test_actuals_scoping.py
import uuid
import pytest
from app.crud import balance as balance_crud


@pytest.mark.asyncio
async def test_summary_empty_cc_ids_returns_no_accounts(db_session, seed_two_cc_plans):
    # seed_two_cc_plans: fixture creating approved plans in CC-A and CC-B, FY 2026.
    res = await balance_crud.get_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[])
    assert res.accounts == []


@pytest.mark.asyncio
async def test_summary_cc_ids_restricts_aggregate(db_session, seed_two_cc_plans):
    cc_a = seed_two_cc_plans["cc_a"]
    only_a = await balance_crud.get_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[cc_a])
    all_cc = await balance_crud.get_actuals_summary(db_session, fiscal_year=2026)
    a_total = sum(x.annual_budget for x in only_a.accounts)
    all_total = sum(x.annual_budget for x in all_cc.accounts)
    assert a_total < all_total  # A-only excludes CC-B's plan
```

> Note: if no `seed_two_cc_plans` fixture exists, add it to `budget-api/tests/conftest.py` creating two cost centers (distinct `department_id`), a current approved `BudgetPlan` + `BudgetPlanLine` in each for FY2026. Keep amounts distinct so the assertion is meaningful.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -k cc_ids -v`
Expected: FAIL — `get_actuals_summary() got an unexpected keyword argument 'cc_ids'`.

- [ ] **Step 3: Edit `get_actuals_summary`**

Change the signature (balance.py:136-139):

```python
async def get_actuals_summary(
    db: AsyncSession,
    *, cost_center_id: uuid.UUID | None = None, fiscal_year: int,
    cc_ids: list[uuid.UUID] | None = None,
) -> ActualsSummaryResponse:
```

Immediately after the docstring / before building `accts_q`, short-circuit empty:

```python
    if cc_ids is not None and len(cc_ids) == 0:
        return ActualsSummaryResponse(
            cost_center_id=cost_center_id, fiscal_year=fiscal_year, accounts=[])
```

In the `if cost_center_id is None:` aggregate branch, decide the branch by `cc_ids`:

```python
        if cost_center_id is None:
            annual_q = (
                select(func.coalesce(func.sum(BudgetPlanLine.amount), 0))
                .join(BudgetPlan, BudgetPlanLine.plan_id == BudgetPlan.id)
                .where(
                    BudgetPlan.fiscal_year == fiscal_year,
                    BudgetPlan.status == "approved",
                    BudgetPlan.is_current.is_(True),
                    BudgetPlanLine.account_id == acct.id,
                )
            )
            committed_q = select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
                BudgetLedger.account_id == acct.id,
                BudgetLedger.fiscal_year == fiscal_year,
                BudgetLedger.operation == "commit",
            )
            release_q = select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
                BudgetLedger.account_id == acct.id,
                BudgetLedger.fiscal_year == fiscal_year,
                BudgetLedger.operation.in_(["release", "actualize"]),
            )
            actual_q = select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
                BudgetLedger.account_id == acct.id,
                BudgetLedger.fiscal_year == fiscal_year,
                BudgetLedger.operation.in_(["actualize", "book_expense", "opening"]),
            )
            if cc_ids:
                annual_q = annual_q.where(BudgetPlan.cost_center_id.in_(cc_ids))
                committed_q = committed_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
                release_q = release_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
                actual_q = actual_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
            annual = Decimal(str((await db.execute(annual_q)).scalar_one()))
            committed = Decimal(str((await db.execute(committed_q)).scalar_one())) \
                       - Decimal(str((await db.execute(release_q)).scalar_one()))
            if committed < 0:
                committed = Decimal("0")
            actual = Decimal(str((await db.execute(actual_q)).scalar_one()))
        else:
            annual = await get_annual_budget(db, cost_center_id, acct.id, fiscal_year)
            committed = await get_committed(db, cost_center_id, acct.id, fiscal_year)
            actual = await get_actual_spent(db, cost_center_id, acct.id, fiscal_year)
```

> The endpoint (Task 3) always passes `cost_center_id=None` together with `cc_ids`, so the aggregate branch runs; the single-CC `else` branch is retained for backward compatibility with any other caller.

- [ ] **Step 4: Edit `get_monthly_actuals_summary`**

Change the signature (balance.py:202-205):

```python
async def get_monthly_actuals_summary(
    db: AsyncSession,
    *, cost_center_id: uuid.UUID | None = None, fiscal_year: int,
    cc_ids: list[uuid.UUID] | None = None,
) -> MonthlyActualsSummaryResponse:
```

After the account query (`acct_rows = …`), short-circuit empty:

```python
    if cc_ids is not None and len(cc_ids) == 0:
        return MonthlyActualsSummaryResponse(
            cost_center_id=cost_center_id, fiscal_year=fiscal_year, accounts=[])
```

Extend the two existing `if cost_center_id is not None:` filters to also honour `cc_ids` (add right after each existing block):

```python
    if cost_center_id is not None:
        plan_q = plan_q.where(BudgetPlan.cost_center_id == cost_center_id)
    elif cc_ids:
        plan_q = plan_q.where(BudgetPlan.cost_center_id.in_(cc_ids))
    ...
    if cost_center_id is not None:
        actual_q = actual_q.where(BudgetLedger.cost_center_id == cost_center_id)
    elif cc_ids:
        actual_q = actual_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -k cc_ids -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add budget-api/app/crud/balance.py budget-api/tests/test_actuals_scoping.py budget-api/tests/conftest.py
git commit -m "feat(budget-api): cc_ids filter on actuals summary CRUD"
```

---

## Task 3: budget-api endpoints — apply scope + GET /actuals/scope

**Files:**
- Modify: `budget-api/app/schemas/actual.py` (add scope schema)
- Modify: `budget-api/app/api/v1/actual.py:35-55` (summary + monthly), add `/actuals/scope`
- Test: `budget-api/tests/test_actuals_scoping.py` (endpoint portion)

**Interfaces:**
- Consumes: `resolve_budget_scope`, `scoped_cc_ids` (Task 1); `cc_ids=` CRUD kwarg (Task 2).
- Produces: `GET /actuals/scope` → `ActualsScopeResponse { full_access: bool, cost_centers: list[ScopeCostCenter{id, code, name, department_id}] }`.

- [ ] **Step 1: Add the response schema**

Append to `budget-api/app/schemas/actual.py`:

```python
class ScopeCostCenter(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    department_id: uuid.UUID | None


class ActualsScopeResponse(BaseModel):
    full_access: bool
    cost_centers: list[ScopeCostCenter]
```

- [ ] **Step 2: Write the failing endpoint test**

```python
# append to budget-api/tests/test_actuals_scoping.py
@pytest.mark.asyncio
async def test_scope_endpoint_dept_user(client, dept_manager_token, seed_two_cc_plans):
    r = await client.get("/actuals/scope",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["full_access"] is False
    ids = {c["id"] for c in body["cost_centers"]}
    assert str(seed_two_cc_plans["cc_a"]) in ids  # dept-A manager sees CC-A
    assert str(seed_two_cc_plans["cc_b"]) not in ids


@pytest.mark.asyncio
async def test_summary_scopes_to_department(client, dept_manager_token, seed_two_cc_plans):
    r = await client.get("/actuals/summary?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200  # never 403
    scoped_total = sum(float(a["annual_budget"]) for a in r.json()["accounts"])
    r_all = await client.get("/actuals/summary?fiscal_year=2026",
                             headers={"Authorization": f"Bearer {admin_token(client)}"})
    all_total = sum(float(a["annual_budget"]) for a in r_all.json()["accounts"])
    assert scoped_total < all_total
```

> `dept_manager_token` fixture: a signed access token (`{"sub": <uid>, "role": "dept_manager", "type": "access"}`) for a user in department-A; the user has `users.department_id = dept_A` and CC-A has `department_id = dept_A`. Reuse the JWT-signing helper the other budget-api tests use. `admin_token` similarly with role `system_admin`.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -k scope -v`
Expected: FAIL — 404 on `/actuals/scope` (not yet defined) / summary not scoped.

- [ ] **Step 4: Edit the endpoints**

In `budget-api/app/api/v1/actual.py`, update imports and the two summary endpoints, and add the scope endpoint:

```python
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import text

from app.core.authz import require_permission
from app.core.budget_scope import resolve_budget_scope, scoped_cc_ids
from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import balance as balance_crud
from app.crud import opening as opening_crud
from app.schemas.actual import (
    ActualsListResponse,
    ActualsScopeResponse,
    ActualsSummaryResponse,
    MonthlyActualsSummaryResponse,
    ScopeCostCenter,
)
from app.schemas.opening import OpeningImportResult, OpeningListResponse

router = APIRouter(tags=["actual"])


async def _scope_for(db, user: dict):
    return await resolve_budget_scope(
        db, uuid.UUID(user["sub"]), user.get("role"))


@router.get("/actuals/scope", response_model=ActualsScopeResponse)
async def actuals_scope(db: SessionDep, user: CurrentUserPayload):
    scope = await _scope_for(db, user)
    if scope.full_access:
        rows = (await db.execute(text(
            "SELECT id, code, name, department_id FROM cost_centers "
            "WHERE is_active IS TRUE ORDER BY code"))).all()
    elif scope.cost_center_ids:
        rows = (await db.execute(text(
            "SELECT id, code, name, department_id FROM cost_centers "
            "WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY code"),
            {"ids": [str(x) for x in scope.cost_center_ids]})).all()
    else:
        rows = []
    return ActualsScopeResponse(
        full_access=scope.full_access,
        cost_centers=[ScopeCostCenter(id=r[0], code=r[1], name=r[2], department_id=r[3])
                      for r in rows],
    )
```

Update `actuals_summary` (was actual.py:35-43):

```python
@router.get("/actuals/summary", response_model=ActualsSummaryResponse)
async def actuals_summary(
    db: SessionDep, user: CurrentUserPayload,
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    scope = await _scope_for(db, user)
    cc_ids = scoped_cc_ids(scope, cost_center_id)
    if scope.full_access:
        return await balance_crud.get_actuals_summary(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year)
    return await balance_crud.get_actuals_summary(
        db, cost_center_id=None, fiscal_year=fiscal_year, cc_ids=cc_ids)
```

Update `actuals_monthly_summary` identically (was actual.py:46-55):

```python
@router.get("/actuals/monthly-summary", response_model=MonthlyActualsSummaryResponse)
async def actuals_monthly_summary(
    db: SessionDep, user: CurrentUserPayload,
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    scope = await _scope_for(db, user)
    cc_ids = scoped_cc_ids(scope, cost_center_id)
    if scope.full_access:
        return await balance_crud.get_monthly_actuals_summary(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year)
    return await balance_crud.get_monthly_actuals_summary(
        db, cost_center_id=None, fiscal_year=fiscal_year, cc_ids=cc_ids)
```

> For full-access callers we pass `cost_center_id` through unchanged (so a full-access user selecting one CC keeps the exact old single-CC path). For non-full-access we always pass `cc_ids` (which is `[]` when fail-closed → empty response).

- [ ] **Step 5: Run tests to verify pass**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add budget-api/app/api/v1/actual.py budget-api/app/schemas/actual.py budget-api/tests/test_actuals_scoping.py
git commit -m "feat(budget-api): scope actuals endpoints + GET /actuals/scope"
```

---

## Task 4: finance-api scope resolver (identical copy)

**Files:**
- Create: `finance-api/app/core/budget_scope.py` (byte-identical to budget-api's, minus the module docstring's path note if desired)
- Test: `finance-api/tests/test_budget_scope.py`

**Interfaces:**
- Produces: same `FULL_ACCESS_PRIMARY`, `FULL_ACCESS_ASSIGNED`, `BudgetScope`, `resolve_budget_scope`, `scoped_cc_ids` as Task 1.

- [ ] **Step 1: Write the failing test**

```python
# finance-api/tests/test_budget_scope.py
from app.core.budget_scope import FULL_ACCESS_PRIMARY, FULL_ACCESS_ASSIGNED


def test_role_sets_match_budget_api():
    # Pins the duplicated constants so finance-api and budget-api cannot drift.
    assert FULL_ACCESS_PRIMARY == {
        "gm", "opm", "finance_manager", "ap_clerk",
        "system_admin", "cfo", "auditor", "procurement_manager",
    }
    assert FULL_ACCESS_ASSIGNED == {
        "gm", "opm", "finance_manager", "procurement_manager", "finance_bp",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_budget_scope.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the file**

Copy `budget-api/app/core/budget_scope.py` verbatim into `finance-api/app/core/budget_scope.py`. The code is identical (both import only `sqlalchemy` + stdlib). Keep the module docstring's sync note.

- [ ] **Step 4: Run test to verify pass**

Run: `cd finance-api && python -m pytest tests/test_budget_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/core/budget_scope.py finance-api/tests/test_budget_scope.py
git commit -m "feat(finance-api): department budget-scope resolver (mirror of budget-api)"
```

---

## Task 5: finance-api CRUD cc_ids filter

**Files:**
- Modify: `finance-api/app/crud/account_balance.py` — `nc_actuals_monthly:466`, `nc_partner_monthly:505`, `nc_partner_monthly_all:548`, `nc_partner_vouchers:593`
- Test: `finance-api/tests/test_nc_scoping.py` (CRUD portion)

**Interfaces:**
- Produces (each gains `cc_ids: list | None = None`; keep existing params for response echo):
  - `nc_actuals_monthly(db, fiscal_year, cost_center_id=None, cc_ids=None)`
  - `nc_partner_monthly(db, income_expense_item_id, fiscal_year, cost_center_id=None, cc_ids=None)`
  - `nc_partner_monthly_all(db, *, fiscal_year, cost_center_id=None, cc_ids=None)`
  - `nc_partner_vouchers(db, income_expense_item_id, fiscal_year, month, cost_center_id=None, partner_id=None, cc_ids=None)`
  - Semantics: `cc_ids is None` → unchanged; `cc_ids == []` → empty result; non-empty → add `JournalVoucherLine.cost_center_id.in_(cc_ids)`.

- [ ] **Step 1: Write the failing test**

```python
# finance-api/tests/test_nc_scoping.py
import pytest
from app.crud import account_balance as crud


@pytest.mark.asyncio
async def test_nc_actuals_empty_cc_ids_is_empty(db_session, seed_posted_jv_two_cc):
    res = await crud.nc_actuals_monthly(db_session, 2026, cc_ids=[])
    assert res["accounts"] == {}


@pytest.mark.asyncio
async def test_nc_actuals_cc_ids_restricts(db_session, seed_posted_jv_two_cc):
    cc_a = seed_posted_jv_two_cc["cc_a"]
    only_a = await crud.nc_actuals_monthly(db_session, 2026, cc_ids=[cc_a])
    all_cc = await crud.nc_actuals_monthly(db_session, 2026)
    def total(r): return sum(sum(m.values()) for m in r["accounts"].values())
    assert 0 < total(only_a) < total(all_cc) or total(only_a) != total(all_cc)
```

> `seed_posted_jv_two_cc`: fixture inserting POSTED `JournalVoucher` + `JournalVoucherLine` rows in two cost centers with `income_expense_item_id` set, FY2026. Reuse whatever JV seeding the existing finance-api NC tests use.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_nc_scoping.py -k cc_ids -v`
Expected: FAIL — unexpected keyword `cc_ids`.

- [ ] **Step 3: Edit the four CRUD functions**

For each, add the param and, right after the existing `if cost_center_id is not None:` filter block, add the `cc_ids` filter; and short-circuit empty when `cc_ids == []`. Example for `nc_actuals_monthly` (account_balance.py:466-491):

```python
async def nc_actuals_monthly(db: AsyncSession, fiscal_year: int,
                             cost_center_id=None, cc_ids=None) -> dict:
    if cc_ids is not None and len(cc_ids) == 0:
        return {"fiscal_year": fiscal_year, "accounts": {}}
    ...
    if cost_center_id is not None:
        q = q.where(JournalVoucherLine.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(JournalVoucherLine.cost_center_id.in_(cc_ids))
```

> Match each function's actual empty-return shape: `nc_actuals_monthly` → `{"fiscal_year": …, "accounts": {}}`; `nc_partner_monthly` / `nc_partner_monthly_all` → their `{… "partners"/"accounts": …}` shape with an empty collection; `nc_partner_vouchers` → `{"period": …, "rows": []}`. Read each function's existing return statement and mirror it for the empty case. Apply the same `elif cc_ids:` filter right after every existing `if cost_center_id is not None:` block in the four functions.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd finance-api && python -m pytest tests/test_nc_scoping.py -k cc_ids -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/account_balance.py finance-api/tests/test_nc_scoping.py finance-api/tests/conftest.py
git commit -m "feat(finance-api): cc_ids filter on NC actuals CRUD"
```

---

## Task 6: finance-api endpoints — apply scope

**Files:**
- Modify: `finance-api/app/api/v1/account_balance.py:88-119` (4 nc endpoints), `:122-174` (export)
- Test: `finance-api/tests/test_nc_scoping.py` (endpoint portion)

**Interfaces:**
- Consumes: `resolve_budget_scope`, `scoped_cc_ids` (Task 4); `cc_ids=` CRUD kwargs (Task 5).

- [ ] **Step 1: Write the failing test**

```python
# append to finance-api/tests/test_nc_scoping.py
@pytest.mark.asyncio
async def test_nc_actuals_endpoint_scopes_to_department(
        client, dept_manager_token, admin_token, seed_posted_jv_two_cc):
    r = await client.get("/gl/nc-actuals-monthly?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200  # never 403
    def total(body): return sum(sum(m.values()) for m in body["accounts"].values())
    r_all = await client.get("/gl/nc-actuals-monthly?fiscal_year=2026",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert total(r.json()) <= total(r_all.json())
    # dept viewer must NOT see the other department's CC total
    assert total(r.json()) < total(r_all.json())
```

> `dept_manager_token` / `admin_token`: signed tokens as in Task 3, using finance-api's JWT settings. The dept manager belongs to the department owning CC-A.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_nc_scoping.py -k endpoint -v`
Expected: FAIL — endpoint returns company-wide total (equal, not `<`).

- [ ] **Step 3: Edit the endpoints**

Add imports at top of `finance-api/app/api/v1/account_balance.py`:

```python
from app.core.budget_scope import resolve_budget_scope, scoped_cc_ids
```

Add a helper and use `user` (drop the `_`) in the four nc endpoints + export:

```python
async def _cc_scope(db, user: dict, requested):
    scope = await resolve_budget_scope(db, uuid.UUID(user["sub"]), user.get("role"))
    if scope.full_access:
        return {"cost_center_id": requested}   # unchanged behaviour
    return {"cost_center_id": None, "cc_ids": scoped_cc_ids(scope, requested)}
```

Then, e.g. `nc_actuals_monthly` (account_balance.py:88-94):

```python
@router.get("/nc-actuals-monthly")
async def nc_actuals_monthly(user: CurrentUser, db: AsyncSession = Depends(get_db),
                             fiscal_year: int = Query(...),
                             cost_center_id: uuid.UUID | None = Query(default=None)):
    kw = await _cc_scope(db, user, cost_center_id)
    return await crud.nc_actuals_monthly(db, fiscal_year, **kw)
```

Apply the same pattern to `nc_partner_monthly`, `nc_partner_vouchers` (pass `**kw` alongside their existing positional args — for vouchers keep `partner_id`), and `budget_actual_partner_export` (compute `kw` and pass `cc_ids`/`cost_center_id` into `crud.nc_actuals_monthly`, `crud.nc_partner_monthly_all`, and into `budget_client.fetch_monthly_summary` as `cost_center_id=kw.get("cost_center_id")` — the plan side is scoped by budget-api once Task 3 lands, so pass the requested `cost_center_id` through only for full-access; for scoped users pass `cost_center_id=None` and rely on budget-api's own scoping of that same caller's token).

> For `nc_partner_monthly` / `nc_partner_vouchers` the UI always sends a locked `cost_center_id`; `_cc_scope` clamps it to the caller's set (in-scope → `[that CC]`; out-of-scope → whole department; fail-closed → `[]`). Response `cost_center_id` echo: pass the original requested value through unchanged for display (these CRUDs echo it), independent of the filter set.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd finance-api && python -m pytest tests/test_nc_scoping.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/api/v1/account_balance.py finance-api/tests/test_nc_scoping.py
git commit -m "feat(finance-api): scope NC actuals + export endpoints by department"
```

---

## Task 7: frontend — consume /actuals/scope, drop local role logic

**Files:**
- Modify: `epms/src/services/budget.ts:328-332` (add `getActualsScope`)
- Modify: `epms/src/hooks/useBudget.ts:363-379` (add `useActualsScope`)
- Modify: `epms/src/pages/budget/BudgetDashboard.tsx:29-79, 205-207, 230-236`

**Interfaces:**
- Consumes: `GET /actuals/scope` → `{ full_access: boolean; cost_centers: {id,code,name,department_id}[] }`.

- [ ] **Step 1: Add the service method**

In `epms/src/services/budget.ts`, near `getActualsSummary`:

```typescript
export interface ApiActualsScope {
  full_access: boolean
  cost_centers: { id: string; code: string; name: string; department_id: string | null }[]
}

// inside the budgetService object:
  getActualsScope: () =>
    budgetApi.get<ApiActualsScope>('/actuals/scope'),
```

- [ ] **Step 2: Add the hook**

In `epms/src/hooks/useBudget.ts`, in the Balance/Actuals section:

```typescript
import type { ApiActualsScope } from '@/services/budget'

export function useActualsScope() {
  return useQuery<ApiActualsScope>({
    queryKey: ['budget', 'actuals-scope'],
    queryFn: () => budgetService.getActualsScope(),
    staleTime: 5 * 60_000,
  })
}
```

- [ ] **Step 3: Refactor `BudgetDashboard.tsx`**

Remove `FULL_ACCESS_ROLES`, `SPECIAL_ROLE_CODES` (lines 29-52), and inside the component remove `myPermissions`/`myAssignedRoles`/`isSpecialRoleAssignee`/`isFinanceBpAssigned`/`isFullAccess` and the `visibleCCs` derivation (lines 55-79). Replace with:

```typescript
  const { data: scope } = useActualsScope()
  const isFullAccess = scope?.full_access ?? false
  const visibleCCs = useMemo(() => {
    const scoped = scope?.cost_centers ?? []
    // Preserve the richer CostCenter objects from useCostCenters where available
    // (keeps existing dropdown label shape), falling back to the scope payload.
    const byId = new Map(costCenters.map((c) => [c.id, c]))
    return scoped.map((s) => byId.get(s.id) ?? {
      id: s.id, code: s.code, name: s.name, department_id: s.department_id,
    } as (typeof costCenters)[number])
  }, [scope, costCenters])
```

Keep `useCostCenters` (still used elsewhere for labels) but delete the now-unused `useRolePermissions`, `useMyAssignedRoles`, `useAuthStore` imports if nothing else in the file uses them (verify: `user` is otherwise unused after this refactor — remove the `useAuthStore` line if so).

Update the dropdown render (line 230) to always show when the viewer has any CC and is not full-access-with-one-option — simplest: render when `visibleCCs.length > 1 || (!isFullAccess && visibleCCs.length >= 1)`:

```tsx
{(visibleCCs.length > 1 || (!isFullAccess && visibleCCs.length >= 1)) && (
  <select value={ccId} onChange={(e) => setCcId(e.target.value)} …>
    <option value="all">All Cost Centers</option>
    {visibleCCs.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
  </select>
)}
```

Add an empty-scope note near the header (after the `scopeLabel` span), shown when the viewer has no visible CC:

```tsx
{!isFullAccess && (scope?.cost_centers?.length ?? 0) === 0 && (
  <p className="text-sm text-warning-700 mt-1">
    No budget is visible for your account. Contact your administrator if this is unexpected.
  </p>
)}
```

`scopeLabel` (lines 205-207) already reads `isFullAccess`; no change needed beyond it now sourcing `isFullAccess` from `scope`.

- [ ] **Step 4: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors (compare against the known 69-error baseline — introduce zero new errors; ideally the file is clean).

- [ ] **Step 5: Commit**

```bash
git add epms/src/services/budget.ts epms/src/hooks/useBudget.ts epms/src/pages/budget/BudgetDashboard.tsx
git commit -m "feat(epms): Budget Dashboard consumes server-side /actuals/scope"
```

---

## Self-Review

**Spec coverage**
- Scope resolver + full-access predicate → Task 1 (budget-api), Task 4 (finance-api, pinned identical).
- Default = department aggregate; dropdown for specific CC → Task 3 (`scoped_cc_ids` omitted→dept set), Task 7 (dropdown always rendered).
- Soft filter, never 403 → Tasks 3/6 return 200 with clamped/empty sets; asserted in tests.
- Fail-closed → `scoped_cc_ids` returns `[]` → CRUD empty short-circuit (Tasks 2/5); frontend empty note (Task 7).
- All 6 endpoints → Task 3 (2 budget-api + scope endpoint), Task 6 (4 finance-api incl. export).
- Frontend drops hard-coded role logic → Task 7.
- Tests incl. role-set pinning in both services → Tasks 1 & 4.

**Placeholder scan:** none — every code step shows the code.

**Type consistency:** `resolve_budget_scope` / `scoped_cc_ids` / `BudgetScope` used identically across Tasks 1,3,4,6. CRUD `cc_ids` kwarg consistent Tasks 2,3,5,6. `ApiActualsScope` shape matches `ActualsScopeResponse` (full_access + cost_centers[id,code,name,department_id]).

## Post-implementation (do NOT run without user go)

- Run the full budget-api, finance-api, and epms-frontend checks (serial, per test-DB discipline).
- Manual verify with a real dept_manager token: dashboard shows only their department; full-access user unchanged.
- Release: merge → main, rebuild all 15 images at one sha, **verify production TAG first**, then deploy. Not pushed/deployed without explicit user approval.
