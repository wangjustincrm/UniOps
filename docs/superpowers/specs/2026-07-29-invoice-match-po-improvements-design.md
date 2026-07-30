# Invoice Match-PO Improvements (total-value mode + auto-match on upload + self-match)

**Date:** 2026-07-29
**Branch:** `feature/invoice-match-po-improvements` (worktree `c:/Project/uniops-matchpo`, off `main` 6d3a9dd)
**Migrations:** none

Three coordinated features on the invoice → PO match flow, bundled into one branch because
they touch the same files (`epms/src/pages/invoices/InvoiceAllocationPanel.tsx`,
`InvoiceListPage.tsx`, `epms-api/app/api/v1/invoices.py`) and feature #3 depends on #4.

Problem #1 (invoice line allocated over a PO line → over-invoice → exception) is **kept as-is**
by user decision; no work.

---

## Feature #4 — Uploader can match their own invoice (do first; #3 depends on it)

### Problem
`match_invoice` and `list_match_candidates` gate on `is_ap OR has_open_match_task`
(`_AP_ROLES = system_admin, ap_clerk, finance_manager, finance_bp`). A non-AP user who
uploads an invoice cannot match it — an AP clerk must first assign them a match task. The user
wants: whoever uploaded an invoice may match it directly, no AP assignment.

### Design
- **Auth (backend `epms-api/app/api/v1/invoices.py`):** extend BOTH gates to also allow the
  uploader:
  - `match_invoice` (~line 257) and `list_match_candidates` (~line 357):
    allow when `is_ap OR has_open_match_task OR caller_id == inv.uploaded_by`.
- **Review behaviour:** currently `require_review = not is_ap` (~line 264), so a non-AP match
  with non-zero variance goes to `match_review` — but a self-match has **no assigner**, so the
  review-task creation (`if result.status == "match_review" and my_task is not None`) is
  skipped and the invoice would be stuck in `match_review` with no task. Fix: treat a
  self-uploader like AP for the review flag:
  `require_review = not (is_ap or caller_id == inv.uploaded_by)`.
  Result for a self-match: within tolerance → `matched`; over tolerance → `exception`
  (AP resolves later); never `match_review`. A non-AP **task-holder** (assignee) still gets
  `require_review = True` (that flow has an assigner and works today).
- **Frontend visibility (`epms`):** the match entry / "Match to PO" affordance is gated by
  `isAp` (`MATCH_ROLES`) in the list, detail, and upload flow. Add an uploader-aware helper so
  the uploader sees it for their own invoices:
  `canMatchInvoice(inv) = isAp || inv.uploaded_by === currentUser.id`.
  Apply it wherever `isAp`/`MATCH_ROLES.has(role)` currently gates the match UI for a specific
  invoice (`InvoiceListPage.tsx` list rows, the upload flow's `canMatch`, and the detail
  `MatchPanel` mount). Do NOT weaken gates that are genuinely AP-only (e.g. `assign-match`).

### Scope / guard
- Self-match is limited to `uploaded_by == caller` — never lets a user match someone else's
  invoice. `list_match_candidates` uses the same rule so the uploader can load candidate POs.
- `ApiInvoice` must expose `uploaded_by` to the frontend (verify it is already in the invoice
  response/type; the backend `InvoiceResponse` has `uploaded_by`).

---

## Feature #3 — Auto-match on upload when a PO is known (depends on #4)

### Problem
On upload, when the parser recognizes a PO number (or the user types one into the existing
`poNumber` field), the invoice is created `unmatched` and the allocation panel opens
**pre-filled but not matched** — the user must still click Confirm. The user wants it matched
on upload, no second action.

### Design (frontend `InvoiceListPage.tsx` upload flow `handleSubmit`)
Today: `if (matchedPo && canMatch && lineItems>0) { setCreatedInv(inv); return }` opens the
panel. Replace with **auto-match, no panel**:

