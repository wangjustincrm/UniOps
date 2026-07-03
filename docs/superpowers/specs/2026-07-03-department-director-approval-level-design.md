# Optional Approval Levels — Department Supervisor (主管) & Director (总监)

**Date:** 2026-07-03
**Status:** Approved (design), pending implementation plan
**Scope:** approval-api (engine), epms-api (users column + config + access_scope + permission matrix), identity-api (User mirror), epms frontend (Timeline + admin config), alembic migrations

---

## 1. Problem

The approval engine stores one flat, company-wide workflow per doc_type
(`CompanyConfig.workflow_defs[doc_type]`); there is no per-department or
per-requester variation. Two optional review levels are needed:

- **Supervisor (主管)** — inside a department, between the **Requester** and the
  **Department Manager**. A department may have several supervisors, each
  overseeing a *group* of requesters. Optional, enabled per-department.
- **Director (总监)** — between the **Department Manager** and **GM/OPM**. One
  director may cover several departments (Marketing, E-commerce, Sales share
  one). Optional, enabled per-department.

A dormant, **never-wired** `dept_supervisor_enabled` config field + admin toggle
already exist (see §9) — this design finally gives the supervisor layer real
behaviour and adds the director layer.

**Secondary bug fixed here:** the Approval Timeline renders the *static*
`workflow_defs` list, so runtime-injected over-budget steps
(`ob_finance_manager`/`ob_gm_or_opm`) are invisible and indices can misalign.
The same effective-workflow refactor that makes Supervisor/Director visible fixes
this (see §6).

## 2. Approval chains (positions locked)

```
PR:  Requester → [Supervisor]? → Dept Manager → [Director]? → GM/OPM
PA:                              Dept Manager → [Director]? → GM/OPM → Finance BP → Finance Mgr
```

- Supervisor applies to **PR only** (it is a pre-review of the requisition; PAs
  are raised by AP clerks and have no requester-side pre-review semantics).
- Director applies to **PR and PA**.
- `[X]?` = optional node: always present in `workflow_defs`, auto-skipped
  per-document when not applicable (VMS `quality_manager` pattern).

`workflow_defs`:
```
pr: [supervisor, dept_manager, director, gm_or_opm]
pa: [dept_manager, director, gm_or_opm, finance_bp, finance_mgr]
```

## 3. Key Decisions (locked)

| # | Decision |
|---|----------|
| D1 | Supervisor and Director are **two independent optional levels** at different positions. Supervisor is *below* the manager, Director is *above*. They compose: a PR can run `Supervisor → Manager → Director → GM/OPM`. |
| D2 | **Director grouping = department-level.** Config map `dept_director_mapping = {department_id: director_user_id}` on `company_config`. Presence of a department key = enabled. Many departments → same director id is allowed. |
| D3 | **Supervisor grouping = requester-level.** Each requester points to their supervisor via a new **`users.supervisor_id`** column (nullable self-FK). One supervisor → many requesters, one manager → many supervisors, all expressed naturally. |
| D4 | **Supervisor master switch = the existing `dept_supervisor_enabled` {dept_id: bool}.** The supervisor step is active for a PR iff the requester's department is enabled **AND** the requester has a `supervisor_id`. This repurposes the dormant field/UI meaningfully. |
| D5 | Routing (who approves) follows the requester's identity via the existing `routing_uid` chain (originating PR creator). Director resolves from `dept_director_mapping[requester_dept]`; Supervisor resolves from `requester.supervisor_id`. |
| D6 | Visibility/permission via two new restricted JWT roles, **`supervisor`** and **`director`**, in the Access Control Matrix, plus `access_scope` branches. Role = "can see"; the mapping/column = "is the supervisor/director of whom". A user acquires the role *derived* from being someone's supervisor / a department's director (parallel to how GM/OPM are derived from `role_management`). |
| D7 | Optional steps are **real nodes**, auto-skipped per-document. The engine's look-ahead handles mid-chain skips today; **submit is extended to skip leading inactive steps** (Supervisor can be step 0) — see §5.3. |
| D8 | The Timeline renders the **effective workflow** (static + injected + skip state) via one shared assembly function used by both execute and read paths; this also fixes the over-budget invisibility bug (§6). |

## 4. Data model & configuration

### 4.1 `users.supervisor_id` (schema owned by epms-api alembic)

```
supervisor_id : UUID NULL  REFERENCES users(id) ON DELETE SET NULL
```

