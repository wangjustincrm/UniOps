# Task 4 Report: Portal Access Control Admin Page

## Status: DONE

Commit: `28fce5f629fccd6a21fc505908cbfd7bffab1b8c` on branch `feature/authz-hub-phase1`.

## Files changed

- **Created** `portal/src/pages/admin/AccessControl.tsx` — two-tab page (Permission Matrix / User Roles), mirrors the chrome/guard pattern of `DataMaintenance.tsx` (bare `<div>` + "Back to UniOps" link + `system_admin`-only gate — confirmed there is no shared `PortalChromeLayout` component in this codebase; `DataMaintenance.tsx` doesn't use one either).
- **Modified** `portal/src/App.tsx` — added `import AccessControl` + `<Route path="/admin/access-control" ...>` right after the Data Maintenance route.
- **Modified** `portal/src/components/layout/navConfig.tsx` — imported `ShieldCheck` from lucide-react, added the `Access Control` nav item (`adminOnly: true`) after Data Maintenance.
- **Modified** `portal/src/lib/api.ts`:
  - Added `epmsApi.put` (was missing; needed for `PUT /users/{id}/roles`), following the existing `epmsRequest` wrapper pattern used by `financeApi.put`.
  - Enhanced `epmsRequest`'s error path: the thrown `Error` now also carries `.detail` (raw JSON `detail` from the response body) and `.status`. Necessary because `extractDetail()` collapses any non-string/non-array `detail` (e.g. the 409 `{"detail":{"locked":[...]}}` shape from `PATCH /config/role-permissions`) down to a generic `"HTTP 409"` string, which would have made it impossible to show *which* cells were rejected as locked. This only adds properties — the `.message` string behavior for all existing callers is unchanged.

## Implementation notes

**Tab 1 — Permission Matrix**: rows grouped by `module` (header rows spanning the table, uppercased, in backend-provided `sort` order rather than a hardcoded EPMS/FINANCE/BOOKING list), columns = roles sorted by `sort` (all roles shown, active and inactive — the brief only asked to filter to active roles for Tab 2's selects). Locked cells (`permission.locked_for.includes(role)`) render checked + disabled + a lock icon with a `title` tooltip. Dirty cells are tracked in a `Record<`${role}.${key}`, boolean>` map — only actual diffs from the fetched baseline count as dirty, so toggling a cell back to its original value un-marks it. A floating footer bar appears when `dirtyCount > 0` with Save/Discard. Save sends only the dirty cells in the legacy `{role:{key:bool}}` shape via `PATCH /config/role-permissions`; on success it invalidates the `authz-matrix` query and clears dirty state; on 409 it reads `err.detail.locked` and lists the rejected role/key pairs inline in red.

**Tab 2 — User Roles**: `useAllUsers()` loops `GET /users?page=N&page_size=200` until a short page comes back (never trusts the default `page_size=20`, per the brief's explicit warning and project memory re: pagination truncation). Client-side text filter over name/email. Each row: name/email, a primary-role `<select>` (active roles only, with a defensive fallback option if the current primary role has since been deactivated), and additional roles as a compact set of toggle-chip buttons (active roles minus primary) — used inline chips rather than a popover/dropdown specifically to avoid the "overlay dropdown must be portaled" footgun documented in project memory (`feedback_uniops_overlay_dropdown_portal`), since a table-cell popover would need portal+fixed positioning to escape the table's overflow container. Save is per-row, gated on a `touched` flag (enabled only after an edit), calls `PUT /users/{id}/roles`, and shows an inline green/red flash next to the button.

## Known backend gap (flagged, not fixed — out of scope for Task 4)

There is **no GET endpoint** for a user's current *additional* roles — `identity-api`'s `authz.py` only exposes `PUT /authz/users/{id}/roles` (write) and `GET /me/permissions` (self, current user only). `GET /users` (`UserAdminResponse`) only has the single `role` (primary) field. This means the "Additional Roles" chips always start unchecked/empty for every user, regardless of what's actually assigned server-side — there is no way for the Portal to know. Mitigated by:
1. Gating Save per-row on an explicit `touched` flag, so rows nobody edits are never re-submitted (no silent overwrite of untouched rows).
2. A visible note at the top of the User Roles tab telling admins that additional-role checkboxes reflect only in-session edits.

Recommend a follow-up to Tasks 1-3: add `GET /authz/users/{id}/roles` (or fold `additional_roles` into `UserAdminResponse`/`GET /users`) so the UI can show ground truth instead of an always-empty starting state.

## Verification

1. **tsc**: baseline (before any change) = **0 errors**. After = **0 errors**. (`cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`)
2. **Dev container smoke**: **could not run** — Docker Desktop is not running in this environment (`docker ps` fails with "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine"). Could not restart `uniops_portal_frontend` or tail its logs to confirm a clean Vite compile. Mitigated by the clean `tsc --noEmit` pass and a careful manual self-review of the diff and the full new file. **The controller should run the docker log check / visual smoke before treating this as production-verified.**
3. **Self-review**: diff for the 3 modified files reviewed in full (see above); new file read back in full; only the 4 brief-named files are staged/committed — confirmed via `git status --short` that the untracked binaries in the repo root (PDF/PPTX/CHM files) and two pre-existing dirty files (`task-2-report.md`, `identity-api/tests/test_authz_api.py`, already modified before this task started) were left untouched.

## Concerns for the controller

- Please run the actual dev smoke test (container restart + `docker logs uniops_portal_frontend --tail 20` + click-through) since Docker wasn't available in this session.
- The "additional roles" read gap above should probably become a quick Task 1-3 follow-up before this ships to real admins, or admins should be warned out-of-band not to blind-save rows they haven't reviewed.
- This `task-4-report.md` path previously held an unrelated report (finance NC AP Export, from an earlier Task 4 numbering) — it has been overwritten with this content per the brief's explicit instruction to write the report here.

---

## Follow-up fix: GET /authz/user-roles (closes the "Known backend gap" above)

### Status: DONE

Addresses the data-loss bug flagged above: the User Roles tab had no way to read a user's
existing additional roles, so saving a row after only editing the primary role would PUT
`additional: []` and silently wipe roles assigned elsewhere.

### Chain added

1. **identity-api** (`app/api/v1/authz.py`): new `GET /authz/user-roles`, gated by the
   existing `_require_admin` helper (403 for non-`system_admin`). Reads every row in
   `user_roles` and returns `{"user_roles": {"<user_id>": ["role_code", ...]}}` (values
   sorted; primary role is intentionally excluded — it lives on `users.role`, already
   returned by `GET /users`). Empty table → `{"user_roles": {}}`.
   Tests added in `tests/test_authz_api.py`: `test_get_user_roles_requires_admin` (403 for
   `requester` role) and `test_get_user_roles_maps_by_user` (inserts two `user_roles` rows
   for one user, asserts the stringified UUID key maps to the sorted role list).

2. **epms-api** (`app/api/v1/config.py`): new `GET /config/user-roles`, mirroring
   `GET /config/authz-defs` exactly — `CurrentUserPayload` dep (admin gating is delegated to
   identity, which 403s), `authz_client.forward("GET", "/authz/user-roles", token)`, 502 on
   any exception forwarding, pass-through of identity's status/detail on non-200.
   Test added in `tests/test_authz_proxy.py`: `test_get_user_roles_proxies_identity` — mocks
   `authz_client.forward`, asserts the call args (`"GET"`, `"/authz/user-roles"`) and that the
   response body is passed through verbatim.

3. **portal** (`src/pages/admin/AccessControl.tsx`): added `useUserRoles()` query
   (`GET /config/user-roles`, typed `{ user_roles: Record<string, string[]> }`), loaded
   alongside `useAuthzDefs`/`useAllUsers` in `UserRolesTab`. `rowFor(u)` now seeds
   `additional` from `userRolesQ.data?.user_roles[u.id] ?? []` instead of always starting at
   `[]`. Removed the on-page warning paragraph about the API having no read endpoint (no
   longer true). Save success now also invalidates the `authz-user-roles` query key so a
   fresh save reflects immediately. Loading/error states extended to include `userRolesQ`.
   The per-row `touched` gate is unchanged — untouched rows still never get re-submitted.

### Verification

- **identity-api**: `TEST_PG_PASSWORD=*** ./.venv/Scripts/python -m pytest tests/test_authz_api.py -q`
  → **7 passed** (5 pre-existing + 2 new), 0 failed.
- **epms-api**: `docker exec uniops_epms_api python -m pytest tests/test_authz_proxy.py -q`
  → **10 passed** (9 pre-existing + 1 new), 0 failed.
- **portal tsc**: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` → **0 errors**.
- **e2e**: after `docker restart uniops_identity_api`, ran from `uniops_epms_api`:
  ```
  docker exec uniops_epms_api python -c "...GET http://identity-api:8009/identity/v1/authz/user-roles..."
  ```
  → `200 ['user_roles']` — confirms the new route is live and returns the expected shape.

### Files touched (all six listed in scope, nothing else)

- `identity-api/app/api/v1/authz.py`
- `identity-api/tests/test_authz_api.py`
- `epms-api/app/api/v1/config.py`
- `epms-api/tests/test_authz_proxy.py`
- `portal/src/pages/admin/AccessControl.tsx`
- `.superpowers/sdd/task-4-report.md` (this report)
