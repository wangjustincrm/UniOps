# Purchase Agreement — Agreement Receipts (release checklist)

> Supersedes `2026-08-11-agreement-pickup-slips.md`. That release was never
> deployed; this one replaces it wholesale. Do not run both checklists.

## Why this replaces the pickup-slip release

Phase 1A shipped `house_account` agreements with **no evidence concept at all**:
every invoice matched to one was flagged `legacy_settlement` with a mandatory
"reason for settling without receipt evidence" — while the system offered no way
anywhere to supply that evidence.

The first attempt at a fix built "pickup slips". At acceptance the user rejected
its shape on three counts, and this release is the answer to all three:

1. **Matching and reconciling were fused.** Linking an invoice to an agreement
   is one act; attaching the evidence is another. Matching no longer asks about
   evidence at all — it behaves exactly like the recurring and milestone routes.
2. **Entry lived in the wrong place.** Recording evidence now has its own menu
   entry, list page and create page, the way Goods Receipt does. The agreement
   detail page shows a **read-only** table plus a link to the create page — the
   same relationship a PO has with its GRs.
3. **★ The evidence type was hardcoded to counter slips.** A house account is
   not necessarily a counter-pickup account: it may take deliveries or services.
   Evidence is now a typed **agreement receipt** —
   `counter_slip` / `delivery` / `service` — sharing one table and one entry
   flow. A fourth type costs a migration adding one enum value, not a redesign.

Plus one addition the user asked for: the agreement detail page now carries the
same **Document Chain** the PR/PO/PA pages have, showing invoices, their
receipts and the payment applications raised from them.

## The model

```
One Agreement (the premise)
   ├── many Invoices   (raised against it)
   └── many Receipts   (counter slip / delivery note / service sign-off)
             ↑
   an Invoice claims N Receipts to reconcile
```

All three layers are independent — creating the agreement, receiving the
invoice, and recording evidence never block each other. **The single hard
constraint sits at payment**: raising a PA requires a house-account invoice to
either hold receipts or carry an explicit no-evidence declaration.

⚠️ **The health metric changes meaning.** The agreement detail page's "settled
without receipt" count previously included every house-account invoice (it could
never be anything but 100%). It now counts only invoices genuinely settled with
no evidence. Expect the number to drop sharply — that is the fix, not a
regression.

## Reconciling: baseline first, accelerators second

Reconciliation happens on the **invoice detail page**. The operator ticks the
receipts this invoice covers; that always works. Two accelerators pre-tick a
suggestion and both are deliberately conservative:

- typing the reference printed on the invoice pre-selects the receipt whose
  `receipt_ref` matches — evaluated on blur/Enter, not per keystroke, because an
  intermediate value can match a different, shorter reference;
- a receipt whose total equals the invoice total and whose date falls in the 14
  days before it is pre-selected **only when exactly one** qualifies.

When several qualify, **nothing** is pre-selected. The accelerators own at most
one receipt at a time and never remove a receipt the operator ticked themselves.

**A non-zero difference asks for a note but does not block submission.** Counter
purchases routinely differ by freight, discounts or tax rounding, and blocking
would simply push operators back into the no-evidence channel.

Submitting the receipt list is **full-overwrite**: the list you send becomes the
complete set, and anything dropped is released back to `open`.

## Who can do what

| Action | Permission |
|---|---|
| See agreements, their receipts, and the receipts list | `epms.agreement.read` |
| **Record / edit / void a receipt, upload its photo** | **`epms.agreement.receipt.write`** |
| Approve or reject a receipt pending AP review | `epms.invoice.match` |
| Attach receipts to an invoice / settle without evidence | the invoice's own match permission |
| Create / edit the agreement itself | `epms.agreement.write` (unchanged) |

