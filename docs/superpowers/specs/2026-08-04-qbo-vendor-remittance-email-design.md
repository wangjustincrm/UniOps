# QBO Vendor Email → EPMS Vendor Remittance Email Backfill — Design

**Date:** 2026-08-04
**Branch:** `feature/qbo-vendor-remittance-email` (worktree `c:/Project/uniops-qbo-email`, base: main 13815cd)
**Status:** Approved by user (approach A)

## Problem

EPMS Vendors (`business_partners`) hold a `remittance_email` used by the Payments Hub
remittance-advice sender (falls back to `contact_email` when empty — resolved in
`finance-api/app/crud/remittance.py`). Most vendors were imported without a remittance
email, while the QBO mirror (`qbo_vendors`, 1413 rows in production) already carries a
vendor `email` synced from QuickBooks. Filling these in by hand is tedious and
error-prone.

## Goal

A manual, on-demand backfill that copies `qbo_vendors.email` into
`business_partners.remittance_email` for matching vendors, with a visible result report.
No automatic/background syncing.

## Decisions (confirmed with user)

1. **Matching:** case-insensitive, whitespace-trimmed exact match on
   `business_partners.name` ↔ `qbo_vendors.display_name`. No fuzzy matching — a wrong
   remittance email sends payment advice to the wrong company.
2. **Overwrite policy:** fill only when `remittance_email` is empty
   (`NULL` **or** blank string — the EPMS frontend persists `""`). Human-entered values
   are never touched.
3. **Trigger:** manual button on the Finance QBO Mirror page (Vendors tab). No
   automatic run after sync.

## Architecture (approach A)

finance-api performs the backfill directly. `qbo_vendors` and `business_partners` live
in the same Postgres database; finance-api already has a read-only mirror model of
`business_partners` (`app/models/mirrors.py`) and multiple services sharing this table
is the established pattern (epms-api maps its `Vendor` model onto it). The write is
narrow: one nullable column, only when empty.

Rejected alternative: routing the write through a new mdm-api batch endpoint — purer
ownership, but doubles the code (HTTP client, batch API, partial-failure handling) for
no user-visible benefit.

## Backend

### Endpoint

`POST /qbo/vendor-emails/backfill` in `finance-api/app/api/v1/qbo.py`.

- Auth: `CurrentUser` (server-side), consistent with every other `/qbo/*` endpoint;
  `view_finance` remains the client-side nav gate.
- No request body. Runs synchronously (a single set-based pass over ≤ ~1500 rows —
  no background thread needed).

### Algorithm

1. Load QBO candidates: `qbo_vendors` where `deleted_at IS NULL` and
   `email` is non-empty after trim. Normalize key = `lower(trim(display_name))`;
   drop rows with empty normalized key.
2. Detect QBO-side duplicates: normalized keys appearing more than once with
   **different** emails → ambiguous, skipped and reported. Same key with the same
   email collapses to one candidate.
3. Load EPMS candidates: `business_partners` where `is_supplier IS TRUE`. Normalize
   key = `lower(trim(name))`. EPMS-side duplicate keys → ambiguous, skipped and
   reported (never guess which partner gets the email).
4. Join on normalized key. For each match where
   `remittance_email IS NULL OR trim(remittance_email) = ''` → update
   `remittance_email` to the QBO email (trimmed). Matches that already have a value
   are counted as `skipped_has_value`.
5. One transaction; commit at the end. Return the report.

### Response shape

```json
{
  "updated": [{"code": "V001", "name": "Acme Ltd", "email": "ap@acme.com"}],
  "skipped_has_value": 12,
  "ambiguous": [{"side": "qbo" | "epms", "name": "Dup Co"}],
  "unmatched_qbo": ["Some Vendor LLC"]
}
```

`unmatched_qbo` = QBO vendors with an email that matched no EPMS supplier — the
human worklist. `updated` carries code+email so the operator can spot-check.

### Non-goals / invariants

- No schema change, no migration. No link column is added; matching stays name-based.
- `business_partners` consumers unaffected: only a nullable field is written, only
  when empty. Writers elsewhere (mdm-api, epms-api vendor edit) keep working —
  a later manual edit simply wins because backfill never overwrites.
- Endpoint is idempotent: a second run finds nothing empty to fill and reports
  `skipped_has_value` instead.

## Frontend

`finance/src/pages/finance/QboMirrorPage.tsx` (+ `services/qboApi.ts`):

- A **Fill Remittance Emails** button, visible only on the **Vendors** tab, styled
  like the existing sync actions.
- On click: confirm dialog (one sentence: fills empty EPMS remittance emails from QBO,
  never overwrites) → POST → result dialog.
- Result dialog: updated count with the code/name/email rows, `skipped_has_value`
  count, ambiguous list, unmatched list (scrollable; these lists can be long).
- Button shows a spinner while the request is in flight; errors surface via the
  page's existing error pattern.
- UI copy in English (project rule).

## Error handling

- DB errors → transaction rolls back, FastAPI returns 500, frontend shows the error;
  no partial fills persist.
- Concurrent runs are harmless (idempotent, last-write-wins on identical data), so no
  lock/409 machinery.

## Testing

- Backend (`finance-api/tests/test_qbo_api.py` style, same fixtures):
  1. fills empty (NULL and `""`) remittance_email on case/whitespace-insensitive match;
  2. never overwrites an existing value (`skipped_has_value`);
  3. skips QBO-side ambiguous names (different emails) and EPMS-side duplicate names,
     reports them;
  4. ignores soft-deleted QBO vendors and empty QBO emails;
  5. unmatched QBO vendors appear in `unmatched_qbo`.
- Frontend: `tsc` gate (finance frontend, TS 5.x — no `--ignoreDeprecations` flag).

## Deployment

Standard release flow (all 15 images at one sha). No migration, no env/config change,
no Caddyfile change. After deploy: open Finance → QuickBooks → Vendors tab → click
the button once; work through `unmatched_qbo`/`ambiguous` manually in EPMS.
