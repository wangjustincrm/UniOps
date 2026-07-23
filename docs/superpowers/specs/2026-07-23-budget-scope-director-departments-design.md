# Budget Scope — Director Sees the Departments They Direct (Design)

**Date:** 2026-07-23
**Branch:** `feature/budget-scope-director-depts`
**Base:** main `9aa3449`
**Amends:** `docs/superpowers/specs/2026-07-22-budget-dashboard-department-scoping-design.md`
**Status:** Design approved, pending spec review → implementation plan

## Problem

The department scoping shipped in `9aa3449` resolves a non-full-access viewer's
visible cost centers from **one** department — `users.department_id`. But a
**Director is assigned per department** and can direct **several** departments,
so a Director sees only the single department they happen to belong to and is
missing the others they are responsible for.

### Confirmed with real data (local dev = prod snapshot)

`cox@canadaroyalmilk.com`:

| Fact | Value |
|---|---|
| `users.role` (primary) | `dept_manager` |
| additional roles (`user_roles`) | `director` |
| `users.department_id` (own) | `0111 Marketing` |
| `approval_dept_routing.director_user_id` → her | `0110 Sales`, `0111 Marketing`, `0113 E-COM` |
| Cost centers of those departments | `SELL-0110`, `SELL-0111`, `SELL-0113` |

Under the shipped code she is (correctly) **not** full-access, and her visible set
resolves to her own department only → **`SELL-0111` alone**. She should see all
three.

## Goals / Non-goals

**Goals**
- A viewer's visible departments = **their own department ∪ every department they
  are the configured Director of**.
- The rule is **role-agnostic**: it keys on the `approval_dept_routing.director_user_id`
  user-id mapping, never on a role string. It must work whether `director` is the
  primary role, an additional role, or absent entirely.

**Non-goals**
- No change to the full-access predicate. `director` is deliberately **not** added
  to `FULL_ACCESS_PRIMARY`/`FULL_ACCESS_ASSIGNED` — a Director must see their
  departments, not the company.
- No change to soft-filter/never-403, fail-closed, the six scoped endpoints, the
  CRUD `cc_ids` contract, or the approval engine's own Director routing.
- No generalisation to other "manages several departments" relationships —
  `approval_dept_routing.director_user_id` is currently the only multi-department
  mapping (YAGNI).

## Design

### 1. Resolver: one department → a set of departments

In `app/core/budget_scope.py` (kept **byte-identical** across budget-api and
finance-api, as today), the non-full-access branch changes from a single
department lookup to a union:

```
depts = {users.department_id  for this user}                      -- own dept (may be NULL)
      ∪ {dept_id FROM approval_dept_routing
         WHERE director_user_id = :uid}                           -- departments they direct

cost_center_ids = active cost_centers WHERE department_id = ANY(depts)
```

- `approval_dept_routing` lives in the same shared DB as `users` / `cost_centers`,
  so both services reach it with the existing raw-SQL pattern (`CAST(:x AS uuid)`).
- A plain employee's set degenerates to their single own department — **behaviour
  unchanged** for everyone who directs nothing.
- NULL own department is simply absent from the union; a Director with no own
  department still gets the departments they direct.
- **Fail-closed unchanged:** an empty department set → empty `cost_center_ids` →
  empty result, never company-wide.

### 2. Role-agnostic by construction

"Which departments do I direct" is answered solely by the user-id mapping in
`approval_dept_routing` (set per department on the Approval Routing admin page).
The resolver reads no role string for this. Consequently all of these behave
identically:

- primary role `director`
- primary role `dept_manager` + additional role `director` ← **the live production shape**
- no `director` role at all, but assigned as Director in Approval Routing

Conversely, holding a `director` role while being assigned to no department
yields only the own department — the routing table is the sole authority on
which departments a Director is responsible for.

### 3. Frontend: scope label only

`BudgetDashboard.tsx` currently labels a non-full-access viewer's scope
"My Department". A Director now legitimately spans several. Change the label to
**"My Departments"** when the returned scope covers more than one department,
otherwise keep "My Department". Nothing else changes: the cost-center dropdown is
already rendered from whatever `GET /actuals/scope` returns, so the extra cost
centers appear automatically.

### 4. Testing

Both services (each pins its own copy):

- **The live production shape** — primary `dept_manager` + additional `director`,
  own dept A, directing A/B/C → visible set = {A, B, C}. This case is written
  explicitly so nobody later "optimises" the resolver back to matching role strings.
- Director whose own department is **not** among the directed ones → union
  contains own dept plus all directed ones (no dept dropped).
- Director with **no** own department → still gets the directed departments (not
  fail-closed to empty).
- Plain employee directing nothing → unchanged, own department only.
- Nobody's department resolvable → empty (fail-closed), never company-wide.
- `director` is still absent from both full-access role sets (existing pinning
  tests continue to guard this).

## Rollout

- **No DB migration** — one additional read of an existing table.
- Touches budget-api, finance-api, and the epms frontend label. Per release
  discipline: merge to main → rebuild **all 15** images at one sha → verify the
  production TAG before deploying. Not pushed or deployed without explicit user go.
- Backwards-safe: everyone who directs no department keeps today's behaviour
  exactly; the only change is additive visibility for configured Directors.
