# Reusable Factor Library — Design & Migration

**Date:** 2026-05-27
**Sprint context:** S4 add-on
**Status:** Shipped (this PR)

---

## 1. Problem

Currently a Budget Account's decomposition factors (e.g. `brand`, `channel`, `region`) are typed inline per account in EPMS Account Catalog. A finance team that wants the same factor — say `region` with values `EAST/WEST/CENTRAL` — applied to 30 accounts has to retype it 30 times, and any later refinement requires editing it 30 places. The requirement: a central "Factor Library" page where templates are defined once and selectable from any Budget Account.

## 2. Decision: copy-on-attach (templates), not live references

When attaching a template to an Account, the factor + active values are **copied** into the existing `budget_account_factors` / `budget_account_factor_values` tables. Once copied the per-Account rows are independent — later template edits do **not** propagate.

| Concern | Live-reference model | Copy-on-attach (chosen) |
|---------|---------------------|-------------------------|
| Historical plan_breakdowns labels | A template value rename mid-fiscal-year mutates labels on historical breakdowns | Frozen at attach time — safe |
| Schema impact on existing tables | Requires changing `budget_account_factors` to bind by `factor_id` instead of `factor_code` | None — additive only |
| "Reuse" semantics | True propagation | "Preset library" — finance manually re-attaches if a template evolves |
| Implementation risk | High (data migration + frontend rewires) | Low (additive endpoints only) |

`budget_plan_breakdowns.factor_combo` JSONB keys remain `factor_code` strings, unchanged.

## 3. Data model (additive)

```
factor_templates                     ←─ new
  - id UUID PK
  - factor_code VARCHAR(30) UNIQUE   ←─ immutable after create; default copy key
  - factor_name VARCHAR(100)
  - description VARCHAR(500) NULL
  - is_active BOOL DEFAULT true
  - created_at / updated_at

factor_template_values               ←─ new
  - id UUID PK
  - template_id UUID FK → factor_templates(id) ON DELETE CASCADE
  - value_code VARCHAR(50)
  - value_name VARCHAR(255)
  - sort_order INT DEFAULT 0
  - is_active BOOL DEFAULT true
  - UNIQUE(template_id, value_code)
  - created_at / updated_at
```

Alembic: [budget-api/alembic/versions/20260527_0004_factor_templates.py](../../../budget-api/alembic/versions/20260527_0004_factor_templates.py).

## 4. API surface

All under existing `budget-api` factor router. Reads open to any authenticated user; writes require `system_admin | finance_manager | finance_bp` (matches the existing per-account factor write roles).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/factor-templates?active_only=true` | List templates (Portal page + EPMS picker) |
| GET | `/factor-templates/{id}` | Fetch one with values |
| POST | `/factor-templates` | Create template + initial values |
| PATCH | `/factor-templates/{id}` | Edit name / description / is_active (code is immutable) |
| DELETE | `/factor-templates/{id}` | Hard delete — never blocked (per-Account copies are independent) |
| POST | `/factor-templates/{id}/values` | Add value to template |
| PATCH | `/factor-template-values/{id}` | Edit name / sort_order / is_active |
| DELETE | `/factor-template-values/{id}` | Hard delete a template value |
| POST | `/accounts/{account_id}/factors/from-template` | Clone template → per-Account factor + values |

**Clone semantics:** body `{ template_id, factor_code?, factor_name?, sort_order? }`. Optional override fields let two Accounts attach from the same template under different codes (no `(account_id, factor_code)` collision). Only `is_active=true` template values are copied. The 3-factor Account ceiling and existing-code collision check still apply.

## 5. UI

- **Portal → Finance → Factor Library** (`/budget/factors`): new full page at [portal/src/pages/budget/FactorLibraryPage.tsx](../../../portal/src/pages/budget/FactorLibraryPage.tsx). Lists templates, per-row expandable values editor, search by code/name, active-only filter. Page guard: `system_admin | finance_manager | finance_bp`.
- **Portal sidebar:** FINANCE → "Factor Library" entry added with `allowedRoles: ['system_admin', 'finance_manager', 'finance_bp']` in [PortalChromeLayout.tsx](../../../portal/src/components/layout/PortalChromeLayout.tsx).
- **EPMS Account Catalog factor section:** new **From Library** button alongside the existing Add Factor button in [FactorConfigPanel.tsx](../../../epms/src/pages/budget/FactorConfigPanel.tsx). Opens an inline picker showing active templates (code, name, value count, "code in use" warning when the template's code already exists on this account); optional overrides for code/name; on confirm calls `POST /accounts/{id}/factors/from-template`.

## 6. Permissions

Mirrors the existing per-account factor write roles: `system_admin | finance_manager | finance_bp`. Same set for both library management and the `from-template` clone endpoint. No new role introduced.

## 7. Test plan

- **Backend** — apply migration `20260527_0004`; verify tables created. Create a template, attach to an Account via `from-template`, verify the per-Account factor has independent values (toggle template's value `is_active=false`, the cloned per-Account value is unaffected).
- **3-factor cap** — attempt to attach a 4th template to an account at the ceiling; expect 409.
- **Code collision override** — attempt to attach a template with `factor_code=brand` to an account that already has `brand`; expect 409 unless `factor_code` override is supplied.
- **Portal** — log in as `finance_manager` → FINANCE sidebar shows Factor Library → create template + 3 values → toggle one inactive → verify EPMS picker only lists active values count.
- **EPMS** — open BudgetCatalogPage → expand a decomposition-enabled account → "From Library" → pick template → factor + active values appear → template edits in Portal do NOT mutate this account.
- **RBAC** — `requester` token POSTing to `/factor-templates` returns 403.

## 8. Risks

- **R1 — Template hoarding:** finance teams might create overlapping templates ("region_NA" vs "north_america_regions") since there's no enforcement of one canonical set. Acceptable in v1; `description` field and the search filter mitigate.
- **R2 — Confusing semantics for users expecting live link:** UI copy on the picker explicitly states "later template edits do not propagate". If the team later wants live-link semantics, that's a separate migration that introduces `factor_id` as the JSONB key on `budget_plan_breakdowns.factor_combo`.
- **R3 — Soft delete vs hard delete on template values:** template values support hard delete (no per-Account ripple), but the editor defaults to a `is_active` toggle to encourage soft hiding for audit history. Hard delete is available via the `Trash2` button with confirm prompt.
