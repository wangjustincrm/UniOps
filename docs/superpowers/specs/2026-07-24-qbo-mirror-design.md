# QuickBooks Online → UniOps Mirror — Design

**Date:** 2026-07-24
**Branch:** `feature/qbo-mirror` (worktree `c:/Project/uniops-qbo`)
**Status:** Design approved, ready for implementation plan

## Background & Goal

Canada Royal Milk runs QuickBooks Online (QBO) as a standalone accounting system,
separate from and unreconciled with NC65. All real invoice/vendor/payment
management happens in QBO. **UniOps will replace QBO.** As the first step, we pull
QBO's entire company into an independent mirror layer inside UniOps so the data is
visible and preserved; **how it later integrates with the existing system (NC65,
finance AP) is deliberately deferred** — QBO and NC have no shared key, so any
association now would be guesswork.

Because QBO will be decommissioned, this is likely the only chance to pull the
data. The migration is **one-time full pull + occasional manually-triggered
incremental**, and sweeps **every queryable entity** (a curated subset risks
finding a gap after the account is gone).

**Production connection (verified 2026-07-24):** realm `123146434640944`
(CANADA ROYAL MILK ULC, country CA). Production keys unblocked after the Intuit
app assessment. OAuth via `authorize.py`; credentials in `C:\Project\qbo_conn.env`
(outside the repo). Full sweep already run into `data/qbo_samples/`.

### Production scale (from the sample sweep)

| Entity | Count | | Entity | Count |
|---|---|---|---|---|
| Bill | 25,044 | | JournalEntry | 3,747 |
| BillPayment | 15,957 | | Item | 2,315 |
| Vendor | 1,413 | | Purchase | 1,082 |
| Transfer | 754 | | Invoice | 724 |
| VendorCredit | 460 | | Payment | 247 |
| Account | 428 | | Deposit | 38 |
| CreditMemo | 13 | | Attachable | 3 |

~50k transactions total. PurchaseOrder / SalesReceipt / Estimate / RefundReceipt /
CreditCardPayment / TimeActivity / Budget are all 0 (unused).

### Key facts from real payloads (these override sandbox assumptions)

- **Multicurrency is in use — `CompanyCurrency` = 4.** Every transaction carries
  `CurrencyRef` + `ExchangeRate`, plus home-currency (CAD) fields
  `HomeTotalAmt` / `HomeBalance`. Every table must store foreign amount + home
  amount + exchange rate.
- `GlobalTaxCalculation` present on every transaction (Canadian tax indicator).
- `Bill.DocNumber` populated ~16/20 (in production, unlike the sandbox where it
  was always null) — the AP document number is usually present.
- `TxnTaxDetail` (GST/HST breakdown) on Bill/Invoice/JournalEntry.
- Canadian-specific vendor fields: `T4AEligible`, `T5018Eligible`, `Vendor1099`.
- Amounts are JSON numbers, not strings.
- `DocNumber` can be null; upsert key must be `qbo_id`.

## Approved design decisions

1. **Table strategy = hybrid.** Core entities get typed columns + a `raw JSONB`
   fallback; the long tail lands raw-only in a single generic table.
2. **Line items = typed child tables** (one per parent that has lines).
3. **Trigger = Finance UI** (sync button + progress + data-browse pages), not
   CLI-only.
4. **Permissions = reuse the existing `view_finance` key** — anyone with Finance
   access sees QBO data and can trigger a sync.

## Architecture

A self-contained subsystem inside **finance-api**. Finance owns these table
schemas and ships their migration. **Read-only mirror — no association, no
posting, no GL involvement.**

```
extract → data/qbo/<entity>.json → load → qbo_* tables → finance-api endpoints → Finance UI
```

extract and load are separated (import_pms / NC convention): the full pull hits
QBO once; load can be re-run repeatedly to refine mapping without re-fetching.

## Data model

Finance owns all `qbo_*` tables (new migration). All amounts stored as `Numeric`.

### Sync log

