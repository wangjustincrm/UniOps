# Purchase Agreement — Pickup Slips (release checklist)

## Why this exists

Phase 1A shipped `house_account` agreements with **no evidence concept at
all**: every invoice matched to one was flagged `legacy_settlement` with a
mandatory "reason for settling without receipt evidence" — while the system
offered no way anywhere to supply that evidence. The operator was required to
explain the absence of something they had never been given a chance to
provide.

This release builds the missing link. A **pickup slip** is the digital record
of a paper counter slip (plus its photo), and it plays the role goods receipt
plays on the PO route:

1. **Register** slips against a house-account agreement, with the photo or
   scan stored as an attachment and OCR pre-filling reference / date /
   amounts (new `slip` OCR mode in expense-api).
2. **Match** — the invoice-match panel now lists the agreement's `open` slips
   and the operator ticks the ones this invoice covers. Ticked slips flip to
   `reconciled` and the invoice is **no longer** flagged `legacy_settlement`.
   The reason field now appears only when **no** slip is selected, which is
   what "settled without receipt evidence" was always supposed to mean.
3. **Pay** — the PA gate refuses a house-account invoice that has neither
   slips nor an explicit legacy settlement. Pre-existing 1A invoices are all
   `legacy_settlement=True`, so **historic data keeps flowing**.
4. **Prove** — the PA attachment roll-up now walks invoice → slips → slip
   photos, so finance downloads the invoice and everything backing it in one
   bundle.

⚠️ **The health metric changes meaning.** The agreement detail page's
"settled without receipt" count previously included every house-account
invoice (it could never be anything but 100%). From this release it counts
only invoices genuinely settled with no slip. Expect the number to drop
sharply — that is the fix, not a regression.

## Matching: baseline first, accelerators second

Matching is **manual by design** — the operator ticks slips, and that always
works. Two accelerators pre-tick a suggestion, and both are deliberately
conservative:

- typing the reference printed on the invoice pre-selects the slip whose
  `slip_ref` matches (evaluated on blur/Enter, not per keystroke);
- a slip whose total equals the invoice total and whose date falls within the
  14 days before it is pre-selected **only when exactly one** slip qualifies.

When several slips qualify, **nothing** is pre-selected. A wrong pre-selection
that the operator accepts on autopilot is worse than no pre-selection: the
claimed slip is stranded at `reconciled` and no screen can release it.

The accelerators own at most one slip at a time and never remove a slip the
operator ticked themselves.

**A non-zero difference between the selected slips and the invoice asks for a
note but does not block submission.** Counter purchases routinely differ by
freight, discounts or tax rounding; blocking would simply push operators back
into the no-evidence channel.

## Who can do what

| Action | Permission |
|---|---|
| See agreements and their slips | `epms.agreement.read` |
| **Record / edit / void a slip, upload its photo** | **`epms.agreement.slip.write`** (new) |
| Approve or reject a slip pending AP review | `epms.invoice.match` |
| Create / edit the agreement itself | `epms.agreement.write` (unchanged) |

`epms.agreement.slip.write` is a **new key**, seeded to `system_admin`,
`ap_clerk` and `dept_admin`. It appears in Portal Admin → Access Control like
any other key, so the role list is yours to change without a code release —
labelled **"Record Pickup Slips (needs View Agreements)"**, because the matrix
has no notion of one key depending on another and this one is inert without
`epms.agreement.read`: no Agreements nav entry, 403 on the agreement and on
its slips, and no error message anywhere saying why. **Always tick the two
together.**

Two consequences worth stating plainly:

- **Procurement cannot record slips by default.** Creating agreements and
  recording slips are now separate capabilities. Tick `procurement_officer` /
  `procurement_manager` in the matrix if you want them to.
- **`dept_admin` is department-scoped.** It only sees agreements whose
  `department_id` matches its own. A house-account agreement with a blank or
  wrong department stays invisible to it even with both permissions ticked —
  verify against a real agreement, not just the checkbox.

Migration `0007` also **widens `epms.agreement.read`** to `dept_admin`, `cfo`
and `erp_pa_officer`. Those roles could already open a PA but not the slips
backing it, so the evidence roll-up degraded to "some attachments could not be
loaded" for them — including for the CFO, who is exactly who the evidence pack
is for. After this release the `epms.agreement.read` grant set is identical to
the `view_pa` set.

## Invoice arrives before its slips

No new mechanism — this reuses the existing invoice-match assignment. AP opens
the invoice, assigns the match to the person who can produce the slips, and
they receive a task. **The assignee must hold `epms.agreement.slip.write` to
record the missing slips** (`dept_admin` is the intended case). Assigning to
someone without it lets them match but not record.

## Deploy

**Migrations**

| Service | Revision |
|---|---|
| epms-api | `ag04_pickup_slips`, then `ag05_slip_ref_uq_active` |
| identity-api | `0007_slip_write_perm` |

All additive — new table, new nullable columns, new permission rows.
`ag05` only replaces `ag04`'s partial unique index with a narrower predicate
(same index name), so it touches no data and must simply run after `ag04`;
`alembic upgrade head` does both.

**Images to rebuild:** `epms-api`, `epms-web`, `expense-api`, `identity-api`.

**No deploy-order constraint.** `invoices.slip_ids` and
`invoices.slip_variance_reason` are additive nullable columns, and the mirror
models in expense-api / finance-api / approval-api select explicit column
lists — they neither see nor need them. Old and new images coexist safely.

**No new environment variables.**

**Post-deploy, in Portal Admin → Access Control:** confirm
`epms.agreement.slip.write` shows the intended roles ticked. The migration
seeds `system_admin` / `ap_clerk` / `dept_admin`; anything beyond that is a
deliberate choice you make there.

## Verification evidence

| Suite | Result |
|---|---|
| expense-api, full, one pass | 155 passed / 0 failed (baseline 146 + 9 new) |
| epms-api, full, one pass | 69 failed / 782 passed — failure set identical to the pre-branch baseline in both directions (69/716), so zero regressions; the branch adds 66 net passing tests |
| epms frontend `tsc -p tsconfig.app.json` | 58 errors = baseline, TS 5.9.3 |
| identity-api `test_phase2_keys.py` | 4 passed / 0 failed |

## Still owed: manual click-through

No automated coverage exists for this frontend. These need a browser and a
real slip photo before anyone should trust the feature in production:

1. Upload a real slip photo and confirm OCR pre-fills amount and date.
   `ocrService.slip()` has never been executed against a real image.
2. Correct an amount OCR misread, then submit.
3. Submit with no photo plus a reason → lands in `pending_ap_review` → AP
   approves and rejects.
4. Void an `open` slip → it leaves the candidate pool, **and its reference is
   free again**: re-record the same paper slip with the corrected amount and
   it is accepted (same for a slip AP rejected). Two *live* slips still cannot
   share one reference.
5. A slip dated more than 45 days ago shows the ageing badge.
6. Type the reference printed on an invoice → the matching slip is ticked.
7. **Open a house-account PA's attachment roll-up as `ap_clerk` *and* as
   `cfo`** and download the slip photos. Testing only as `ap_clerk` passes and
   would have hidden the permission gap this release fixes.

Known untested edge: an iPhone HEIC photo. OCR rejects it (415) and falls back
to manual entry, which is fine — but the attachment endpoint does not filter by
type, so it would be stored as a file nobody can open.
