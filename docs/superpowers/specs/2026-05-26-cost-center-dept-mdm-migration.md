# Cost Center & Department Migration — EPMS Admin → UniOps Finance / mdm-api

**Date:** 2026-05-26
**Sprint context:** S4 add-on
**Related:** [2026-05-26-erp-mdm-integration-design.md](./2026-05-26-erp-mdm-integration-design.md)

---

## 1. Goal

Move Cost Center and Department management out of EPMS Admin Panel and shift write/code ownership to `mdm-api` (the documented master-data service). UI splits by concept:

- **Cost Center** is finance-flavoured → managed in **Portal → Finance → Budget Config** as a new tab.
- **Department** is general master data → managed in **Portal → Admin → Departments** (the existing section already there; its backend base URL is swapped from epms-api to mdm-api).

End state:

- **mdm-api** is the source of truth for `cost_centers` and `departments` tables (writes, validation, audit).
- Portal Finance owns CC UI; Portal Admin owns Department UI.
- `epms-api`, `budget-api`, `finance-api` consume the tables as read-only.
- The EPMS Admin Panel `Departments` and `Cost Centers` tabs are removed entirely (Department UI relocates to Portal Admin; CC UI relocates to Portal Budget Config).

## 2. Why mdm-api (not budget-api / finance-api)

- mdm-api already has scaffolded models / read endpoints for both entities ([mdm-api/app/models/cost_center.py](../../../mdm-api/app/models/cost_center.py)).
- The 2026-05-26 ERP MDM spec designates mdm-api as the directory/master-data service. Cost Centers and Departments fit that mandate; budget plans and balances do not.
- Co-locating CC + Dept with ERP mirrors (`erp_materials`, `erp_suppliers`, `erp_persons`) keeps all master data in one service.

## 3. Architecture — current vs target

```
BEFORE                                                 AFTER
======                                                 =====
EPMS Admin → /departments (epms-api)                   Portal Admin → /departments (mdm-api)   ◀── Department UI
EPMS Admin → /cost-centers (epms-api)                  Portal Budget Config → /cost-centers (mdm-api)  ◀── CC UI
Portal Admin → /departments (epms-api) [also exists]   ▲
                                                       │
                                                       ├── full CRUD now lives in mdm-api
                                                       │
                                                       └── epms-api / budget-api / finance-api
                                                           continue reading via local ORM (shared DB)
```

All services already share the same Postgres database (`epms` DB on `postgres:5432` per `docker-compose.dev.yml`), so this is a **code-ownership** migration, **not a data migration**. No backfill needed. UUIDs in PR/PO/budget continue to work unchanged.

## 4. Non-goals (v1)

- No DB split. mdm-api and epms-api keep sharing the `epms` Postgres database.
- No httpx cross-service calls for reads — consumers continue to use their local SQLAlchemy ORM against the same physical tables (zero performance regression).
- No data deletion or schema changes — table structure stays as-is.
- Alembic migrations remain in `epms-api/alembic` for now; future schema changes for CC/Dept move to `mdm-api/alembic` in a follow-up to avoid double-migrations during transition.

## 5. Data ownership rules

| Operation | Owner | Caller |
|-----------|-------|--------|
| List / Get | mdm-api (canonical) + local ORM reads allowed | Portal, EPMS pickers, budget-api joins |
| Create / Update / Delete / Deactivate | **mdm-api only** | Portal Budget Config page |
| Schema migrations going forward | mdm-api/alembic | — |
| Reference counting (block delete when in use) | mdm-api queries via local ORM (shared DB) | mdm-api |

## 6. Permissions

Per user decision: management UI / write endpoints require **`system_admin` OR `finance_manager` OR `ap_clerk`**.
- **Cost Center writes** (Portal Budget Config tab): all three roles.
- **Department writes** (Portal Admin): same — even though Departments live under Admin, the role gate matches CC for consistency. If the team prefers `system_admin`-only for Departments, only the Portal-side guard needs adjusting; mdm-api can still accept all three.

Read endpoints remain available to any authenticated user (same as today — needed for PR/PO pickers and User Management dropdowns).

## 7. Implementation phases

### Phase A — mdm-api becomes the write owner (backend)

1. Add Pydantic schemas in `mdm-api/app/schemas/cost_center.py` and `department.py` (Create / Update / Response / ListResponse). Copy from `epms-api/app/schemas/department.py` and adapt naming.
2. Expand `mdm-api/app/crud/cost_center.py` and `crud/department.py` to full CRUD: `create`, `update`, `delete`, `count_references`, plus the existing `get_all` / `get_by_id` / `get_by_code`.
   - `count_references` queries `purchase_requests.cost_center_id` directly via the shared DB. mdm-api needs to import minimal read-only models or use raw SQL `SELECT count(*) FROM purchase_requests WHERE cost_center_id = :id`.
