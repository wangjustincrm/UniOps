# QBO Vendor Credit import — a throwaway migration tool

> **For agentic workers:** two tasks. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let AP bring the vendor credit balances that still sit in QuickBooks Online into `vendor_credits`, during the transition period before QBO is retired.

**This is deliberately disposable.** QuickBooks is being decommissioned; once the balances are across, this tool is dead code to be deleted. Every design choice below is made on that basis:

- **No new table, no migration.** A throwaway feature must not leave permanent schema behind. The vendor mapping travels in the request body.
- **The page remembers decisions in `localStorage`, not the database.** Survives a reload, costs nothing to delete later.
- **Two endpoints and one page.** Nothing more.

This supersedes the two earlier Phase C drafts, which designed a persisted decision table and a four-section workbench with heuristic tags. Both were written before the QBO mirror was measured and before the retirement was known.

## What the data actually is (measured on the dev snapshot, 2026-08-24)

- `qbo_vendor_credits`: 461 rows, **18 with `balance > 0`**. The other 443 are fully applied inside QBO — history, not balances.
- Those 18 belong to **14 vendors**, in **three currencies: CAD, USD, CNY**.
- With normalisation stronger than `lower(strip())` — strip `.,()`, drop one trailing legal suffix, collapse whitespace — **7 of the 14 match an EPMS supplier exactly, 7 do not.**
- Of the 7 unmatched: 3 have a close counterpart a human must confirm (`Independent Can Compan(US)` → `Independent Can Company`; `R. W. Beckett` → `R.W. BECKETT CANADA LTD.`; `Julie Ju Ni` → `Julie Ju Ni (Graphic Design)`), and 4 have none at all — including the three largest: `Caloy Quality Natural Oils` **88,452 USD**, `Harbin Haitian Plastic Packaging` **15,672 CNY**, `Receiver General for Canada` **14,665 CAD**.

Two consequences that shape the screen:

1. **By value the dominant case is "this vendor is not in EPMS."** So "create the vendor in Vendor Master, then re-run" is the main path, not an edge case. Each vendor AP creates makes the next run match automatically — successive runs need *less* manual mapping, which is also why persisting decisions buys little.
2. **A person-looking name was a real supplier.** `Julie Ju Ni` reads like an employee reimbursed through QBO; in EPMS it is a graphic-design supplier. Any "looks like an employee" heuristic would have been wrong on the one row it existed for. **Nothing is auto-decided. A match may pre-fill a control; a human always clicks.**

## Rules that are not negotiable, disposable tool or not

This writes spendable money into a ledger Phase B pays out of.

- **Take `qbo_vendor_credits.balance`, never `total_amt`.** `balance` is the unapplied remainder; `total_amt` is the original face value. Importing face value hands vendors credit they already spent. Keep `total_amt` in `notes` for reference.
- **Go through `crud.vendor_credit._positive()` and the same write path as manual upload.** No direct INSERT — that is what keeps "every amount stored positive" a single enforced rule.
- **Idempotent.** `(source, source_ref)` is uniquely indexed; a `qbo_id` already imported is skipped and counted, never re-imported or updated.
- **Dry run is the default.** `POST /run` previews unless explicitly told to commit.
- **Only vendors the caller explicitly mapped are imported.** Everything else is counted and reported.
- ⚠️ **QBO permits an empty `DocNumber`.** `vendor_credits.vendor_credit_number` is NOT NULL under a unique index that is **not** scoped by source, so two blank-numbered credits for one vendor would collide. Synthesise `QBO-<qbo_id>` when blank.
- **Currency is carried through and shown.** Phase B nets only on an exact currency match — a USD credit will only ever reduce a USD payment. Say so on the screen or AP will expect a CAD payment to absorb it.
- Imported rows: `source='qbo_import'`, `source_ref=<qbo_id>`, `opening_balance=True`, `status='available'`, `applied_amount=0`, `remaining_amount=total_amount`. They skip review — the data was already reconciled inside QBO.
- **A row already imported whose QBO balance has since changed is reported, never overwritten.** Our row may already carry Phase B applications; overwriting would erase them. Compare against `total_amount`, **not** `remaining_amount` — our own applications move `applied`/`remaining` and leave `total` alone, so a credit we spent ourselves must not look like drift.

## Environment (verified — do not re-investigate)

