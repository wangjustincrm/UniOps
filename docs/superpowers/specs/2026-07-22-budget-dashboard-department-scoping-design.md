# Budget Dashboard — Department Scoping (Design)

**Date:** 2026-07-22
**Branch:** `feature/budget-dashboard-dept-scoping`
**Status:** Design approved, pending spec review → implementation plan

## Problem

A non-full-access viewer (e.g. a Department Manager) opening the Budget Dashboard
sees **company-wide** budget numbers, mislabelled as "My Department" — not their
own department's budget. Reported case: `yuwq@canadaroyalmilk.com` (市场部 /
Marketing dept_manager) sees data that is not Marketing's.

### Root cause

Department scoping exists **only in the frontend, and only for the cost-center
dropdown options** — never on the data itself.

1. `epms/src/pages/budget/BudgetDashboard.tsx` filters `visibleCCs` by
   `cc.department_id === user.department_id` (lines 77-79), but this only
   controls which options appear in the dropdown.
2. The data query defaults to `ccId = 'all'` (line 82, no effect ever changes
   it), so `summaryParams` omits `cost_center_id` (lines 101-104).
3. Backend endpoints do **not** scope by the caller. `budget-api`'s
   `/actuals/summary` and `/actuals/monthly-summary` mark `user` unused
   (`# noqa: ARG001`); with `cost_center_id=None` the CRUD **aggregates across
   all cost centers company-wide** (`budget-api/app/crud/balance.py:212`).
   `finance-api`'s `/gl/nc-*` endpoints discard the user (`_: CurrentUser`) and
   the export path is explicitly "Fail-open … aggregated across all cost
   centers".

This is both a **correctness bug** (wrong numbers shown) and a **data-exposure
issue** (any dept_manager can call the APIs directly and receive company-wide
financials). Because the endpoints **pre-aggregate across cost centers before
returning**, the frontend cannot un-mix departments from the response — so the
scoping *must* be applied server-side, before aggregation.

### Consumers (all 6 currently unscoped)

| Service      | Endpoint                              | Used by dashboard for            |
|--------------|---------------------------------------|----------------------------------|
| budget-api   | `GET /actuals/summary`                | Summary cards, over/near alerts  |
| budget-api   | `GET /actuals/monthly-summary`        | Monthly plan-vs-actual grid      |
| finance-api  | `GET /gl/nc-actuals-monthly`          | "NC posted" cell line + totals   |
| finance-api  | `GET /gl/nc-partner-monthly`          | Partner (客商) drill-down         |
| finance-api  | `GET /gl/nc-partner-vouchers`         | Voucher drill-down               |
| finance-api  | `GET /gl/budget-actual/partner-export`| XLSX export                      |

## Goals / Non-goals

**Goals**
- A non-full-access viewer sees only their **department's** budget/actuals,
  aggregated across all cost centers whose `department_id` = their department.
- The cost-center dropdown lets them narrow to a specific CC **within** their
  department.
- Server-side is authoritative — the frontend cannot be the security boundary.
- Robust: no hard failures / read anomalies. The page always renders.

**Non-goals**
- No change to what full-access roles can see (company-wide, unchanged).
- No change to the aggregation math, plan/actual/NC semantics, or the
  payroll/depreciation exclusion.
- No new per-user CC-level ACLs beyond "your department's cost centers".

## Key decisions (agreed)

1. **Default scope semantics** — a non-full-access viewer defaults to their
   **department aggregate** (all CCs where `department_id` = their department);
   the dropdown then lets them pick one specific CC.
2. **Single source of truth = backend.** The "who is full-access / which CCs are
   visible" logic moves out of the frontend and becomes server-authoritative.
3. **Local resolution, shared DB — no cross-service runtime call.** Both
   budget-api and finance-api resolve scope **locally from the shared DB**
   (`users.department_id`, `user_roles`, `cost_centers.department_id`). We do
   **not** have finance-api call budget-api for scope at request time — that
   would add a network failure mode that could blank the NC columns. The data
   source of truth is the shared tables (genuinely one source); the only
   duplicated *logic* is the full-access role constant, which is pinned by a
   test in each service.
4. **Soft filter, never 403.** Scoping is applied as a `cost_center_id IN (…)`
   filter. An omitted CC → aggregate the department's CCs. An explicit
   out-of-scope CC → **clamped back** to the department scope (not rejected).
   No endpoint returns 403 for this; the page always renders.
5. **Fail-closed.** If scope cannot be resolved (viewer has no `department_id`,
   or any resolution error), treat the visible-CC set as **empty** → the page
   shows the existing empty state ("No approved plan … in this scope"). Never
   fall back to company-wide.

## Scope resolver

A small resolver added to **each** of budget-api and finance-api
(`app/core/budget_scope.py`), reading the shared DB:

```
resolve_budget_scope(db, user_id: UUID, primary_role: str) -> BudgetScope
  BudgetScope = { full_access: bool, cost_center_ids: list[UUID] }
```

**full_access** (faithful port of the current frontend logic — no behavioural
change to who sees company-wide):

- `primary_role ∈ FULL_ACCESS_PRIMARY`
  = `{gm, opm, finance_manager, ap_clerk, system_admin, cfo, auditor,
     procurement_manager}`, **OR**
- the viewer's assigned roles (`user_roles`) intersect
  `FULL_ACCESS_ASSIGNED = {gm, opm, finance_manager, procurement_manager,
  finance_bp}`.

