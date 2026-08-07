# Purchase Agreement Phase 1A — release checklist

## What this phase ships

A "Purchase Agreement" document for spend that never had a purchase order:
staff collect goods at a vendor counter on a house account and the vendor
bills monthly. There is **no purchase order and no goods receipt** on this
route, and none in this phase. Every invoice matched to an agreement is
flagged `legacy_settlement` with a mandatory reason — that reason is the only
audit trail standing in for a goods receipt. Payment Applications can now be
created directly from an agreement and paid through the normal PA approval /
payment pipeline.

## Migrations (both required, in order)

- **epms-api**: `ag01_purchase_agreements` → `ag02_agreement_links`, chained
  onto `nc02_nc_cutover`.
- **identity-api**: `0006_agreement_perms`, chained onto
  `0005_procurement_officer_pa`.

Run both via `migrate-prod.sh`. Re-verify the epms-api head before deploying —
another branch may have landed a migration since 2026-08-06.

## ⚠️ Deploy order is now load-bearing

`finance-api` mirrors `payment_applications.agreement_id`
(`finance-api/app/models/pa.py`) and every one of its `select(PaymentApplication)`
call sites now selects that column:

- `app/crud/ap.py` (AP list)
- `app/crud/payment.py`, `app/crud/payment_execute.py` (payment execution)
- `app/crud/payment_batch.py` (payment batches)
- `app/crud/remittance.py`, `app/api/v1/remittance.py` (remittance)
- `app/services/nc_ap_export.py` (NC AP export)

**If the finance-api image starts before `ag02_agreement_links` has run on the
shared database, every one of those reads fails with `UndefinedColumn` for
`payment_applications.agreement_id`** — not just agreement-sourced PAs. AP
list, paying anything, payment batches, remittance, and the NC AP export all
break simultaneously, for every PA regardless of route.

**Migrations must complete before any service (epms-api, finance-api,
approval-api) is brought up on the new image.** Follow the standard order —
`migrate-prod.sh` first, then `docker compose up -d` — do not reorder this
for this release.

## Post-deploy verification

1. **Access Control**: Portal → Access Control shows **View Agreements** and
   **Create / Edit Agreements** under EPMS. Confirm the seeded grants landed —
   `procurement_officer` and `procurement_manager` should hold both
   (identity-api migration `0006_agreement_perms`).
2. **Approval workflow seed** — approval-api seeds `workflow_defs["agr"]` on
   boot; no migration writes it. Verify directly:
   ```sql
   SELECT workflow_defs->'agr' FROM company_config;
   ```
   Expect an array of **three** steps (default: Department Manager →
   Procurement Manager → Finance Manager).
   **Portal Admin's Approval Workflows screen will not show it.**
   `portal/src/pages/admin/AdminPanel.tsx`'s `ActionKey` type and
   `ACTION_KEYS` array are a hardcoded list —
   `'pr' | 'po' | 'pa' | 'pa_dir' | 'exp' | 'mil' | 'trv' | 'tra' | 'cfm' |
   'budget_plan' | 'vms_visit'` — and do **not** include `'agr'`. The chain is
   live and enforced by approval-api regardless, but nobody can view or edit
   it from Portal Admin until a line is added to that array. Confirmed by
   reading the file, not assumed — this is a known gap, not a regression from
   this release.
3. **PA approval workflow step check (operator action, not automated)** — if
   the *configured* PA workflow (`company_config.workflow_defs["pa"]`,
   editable from the same Portal Admin screen) contains a `gm_or_opm` step:
   that step is a **broadcast** task (`assigned_user_id = None`, everyone
   holding the role sees it), so the task-chain visibility shortcut other
   approval steps rely on cannot help that approver find the PA. Combined
   with the fact that an agreement-sourced PA's approval **routes** on the
   *creator's* department (the generic routing fallback — `"agr"`/`"pa"`
   department routing isn't in approval-api's per-doc-type branch list) while
   PA **visibility** scopes on the *agreement's own* `department_id`, a
   `gm_or_opm` approver could end up unable to both list AND open the PA.
   **The default PA workflow is `finance_bp` + `finance_manager`, both
   unrestricted roles that see every PA regardless of department scoping —
   the default installation is unaffected.** Only check this if your
   `workflow_defs["pa"]` has been customized to include a `gm_or_opm` step.

## Cutover script for Princess Auto (the backlog this phase exists for)

1. Create the agreement:
   - `agreement_type` = `house_account`
   - `vendor_reference` = the **existing open PO number** — the vendor keeps
     printing what it already prints, no re-registration needed.
   - `not_to_exceed` = last year's actual spend with this vendor.
2. Take it through the `agr` approval chain (Department Manager →
   Procurement Manager → Finance Manager by default) to `active`.