- **Run every command in the FOREGROUND.** Do not background it in the first place. This does NOT mean "don't poll" and does NOT mean "hand it to a monitor". Six agents on this project have been stranded that way.
- **NEVER run the full finance-api suite** — 55 minutes. Targeted files only, one at a time; concurrent runs corrupt the shared test DB.
  ```
  cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_qbi python -m pytest tests/test_vendor_credit_import.py -v
  ```
- finance frontend gate — baseline **0**:
  ```
  cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -c "error TS"
  ```
  A bare `npx tsc --noEmit` there compiles zero files and always prints 0 — a false pass. Running `npx tsc` on the host may rewrite `package-lock.json`; check `git status` and `git checkout --` it before committing. Do not run `npm ci`.
- **Never run `alembic` by hand** — `.env` files here point at the PRODUCTION database. This change needs no migration anyway.
- **The dev stack bind-mounts this worktree** (finance-api, epms-api, expense-api, both frontends) for user acceptance. Edits land live, which is expected. Do NOT run `docker compose down`, recreate containers, or touch `c:/Project/uniops`. finance-api runs `--reload`; a syntax error takes the running API down.
- **Never `git stash`.**

## What already exists

- `qbo_vendor_credits` (`app/models/qbo.py`): `qbo_id` PK, `doc_number`, `txn_date`, `currency`, `total_amt`, **`balance`**, `counterparty_id`, `counterparty_name`, `deleted_at`.
- `qbo_vendors`: `qbo_id`, `display_name`.
- `qbo_sync_runs`: `mode` (`full`|`incremental`), `status` (`success`), `finished_at`.
- `business_partners` mirror: `id`, `code`, `name`, `is_supplier`.
- `POST /qbo/vendor-emails/backfill` (`app/api/v1/qbo.py`) already matches QBO vendors to EPMS partners and already treats a normalised key with more than one candidate on **either** side as ambiguous rather than guessing. Reuse that two-sided rule.
- `app/crud/vendor_credit.py`: `create()`, `_positive()`, status constants, `AVAILABLE`.

---

## Task 1 — backend

**Files:** create `finance-api/app/crud/vendor_credit_import.py`, `finance-api/app/schemas/vendor_credit_import.py`, `finance-api/app/api/v1/vendor_credit_import.py`, `finance-api/tests/test_vendor_credit_import.py`; modify `finance-api/app/api/v1/__init__.py`.

**Produces**

```python
def norm(s: str | None) -> str
async def list_candidates(db) -> dict   # {"candidates": [...], "drift": [...], "cutover": {...}}
async def run_import(db, *, mapping: dict[str, uuid.UUID], imported_by: uuid.UUID,
                     dry_run: bool = True) -> dict
```

`list_candidates` returns one entry per QBO vendor holding importable credit:
`qbo_vendor_id`, `qbo_display_name`, `credit_count`, `credit_total`, `currencies`
(sorted distinct — a vendor can hold more than one), `suggested_vendor_id`,
`suggested_vendor_name`, `already_imported` (count of that vendor's credits
already in `vendor_credits`).

`drift` in the same response: already-imported rows whose QBO `balance` no longer
equals the `total_amount` we imported — `source_ref`, `credit_number`,
`vendor_name`, `imported_total`, `qbo_balance`, `applied_amount`.

`cutover`: the most recent successful FULL RELOAD — `{sync_run_id, finished_at}`
or nulls. Stamp `imported_from_sync_run_id` on every imported row from it.

`run_import` returns `{dry_run, imported, skipped_unmapped, skipped_existing, total_amount, rows}` where `rows` describes what was (or would be) created: `qbo_id`, `vendor_credit_number`, `vendor_name`, `amount`, `currency`.

**`norm` is measured, not guessed.** `lower(strip())` matches 5 of the 14; additionally stripping `.,()`, dropping one trailing legal suffix from `{inc, ltd, llc, ulc, corp, corporation, co, company, sec}`, and collapsing internal whitespace matches **7** — it catches `Alamfoods Inc` → `Alamfoods Inc.` and the double-spaced `MSC  Industrial Supply ULC`. Use the stronger form here. Do **not** weaken the existing backfill's own normalisation.

**Routes** under `/finance/v1/qbo-credit-import`, every one gated on `epms.vendor_credit.manage` via `require_permission` from `app.core.authz`, with the gate as the **first statement in the handler** so a caller cannot probe existence through differing status codes:
- `GET /candidates`
- `POST /run` — body `{mapping: {qbo_vendor_id: vendor_id}, dry_run: bool}`

