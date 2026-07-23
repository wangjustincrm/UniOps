# Task 8 Report: Routing parity verifier + phase 3 release notes

## Status: DONE (with one flagged, investigated, non-blocking finding — see Concerns)

## Commit
- `d0c0381` — "test(approval): routing parity verifier + phase 3 release notes"
  - `approval-api/scripts/verify_routing_parity.py` (new)
  - `docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md` (new)

## Parity verifier output (dev)

```
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```

```
DIFF dept=0100 doc=pa step=finance_bp old=['907147d2-aabb-4fba-83e0-9bee9d01a33e'] new=['907147d2-aabb-4fba-83e0-9bee9d01a33e', 'f933fd52-bdfa-433d-b4c1-9226075bc7b0']
... (identical shape, one DIFF per active department — 12 total)
PARITY FAILED: 12 divergence(s) across 12 depts x 3 doc types   [exit 1]
```

**Root-caused, not papered over.** All 12 diffs are the same single cause: user
Yuping Huang (`f933fd52-...`) has **primary** `users.role = 'finance_bp'` (set
2026-06-30, unrelated to this branch), but was never in the old
`company_config.role_management.finance_bp_user_ids` curated list. The new
`_post_holders()` (approval-api/app/crud/workflow.py:33-46) unions
`users.role` ∪ `user_roles` for **every** post code including `finance_bp` —
this is Task 4's **deliberate, spec'd, reviewed-and-approved** design (spec
line 713 `codes = list(_POST_CODES) + ["finance_bp"]`; docstring "A post can
be held as a PRIMARY role or an ADDITIONAL role — both count"; existing
tests are load-bearing on this union per Task 4's own report). It is a
one-directional widening (adds an eligible approver, never removes one) and
was never covered by Task 3's "5 singleton posts match role_management"
parity check (finance_bp is explicitly non-singleton, list-type). This is
the first end-to-end check to exercise it. All other migrated getters
(`gm`/`opm`/`vendor_manager`/`finance_manager`/`procurement_manager`,
`gm_or_opm` dept resolution, `director`, `supervisor_enabled`) show **zero**
divergence across all 12 depts x 3 doc types.

Documented in the release notes' "验收阶段发现...一处行为变化" section with a
recommendation to cross-check prod's finance_bp-primary-role users against
the old curated list before go-live. Did not alter the verifier to mask this
— it is a real, reproducible, and now-permanent property of the new getters
given current dev data, not a seed/migration defect.

## Full regression

| Suite | Result | Expected |
|---|---|---|
| approval-api pytest | **37 passed** | 37+ |
| identity-api pytest | **20 passed** | 20 |
| expense-api pytest (docker exec) | **83 passed** | 83 |
| vms-api pytest (docker exec) | **175 passed** | 175 |
| portal tsc --noEmit | **0 errors** | 0 |
| epms tsc --noEmit (`grep -c "error TS"`) | **69** | 69 (baseline, unchanged) |

finance-api and epms-api pytest skipped per instructions (controller already
ran both on this exact code: finance 201 passed; epms failure set identical
to baseline minus the 4 deleted temp-assignment tests).

## Release notes

`docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md`,
modelled on the phase 1 release doc. Covers: 3 migrations (approval
`0001_approval_routing` first-ever + `migrate-prod.sh` confirmed updated,
identity `0003_post_role_singleton`, epms `z4_drop_temp_assignments`);
mandatory seed step with exact command, order (migrate → seed → parity →
up), and the "no-seed" failure mode stated plainly (every non-dept_manager
step fails to resolve an approver — worse than phase 1); seed's reassign
behavior + WARNING lines + when it is unsafe to re-run; zero new
infrastructure (verified: approval-api's prod compose block has no
`ALLOWED_ORIGINS`, `Caddyfile:52` confirms no subdomain, `APPROVAL_ENGINE_URL`
already present in both dev/prod compose for epms-api — verified line
numbers cited in the doc); rollback (four JSONB columns untouched;
temp_assignments downgrade recreates empty, table was 0 rows in prod); the
deliberate `reconstruct.py` exemption; concrete post-deploy verification
commands (epms-gateway script asserting departments=12 and
supervisor_on=0, plus a browser checklist).

## Concerns

- The finance_bp parity divergence above is real and will reproduce
  identically in prod if any user's primary role is `finance_bp` without
  being in the old curated list. It is not a code defect from this branch's
  work, but it is new information the user should have before running the
  prod seed — flagged prominently in the release notes; recommend a manual
  prod cross-check before go-live per the doc's wording.
- Everything else (migrations, seed order, gateway plumbing, rollback,
  regression) verified directly against the repo, not taken on faith.

## Follow-up fix (2026-07-15): the finance_bp diff above was a real over-grant, now fixed

The 12 DIFFs flagged above were re-triaged as an actual bug, not an
acceptable one-directional widening: `finance_bp` is exempt from
identity's singleton index specifically *because* it's a multi-holder job
function, not a company-unique position like the other five post codes.
Reading `users.role` for it conflates "holds the job function" with "is the
assigned approver" — Yuping Huang's primary role is `finance_bp` but she was
never added to `role_management.finance_bp_user_ids`, so she should never
have resolved as a PA approver.

### Status: DONE

### Root cause
`approval-api/app/crud/workflow.py::_post_holders` unioned `users.role` ∪
`user_roles` for **all six** post codes including `finance_bp`. Correct for
the five singleton posts (gm/opm/vendor_manager/finance_manager/
procurement_manager — identity enforces one holder each via
`0003_post_role_singleton` + the cross-table 409 in
`PUT /authz/users/{id}/roles`, so holding the primary role IS holding the
post). Wrong for `finance_bp`, which must resolve from the curated
ASSIGNMENT (`user_roles`) only.

### Changes
- `approval-api/app/crud/workflow.py` — `_post_holders()`: split the SQL so
  `users.role` is matched against `_POST_CODES` (five singletons) only,
  while `user_roles` continues to be matched against
  `_POST_CODES + ["finance_bp"]`. Added an explicit docstring explaining the
  singleton-position vs. multi-holder-job-function distinction and citing
  the real prod case (Yuping Huang) as the motivating example.
- `approval-api/scripts/seed_routing.py` — removed the
  `if primary == "finance_bp": continue` skip from the finance_bp loop. Since
  finance_bp is now resolved from `user_roles` only, skipping the insert
  when an assignee's primary role happens to also be `finance_bp` would make
  that assignee vanish as an approver entirely. The `ON CONFLICT (user_id,
  role_code) DO NOTHING` still makes repeat runs idempotent. The five
  singletons' skip-if-primary-matches logic and stale-holder
  reassignment were left untouched, per instructions.
- Tests added:
  - `test_routing_adapters.py::test_finance_bp_primary_role_alone_is_not_included`
    — user with `users.role='finance_bp'` and no `user_roles` row must NOT
    appear in `finance_bp_user_ids`. Reproduces the exact prod situation;
    fails against the pre-fix code.
  - `test_seed_routing.py::test_finance_bp_row_always_written_even_when_primary_role_matches`
    — an assigned finance_bp whose primary role is also `finance_bp` must
    still get a `user_roles` row written. Guards the seed script change.
  - Kept `test_post_from_primary_role_is_included` (gm) unchanged as the
    contrasting singleton-post case — the two tests together pin the
    distinction.

### Verification (foreground)

```
cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=*** ./.venv/Scripts/python -m pytest tests -q
```
```
39 passed in 18.78s
```
(37 pre-existing + 2 new; no regressions.)

```
docker exec uniops_approval_api python -m scripts.seed_routing
```
```
seed_routing done: {'user_roles': 0, 'dept_rows': 0, 'backups': 0, 'reassigned': 0}
```
No-op re-run, as expected — the live-mounted container picked up the source change without a rebuild.

```
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```
```
PARITY OK (12 depts x 3 doc types)
```
Zero DIFFs — the 12 diffs from the original Task 8 run are gone. Yuping Huang
no longer resolves as a `finance_bp` approver; prod's only real assignee
(`907147d2-...`, PM test) is unaffected since she's read from `user_roles`
which already carries her via the original curated
`role_management.finance_bp_user_ids` seed.

### Commit
`fix(approval): finance_bp approvers come from the assignment only, not from holding the job function`

## Final-review fixes (2026-07-15)

Three findings from the controller's final review, all confirmed against live
code/data.

### Status: DONE

### FIX 1 — finance_bp over-grant in two more consumers

**(a) `finance-api/app/api/v1/coa.py::_can_manage`** — the same doctrine as
`workflow.py::_post_holders`: `finance_manager` (singleton post) may resolve
from `users.role` ∪ `user_roles`; `finance_bp` (job function) must resolve
from `user_roles` only. Rewrote `_can_manage` to check `finance_manager` via
the existing `_user_role_codes` union, then check `finance_bp` with a direct
`SELECT 1 FROM user_roles WHERE user_id = :u AND role_code = 'finance_bp'`
query — no longer reads the primary role for finance_bp.
Added `finance-api/tests/test_coa.py::test_coa_primary_role_finance_bp_without_assignment_denied`
— a user whose JWT/primary role is `finance_bp` with no `user_roles` row
gets `can_manage: false` from `GET /coa/permissions` and `403` from a
mapping-write endpoint. Confirmed it fails against the pre-fix code
(re-ran before editing: pre-fix code returned `can_manage: true`).

**(b) `epms/src/pages/budget/BudgetDashboard.tsx`** — two separate leaks, both
fixed:
  - `FULL_ACCESS_ROLES` included `'finance_bp'` checked directly against
    `user.role` (the primary role from the auth store) — removed it.
  - `SPECIAL_ROLE_CODES` included `'finance_bp'` checked against `myRoles`
    (from `/config/me/permissions`, which is primary ∪ additional) — removed
    it and added a separate `isFinanceBpAssigned` computed from the
    ADDITIONAL-roles-only `GET /config/user-roles` proxy (same one Portal's
    Access Control page uses; returns `{user_roles: {uid: [codes]}}`).
    `isFullAccess` now ORs in `isFinanceBpAssigned` instead of trusting
    myRoles/user.role for this code. Added `configService.getUserRoles()`
    (`epms/src/services/config.ts`) and `useUserRoles()`
    (`epms/src/hooks/useConfig.ts`) to support it. Comments in the file state
    why finance_bp is excluded from both role-only sets.
  - No test harness exists for this component (no epms frontend test runner
    wired up for pages); verified via `tsc` only, matching the file's
    existing testing posture.

### FIX 2 — singleton invariant unguarded write path in epms-api

`epms-api/app/api/v1/users.py`: added `_POST_ROLES` (the five singleton
codes, finance_bp deliberately excluded) and `_post_conflict(db, role,
exclude_user_id=None)`, mirroring identity-api's `authz.py::_post_conflict`
query shape (primary-role hit first, then `user_roles` hit), excluding the
user being edited so re-saving the current holder is idempotent. Wired into
both `update_user` (PATCH `/users/{id}`) and `create_user` (POST `/users`) —
both now 409 when the requested role is a singleton post already held by
someone else. The CSV bulk `/users/import` path also sets `.role` directly
and was **not** touched — see Concerns.

Tests added to `epms-api/tests/test_user_supervisor_assignment.py` (the file
already had the admin_client + `_make_user` PATCH-role test harness):
- `test_patch_role_to_held_singleton_post_rejected_409` — PATCHing a second
  user to `gm` when one already exists → 409.
- `test_patch_role_resave_current_holder_is_idempotent` — re-saving the
  current holder's own post role → 200 (exclude-self works).
- `test_patch_role_to_finance_bp_allows_multiple_holders` — finance_bp is
  never guarded → 200 with two primary-role holders.
- `test_create_user_with_held_singleton_post_rejected_409` — POST `/users`
  with an already-held singleton role → 409.

Hardening in `approval-api/app/crud/workflow.py::_post_holders`: added
`ORDER BY 1, 2` to the UNION ALL query (deterministic `[0]` selection in
`get_role_management` if the invariant is ever broken) and a
`logger.warning(...)` when any of the five singleton codes resolves to more
than one holder, naming the role and listing the holder ids.

### FIX 3 — z4_drop_temp_assignments removed from this release

Confirmed the finding: `epms-api/app/api/v1/config.py::_full_response` calls
`list_temp_assignments`, backing `GET /api/v1/config` (used everywhere via
`useConfig`) — dropping the table at migrate time while the OLD container
still serves until `up` would 500 every config request company-wide for the
whole migrate→seed→parity→up window.

Exact sequence run (dev DB was at `z4` / head going in):
```
docker exec uniops_epms_api alembic current      # z4_drop_temp_assignments (head)
docker exec uniops_epms_api alembic downgrade -1  # -> z3_add_created_by_to_tasks; recreates empty temp_assignments
rm /c/Project/uniops/epms-api/alembic/versions/z4_drop_temp_assignments.py
docker exec uniops_epms_api alembic heads          # z3_add_created_by_to_tasks (head) — single head
docker exec uniops_epms_api alembic upgrade head   # no-op, already at head — clean
```
Grepped for other references to `temp_assignments` in epms-api after the
delete: only the original creation migration
(`d4e5f6a7b8c9_sprint4_company_config.py`) remains, as expected — all Task 7
reading-code deletions (UI/endpoints/model/schema/tests) stay in place per
instructions.

Release notes (`docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md`)
updated:
- Migration count 3 → 2 (approval `0001_approval_routing`, identity
  `0003_post_role_singleton`); explicit note that epms has no migration this
  release and z4 was removed.
- Added a "下个发布" row: `temp_assignments` stays as an empty table, safe to
  drop next release once this release's code (which already stopped
  reading it) is live.
