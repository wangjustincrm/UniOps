# Task 7 Report — EPMS Role Management 页签下线 + 代班功能删除

## Status
DONE

## Commit
`40a8f67` on `feature/approval-routing-phase3` — "feat(epms): retire Role Management tab; drop dead temp_assignments feature"

## Files changed (exactly these 10, no `-A`)
- `epms/src/pages/admin/AdminPanel.tsx` — removed `role_management` NAV entry, its `renderSection` case, the whole `RoleManagementSection` component, `TEMP_ROLE_OPTIONS`, `DEFAULT_ROLE_MANAGEMENT_CONFIG`, and now-unused imports (`UserCog`, `Calendar`, `useCreateTempAssignment`, `useDeleteTempAssignment`, the `RoleManagementConfig` type import).
- `epms/src/services/config.ts` — removed `TempAssignment` interface, `CreateTempAssignmentBody`, `listTempAssignments`/`createTempAssignment`/`deleteTempAssignment` from `configService`, and the `temp_assignments` field from `RoleManagementConfig`/`CompanyConfig`.
- `epms/src/hooks/useConfig.ts` — removed `useCreateTempAssignment`/`useDeleteTempAssignment`.
- `epms-api/app/api/v1/config.py` — removed the 3 `/temp-assignments` endpoints and the temp-assignment embed in `_full_response`.
- `epms-api/app/crud/config.py` — removed `list_temp_assignments`/`get_temp_assignment`/`create_temp_assignment`/`delete_temp_assignment`.
- `epms-api/app/schemas/config.py` — removed `TempAssignmentCreate`, `TempAssignmentResponse`, the unused `RoleManagementConfig` pydantic class, and `ConfigResponse.temp_assignments`.
- `epms-api/app/models/config.py` — removed the `TempAssignment` ORM class (and now-unused `Date`/`date`/`UUIDPrimaryKey` imports).
- `epms-api/app/models/__init__.py` — dropped the dangling `TempAssignment` import (would have been an `ImportError` at app startup otherwise; necessary consequence of deleting the model, not a scope expansion).
- `epms-api/tests/test_config.py` — deleted the 6 temp-assignment tests (endpoints no longer exist).
- `epms-api/alembic/versions/z4_drop_temp_assignments.py` (new) — drops `temp_assignments`; `down_revision = 'z3_add_created_by_to_tasks'` (confirmed via `alembic heads` before writing, not guessed).

`test_config_director_mapping.py` (named in the brief) was checked and needs **no** changes — it only tests `dept_director_mapping`, unrelated to this task.

## Scope decision worth flagging: what "四件套" did NOT include

The brief's Interfaces line says `company_config` 的四件套 JSONB 列 (`role_management` +
the 3 `dept_*` mapping columns) "保留不删…仅无人再读" (kept, no longer read). I verified
that claim before touching schemas and found it's **not fully true for `role_management`**:
`epms/src/pages/budget/BudgetDashboard.tsx:39-47` actively reads `config.role_management`
(`rm.gm_user_id`, `rm.gm_backup_user_id`, etc.) at runtime to compute `isSpecialRoleAssignee`
for full budget-dashboard access — and that file is **not** in the brief's file list.

Given that, I kept `role_management` (and the 3 `dept_*` columns, also still live via the
`dept_mapping`/`dept_director_mapping`/`dept_supervisor` AdminPanel tabs that remain) fully
intact in the DB model **and** in `ConfigResponse`/`ConfigUpdate`/frontend `CompanyConfig`
type — removing only what's unambiguously dead: the `TempAssignment*` classes/table/endpoints
and the now-unused backend `RoleManagementConfig` pydantic class (confirmed via repo-wide
grep it was never referenced by any endpoint's `response_model`). This is a narrower deletion
than a literal reading of "四件套字段" in the schemas/config.py bullet might suggest, but the
alternative (stripping `role_management` from the API) would have silently broken
BudgetDashboard's backup-role full-access gating for GM/OPM/Finance-Manager backups — a file
outside this task's stated scope that I was told not to touch. Flagging for whoever owns the
next pass in case BudgetDashboard's dependency on `role_management` also needs migrating to
`user_roles` (mirroring what Task 5 already did for the backend can_pay/access_scope readers).

## Verification (all foreground, positive evidence)

**tsc** — `cd epms && npx tsc -p tsconfig.app.json --noEmit`
- Before: 69 errors
- After: 69 errors (identical — the 6 remaining hits in AdminPanel.tsx are pre-existing
  unused-`MovedToPortal`-component warnings, e.g. `CompanySettings`/`SecuritySettings`, not
  touched by this change; confirmed unrelated to the deleted `RoleManagementSection`)

**alembic**
```
docker exec uniops_epms_api alembic upgrade head
→ Running upgrade z3_add_created_by_to_tasks -> z4_drop_temp_assignments, drop temp_assignments — dead feature (approval engine never read it)
docker exec uniops_epms_api alembic heads
→ z4_drop_temp_assignments (head)          # single head, no fork
```

**epms-api pytest** — `docker exec uniops_epms_api python -m pytest tests -q`
- Before (baseline, `/tmp/epms_base_fails.txt`): 76 failed
- After (`/tmp/epms_t7.txt`): 72 failed, 266 passed
- `diff /tmp/epms_base_fails.txt /tmp/epms_t7.txt` → only **removals**, zero additions:
  the 1 ERROR log line + 3 `FAILED tests/test_config.py::test_*temp_assignment*` lines that
  no longer exist (their tests were deleted along with the feature). `test_config.py::
  test_get_config_returns_defaults` and `test_update_config_non_admin_forbidden` remain
  failing in both runs — pre-existing, unrelated to this change.
  → **ZERO REGRESSION** confirmed.

**grep-zero proof**
```
$ grep -n "TEMP_ROLE_OPTIONS\|RoleManagement\|role_management\|temp_assignment" epms/src/pages/admin/AdminPanel.tsx
(no output, exit 1)
```

**DB**
```
$ docker exec uniops_postgres psql -U epms -d epms -c "\d temp_assignments"
Did not find any relation named "temp_assignments".
```
Row count was confirmed 0 both before dropping (local dev DB) and per the controller's earlier
production check cited in the brief.

## Concerns
- BudgetDashboard.tsx's live dependency on `role_management` (see scope-decision section
  above) — not fixed here, flagged for a follow-up task if the intent is to fully retire
  `role_management` as an API-visible field.
