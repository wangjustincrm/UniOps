# Task 2 Report: budget-api CRUD `cc_ids` filter for actuals summaries

## Summary

Added an optional `cc_ids: list[uuid.UUID] | None = None` keyword parameter to
`get_actuals_summary` and `get_monthly_actuals_summary` in
`budget-api/app/crud/balance.py`, per the exact edits in
`.superpowers/sdd/task-2-brief.md`. Semantics implemented:

- `cc_ids is None` → unchanged behavior (existing callers unaffected).
- `cc_ids == []` → short-circuits to an empty `accounts` response (no queries run).
- `cc_ids` non-empty → in the `cost_center_id is None` aggregate branch, all
  relevant sub-queries (`annual_q`/`committed_q`/`release_q`/`actual_q` in
  `get_actuals_summary`; `plan_q`/`actual_q` in `get_monthly_actuals_summary`)
  are additionally filtered by `cost_center_id IN cc_ids`.
- The single-CC `else` branch (`cost_center_id is not None`) is untouched —
  retained for backward compatibility per the brief's note that the later
  endpoint task always passes `cost_center_id=None` together with `cc_ids`.

Also added the `seed_two_cc_plans` pytest fixture to
`budget-api/tests/conftest.py`, which the brief's tests need but which did not
exist yet. It creates one shared `BudgetL1`/`BudgetAccount`, two cost centers
(distinct `department_id`, inserted into the stub `cost_centers` table added
by Task 1) with a current-approved `BudgetPlan` + `BudgetPlanLine` each for
FY2026, using distinct amounts (CC-A=1000, CC-B=5000) so the "A-only < all"
assertion is meaningful. Returns
`{"cc_a": uuid, "cc_b": uuid, "dept_a": uuid, "dept_b": uuid}`.

## Files changed

- `budget-api/app/crud/balance.py` — signature + query-filter edits to
  `get_actuals_summary` (lines ~136-208) and `get_monthly_actuals_summary`
  (lines ~211-282).
- `budget-api/tests/conftest.py` — added imports (`uuid`, `Decimal`,
  `BudgetAccount`, `BudgetL1`, `BudgetPlan`, `BudgetPlanLine`) and the
  `seed_two_cc_plans` fixture.
- `budget-api/tests/test_actuals_scoping.py` — new file, the two tests from
  the brief, copied verbatim.

Not touched: `app/core/budget_scope.py` (Task 1's file) and no endpoint files
— CRUD + conftest only, as instructed.

## TDD evidence

Environment: worktree has no `.env`/venv of its own. Used the main checkout's
venv (`C:/Project/uniops/budget-api/.venv`) against local docker
`uniops_postgres` → `budget_test` DB. Required two env vars not covered by the
task setup notes, exported for the run only (nothing committed):
- `TEST_PG_PASSWORD` — read via `docker exec uniops_postgres env | grep POSTGRES_PASSWORD`
  (conftest's default `epms_dev` did not match the container's actual password).
- `JWT_SECRET_KEY` — required by `app/core/config.py` `Settings` (no default,
  fail-closed); alembic's `env.py` imports `app.core.config.settings` at
  import time so migrations fail without it. Reused the value from the main
  checkout's `budget-api/.env`.

### RED (before CRUD edit, fixture already added)

```
cd budget-api && python -m pytest tests/test_actuals_scoping.py -k cc_ids -v
```
```
tests/test_actuals_scoping.py::test_summary_empty_cc_ids_returns_no_accounts FAILED
tests/test_actuals_scoping.py::test_summary_cc_ids_restricts_aggregate FAILED
...
E       TypeError: get_actuals_summary() got an unexpected keyword argument 'cc_ids'
```
Matches the brief's expected failure exactly.

### GREEN (after CRUD edit)

```
cd budget-api && python -m pytest tests/test_actuals_scoping.py -k cc_ids -v
```
```
tests/test_actuals_scoping.py::test_summary_empty_cc_ids_returns_no_accounts PASSED
tests/test_actuals_scoping.py::test_summary_cc_ids_restricts_aggregate PASSED
============================== 2 passed in 5.07s ==============================
```

Full file:
```
cd budget-api && python -m pytest tests/test_actuals_scoping.py -v
```
```
2 passed in 5.14s
```

Full budget-api suite (single run, no concurrent suite):
```
cd budget-api && python -m pytest tests/ -v
```
```
24 passed in 52.39s
```
All prior suites green, including Task 1's `tests/test_budget_scope.py` (6
tests) — confirms Task 1's stub tables/conftest additions and this task's
`seed_two_cc_plans` fixture coexist without conflict.

## Self-review

- **YAGNI**: No extra parameters, no touching of `budget_scope.py` or
  endpoints, no speculative generalization beyond the brief's exact diff.
  The `elif cc_ids:` branches in `get_monthly_actuals_summary` intentionally
  mirror the brief rather than refactoring the two `if cost_center_id`
  blocks into a shared helper — kept minimal per instructions.
- **Tests assert real behavior**: `test_summary_cc_ids_restricts_aggregate`
  checks `a_total < all_total` using genuinely distinct seeded amounts (1000
  vs 5000), not a magic-number equality that could pass by coincidence.
  `test_summary_empty_cc_ids_returns_no_accounts` verifies the short-circuit
  returns before any DB aggregation.
- **Output pristine**: no stray `__pycache__`/`.pyc` files staged; `git
  status --short` before commit showed only the three intended files.
- **`cc_ids=[]` falsy-vs-None care**: used `if cc_ids is not None and
  len(cc_ids) == 0` (not `if not cc_ids`) so `None` and `[]` are
  distinguished correctly — matches the brief's explicit code.

## Follow-up: monthly-summary cc_ids coverage (review gap)

Review flagged that `get_monthly_actuals_summary`'s new `cc_ids` behavior had
no test coverage — only `get_actuals_summary` was tested. Added two analogous
tests to `budget-api/tests/test_actuals_scoping.py`, reusing `seed_two_cc_plans`
(CRUD code unchanged):

- `test_monthly_summary_empty_cc_ids_returns_no_accounts` — asserts
  `get_monthly_actuals_summary(..., cc_ids=[]).accounts == []`.
- `test_monthly_summary_cc_ids_restricts_aggregate` — asserts the CC-A-only
  `plan_year` subtotal (summed across `accounts`) is strictly less than the
  company-wide (no `cc_ids`) subtotal.

```
cd budget-api && python -m pytest tests/test_actuals_scoping.py -v
```
```
tests/test_actuals_scoping.py::test_summary_empty_cc_ids_returns_no_accounts PASSED
tests/test_actuals_scoping.py::test_summary_cc_ids_restricts_aggregate PASSED
tests/test_actuals_scoping.py::test_monthly_summary_empty_cc_ids_returns_no_accounts PASSED
tests/test_actuals_scoping.py::test_monthly_summary_cc_ids_restricts_aggregate PASSED
============================== 4 passed in 11.52s ==============================
```

Committed as `test(budget-api): cover monthly-summary cc_ids filter`.

## Concerns

None. The only deviations from the task brief's stated setup were the two
environment variables needed to get the existing conftest migration harness
running at all (`TEST_PG_PASSWORD`, `JWT_SECRET_KEY`) — these were required
by code that already existed before this task and are not part of what I
changed; future task sessions in this worktree will hit the same
`JWT_SECRET_KEY` requirement and should export it the same way.