`qbo_sync_runs` (modeled on `NcSyncRun`): `mode` (full|incremental), `status`
(running|success|failed), `started_by`, `started_at`, `finished_at`, per-entity
insert/update counters, per-entity watermark (JSONB), `error`. Doubles as the
live progress feed the UI polls.

### Core entities — header table + line table + raw

| Header table | Line table |
|---|---|
| `qbo_vendors` | — |
| `qbo_accounts` | — |
| `qbo_customers` | — |
| `qbo_bills` | `qbo_bill_lines` |
| `qbo_bill_payments` | `qbo_bill_payment_lines` |
| `qbo_vendor_credits` | `qbo_vendor_credit_lines` |
| `qbo_purchases` | `qbo_purchase_lines` |
| `qbo_invoices` | `qbo_invoice_lines` |
| `qbo_payments` | `qbo_payment_lines` |
| `qbo_credit_memos` | `qbo_credit_memo_lines` |
| `qbo_journal_entries` | `qbo_journal_entry_lines` |
| `qbo_deposits` | `qbo_deposit_lines` |
| `qbo_transfers` | — |

**Common header columns:** `qbo_id` (unique), `sync_token`, `doc_number`
(nullable), `txn_date`, counterparty ref (vendor/customer `qbo_id` + name),
`currency`, `exchange_rate`, `total_amt` (foreign), `home_total_amt` (CAD),
`balance`, `home_balance`, `global_tax_calc`, `private_note`,
`last_updated_time` (tz-aware), `deleted_at` (soft-delete, nullable), `raw` JSONB.

**Common line columns:** `parent_qbo_id`, `line_num`, `detail_type`, `amount`,
`account_ref` (id+name), `tax_code_ref`, `description`, `raw` JSONB. Payment-type
line tables additionally store `linked_txn_id` / `linked_txn_type` — these capture
the AP/AR reconciliation (which payment settled which bill/invoice).

**Master/tax detail** (`Vendor`, `Account`) keep their typed columns; Canadian
slip flags (T4A/T5018/1099) and `TxnTaxDetail` breakdowns live in `raw` and are
promoted to columns only if a later need arises.

### Long-tail entities — raw only

`qbo_raw` (`entity_type`, `qbo_id`, `payload` JSONB, `last_updated_time`,
unique on `(entity_type, qbo_id)`) holds: Term, TaxCode, TaxRate, TaxAgency,
PaymentMethod, Class, Item, CustomerType, CompanyCurrency, Employee, Department.

### Attachments

`qbo_attachments` (metadata: `qbo_id`, `file_name`, `content_type`, linked txn
refs, `file_api_id`, `raw`). The 3 binaries are downloaded from QBO's signed URL
and stored in file-api. Trivial volume.

## Import engine

Location: `finance-api/scripts/qbo_import/` (extends existing `client.py` /
`authorize.py` / `sample_extract.py`). New: `extract.py`, `load.py`,
`attachments.py`, `run_import.py`, plus a thin finance-api service wrapper the API
layer calls.

### Full (`--full`)

1. Open a `qbo_sync_runs` row (mode=full).
2. Per entity, `query_all` paging (1000/page, serial + 429 backoff — already
   implemented) → write `data/qbo/<entity>.json`.
3. `load` per entity: header upsert by `qbo_id`; line tables delete-then-insert
   by `parent_qbo_id` (avoids line drift); long tail → `qbo_raw`.
4. Record each entity's `max(LastUpdatedTime)` as the incremental watermark.
5. Attachments: fetch `Attachable` metadata → download → file-api →
   `qbo_attachments`.

### Incremental (`--incremental`)

- Per entity, read the prior watermark and query
  `WHERE MetaData.LastUpdatedTime > '<watermark>' ORDER BY Id`.
- Same upsert semantics; advance watermarks.
- **Delete blind spot:** `LastUpdatedTime` never surfaces QBO-side deletes. Since
  QBO is being retired and incrementals only mop up a tail, the plan is: run a
  **final full pull before decommissioning**, and during that load diff QBO's live
  `qbo_id` set against the local set per entity, marking local extras with
  `deleted_at` (soft delete — never hard delete, preserve the record). Day-to-day
  incrementals ignore deletes.