- Added a pre-flight check step (both as prose and as a runnable
  `docker compose run --rm epms-api python -c "..."` snippet using
  `app.db.session.engine`, since prod's DB is an external server, not a
  compose service — no `psql` in the container) for the
  `user_roles` duplicate-singleton-post query, run and confirmed working
  against dev (`dup singleton posts: []`).
- Rewrote the rollback section's `temp_assignments` bullet — it's no longer
  touched by this release's migration at all.

### Verification (foreground, all run and awaited before this report)

```
cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=*** ./.venv/Scripts/python -m pytest tests -q
```
```
39 passed in 20.84s
```

```
cd /c/Project/uniops/finance-api && TEST_PG_PASSWORD=*** ./.venv/Scripts/python -m pytest tests/test_coa.py tests/test_payment_execute.py -q
```
```
29 passed in 128.91s
```

```
docker exec uniops_epms_api python -m pytest tests/test_admin.py -q
```
```
19 passed in 7.99s
```
(the 4 new singleton-guard tests live in `test_user_supervisor_assignment.py`,
not `test_admin.py`; ran together separately: `25 passed`.)

```
cd /c/Project/uniops/epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"
```
```
69
```
(baseline, unchanged)

```
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```
```
PARITY OK (12 depts x 3 doc types)
```

