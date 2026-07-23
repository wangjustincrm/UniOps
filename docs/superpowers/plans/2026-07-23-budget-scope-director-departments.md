# Director Multi-Department Budget Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Director sees the budgets of every department they direct, not just the one department they belong to.

**Architecture:** The shared-DB scope resolver's non-full-access branch changes from a single-department lookup to a union of the viewer's own department and every department where they are the configured Director (`approval_dept_routing.director_user_id`). The rule is role-agnostic. The resolver file stays byte-identical across budget-api and finance-api; the frontend only relabels the scope chip.

**Tech Stack:** FastAPI + SQLAlchemy async raw SQL (budget-api, finance-api), React + TS 5.9.3 (epms frontend).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-23-budget-scope-director-departments-design.md`
- Visible departments (non-full-access) = **own `users.department_id` ∪ every `approval_dept_routing.dept_id` where `director_user_id` = this user**.
- **Role-agnostic:** never read a role string to decide which departments a user directs. Works when `director` is the primary role, an additional role, or absent.
- **Do NOT** add `director` to `FULL_ACCESS_PRIMARY` or `FULL_ACCESS_ASSIGNED`. The pinned sets stay exactly:
  - `FULL_ACCESS_PRIMARY = {gm, opm, finance_manager, ap_clerk, system_admin, cfo, auditor, procurement_manager}`
  - `FULL_ACCESS_ASSIGNED = {gm, opm, finance_manager, procurement_manager, finance_bp}`
- **Fail-closed unchanged:** empty department set → empty `cost_center_ids` → empty result, never company-wide.
- `budget-api/app/core/budget_scope.py` and `finance-api/app/core/budget_scope.py` must remain **byte-identical** (verify with `diff`).
- Raw SQL uuid binds use `CAST(:x AS uuid)`; uuid lists use `ANY(CAST(:xs AS uuid[]))` (asyncpg).
- No DB migration. `approval_dept_routing` already exists in the shared DB.
- UI copy: English only.
- Test DB discipline: run ONE suite at a time. Local docker `uniops_postgres`; budget-api → `budget_test`, finance-api → `finance_test`. Never point at prod (`10.10.50.20`).
- Env to run pytest in a worktree (no `.env`): export `TEST_PG_PASSWORD` (`docker exec uniops_postgres env | grep POSTGRES_PASSWORD`), `JWT_SECRET_KEY` (from the main checkout's service `.env`), and for finance-api also `DATABASE_URL` pointed at local `finance_test`. Use the main checkout's venv.
- Frontend typecheck: TS **5.9.3** — do NOT pass `--ignoreDeprecations 6.0`. No new tsc errors vs baseline.
- Do NOT push or deploy. Commit to `feature/budget-scope-director-depts` only.

---

## File Structure

**budget-api**
- Modify: `budget-api/app/core/budget_scope.py` (resolver union + docstring)
- Modify: `budget-api/tests/conftest.py` (add `approval_dept_routing` stub table)
- Modify: `budget-api/tests/test_budget_scope.py` (director cases)

**finance-api**
- Modify: `finance-api/app/core/budget_scope.py` (byte-identical copy)
- Modify: `finance-api/tests/conftest.py` (shadow `approval_dept_routing`)
- Modify: `finance-api/tests/test_budget_scope.py` (director cases)

**epms frontend**
- Modify: `epms/src/pages/budget/BudgetDashboard.tsx:173-174` (scope label)

---

## Task 1: budget-api resolver — own department ∪ directed departments

**Files:**
- Modify: `budget-api/app/core/budget_scope.py:1-8` (docstring), `:47-61` (dept lookup)
- Modify: `budget-api/tests/conftest.py:63-72` (stub tables block)
- Modify: `budget-api/tests/test_budget_scope.py`

**Interfaces:**
- Produces: `resolve_budget_scope(db, user_id, primary_role) -> BudgetScope` — unchanged signature; `cost_center_ids` now covers own ∪ directed departments. `scoped_cc_ids` unchanged.

- [ ] **Step 1: Add the `approval_dept_routing` stub table**

In `budget-api/tests/conftest.py`, inside the same `with engine.connect() as conn:` block that creates the `users` / `user_roles` / `cost_centers` stubs (right after the `cost_centers` statement, before `conn.commit()`), add:

```python
        # Test scaffolding for scope tests: approval-api owns this table in the
        # real shared DB; budget-api's own alembic chain never creates it. Only
        # the two columns budget_scope.py reads are included.
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " director_user_id uuid"
            ")"
        ))