- `company.store.ts` has its own unrelated, independently-typed `TempAssignment`/
  `RoleManagementConfig` (camelCase, Zustand `persist` localStorage store, name `epms-company`)
  — left untouched; it's not wired to the backend config service and is out of this task's
  file list. Possible pre-existing dead code, not addressed here.

## Follow-up — BudgetDashboard, the last role_management reader

### Status
DONE

### Commit
See sha below on `feature/approval-routing-phase3` — "feat(epms): budget dashboard full-access gate reads the role union, not role_management"

### Files changed (2)
- `epms/src/pages/budget/BudgetDashboard.tsx` — replaced the `config?.role_management` id-comparison block (`rm.gm_user_id`, `rm.gm_backup_user_id`, ... `finance_bp_user_ids`) with a role-union check: `useRolePermissions()` (Phase 1 hook, `GET /config/me/permissions`, returns `{ permissions, roles }` where `roles` = primary role ∪ additional `user_roles`) against a `SPECIAL_ROLE_CODES` set (`gm`, `opm`, `finance_manager`, `procurement_manager`, `finance_bp`). `vendor_manager` intentionally excluded (was never in the old check).
- `epms/src/services/config.ts` — removed the now-fully-unused `RoleManagementConfig` interface and the `role_management: RoleManagementConfig` field from `CompanyConfig` (confirmed via repo-wide grep across epms/portal/finance/oa `src` that BudgetDashboard.tsx was the only functional reader; the 3 remaining hits elsewhere are comments, not reads). `useConfig.ts` needed no change (it never referenced `role_management`).

### Why this is equivalent (not just "should be fine")
- All 4 post ids (`gm_user_id`/`opm_user_id`/`finance_manager_user_id`/`procurement_manager_user_id`) and `finance_bp_user_ids` were migrated into `user_roles` — same humans now carry the matching role code.
- Backups: prod's `gm_backup_user_id` = Farshid = holds `opm`; `opm_backup_user_id` = Chenggang = holds `gm` — union still admits them via the primary role code.
- `finance_manager_backup_user_id` / `procurement_manager_backup_user_id` never existed as keys in prod's `role_management` JSONB (verified 9 keys total) — those branches of the old check were dead code, so dropping them changes nothing.

### Verification (foreground, positive evidence)

**grep-zero proof** (whole-repo, all 4 frontend apps):
```
$ grep -rn "role_management" --include=*.tsx --include=*.ts epms/src portal/src finance/src oa/src | grep -v node_modules
epms/src/pages/budget/BudgetDashboard.tsx:30:// retired) company_config.role_management ids. Migrated to identity's
finance/src/pages/finance/CoaConfigPage.tsx:355:  // Server-side capability (JWT roles ∪ role_management assignments) —
oa/src/pages/expenses/ExpenseDetailPage.tsx:366:  // not JWT roles, so authorization is resolved server-side via tasks + role_management.
oa/src/pages/pa/PaDetailPage.tsx:731:  // not JWT roles, so authorization is resolved server-side via tasks + role_management.
```
All 4 hits are comments — zero functional reads remain.

**tsc** — `cd epms && npx tsc -p tsconfig.app.json --noEmit` (TS 5.9.3, no `--ignoreDeprecations` flag)
- Before: 69 errors
- After: 69 errors (identical count; confirmed no new errors in `BudgetDashboard.tsx` or `services/config.ts` specifically via targeted grep of the tsc output)

**docker** — `docker restart uniops_epms_frontend && sleep 6 && docker logs uniops_epms_frontend --tail 6`
```
uniops_epms_frontend
  VITE v8.0.1  ready in 1101 ms
  ➜  Local:   http://localhost:5173/
  ➜  Network: http://172.20.0.17:5173/
```
Clean start, no compile error.

### Concerns
- `company.store.ts` still has its own independent camelCase `roleManagement`/`RoleManagementConfig` (Zustand `persist`, localStorage key `epms-company`) — pre-existing dead code per Task 7's note, untouched here (not in this task's file list, and the verify grep targets snake_case `role_management` so it doesn't collide).
- `company_config.role_management` (the DB/JSONB column and backend `ConfigResponse` field) itself is unchanged — this task only removed the frontend TS type/field now that nothing reads it. Whether to also drop it from the backend schema/model is a separate decision outside this task's scope.
