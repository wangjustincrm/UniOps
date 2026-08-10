# Delete unapproved Travel Applications (OA)

**Date:** 2026-08-07
**Branch:** `feature/oa-tra-delete` (base `origin/main` = d57578d)
**Scope:** expense-api + oa frontend. No migration.

## Problem

A Travel Application (TRA) can be created but never withdrawn. The engine
already supports `recall` (submitted/in_review → draft) and `cancel`
(draft/returned → cancelled), but neither is exposed in the OA UI, so a TRA
raised by mistake sits in the list forever and keeps showing up in approvers'
task inboxes.

Users want to remove such an application outright. Decided with the requester:

- **Hard delete** — the record disappears from the database, not a `cancelled`
  status flag. Consequence accepted: submitted applications an approver has
  already seen also vanish without a trace, and the claim number is retired
  (numbering takes max-suffix + 1, so the gap is permanent).
- **Deletable while unapproved** — `draft`, `returned`, `submitted`,
  `in_review`. Approved and terminal states are not deletable.
- **No deletion reason** — hard delete leaves nowhere durable to store it
  (`approval_events` is purged too), so the dialog is a plain confirmation.
- **Button on both** the Travel Applications list and the detail page.

## Backend

### `DELETE /api/v1/expenses/{claim_id}` (expense-api)

New endpoint in `app/api/v1/expenses.py`. Returns `204 No Content`.

| Rule | Behaviour |
|---|---|
| Not found | 404 |
| `claim_type != "TRA"` | 409 — EXP/MIL/TRV carry budget and payment consequences; this feature deliberately does not open hard delete for them |
| Status not in `draft`/`returned`/`submitted`/`in_review` | 409 with the current status in the message |
| Actor is neither `claim.employee_id` nor `system_admin` | 403 |
| A TRV references this TRA (`travel_application_id`) | 409 |

The TRV check is defence, not a live path: the TRV gate in `create_expense`
requires the referenced TRA to be `approved`, and `approved` is not deletable.
But the FK is `ON DELETE SET NULL`, so if the two rules ever drift apart a
submitted reimbursement would silently lose its authorization basis. The check
costs one query.

Authorization uses `employee_id`, matching `update_expense` and
`delete_claim_attachment`. Approvers do not get delete — they have
return/reject.

### Cascade

Reuse the delete path Data Maintenance already uses for expense claims
(`app/admin/registry.py::_claim_delete`):

1. `purge_shared_refs(db, claim.id)` — deletes `tasks` and `approval_events`
   rows by `document_id`. These are approval-api's polymorphic tables with **no
   foreign key** to `expense_claims`; skipping this leaves orphaned approve
   tasks in approvers' inboxes.
2. `db.delete(claim)` — FK `ON DELETE CASCADE` removes `expense_line_items`,
   `expense_trip_items`, `expense_travelers`, `expense_attachments` and
   `expense_approval_events`.

Extracted into `app/crud/expense.py::delete_claim(db, claim)` so the endpoint
and the admin registry share one implementation rather than two copies that
can drift.

### Attachment files

Before deleting the row, call `delete_from_file_server(file_id, token)` for
each attachment that has a `file_id` — the same helper
`delete_claim_attachment` uses. Best-effort: a failure is logged and does not
block the delete, since the claim row going away matters more than a stray
blob. Without this, file-api accumulates orphaned files (the Data Maintenance
path has this gap already; this is a user-facing path and will be used far
more often).

### `can_delete` — computed server-side, in one place

Extract a pure helper `_can_delete_claim(claim, user_id, role) -> bool` holding
the type + status + ownership rules (no queries), and use it in three places:

1. The `DELETE` endpoint's gate.
2. `GET /{claim_id}/permissions` — `ClaimPermissions` gains `can_delete: bool`,
   so the detail page renders its button from a flag.
3. `ExpenseClaimListItem` gains `can_delete: bool` (default `False`), filled in
   by `list_expenses` on both visibility branches after `model_validate`.

Putting it on the list item rather than exposing `employee_id` keeps every
permission decision on the server — the list page needs no notion of who the
current user is, and there is no client-side role logic to drift.

The helper deliberately does **not** run the TRV-reference query: that would be
one extra query per row on every list render, for a case the status rule
already makes unreachable. Consequence: in the impossible-in-practice case
where a TRV does reference a deletable TRA, the button shows and the delete
returns 409. The frontend surfaces the error message.

## Frontend

### `TravelApplicationsListPage.tsx`

- New trailing **Actions** column with a trash icon button.
- Rendered from the row's `can_delete` flag. No client-side status or role
  checks.
- `e.stopPropagation()` in the handler — the row already navigates to the
  detail page on click.
- Confirmation dialog naming the claim number and stating the delete cannot be
  undone. Follows the EPMS confirm-dialog pattern
  ([EPMS is the style template](feedback_uniops_epms_is_style_template)); use
  the shell `Button`, not a bare `<button>`.
- On success invalidate the `travel-list` query. If the last row of a page is
  deleted, step back a page when `page > 1`.

### Travel Application detail page

`/travel/:id` renders `ExpenseDetailPage`. Add a Delete button in the header
action area, shown when `permissions.can_delete` is true. On success navigate
back to `/travel` and invalidate `travel-list`.

The button must not appear for EXP/MIL/TRV claims rendered by the same
component — `can_delete` is false for them server-side, which handles it.

## Testing

Backend (`expense-api/tests/test_tra_delete.py`):

- Delete succeeds from each of `draft`, `returned`, `submitted`, `in_review`
- `approved` → 409; `cancelled` → 409
- Non-TRA claim type → 409
- Non-owner, non-admin → 403; `system_admin` deleting someone else's → 204
- After deleting a submitted TRA, `tasks` and `approval_events` rows for that
  `document_id` are gone (positive assertion on counts, not "no error")
- Child rows (travelers, trip items) are gone
- A TRV referencing the TRA → 409, and the TRA still exists afterwards
- `GET /expenses?type=TRA` returns `can_delete: true` on my own draft and
  `false` on a co-worker's, for the same row set

Frontend: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
must report 0 errors (the bare invocation reports one pre-existing `baseUrl`
deprecation from tsconfig).

Run the backend suite against the baseline commit in the same container and
compare failure counts — do not read "no output" as passing.

## Out of scope

- Exposing `recall` / `cancel` buttons in the UI. They remain available via the
  API. Revisit if users ask to withdraw an application without destroying it.
- Hard delete for EXP / MIL / TRV.
- Notifying approvers that a pending application was deleted. Matches existing
  `recall` behaviour, which also silently clears their task.
