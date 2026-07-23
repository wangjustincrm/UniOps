# Payments Hub and Remittance Advice Email

Date: 2026-07-22
Branch: `feature/batch-payment-remittance`
Worktree: `C:/Project/uniops-remittance`

## Problem

When money goes out of UniOps, nobody tells the payee. Vendors have no way to know which
of their invoices were just paid, so AP fields phone calls and emails asking "did you pay
invoice X?".

Payments leave the system through two entry points, and both need to notify:

- **Batch payment** — Finance › Payment Batches, `POST /payments/batches/{id}/execute`.
- **Single payment** — the Process / Confirm Payment modal on the EPMS PA detail page (and
  the OA Direct PA equivalent), which forwards through epms-api / expense-api to
  `POST /payments/execute`.

Finance also has no place to see payments as a whole. Payment Batches shows one slice —
payments made through a run — and nothing shows single payments at all. There is no
Payments page in the Finance app today. So the same gap that hides OA Direct PA payments
from remittance also hides them from AP entirely.

## Goal

Two things:

1. Give Finance a single Payments hub listing every payment the system has made, whatever
   its entry point.
2. After a payment executes successfully, by either entry point, let the AP operator
   confirm a preview and have UniOps email each payee a remittance advice: which vendor
   invoice numbers were paid, for how much, on what date.

The hub is also the catch-all entry point for sending remittance on payments whose
originating screen has no dialog.

## Scope

In scope:

- Per-payee aggregated remittance email after batch execution *and* after single-payment
  execution, behind a confirm dialog in both cases.
- Vendor payees (`doc_kind` = `pa` / `pa_dir`) and employee payees (`doc_kind` =
  `expense_claim`), with separate templates.
- A dedicated remittance sender address, separate from the shared SMTP server settings.
- A send log with per-payee status, live email-configured status, refresh, and resend.
- A Finance Payments hub: filterable transaction list of every payment record, per-currency
  summary cards, a remittance status column, CSV export, and a detail drawer that can send
  or resend remittance.

Out of scope (YAGNI):

- Configurable email templates.
- PDF attachments.
- Multiple recipients per vendor.
- A scheduled retry queue.
- Voiding or reversing a payment from the hub. The hub is read-plus-remittance only;
  reversal touches posting and belongs in its own change.
- A vendor-rollup view on the hub. The transaction list plus filters covers the reconciling
  case for now.

## Design

### 1. Trigger and orchestration

Neither `POST /payments/batches/{id}/execute` nor `POST /payments/execute` sends anything.
Both keep their current behaviour exactly. Payment must never be coupled to SMTP
availability.

Sending is a separate, explicit step in both flows:

- **Batch** — Finance's Payment Batches page, after a successful execute and only when the
  company switch is on, fetches the remittance preview for the batch and opens the confirm
  dialog.
- **Single** — the EPMS PA detail Process modal does the same thing after Confirm Payment
  succeeds, for the one payment record it just created.

The operator reviews recipients and lines, then presses **Send**. Sending always happens
after the payment transaction has committed — the same ordering bug that sent
invoice-reassignment mail to the previous assignee is avoided by construction.

Failure is never blocking: an SMTP error on one payee is recorded against that payee and
the remaining payees still go out.

### 2. Payee aggregation

Remittance is anchored on **`payment_records`**, not on batch lines. That table is the one
artifact both entry points produce, and it already carries `batch_id` (NULL for a single
payment), `doc_kind`, `doc_id`, `vendor_id`, `amount`, `currency`, and `payment_date`. A
batch is simply the set of records sharing a `batch_id`; a single payment is a set of one.
Everything downstream — grouping, templates, block rules, logging, resend — is one code
path.

Only records with `status = 'completed'` are considered. A batch line that failed produces
no record, so failed payments are never announced.

- `doc_kind` in (`pa`, `pa_dir`) — grouped by `payment_applications.vendor_id`, one
  email per vendor. Detail rows come from each PA in the group.
- `doc_kind` = `expense_claim` — grouped by `expense_claims.employee_id`, one email per
  employee, using a separate template.

Vendor invoice numbers are resolved by mapping each PA's `invoice_ids` to
`invoices.vendor_invoice_number`. The existing `crud.payment_batch._vendor_inv_no_map`
already does this for a list of PAs and is reused as-is; the batch-line-specific
`vendor_inv_no_for_lines` wrapper stays where it is, serving the existing batch UI.

Recipient resolution:

- Vendor: `business_partners.remittance_email`, falling back to `contact_email`. Both
  empty means the vendor is reported as blocked and cannot be selected for sending.
