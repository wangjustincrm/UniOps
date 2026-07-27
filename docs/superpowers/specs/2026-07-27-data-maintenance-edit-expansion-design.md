# Data Maintenance — Expanded Edit for PR / PO / PA

**Date:** 2026-07-27
**Module:** EPMS admin (`uniops/epms-api/app/admin`), Portal admin UI (`uniops/portal`), approval-api (`uniops/approval-api`) — single new endpoint
**Scope:** Backend + frontend + one cross-service endpoint. Additive only; no changes to existing business (create/approve) code paths.

---

## 1. Goal & Positioning

Today the Data Maintenance **Edit** drawer for PR / PO / PA exposes only a handful of
flat scalar header fields (title, status, a few amounts, notes, dates). Admins cannot
correct the **Requester**, the **line items**, the **approval routing**, or most other
header fields. This project expands Edit to cover them.

**Positioning: static data correction + derived-value recompute.** Editing writes
corrected values directly to the shared DB and recomputes derived aggregates. It does
**NOT** re-run the approval engine, does **NOT** send notifications, and does **NOT**
recompute budget commitments. The one deliberate exception is department-derived
approval routing (§5), which is re-resolved when the Requester changes — because a
wrong Requester silently strands the document with the wrong approvers.

Gating and audit are unchanged: `system_admin` + `data_maintenance` permission, every
change written to `admin_audit_log`.

---

## 2. Architecture — Hybrid (approved approach "C")

Generalize what generalizes into the metadata framework; isolate what is genuinely
entity-specific into per-entity callbacks (matching the registry's existing
per-entity `cascade_*` pattern).

| Capability | Mechanism | Generalized? |
|-----------|-----------|--------------|
| Reference fields (Requester/Vendor/CostCenter) | new `reference` FieldSpec + resolver registry | Yes (shared across entities) |
| Remaining scalar fields | existing flat FieldSpec path | Yes |
| Line items | generic child-collection diff + per-entity `recompute()` | Partly (diff generic, recompute per-entity) |
| Approval active state | dedicated endpoint, **not** a field PATCH | No (bespoke) |
| PO number regeneration on vendor change | PO-only post-set hook + cascade rename | No (bespoke) |
| Requester change → approval routing follows | epms-api → approval-api single-doc resync | No (bespoke, cross-service) |

All new backend code lives under `epms-api/app/admin/` plus one new approval-api
endpoint. No new DB migration (all columns already exist; audit table already exists).

---

## 3. Metadata model changes (`admin/fields.py`)

`FieldSpec` gains optional attributes (all default `None`, so existing specs are
unchanged):

```python
ref_source: str | None = None       # "users" | "vendors" | "cost_centers"
ref_name_field: str | None = None   # denormalized name column to keep in sync, e.g. "vendor_name"
# FieldSpec.type may now be "reference"
```

`EntitySchema` gains:

```python
child: ChildSchema | None = None    # line-item sub-collection (None = no line items)
```

`ChildSchema` = `{ table_label, model, fk_field, fields: list[FieldSpec] }`.
`to_dict()` serializes the new attributes so the frontend can render pickers and the
line-item editor. **Approval state is NOT in the schema** — it is a separate operation.

---

## 4. Reference fields — Requester / Vendor / Cost Center (§4)

**Resolver registry** (`admin/resolvers.py`, new): maps `ref_source` → `(search_fn,
fetch_by_id_fn)`. Each resolver returns `(id, display_name)` and is backed by the
existing CRUD/model for that source:

- `users` → users table (display = full name / email); used by **Requester** (`created_by`)
- `vendors` → `business_partners` (display = name; also exposes `code` for §6); used by **Vendor** (`vendor_id`)
- `cost_centers` → cost_centers (display = `code — name`); used by **Cost Center** (`cost_center_id`)

**New lookup endpoint:** `GET /api/v1/admin/lookup/{source}?q=&limit=` → `[{id, label}]`,
for the frontend picker's typeahead. `system_admin` + `data_maintenance` gated.

**Apply semantics** (in `edit_record`): when a `reference` field is in the patch,
validate the id exists via the resolver; set the id column **and** overwrite
`ref_name_field` with the resolver's display name in the same operation — id and
denormalized name can never diverge. `created_by` is `RESTRICT`+`NOT NULL`: a null /
unknown user is rejected with **422**.

**Editable-field expansion** (remove only truly-immutable `id` / `number` /
`created_at`; open the rest):