- Migration: **epms-api/alembic** (the `users` table schema is owned there;
  identity-api's `User` is a documented mirror — see its model docstring).
- Model mirrors to update (verbatim add of the column):
  `epms-api/app/models/user.py`, `identity-api/app/models/user.py`,
  `approval-api/app/models/user.py` (engine routing reads it).
- No FK to a "supervisor role" — anyone can be pointed to as a supervisor; the
  `supervisor` *permission role* is derived (§4.4).

### 4.2 `company_config.dept_director_mapping` (epms-api owns the column)

```
dept_director_mapping : JSONB NOT NULL DEFAULT '{}'   -- {department_id: director_user_id}
```

- `epms-api`: `models/config.py` (new `Mapped[dict]`), `schemas/config.py`
  (read + update, parallel to `dept_supervisor_enabled` at lines 195/249),
  `crud/config.py` (create default ~line 262, updatable allowlist ~line 309).
- `approval-api/app/models/config.py`: add the same read-only mirror field.
- Alembic (epms-api): add the column, `server_default="{}"`.

### 4.3 Reused `dept_supervisor_enabled`

Already present end-to-end (column, schema, crud allowlist, company store,
AdminPanel toggle at `epms/src/pages/admin/AdminPanel.tsx:1743`). No schema
change — we wire it into the engine (§5) and clarify the admin copy.

## 5. Engine (`approval-api/app/crud/engine.py`)

### 5.1 Resolution helpers

- `_resolve_director(db, routing_uid, dept_director_mapping) -> uuid | None`
  — requester's `department_id` → `dept_director_mapping.get(dept)`.
- `_resolve_supervisor(db, routing_uid, dept_supervisor_enabled) -> uuid | None`
  — requester's `department_id`; if `dept_supervisor_enabled.get(dept)` is
  truthy, return the requester's `supervisor_id`; else None.

Both load their config maps from `cfg` in `execute_action`, alongside
`dept_gm_opm`.

### 5.2 Wire into the four existing role-dispatch sites

Add `supervisor` and `director` branches wherever `gm_or_opm` is handled:
- `_create_approve_task`: set `assigned_user_id` from the resolver,
  `assigned_role = "supervisor" | "director"`.
- `_actor_can_approve`: authorized iff `actor_id == resolver(...)`.
- `_build_role_map` / `_holds`: resolve the same way so same-approver auto-skip
  works (e.g. a manager who is also a director collapses the redundant step).

### 5.3 Conditional skip — including leading step at submit

Skip rule (a step is skipped when it has **no valid, active assignee**):
- `supervisor` step skipped ⇔ `_resolve_supervisor(...)` yields no **active**
  user — i.e. dept disabled, requester has no `supervisor_id`, OR the referenced
  supervisor is inactive / no longer exists.
- `director` step skipped ⇔ `_resolve_director(...)` yields no **active** user —
  i.e. department not mapped, OR the mapped director is inactive / missing.

The resolvers therefore validate the resolved user is active
(`User.is_active` and exists); a stale/left assignee resolves to `None` and the
step is skipped rather than blocking the document. Skips caused by a
missing/inactive *configured* assignee emit a distinct warning-flavoured audit
event (see below) so admins can spot the misconfiguration.

Two code paths must honour this:
1. **`approve` look-ahead loop** — extend `_should_skip_step` (today VMS-only) to
   take the resolved supervisor/director ids (computed once in the `approve`
   branch) and skip accordingly, emitting an audit `ApprovalEvent`. Two skip
   reasons are distinguished in the `comment`:
   - normal opt-out — `"Auto-skipped (no Supervisor assigned)"` /
     `"Auto-skipped (department has no Director)"`;
   - misconfiguration — `"Auto-skipped ⚠ configured Supervisor is inactive/missing"`
     / `"Auto-skipped ⚠ configured Director is inactive/missing"`, so a
     left/invalid assignee is surfaced rather than silently equal to "not set".
2. **`submit`** — Supervisor can be step 0, and `submit` currently creates step 0
   unconditionally. Add `find_first_active_step(workflow, doc, resolvers)`:
   starting at 0, skip leading inactive steps (emit skip events for them), set
   `approval_step_idx` to the first active step, and create *that* task. This is
   the one genuinely new engine mechanic beyond the existing look-ahead.

### 5.4 Effective workflow assembly (shared)

Extract the workflow-assembly currently inline in `execute_action` (base
`_get_workflow` + over-budget prepend) into
`build_effective_workflow(db, doc_type, doc, cfg) -> list[dict]`. `execute_action`
calls it (no behaviour change); the read path (§6) calls the same function so the
node list can never drift from what the engine executed. Over-budget `ob_` steps
remain prepended to the front, so `[ob_fm, ob_gm, supervisor, dept_manager,
director, gm_or_opm]` composes correctly.

## 6. Timeline = effective workflow (bonus over-budget fix)

Root cause: `buildWorkflowSteps` in the detail pages builds nodes from static
`config.workflow_defs[doc_type]` (`PoDetailPage.tsx:593`,
`PrDetailPage.tsx` ~L264), so injected steps have no node.

Fix:
1. Backend exposes the effective ordered steps for a document — extend the
   events endpoint response (`GET /{doc}/{id}/events`) with
   `workflow_steps: [{id, role, label}]` from `build_effective_workflow`.
   Existing `ApprovalEvent.step_idx` values already index the injected workflow,
   so events map onto this list 1:1.
2. Frontend `buildWorkflowSteps` renders from returned `workflow_steps` instead
   of `config.workflow_defs[...]`. Supervisor, Director, and over-budget steps
   all appear; auto-skip events render with the existing `skipped` visual
   (dashed circle + SkipForward, already in `ApprovalTimeline.tsx`).

Applies to PR, PO, PA detail pages (all three share the pattern).

## 7. Access control (`epms-api`)

**Permission matrix** — `crud/config.py` `_DEFAULT_ROLE_PERMISSIONS` (~line 212):
- `"supervisor": {view_pr}` (supervisor only reviews PRs).
- `"director": {view_pr, view_pa}`.

**Visibility** — `core/access_scope.py`:
- Add `"supervisor"` and `"director"` to `_RESTRICTED_ROLES` (line 30).
- `_effective_role_codes`: derive the roles for a user —
  - `"director"` if `uid in cfg.dept_director_mapping.values()`;
  - `"supervisor"` if any user has `supervisor_id == uid`
    (`exists(select(User.id).where(User.supervisor_id == uid))`).
- New `visible_pr_subquery` branches:
  - `director`: PRs whose requester is in the director's departments **OR** whose
    cost center is in those departments — the same union the `dept_manager`
    branch uses (access_scope.py:161-168), keeping routing and visibility aligned
    (avoids the PR-20260620-0001 404 class of bug). Helper `_director_dept_ids`
    parallels `_mapped_dept_ids`.
  - `supervisor`: PRs created by the supervisor's direct reports —
    `PurchaseRequest.created_by IN (select User.id where User.supervisor_id == me)`.
- PO/PA visibility: Director inherits via the existing non-requester path (POs
  linked to visible PRs; `is_pa_visible` via `po_subq`). Supervisor is PR-only —
  no PO/PA visibility needed.

## 8. Admin UI (`epms` / `portal`)

- **Supervisor:** keep the existing per-department enable toggle
  (`dept_supervisor_enabled`); add supervisor assignment on the **user** record
  (User edit in AdminPanel / `services/users.ts`): a "Supervisor" picker
  (user-directory search) writing `users.supervisor_id`.
- **Director:** new "Department Directors" section beside the GM/OPM department
  mapping — pick department, pick director (directory search), writes
  `dept_director_mapping`. Populate Marketing / E-commerce / Sales initially.
- Clarify supervisor toggle copy: "Requires each requester in this department to
  have a Supervisor assigned; requesters without one route straight to the
  Manager."

## 9. Status of the pre-existing `dept_supervisor` scaffolding (audit result)

Confirmed by a full-repo scan (2026-07-03):

| Layer | State |
|-------|-------|
| DB column `dept_supervisor_enabled` | ✅ exists (migration `d4e5f6a7b8c9`) |
| epms-api model / schema / crud | ✅ present (read + write) |
| Admin Panel UI (per-dept toggle) | ✅ fully built (`AdminPanel.tsx:1743-1761`) |
| epms company store / config service | ✅ field present |
| **Approval engine** | ❌ **zero handling** — `grep supervisor` over `approval-api` returns nothing; no supervisor node in `_WORKFLOW_DEFAULTS` |
| Permission matrix / access_scope | ❌ no `supervisor` role |
| **"Who is the supervisor"** | ❌ **never defined** — `dept_supervisor_enabled` is only a bool map; no assignment mechanism existed |

Verdict: it was a **live-but-inert toggle** — saving it did nothing, and the
design was incomplete (no way to name a supervisor). This spec completes it:
`users.supervisor_id` supplies the missing "who", and §5 supplies the missing
engine behaviour.

## 10. Data flow examples

```
Marketing requester with supervisor S, dept has supervisor enabled + director D:
  submit → first active step: supervisor (S has task)
  S approve → dept_manager (Marketing mgr)
  mgr approve → director: _resolve_director(Marketing)=D → D task
  D approve → gm_or_opm (via dept_gm_opm_mapping[Marketing])
  approve → PR approved → create_po

Finance requester, dept supervisor disabled, no director:
  submit → step 0 supervisor: _resolve_supervisor=None → SKIP (audit event)
        → first active step: dept_manager (task)
  mgr approve → step director: _resolve_director(Finance)=None → SKIP (audit)
        → gm_or_opm → approved
```

## 11. Edge cases

- **Dept supervisor-enabled but requester has no `supervisor_id`:** supervisor
  step skips; requester routes straight to the manager (documented behaviour, not
  an error).
- **`supervisor_id` / mapped director points to an inactive or non-existent
  user:** **skip the step** and continue, emitting the warning-flavoured audit
  event (§5.3) so the misconfiguration is visible in the Timeline / audit trail.
  The document is never blocked by a stale assignee. (This differs from the
  `dept_manager` step, which hard-fails — the manager is mandatory; supervisor
  and director are optional levels, so a broken assignee degrades gracefully to
  "level skipped".)
- **Supervisor/Director is also the Manager/GM:** same-approver auto-skip
  collapses the redundant step.
- **In-flight documents:** engine changes only affect newly-advanced steps; no
  retroactive insertion. Consistent with prior engine changes — no back-fill.
- **Over-budget + both optional steps:** effective workflow composes as
  `[ob_fm, ob_gm, supervisor, dept_manager, director, gm_or_opm]`.

## 12. Testing

**approval-api** — `tests/test_engine_optional_levels.py`:
1. Supervisor active: submit lands on the assigned supervisor → then manager.
2. Supervisor skipped at submit (dept disabled OR no `supervisor_id`): first task
   is the manager; a skip event is recorded.
3. Director active: manager approve → director → gm_or_opm (PR and PA).
4. Director skipped: chain behaves as `manager → gm_or_opm`.
5. Inactive/missing assignee (supervisor_id → inactive user; mapped director
   inactive): step skips with the warning-flavoured audit comment, document is
   not blocked.
6. Same-approver collapse (supervisor==manager, director==gm).
7. Authorization: non-assigned user cannot approve a supervisor/director step.
   (`conftest._ENGINE_TABLES` already includes `PurchaseRequest`.)

**epms-api**:
- `access_scope`: a supervisor sees only their direct reports' PRs; a director
  sees their mapped departments' PR/PA (no 404 on open); neither sees others'.
- permission-matrix: `supervisor→view_pr`, `director→view_pr,view_pa`.

**epms frontend**:
- Timeline renders Supervisor + Director nodes (awaiting/approved/skipped) from
  effective workflow, and renders over-budget injected steps (regression for the
  fixed bug).

## 13. Affected files (summary)

| Area | Files |
|------|-------|
| users column | epms-api/alembic (new migration), `epms-api/app/models/user.py`, `identity-api/app/models/user.py`, `approval-api/app/models/user.py` |
| config column | `epms-api/app/models/config.py`, `schemas/config.py`, `crud/config.py`, new alembic migration; `approval-api/app/models/config.py` (mirror) |
| Engine | `approval-api/app/crud/engine.py` (supervisor/director resolvers, skip-at-submit, `build_effective_workflow`) |
| Access control | `epms-api/app/crud/config.py` (`_DEFAULT_ROLE_PERMISSIONS`), `epms-api/app/core/access_scope.py` |
| Timeline read | document events endpoint(s) → effective `workflow_steps` |
| Frontend | `epms/src/pages/{pr,po,pa}/*DetailPage.tsx` (`buildWorkflowSteps`), `epms/src/pages/admin/AdminPanel.tsx` (director section + supervisor assignment), `epms/src/services/users.ts` |
| Tests | `approval-api/tests/test_engine_optional_levels.py`, epms-api access_scope + permission tests, frontend Timeline checks |