- After `createInvoice` succeeds and the file attachment is saved, if `matchedPo` is set AND
  `canMatchInvoice(inv)` (now true for the uploader per #4):
  1. **Smart match kind:**
     - Build line-level allocations from the existing prefill heuristic (map each invoice line
       to a PO line by amount; the same logic already computed for `prefill`). If the mapping
       covers **all** invoice lines and their pre-tax `line_total`s sum to `invoice.amount`
       (balances) → **line-level auto-match**: call
       `matchInvoiceMutation.mutate({ id, allocations, gr_id? })`.
     - Otherwise (e.g. zero-price lines, or partial/ambiguous mapping) → **total-value
       auto-match**: call `matchInvoiceMutation.mutate({ id, po_id: matchedPo.id, gr_id? })`
       (legacy header-level path → one allocation of the full pre-tax amount vs `po.subtotal`).
  2. On success → `onUploaded(inv.id)` (close; the invoice is now `matched`/`exception`).
     Do **not** open the panel.
  3. On match **error** → fall back to opening the panel (`setCreatedInv(inv)`) so the user can
     resolve manually. Safety net; never leave the user stuck.
- No `matchedPo` → unchanged: land in the queue as `unmatched`.

### Notes
- The PO the user acts on is the one shown in the upload form (`poNumber` → `matchedPo`), which
  they can review/edit before submitting — so auto-matching it is user-confirmed.
- Auto-match may yield `exception` (over-tolerance) — acceptable; it is flagged for AP, still no
  second action required from the uploader.
- Reuses the existing `match` endpoint and payloads; no new backend endpoint.

---

## Feature #2 — Total-value match mode ("By line" / "By total amount")

### Problem
Some vendors (e.g. Spirax Sarco) send invoices whose line items carry **no unit price** (all
`CA$0.00`); only a header total exists. Line-to-line matching can never balance
(`assignedTotal` stays 0 ≠ `invoice.amount`), so the invoice cannot be matched.

### Design — a mode toggle on `InvoiceAllocationPanel.tsx`
Top of the panel: **`Match by: [By line] [By total amount]`**, default **By line**.

**By line (unchanged):** invoice lines + PO lines shown, line-level drag; the fee-only
"Link this invoice to a PO" dropdown when nothing is allocated. No behaviour change.

**By total amount (new):** same two-column layout, but **collapse away individual invoice lines
and PO lines** — show only headers:
- **Left:** one draggable **Invoice card** — shows the invoice pre-tax total and a live
  **Unallocated (pre-tax)** balance = `invoice.amount − Σ(total-mode allocations)`; must reach
  0 to Confirm.
- **Right:** each candidate PO as a droppable **PO card** — shows PO number, `po.subtotal`,
  **Remaining billable** = `subtotal − already_allocated_total − allocated-here`, and the
  amount allocated here.
- **Drag Invoice card → a PO card** creates/updates one allocation for that PO:
  - amount = `min(invoice unallocated balance, PO remaining billable)`, **editable inline**
    (raising it above the PO remaining → over-invoice → existing exception path).
  - Supports **one invoice → multiple POs** (drag to several PO cards; each takes its share;
    invoice balance decrements to 0).
  - Supports **one PO → multiple invoices** (a second invoice sees the PO card showing
    `already_allocated_total` by other invoices and its reduced remaining; allocates against it).
  - Each allocated PO shows its amount + an "unassign".
- **Confirm** enabled when the unallocated balance == 0 and at least one PO is allocated.

### Submit / backend
- Total-value Confirm sends the existing **`allocations`** field, one entry per allocated PO,
  each **header-level** (`po_line_id: null`), `invoice_line_id` = the invoice's first line id as
  a common anchor (verified: `invoice_po_allocations` has **no** unique constraint on
  `(invoice_id, invoice_line_id)`, so multiple allocations may share the anchor),
  `allocated_amount` = the per-PO amount, `allocated_tax: 0`.
- Backend `match()` is unchanged: header-level allocations already reference `po.subtotal`, and
  the header balance check `Σ allocated_amount + excluded == invoice.amount` gates it. Variance
  per `(po_id, po_line_id=NULL)` is cumulative across invoices → one PO billed by multiple
  invoices reconciles correctly.
- **Only backend change:** `list_match_candidates` must return, per candidate PO, an
  `already_allocated_total` = `Σ InvoicePoAllocation.allocated_amount` for that `po_id` from
  **other** invoices (`invoice_id != this`), across all `po_line_id`s (both line- and
  header-level consume the PO's value). Attach to `PoResponse` (new optional field
  `already_allocated_total`); expose on `ApiPo`. Frontend PO remaining uses it.

### Frontend
- `InvoiceAllocationPanel.tsx`: mode-toggle state; total-mode rendering (collapse lines,
  invoice card drag source, PO cards drop targets, inline-editable amounts, multi-PO,
  unallocated balance, Confirm gate); always-on pre-tax headers (invoice total on the left,
  `subtotal + remaining` per PO on the right).
- `onSubmit`: total mode emits the header-level `allocations` array (no `reference_po_id`); the
  two consumers (`MatchPanel`, `InvoiceListPage`) already forward `{ id, allocations, gr_id? }`
  unchanged — no new payload field needed.
- `ApiPo` gains `already_allocated_total?: number`.

### Out of scope (explicit, user-confirmed)
- Matching an invoice's total to a *specific PO line* (total mode is whole-PO only).
- Line-level over-invoice handling — that is Problem #1, kept as-is.

---

## Global constraints (bind all three features)

- Branch `feature/invoice-match-po-improvements`, worktree `c:/Project/uniops-matchpo`, off
  `main` 6d3a9dd. Never commit to `main`. ([[feedback_uniops_multisession_release_discipline]])
- **Zero DB migrations.**
- Frontend user-facing copy **English** ([[feedback_uniops_ui_english_only]]).
- Frontend **tsc baseline 59** must not increase; gate `tsc -p tsconfig.app.json --noEmit`
  from `epms` ([[project_uniops_pa_chain_attachments]]). No `--ignoreDeprecations` (epms is TS
  5.9.3).
- Backend tests run per [[feedback_uniops_admin_test_db_env]] (override `POSTGRES_*` to local
  docker `uniops_postgres`, `epms_test` DB, `JWT_SECRET_KEY`), serialized
  ([[feedback_uniops_test_db_concurrency]]); the relevant self-contained suite is
  `tests/test_invoice_allocations.py`.
- Do **not** push / build images / deploy without explicit user approval; release at the single
  integration point per R4/R5.

## Implementation order
#4 (auth, small) → #3 (auto-match on upload, depends on #4) → #2 (total-value mode, largest).

## Tests
- Backend `tests/test_invoice_allocations.py`:
  - #4: self-uploader (non-AP) can match own invoice → within tolerance `matched`; over
    tolerance `exception`, never `match_review`; a non-uploader non-AP still 403;
    `list_match_candidates` allows the uploader.
  - #2: `list_match_candidates` returns `already_allocated_total` per PO (0 when none; equals
    another invoice's header allocation after it matched). Multi-PO header-level allocations
    from one invoice balance and land `matched`. One PO billed by two invoices reconciles
    cumulatively.
- Frontend: tsc gate + manual smoke (dev stack mounts main checkout — smoke at integration).
