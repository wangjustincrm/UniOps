# Payments Hub — Vendor Filter and Multi-Select Remittance

Increment on the shipped remittance feature. Branch `feature/batch-payment-remittance`,
worktree `C:/Project/uniops-remittance`.

## Problem

On the Payments hub an operator can send remittance for one payment (the row's drawer) or
for a whole batch, but not for an arbitrary set of payments to one vendor. A vendor paid
across several separate payments should be able to receive one remittance advice covering
all of them, and the operator needs to find that vendor's payments quickly.

## Two asks

1. A vendor filter on the hub.
2. Row checkboxes plus a "send remittance for the selected payments" action that produces
   one aggregated advice.

## Decisions (from the user)

- A multi-select send is restricted to a single payee. Selecting payments that resolve to
  more than one payee is rejected, not silently split.
- The send is logged under a new `selection` scope, so the existing double-send guards keep
  working.

## Design

### Backend

**New scope.** `app/models/remittance.py` gains `SCOPE_SELECTION = "selection"`. No
migration — `scope_kind` is already `varchar(10)` and `"selection"` fits.

**Record resolution.** A selection is a list of ids, so it cannot go through
`resolve_scope(scope_kind, scope_id)` (single uuid). Add
`resolve_records(db, ids) -> list[PaymentRecord]` in `app/crud/remittance.py`: the completed
records among `ids`, ordered by `created_at`. It does not invent a scope.

**Deterministic scope id.** The log key is `(scope_kind, scope_id, recipient_kind,
party_id)`. A selection has no natural id, so derive one deterministically from the sorted
record ids: `uuid.uuid5(_SELECTION_NS, ",".join(sorted(str(i) for i in ids)))` with a fixed
namespace constant. Re-sending the exact same selection therefore hits the same row and
upserts (resend semantics, `attempts` increments); a different set is a different row. The
derivation lives in one helper so the preview and the send agree.

**Generalize the cross-scope guard.** `last_send_for_group` currently finds the "other"
scope's row via `_other_scope(scope_kind)`, which is binary (batch↔payment). With a third
scope that is wrong — a selection send must still see a prior batch OR payment send of the
same records. Change the cross lookup from `scope_kind = :other` to `scope_kind != :current`
(and drop `_other_scope`). The containment match on `payment_record_ids` already does the
real work; widening the scope filter to "any scope but this one" generalizes it to N scopes
without double-counting the exact-key `same` row. This is the safety-critical change —
review it specifically.

**Endpoints** (`app/api/v1/remittance.py`), body-based since the scope is a list:

- `POST /payments/remittance/selection/preview` — body `{payment_ids: [uuid, …]}`. Resolve
  the records, `build_groups`, and if the result has more than one group return **400**
  "Selection spans more than one payee; send them separately." Otherwise return the same
  preview shape the other scopes return, with `reference` set to something meaningful for an
  ad-hoc selection (e.g. the vendor name and a count — decide and keep it out of the vendor
  email per §3 of the original spec, which keeps internal numbers out; a plain
  "N payments" reference is fine). Compute the deterministic scope id and use it for the
  `last_send` lookup so an already-sent selection shows correctly.
- `POST /payments/remittance/selection/send` — body `{payment_ids: [uuid, …], resend?: bool,
  recipients?: […]}`. Same one-payee validation. Resolve, build, and call `send_groups`
  with `scope_kind="selection"` and the derived scope id. The per-payee `resend` flag from
  the last round still applies.

Both require the same `_authorize` (payment authority) the other remittance endpoints use,
and both must be registered before the catch-all `GET /{payment_id}` like every other
literal route in the file.

**Route-order note.** These live under `/payments/remittance/selection/...`, which does not
collide with `/{payment_id}`, but keep them with the other remittance routes at the top of
the include for consistency.

### Frontend (`finance/`)

**Vendor filter.** The hub's `PaymentFilters` already send `vendor_id`, and the backend
`get_all` already honours it. Add a vendor picker to the filter bar. Vendors come from mdm
via whatever client the finance app already uses for master data — check `finance/src/lib/api.ts`
for an existing mdm/vendor fetch before adding one. If none exists, a lightweight typeahead
hitting the vendor list endpoint is fine; do not build a heavy component. Changing the
vendor resets to page 1 like the other filters, and feeds `/summary` and `/export` too.

**Multi-select.** Add a checkbox column to the payments table and a header select-all that
selects the current page. Track selected payment ids in page state. When a selection exists,
show a bar with the count and a "Send remittance" action. Disable or warn if the selected
rows span more than one payee (the backend rejects it anyway, but the UI should not invite
the 400 — the vendor filter makes staying within one vendor easy). Employee/claim payments
count as their own payee.

**Selection scope in the panel.** `RemittanceScope` in `finance/src/services/remittance.ts`
is currently `{ kind: 'batch'|'payment', id }`. Add a third shape
`{ kind: 'selection', paymentIds: string[] }`, and have the service post to the selection
endpoints with the id list in the body when that shape is used. `RemittancePanel` keys its
react-query cache on the scope; make sure the selection variant produces a stable key from
the sorted ids so a re-open of the same selection reuses the cache and the preview/send agree
(mirror the backend's deterministic id). The panel's existing already-sent / resend / block
behaviour must work unchanged for this scope — it reads `last_send` from the preview, which
the backend fills via the generalized guard.

Everything user-facing is English. Amounts are strings; `Number()` before formatting.

## Testing

Backend (`tests/test_remittance.py`, `tests/test_payments_hub.py`):

- A selection of two payments to one vendor previews as a single group with both invoices
  and the summed total.
- A selection spanning two vendors returns 400.
- Sending a selection writes one `selection`-scope log row whose `payment_record_ids` holds
  both ids; re-sending the same selection upserts (one row, `attempts == 2`) rather than
  duplicating.
- **The cross-scope guard, both new directions:** a payment already sent under a `batch`
  scope is reported as already-sent when it appears in a `selection` preview, and an
  unqualified selection send of it returns `skipped` with no mail; `resend: true` sends.
  Prove the guard test fails against the old binary `_other_scope` code before trusting it.
- The existing batch↔payment guard tests still pass (the generalization must not regress
  them).
- 403 without payment authority on both selection endpoints.

Frontend: typecheck at 0 new errors (`--ignoreDeprecations 6.0`, finance is TS 6.0.3). No
browser test harness exists; the real interaction is on the user's dev stack.

## Out of scope

- Cross-vendor aggregated sends (explicitly rejected).
- Persisting or naming a selection beyond the send log.
- Bulk actions other than remittance (no bulk void/export-of-selection).