`require_permission` short-circuits `role == "system_admin"` without a DB lookup, so `_h("system_admin")` clears it in tests. Copy the `client` fixture and `_h()` helper from `tests/test_vendor_credit.py`.

- [ ] **Step 1: Write the failing tests.** Cover at least:
  - candidates lists only vendors with `balance > 0`; a zero-balance vendor is absent
  - `credit_total` sums `balance`, **not** `total_amt`
  - `deleted_at` rows excluded
  - an exact normalised match pre-fills `suggested_vendor_id` — and the row still carries no decision of any kind
  - two EPMS partners sharing a normalised name yield **no** suggestion (two-sided ambiguity)
  - `currencies` returns the distinct set for a vendor holding two currencies
  - `run_import` with `dry_run=True` creates **nothing** but reports the rows it would create
  - it takes `balance` not `total_amt`
  - a vendor absent from `mapping` is not imported and is counted in `skipped_unmapped`
  - a second run is idempotent — `skipped_existing`, no duplicate row
  - **two blank-`doc_number` credits for one vendor both import, with distinct `QBO-<id>` numbers**
  - `imported_from_sync_run_id` is stamped from the latest successful full run
  - drift: balances agree → empty; QBO balance changed → reported and our row untouched; **a credit we consumed ourselves is NOT drift**
  - a caller without `epms.vendor_credit.manage` gets 403 from both routes

- [ ] **Step 2: Run them, watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run — all pass.** Then re-run `tests/test_vendor_credit.py` and `tests/test_vendor_credit_netting.py` and confirm both are unchanged; registering a router must not disturb them.
- [ ] **Step 5: Commit.**

---

## Task 2 — the screen

**Files:** create `finance/src/services/qboCreditImport.ts`, `finance/src/pages/finance/QboCreditImportPage.tsx`; register the route beside the existing QBO mirror page (`grep -rn "QboMirrorPage" finance/src`).

One table, ~14 rows. Columns: **QBO vendor · credits · total · currency · EPMS vendor · status**.

- The **EPMS vendor** cell is a vendor picker, pre-filled from `suggested_vendor_name` when the backend found an unambiguous match. **Pre-filled is not chosen** — the row counts as mapped only once the operator confirms it. Offer no "accept all suggestions" button; that would reintroduce automatic mapping through the back door, and the measured data already contains one row where the obvious guess was wrong.
- **When there is no suggestion, say so plainly and point at Vendor Master.** That is the dominant case by value here, and the operator's next step is to create the vendor and come back. Note in the UI that re-running after creating vendors picks them up automatically.
- **Currency is a column, with a one-line note under the header** that a credit only ever reduces a payment in the same currency.
- Decisions (chosen vendor, and rows the operator marked *skip*) persist in `localStorage` keyed per QBO vendor id, so a reload or a trip to Vendor Master does not lose them. Wrap every read and write in `try/catch` and render correctly when storage is empty or unavailable.
- Two buttons: **Preview** (`dry_run: true`) and **Import** (`dry_run: false`). Preview shows exactly what would be created; Import is only enabled after a Preview in the current session, and its result shows `imported`, `skipped_unmapped` and `skipped_existing` — so nobody assumes everything went in.
- Render the **drift** list only when non-empty, with a short line explaining that a credit changed inside QBO after we imported it and that neither side was altered.

**Money fields are Decimal-serialised strings.** Type them `string`; put every arithmetic operation through `Number()`.

- [ ] **Step 1: Measure the tsc baseline first** and record it. **Steps 2-4:** client, page, route. **Step 5:** tsc back at baseline. **Step 6:** commit.

---

## Done criteria

- [ ] `tests/test_vendor_credit_import.py` passes in full
- [ ] `tests/test_vendor_credit.py` and `tests/test_vendor_credit_netting.py` unchanged
- [ ] finance tsc back at its measured baseline
- [ ] No code path imports a vendor the caller did not explicitly map
- [ ] No migration was added

## Deploy notes

- Run a QBO **FULL RELOAD** first — the import reads whatever the mirror currently holds and stamps each row with the run it came from.
- **After cutover, vendor credits are applied only in EPMS. Nobody applies one inside QBO.** Two independently-decrementing ledgers double-spend. Drift detection *detects* violations; it cannot prevent them. Agree this with whoever still works in QBO before importing.
- **When QuickBooks is retired, delete this tool**: `crud/vendor_credit_import.py`, `schemas/vendor_credit_import.py`, `api/v1/vendor_credit_import.py`, its router registration, the two frontend files, and the route. It leaves no schema behind by design.
