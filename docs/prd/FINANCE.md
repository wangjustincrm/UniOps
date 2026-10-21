# Finance — Product Requirements Document

**Version:** 2.6
**Date:** 2026-10-20
**Status:** Current. Describes the system as built on the main line, under the 2026-09-20 positioning decision.
**Scope:** The UniOps Finance module: Finance web app and finance service. Covers the NC65 data mirror, the checks and reports built on it, AP handoff to NC, payments, remittance, vendor credits, bank reconciliation and the remaining finance-held records.

**What changed in version 2.6** (2026-10-20)

- EPMS Direct PA (EPMS.md §3.22): a Direct PA raised in EPMS is paid as an EPMS PA (its invoices are marked paid) and is listed under **Direct PA** on the Payment Batches due list (FIN-PAY-06); its invoice is exported to NC with the department, cost center, budget account and employee of the Direct PA paying it, and is refused until one does (FIN-AP-05).

**What changed in version 2.5** (2026-10-20)

- An NC payable can carry several supplier invoices, one per line. The bill lists in AP Subledger Health (supplier view, bills by date, excluded) and the AP Cash Flow drill-down now show every invoice on the bill with its own amount, instead of one invoice number beside the bill's total (FIN-APH-05, FIN-CF-06). The CSV exports list every invoice too.

**What changed in version 2.4** (2026-10-19)

- Every Finance list sorts by clicking a column header, ascending then descending (column sorting rule in EPMS.md §3.20 (UI-SORT-001…007)). AP invoices, the AP reconciliation drill-down, JV validation lines, Payments, the QBO mirror and Journal Vouchers sort on the server across all pages; on Journal Vouchers empty values now sort last and a newly clicked column starts ascending.
- AR invoices, the Payment Batches history, the AP cash-flow drill-down, AP ledger health, duplicate invoices and unallocated lines now page on the server instead of showing only the newest N rows, so sorting covers every matching row.
- The AP reconciliation, JV validation and AP ledger health CSV exports follow the on-screen sort. AP and AR aging tables are sortable. Reports with total rows or order-dependent columns (trial balance, account ledger, cash-flow buckets, bank reconciliation workspace) are not.

**What changed in version 2.3** (2026-10-19)

- Payment Batches due list: every column can be sorted by clicking its header; clicking again reverses the order (FIN-PAY-06).
- Courier freight (EPMS → Freight, EPMS.md §3.21): Freight PAs are paid as EPMS PAs and get their own **Freight PA** type on the Payment Batches due list (FIN-PAY-06); a courier invoice is exported to NC as **one row per cost center**, budget account CRM0150102 (FIN-AP-05).

**What changed in version 2.2** (2026-10-19)

- Payment Batches due list: a PA's vendor invoice number now opens the EPMS invoice, and a new Due Date column shows the earliest due date of the PA's invoices, in red once past (FIN-PAY-06).

**What changed in version 2.1** (2026-10-16)

- Approved expense claims are now visible on the Finance side before they are paid: a banner on the Payments hub and an "Expense Claim" filter on the Payment Batches due list (FIN-PAY-06, FIN-PAY-07).
- Travel Applications can no longer enter the payment path through Payment Batches (FIN-PAY-04, FIN-PAY-06).

**What changed in version 2.0**

- Merged the two Chinese v1.0 documents into one English PRD and translated them: the review PRD (2026-07-13) and the full-module PRD (last edited 2026-09-21). The full-module PRD is the backbone.
- **Positioning rewritten.** NC65 is the book of record. UniOps Finance is a finance assurance and analysis layer built on top of NC (decided 2026-09-20, for at least one year). The v1.0 statement that UniOps Finance would be a self-built finance hub that replaces NC is withdrawn.
- New since v1.0: the NC voucher mirror with scheduled sync, the NC chart-of-accounts sync, and the NC AP and payment mirror. Four AP check pages built on that mirror: AP Reconciliation, AP Subledger Health, AP Duplicate Invoices and AP Cash Flow.
- New pages: JV Validation, the Journal Voucher Center, Account Balance at NC parity (including "Include unposted vouchers"), and the Budget Actual report layer.
- Bank Reconciliation rebuilt: PDF statements and payment files, NC as the book side, a many-to-many matching ladder, foreign-currency accounts reconciled in their own currency, sign-off with an auditors' report, and unposted vouchers counted by default.
- Payments now have a Payments hub, a Payment Officer role separate from AP Clerk, vendor-credit netting and remittance advice emails. NC AP export is built.
- Open items are collected in §16. Statuses from v1.0 have been checked against the code. Items still wanted but not built are marked **Not implemented**.

---

## 1. Purpose and Positioning

### 1.1 Background

Canada Royal Milk keeps its books in **Yonyou NC65**. NC65 is a Feihe group shared system that uses a Chinese chart of accounts. The original plan (v1.0) was for UniOps Finance to grow into a full ledger and eventually replace NC's finance functions. That plan is suspended. UniOps has no Sales, Fixed Assets, Production or Inventory ledger, so it cannot hold a complete set of books.

### 1.2 Positioning statement (decided 2026-09-20, valid for at least one year)

> **NC65 is the book of record. UniOps Finance is a finance assurance and analysis layer built on NC's books.** It does not produce the official accounting entries. Its job is to bring NC's data across completely, check that it is right, and provide the reports and checks NC cannot.

**Red line 1: UniOps Finance does not keep a second set of books.** When anyone asks which number is correct, the answer is NC. Reports may only reorganise NC data. They must never recompute a different set of balances.

**Red line 2: business documents still belong to UniOps.** PR, PO, GR, invoice and payment application (PA) are raised in UniOps and handed to NC through the AP export. In short: **UniOps owns the business documents, NC owns the accounting result.**

### 1.3 Data ownership

| Data domain | Owner | Notes |
|---|---|---|
| Vouchers, account balances, financial statements | **NC** | Mirrored into UniOps |
| AP balance (what is still owed) | **NC** | Read from NC's open balance on approved payables, never from UniOps' paid amount (§8) |
| PR / PO / GR / invoice / PA | **UniOps** | Except POs imported from NC, which NC owns |
| Vendor payment terms | **UniOps** | Deliberate exception: NC cannot compute due dates, so UniOps is the only consumer |
| Other vendor master data | NC | Mirrored |
| Chart of accounts, cost centres, auxiliary dimensions | **NC** | Mirrored. No editing should be offered (see §16, FIN-OI-07) |
| Customers / sales | NC | UniOps has no sales module |
| Budget plans | **UniOps** | NC has no equivalent. See BUDGET.md |
| Payment execution | UniOps initiates, NC books | §10 |

