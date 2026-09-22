# Bank Reconciliation v2 — PDF statements, batch payments, NC as the book

Branch: `feature/bank-reconciliation-v2` (off `98ca50d6`)
Supersedes the matching model in [2026-06-12-phase-a-a3-bank-recon.md](2026-06-12-phase-a-a3-bank-recon.md).

**Status: design only — no code written.** Awaiting review.

Decisions taken 2026-09-22 (user):
- Backfill of the NC bank-account auxiliary runs as **one `full` nc_sync**, not a targeted script (§6.0, §7.1).
- First reconciled period is **2026-07**, cross-checked line-by-line against the QB report (§8).
- Other banks' statement samples will be supplied; the parser is built to take them (§7.2).

## 1. What the finance team actually does today

1. **NC, with an AP bill** — payment voucher against the bill → approve → voucher → NC books the bank payment.
2. **NC, no AP bill** (payroll, HQ funding, expense reimbursement) — finance books a GL voucher straight to the bank account.
3. **Bank portal** — finance merges the day's payables by hand and releases one batch transfer. The portal returns a
   **payment file PDF** listing every vendor in that batch.
4. **Month end** — the bank statement arrives. Reconciliation used to be done in QuickBooks, which produced the
   *Reconciliation Report* handed to the auditors.
5. **Book side** = NC account-balance report, account **`100201` Checking**, expanded by the **bank-account auxiliary
   (辅助核算)**, drilled through to the line detail.
6. **Compare** book detail against the statement — but a statement line is usually *one merged payment*, so the
   payment-file PDF from step 3 is the only thing that explodes it back to vendor level.

## 2. Evidence — measured against `/srv/uniops/bankDoc` (RBC CAD, July 2026)

| Artifact | Shape |
|---|---|
| `RBC CAD 2026.7STATEMENT.pdf` | 3 pages, **AES-encrypted (empty user password)**, 7 credits + 30 debits, 564,623.34 → 709,868.72 |
| `PaymentFile/*.pdf` (19 files) | 17 × "Payment File Content" (PDS batch, has a printed **Total**) + 2 × "Released Bill Payment Confirmation Numbers" |
| `RBC CAD 2026.7RECON.pdf` | QB report: **253** cleared payments + **7** cleared deposits against those 37 statement lines |

Measurements taken while writing this doc:

- **19/19 payment files parse exactly.** Every file's line sum equals its own printed total; the 19 totals sum to
  **1,031,087.99** over **237** vendor lines. Plain `pypdf` text extraction is sufficient — the layout is simple.
- **The batch really is the statement line.** `7.2.pdf` total `48,336.03` = the 02 Jul statement line
  *"Direct Deposits (PDS) service total GRADS9804420000 48,336.03"*, and its 18 vendor lines are 18 separate rows in
  the QB report. Same for 7.6/7.7/7.8/7.16/7.20/7.21/7.22/7.23/7.24/7.27/7.28/7.30.
- **It is not merely many-to-one, it is many-to-many.** The 31 Jul statement line `108,498.24` = `7.31.pdf` (60,576.42)
  **+** `7.31-.pdf` (47,921.82). The 14 Jul line `118,269.09` = `7.13.pdf` (54,134.86) **+** `7.14.pdf` (64,134.23) —
  a file released on the 13th that cleared on the 14th.
- **234 of 237 payment-file lines match an NC `100201` CAD credit by exact amount** in a ±5-day window. The 3 misses
  are vendor-level merges: Ideal Supply `1,220.65` = NC `629.69` + `590.96`; Yonger `923.21` = `361.60` + `561.61`.
- **Bill payments carry a join key.** `7.2-bill.pdf` → confirmation number `8642`; the statement reads
  *"Bill payment - **8642** TYENDINAGA PROP 31.64"*. Likewise `5386` / UTIL. KINGSTON.
- **Book date ≠ bank date, routinely.** The 7.2 batch is NC voucher date `2026-07-03`, bank date `02 Jul`. The QB
  report shows book `07/30` rows clearing on the `31 Jul` statement.

