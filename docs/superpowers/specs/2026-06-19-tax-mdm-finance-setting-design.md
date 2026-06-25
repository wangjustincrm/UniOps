# Tax MDM — Finance Tax Settings page + consumer rewiring

Date: 2026-06-19
Status: Approved, implementing

## Problem

The PO Tax Rate dropdown is hardcoded (`TAX_OPTIONS` in `epms/src/pages/po/PoCreatePage.tsx` +
`PoEditPage.tsx`, and `TAX_RATES` set in `epms-api/app/schemas/po.py`). PA uses a hardcoded
`0.13` auto-fill. There is no admin surface to manage tax rates. Tax master data should be a
single MDM source that all tax-consuming modules read from.

## What already exists (Phase 0-B2)

- `mdm-api`: `tax_codes` table (effective-dated `rate`, ITC `recoverable`, `active`,
  `effective_from/to`, province, tax_type) + `tax_rules` determination engine — both seeded
  with real Canadian rates (migration `0003_create_tax_master.py`).
- `mdm-api` read API (registered): `GET /tax/codes?as_of=`, `GET /tax/determine`.
- Frontend: `epms/src/services/invoiceTax.ts` `getTaxCodes()` → `mdmApi.get('/tax/codes')`;
  Invoice tax-line editor consumes it. OA `ExpenseCreatePage` already queries `/tax/codes`
  and stores `tax_code` per line.

## Scope (approved)

- Manage **tax codes/rates only**. `tax_rules` stay seeded, not editable this phase.
- PO/PA persist **`tax_code` + snapshot `tax_rate`**.
- Rewire: EPMS PO, EPMS PA, OA expense. Invoice = verify only.
- Page hosted in **Portal → Finance → Tax Settings**, gated by `view_finance` matrix key;
  write actions only for privileged roles.

## Architecture

`mdm-api` remains the single owner of `tax_codes`. Add the missing write layer there
(mirroring `uom.py`). Portal Finance "Tax Settings" page manages it via `mdmApi`. All
consumers read `GET /tax/codes`. Matches the UOM MDM precedent and PRD "no hardcoded tax
treatment" rule.

## Backend — mdm-api tax CRUD

Mirror `mdm-api/app/api/v1/uom.py`:
- `POST /tax/codes` (201), `PATCH /tax/codes/{id}`, `DELETE /tax/codes/{id}`.
- Write gate: `require_roles("system_admin", "finance_manager", "ap_clerk")` (UOM's `WriteDep`).
  Reads stay open to all app roles.
- `DELETE` = soft delete (`active=false`) so referenced codes never break history.
- New schemas `TaxCodeCreate` / `TaxCodeUpdate`; extend `crud/tax.py` with
  `create` / `update` / `deactivate`. Enforce the `(code, effective_from)` unique constraint
  with a clean 409.
- `tax_rules` untouched.

## Backend — epms-api consumers

- Migration: `tax_code VARCHAR(20) NULL` on `purchase_orders`; `tax_code VARCHAR(20) NULL` +
  `tax_rate NUMERIC(5,4) NULL` on `payment_applications`. Nullable, no backfill (legacy rows:
  `tax_code` null, existing numeric rate retained).
- PO `schemas/po.py`: replace hardcoded `TAX_RATES` set with range check `0 <= rate <= 1`;
  add `tax_code: str | None`. Persist `tax_code` in `crud/po.py`. No per-write mdm call
  (consistent with existing fail-open epms↔mdm pattern).
- PA `schemas/pa.py` + `crud/pa.py` + `models/pa.py`: add `tax_code` + `tax_rate` (nullable),
  persist alongside existing `tax_amount`.

## Frontend

- `portal/src/pages/finance/TaxSettingsPage.tsx` — table + create/edit modal, structured like
  `portal/src/pages/admin/UnitsOfMeasure.tsx`. Columns: code, name, tax_type, province,
  rate %, recoverable, effective from/to, active. Route under Finance + sidebar link, gated by
  `view_finance`. Write controls shown only to privileged roles.
- `portal/src/services/tax.ts` — `mdmApi` list/create/update/deactivate.
- EPMS PO (`PoCreate`/`PoEdit`): delete `TAX_OPTIONS`; dropdown from `mdmApi.get('/tax/codes')`,
  value = code, label = `"{rate}% — {name}"`. Store `tax_code` + snapshot `tax_rate`. Keep a
  saved-but-inactive code selectable (UOM pattern).
- EPMS PA (`PaCreate`/`PaEdit`): replace `0.13` auto-fill with selected code's rate; tax stays
  manually overridable. Store `tax_code` + `tax_rate`.
- OA expense: drop hardcoded `hstRate`; derive rate from the line's selected `tax_code`.
- Invoice: verify picker still reads `/tax/codes`; no change.

## Edge cases

- Effective dating: dropdown uses today's active set via `GET /tax/codes` (no `as_of`).
- Deactivating a code in use: existing docs keep their snapshot `tax_code`+`tax_rate`;
  the code stops appearing in new pickers; a saved value that is now inactive is still shown.
- Decimal-as-string: mdm rates arrive as JSON strings — `Number()`-coerce before math/`toFixed`.

## Testing

- mdm-api pytest: create/update/deactivate, role gating (403 for non-privileged), effective-date
  filtering, unique-constraint 409.
- epms-api pytest: PO/PA create with `tax_code` persists; totals correct.
- Frontend: no harness — manual verification steps (create a code in Tax Settings → appears in
  PO/PA/OA pickers → save persists tax_code).

## Out of scope

- `tax_rules` determination-rule editing UI.
- Cross-service validation of `tax_code` on every PO/PA write.