Rule: where NC holds a master record, NC is the only source. UniOps mirrors it and does not edit or create it. Any exception must be listed in this table with its reason.

### 1.4 Capability layers

| Layer | Content | Where in this PRD |
|---|---|---|
| L0 Mirror | Copy NC finance data (vouchers, COA, dimensions, payables, payments, bank-account auxiliary) and tie it out | §4 |
| L1 Master-data alignment | Cost-centre mapping, account catalog mapping, drift detection | §5, §7 |
| L2 Assurance | Checks NC does not run: dimension validation, AP reconciliation, subledger health, duplicate invoice numbers, date anomalies | §7, §9 |
| L3 Reports | Account Balance at NC parity, Budget vs Actual report layer, AP Cash Flow, bank reconciliation report | §6, §8, §9, §12 |
| L4 Planning | Budget planning and budget vs actual. See BUDGET.md | — |

### 1.5 Engineering red lines (binding)

For an assurance layer, the worst failure is not a wrong number but a number that is quietly missing. Past incidents were mostly of that kind.

| ID | Rule |
|---|---|
| FIN-RL-01 | Every mirror must have a **tie-out** against NC (row counts and money totals), run on schedule. A failed tie-out must be visible, not shown as green. |
| FIN-RL-02 | Any row a report filters out must be **counted and inspectable**. Silently dropping rows is not allowed. |
| FIN-RL-03 | A record skipped during incremental sync is a **visible failure**, not a silent `continue`. |
| FIN-RL-04 | Every report that reads mirrored data states **how fresh the data is** (last sync). |
| FIN-RL-05 | A "no match" is never a conclusion on its own. Any negative finding needs a second check on amount and supplier before it is reported. |
| FIN-RL-06 | Currencies are never mixed in a total. |

Status: FIN-RL-01 is met for the AP mirror but **not for the voucher mirror** (FIN-OI-01). FIN-RL-04 is met on the AP pages, but there is no unified freshness display (FIN-OI-02).

### 1.6 Review and exit conditions

Review this positioning when any of the following becomes true:

- UniOps gains sales, inventory, production or fixed-asset modules that close the accounting loop.
- NC65 maintenance cost or compliance risk forces a replacement.
- The L0 tie-outs stay stable over a long period, showing UniOps can carry a ledger.

L0 mirror tables are designed so that they *could* later become UniOps' own ledger. A future switch would then change the data source rather than require a rebuild.

---

## 2. Users and Roles

### 2.1 Primary users

| Role | Main needs | Layer |
|---|---|---|
| AP / AP Clerk | Three-way match, duplicate payment, due invoices, abnormal invoices, NC export | L2 |
| Payment Officer | Cash requirement, bank balances, payment runs, bank reconciliation | L3 |
| Finance Manager | Reports, closing, reconciliations, audit trail | L2 + L3 |
| Finance BP | Budget vs actual, department cost analysis | L3 + L4 |

All four use the system every day. None of them is a dashboard audience, so checks and reports take priority over dashboards.

### 2.2 Roles and permissions

| Role / key | Grants in Finance |
|---|---|
| Finance role set: System Admin, Finance Manager, Finance BP, AP Clerk, Payment Officer (primary or additional role) | Payments hub reads, vendor-credit netting preview, NC AP export, AP check judgements (dismiss / duplicate review) |
| `view_finance` | Finance menu entries (navigation only, see §15) |
| `view_budget_dashboard`, `view_budget_plans` | Budget menu entries |
| `finance.coa.manage` | COA edits and CSV import, NC COA sync, account mappings, GL opening balance and year close, AR writes, FX rates, AP invoice void |
| `finance.jv.post` | Review / post / unpost / reverse UniOps-originated journal vouchers |
| `finance.period.close` | Close / reopen fiscal periods (API only) |
| `finance.bank.reconcile` | Import statements and payment files, match, sign off, reopen |
| `finance.bank.settings` | Bank-account master, including the link to the NC bank account |
| `finance.analysis.view` | AI Financial Analysis pages (see AI-FINANCIAL-ANALYSIS.md) |
| `epms.vendor_credit.manage` | Approve / reject / void vendor credits (EPMS UI, see EPMS.md §3.18) |
| Payment authority | Payment Officer, Finance Manager, Finance BP (primary or additional role) and System Admin (primary role only). **AP Clerk cannot execute payments.** |
| Remittance sending | Payment authority roles plus AP Clerk |
| NC voucher sync trigger and interval | System Admin only |
| Finance Director | Payment sign-off step on PAs, configured in the PA workflow (EPMS.md §3.15). Finance Director does not execute payments. |

The permission matrix and role assignment are managed in Portal → Admin → Access Control (PORTAL.md).

---

## 3. Module Overview

### 3.1 Navigation

| Section | Pages |
|---|---|
| AP | Accounts Payable · AP Reconciliation · AP Subledger Health · AP Duplicate Invoices · AP Cash Flow · Payments · Payment Batches |
| AR | Accounts Receivable |
| General | General Ledger · Account Balance |
| Voucher | Journal Vouchers · JV Validation |
| Budget | Budget Dashboard · Budget Plans · Budget Actual · Account Catalog · Factor Library · Budget Config (see BUDGET.md; Budget Dashboard, Budget Plans and Account Catalog are EPMS pages embedded in a frame) |
| Banking | Bank Reconciliation · Bank Settings (plus Statement Import (CSV), reached from the workbench) |
| AI Financial Analysis | Separate PRD: AI-FINANCIAL-ANALYSIS.md |
| Settings | Chart of Accounts · Tax Settings · QuickBooks (legacy archive) |

A failed permission load shows "Couldn't load your Finance permissions" with a retry, never an empty "no access" screen. The AI Assistant is available in Finance (see EPMS.md §3.19).

### 3.2 Service boundaries

- **Finance service:** NC mirrors and syncs, COA, periods, vouchers and GL reports, AP, AR, payments, remittance, vendor credits, bank and bank reconciliation, tax worksheet.
- **Budget service:** budget catalog, plans and settings (BUDGET.md). Finance supplies the NC actuals the budget pages read.
- **MDM service:** vendors, tax codes and other master data.
- **EPMS / OA (expense):** raise invoices and PAs, push AP invoices to Finance, and forward payment execution to Finance.
- **File service:** stores bank statements, payment files and signed reconciliation reports.
- **NC65 (Oracle, read-only):** the source of every mirror.