- **PR** add: `created_by` (ref users), `vendor_id` (ref vendors), `cost_center_id` (ref cost_centers), `cost_center_name`, `project_code`, `delivery_address`, `is_prepaid`, `over_budget`, `over_budget_justification`, `required_by`, `type`
- **PO** add: `created_by` (ref users), `vendor_id` (ref vendors), `budget_code`, `expected_delivery`, `delivery_address`, `is_prepaid`, `tax_rate`, `tax_code`, `type`
- **PA** add: `created_by` (ref users), `vendor_id` (ref vendors), `pa_type`, `shipping_amount`, `other_charges`, `other_charges_note`, `tax_rate`, `tax_code`, `currency`

(GR / Invoice similarly gain `created_by` and a vendor picker; out of the headline
three but trivially covered by the same mechanism.)

---

## 5. Requester change → approval routing follows

**Why:** the approval engine derives the `dept_manager` / `gm_or_opm` / `director` /
`supervisor` steps from the **routing user's department**, where the routing user is:

- **PR:** `PR.created_by` directly (the requester)
- **PA:** resolved via `pa → po → pr → PR.created_by` (NOT `PA.created_by`, which is the AP clerk)

So a corrected Requester must re-resolve the department-derived open approval tasks, or
the document stays stranded with the old department's approvers.

**PA decision (approved "B-1"):** the PA Requester editor targets the **source PR's
creator** (`PR.created_by` of the linked PR) — that is the field that actually moves PA
routing. `PA.created_by` (AP clerk) remains separately editable but does **not** trigger
routing recompute. If a PA has no linked PR, the routing-relevant Requester field is
hidden/disabled with an explanatory note.

**Mechanism (cross-service):** the routing resolution logic lives in **approval-api**
(`_resync_document`, `_routing_user_id`, `_resolved_assignee_for_step`). Rather than
duplicate it into epms-api, add a single-document variant of the existing
`POST /routing/resync-inflight`:

- **New endpoint:** `POST /routing/resync-document` body `{doc_type, doc_id}` → calls
  the existing `_resync_document(db, doc_type, doc_id)`, commits, returns its summary.
  `system_admin` gated (same as resync-inflight).

- **epms-api flow:** after `edit_record` commits a Requester change on a PR (or a PA's
  source-PR creator), the admin service calls approval-api
  `POST /routing/resync-document {doc_type: "pr"|"po"|"pa", doc_id}` over HTTP (reusing
  the existing approval-api client / proxy). `_resync_document` re-resolves and reissues
  only drifted department-derived tasks; it does **not** re-run the full engine, send
  notifications, or touch `approval_events` history beyond the skip/re-sync events it
  already writes.

- **Scope note:** for a PA, changing the source PR's creator moves routing for the PR
  **and** every doc whose routing chains through it (its PO, its PAs). The resync is
  therefore invoked for the affected document(s); the audit records which docs were
  resynced.

**Failure handling:** the field edit and the resync are reported together. If the
resync HTTP call fails, the field change is already committed (correct data wins) and
the response surfaces a warning "Requester updated; approval routing resync failed —
run Re-sync in-flight manually." (No silent success.)

---

## 6. Line items — full add / edit / delete + auto-recompute (§6)

Frontend submits the **entire** line-item array. Backend `edit_child_collection`
(`admin/service.py`) diffs by row `id`:

- row **with** `id` present in DB → update its editable fields
- row **without** `id` → insert (new `PoLineItem` / `PrLineItem` / `PaLineItem`)
- existing DB row **absent** from the payload → delete (relationship is already
  `cascade="all, delete-orphan"`)

`line_total = qty × unit_price` is **recomputed server-side per row** (frontend values
are not trusted). Then a **per-entity recompute callback** updates the header:

| Entity | Recompute |
|--------|-----------|
| **PR** | `amount = Σ line_total` |
| **PO** | `subtotal = Σ line_total`; `tax_amount = round(subtotal × tax_rate, 2)`; `total = subtotal + tax_amount` |
| **PA** | `subtotal = Σ line_total`; `tax_amount = round(subtotal × (tax_rate or 0), 2)`; `payment_amount = subtotal + tax_amount + shipping_amount + other_charges − (prepayment_applied or 0)` |

Line-item edits, header-field edits, and the recompute all run in **one transaction /
one Edit submit**, producing one audit row (before/after captures the line-item
snapshot and the recomputed header deltas).

Rounding uses `Decimal` with `ROUND_HALF_UP` to 2 dp, matching the column definitions
(`Numeric(15,2)`).

---

## 7. Approval active state — dedicated operation (§7)

Not a field PATCH. New endpoint `PATCH /api/v1/admin/{entity}/{id}/approval-state`:

- update `approval_step_idx` (header column)
- reassign **open (`is_completed = False`)** `task` rows for this document
  (`document_id = doc.id`): set `assigned_role` and/or a specific `assigned_user_id`