## 3. The blocker: the book side does not exist in UniOps yet

`100201` is **one postable account** — there are no per-bank sub-accounts:

```
1002   Cash on Bank   (not postable)
100201 Checking       (postable)   coa_aux_items: bank_category[req], bank_account[req]
100202 Savings        (postable)   coa_aux_items: bank_category[req], bank_account[req]
```

So the bank account lives **only** in the auxiliary. And `nc_sync` never reads it:

- [`nc_sync.py:fetch_from_nc`](../../../finance-api/app/services/nc_sync.py) reads `GL_FREEVALUE.typevalue1..9` but decodes
  exactly five auxiliary types — department, cost centre, income/expense item, supplier, customer.
- `jv_line_dimensions` in the prod snapshot holds **one** `dim_code`: `income_expense_item` (67,941 rows).
  **`bank_account`: 0 rows.**
- [`account_balance.py`](../../../finance-api/app/crud/account_balance.py) already says so out loud — `bank_account` is
  listed in `DIM_LABELS` under *"carried from NC BD_ACCASS but not expandable — jv_lines has no column for them"*.

Why this is fatal rather than cosmetic — `100201` CAD credits, 2026-06-28..2026-08-07: **331 lines, 5,678,381.21**,
against a statement whose total debits are **1,269,496.36**. The rest belongs to other bank accounts:

```
700,000.00  2026-06-29  BOC transfer to RBC CAD $700000
433,001.99  2026-07-16  Pay Payroll through ADP
416,425.62  2026-07-02  Pay Payroll through ADP
257,548.00  2026-07-03  Property Tax-
```

Payroll and property tax never touch this RBC account. Worse, *"BOC transfer to RBC CAD $500000"* is the **Bank of
China side** of an internal transfer — a **credit** on `100201` — while the RBC side is a **debit** on the same account.
The statement's four `Funds transfer credit TT` deposits (1,400,000 total) are exactly these. Without the auxiliary
the two sides of every internal transfer are indistinguishable and net to zero.

**Nothing downstream can be scoped to "the RBC CAD account" until the `bank_account` auxiliary is synced.**

## 4. Why the current implementation has to be replaced, not extended

[`finance-api/app/crud/bank.py`](../../../finance-api/app/crud/bank.py) / [`api/v1/bank.py`](../../../finance-api/app/api/v1/bank.py):

| Today | Required |
|---|---|
| CSV only (`import_statement_csv`) | PDF (encrypted), CSV kept as fallback |
| `BankTransaction.matched_payment_id` — one nullable FK, **strictly 1:1** | N:M match groups (1 bank line ↔ 18 book lines; 2 bank lines ↔ 1 batch) |
| Book side = `payment_records` (EPMS PA payments) | Book side = NC GL `100201` + bank-account aux. Payroll, transfers, bank fees and HQ funding have **no** `payment_record` and never will |
| No payment-file layer | The batch↔vendor bridge is the whole job |
| No period, no opening/closing balance, no sign-off, no artifact | QB-shaped Reconciliation Report, frozen on finalize, handed to auditors |
| `auto_match` scans `amount < 0` only | Deposits reconcile too (7 of them in July) |

`matched_payment_id` stays as an **optional** extra link — drilling from a bank line to the PA/PO is useful — but it
stops being the reconciliation spine.

## 5. Design

### 5.1 Three layers plus a session

```
A  bank side     bank_statements ──< bank_transactions          (from the statement PDF)
B  bridge        bank_payment_advices ──< bank_payment_advice_lines   (from the payment-file PDFs)
C  book side     journal_voucher_lines where account_code ∈ 1002* and dim bank_account = this account
D  session       bank_reconciliations ──< bank_recon_matches
                                           ├──< bank_recon_match_txns   (N bank lines)
                                           └──< bank_recon_match_books  (M book lines)
```