---

## 4. NC Data Mirror (L0)

### 4.1 Voucher (JV) mirror

| FR ID | Requirement |
|---|---|
| FIN-SYNC-01 | Only the Canada Royal Milk account book is mirrored. The other books in the shared NC database are excluded. |
| FIN-SYNC-02 | **Incremental sync** uses a watermark plus de-duplication on the NC voucher key. **Full reload** clears and reloads the mirror, and requires typing the confirmation phrase "FULL RELOAD". Only one run at a time; a second trigger is refused. Progress is shown live. |
| FIN-SYNC-03 | **Scheduled sync** runs incremental only, every N minutes. Default 60, maximum 1,440, 0 turns it off. The interval is re-read every minute, so a change takes effect without a restart. It counts from the last run's start, so an unreachable NC is retried once per interval. Full reload is never scheduled. |
| FIN-SYNC-04 | Trigger and interval are **System Admin only**: Finance → Journal Vouchers (NC Sync button) and Portal → Admin → NC Sync → **JV Sync** tab. |
| FIN-SYNC-05 | A mirrored voucher is `posted` only when NC has tallied it (记账) and its state is normal. Otherwise it is `draft`. Status changes in NC are backfilled on later runs. |
| FIN-SYNC-06 | The NC voucher state (normal / discarded / temp-saved / error) is mirrored. Vouchers discarded in NC are kept and marked, not dropped. A voucher deleted in NC after import is marked `deleted` and stays listed until the next full sync. None of these states counts as spend under any basis. |
| FIN-SYNC-07 | NC red-entry (negative) signs are preserved. Voucher numbers use the `JV-YYYYMM-NNNN` form and keep NC's numbers. |
| FIN-SYNC-08 | NC header facts are mirrored: preparer, checker, tallier, NC system (GL / AP / AR …), voucher kind, attachments. |
| FIN-SYNC-09 | Auxiliary dimensions are resolved per line: department, cost centre, income-expense item, partner (supplier ∪ customer), material, project, employee and bank account. A missing auxiliary raises an error. It never silently empties the dimension. |
| FIN-SYNC-10 | Cost-centre resolution: on the five budget categories (5101, 5301, 6601, 6602, 6603) the cost centre is resolved **account-aware** through the cost-centre map. All other accounts use the curated NC-code map: the NC code decides when present, otherwise the department. **A synthesised cost centre is a derived value, not an NC fact.** Manufacturing accounts (5xxx) carry NC's real cost centre on 100% of lines. Period-expense accounts (66xx) carry none, so their cost centre is always derived. |
| FIN-SYNC-11 | After each run, JV dimension validation runs and refreshes one standing task (§7). |
| FIN-SYNC-12 | **Tie-out of the voucher mirror against NC (counts and totals per period and account): Not implemented.** See FIN-OI-01. |

### 4.2 Chart of accounts and auxiliary sync

| FR ID | Requirement |
|---|---|
| FIN-SYNC-20 | NC COA and per-account auxiliary items are synced with **Preview → Apply** on the Chart of Accounts page. Preview shows three separate buckets: new, changed and deactivated. It also warns when a changed account is still referenced by a mapping. Gated by `finance.coa.manage`. |
| FIN-SYNC-21 | Account type, normal balance and the leaf flag are taken from NC's own data, never inferred from the code prefix. An unmapped value stops the sync. There is no default fallback. |
| FIN-SYNC-22 | Accounts that came from the sync are marked read-only ("Managed by NC Sync") in the editor. |

### 4.3 AP and payment mirror

| FR ID | Requirement |
|---|---|
| FIN-SYNC-30 | NC payables (bills and lines) and NC payments (headers and lines) are mirrored. The watermark is NC's timestamp, which moves when a bill is settled. The changed set is the union of header and line changes. |
| FIN-SYNC-31 | Incremental sync cannot detect deletions. Only a full reload removes them. |
| FIN-SYNC-32 | **Every run records a tie-out** of whole-population counts and money totals against NC. A run that finishes but does not tie out is recorded as such and logged. It never shows as plain success. |
| FIN-SYNC-33 | Scheduled hourly by default (interval configurable, 0 = off). The duplicate-invoice check runs after each sync (§9.3). |
| FIN-SYNC-34 | Pages built on this mirror show when it was last synced. |
| FIN-SYNC-35 | **A manual "sync now" button for the AP domain in the UI: Not implemented.** The trigger exists only in the API (System Admin). See FIN-OI-03. |

### 4.4 Other mirrors

| FR ID | Requirement |
|---|---|
| FIN-SYNC-40 | NC bank-account auxiliary values for this book are mirrored. They are the only thing that separates the banks booked on the single checking account 100201 (§12). |
| FIN-SYNC-41 | NC customers are mirrored for the partner dimension. A partner resolves against suppliers, customers or both, and keeps its NC code as text when it cannot be resolved. |
| FIN-SYNC-42 | A unified **NC Sync Center**: one row per domain with watermark, last run, result, error count, and incremental / full buttons. **Not implemented.** Portal → Admin → NC Sync currently has three tabs: Purchase, JV and MDM. See FIN-OI-02. |

---

## 5. Chart of Accounts, Mappings and Periods

| FR ID | Requirement |
|---|---|
| FIN-COA-01 | The COA uses NC's Chinese account structure: 1 assets, 2 liabilities, 4 equity, 5 cost, 6 P&L. English names are preferred. Each account has type, normal balance, leaf / header flag, parent, required auxiliary dimensions, and NC parity metadata. |
| FIN-COA-02 | Header (non-leaf) accounts roll up their whole subtree in every balance report, as NC does. |
| FIN-COA-03 | **Account mappings:** `line_role` maps posting roles to accounts (e.g. vendor-credit clearing → 1123 Advance to suppliers). `budget_account` maps budget accounts to GL accounts. |
| FIN-COA-04 | Auxiliary-dimension types catalogue. |
| FIN-COA-05 | The page also offers create, edit, deactivate, delete (only if never referenced) and CSV import/export, gated by `finance.coa.manage`. **Under the positioning these edit functions should be withdrawn and the page turned into a read-only mirror view: Not implemented** (FIN-OI-07). |
| FIN-COA-06 | **Cost-centre map (account-aware):** maps (account, department, NC cost centre) to a UniOps cost centre for the five budget categories. Loaded from an Excel file by an operator script. **No UI** (FIN-OI-08). |
| FIN-PER-01 | Fiscal periods are open, soft-closed or hard-closed. A missing period row means open. A closed period (soft or hard) blocks payment execution and posting / unposting / reversing of UniOps vouchers. Close and reopen need `finance.period.close`. A hard-closed period cannot be reopened through the system. **No UI page for period management** (FIN-OI-09). |