- Employee: `users.email`. Empty means blocked, same treatment.
- CC: a single company-wide AP address from configuration, applied to every email.

### 3. Email content

Vendor template detail table — deliberately free of internal document numbers, since PA
numbers mean nothing to the vendor:

| Vendor Invoice No | Payment Date | Amount |
| --- | --- | --- |

One row per PA in the group. If a PA carries several invoices, its numbers join with
`, `.

A vendor invoice number is mandatory for every vendor row, Direct PAs included: a
remittance line without one cannot be reconciled by the vendor's invoicing system, so
sending it is worse than sending nothing. A PA with no resolvable invoice number blocks
its payee group (see §7) instead of rendering a placeholder. Employee claims are the only
payee rows exempt — they carry Claim No, which is the meaningful reference internally.

The email also states the batch number, payment date, payment method, currency, and the
group total. It does not disclose bank account details.

Employee template is the same shape but lists Claim No instead of Vendor Invoice No,
since internal document numbers are meaningful to employees.

All user-facing strings are English.

### 4. Sender configuration

SMTP server parameters (host, port, TLS) are shared with the rest of the system: read
`company_config`, preferring the outbound `po_smtp_*` block used for PO emails and
falling back to the internal `smtp_*` block.

The from address is configured independently, in a new `company_config.remittance_config`
JSONB column:

```json
{
  "enabled": true,
  "from_email": "ap@canadaroyalmilk.com",
  "from_name": "Canada Royal Milk AP",
  "cc_email": "ap@canadaroyalmilk.com",
  "smtp_user": null,
  "smtp_password": null
}
```

`smtp_user` / `smtp_password` are optional overrides. Many SMTP servers reject a message
whose From differs from the authenticated account; when left empty the shared credentials
are used.

The column is `JSONB NOT NULL DEFAULT '{}'`, and every consumer reads through
`.get(key)` with an explicit fallback. A non-null scalar default would shadow the switch
the way `notification_channel` shadowed `default_channel`.

### 5. Data changes

**mdm migration** — add `business_partners.remittance_email VARCHAR(255) NULL`. Expose it
in the mdm business partner schemas and in the Vendor maintenance page so AP can fill it
in later.

**epms migration** — add `company_config.remittance_config JSONB NOT NULL DEFAULT '{}'`.
Expose it through the existing epms config endpoints and add a Remittance section to the
Company Settings page (switch, from address, from name, CC address, optional credential
override).

**finance migration** — new table `payment_remittance_notifications`:

| column | type | note |
| --- | --- | --- |
| id | uuid pk | |
| scope_kind | varchar(10) | `batch` \| `payment` |
| scope_id | uuid | `payment_batches.id` or `payment_records.id`, indexed with scope_kind |
| recipient_kind | varchar(10) | `vendor` \| `employee` |
| party_id | uuid | vendor_id or employee_id |
| party_name | varchar(255) | snapshot |
| email | varchar(255) | address actually used |
| payment_record_ids | jsonb | payment records covered by this email |
| amount | numeric(15,2) | group total |
| currency | varchar(10) | |
| status | varchar(10) | `sent` \| `failed` |
| error | varchar(500) | nullable |
| attempts | integer | incremented on resend |
| sent_at | timestamptz | nullable |
| created_by | uuid | |
| created_at / updated_at | timestamptz | |

Unique on `(scope_kind, scope_id, recipient_kind, party_id)`; a resend upserts the row and
bumps `attempts`. Blocked payees are not written to this table — their state is derived
live (see §7), so fixing a vendor's email immediately clears the block.

A GIN index on `payment_record_ids` backs the hub's "was this payment notified?" lookup,
which is a JSONB containment query.

No foreign key is declared on `scope_id`, since it points at one of two tables depending
on `scope_kind`. Rows are cleaned up by scope kind rather than by cascade.

`down_revision` for each migration is verified against the real alembic head of that
service before writing, not guessed from filenames.

**finance read-only mirrors** — add `BusinessPartner` (`id`, `code`, `name`,
`contact_email`, `remittance_email`, `is_supplier`), add `email` to the existing `User`
mirror, add `remittance_config` to the existing `CompanyConfig` mirror. Every mirror
column is checked against `information_schema` on the shared database before the model is
written; `CompanyConfig` in particular has no timestamp columns, so no `TimestampMixin`.

### 6. Email transport