Layer C is a **query, not a table** — the GL stays the single source of truth and a re-sync must never orphan a match.
Matches reference `journal_voucher_lines.id`, which `nc_sync` regenerates on a full reload; see §5.6.

### 5.2 New tables

```sql
bank_statements(
  id, bank_account_id, period_start, period_end,
  opening_balance, closing_balance,                 -- read off the PDF, verified by arithmetic
  total_debits, total_credits, debit_count, credit_count,
  source_file_id, parse_method, parse_confidence, parsed_payload jsonb,
  status,            -- imported | verified | superseded
  imported_by, imported_at
)

bank_payment_advices(
  id, bank_account_id, advice_kind,                 -- pds_batch | bill_payment
  advice_date, client_number, total, line_count, currency,
  confirmation_number,                              -- bill_payment only; joins statement description
  source_file_id, parsed_payload jsonb, status, imported_by, imported_at
)
bank_payment_advice_lines(id, advice_id, seq, payee_code, payee_name, currency, amount, matched_jv_line_id?)

bank_reconciliations(
  id, bank_account_id, period_start, period_end,
  statement_id, statement_opening, statement_closing,
  book_opening, book_closing,
  cleared_debit_total, cleared_credit_total, cleared_debit_count, cleared_credit_count,
  outstanding_debit_total, outstanding_credit_total,
  difference,                                       -- must be 0 to finalize
  status,            -- open | finalized | reopened
  finalized_by, finalized_at, report_file_id, snapshot jsonb
)

bank_recon_matches(id, reconciliation_id, method, advice_id?, amount, note, matched_by, matched_at)
bank_recon_match_txns(match_id, bank_transaction_id)          -- unique(bank_transaction_id) per reconciliation
bank_recon_match_books(match_id, jv_line_id, amount)          -- unique(jv_line_id) per reconciliation
```

`bank_transactions` gains `statement_id`, `running_balance`, `sort_seq`, and keeps `status`
(`unmatched | matched | excluded`) as a **derived** column maintained by the match writer.

### 5.3 Statement PDF ingestion

1. **Decrypt.** RBC's export is AES-encrypted with an empty user password. `pypdf` + `cryptography`; `PdfReader.decrypt("")`
   before anything else. Without `cryptography` installed, `pypdf` raises `DependencyError` — both go in
   `finance-api/requirements.txt` (neither is there today).
2. **Extract with Claude** (`claude-haiku-4-5-20251001`, per the standing rule that application AI is Haiku only — see
   [feedback_uniops_haiku_only_for_api_key]). Reuse the hardened pattern in
   [`expense-api/app/services/ocr_service.py`](../../../expense-api/app/services/ocr_service.py): base64 `document` block,
   `asyncio.to_thread` around the sync client, and `_raise_for_bad_request`'s split between "unreadable file" (422) and
   "account hit a billing/usage limit" (503).
   **Text-order extraction is not an option here.** On the 31 Jul page, `BR TO BR - 2402 14,572.68` extracts adjacent to
   the debits but is a **credit** (QB lists it under *Deposits and other credits cleared*). Debit-vs-credit is a
   *column-position* fact, which only a layout- or vision-aware read recovers.