```
docker exec uniops_epms_api alembic heads
```
```
z3_add_created_by_to_tasks (head)
```
Single head, no z4.

### Concerns

- `epms-api/app/api/v1/users.py`'s CSV bulk-import path (`POST
  /users/import`) also writes `.role` directly for both new and existing
  users (lines ~275 and ~290-298) without going through the new
  `_post_conflict` guard. This wasn't named in the controller's three
  findings (which called out `update_user` and "the create path" — i.e.
  `POST /users`, which is now guarded), so it was left untouched to avoid
  scope creep beyond what was reviewed. It is the same class of gap as FIX 2
  and should be looked at — a CSV import setting two rows to `gm` would
  silently arm the same nondeterminism.
- `finance-api/app/crud/payment_execute.py::_check_can_pay` has the
  identical shape of bug: `_PAY_ROLES = {"ap_clerk", "finance_manager",
  "finance_bp", "system_admin"}` is checked against `user.get("role")`
  (line 81) — a primary-role-only check — before the assignment-based
  `_user_role_codes` fallback is even consulted, so a primary-role
  `finance_bp` user without an assignment can already execute payments.
  Not touched: the controller's FIX 1 named exactly "two consumers" (coa.py
  and BudgetDashboard.tsx) and `test_payment_execute.py` was listed as an
  unmodified green baseline in the verify block, so this was treated as
  out of scope for this pass rather than assumed-broken-and-silently-fixed.
  Flagging it explicitly here since it's the same doctrine violation.
- BudgetDashboard.tsx's `isFinanceBpAssigned` now fetches the whole-company
  `GET /config/user-roles` map (same proxy already used by Portal's Access
  Control page) just to check one user's membership. Accepted as the
  correct-per-doctrine option per the task's explicit menu of choices; the
  endpoint is already used elsewhere and `useUserRoles` sets a 60s
  `staleTime`, so this is not expected to be a meaningful load concern, but
  it is a new network call this page did not previously make.


## CSV/ERP import guard(控制器验证并代提交)

实施代理再次返回无效响应(第 6 次:"wait for the Monitor task"),未验证未提交。代码本身完整正确,由控制器验证后提交。

**覆盖三条绕过路径**:CSV 导入的 update 分支(~275)/create 分支(~293)、ERP 导入(~474) —— 此前 `_post_conflict` 只守 create_user/update_user 两个端点,导入路径直接写 `.role` 完全绕过守卫(守前门敞后门=守卫没做完)。
**设计**:批量导入按各自既有的错误收集惯例逐行 error/skip(不中断整个文件),消息点名当前持有人;新增 `claimed_posts` 字典处理**同一文件内两行争抢同一岗位**(DB 检查在 flush 前看不到前一行)。

**验证(控制器亲跑)**:
- `test_user_supervisor_assignment.py` 单独跑 **6 passed**(含 4 个新守卫测试:PATCH 409/自我改保幂等/finance_bp 多持有者放行/create 409)
- 与 `test_users_import_from_erp.py` 同跑时出现的 10 errors 是**既有的测试顺序污染**(该文件的 `assert 9 >= 12` 密码策略失败在 76 条基线里),非本次引入
- **epms 全量失败集:72 vs 基线 76,新增为零**,消失的 4 条正是随死功能删除的 temp-assignment 测试