---

## 6. Vouchers and General Ledger Reports

### 6.1 Journal Voucher Center

| FR ID | Requirement |
|---|---|
| FIN-JV-01 | The list shows what NC's voucher list shows: number, date, period, summary, preparer / checker / tallier, NC system, voucher kind, status, NC state, amounts. Columns sort on the server. |
| FIN-JV-02 | Query filters: period or period range, date range, number range, status, NC voucher state, voucher kind, preparer, checker, manager, account, contra account, currency, amount range, auxiliary type + value, source document type, source subsystem, free text. |
| FIN-JV-03 | The detail modal shows lines with account, debit / credit (original and CAD), currency, rate and resolved dimension names. |
| FIN-JV-04 | **NC-mirrored vouchers are read-only.** Their status follows NC. Review, post and reverse are refused for them, including in batch actions. |
| FIN-JV-05 | UniOps-originated vouchers (from the internal posting records, §14) follow draft → reviewed → posted, with unreview, unpost and red-entry reversal. The reversal is a new negative voucher; the original is marked reversed. All of these need `finance.jv.post` and an open period. If the SoD rule *jv_self_review* is enabled, the preparer cannot review their own voucher. Batch review and post are available. |

### 6.2 Account Balance (NC 科目余额表 parity)

| FR ID | Requirement |
|---|---|
| FIN-AB-01 | Per account for a period: opening, period debit, period credit, closing, in CAD. Header accounts are indented and bold, and roll up their subtree. |
| FIN-AB-02 | **Opening window:** the opening balance is counted from the most recent NC year-opening batch (period `YYYY-00`) up to the report period, not from the start of time. NC restates every balance in that batch each year, so adding up all history would multiply balances. If the current year's batch is missing, the window falls back to the previous year. The same window is used by the trial balance, account ledger and balance sheet. |
| FIN-AB-03 | **"Include unposted vouchers"** (default **ticked**) adds vouchers NC holds but has not tallied, as NC's own report can. It moves both the opening and the movement. Drill-down marks rows that are only entered, not tallied. Discarded, temp-saved, error and deleted NC vouchers are never counted. |
| FIN-AB-04 | Expand any account by one or more of its auxiliary dimensions, in any combination. Each expanded row shows opening / movement / closing. Monthly view shows only dimensions that moved in the period. |
| FIN-AB-05 | Drill from any row or dimension combination to the vouchers behind it, and from there to the voucher detail. |

### 6.3 General Ledger page

| FR ID | Requirement |
|---|---|
| FIN-GL-01 | Tabs: Trial Balance (with drill to the account ledger), Balance Sheet, Income Statement (period or year-to-date), Journal. These read posted vouchers. |
| FIN-GL-02 | Opening Balance entry and **Close Year** (moves P&L to retained earnings) are offered to `finance.coa.manage` holders. Both write UniOps vouchers into the same ledger that holds the NC mirror. NC's own year-opening and carry-forward vouchers are already mirrored, so **these two functions conflict with Red line 1** and should be withdrawn (FIN-OI-10). |
| FIN-GL-03 | The Income Statement does not exclude year-end closing vouchers. A year-to-date December statement may therefore net to zero. **Defect** (FIN-OI-11). |

---

## 7. JV Validation (L2)

| FR ID | Requirement |
|---|---|
| FIN-VAL-01 | Finds posted NC voucher lines that cannot reach a budget cell. Such lines are not wrong-looking anywhere; they are silently missing from Budget vs Actual. Rules, in order of cost: no cost-centre mapping for this account × department (needs a decision); NC cost-centre code not in the mapping; income-expense code not in the budget catalog; no income-expense item; financial-expense account with no budget account; account category and cost centre disagree; department and cost centre disagree; cost centre differs from what the current map resolves to. |
| FIN-VAL-02 | The page lists findings by rule with drill-down to lines. Payroll, Depreciation and Shut-down loss are excluded by policy and labelled as such, not reported as defects. |
| FIN-VAL-03 | The same rule set raises **one standing task** ("JV VALIDATION", type *resolve_jv_validation*, to System Admin) after every voucher sync. The task covers the current and previous fiscal year, is refreshed with counts on each run, and closes itself when the range is clean. |

---

## 8. Budget Actual (Report Layer)

Budget planning, the dashboard, catalog, factors and budget settings (including the over-budget mode) are specified in BUDGET.md. Finance supplies the actuals.

| FR ID | Requirement |
|---|---|
| FIN-BA-01 | **Actual = gross period debit** on five category accounts, grouped by cost centre: 5101 MOH, 5301 R&D, 6601 Selling, 6602 G&A, **6603 Financial expenses**. Gross debit is used rather than net because manufacturing overhead is cleared at period end and nets to about zero. |
| FIN-BA-02 | Payroll, Depreciation and Shut-down loss are tracked at category level only. They appear as category-level tie-out rows, never spread over cost centres. |
| FIN-BA-03 | **Grid:** approved plan vs NC actual per cost centre × income-expense item, with an exceptions panel for posted lines that could not be mapped. |
| FIN-BA-04 | **Roll-up:** company → expense centre (MOH / RD / SELL / GA / FN, taken from the cost-centre code prefix) → department → cost centre. Two measures: period plan vs actual over any month range (month / quarter / YTD / custom), and full-year plan vs actual to date. "Expand by months" applies to multi-month windows. |
| FIN-BA-05 | **Unallocated** amounts (category-level items, and lines with no cost centre) are shown. A reconciliation line proves that company actual + unallocated = every posted category line in the period. |
| FIN-BA-06 | "Include unposted vouchers" (default ticked) uses the same basis rule as Account Balance. |
| FIN-BA-07 | Excel export of the on-screen grouping, fully expanded. Text cells that begin with "=" are written as text. Partner (vendor) breakdown export. |
| FIN-BA-08 | Each figure can explain how it is arrived at (lineage), and the exclusion list is derived from the query itself. |
| FIN-BA-09 | Finance also serves NC actuals to the EPMS Budget Dashboard and to the PR over-budget check. The PR check counts unposted vouchers and is deliberately not limited to the requester's departments. See BUDGET.md and EPMS.md. |