New `finance-api/app/services/email.py`, a minimal port of the send function from
`epms-api/app/services/email.py`, carrying over the TLS mode decision that has already
cost one incident: port 465 means implicit TLS (`use_tls`), any other port with TLS
enabled means STARTTLS (`start_tls`). A comment names the source file so the two do not
silently diverge. Only the send primitive is ported — no MFA or PO template code.

### 7. Endpoints (finance-api, `/payments`)

**Existing list endpoint — bug fix first.** `PaymentResponse` declares `pa_id`,
`pa_number`, `vendor_id`, and `vendor_name` as required, but all four columns are nullable
on `payment_records` and are NULL for every expense-claim payment. `GET /payments`
therefore raises a validation error as soon as one claim payment falls in the page. This
is fixed as part of the hub work, not deferred: the four fields become optional and the
response gains the columns the hub needs — `doc_kind`, `doc_number`, `batch_id`,
`bank_account_id`, `payment_date`, `entity_id`, and `payee_name` (vendor name, or the
claim's employee name when the payment is a claim).

**Hub filters.** `GET /payments` gains query parameters: `date_from` / `date_to` on
`payment_date`, `doc_kind`, `currency`, `payment_method`, `status`, `bank_account_id`,
`batch_id`, `source` (`batch` when `batch_id` is set, `single` when it is NULL),
`remittance` (`sent` / `not_sent`), and `q` for a case-insensitive match on
document number, payee name, or reference.

A payment record counts as notified when a `payment_remittance_notifications` row with
`status = 'sent'` contains its id in `payment_record_ids`, regardless of whether that row
came from a batch scope or a payment scope. The lookup is a JSONB containment query backed
by a GIN index on `payment_record_ids`. `blocked` is deliberately **not** a filter value:
block reasons are computed live per payee and evaluating them across an unbounded result
set would be costly. The list computes them only for the rows on the current page, to
render the status column. Existing `pa_id` / `vendor_id` parameters and
the current pagination are kept. Default sort is `payment_date` descending, then
`created_at` descending.

`GET /payments/summary` returns, for the same filter set, the per-currency count and total
so the hub's summary cards do not depend on the current page.

`GET /payments/export` returns CSV for the same filter set, ignoring pagination. It streams
rather than materializing the whole result set.

**Remittance endpoints.** Two scopes, one implementation. The scope resolves to a set of `payment_records`, and
everything after that is shared:

- `GET /batches/{batch_id}/remittance/preview` — records with that `batch_id`.
- `GET /{payment_id}/remittance/preview` — that single record.

Route ordering matters: `/{payment_id}/remittance/preview` is more specific than the
existing catch-all `GET /{payment_id}`, but it must still be declared before it, as
`/due` and `/batches` already are.

`GET …/remittance/preview`

Returns the payee groups computed **live** on every call: recipient kind, party id and
name, currently resolved email, detail rows, group total, `block_reasons`, plus the last
send result from `payment_remittance_notifications` if one exists.

`block_reasons` is a list, empty when the group is sendable:

- `missing_email` — no `remittance_email` and no `contact_email` (vendor), or no
  `users.email` (employee).
- `missing_invoice_no` — at least one vendor PA in the group has no resolvable vendor
  invoice number. Never applies to employee groups.

Because both are resolved live, the refresh button in the UI is simply a re-fetch: a
vendor email filled in, or an invoice number attached to the PA, after the batch was
executed clears the block immediately and enables Send.

`POST /batches/{batch_id}/remittance/send` and `POST /{payment_id}/remittance/send`

Body: `{"recipients": [{"recipient_kind": "vendor", "party_id": "..."}] | null}`, where
`null` means every sendable group. Sends one email per group, isolating failures, and
upserts the notification rows. Returns per-group `sent` / `failed` / `skipped` with
counts.

All four endpoints require the same authority as executing a payment
(`payment_execute._check_can_pay`, 403 otherwise). The batch scope requires the batch to
be `executed` and the payment scope requires the record to be `completed`; otherwise 409.

### 8. Frontend

The Remittance dialog and the Remittance status list are the same two pieces of UI in both
apps, differing only in which scope they query. Both are written against the shared
preview / send response shape.

**finance — `PaymentBatchPage.tsx`**:

- After a successful execute, if the company switch is on, fetch the preview and open a
  Remittance confirm dialog. Groups with a non-empty `block_reasons` are shown greyed with
  the reason spelled out and cannot be selected. **Send** posts the selected groups.
- The batch detail view gains a Remittance section: one row per payee showing name,
  email, a readiness badge (`Ready`, `Missing email`, or `Missing invoice no`), the last
  send status, and a Resend action enabled whenever the group is unblocked. A Refresh
  button re-fetches the preview.

**epms — `pages/pa/PaDetailPage.tsx`**:

- After Confirm Payment succeeds in the Process modal, if the company switch is on, fetch
  the preview for the new payment record and open the same Remittance dialog. For a PA
  this is a single vendor group, so the dialog is short, but the block reasons and the
  Send confirmation behave identically.
- A processed PA gains the same Remittance status row (badge, last send status, Refresh,
  Resend) in its detail view.
- These calls go through the existing `financeApi` client, which the Process modal already
  uses to load payment sources — `VITE_FINANCE_URL` and the finance-api CORS origin for
  EPMS are therefore already wired. No new build argument is introduced; if that changes,
  it must be declared as both `ARG` and `ENV` in the Dockerfile.

**finance — new `PaymentsPage.tsx` (the hub)**:

The Finance app gains a Payments page, the single place to see every payment the system
has made. Payment Batches stays as the place to *build and execute* runs; the hub is the
place to *look things up*.

Layout, top to bottom:

- Summary cards — one per currency in the filtered set, showing total paid and payment
  count, fed by `GET /payments/summary` so the numbers describe the whole filter, not the
  visible page.
- Filter bar — date range, payee search, document kind, source (Batch / Single), payment
  method, bank account, currency, status, remittance status. Plus an Export CSV button
  that hands the current filters to `GET /payments/export`.
- Transaction table — one row per payment record: payment date, document number, payee,
  amount, currency, payment method, bank account, source (batch number as a link to the
  batch, or `Single`), payment status, and a Remittance column showing `Sent` / `Not sent`
  / `Missing email` / `Missing invoice no`.
- Row click opens a detail drawer with the full record and a Remittance block — the same
  component used elsewhere, scoped to that payment: badge, last send result, Refresh,
  Send / Resend.

This closes the OA Direct PA gap: a Direct PA paid from OA has no dialog on its own screen,
but it appears in the hub like any other payment and remittance can be sent from there. Its
own screen is left unchanged in this iteration.

The page is wrapped in `PortalChromeLayout` with an `activeKey` matching its `navConfig`
entry — no bare `div`. The sidebar entry is added to the shared `navConfig` and gated by
the existing `view_finance` permission key, not by a page-local role check or a page-local
`NAV_SECTIONS` copy.

Badges use the app-level `StatusBadge`, not page-local colour classes. Amounts arrive as
strings from Pydantic `Decimal` serialization and are passed through `Number()` before
any arithmetic. Any new frontend build argument is declared as both `ARG` and `ENV` in
the Dockerfile — a missing build arg is silently dropped and produces a page that hangs
on loading.

## Testing

finance-api pytest, against the local docker postgres (never the host `.env`, which
points at production):

- Scope equivalence: a batch of one PA and that same PA paid singly produce an identical
  payee group, proving the two entry points share the code path.
- Aggregation: several PAs for one vendor collapse into one group; cancelled or
  non-`completed` payment records are excluded; a vendor with no email yields `missing_email`; a vendor PA with no invoice
  number yields `missing_invoice_no`, and a Direct PA is treated no differently from a
  PO-based one here; an employee claim group is never blocked for a missing invoice
  number.
- Blocked groups are rejected server-side too, not only greyed in the UI: posting a
  blocked payee to the send endpoint returns it as `skipped`, never as `sent`.
- Send: upsert is idempotent per `(batch, kind, party)`, `attempts` increments on resend,
  one payee's SMTP failure does not abort the others.
- Permissions: 403 without payment authority; 409 on a non-executed batch.
- Payments list: a page containing an expense-claim payment serializes without error —
  the regression that the current required-field schema would produce. Filters compose
  (date range plus source plus currency narrows correctly), `summary` totals match the sum
  of all matching rows rather than the current page, and CSV export respects the filters
  while ignoring pagination.
- SMTP transport is mocked; a positive-path assertion checks the exact recipient, CC, and
  subject rather than merely asserting no exception.

Frontend typecheck for the finance app at its actual TypeScript version.

## Rollout

The Payments hub ships independently of the email switch — it is a read surface plus a bug
fix, useful on its own the moment it lands, and it is where remittance can be sent for
payments whose own screen has no dialog.

Three migrations land in three services (mdm, epms, finance). The feature is inert until
`remittance_config.enabled` is set, so the migrations and images can ship ahead of the
switch being turned on. Vendor remittance addresses can be backfilled at leisure; until
then those vendors simply show `Missing email` and are resent later.