```

- [ ] **Step 2: Write the failing tests**

Append to `budget-api/tests/test_budget_scope.py`:

```python
async def _seed_dept_cc(db_session, dept_id, cc_id, code):
    await db_session.execute(sa.text(
        "INSERT INTO cost_centers (id, code, name, department_id, is_active) "
        "VALUES (CAST(:cc AS uuid), :code, :code, CAST(:d AS uuid), true)"),
        {"cc": str(cc_id), "code": code, "d": str(dept_id)})


async def _make_director(db_session, uid, own_dept, directed_depts, primary_role,
                         additional_roles=()):
    await db_session.execute(sa.text(
        "INSERT INTO users (id, department_id, role, is_active) "
        "VALUES (CAST(:u AS uuid), CAST(:d AS uuid), :r, true)"),
        {"u": str(uid), "d": str(own_dept) if own_dept else None, "r": primary_role})
    for rc in additional_roles:
        await db_session.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (CAST(:u AS uuid), :r)"),
            {"u": str(uid), "r": rc})
    for d in directed_depts:
        await db_session.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, director_user_id) "
            "VALUES (CAST(:d AS uuid), CAST(:u AS uuid))"),
            {"d": str(d), "u": str(uid)})


@pytest.mark.asyncio
async def test_director_sees_own_and_directed_departments(db_session):
    """The LIVE PRODUCTION SHAPE: primary role dept_manager + ADDITIONAL role
    director. Which departments they direct must come from
    approval_dept_routing.director_user_id, never from a role string."""
    uid = uuid.uuid4()
    dept_a, dept_b, dept_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cc_a, cc_b, cc_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for d, cc, code in ((dept_a, cc_a, "SELL-A"), (dept_b, cc_b, "SELL-B"),
                        (dept_c, cc_c, "SELL-C")):
        await _seed_dept_cc(db_session, d, cc, code)
    await _make_director(db_session, uid, own_dept=dept_a,
                         directed_depts=[dept_a, dept_b, dept_c],
                         primary_role="dept_manager", additional_roles=["director"])

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert scope.full_access is False          # director must NOT be company-wide
    assert set(scope.cost_center_ids) == {cc_a, cc_b, cc_c}


@pytest.mark.asyncio
async def test_director_own_dept_not_among_directed_is_still_included(db_session):
    uid = uuid.uuid4()
    own, dir1 = uuid.uuid4(), uuid.uuid4()
    cc_own, cc_dir = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, own, cc_own, "SELL-OWN")
    await _seed_dept_cc(db_session, dir1, cc_dir, "SELL-DIR")
    await _make_director(db_session, uid, own_dept=own, directed_depts=[dir1],
                         primary_role="dept_manager", additional_roles=["director"])

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert set(scope.cost_center_ids) == {cc_own, cc_dir}


@pytest.mark.asyncio
async def test_director_without_own_department_still_gets_directed(db_session):
    uid = uuid.uuid4()
    dir1 = uuid.uuid4()
    cc_dir = uuid.uuid4()
    await _seed_dept_cc(db_session, dir1, cc_dir, "SELL-DIR2")
    await _make_director(db_session, uid, own_dept=None, directed_depts=[dir1],
                         primary_role="requester", additional_roles=["director"])

    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.cost_center_ids == [cc_dir]   # NOT fail-closed to empty


@pytest.mark.asyncio
async def test_plain_employee_directing_nothing_unchanged(db_session):
    uid = uuid.uuid4()
    own, other = uuid.uuid4(), uuid.uuid4()
    cc_own, cc_other = uuid.uuid4(), uuid.uuid4()
    await _seed_dept_cc(db_session, own, cc_own, "SELL-MINE")
    await _seed_dept_cc(db_session, other, cc_other, "SELL-OTHER")
    await _make_director(db_session, uid, own_dept=own, directed_depts=[],
                         primary_role="requester")

    scope = await resolve_budget_scope(db_session, uid, "requester")
    assert scope.cost_center_ids == [cc_own]
```

Ensure the file imports `sqlalchemy as sa`, `uuid`, `pytest`, and `resolve_budget_scope` (add any that are missing).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd budget-api && python -m pytest tests/test_budget_scope.py -k director -v`
Expected: FAIL — the directed departments are ignored, so
`test_director_sees_own_and_directed_departments` gets `{cc_a}` instead of
`{cc_a, cc_b, cc_c}` (and the no-own-department case returns `[]`).