### Idempotency & resumability (import_pms lessons)

- Whole flow re-runnable; upsert prevents duplicates.
- `qbo_sync_runs` tracks progress; a failed entity can be re-run alone
  (`--entities Bill`).
- extract's JSON files decouple load from the network — a load error never forces
  a re-fetch.

### Operational constraints

- **Run inside the server container** (or with an explicit `DATABASE_URL`
  override). finance-api's host `.env` points at the production DB — running on
  the host risks hitting prod unintentionally (the 0018 incident).
- **Timezone:** `LastUpdatedTime` carries an `America/Toronto` offset; watermark
  comparisons must be tz-aware, never treated as UTC.
- Throttling already handled (500/min + backoff). Full pull ~30–60 min serial.

## API (finance-api, gated on `view_finance`)

- `POST /qbo/v1/sync` — body `{mode, entities?}`; starts a background run,
  returns `run_id`.
- `GET /qbo/v1/sync/runs`, `GET /qbo/v1/sync/runs/{id}` — list + single-run
  progress (UI polls this, NcSyncRun-style).
- `GET /qbo/v1/{entity}` — paged browse of each mirror table, filterable by
  date / counterparty / doc number.
- `GET /qbo/v1/bills/{qbo_id}` (and peers) — detail: header + lines + reconciliation.
- `GET /qbo/v1/attachments/{qbo_id}/file` — proxied file download.

## Finance UI

A new **QuickBooks** page, wrapped in `PortalChromeLayout` with its `navConfig`
active key:

- **Sync panel:** Full / Incremental buttons, last-sync time, progress bar.
- **Data tabs:** per-entity tables (Vendors / Bills / Bill Payments / Invoices /
  Payments / Journal Entries). Paged (via `listAll` / paging — no 20-row
  truncation), filter by date / counterparty / doc number, row → detail
  (header + lines + reconciliation).
- Amounts shown with currency; foreign-currency docs also show the home (CAD)
  amount. `Decimal` values are `Number()`-coerced before `.toFixed()`.
- App-level `StatusBadge`; dropdowns/overlays use portal positioning.

## Testing

- Engine unit tests: paging boundaries, upsert idempotency, line
  delete-then-insert, watermark advance, multicurrency amount mapping, soft-delete
  diff, tz-aware comparison.
- Fixtures from the real `data/qbo_samples/` payloads — no live QBO in tests.
- Smoke: full-pull a small entity (Account, 428) to exercise the success path.
- Columns were defined from **real production payloads**, per the
  "mirror models match physical reality" discipline.

## Rollout (standard release workflow)

- New migration (the `qbo_*` tables) → deploy runs `migrate-prod.sh`.
- finance frontend + finance-api changed → **rebuild finance-web + finance-api
  images** (not all 15, but both of these).
- Full import run **manually inside the production server container** once
  (~30–60 min); not part of automated deploy.
- Reconciliation check: run `/reports/TrialBalance` and
  `/reports/VendorBalanceDetail`, compare against balances computed from the
  loaded data — the completeness gate before calling the migration done.
- Branch `feature/qbo-mirror`, following the R1–R5 multi-session discipline.

### Phasing

1. **P1** — tables + migration (core header/line tables + `qbo_raw` + `qbo_sync_runs`).
2. **P2** — engine (extract/load/full/incremental/attachments) + unit tests.
3. **P3** — API endpoints.
4. **P4** — Finance UI.
5. **P5** — production full import + TrialBalance / VendorBalanceDetail reconciliation.

## Out of scope (deferred)

- Any association or reconciliation between QBO data and NC65 / finance AP.
- Posting QBO data to the GL / generating posting events.
- AR beyond Invoice/Payment/CreditMemo (SalesReceipt/Estimate unused).
- Continuous/automated sync (Webhooks/CDC) — manual trigger only.