> Rationale for the two sets: mirrors `BudgetDashboard.tsx`'s
> `FULL_ACCESS_ROLES` (checked against the primary role) ∪ `SPECIAL_ROLE_CODES`
> (checked against primary∪additional) ∪ the `finance_bp`-assigned check.
> `procurement_manager` is added to the primary set because the frontend's
> SPECIAL check covers it via primary∪additional. `finance_bp` counts only as an
> **assigned** (`user_roles`) role, never as a bare primary role — same rule the
> rest of the codebase uses (approval-api `_post_holders`, finance-api `coa.py`).

**Non-full-access:** `cost_center_ids` = active cost centers where
`department_id = users.department_id` for `user_id`. If the viewer has no
`department_id`, or the department has no cost centers, the list is empty
(fail-closed).

`FULL_ACCESS_PRIMARY` / `FULL_ACCESS_ASSIGNED` are defined as module constants
in **both** services, with a test in each asserting the exact set, so the two
copies cannot silently drift.

### Applying the scope (both services)

Given a resolved `scope` and an optional requested `cost_center_id`:

- `full_access` → behave exactly as today (requested CC or company-wide).
- else if `cost_center_ids` is empty → **empty result** (fail-closed).
- else compute the **effective CC set**:
  - requested CC present **and** in `cost_center_ids` → `{requested}`
  - requested CC present but **not** in set → clamp to `cost_center_ids`
    (whole department; not 403)
  - requested CC absent → `cost_center_ids` (whole department)
- pass the effective set to the CRUD as an `allowed_cc_ids` / `cc_ids IN (…)`
  filter.

CRUD changes: `get_actuals_summary`, `get_monthly_actuals_summary` (budget-api)
and `nc_actuals_monthly`, `nc_partner_monthly`, `nc_partner_monthly_all`,
`nc_partner_vouchers` (finance-api) gain an optional `cc_ids: list[UUID] | None`
parameter. `None` = current behaviour (full access / unfiltered); a list adds
`WHERE cost_center_id IN (:cc_ids)`. An empty list short-circuits to an empty
response (no DB round-trip needed).

## Endpoint changes

### budget-api

- **New** `GET /actuals/scope` → `{ full_access: bool,
  cost_centers: [{ id, code, name, department_id }] }` for the caller. This is
  what the frontend consumes to populate the dropdown and the full-access flag.
  (For full-access callers, returns `full_access: true` plus all active CCs.)
- `GET /actuals/summary`, `GET /actuals/monthly-summary`: resolve scope, apply
  the effective-CC filter as above.

### finance-api

- `GET /gl/nc-actuals-monthly`, `/gl/nc-partner-monthly`,
  `/gl/nc-partner-vouchers`, `/gl/budget-actual/partner-export`: resolve scope
  locally, apply the effective-CC filter. The export additionally already calls
  budget-api for the plan side (`budget_client.fetch_monthly_summary`); once
  budget-api's `monthly-summary` is scoped, the plan side is scoped
  automatically — the NC side is scoped here.

> Note: `nc-partner-monthly` / `nc-partner-vouchers` are drill-downs that always
> receive a locked `cost_center_id` from the UI, but they are still scoped
> server-side so a hand-crafted request cannot read another department's CC.

## Frontend changes (`BudgetDashboard.tsx`)

- Remove `FULL_ACCESS_ROLES`, `SPECIAL_ROLE_CODES`, `isFinanceBpAssigned`,
  `isFullAccess`, and the local `visibleCCs` derivation.
- Add a `useActualsScope()` hook → `GET /actuals/scope`. Use its
  `cost_centers` for the dropdown and its `full_access` for the "Company-wide"
  vs "My Department" label.
- Always render the CC dropdown when the scope has ≥1 CC (drop the
  `visibleCCs.length > 1` gate that hid it for single-CC departments).
- Default `ccId` stays `'all'` — now correctly meaning "my department aggregate"
  server-side.
- Empty-scope viewers: the grid already shows a graceful empty state; add a
  short "no visible budget — contact your administrator" note when
  `scope.cost_centers` is empty and not full-access.

## Testing

Respect the shared-test-DB serial discipline (run one suite at a time; override
`POSTGRES_*` to the local docker DB).

**budget-api**
- resolver: each role class → correct `full_access` / department CC set; no
  department → empty; assigned-only `finance_bp` → full access; a full-access
  role set assertion test (pins `FULL_ACCESS_PRIMARY` / `FULL_ACCESS_ASSIGNED`).
- `/actuals/summary` & `/actuals/monthly-summary`: dept viewer omitting CC →
  only their department's CCs aggregated; explicit out-of-scope CC → clamped
  (not 403); no-department viewer → empty; full-access → unchanged company-wide.
- `/actuals/scope`: shape + correct CC list per role.

**finance-api**
- same role-set assertion test (pins the duplicated constants).
- each `/gl` endpoint: dept viewer omitting CC → department-only aggregate;
  out-of-scope CC → clamped; no-department → empty; full-access → unchanged.

**frontend**
- typecheck clean (`tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`).

## Rollout notes

- No DB migration — reads existing `users`, `user_roles`, `cost_centers`.
- Touches budget-api, finance-api, and epms (frontend) images. Per release
  discipline: merge branch → main, rebuild **all 15** images at one sha, verify
  production TAG before deploy. Not pushed/deployed without explicit user go.
- Backwards-safe: full-access users are unaffected; the only behavioural change
  is that non-full-access users now see their department instead of the company.