- [ ] **Step 4: Implement the union in the resolver**

In `budget-api/app/core/budget_scope.py`, replace the department lookup and cost-center query (currently lines 47-61, from `dept = (await db.execute(` through the final `return`) with:

```python
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
```

Update the module docstring's first paragraph (lines 3-5) to read:

```python
Server-side source of truth for "who may see company-wide budget" vs "only the
departments they are responsible for" (their own department plus every department
they are the configured Director of). Reads the shared DB (users / user_roles /
approval_dept_routing / cost_centers). An identical copy lives in
finance-api/app/core/budget_scope.py — it MUST stay byte-identical (each service
pins the role sets with a test).
See docs/superpowers/specs/2026-07-23-budget-scope-director-departments-design.md
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd budget-api && python -m pytest tests/test_budget_scope.py -v`
Expected: PASS — the four new director tests plus the pre-existing pinning and
fail-closed tests.

- [ ] **Step 6: Run the full budget-api suite**

Run: `cd budget-api && python -m pytest -q`
Expected: no new failures versus before this task (the scope endpoint tests must
still pass — a viewer directing nothing is unchanged).

- [ ] **Step 7: Commit**

```bash
git add budget-api/app/core/budget_scope.py budget-api/tests/conftest.py budget-api/tests/test_budget_scope.py
git commit -m "feat(budget-api): director budget scope covers every department they direct"
```

---

## Task 2: finance-api resolver — keep the byte-identical copy in sync

**Files:**
- Modify: `finance-api/app/core/budget_scope.py` (replace with budget-api's copy)
- Modify: `finance-api/tests/conftest.py` (shadow `approval_dept_routing`)
- Modify: `finance-api/tests/test_budget_scope.py`

**Interfaces:**
- Consumes: the updated `budget-api/app/core/budget_scope.py` from Task 1 (copied verbatim).
- Produces: identical `resolve_budget_scope` / `scoped_cc_ids` behaviour in finance-api.

- [ ] **Step 1: Shadow the `approval_dept_routing` table in the test schema**

`finance-api/tests/conftest.py` builds the test schema from alembic + mirror
models and already shadows the identity-owned `user_roles` table with a raw
`CREATE TABLE`. Follow that same pattern and add, next to it:

```python
        # approval-api owns this in the real shared DB (no ORM model here);
        # budget_scope.py reads it to resolve which departments a user directs.
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " director_user_id uuid"
            ")"
        ))
```

Match the surrounding code's connection/commit style exactly (read the existing
`user_roles` shadow statement and mirror it).

- [ ] **Step 2: Write the failing test**

Append to `finance-api/tests/test_budget_scope.py`:

```python
import uuid
import sqlalchemy as sa
import pytest
from app.core.budget_scope import resolve_budget_scope


@pytest.mark.asyncio
async def test_director_sees_own_and_directed_departments(db_session):
    """Live production shape: primary role dept_manager + additional role
    director; departments come from approval_dept_routing, not the role."""
    uid = uuid.uuid4()
    dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
    cc_a, cc_b = uuid.uuid4(), uuid.uuid4()
    for d, cc, code in ((dept_a, cc_a, "FIN-A"), (dept_b, cc_b, "FIN-B")):
        await db_session.execute(sa.text(
            "INSERT INTO cost_centers (id, code, name, department_id, is_active) "
            "VALUES (CAST(:cc AS uuid), :code, :code, CAST(:d AS uuid), true)"),
            {"cc": str(cc), "code": code, "d": str(d)})
    await db_session.execute(sa.text(
        "INSERT INTO users (id, department_id, role, is_active) "
        "VALUES (CAST(:u AS uuid), CAST(:d AS uuid), 'dept_manager', true)"),
        {"u": str(uid), "d": str(dept_a)})
    await db_session.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) "
        "VALUES (CAST(:u AS uuid), 'director')"), {"u": str(uid)})
    for d in (dept_a, dept_b):
        await db_session.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, director_user_id) "
            "VALUES (CAST(:d AS uuid), CAST(:u AS uuid))"),
            {"d": str(d), "u": str(uid)})

    scope = await resolve_budget_scope(db_session, uid, "dept_manager")
    assert scope.full_access is False
    assert set(scope.cost_center_ids) == {cc_a, cc_b}
```

