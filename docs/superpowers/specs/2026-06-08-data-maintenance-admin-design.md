# Cross-System Data Maintenance Admin — Design

**Date:** 2026-06-08
**Status:** Approved design, ready for implementation planning
**Scope of this spec:** Phase 1 — framework + EPMS entities. OA and VMS are later phases.

## Problem

Operators need a controlled way to browse, edit, and delete production business
records — starting with EPMS PR / PO / GR / Invoice / PA, and eventually OA and
VMS records too. Today the only options are raw SQL against the production
database (`epms` on 10.10.50.20) or the per-module business forms, neither of
which gives a safe, audited, cross-system maintenance surface. The original
trigger was "delete all PR/PO/PA/INVOICE/GR"; the chosen solution is a long-term
**data maintenance admin tool**, not a one-time purge.

## Key architectural finding: one shared database

All services (epms-api, expense-api/OA, vms-api, approval-api, budget-api, …)
connect to a **single physical Postgres database** (`epms` on 10.10.50.20:5432).
The per-service "mirror" models are not separate copies — they map to the **same
physical tables**, each service declaring only the columns it needs:

- `purchase_requests`, `purchase_orders`, `invoices`, `payment_applications`,
  `goods_receipts` — one physical table each
- `tasks`, `approval_events`, `users`, `vendors`, `cost_centers`,
  `company_config`, `file_metadata` — one physical table each, shared

**Consequence:** "cross-service cascade delete" collapses into "cross-table
cascade inside one database, in one transaction." There is no separate copy to
keep in sync and no need for cross-service HTTP purge endpoints. Deleting a
`purchase_requests` row removes it for EPMS, OA, and approval simultaneously.

## Decisions (locked)

| Decision | Choice |
|---|---|
| Target environment | Production DB (10.10.50.20), via tool — never raw SQL |
| Coverage | Cross-system: EPMS + OA + VMS (phased; EPMS first) |
| Architecture | Generic metadata-driven framework |
| Delete behavior | Full cascade (cross-table, one DB transaction) |
| Edit behavior | Full-field edit |
| Access control | `system_admin` + new `data_maintenance` permission only |
| Delete confirmation | Cascade impact **preview + explicit confirm** |
| Audit | Every edit and delete written to an audit log |
| Frontend home | Portal (`:5174`) new "Data Maintenance" area |
| Build order | Framework + EPMS first, then OA, then VMS |

## Architecture

Two layers:

### Backend — per-service `admin` module (authoritative)
Each business API gets a small, self-contained `app/admin/` module following an
identical pattern. Phase 1 implements it in **epms-api** only.

Each service owns an **entity registry**. For every manageable entity it declares:

- **Metadata**: table label, system tag, list columns, full field list with
  types, which fields are editable, search keys, default ordering, page size.
- **Cascade descriptor**: an ordered list of dependent purges executed in one
  transaction (see below).

