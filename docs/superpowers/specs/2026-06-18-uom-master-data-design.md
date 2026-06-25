# Unit of Measure (UOM) as MDM Master Data — Design

**Date:** 2026-06-18
**Status:** Approved (design), pending implementation plan
**Author:** Justin Wang

## Problem

Line-item units are hardcoded as a frontend constant `LINE_ITEM_UNITS`
(`epms/src/types/index.ts`): `pcs, kg, set, pair, box, carton, roll, m, m², L,
hour, month, lot`. The unit picker needs to be configurable from the UniOps
admin panel rather than baked into the code.

UOM has low impact on today's procurement/payment flows, but it becomes
critical once raw-material procurement and production modules are added: many
materials will carry a **primary UOM** and a **secondary UOM** with a
**conversion rate** between them. Because of that future, UOM is modelled as a
first-class **Master Data (MDM)** entity now, not as a flat config blob.

## Decisions

- **UOM lives in mdm-api** as a master entity, mirroring the existing
  Department master. It is *not* stored on `company_config`.
- **Entity shape now:** `code`, `name`, `dimension`, `is_active` (+ timestamps).
  `dimension` is intrinsic to a unit and lets the future material module group
  valid conversions.
- **Conversion rates are deferred.** Per-material primary/secondary UOM and the
  conversion factor between them are *material-specific* and belong to the
  future material/production module (on the material record), not on the UOM
  master.
- **Transaction lines keep storing the unit as a free code string**, matching
  the existing `parts.unit` and EPMS PR line behaviour. MDM owns the canonical
  picklist; there is no cross-service FK from line tables to `units_of_measure`
  yet. A future material module can introduce a proper FK.
- The unit picklist is read by every consumer through the existing `mdmApi`
  client (already present in epms, portal, and oa frontends).

## Architecture

### New entity — mdm-api: `units_of_measure`

Mirrors `Department` (UUID PK + `TimestampMixin`).

| column      | type        | notes                                                        |
|-------------|-------------|-------------------------------------------------------------|
| id          | UUID PK     |                                                             |
| code        | String(50)  | unique, indexed; upper-cased on write (e.g. `PCS`, `KG`)    |
| name        | String(255) | e.g. `Pieces`, `Kilogram`                                   |
| dimension   | String(20)  | `count` / `mass` / `volume` / `length` / `area` / `time` / `other` |
| is_active   | Boolean     | default true                                                |
| created_at  | timestamp   | via TimestampMixin                                          |
| updated_at  | timestamp   | via TimestampMixin                                          |

New files (mirror the Department pattern exactly):
- `app/models/uom.py` — `UnitOfMeasure` ORM model.
- `app/schemas/uom.py` — `UomCreate`, `UomUpdate`, `UomResponse`, `UomListResponse`.
- `app/crud/uom.py` — `get_all(active_only)`, `get_by_id`, `get_by_code`,
  `create` (code upper-cased), `update`, `count_references`, `delete`.
- `app/api/v1/uom.py` — router `prefix="/uom"` (full path `/mdm/v1/uom`):
  - `GET /uom?active_only=` — any authenticated user.
  - `POST /uom` — write roles; 409 on duplicate code.
  - `GET /uom/by-code/{code}`, `GET /uom/{id}`.
  - `PATCH /uom/{id}` — write roles.
  - `DELETE /uom/{id}` — write roles; blocked when an active Part uses the code
    (same-DB reference guard), else deletes. Prefer deactivate over delete.
- Register router in `app/api/v1/__init__.py`; import model in `app/main.py`
  model-import line so Alembic metadata sees it.

Write authorization matches Departments:
`require_roles("system_admin", "finance_manager", "ap_clerk")`.

### Migration & seed — mdm-api

New Alembic migration:
1. Create `units_of_measure` table.
2. Seed the current 13 units with dimensions:
   - `count`: pcs, set, pair, box, carton, roll, lot
   - `mass`: kg
   - `length`: m
   - `area`: m²
   - `volume`: L
   - `time`: hour, month

(Seed `code` values upper-cased to match the upper-casing convention; `name`
human-readable.)

### expense-api change — persist unit on invoice lines

OA Direct PA stores its line items as **invoice lines in expense-api**
(`expense_invoice_lines`), which currently has no `unit` column. Add it so the
selected unit is persisted:
- `app/models/invoice.py` — add `unit: Mapped[str | None]` (String(50),
  nullable) to `ExpenseInvoiceLine`.
- Alembic migration adding the nullable column.
- `app/schemas/invoice.py` — add `unit: Optional[str]` to `InvoiceLineResponse`
  and `InvoiceLineUpdate`.
- `app/api/v1/invoices.py` — add `unit` to `InvoiceLineCreate` and set it in the
  create loop that builds `ExpenseInvoiceLine`.

No server-side validation of the unit against the MDM list — the frontend
dropdown constrains the choices (consistent with how EPMS PR already works).

### Consumers (frontend) — read list via `mdmApi`

All read `GET /uom?active_only=true` through the existing `mdmApi` client and
fall back to the `LINE_ITEM_UNITS` constant only while loading / if empty.

1. **EPMS PR line editor** — `epms/src/components/pr/PrLineItems.tsx`. Replace
   the two `LINE_ITEM_UNITS.map(...)` dropdowns with the MDM-sourced list.
   **PO create** reuses this component (`PoCreatePage.tsx`), so it is covered
   automatically.
2. **EPMS Parts master** — `epms/src/pages/parts/PartsListPage.tsx`. Replace the
   `LINE_ITEM_UNITS` dropdown.
3. **OA Direct PA** — `oa/src/pages/pa/PaDirectCreatePage.tsx`. **Add** a Unit
   dropdown column to the line-item editor (today the columns are
   Description / Qty / Unit Price / Total and `unit` is always `null`), include
   `unit` in the invoice-line POST payload, and show the unit in the preview
   tab.

Shared frontend plumbing:
- Add a small `useUoms()` hook in epms and oa over
  `mdmApi.get('/uom?active_only=true')` (React Query, sensible `staleTime`).
- Keep `LINE_ITEM_UNITS` as the offline fallback constant.
- **Edge case:** when editing an existing PR / part / invoice line whose saved
  unit is no longer in the active MDM list, inject that saved value into the
  `<select>` options so editing never silently changes a stored unit.

### Admin UI — Portal

New **"Units of Measure"** section in the Portal admin panel
(`portal/src/pages/admin/AdminPanel.tsx`), placed beside Department Management
(both use `mdmApi`). A table of code / name / dimension / status with a
create-edit modal and deactivate/delete, mirroring `DepartmentManagement`.
Register it in the admin nav. All user-facing copy in English.

### Unchanged

- **EPMS PA (PO-linked)** inherits units read-only from the selected PO lines —
  no change.

## Out of scope (future material/production module)

- Per-material primary/secondary UOM and conversion factors (live on the
  material record).
- FK from transaction line tables → `units_of_measure`.
- Global same-dimension conversion factors on the UOM master.

## Testing

- **mdm-api:** CRUD happy paths (create upper-cases code, duplicate → 409,
  list `active_only`, patch, delete blocked by active part reference, delete
  succeeds otherwise). Migration applies and seeds 13 units.
- **expense-api:** invoice create persists `unit` on lines; response and update
  round-trip `unit`; existing invoices (null unit) still serialize.
- **Frontend:** PR / Parts / OA Direct PA dropdowns populate from MDM; fallback
  to constant while loading; saved-but-removed unit still selectable on edit;
  OA Direct PA sends and displays the chosen unit.