If `cost_centers` / `users` in finance-api's test schema require additional
NOT NULL columns (they are ORM mirror tables, not stubs), read the mirror models
in `finance-api/app/models/mirrors.py` and supply those columns in the INSERTs.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_budget_scope.py -k director -v`
Expected: FAIL — only `cc_a` is returned (directed department B is ignored).

- [ ] **Step 4: Copy the updated resolver verbatim**

```bash
cp budget-api/app/core/budget_scope.py finance-api/app/core/budget_scope.py
diff budget-api/app/core/budget_scope.py finance-api/app/core/budget_scope.py
```
The `diff` must print nothing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd finance-api && python -m pytest tests/test_budget_scope.py -v`
Expected: PASS — the new director test plus the existing role-set pinning test.

- [ ] **Step 6: Confirm no NC-scoping regression**

Run: `cd finance-api && python -m pytest tests/test_nc_scoping.py -v`
Expected: PASS (14 tests) — a viewer directing nothing behaves exactly as before.

- [ ] **Step 7: Commit**

```bash
git add finance-api/app/core/budget_scope.py finance-api/tests/conftest.py finance-api/tests/test_budget_scope.py
git commit -m "feat(finance-api): sync director-aware budget scope resolver"
```

---

## Task 3: frontend — "My Departments" when the scope spans several

**Files:**
- Modify: `epms/src/pages/budget/BudgetDashboard.tsx:173-174`

**Interfaces:**
- Consumes: `ApiActualsScope` = `{ full_access: boolean; cost_centers: { id: string; code: string; name: string; department_id: string | null }[] }` (already in `epms/src/services/budget.ts`).

- [ ] **Step 1: Compute the distinct department count and use it in the label**

The current code is:

```tsx
  const scopeLabel = ccId === 'all'
    ? (isFullAccess ? 'Company-wide' : 'My Department')
```

Replace those two lines with:

```tsx
  // A Director can be responsible for several departments, so the chip must not
  // hard-code the singular.
  const scopedDeptCount = new Set(
    (scope?.cost_centers ?? []).map((cc) => cc.department_id).filter(Boolean),
  ).size
  const scopeLabel = ccId === 'all'
    ? (isFullAccess ? 'Company-wide' : (scopedDeptCount > 1 ? 'My Departments' : 'My Department'))
```

Leave the rest of the ternary (the specific-cost-center branch) untouched.

- [ ] **Step 2: Typecheck**

Run from `epms/`: the same tsc invocation used for this repo's epms app —
TS 5.9.3, `tsconfig.app.json`, `--noEmit`, **without** `--ignoreDeprecations 6.0`.
Capture the total error count before and after your edit.
Expected: count unchanged versus baseline, and no error line mentions
`BudgetDashboard.tsx`. Paste both counts into your report — "no output from a
grep" is not evidence.

- [ ] **Step 3: Commit**

```bash
git add epms/src/pages/budget/BudgetDashboard.tsx
git commit -m "feat(epms): pluralise budget scope chip for multi-department viewers"
```

---

## Task 4: close the unscoped `GET /actuals` and `GET /actuals/opening`

Both endpoints shipped in `9aa3449` require only a valid JWT — no permission key
and no scope filter — so any authenticated user can read raw monthly actuals and
opening balances for **any** cost center. (Their sibling `/actuals/opening-import`
does gate on `require_permission("budget.opening.write")`, so this is an omission,
not a deliberate opening.) Scope them exactly like the other six endpoints.

**Files:**
- Modify: `budget-api/app/crud/balance.py:71-77` (`list_monthly_actuals`)
- Modify: `budget-api/app/crud/opening.py:109-112` (`list_opening_balances`)
- Modify: `budget-api/app/api/v1/actual.py:50-57` (`list_actuals`), `:98-103` (`list_opening`)
- Test: `budget-api/tests/test_actuals_scoping.py`

**Interfaces:**
- Consumes: `_scope_for(db, user)` and `scoped_cc_ids(scope, requested)` — both
  already present in `budget-api/app/api/v1/actual.py`.
- Produces: `list_monthly_actuals(..., cc_ids: list[uuid.UUID] | None = None)` and
  `list_opening_balances(..., cc_ids: list[uuid.UUID] | None = None)`.
  Semantics identical to the existing summary CRUD: `None` → unchanged;
  `[]` → empty result; non-empty → `cost_center_id IN cc_ids`.