Uniform endpoints, all gated by `require_roles("system_admin")` plus a new
`data_maintenance` permission key in the Access Control Matrix:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/admin/entities` | List manageable entities + their schemas |
| GET | `/api/v1/admin/{entity}` | Paginated, filterable, searchable list |
| GET | `/api/v1/admin/{entity}/{id}` | Full record (all fields + line items) |
| PATCH | `/api/v1/admin/{entity}/{id}` | Full-field edit |
| DELETE | `/api/v1/admin/{entity}/{id}?preview=1` | Cascade impact preview (no mutation) |
| DELETE | `/api/v1/admin/{entity}/{id}` | Execute cascade delete |
| POST | `/api/v1/admin/{entity}/bulk-delete` | Multi-select cascade delete |

### Frontend — single generic UI in portal
A new "Data Maintenance" admin area in `portal/src/pages/admin`:

1. **System picker** — EPMS / OA / VMS (each backed by its service's `/admin`).
2. **Entity picker** — entities returned by `GET /admin/entities`.
3. **Records table** — columns/search/filter/ordering driven by the entity
   schema; supports pagination and multi-select.
4. **Record detail** — full-field edit form generated from the schema; Save
   calls `PATCH`.
5. **Delete** — calls preview first, shows what else will be removed and the
   counts, requires explicit confirmation, then executes.

The UI is schema-driven: adding OA/VMS entities later requires no portal changes
beyond pointing at the new service base URL.

## Cascade delete strategy (EPMS)

Deletes run entirely in-DB in a single transaction. Two kinds of dependents:

1. **FK children** — mix of `ondelete=CASCADE` (e.g. line items, attachments)
   and `ondelete=RESTRICT` (e.g. PO→PR, downstream docs). RESTRICT children must
   be purged first, in dependency order, or the DB blocks the delete.
2. **Polymorphic references with no FK** — `tasks` and `approval_events`
   reference documents via `(document_type, document_id)`. These must be purged
   explicitly by matching type + id; they will NOT cascade automatically.

Known EPMS dependency graph (descriptors encode purge order, children-first):

- **PR** → linked PO (and that PO's full subtree) → `tasks`/`approval_events`
  where `(document_type='pr', document_id=pr.id)` → budget commitment rows for
  the PR → PR attachments (FK CASCADE) → `pr_line_items` (FK CASCADE) → PR row.
- **PO** → its GRs, Invoices, PAs (each with their own subtree) →
  `tasks`/`approval_events` for the PO → PO attachments → PO line items → PO row.
  Clear the originating PR's `po_id`/`po_number` back-reference.
- **GR** → `tasks`/`approval_events` for the GR → GR attachments → GR row.
- **Invoice** → `tasks`/`approval_events` for the invoice → invoice attachments →
  invoice row. (Existing status guard in invoice CRUD is bypassed by the admin
  path — see Risks.)
- **PA** → `tasks`/`approval_events` for the PA → PA attachments →
  payment rows (finance-api `payments`, same DB) → PA row.

Each descriptor is data, not ad-hoc code, so the preview endpoint computes impact
counts by walking the same descriptor without mutating.

## Audit log

A single `admin_audit_log` table (in the shared DB):

- `id`, `actor_id`, `actor_email`, `action` (`edit` | `delete` | `bulk_delete`),
  `system`, `entity`, `record_id`, `record_number`, `before` (JSONB),
  `after` (JSONB, null for deletes), `cascade_summary` (JSONB, for deletes),
  `created_at`.

Written inside the same transaction as the mutation, so the audit row and the
change commit together. Edits store before→after; deletes store the deleted
record snapshot plus the cascade summary.

## Access control

- New permission key `data_maintenance` added to the Access Control Matrix
  (`role_permissions`), defaulting to disabled for every role.
- All `/admin/*` endpoints depend on `require_roles("system_admin")`; the matrix
  permission allows granting access to a dedicated maintenance role without
  giving full `system_admin`.
- The portal nav entry for Data Maintenance is hidden unless the user has the
  permission.

## Components & boundaries

| Component | Responsibility | Depends on |
|---|---|---|
| `app/admin/registry.py` (epms-api) | Entity metadata + cascade descriptors | ORM models |
| `app/admin/service.py` (epms-api) | Generic list/get/edit/delete/preview + audit | registry, DB session |
| `app/api/v1/admin.py` (epms-api) | HTTP surface, access gating | service, deps |
| `admin_audit_log` model + migration | Persistent audit trail | DB |
| Portal `DataMaintenance` UI + `adminApi` client | Schema-driven browse/edit/delete | service `/admin` endpoints |

Each unit is independently testable: the registry is pure data; the service
takes a session and a descriptor; the HTTP layer is thin; the frontend renders
from schema JSON.

## Testing

- **Registry**: every registered entity has a valid schema + descriptor; field
  types resolve.
- **Service (integration, against test DB)**: list/filter/paginate; full-field
  edit persists + writes audit; preview returns correct impact counts without
  mutating; cascade delete removes the full subtree + polymorphic tasks/events +
  audit row, all-or-nothing on error.
- **Cascade ordering**: deleting a PR with a downstream PO+GR+Invoice+PA succeeds
  (RESTRICT children purged first); deleting with a forced mid-transaction error
  rolls back everything.
- **Access**: non-`system_admin` without `data_maintenance` gets 403 on every
  endpoint.
- **Frontend**: schema renders table + edit form; delete shows preview and
  requires confirm.

## Risks & mitigations

- **Irreversible production deletes** → preview + confirm, audit log, single
  transaction, restricted access.
- **Bypassing business guards** (e.g. invoice status guard, approval workflow):
  intentional for a maintenance tool, but every such action is audited.
- **Orphan rows** from polymorphic `tasks`/`approval_events` → explicit purge in
  descriptors; covered by tests.
- **Descriptor drift** as schema evolves → registry validation test; descriptors
  live next to models.

## Out of scope (this spec)

- OA (expense-api) and VMS (vms-api) entity registration — later specs reusing
  this framework.
- Restore/undo of deleted records (audit captures snapshots but no restore UI).
- Bulk edit (only bulk delete in phase 1).