---

## 9. Accounts Payable

### 9.1 UniOps AP invoices (business-document hub)

| FR ID | Requirement |
|---|---|
| FIN-AP-01 | EPMS (procurement) and OA (expense) push their vendor invoices into one AP table, keyed by source + source document. Statuses: draft, posted, partially paid, paid, void. |
| FIN-AP-02 | A vendor + vendor invoice number is unique. A second one is refused with "duplicate vendor invoice". This is the anchor for reconciling against NC. |
| FIN-AP-03 | When the source deletes an invoice: an AP row with no postings and no payment is removed; a posted one is voided and its accrual reversed. |
| FIN-AP-04 | **Accounts Payable page:** paginated list with vendor and vendor-invoice-number search. Exported invoices are hidden by default. Aging tab: vendor × currency, five buckets by due date. |
| FIN-AP-05 | **Export to NC** (any finance role): select invoices and download the NC payable-module import workbook. One summary row per invoice — except a **courier invoice** (EPMS → Freight), which has one row per cost center from its matched lines, budget item CRM0150102, and tax rate 0 on a row with no tax; a courier invoice whose lines are not all assigned to a cost center is refused with a reason. All enumerations are NC codes. Expense account comes from cost centre / department. Dimensions come from the PR (EPMS), from the Direct PA paying it for an EPMS Direct invoice (refused with a reason while no live Direct PA lists it), or from the PA (OA). Zero tax-excluded amount and tax are written explicitly. The file imports into NC without being re-saved in Excel. NC assigns the document number; the file is named after the AP number. |
| FIN-AP-06 | Draft or void invoices, and invoices without an accrual, are refused with reasons. Exported invoices are stamped with a batch. The UI asks for confirmation before re-exporting. |
| FIN-AP-07 | Under the positioning, **AP balances are read from NC** (§9.2–9.5). The UniOps paid amount is not used for balances or cash flow, because NC payments do not flow back. |

### 9.2 AP Reconciliation: where each UniOps invoice stands in NC

| FR ID | Requirement |
|---|---|
| FIN-APR-01 | Each UniOps invoice is put in one category: **missing in NC**, in NC but never approved, invoice-number mismatch (same supplier and amount under a different number), in NC and still open (real debt), in NC and settled, not submitted (our draft). |
| FIN-APR-02 | Matching uses the normalised invoice number (upper case, whitespace removed) plus the supplier (vendor ERP code = NC supplier code). Before an invoice is called missing, the same supplier + amount within 120 days is searched under other numbers (FIN-RL-05). |
| FIN-APR-03 | "Still open" is read from NC's open balance on the bill line, never from UniOps. |
| FIN-APR-04 | Additional panels: **amount mismatch** (NC billed a different amount) and **date anomalies** (due date before invoice date, or an invoice date in the future, typically a day/month swap from OCR). |
| FIN-APR-05 | Everything is derived when read. No reconciliation table is stored. |

### 9.3 AP Duplicate Invoices

| FR ID | Requirement |
|---|---|
| FIN-APD-01 | Rule set by finance (2026-09-25): **vendor name + invoice number is unique on NC payables, whatever the amount.** NC does not enforce this, and the same invoice has been paid twice. The same number from two different vendors is not a duplicate. |
| FIN-APD-02 | A bill and the credit that reverses it, netted per currency and exact amount, count as one live copy. |
| FIN-APD-03 | Finding kinds: **exact** (same amount on two or more live copies), **amount differs**, and **pending** (an unapproved payable repeating an existing vendor/number). |
| FIN-APD-04 | Finance can record a verdict: not a duplicate / deliberate split / recovered / being corrected in NC / other (a note is required for "other"). A verdict lapses when a new bill joins the group. Unreview is available. |
| FIN-APD-05 | One standing AP task (assigned to AP Clerk) is refreshed after every AP sync. It is urgent when money is still payable on a repeated copy or a draft is about to be approved. It closes itself when nothing unreviewed remains. |

### 9.4 AP Subledger Health

| FR ID | Requirement |
|---|---|
| FIN-APH-01 | Only bills NC actually approved count (bill status = 1 and approve status = 1). Without this filter the "open" figure is inflated by unapproved documents (about 36M of 47M at measurement time). |
| FIN-APH-02 | **Excluded** (shown with amount, not worked): payables raised and never approved. **Inconsistent**: approved bills where open ≠ billed − paid (tolerance 0.01). This is kept as a tripwire. |
| FIN-APH-03 | **Supplier view** with block subtotals, bill-to-payment relationships, and journal entries that cancel each other folded away. A GL search looks for differences finance settled by manual voucher. |
| FIN-APH-04 | **Dismiss** stale NC payables from the working list with a closed reason set: legacy (NC period closed), duplicate document, settled outside AP by journal, written off, other (note required). Dismissal can be reversed (restore). Bills can be judged in bulk by bill date, with bulk undo. Dismissed bills are excluded from AP Cash Flow. Vendor search is available. |
| FIN-APH-05 | **A bill can carry several invoices.** In NC the invoice number is on the bill **line**, and one payable often holds several invoices from the same supplier (at measurement, 1,872 of 12,006 approved bills, 65 of 364 open ones). Wherever a bill is shown as one row, the invoice column lists each invoice number on its lines with that invoice's own open amount; the amounts add up to the row's amount. Lines with the same invoice number are one invoice; lines with no number are shown together as "no number". A bill with one invoice shows just the number. The PO column lists every PO on the bill. Sorting by invoice number or PO uses the smallest value on the bill. CSV exports write the invoices as `number (amount); number (amount)`. Dismissal stays per bill. |

### 9.5 AP Cash Flow