3. **Verify arithmetically — this is the gate, not the AI's confidence score.**
   - `opening + Σ signed amounts == closing`
   - every printed running balance is reproduced in order (RBC prints one per date group, plus mid-group after a
     transfer credit — e.g. 07 Jul shows `796,162.48` before the day's debits)
   - `debit_count` / `credit_count` match the printed *"Total cheques & debits (30)"* / *"(7)"*
   A statement that does not tie is **rejected with the failing line named**, never imported half-right. Same principle
   as the invoice total check ([project_uniops_invoice_total_check]).
4. Re-import of the same period supersedes rather than duplicates; existing matches survive if the line's
   `import_hash` is unchanged.

### 5.4 Payment-file ingestion

Deterministic first, AI second — the opposite of the statement, because the evidence says so (19/19 exact):

1. Detect the kind from the first line — `Payment File Content` vs `Released Bill Payment Confirmation Numbers`.
2. Regex the vendor rows; for PDS batches, read `Number Of Payments : N  Total: X`.
3. **Tie test:** `Σ lines == printed total` and `count == N`. Pass → accept, zero AI cost.
   Fail → fall back to the Haiku extractor, then re-run the same tie test.
4. Bill-payment files carry the **confirmation number**; store it — it is an exact join key to the statement.

Multiple files can be uploaded at once; the importer groups them by account and date.

### 5.5 Matching ladder

Deterministic, ordered, and it **stops rather than guesses** — the existing `auto_match` comment ("never guess") is the
right instinct, kept.

| # | Rule | Evidence |
|---|---|---|
| 1 | advice total → bank line, exact amount, ±5 days | 12 of 18 PDS lines, 1:1 |
| 2 | Σ of 2–3 advice totals → bank line, exact | 14 Jul = 7.13+7.14; 31 Jul = 7.31+7.31- |
| 3 | bill-payment confirmation number ∈ statement description | `8642`, `5386` |
| 4 | advice line → book line, exact amount + vendor-name similarity, ±5 days | 234/237 |
| 5 | book line ↔ bank line 1:1 for anything outside a batch | transfers, Hydro One 136,198.91, activity fee 455.60 |
| 6 | bounded subset-sum inside one (vendor, ±5 days) bucket, ≤6 candidates | Ideal Supply 629.69+590.96; Yonger 361.60+561.61 |
| 7 | anything left | human, with a ranked candidate list and a one-click group builder |

Every match records `method` so the report can show how each line was cleared, and any match is reversible until the
period is finalized.

### 5.6 Surviving an NC re-sync

`nc_sync` in `full` mode does `delete from journal_vouchers where nc_source_pk is not null` and regenerates every
`journal_voucher_lines.id`. Today the only things pointing at those rows are two `ON DELETE CASCADE` FKs
(`journal_voucher_lines.jv_id`, `jv_line_dimensions.jv_line_id`), so a reload is harmless. The moment
`bank_recon_match_books` references `jv_line_id` that stops being true — a cascade would **silently delete
reconciliation matches**. So that FK is `ON DELETE SET NULL`, never `CASCADE`, and
`bank_recon_match_books` also stores the **natural key** (`nc_voucher_pk`, `line_no`, `account_code`, `amount`) and a
post-sync healer re-points matches; anything it cannot re-point is raised on the existing
[JV validation Admin Task](../../../finance-api/app/services/jv_validation_tasks.py) channel rather than disappearing.
Finalized periods keep an immutable `snapshot` and never re-point.

### 5.7 Report

Reproduces the QB layout the auditors already accept: summary block (statement opening, cleared payments with count,
cleared deposits with count, statement closing, register balance, difference) then the two detail tables, then
**Outstanding** — items the QB report omits only because July happened to have none (register balance == statement
closing). PDF via `reportlab` (already an epms-api dependency; add to finance-api) plus xlsx via `openpyxl`
(already present). Frozen into `snapshot` + `report_file_id` on finalize.

## 6. Work breakdown

| # | Item | Depends on |
|---|---|---|
| **0** | **`bank_account` auxiliary into `jv_line_dimensions`** — decode `BD_ACCASSITEM` `0011` in `fetch_from_nc`, resolve via `BD_BANKACCSUB`, write the dim. History is backfilled by **one `full` nc_sync** (decided; see §7.1) | — |
| 0b | Register `bank_account` in `account_balance._dimensions()` so the NC-style "expand 100201 by bank account" drill-down works in Finance too | 0 |
| 0c | Map `bank_accounts.id` ↔ NC bank-account code (new column on `bank_accounts`) | 0 |
| 1 | Schema + migration for §5.2 | — |
| 2 | Statement PDF parse + arithmetic verification | 1 |
| 3 | Payment-file parse (regex, AI fallback) | 1 |
| 4 | Book-side query service | 0, 0c |
| 5 | Matching ladder + manual group builder API | 2,3,4 |
| 6 | Reconciliation session: open / compute / finalize / reopen | 5 |
| 7 | Report PDF + xlsx | 6 |
| 8 | Frontend rework: statement view ‖ book view, grouped match rows, advice drill-down, session header, report download | 5,6,7 |

## 6b. What was built (2026-09-22)

All of §6 except the production run itself. Measured end to end on the real July
documents — the AI-read statement, the 19 payment files, and NC's own 260 ledger
lines for RBC CAD:

| | |
|---|---|
| statement | 37 lines, verified: 30 debits `1,269,496.36` / 7 credits `1,414,741.74` |
| payment files | 19/19 tie, 237 vendor lines, `1,031,087.99` |
| ladder | **37/37 statement lines and 260/260 ledger lines cleared**, 237/237 advice lines linked |
| findings | 1 — correctly naming the statement line whose payment file was not supplied |

Two things the evidence changed from the original design:

- **The statement's signs are solved from the printed balances, not taken from
  the model.** A whole-document read returned every date, description, magnitude
  and running balance correctly and inverted the signs wholesale. Direction is a
  column-position fact with no textual signal, so it is derived: inside each
  balance segment the signs must sum to that segment's delta. No solution means a
  magnitude is wrong (said so); several means the statement genuinely does not
  say (said so).
- **Reading is page by page.** A whole-document read dropped exactly one row — the
  28,918.08 batch at the bottom of page 2, where a date group breaks across the
  page boundary. Splitting the PDF removes the seam and the max_tokens cliff with
  it.

## 7. Open risks

### 7.1 The `full` re-sync (decided)

Better than it first looks: `nc_sync` opens its connection with `autocommit = False` and the delete, the reload and
the status backfill are **one transaction** (`finance-api/app/services/nc_sync.py:462`, commit at :605). Readers see
the previous snapshot right up to the commit, so the GL, Budget Actual and the AP pages never go blank mid-run.

What still needs care:

- It is a ~40k-voucher / ~200k-line delete-and-reinsert in one transaction — long locks and table bloat. Run it in a
  window and `VACUUM` after.
- It must land **before** any `bank_recon_match_books` rows exist, or run with §5.6's healer already deployed.
- The run reads the whole book, so it also re-derives cost centres and income/expense items. Any `budget_actual_cc_map`
  drift since the last full run will show up as changed Budget Actual numbers — compare before/after, do not assume
  the only delta is the new dimension.

### 7.2 Other statement formats

Only RBC CAD is evidenced here; Chase / Bank of China / ICBC samples are coming. The arithmetic gate (§5.3.3) is
format-independent and carries over unchanged. What is per-bank: the extraction prompt, and which anchors exist —
RBC prints a running balance and both counts, which is the strongest possible check. A bank that prints neither
needs a weaker gate, and that has to be an explicit, visible downgrade rather than a silent one.

### 7.3 Opening balance for 2026-07

The RBC statement's own `564,623.34` is the anchor, and the QB report agrees. The **book** opening for that account
is only computable once §6.0 lands — it does not exist today.

### 7.4 The assistant can describe this module but not yet query it

`modules.yaml` now lists reconciling a bank statement among what Finance does, so
the guide layer answers "how does this work". An **ontology entity** over
`bank_reconciliations` is deliberately NOT added yet: `test_ontology.py` checks
entities against the real database and refuses one over an empty table — "an
entity over a table with no rows answers every question with 'none'". Add it once
a period has actually been reconciled in production, and the same test will then
verify it.

### 7.5 Shared AI quota

The API key is shared with Claude Code ([project_uniops_ocr_shared_key_quota]). A month-end burst of statement parses
competes with EPMS invoice OCR on the same key; §5.4's deterministic-first rule keeps the 19 payment files off the API
entirely, so only the statement itself costs quota.

## 8. Acceptance — 2026-07 RBC CAD against the QB report

The sample set is complete for July, so acceptance is arithmetic rather than a judgement call. After importing the
statement, the 19 payment files, and running the ladder:

| Check | Expected |
|---|---|
| Statement opening / closing | `564,623.34` / `709,868.72` |
| Cleared payments | **253** lines, `-1,269,496.36` |
| Cleared deposits | **7** lines, `1,414,741.74` |
| Difference (register − statement closing) | **0.00** |
| Statement lines auto-cleared by the ladder | all 37 (30 debits + 7 credits) |
| Advice coverage | 17 PDS batches + 2 bill payments, every file's lines summing to its printed total |
| Known many-to-many cases resolved | 14 Jul = 7.13 + 7.14; 31 Jul = 7.31 + 7.31-; Ideal Supply `629.69 + 590.96`; Yonger `361.60 + 561.61` |
| Known gaps surfaced, not hidden | the 3 statement PDS lines with no supplied payment file (`5,085.00` 21 Jul, `66.22` 24 Jul, `28,918.08` 31 Jul) must appear as *needs a payment file*, never as silently cleared |

Every one of these numbers was measured from the supplied documents while writing this doc, so a failing run means the
implementation is wrong — not the baseline.


## 9. Deploying this

Four things this branch needs that a normal release does not.

### 9.1 Two migrations, and finance-api already runs first

`0036_nc_bank_acct_dim` → `0037_bank_recon_v2`, on top of `0035_ap_bill_dismiss`.
Single head, verified. Both revision ids are well under the 32-character limit
that has stopped `migrate-prod.sh` mid-run before. Everything they touch is
finance-api-owned (`journal_voucher_lines`, `bank_accounts`, and the new tables),
and finance-api is already first in `migrate-prod.sh`, so no ordering change.

### 9.2 A full NC sync MUST follow — the migration only creates the structure

0036 creates an empty `nc_bank_accounts` and a NULL `journal_voucher_lines.
bank_account_id`. Until a `full` nc_sync runs, **every bank-scoped read returns
nothing** and the workbench shows an empty ledger side. The sequence is:

1. merge and deploy (0036 creates the table and the column)
2. run a `full` NC sync
3. link each bank account to its NC code in Bank Settings (RBC CAD is `1033760`)

The sync is one transaction — delete, reload and status backfill all commit
together — so readers see the previous snapshot throughout and no page goes blank
mid-run. It is still a ~40k-voucher, ~200k-line delete-and-reinsert: run it in a
window and `VACUUM` after.

★ It also re-derives cost centres and income/expense items for the whole book, so
any `budget_actual_cc_map` drift since the last full run surfaces in the same
pass and will be blamed on this release unless a **before/after Budget Actual
comparison** is taken first.

### 9.3 New dependencies — finance-api's image must be built, never retagged

`pypdf`, `cryptography`, `reportlab`, `anthropic` are new in
`finance-api/requirements.txt`. `cryptography` is not optional: RBC ships
statements AES-encrypted with an empty user password, and without it pypdf raises
`DependencyError` rather than anything that reads as "encrypted".

### 9.4 Two new environment variables on finance-api

Already wired into `docker-compose.prod.yml`:

- `FILE_SERVER_URL` — finance-api had no file storage at all before this. Without
  it the statements, payment files and signed reports are not retained; the
  figures still reconcile and the API says the document was not kept.
- `BANK_ANTHROPIC_API_KEY` — falls back to `ANTHROPIC_API_KEY`, but exists so it
  need not. That shared key also drives EPMS invoice OCR, and when it hit its
  account limit every upload across the product answered "AI parsing failed". A
  month-end burst of statement reads is exactly what does that.

### 9.5 After any later full NC sync

`POST /finance/v1/bank-recon/heal`. A full sync regenerates every
`journal_voucher_lines.id`; the SET NULL foreign keys keep the match rows and the
healer re-points them from NC's natural key. Finalized periods are skipped on
purpose. Safe to run at any time — it reports `{orphans, healed, unresolved}` and
does nothing when there is nothing to do.