3. Match the backlog invoices to the agreement, one `legacy_settlement`
   reason each (mandatory — MatchPanel's Agreements tab enforces this).
4. Create a Payment Application from the agreement (agreement detail page,
   or `?agreement_id=` deep link into `PaCreatePage`), select the
   matched-but-unpaid invoices, submit through the standard PA approval /
   payment flow. Confirm the PA needs no goods receipt and lists correctly
   in the EPMS PA list.
5. **Only after the backlog is clear**, close the old open PO
   (`status = 'closed'`). This removes it from the invoice match candidate
   pool so future statements route to the agreement instead.

## Known limitations shipped deliberately

- **No pickup slips.** Every agreement-route invoice match is flagged
  `legacy_settlement` with a mandatory reason. This is the 1A escape hatch
  that lets the backlog get paid before slip reconciliation exists.
- **No automatic route detection.** `MatchPanel`'s PO/Agreement tabs are a
  manual choice; the panel only pre-selects the Agreements tab when there are
  zero PO candidates and at least one agreement candidate. Reference-number
  auto-resolution is explicitly deferred to 1B.
- **The not-to-exceed ceiling warns but never blocks.** Passing it is
  recorded and shown (on the agreement detail page, the match panel, and the
  PA create page) but does not disable submit/approve/match/create anywhere.

## ⚠️ Follow-up that must not be dropped

Once the next phase ships slip reconciliation, **narrow `legacy_settlement`**:
restrict which role may set it, and surface its count per agreement (the
agreement detail page already shows a count badge — extend that pattern to
gate who can *create* one). Left open, `legacy_settlement` becomes the normal
way to skip reconciliation entirely rather than the 1A backlog escape hatch it
was built as. Currently any user who can match invoices can set it.

## Verification steps that need a human

This project has no frontend test framework — zero vitest/jest/testing-library
files anywhere in `epms/`. None of the three frontend tasks in this phase
(agreement pages, the match-panel route switch, PA-from-agreement) were
verified by actually rendering a browser. After deploy, a person must click
through:

1. **Agreement pages** (`epms/src/pages/agreements/`): create an agreement,
   edit it while `draft`, submit it, approve it through all three steps as
   each role, confirm it reaches `active` (not `approved`), confirm Cancel
   is offered on `draft`/`returned`/`submitted` and refused elsewhere, confirm
   Return → resubmit round-trips.
2. **Invoice match panel** (`epms/src/pages/invoices/MatchPanel.tsx`): upload
   an invoice for a vendor with both an open PO and an active agreement,
   confirm both tabs list candidates; upload one for a vendor with only an
   agreement, confirm the Agreements tab auto-selects; match through the
   Agreements tab, confirm the mandatory reason field blocks submission when
   blank, confirm the resulting invoice shows "Settled without receipt" on
   its detail page.
3. **PA creation from an agreement** (this task —
   `epms/src/pages/pa/PaCreatePage.tsx`): open `?agreement_id=<id>` (from the
   agreement detail page or a matched invoice), confirm the PO line picker,
   goods-receipt picker, and receipt-override field are **absent**, confirm
   only matched-but-unpaid invoices are selectable, confirm an invoice
   already claimed by another open PA on the same agreement shows "Already in
   PA" and cannot be re-selected, submit, confirm the new PA appears in the
   EPMS PA list with no goods-receipt requirement, and confirm it can be
   approved and paid through the standard PA flow.
4. **Over-ceiling warning**: match/create against an agreement whose consumed
   amount exceeds its not-to-exceed, confirm the warning displays and nothing
   is blocked.

## What was verified by driving the real API (not by reading)

`epms-api`'s running Docker container mounts a different worktree
(`C:\Project\uniops-mrp-phase0`), so it could not exercise this code. A
throwaway database and a real `uvicorn` process running this worktree's
`epms-api` were used instead (seeded, exercised, then torn down — no
credentials or persistent state left behind):

- `POST /api/v1/pa` with `agreement_id` + one matched invoice → **201**,
  `po_id`/`po_number` null, `agreement_id`/`agreement_number` populated,
  `payment_amount` computed correctly from subtotal+tax.
- `POST /api/v1/pa` with **neither** `po_id` nor `agreement_id` → **422**
  (`Provide exactly one of po_id or agreement_id`).
- `POST /api/v1/pa` with **both** → **422** (same message).
- Agreement route with `pa_type != "regular"` → **422**.
- Agreement route with **no** `invoice_ids` → **422**.
- Agreement route with an invoice matched to a *different* agreement → **422**
  (names the invoice and the agreement it should have matched).
- Agreement route against a `draft` (inadmissible) agreement → **422**.
- `GET /api/v1/pa?search=<agreement number>` and
  `GET /api/v1/invoices?agreement_id=<id>` — the two read endpoints
  `PaCreatePage.tsx`'s agreement mode depends on for its "already claimed"
  invoice lock and its invoice picker — both returned the expected shape with
  `agreement_id`/`agreement_number` populated.

Full transcript and file-by-file changes: see
`.superpowers/sdd/2026-08-06-purchase-agreement-phase1a/task-10-report.md`.