3. Replace `mdm-api/app/api/v1/cost_centers.py` and `departments.py` with full POST / PATCH / DELETE endpoints. Reuse `require_roles("system_admin", "finance_manager", "ap_clerk")` from `mdm-api/app/core/deps.py`.
4. Verify `mdm-api/app/core/deps.py` understands all three roles — if not, extend it (likely already shared across services).

### Phase B — Portal UI

5. **Cost Center (new)**: port `CostCenterManagement` from `epms/src/pages/admin/AdminPanel.tsx` into `portal/src/pages/budget/BudgetConfigPage.tsx` as a new "Cost Centers" tab. All API calls go via the existing `mdmApi` client (`portal/src/lib/api.ts` already exports it for ERP MDM).
6. **Departments (relocate API)**: in `portal/src/pages/admin/AdminPanel.tsx`, swap the existing Department CRUD calls from `epmsApi.post/patch/delete('/departments…')` → `mdmApi.post/patch/delete('/departments…')`. UI stays in place. The list read stays on `epmsApi` until Phase D (see R2).
7. Honour the section-level role guard at Portal AdminPanel (currently `system_admin`-only at line 1694). For Budget Config Cost Centers tab, gate on `system_admin | finance_manager | ap_clerk`.
8. Confirm token handoff: the Portal session JWT is acceptable to mdm-api (shared JWT secret check). The existing ERP MDM section already calls mdm-api with this JWT, so the path is proven.

### Phase C — EPMS Admin Panel cleanup

9. Remove `CostCenterManagement`, `DepartmentManagement` components and the `cost_centers` / `departments` tab IDs from `epms/src/pages/admin/AdminPanel.tsx`.
10. Remove `epms/src/hooks/useCostCenters.ts`, `useDepartments.ts` write hooks (`useCreate*`, `useUpdate*`, `useDelete*`). Keep read-only `useCostCenters()` / `useDepartments()` if other EPMS pages use them (PR Create, etc.).
11. Add a Portal redirect notice on the old route for one release if discoverability is a concern — or skip it (user opted "remove entirely").

### Phase D — Decommission EPMS write endpoints

12. Delete `epms-api/app/api/v1/cost_centers.py` and `departments.py` POST / PATCH / DELETE routes — keep GET routes for backward-compat for any EPMS picker that still calls them, OR redirect EPMS callers to mdm-api in a follow-up release.
13. Update PR Create's cost_center picker to call mdm-api directly (cleaner) or keep using epms-api GET (deferred).

### Phase E — Docs

14. Update `epms/docs/PRD.md` §3.6.x to reference Portal → Finance → Budget Config as the new location for CC/Dept management.
15. Update implementation-status table at PRD §End to mark CC/Dept management as Portal-owned.

## 8. Test plan

- **Backend smoke**: `curl mdm-api/cost-centers` (list), POST new CC, PATCH, DELETE; verify RBAC by hitting with a `requester` token (403 expected).
- **Reference guard**: try DELETE on a CC referenced by an existing PR → expect 409 with reference count message.
- **Portal flow**: log in as system_admin → Portal → Finance → Budget Config → Cost Centers tab → create / edit / deactivate; confirm changes propagate to EPMS PR Create dropdown.
- **EPMS regression**: PR Create still loads cost centers; access_scope still resolves dept_id via CC; budget-api plans still work.

## 9. Risks & open items

- **R1 — Alembic ownership crossover**: both `epms-api/alembic` and `mdm-api/alembic` point at the same DB. Adding a CC migration in mdm-api after this PR could conflict with an unrelated epms-api migration. Mitigation: declare CC/Dept tables out-of-scope for epms-api's `target_metadata` (or just don't touch the tables from epms-api going forward).
- **R2 — Read paths still using epms-api**: even after the move, EPMS PR Create page calls `useCostCenters()` which hits `epms-api/cost-centers` GET. We're leaving GET routes in place so this works. Plan a follow-up to point EPMS readers at mdm-api once Portal cookie/JWT handoff is verified.
- **R3 — finance_manager / ap_clerk write access**: PRD §1.3 currently treats CC/Dept management as system_admin-only. Granting finance_manager + ap_clerk widens the role envelope; document the rationale (these roles need to register a new cost code without filing a ticket).

## 10. Rollback

Since this is a code-ownership move with no schema or data changes, rollback is a revert of the relevant commits. Tables and FKs are untouched.