- [ ] **Step 1: Write the failing tests**

Append to `budget-api/tests/test_actuals_scoping.py`:

```python
@pytest.mark.asyncio
async def test_list_actuals_scopes_to_department(client, dept_manager_token, admin_token, seed_two_cc_plans):
    r = await client.get("/actuals?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200
    ccs = {i["cost_center_id"] for i in r.json()["items"]}
    assert str(seed_two_cc_plans["cc_b"]) not in ccs   # other department must not leak

    r_all = await client.get("/actuals?fiscal_year=2026",
                             headers={"Authorization": f"Bearer {admin_token}"})
    ccs_all = {i["cost_center_id"] for i in r_all.json()["items"]}
    assert ccs <= ccs_all


@pytest.mark.asyncio
async def test_list_actuals_out_of_scope_cc_is_clamped(client, dept_manager_token, seed_two_cc_plans):
    r = await client.get(f"/actuals?fiscal_year=2026&cost_center_id={seed_two_cc_plans['cc_b']}",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200                        # never 403
    ccs = {i["cost_center_id"] for i in r.json()["items"]}
    assert str(seed_two_cc_plans["cc_b"]) not in ccs   # clamped, not honoured


@pytest.mark.asyncio
async def test_list_opening_scopes_to_department(client, dept_manager_token, seed_two_cc_plans):
    r = await client.get("/actuals/opening?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200
    ccs = {i["cost_center_id"] for i in r.json()["items"]}
    assert str(seed_two_cc_plans["cc_b"]) not in ccs
```

The `client`, `dept_manager_token`, `admin_token` and `seed_two_cc_plans` fixtures
already exist in `budget-api/tests/conftest.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -k "list_actuals or list_opening" -v`
Expected: FAIL — the dept viewer currently receives cc_b rows (no scoping applied).

- [ ] **Step 3: Add `cc_ids` to the two CRUD functions**

In `budget-api/app/crud/balance.py`, change `list_monthly_actuals`'s signature to add
`cc_ids: list[uuid.UUID] | None = None`, short-circuit an empty result immediately
after the docstring, and extend the existing cost-center filter:

```python
    if cc_ids is not None and len(cc_ids) == 0:
        return []
    ...
    if cost_center_id is not None:
        q = q.where(BudgetLedger.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(BudgetLedger.cost_center_id.in_(cc_ids))
```

In `budget-api/app/crud/opening.py`, do the same for `list_opening_balances`: add
`cc_ids: list[uuid.UUID] | None = None`, extend its
`if cost_center_id is not None:` filter with the matching `elif cc_ids:` clause,
and return an empty response when `cc_ids == []`. Read that function's own `return`
statement and mirror its exact response shape for the empty case — do not guess it.

- [ ] **Step 4: Apply the scope in both endpoints**

In `budget-api/app/api/v1/actual.py`, drop the `# noqa: ARG001` from both handlers
(the `user` argument is now used) and route through the scope:

```python
@router.get("/actuals", response_model=ActualsListResponse)
async def list_actuals(
    db: SessionDep, user: CurrentUserPayload,
    cost_center_id: uuid.UUID | None = Query(default=None),
    fiscal_year: int | None = Query(default=None),
    account_id: uuid.UUID | None = Query(default=None),
    month: int | None = Query(default=None, ge=1, le=12),
):
    scope = await _scope_for(db, user)
    if scope.full_access:
        items = await balance_crud.list_monthly_actuals(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
            account_id=account_id, month=month,
        )
    else:
        items = await balance_crud.list_monthly_actuals(
            db, cost_center_id=None, fiscal_year=fiscal_year,
            account_id=account_id, month=month,
            cc_ids=scoped_cc_ids(scope, cost_center_id),
        )
    return ActualsListResponse(items=items, total=len(items))


@router.get("/actuals/opening", response_model=OpeningListResponse)
async def list_opening(
    db: SessionDep, user: CurrentUserPayload,
    fiscal_year: int = Query(..., ge=2020, le=2100),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """List imported opening balances for a (cost_center, fiscal_year) scope."""
    scope = await _scope_for(db, user)
    if scope.full_access:
        return await opening_crud.list_opening_balances(
            db, cost_center_id=cost_center_id, fiscal_year=fiscal_year,
        )
    return await opening_crud.list_opening_balances(
        db, cost_center_id=None, fiscal_year=fiscal_year,
        cc_ids=scoped_cc_ids(scope, cost_center_id),
    )
```

