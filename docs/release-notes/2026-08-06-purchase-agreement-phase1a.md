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

## If something fails — stop conditions and rollback

**Confirm each step before starting the next. Do not batch.**

1. **Before `migrate-prod.sh`** — record the current heads so you can tell what
   actually ran:
   ```sql
   SELECT version_num FROM alembic_version;            -- epms-api
   SELECT version_num FROM alembic_version_identity;   -- identity-api
   ```
   Take a database backup. `ag01`/`ag02` create a table and add columns; they
   are additive and low-risk, but the identity migration writes grant rows into
   a table an admin may also have edited by hand.
2. **After `migrate-prod.sh`, before `docker compose up -d`** — confirm both
   heads moved:
   ```sql
   SELECT version_num FROM alembic_version;            -- expect ag02_agreement_links
   SELECT version_num FROM alembic_version_identity;   -- expect 0006_agreement_perms
   ```
   Confirm the columns actually exist — the head row moving is not proof the
   DDL landed if the script was interrupted:
   ```sql
   \d payment_applications   -- expect agreement_id, agreement_number
   \d invoices               -- expect agreement_id, agreement_number, match_route,
                             --        match_route_auto, legacy_settlement,
                             --        legacy_settlement_reason
   ```
   **If either head did not move, do not bring services up.** Per the deploy-order
   section above, starting the new `finance-api` against a database without
   `payment_applications.agreement_id` breaks AP list, payment, batches,
   remittance and the NC AP export for **every** PA, not just agreement ones.

**If `migrate-prod.sh` fails partway.** Each alembic revision runs in its own
transaction, so a failure leaves you at the last *fully applied* revision — read
`alembic_version` to find out which, do not assume. Never hand-edit or INSERT
into `alembic_version` / `alembic_version_identity` to "register" a revision:
that strands the real DDL and every later migration then runs against a schema
that does not match what alembic believes. Fix the cause and re-run
`migrate-prod.sh`; both new epms-api migrations are plain additive DDL and both
identity statements are `ON CONFLICT DO NOTHING`, so re-running is safe.

**If you must roll the code back after a successful migration.**

> **Do NOT downgrade `ag02_agreement_links`.**

The extra columns are *inert* to the previous release: nothing in the old code
reads `agreement_id`, so leaving them in place costs nothing. Dropping them, by
contrast, breaks the **new** finance-api instantly and irreversibly for the
duration — and finance-api is the service most likely to still be running
mid-rollback. The safe rollback is **code only**: redeploy the previous image
tags and leave the schema at `ag02_agreement_links`. The same applies to
`0006_agreement_perms` — surplus permission grants for a permission key nothing
reads are harmless; revoking them is not, and re-running the migration later
will not restore grants an admin has since un-ticked.

Roll the schema back only if you are abandoning the feature entirely, and only
after every service is down and confirmed on the old image.

**If an agreement will not leave `submitted`.** The step-0 approver almost
certainly cannot read it — check `epms.agreement.read` per verification step 1
below before looking anywhere else.

## Post-deploy verification