**Deliberately does NOT:** re-run the engine, send notifications, touch
`approval_events` history, or modify completed tasks.

Frontend shows a dedicated **Approval State** panel in the Edit drawer: current step +
list of open tasks + editable target role/user. Separate audit row (`action =
edit_approval_state`).

> Note: §5 (requester→routing resync) and §7 (manual state edit) are distinct. §5 is an
> automatic, engine-faithful re-resolution triggered by a Requester change. §7 is a
> manual override of step/assignee for stuck documents. Both are available; they do not
> conflict (§5 runs on requester change, §7 on explicit demand).

---

## 8. PO number regeneration on Vendor change (§8, approved "5-B default")

PO number format = `PO-{vendor.code}-{YYMM}-{seq:02d}`. Changing the vendor makes the
`vendor_code` segment stale.

**Default behavior (5-B): regenerate the PO number** using the new vendor's code (via
`crud/po._next_number`-equivalent logic) and **cascade-rename** every denormalized copy,
joined by `po_id` for reliability:

| Table | Column | Match |
|-------|--------|-------|
| `purchase_orders` | `number` | the PO itself |
| `purchase_requests` | `po_number` | `po_id = po.id` |
| `payment_applications` | `po_number` | `po_id = po.id` |
| `goods_receipts` | `po_number` | `po_id = po.id` |
| `invoices` | `po_number` | `po_id = po.id` |
| `tasks` | `document_number` | `document_id = po.id` (approve tasks etc.) |
| `approval_events` | `document_number` | `document_id = po.id` |
| `finance ap_invoices` | `po_number` | **string match on old number** ⚠️ (no `po_id` FK; flagged as best-effort, counted separately in the summary) |

The regeneration and cascade run in the same transaction as the vendor change. The audit
row records `cascade_summary` (old number, new number, rows updated per table).

**Opt-out:** the Edit drawer shows a checkbox **"Regenerate PO number for new vendor"**,
**checked by default**. Unchecking keeps the existing number (vendor_id/name change
only). A second confirmation dialog fires when regeneration is armed, warning that the
document identity is rewritten across N tables.

**Collision safety:** the new sequence is computed under the same prefix count logic as
creation; the write is guarded by the `purchase_orders.number` unique index (retry /
error on the astronomically unlikely race).

---

## 9. Frontend (Portal `DataMaintenance.tsx` + `data-maintenance/`)

The Edit drawer becomes sectioned:

1. **Header fields** — existing scalar inputs + new **reference pickers** (Requester /
   Vendor / Cost Center) using the overlay-dropdown + `createPortal` pattern (project
   overlay convention), backed by `GET /admin/lookup/{source}`.
2. **Line items** — editable table: add row, delete row, inline-edit fields, live
   client-side preview of recomputed line totals and header aggregates (server is
   authoritative on save).
3. **Approval state** — current step + open-task reassignment panel (§7).
4. **PO vendor** — the "Regenerate PO number" checkbox + confirmation (§8), shown only
   for PO.

All user-facing strings are English. Reference pickers, line-item editor, and approval
panel are separate components under `data-maintenance/`.

---

## 10. Audit, errors, testing

**Audit** — one row per logical change kind, full before/after snapshot:
`edit` (fields + line items), `edit_approval_state` (§7), and `edit` with
`cascade_summary` for a PO-number regeneration (§8). Requester-triggered resync (§5)
records which documents were resynced.

**Errors** (all → 422 with a clear message): unknown/absent reference id; clearing a
`NOT NULL` reference (e.g. Requester); line-item recompute overflow; PA source-PR
requester edit when the PA has no linked PR.

**Tests** (`epms-api/tests/test_admin.py` + approval-api test for the new endpoint; run
serially per the shared test-DB constraint):
- reference edit sets id **and** syncs denormalized name; unknown id → 422
- line-item add/edit/delete + PR/PO/PA header recompute each verified numerically
- approval-state step change + open-task reassignment; completed tasks untouched
- PO vendor change **with** regeneration → number + all `po_id`-joined tables renamed; **without** (unchecked) → number unchanged
- Requester change on PR → approval-api `resync-document` invoked, department-derived open task reassigned to the new department's approver (approval-api engine test)
- every path writes the expected `admin_audit_log` row

---

## 11. Out of scope

- Re-running the approval engine, sending notifications, budget-commitment recompute
- Editing historical `approval_events` rows or adding/removing/reordering events
- Changing `PA.created_by` to drive PA routing (rejected "B-2")
- OA / VMS entity edit expansion (this spec covers EPMS PR/PO/PA/GR/Invoice; the same
  framework extensions carry over later)