| FR ID | Requirement |
|---|---|
| FIN-CF-01 | Based on NC's **approved open balance**, per currency, excluding dismissed bills. |
| FIN-CF-02 | Due date = NC bill date + the **vendor master payment term** (`net<N>` = N days for any N in 1–365, not only the Net 15/30/60/90 presets; COD and prepayment = 0). An unknown or missing term is **not** defaulted to net30. Such bills are reported as **undated** ("No payment term on file") with the amount and the supplier list. |
| FIN-CF-03 | Buckets: overdue >180 / 91–180 / 31–90 / ≤30 days; due within 7 / 8–30 / 31–60 / 61–90 / beyond 90 days; undated. Clicking a figure lists its bills. |
| FIN-CF-04 | Missing-terms panel lists NC suppliers with no vendor record, and the largest single-date amount, which is typically NC's go-live opening load. |
| FIN-CF-06 | The drill-down shows every invoice on a bill with its own open amount, as in FIN-APH-05. The due date stays per bill (NC bill date + vendor term). |
| FIN-CF-05 | **Not implemented:** invoice-level term precedence (invoice term → vendor term → "term unknown"); further layers (UniOps invoices not yet in NC, received-not-invoiced, ordered-not-received, recurring agreement schedules, a monthly milk layer). See FIN-OI-12. |

---

## 10. Payments, Payment Batches and the Payments Hub

Executing PA payments from EPMS is specified in EPMS.md §3.18. This section covers the Finance side.