Keep the full-access branch calling the CRUD exactly as before, so full-access
behaviour is byte-equivalent.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -v`
Expected: PASS, including the pre-existing summary/monthly/scope endpoint tests.

- [ ] **Step 6: Run the full budget-api suite**

Run: `cd budget-api && python -m pytest -q`
Expected: no new failures versus before this task.

- [ ] **Step 7: Commit**

```bash
git add budget-api/app/crud/balance.py budget-api/app/crud/opening.py budget-api/app/api/v1/actual.py budget-api/tests/test_actuals_scoping.py
git commit -m "fix(budget-api): scope GET /actuals and /actuals/opening by department"
```

---

## Task 5: authenticate `GET /plan-lines` (close the unauthenticated read)

`budget-api` `GET /api/v1/plan-lines` (`budget-api/app/api/v1/crossservice.py:16-17`)
has NO auth dependency — no `CurrentUserPayload`, no permission. budget-api is
proxied on a public subdomain, so an **unauthenticated** caller can read every cost
center's approved plan amounts. Its four sibling POST endpoints in the same file all
require `CurrentUserPayload`; this GET is the omission. It is unauthenticated by
construction because finance-api's only consumer calls it without a token. This task
requires a valid JWT on the route and forwards the caller's token from finance-api.
(Department scoping of plan-lines belongs to the separate finance `/gl` scoping
follow-up, not this task — this closes only the *anonymous* hole.)

**Files:**
- Modify: `budget-api/app/api/v1/crossservice.py:16-17` (add auth dependency)
- Modify: `finance-api/app/services/budget_client.py:141-152` (`fetch_plan_lines` forwards token)
- Modify: `finance-api/app/api/v1/account_balance.py:85-101` (`budget_actual_grid` extracts + forwards token)
- Test: `budget-api/tests/test_actuals_scoping.py`

**Interfaces:**
- `CurrentUserPayload` is ALREADY imported in `crossservice.py`. Reuse it.
- `_auth_headers(bearer_token)` in `finance-api/app/services/budget_client.py`
  already builds the Authorization header and tolerates `None` (used by
  `fetch_monthly_summary`, whose `bearer_token` is `str | None`). Reuse it.
- `Request` is ALREADY imported in `finance-api/app/api/v1/account_balance.py`
  (the export endpoint uses `request: Request`). Reuse the same
  `auth = request.headers.get("authorization")...` extraction it uses.

- [ ] **Step 1: Write the failing test**

Append to `budget-api/tests/test_actuals_scoping.py` (it already has `client` and
`admin_token` fixtures):

```python
@pytest.mark.asyncio
async def test_plan_lines_requires_auth(client, admin_token):
    r = await client.get("/api/v1/plan-lines?fiscal_year=2026&month=1")
    assert r.status_code in (401, 403)          # was 200/anonymous before the fix
    r_ok = await client.get("/api/v1/plan-lines?fiscal_year=2026&month=1",
                            headers={"Authorization": f"Bearer {admin_token}"})
    assert r_ok.status_code == 200
    assert isinstance(r_ok.json(), list)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -k plan_lines -v`
Expected: FAIL — the no-header request currently returns 200 (endpoint is anonymous).

- [ ] **Step 3: Add the auth dependency on the route**

In `budget-api/app/api/v1/crossservice.py`, change the handler signature:

```python
@router.get("/plan-lines")
async def plan_lines(fiscal_year: int, month: int, db: SessionDep,
                     user: CurrentUserPayload):  # noqa: ARG001 — auth gate only
    """Current approved plan lines for a (fiscal_year, month), all cost centers.
    Read-only, consumed by finance's predreal grid for the budget column.
    Requires a valid JWT (forwarded by finance-api)."""
    rows = await plan_crud.current_plan_lines(db, fiscal_year, month)
    return [{"cost_center_id": str(cc), "account_id": str(a), "amount": str(amt)}
            for cc, a, amt in rows]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd budget-api && python -m pytest tests/test_actuals_scoping.py -k plan_lines -v`
Expected: PASS (401/403 without a token, 200 with).

- [ ] **Step 5: Forward the token from finance-api**

In `finance-api/app/services/budget_client.py`, change `fetch_plan_lines`:

```python
async def fetch_plan_lines(fiscal_year: int, month: int,
                           *, bearer_token: str | None = None) -> dict:
    """(cost_center_id, account_id) -> Decimal budget from current approved plans.
    account_id == the JV line's income_expense_item_id (shared budget_accounts),
    so the predreal grid joins budget↔actual directly on this key.
    budget-api requires auth → forward the caller's bearer token."""
    import uuid
    from decimal import Decimal
    url = f"{settings.budget_api_url}/api/v1/plan-lines"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, params={"fiscal_year": fiscal_year, "month": month},
                             headers=_auth_headers(bearer_token))
        r.raise_for_status()
    return {(uuid.UUID(x["cost_center_id"]), uuid.UUID(x["account_id"])): Decimal(x["amount"])
            for x in r.json()}