`epms.agreement.receipt.write` is seeded to `system_admin`, `ap_clerk` and
`dept_admin`, and appears in Portal Admin → Access Control labelled **"Record
Agreement Receipts (needs View Agreements)"**. The parenthetical is deliberate:
the matrix has no notion of one key depending on another, and this key is inert
without `epms.agreement.read` — no Agreements or Agreement Receipts nav entry,
403 on the agreement and on its receipts, and no error anywhere saying why.
**Always tick the two together.**

Two consequences worth stating plainly:

- **Procurement cannot record receipts by default.** Creating agreements and
  recording evidence are separate capabilities now. Tick
  `procurement_officer` / `procurement_manager` in the matrix if you want them to.
- Migration `0007` also widens `epms.agreement.read` to `dept_admin`, `cfo` and
  `erp_pa_officer`. Those roles could already open a PA but not the evidence
  behind it, so the attachment roll-up degraded to "some attachments could not
  be loaded" — including for the CFO, who is exactly who the evidence pack is
  for. After this release the `epms.agreement.read` grant set is identical to
  the `view_pa` set.

## Invoice arrives before its receipts

No new mechanism — this reuses the existing invoice-match assignment. AP assigns
the match to whoever can produce the evidence and they receive a task. **The
assignee must hold `epms.agreement.receipt.write` to record the missing
receipts** (`dept_admin` is the intended case). Assigning to someone without it
lets them match but not record.

## Added during acceptance

The user exercised this on the dev stack before sign-off and four things came out
of it. All are in the branch.

**Receipts are clickable.** There was no receipt detail page at all — the backend
could edit a receipt and manage its photos, but nothing in the UI could reach
those endpoints. `/receipts/:id` now shows every field and photo, and allows
editing, re-attaching or deleting a photo, voiding, and AP approve/reject.
Editing is open only while a receipt is `open` or `pending_ap_review`, matching
the backend; other states explain why rather than greying out silently.

This also closed a real hole: if the photo upload failed during entry (and the
compensating retry failed too), the receipt sat there `open`, with no photo and
no reason — a payable piece of evidence that no screen could repair. Its only
exit was void-and-re-record.

**Receipts carry the vendor printed on the slip.** OCR now extracts the merchant
name, and the entry form matches it against the vendor master the same way the
invoice upload does: an exact match binds the real vendor record and is badged as
AI-filled; no match leaves the extracted text and still submits, because counter
slips routinely come from merchants nobody has set up yet.

**Mismatches are flagged.** A receipt's vendor is compared against the
agreement's. When both are bound to master data the comparison is an id
equality; only when the receipt is text-only does it fall back to a deliberately
tolerant name comparison (case- and punctuation-insensitive, ignoring store
numbers, either-contains-either) so that `Princess Auto #12` against
`Princess Auto Ltd` does not raise a false alarm. An empty vendor is never a
mismatch. This is what catches a slip from one merchant filed against another
merchant's house account — the classic misfiling error, and one that showing the
agreement's own vendor would never reveal.

The flag is a hint, not an error: the row is badged, nothing is blocked. Two
stores of the same chain are a legitimate reason for it to differ.

**Existing receipts show "Text only".** Nothing was backfilled. Guessing a
vendor id from a name is exactly the inference this design removes, and a wrong
guess would make the verdict say "no mismatch" by the wrong id — silently. Bind
them one at a time from the detail page.

## Deploy

**Migrations**

| Service | Revision |
|---|---|
| epms-api | `ag04_agreement_receipts` → `ag05_invoice_agreement_type` → `ag06_receipt_vendor` → `ag07_receipt_vendor_id` → `ag08_agreement_schedule_start` |
| identity-api | `0007_receipt_write_perm` |

★ **There IS a deploy-order constraint, unlike the superseded release.**
`ag05` adds `invoices.agreement_type`, and the new epms-api code selects that
column unconditionally. **The migration must run BEFORE the epms-api image goes
up** — an old database under new code 500s every invoice read endpoint. The
reverse order (migration first, old image still running) is safe: the old ORM
model simply ignores the extra column.