| FR ID | Requirement |
|---|---|
| FIN-PAY-01 | A **single executor** handles all money going out: EPMS PA "process", OA Direct PA and expense-claim payments all forward to it. Under one transaction it checks authority, segregation of duties and the period gate, sets the status, writes the payment record, writes the internal posting, and writes back the invoice / AP status. |
| FIN-PAY-02 | Authority: Payment Officer, Finance Manager, Finance BP (primary or additional role); System Admin as primary role only. AP Clerk is excluded. |
| FIN-PAY-03 | SoD *self_payment* (configurable): the creator of a PA, or the claimant of an expense claim, cannot execute its payment. |
| FIN-PAY-04 | Refused when: the payment date falls in a soft- or hard-closed period; the PA is not `approved`; the claim is a Travel Application (TRA — it carries no money, OA.md §5); the funding account's currency differs from the payment's; or a PO-route non-prepayment PA links no invoice while its PO still has a payable invoice no live PA claims. |
| FIN-PAY-05 | Payment record: document reference, payment date, method, amount, currency, **funding bank account or credit card**, batch, credit applied. The legacy direct-create payment endpoint is retired. |
| FIN-PAY-06 | **Payment Batches:** pick approved PAs and claims from the due list (vendor invoice number shown) and execute them together. Each line goes through the same executor. For vendor PAs the dialog shows a vendor-credit netting preview per line, where credits can be unticked. The remittance dialog opens after execution when remittance is enabled. The due list has a **type filter** — All / PA / Freight PA / Direct PA / Expense Claim (a Freight PA — courier invoices paid from EPMS → Freight, no PO — is paid exactly like a PA and its invoices are marked paid; *Direct PA* covers EPMS Direct PAs, paid the same way, and OA's retired ones), each with its count for the selected currency — a Submitted date column, and a **Due Date** column — the earliest due date among the PA's invoices (invoices on one PA fall due within 7 days of each other), shown in red once it has passed; claims and PAs without invoices show "—". **Every column header sorts the list**: the first click sorts ascending, the next click reverses it, and a header shows ↕ until it is the active sort. Rows with no value in that column (no due date, no invoice number) stay at the bottom in both directions; amounts sort as numbers, and document and invoice numbers in number-aware order (508027 before 2026000066). Sorting works together with the type filter and starts from the API order on each visit. Currency and type are kept in the URL (`?currency=…&type=claim`). A claim's number opens the OA claim detail as a tab inside Finance; each of a PA's vendor invoice numbers opens that EPMS invoice as a tab inside Finance. OA Direct PAs show their invoice number as plain text, since OA no longer has an invoice detail page. Approved Travel Applications are **not** in the due list, and a batch that names one is refused. |
| FIN-PAY-07 | **Payments hub:** list with filters (vendor, date range, document kind, currency, method, status, funding account, batch, source batch/single, remittance sent/not sent, free text), summary, CSV export, and multi-select remittance. The Method column shows the funding account. Reads need a finance role. The hub lists money already paid, so above it a **banner per currency** shows approved expense claims still awaiting payment (count and total) and links to the Payment Batches due list filtered to expense claims in that currency. No banner when there are none. |
| FIN-PAY-08 | Paying closes the matching *process_pa* / *process_expense* tasks. |

---

## 11. Vendor Credits and Remittance Advice

### 11.1 Vendor credits

Credit capture, review and the EPMS screens are in EPMS.md §3.18.2. The Finance side covers the following.

| FR ID | Requirement |
|---|---|
| FIN-VC-01 | Credits are stored as positive amounts whatever sign the document carries. Sources: credit-note upload, manual credit (vendor email as evidence), QuickBooks import. There is one live credit per vendor + credit-note number. Statuses: pending review → available → exhausted, or void. |
| FIN-VC-02 | At payment, available credits of the same vendor and currency are netted **oldest first** up to the payment amount, with row locking. Credits do not apply to expense claims. Each application is recorded. |
| FIN-VC-03 | The payment posting splits its credit side into bank + vendor-credit clearing (mapped to 1123 Advance to suppliers). |
| FIN-VC-04 | **QBO credit import** (temporary migration tool, to be removed with QuickBooks): imports only the unapplied **balance** of still-spendable QBO credits, never the face value. Goes through the normal create path. Vendor mapping is supplied with the request. |

### 11.2 Remittance advice

Recipient, line and blocking rules match EPMS.md §3.18.3 (REM-001 to REM-006).

| FR ID | Requirement |
|---|---|
| FIN-REM-01 | Scopes: single payment, payment batch, or a hand-picked selection. An advice already sent for the same payee and scope is skipped unless the payee is marked for resend. |
| FIN-REM-02 | **Preview & Send → render → Confirm & Send**: the operator sees the exact emails (From / To / Cc / Subject / HTML) before anything is sent. Block reasons are computed live. |
| FIN-REM-03 | Each payee is sent and logged on its own and committed straight away. One failure does not stop the rest, and a sent email is never recorded as "not sent". |
| FIN-REM-04 | The advice shows gross / credit notes / net when credits were netted, and the vendor's invoice number. Prepayment PAs with no invoice number appear as "Prepayment - PO …" and are not blocked. A payment-date picker changes only the date printed on the email. |
| FIN-REM-05 | Sender, CC, enable switch, template text and logo come from Portal → Admin → Remittance Advice settings. Data values are HTML-escaped. Address lists accept commas or semicolons and are normalised before sending. |

---

## 12. Bank and Bank Reconciliation

### 12.1 Bank Settings

| FR ID | Requirement |
|---|---|
| FIN-BANK-01 | Bank accounts and credit cards: name, bank / issuer, kind, masked number, currency, ledger account, active flag, CSV column-mapping template. |
| FIN-BANK-02 | **Link to the NC bank account** (the bank-account auxiliary on 100201). The picker offers only this book's accounts. The link must be made for each account before it can be reconciled. Accounts are named the way finance names them. |
| FIN-BANK-03 | Gated by `finance.bank.settings`. FX rates (currency pair + effective date) are maintained under `finance.coa.manage`. |

### 12.2 Reconciliation workbench (v2)

| FR ID | Requirement |
|---|---|
| FIN-BR-01 | **Book side = NC's ledger** for the linked bank account: opening, lines and closing, each line with its contra account. The contra account classifies the movement: AP payment, other payable, inter-bank transfer, payroll, credit-card repayment, bank charges. |
| FIN-BR-02 | **Foreign-currency accounts reconcile in their own currency.** Original amounts are read, not CAD. Lines with no amount in that currency (FX revaluation, CAD-only carry-forward pairs) are kept out of matching and listed separately with their CAD total. CAD accounts read CAD. |
| FIN-BR-03 | **Statements** are imported from PDF (encrypted PDFs handled). The model used is Haiku only. A statement is accepted only if it **verifies against its own printed anchors**: opening + signed lines = closing, debit/credit counts and totals, and every printed running balance. An unverified statement is stored with reasons and refused by the reconciliation. Re-importing does not duplicate lines. The CSV importer remains for banks whose PDF cannot be read yet. |
| FIN-BR-04 | **Payment files** (PDS batches, bill-payment confirmations) are parsed by rule first, with AI only when the rule fails. A file is used only if it ties: sum = printed total and count = printed N, or consecutive row indices. |
| FIN-BR-05 | **Matching ladder** (automatic, idempotent), producing N statement lines ↔ M ledger lines groups: advice total; 2–3 advice totals combined; confirmation number; advice line → ledger line; direct one-to-one; subset-sum within a vendor; book subset with no payment file; month roll-up of card settlements; a unique-amount pair outside the window is *named* for a person, not cleared. Anything left goes to manual matching with ranked candidates. A statement line clears only once. |
| FIN-BR-06 | **Basis:** "Include unposted vouchers" is stored per period and **ticked by default**. Periods signed off before this default keep the posted-only basis they were signed on. |
| FIN-BR-07 | The difference is split into the amount **carried in** (opening) and the amount that **arose this period**. Past periods can be reached. A matched group can be located on screen, and the screen says which matches are backed by evidence. |
| FIN-BR-08 | **Sign-off** is refused unless the difference is zero and the statement is verified. Sign-off freezes a snapshot: later imports or NC re-syncs never rewrite it. A PDF **report** in the QuickBooks reconciliation layout, always including both outstanding sections, is generated and stored with the statements and payment files. All of these documents are listed per period. **Reopen** is allowed with `finance.bank.reconcile`. |
| FIN-BR-09 | After a full NC voucher sync, **Heal** re-points ledger references using NC's natural key. Signed-off periods are skipped. |
| FIN-BR-10 | If the file service is unavailable, sign-off still completes with a warning that the document was not kept. |
| FIN-BR-11 | Legacy v1 functions remain: import with hash de-duplication; match / unmatch / exclude against payment records; auto-match. |

---

## 13. Accounts Receivable and Tax

| FR ID | Requirement |
|---|---|
| FIN-AR-01 | Basic AR held by finance, because there is no sales module: AR invoices with tax lines (draft → posted → partially paid → paid, or void), customer receipts applied to an invoice or held on account, open items and aging. Writes need `finance.coa.manage`. Customers and sales are NC-owned under the positioning; AR has low priority. |
| FIN-TAX-01 | Tax codes (HST/GST/PST and rates) are maintained in MDM through Finance → Tax Settings. PO / PA / invoice record the tax code and a rate snapshot. |
| FIN-TAX-02 | A GST/HST return worksheet is available through the API only (ITC from AP accrual and expense payments, output tax from AR, net, uncoded exceptions, CSV export). It reads UniOps' internal posting records, not NC. **No UI. Not aligned with the positioning** (FIN-OI-13). |

---

## 14. Internal Posting Records and Legacy Functions

| FR ID | Requirement |
|---|---|
| FIN-SPINE-01 | UniOps still writes an internal posting event for AP accrual (invoice posted), payment, expense payment and AR events. Each event is idempotent per document + event type and debits and credits are kept separate. Each event also creates a draft UniOps voucher. **These records are an internal trail, not the book of record.** They are not exported to NC; the AP export in §9.1 is the handoff. |
| FIN-SPINE-02 | Under "Include unposted vouchers", UniOps' own draft vouchers are counted together with NC's untallied ones. They may duplicate amounts NC also books for the same invoice or payment. **To be confirmed** (FIN-OI-14). |
| FIN-QBO-01 | **QuickBooks mirror:** an archive of the previous system (vendors, bills, payments, credits, journal entries, attachments), with sync, browse, export, and "Fill Remittance Emails" (copies QBO vendor emails into empty vendor remittance emails, fill-only). It is under Settings and is to be removed when QuickBooks is retired. |
| FIN-LEG-01 | Legacy budget-ledger endpoints (balance / commit / release / actualize) are still present. Under the positioning, the budget ledger is to be removed and replaced by a derived commitments view (see BUDGET.md, FIN-OI-15). |

---

## 15. Security and Integration Notes

- **The Finance menu is a navigation switch only.** On the server, many Finance read endpoints accept any signed-in user. These include the GL reports, Account Balance, Budget Actual, vouchers, the AP check pages, bank reconciliation reads and vendor-credit list / detail. The QuickBooks sync and vendor-email backfill writes also have no role check. **Defect** (FIN-OI-04, FIN-OI-05).
- Deliberately open reads: the department-scoped NC actuals used by the EPMS Budget Dashboard (rows are limited to the user's own departments), the AP invoice ingestion and settlement endpoints that EPMS / OA call with the end user's token, and the yes/no permission probes.
- Payment, remittance, bank, period, COA, voucher and vendor-credit-review writes are gated as listed in §2.2.
- Finance Director holds the Finance menu but is not in the finance read role set, so Payments hub reads are refused unless another finance role is held (FIN-OI-06).
- Finance embeds the EPMS budget pages in a frame and passes the browser's local-network permission to that frame.

---

## 16. Open Items

| ID | Item | Status |
|---|---|---|
| FIN-OI-01 | Tie-out of the voucher mirror against NC (counts and totals per period / account), run on schedule, with a task when it fails | Not implemented |
| FIN-OI-02 | Unified NC Sync Center page, plus "data as of" shown on every mirrored report | Not implemented |
| FIN-OI-03 | Manual incremental sync button for the AP / payment mirror in the UI | Not implemented |
| FIN-OI-04 | Finance read endpoints need a finance-role check on the server (any signed-in user can read GL, AP, bank and vendor-credit data). A fix exists on an unmerged branch | Defect |
| FIN-OI-05 | QuickBooks sync / full reload and vendor-email backfill have no role check; the sync status always reports "can sync" | Defect |
| FIN-OI-06 | Finance Director is not in the finance read role set; Payments hub reads return 403 | Defect (confirm intent) |
| FIN-OI-07 | Turn Chart of Accounts (and Tax Settings) into read-only mirror views; remove create / edit / delete / CSV import | Not implemented |
| FIN-OI-08 | UI for the account-aware cost-centre map (currently loaded by an operator script) | Not implemented |
| FIN-OI-09 | Fiscal period close / reopen page (API only today) | Not implemented |
| FIN-OI-10 | Withdraw GL Opening Balance and Close Year: they write UniOps vouchers on top of NC's mirrored year-opening and carry-forward vouchers | Open |
| FIN-OI-11 | Income Statement does not exclude year-end closing vouchers | Defect |
| FIN-OI-12 | AP Cash Flow: invoice-level term precedence; layers for UniOps invoices not yet in NC, received-not-invoiced, ordered-not-received, agreement schedules, milk; vendor-term governance prompt (all 752 vendors defaulted to net30 at measurement) | Not implemented |
| FIN-OI-13 | GST/HST worksheet: move onto NC data and give it a UI, or retire it | Open |
| FIN-OI-14 | Check whether UniOps-originated draft vouchers inflate figures under "Include unposted vouchers" (Account Balance, Budget Actual, Bank Reconciliation) | Open |
| FIN-OI-15 | Remove the budget ledger and its endpoints; replace them with a derived commitments view (invoices not yet posted in NC) | Not implemented |
| FIN-OI-16 | AP check 4: received but neither accrued in NC nor covered by a manual accrual voucher (needs a rule for recognising manual accrual vouchers) | Not implemented |
| FIN-OI-17 | AP check 5: orphan expense invoices with no value adjustment before month-end lock (exclude milk settlement credits) | Not implemented |
| FIN-OI-18 | Document chain: drill from a voucher or AP item through PR → PO → GR → invoice → payment | Not implemented |
| FIN-OI-19 | Accrual of employee expense claims at approval (liability for approved but unpaid claims) | Not implemented |
| FIN-OI-20 | Systematic cross-check of UniOps-exported AP vs NC vouchers by invoice number in the voucher summary (fuzzy contains-match; "no match" = needs review) | Not implemented |
| FIN-OI-21 | Decision: Budget vs Actual cost-centre basis — real NC cost centre for 5xxx vs derived value for 66xx in one report, and how derived values are labelled | Open |
| FIN-OI-22 | Decision: received-not-invoiced on EPMS purchases, which have no accrual step in NC | Open |
| FIN-OI-23 | Decision: whether a once-a-day position is enough for the Payment Officer, or bank data should be read directly | Open |
| FIN-OI-24 | Customs-duty allocation (about 20k manual voucher lines a year): a separate project; manual PO references are not authoritative | Open |
| FIN-OI-25 | Vendor-credit clearing (1123) has no matching debit posting in UniOps; confirm the treatment in NC | Open |
| FIN-OI-26 | Server-side refusal of AP re-export to NC unless explicitly allowed (today the confirmation is in the browser only) | Not implemented |
| FIN-OI-27 | AR beyond the basic framework (credit control, statements, dunning) — waits for a sales source | Not implemented |

---

## 17. Out of Scope

- Keeping a UniOps AP balance or any second valuation of NC balances.
- Editing NC master data (COA, cost centres, dimensions) in UniOps.
- PO-level traceability or PO-level budget vs actual for milk. Milk is reported by supplier × month × component.
- Using manually inferred freight or duty allocations as authoritative links.
- China-statutory processing (mandatory cash-flow items, Golden Tax invoices).
- Fixed assets, cost accounting, consolidation (cost analysis: see AI-FINANCIAL-ANALYSIS.md).
- Counting in-flight commitments in the over-budget control (alert only; see BUDGET.md).

---

## Appendix A. v1.0 Status Re-assessed

| v1.0 item | v1.0 status | Now |
|---|---|---|
| Posting spine | Live, "single source of truth" | Still writes, but only as an internal trail. NC is the source of truth (§14) |
| COA + periods | Built | COA mirrored from NC with sync. Editor still present (FIN-OI-07). No period UI |
| Journal vouchers + account balance | Backend only, UI pending | Built: Journal Voucher Center, Account Balance, NC mirror with schedule |
| NC65 migration (360 accounts, 39,839 vouchers) | Local dry run | Replaced by the live voucher and COA sync |
| AP | Live | Live. Plus NC export, NC AP mirror and four check pages |
| AR | Framework | Unchanged, low priority |
| Payments + batches | Built | Plus Payments hub, Payment Officer role, credit netting, remittance |
| Bank & reconciliation | Built (CSV, one-to-one) | Rebuilt (v2, §12) |
| Tax | Built | Tax codes live. Worksheet API-only (FIN-OI-13) |
| Budget | Live | See BUDGET.md. Budget Actual now uses 5 accounts incl. 6603 |
| Partner archive | Pending | Supplier and customer partner dimension mirrored from NC |
| Document chain, claim accrual | Pending | Not implemented (FIN-OI-18, FIN-OI-19) |
| Parallel-run AP export + JV cross-check | Pending | Export built. Cross-check not built (FIN-OI-20) |