```

In `finance-api/app/api/v1/account_balance.py`, make `budget_actual_grid` extract
and forward the caller's token:

```python
@router.get("/budget-actual-grid")
async def budget_actual_grid(request: Request, _: CurrentUser,
                             db: AsyncSession = Depends(get_db),
                             period: str = Query(...)):
    """④b Budget-vs-Actual grid: per (cost center × income-expense item)
    budget/actual/variance for the 5 categories + Payroll/Depreciation tie-out
    rows + unmapped exceptions. Budget comes from budget-api; if it is
    unreachable the grid still renders actuals (budget column 0)."""
    import logging

    from app.services import budget_client
    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    try:
        budget = await budget_client.fetch_plan_lines(
            int(period[:4]), int(period[5:7]), bearer_token=token)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "budget-api plan-lines fetch failed; rendering actuals only")
        budget = {}
    return await crud.budget_actual_grid(db, period, budget)
```

> Confirm `fetch_plan_lines` has no other caller before changing its signature:
> `grep -rn "fetch_plan_lines" finance-api/app`. Update any other call site too.

- [ ] **Step 6: Regression — finance-api budget-actual-grid still works**

Run: `cd finance-api && python -m pytest tests/test_account_balance.py -k "grid or budget_actual" -v`
Expected: PASS. If a test drives `budget_actual_grid` and asserts the budget column
is populated, check how it stubs `budget_client`
(`grep -n "fetch_plan_lines\|budget_client" finance-api/tests/test_account_balance.py`);
if it monkeypatches `fetch_plan_lines`, make the stub accept the new `bearer_token`
keyword (e.g. `async def _fake(fy, m, **kwargs): ...`).

- [ ] **Step 7: Commit**

```bash
git add budget-api/app/api/v1/crossservice.py finance-api/app/services/budget_client.py finance-api/app/api/v1/account_balance.py budget-api/tests/test_actuals_scoping.py
git commit -m "fix(budget-api): require auth on GET /plan-lines; finance forwards token"
```

---

## Self-Review

**Spec coverage**
- Resolver union (own ∪ directed), role-agnostic, raw SQL patterns → Task 1, copied in Task 2.
- `director` stays out of both full-access sets → Global Constraints + existing pinning tests (Tasks 1/2 keep them green).
- Fail-closed on empty department set → Task 1 Step 4 (`if not dept_ids: return ... []`), covered by the pre-existing fail-closed test.
- Byte-identical copies → Task 2 Step 4 `diff` gate.
- Test matrix (live production shape / own-dept-not-directed / no own dept / plain employee / fail-closed) → Task 1 Step 2, key case mirrored in Task 2.
- Frontend label → Task 3.
- No migration, no endpoint/CRUD changes → nothing to do, stated in Global Constraints.

**Placeholder scan:** none — every code step shows the code.

**Type consistency:** `resolve_budget_scope(db, user_id, primary_role) -> BudgetScope` and `scoped_cc_ids` signatures unchanged across tasks; `BudgetScope.cost_center_ids: list[uuid.UUID]`; frontend uses `scope.cost_centers[].department_id` exactly as typed in `ApiActualsScope`.

## Post-implementation (do NOT run without user go)

- Verify against real dev data: `cox@canadaroyalmilk.com` (primary `dept_manager` + additional `director`, own dept 0111, directs 0110/0111/0113) must resolve to `SELL-0110`, `SELL-0111`, `SELL-0113`.
- Release: merge → main, rebuild all 15 images at one sha, **verify the production TAG first**, then deploy. No migration.