1. **Access Control**: Portal → Access Control shows **View Agreements** and
   **Create / Edit Agreements** under EPMS. Confirm the seeded grants landed —
   `procurement_officer` and `procurement_manager` should hold both
   (identity-api migration `0006_agreement_perms`).
   **View Agreements must also be held by every role that can sit in the `agr`
   approval chain** — `dept_manager`, `director`, `gm`, `opm` — plus
   `requester`. `dept_manager` is step 0 of the default chain; without the read
   grant that approver 403s on their own task deep link and no agreement can
   ever reach `active`. Verify:
   ```sql
   SELECT role_code FROM role_permissions
   WHERE permission_key = 'epms.agreement.read' ORDER BY role_code;
   ```
   Expect 12 rows: ap_clerk, auditor, dept_manager, director, finance_bp,
   finance_manager, gm, opm, procurement_manager, procurement_officer,
   requester, system_admin. (`gm_or_opm` is deliberately **not** there — it is
   a pseudo-role the approval engine resolves into a real `gm`/`opm`, and it is
   not a `role_defs` code, so granting it would fail the migration's FK.)
2. **Approval workflow seed** — approval-api seeds `workflow_defs["agr"]` on
   boot; no migration writes it. Verify directly:
   ```sql
   SELECT workflow_defs->'agr' FROM company_config;
   ```
   Expect an array of **three** steps (default: Department Manager →
   Procurement Manager → Finance Manager).
   Portal Admin → Approval Workflows now lists **Purchase Agreement** as an
   editable key (`portal/src/pages/admin/AdminPanel.tsx`'s `ActionKey` /
   `ACTION_KEYS`). That listing is not cosmetic: `epms-api`'s
   `crud/config.py::update` replaces `workflow_defs` **wholesale** (only
   `notification_settings` is shallow-merged), so before `'agr'` was added to
   that array, **any** workflow save from Portal Admin deleted
   `workflow_defs.agr`. approval-api reseeded it on its next boot, but between
   the save and that boot the SQL above returned NULL while the chain still
   worked — a false alarm at exactly this verification step. If you see NULL
   here on a system that has been up a while, check the Portal build actually
   contains this fix before concluding the seed failed.
3. **PA approval workflow step check — REQUIRED, not conditional on
   customization.** `approval-api/app/main.py`'s `seed_default_workflows()`
   writes any missing key into `company_config.workflow_defs` on **every
   boot**, only skipping keys that already exist. `_WORKFLOW_DEFAULTS["pa"]`
   (`approval-api/app/crud/engine.py`) is:
   `dept_manager → director → gm_or_opm → finance_bp → finance_mgr` —
   **`gm_or_opm` is in the default chain.** (`workflow.py`'s separate
   `_DEFAULT_PA` = `finance_bp` + `finance_manager` is a fallback used only
   when `workflow_defs` has no `"pa"` key at all — boot seeding guarantees it
   does, so that fallback is effectively dead code and must not be read as
   "the default.")
   **This means a stock, unmodified installation is the at-risk
   configuration**, not an edge case reachable only by customizing the
   workflow. Verify:
   ```sql
   SELECT workflow_defs->'pa' FROM company_config;
   ```
   If it contains a `gm_or_opm` step, the risk is live:
   - `gm_or_opm` is a **broadcast** task (`assigned_user_id = None` — every
     active holder of that post sees it), so the task-chain visibility
     shortcut other approval steps rely on cannot help that approver find the
     PA the normal way.
   - An agreement-sourced PA's approval **routes** on the *creator's*
     department: `_routing_department_id` (`approval-api/app/crud/engine.py`)
     has branches for `doc_type` `"pr"`, `"po"`, `"pa"`/`"pa_dir"` — the
     `"pa"` branch resolves department via `PA.po_id → PO.pr_id →
     PR.department_id`, which is always `None` for an agreement-sourced PA
     (`po_id` is null by construction), so it falls through to the routing
     user's (the PA's creator's) own department.
   - PA **visibility** (who can even list/open it) instead scopes on the
     *agreement's own* `department_id` (`epms-api`'s `is_pa_visible` /
     `build_scope`).
   - A `gm_or_opm` approver whose own department differs from the
     agreement's department can therefore end up **unable to both list AND
     open** the PA they're supposed to approve.
   **Also affects the agreement document itself, not just its PAs**:
   `_routing_department_id` has no `"agr"` branch either, so submitting an
   *agreement* for approval also routes `dept_manager`/`gm_or_opm`/`director`
   steps off the submitter's own department rather than
   `PurchaseAgreement.department_id` — the same class of bug, one layer up.
   **Mitigation if this is live in your environment**: reassign the PA
   creator (or check "on behalf of" support) so agreement-sourced PAs are
   created by someone in the agreement's own department, or flag this for a
   routing fix in approval-api before agreement PAs reach a `gm_or_opm` step
   in production.

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
4. Open the agreement's detail page and click **Create PA** in the header
   action bar (visible once the agreement is admissible and has at least one
   matched-but-unpaid invoice — `AgreementDetailPage.tsx`'s `canCreatePa`).
   Select the matched-but-unpaid invoices, submit through the standard PA
   approval / payment flow. Confirm the PA needs no goods receipt and lists
   correctly in the EPMS PA list.
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
- **The validity window, by contrast, DOES block — and it is enforced by the
  admission predicate, not by a status field.** Nothing in the system writes
  `expired` or `closed` (there is no scheduler in this phase; the only status
  write is approval-api activating the agreement). So
  `epms-api/app/crud/agreement.py::_admissible_predicate` compares dates
  directly: an agreement admits new spend only while
  `today <= valid_to + grace_days`, regardless of it still reading `active`.
  Practical consequence for the cutover: **set `valid_to` far enough out to
  cover the whole backlog**, or matching will start refusing with
  *"not open for new spend"* while the agreement still looks active in the UI.
  `valid_from` is deliberately not part of the test — back-dated agreements for
  historical invoices are the point of 1A.
  The agreement detail page's Create-PA button is a deliberately *permissive*
  display gate (browser clock vs server clock), so it can offer a button the
  API then refuses with a 422 that explains why. That is the intended
  direction of the divergence.

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
   `epms/src/pages/pa/PaCreatePage.tsx` +
   `AgreementDetailPage.tsx`'s **Create PA** button): from an admissible
   agreement with at least one matched-but-unpaid invoice, click Create PA on
   its detail page and confirm it lands on `PaCreatePage` with the agreement
   pre-loaded. Confirm the PO line picker, goods-receipt picker, and
   receipt-override field are **absent**, confirm only matched-but-unpaid
   invoices are selectable, confirm an invoice already claimed by another
   open PA on the same agreement shows "Already in PA" and cannot be
   re-selected, submit, confirm the new PA appears in the EPMS PA list with
   no goods-receipt requirement, and confirm it can be approved and paid
   through the standard PA flow. Separately: confirm the button is **absent**
   on an agreement with zero matched-but-unpaid invoices, and on one that is
   `draft`/`submitted`/`in_review`/`closed`/`cancelled`/`returned`/`rejected`
   or expired past its grace window. Also confirm a mistyped/nonexistent
   `?agreement_id=` shows a real error, not an infinite "Loading agreement…".
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