`ag04` creates `agreement_receipts` and `agreement_receipt_attachments` and adds
`invoices.receipt_ids` / `receipt_variance_reason`. It supersedes the
pickup-slip migrations, which were rewritten rather than stacked — production
sees one migration creating the final table, not a create-then-rename dance.
`ag06` and `ag07` add the receipt's `vendor_name` and `vendor_id`; both are
nullable and neither is backfilled, so they impose no ordering of their own.
`ag08` adds `purchase_agreements.schedule_start_date`, also nullable and also
not backfilled — NULL means "generate from valid_from", which is exactly the
behaviour that column did not exist for.

⚠️ **Check before deploying:** any database whose `alembic_version` still reads
`ag04_pickup_slips` or `ag05_slip_ref_uq_active`, or whose
`alembic_version_identity` reads `0007_slip_write_perm`, will fail *every*
alembic command after this merge with `Can't locate revision` — those revisions
were rewritten out of existence. Production never ran them (this feature has
never shipped), but another session's local dev database or a second worktree
may be sitting on one.

**Images to rebuild:** `epms-api`, `epms-web`, `expense-api`, `identity-api`.

**Mirror services are unaffected.** expense-api / finance-api / approval-api
select explicit column lists that contain none of the new columns. Note that
finance-api *does* write `invoices.status` (`payment_execute.py`), so it is a
real writer of the shared table — it just never touches anything this release
added.

**No new environment variables.**

**Post-deploy, in Portal Admin → Access Control:** confirm
`epms.agreement.receipt.write` shows the intended roles ticked. The migration
seeds `system_admin` / `ap_clerk` / `dept_admin`; anything beyond that is a
deliberate choice you make there — and remember to tick View Agreements
alongside it.

## Verification evidence

| Suite | Result |
|---|---|
| epms-api, full, one pass | 69 failed / 850 passed — failure set identical to the pre-branch baseline in both directions (69/716), so zero regressions; the branch adds 134 net passing tests |
| expense-api, full, one pass | 163 passed / 0 failed (baseline 155) |
| identity-api `test_phase2_keys.py` | 4 passed / 0 failed |
| epms frontend `tsc -p tsconfig.app.json` | 58 errors = baseline, TS 5.9.3 |

## Still owed: manual click-through

This frontend has no automated coverage. These need a browser and a real receipt
photo before anyone should trust the feature in production:

1. Upload a real photo and confirm OCR pre-fills reference, date and amounts.
   The `slip` OCR mode has still never been executed against a real image.
2. Correct an amount OCR misread, then submit — and confirm the page navigates
   away rather than just clearing the form.
3. Submit with no photo plus a reason → lands in `pending_ap_review` → approve
   it from the **Agreement Receipts list page** (that is now the only place AP
   review lives) → confirm it reaches `open`.
4. Void an `open` receipt, then re-record the same paper document with the same
   reference — it must be accepted (retired receipts release their reference).
5. Reject a receipt and confirm the confirmation dialog appears; a rejected
   receipt is permanently terminal by design.
6. On an invoice: tick receipts, save, and confirm the checkboxes **stay
   ticked** afterwards and the "settle without evidence" form does not appear.
7. Detach all receipts from an invoice and confirm it returns to "pending"
   without stamping the legacy flag.
8. **Open a house-account PA's attachment roll-up as `ap_clerk` *and* as `cfo`**
   and download the receipt photos. Testing only as `ap_clerk` passes and would
   hide the permission gap this release fixes.
9. Open the agreement detail page's Document Chain as a `dept_manager` who is an
   approver on one of its PAs — confirm that PA appears.
10. Record a `delivery` and a `service` receipt, not just `counter_slip` — those
    two types have structure but no real-world data behind them yet.

Known untested edge: an iPhone HEIC photo. OCR rejects it (415) and falls back to
manual entry, which is fine — but the attachment endpoint does not filter by
type, so it would be stored as a file nobody can open.
