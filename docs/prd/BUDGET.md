# Budget — Product Requirements Document

**Version:** 2.0
**Date:** 2026-10-16
**Status:** In Production
**Scope:** The UniOps Budget module: budget account catalog and factors, annual budget plans with versioned revisions and approval, Budget Config (including over-budget modes), budget visibility scope, the Budget Dashboard and Budget Actual reports measured against NC actuals, and the budget balance service used by PRs and expense claims.

> **What changed in this version (v1.0 → v2.0)**
> - Rewritten in English and brought in line with the system on main. Every requirement was checked against the code; anything wanted but not built is marked **Not implemented** and listed in §11.
> - **Actuals now come from NC**, not from the budget ledger. Budget figures (Actual Spent, Available, over-budget alerts, the PR over-budget check) are measured against NC general-ledger actuals read from the Finance module. The PR/PO/PA chain does not write to the budget ledger (decision 2026-09-20); the ledger only holds opening-balance imports. *Committed* is therefore always 0 (§4.4).
> - **Include unposted vouchers**: the Budget Dashboard and Budget Actual report can count vouchers entered in NC but not yet tallied; ticked by default. The PR over-budget check always includes them (§4.4, §4.8).
> - **Over-budget modes**: a fourth mode, *Allow without extra approval*, was added; the setting lives in Finance → Budget → Budget Config (§4.6).
> - **Budget visibility scope** is driven by the Access Control Matrix (company-wide vs department-related), fails closed, and covers directors and the OPM (§4.7).
> - **Where the UI lives**: Budget pages are in the **Finance** module's *Budget* section; the Dashboard, Plans and Account Catalog are EPMS pages embedded in Finance (§1.4).
> - Write permissions moved to Access Control Matrix keys (*Edit Budget Catalog*, *Edit Budget Plans*, *Edit Opening Balances*) (§4.7).
> - New: Opening Balances import (§4.4.2), Budget Actual report (§4.9), Budget Dashboard XLSX export and vendor/voucher drill-down (§4.8), figure-lineage explanation for the AI Assistant (§4.4.3), plan-approval tasks open in Finance (§4.3.3).
> - Factor cap fixed at 3 per account; the configurable limit is no longer editable (§4.2). Plan cells save on blur rather than by a 5-second autosave (§4.3).

---

## 1. Module Overview

### 1.1 Positioning

The Budget module is UniOps' single source of budget plans. It:

- maintains one shared catalog of budget accounts (L1 categories and L2 accounts) used by every cost center;
- lets cost centers prepare an annual plan (12 months × account, optionally broken down by factors), submit it for approval, and revise it after approval through versioned revisions;
- compares the current approved plan with **actual spend recorded in NC** (the company's ERP and the system of record for finance — see FINANCE.md) on the Budget Dashboard and the Budget Actual report;
- answers "how much budget is left" for PRs (EPMS.md §3.2.6, §9) and for OA expense claims (OA.md).

The module is a support layer for finance: NC remains the ledger of record. Budget does not post, reserve or adjust anything in NC.

### 1.2 In scope / out of scope

| In scope | Out of scope |
|---|---|
| L1/L2 account catalog, factors, Factor Library | Departments and cost centers master data (owned by MDM; edited from Budget Config, §4.6) |
| Annual budget plans, versions, revisions, CSV import/export | The approval engine itself (shared approval service — see EPMS.md §3.3) |
| Plan approval routing configuration for `budget_plan` | NC voucher sync and the NC-to-budget-account mapping (FINANCE.md) |
| Budget Config (fiscal years, alert thresholds, over-budget mode) | PR over-budget behaviour in the PR form and approval chain (EPMS.md §3.2.6) |
| Budget visibility scope | Commitment tracking of PR/PO/PA (not built — §4.4, §11) |
| Budget Dashboard, Budget Actual report, exports | Cross-year budget transfers |
| Balance service for PRs and expense claims; opening-balance import | |

### 1.3 Architecture (summary)

- **Budget service** — a standalone backend service sharing the UniOps database. It owns the catalog, factors, plans, settings and the budget ledger.
- **Finance service** — supplies NC actuals per cost center × budget account × month (posted, or posted plus not-yet-tallied), the Budget Actual roll-up, and the Dashboard export. The Budget service calls it with the caller's own sign-in token.
- **Approval service** — runs the `budget_plan` workflow and writes plan status changes; on final approval of a revision it switches the current version.
- **EPMS service** — stores Budget Config (fiscal years, thresholds, over-budget mode) in company configuration and calls the Budget service's balance endpoint for the PR over-budget check.
- **MDM service** — owns cost centers and departments.

### 1.4 Where the UI lives

All budget pages are reached from **Finance → Budget** in the UniOps Portal:

| Menu item | Page | Implementation | Menu visibility (Access Control Matrix) |
|---|---|---|---|
| Budget Dashboard | Plan vs actual per account and month | EPMS page embedded in Finance | *View Budget Dashboard* |
| Budget Plans | Plan list and plan editor | EPMS pages embedded in Finance | *View Budget Plans* |
| Budget Actual | Finance-facing budget vs actual report | Finance page | *View Finance* |
| Account Catalog | L1/L2 accounts and factors | EPMS page embedded in Finance | *View Finance* |
| Factor Library | Reusable factor templates | Finance page | *View Finance* |
| Budget Config | Cost centers, opening balances, fiscal years, thresholds, over-budget mode | Finance page | *View Finance* |

Embedded EPMS pages hide their own sidebar and header and receive the Portal session; an *Open in EPMS* link opens the same page in a new tab. Budget pages are no longer in the EPMS sidebar (EPMS.md §3.8).

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Department** | Organisational unit (MDM). |
| **Cost Center (CC)** | Sub-unit of a department; budgets are planned per CC. |
| **L1 (Category)** | Top-level budget category; one shared catalog for all CCs. |
| **Budget Account (L2)** | Account within an L1; shared by all CCs. Its code is what PRs and expense claims carry as their budget code. |
| **Factor / Factor value** | Optional breakdown dimension of an account (e.g. Brand × Channel) and its allowed values. |
| **Budget Plan** | One CC's plan for one fiscal year, in one version (v1, v2, …). |
| **Current approved plan** | The version of a (CC, year) that is `approved` and marked current; the only version that counts in any budget figure. |
| **Plan line / breakdown** | Amount for CC × account × month; breakdown rows split a line by factor combination. |
| **NC actual** | Expense recorded in NC for a CC × budget account × month, as mirrored by the Finance module. *Posted* = tallied vouchers; *unposted* = vouchers entered in NC but not yet tallied. Discarded or deleted vouchers never count. |
| **Budget ledger** | The Budget service's own movement table. Today it holds only opening-balance imports (§4.4). |
| **Fiscal year** | Calendar year (no configurable fiscal start month). |

---

## 3. Data Model Summary

```
budget_l1                       shared L1 catalog
  └ budget_accounts             shared L2 accounts (decomposition flag)
        └ budget_account_factors        up to 3 per account
              └ budget_account_factor_values
factor_templates                Factor Library (copied into accounts on attach)
  └ factor_template_values
budget_plans                    per CC × fiscal year × version (is_current, parent_plan_id)
  └ budget_plan_lines           per account × month
        └ budget_plan_breakdowns  per factor combination
budget_ledger                   movements: opening (in use); commit/release/actualize/book_expense (no writers)
budget_settings                 single row (legacy factor limit)
```

Budget Config values (fiscal years, thresholds, over-budget mode) are stored in EPMS company configuration, not in the Budget service. Detailed table definitions: Budget service design document.

---

## 4. Functional Requirements

### 4.1 Account Catalog

| FR ID | Requirement |
|---|---|
| **BACC-001** | Users with *Edit Budget Catalog* can create, edit and delete L1 categories. L1 codes are unique. |
| **BACC-002** | Users with *Edit Budget Catalog* can create, edit and delete L2 accounts. An account code is unique within its L1. |
| **BACC-003** | Each account has a *decomposition enabled* flag that controls whether its plan amounts are entered by factor breakdown (§4.3, BPLAN-004). |
| **BACC-004** | Deleting an account (or an L1 together with its accounts) is a hard delete and is refused when any account is referenced by plan lines or ledger entries; such accounts must be deactivated instead. |
| **BACC-005** | Inactive accounts are not seeded into new plans and are not offered in pickers; existing plan lines are kept. |
| **BACC-006** | Catalog CSV export (any signed-in user) and import (System Admin only) cover the catalog definition only (code / name / L1), not plan or actual data. Import upserts by code. |
| **BACC-007** | All signed-in users can read the catalog; PR, agreement and expense-claim forms use it as the only source of valid budget codes (EPMS.md §3.2). |

### 4.2 Factors

| FR ID | Requirement |
|---|---|
| **BFAC-001** | An account with decomposition enabled can have 1 to **3** factors (hard limit). The three map to the matrix editor's axes: Primary = rows, 2nd = columns, 3rd = sub-columns. |
| **BFAC-001a** | The legacy *max factors per account* setting (1–50, default 10) still exists and can be changed by a System Admin through the API, but the effective limit is the lower of that setting and 3. It is no longer editable in Budget Config, which shows the 3-factor rule as information only. |
| **BFAC-002** | Each factor has a list of values (at least one). |
| **BFAC-003** | Changing factors or values does not alter existing breakdown rows (historical snapshot). |
| **BFAC-004** | A factor cannot be removed from an account that already has breakdowns; values can be deactivated instead. |
| **BFAC-005** | Inactive factor values are not offered when entering new breakdowns. |
| **BFAC-006** | Factor order determines its role (Primary / 2nd / 3rd); the catalog shows the role on each factor. |
| **BFAC-007** | Accounts that had more than 3 factors before the cap keep their data; the matrix editor uses the first 3 by order. |

#### 4.2.1 Factor Library

Reusable factor templates (e.g. *brand*, *channel*, *region*) that can be copied onto accounts. Attaching copies the template; afterwards the account's factor and the template are independent.

| FR ID | Requirement |
|---|---|
| **BFAC-LIB-001** | Templates have a unique, immutable code, a name, optional description, active flag, and a list of values with active flags. |
| **BFAC-LIB-002** | All signed-in users can read templates; create/edit/delete requires *Edit Budget Catalog*. |
| **BFAC-LIB-003** | Inactive templates are not offered in the account's *From Library* picker. |
| **BFAC-LIB-004** | Deleting a template always succeeds and never affects accounts it was attached to. Template values can be hard-deleted or deactivated. |
| **BFAC-LIB-005** | *From Library* on an account copies the template and its **active** values; the factor code and name can be overridden. The 3-factor limit and per-account code uniqueness still apply. |
| **BFAC-LIB-006** | Template maintenance is on **Finance → Budget → Factor Library** (page open to System Admin, Finance Manager and Finance BP by primary role). The Account Catalog only consumes templates. |

### 4.3 Budget Plans

| FR ID | Requirement |
|---|---|
| **BPLAN-001** | A (cost center, fiscal year) can have several plan **versions**; at most one is current at any time (enforced by the database). |
| **BPLAN-002** | Plan statuses: `draft → submitted → in_review → approved`, with `returned`, `rejected` and `cancelled`. Only one non-final version may exist per (CC, year). |
| **BPLAN-003** | Creating a plan (users with *Edit Budget Plans*) requires a cost center and a fiscal year from Budget Config's available years; it is refused if a current plan already exists for that (CC, year). The new plan is seeded with a zero line for every active account × 12 months. |
| **BPLAN-004** | For decomposed accounts, amounts are entered in the **matrix editor** (2- or 3-dimensional grid) and the line amount is always the sum of its breakdowns; editing the line directly is refused. The editor offers Spread (Evenly / Proportional / Fill by row, column or all), a reference side panel (last year's same-month plan and *Copy from Last Year*), keyboard navigation and multi-cell paste from Excel. Saving replaces all breakdowns of the cell in one step. |
| **BPLAN-005** | Factor combinations may be a partial cross-product. |
| **BPLAN-006** | Quarter and year totals are calculated, not stored. |
| **BPLAN-007** | Plans are submitted to the shared approval engine (§4.3.3). |
| **BPLAN-008** | Non-decomposed cells save individually when the user leaves the cell. *(Changed: the previous 5-second autosave was not built.)* |
| **BPLAN-009** | *Copy FY n-1* copies the previous year's current approved plan of the same CC into this plan. Only a **draft** plan can receive the copy (a returned plan is refused although the button is shown — §11). |
| **BPLAN-010** | Only `draft` plans can be deleted; lines and breakdowns are deleted with them. Deleting a draft revision does not affect its parent. Other statuses must be returned, rejected or cancelled through the workflow. |
| **BPLAN-011** | Lines and breakdowns can only be edited while the plan is `draft` or `returned`. |
| **BPLAN-012** | The matrix editor's reference panel shows last year's same-month plan amount and breakdowns from the previous year's current approved plan. It also has *current month actual* and *YTD actual* fields, but these read the budget ledger's invoice/expense movements, which are never written, so they are always empty — **Defect** (§11). |
| **BPLAN-013** | All signed-in users can list and open any plan and export it; plan lists and plan details are not limited by budget visibility scope (§11). |

#### 4.3.1 Plan CSV import / export

| FR ID | Requirement |
|---|---|
| **BPIE-001** | *Export CSV* returns a wide CSV: L1 code/name, account code/name, decomposed flag, Jan–Dec, Q1–Q4, Year. File name `budget-plan-fy{year}-v{version}.csv`. Any signed-in user. |
| **BPIE-002** | *Import CSV* (users with *Edit Budget Plans*; plan `draft` or `returned`) matches rows by account code; extra or missing columns are tolerated; the file must be UTF-8. |
| **BPIE-003** | Blank month cell = unchanged; `0` = set to zero; other numbers overwrite. Thousands separators and `$`/`CAD` are accepted. Invalid numbers are reported without blocking other cells. |
| **BPIE-004** | Decomposed accounts are skipped (counted as skipped); their breakdowns must be entered in the matrix editor. |
| **BPIE-005** | The result reports lines updated, accounts touched, accounts skipped and errors; the page shows the counts and the first six problems. |

#### 4.3.2 Plan revisions

An approved plan cannot be edited; changes go through a new version.

| FR ID | Requirement |
|---|---|
| **BPREV-001** | *Revise Plan* is available only on the current approved version. It creates version n+1 as a draft (not current) linked to its parent, copies all lines and breakdowns, and adds zero lines for active accounts the parent did not have. A revision note can be entered. |
| **BPREV-002** | A second revision is refused while a non-final version exists for the same (CC, year); the editor then offers *Open Draft v{n}*. |
| **BPREV-003** | Revisions follow the same approval workflow as new plans. |
| **BPREV-004** | On final approval of a revision the approval engine makes it current and the parent not current, in one transaction. The parent stays `approved` for audit. |
| **BPREV-005** | If a revision is rejected or cancelled, the parent remains current. |
| **BPREV-006** | *History* shows the full version chain of the (CC, year), newest first. |
| **BPREV-007** | The plan list shows current versions by default; *Show superseded versions* includes older ones, marked *Superseded*. |
| **BPREV-008** | Every budget figure (balance, dashboard, Budget Actual, OA picker) uses only the current approved version; while a revision is in progress, the parent's amounts remain in force. |

#### 4.3.3 Plan approval and tasks

| FR ID | Requirement |
|---|---|
| **BPAPR-001** | Submit / Approve / Return / Reject are executed by the shared approval engine (document type `budget_plan`). Default workflow: Department Manager → Finance Manager; editable in Portal → Admin → workflow configuration (EPMS.md §3.3). |
| **BPAPR-002** | The Department Manager step is routed by the **submitting user's** department, not by the plan's cost-center department (§11). |
| **BPAPR-003** | Submit is allowed from `draft` or `returned`; approve/return/reject from `submitted` or `in_review`; cancel from `draft` or `returned`. |
| **BPAPR-004** | Approvers receive an *Approve Budget Plan* task; the requester receives a *Revise Budget Plan* task when returned. Tasks show the cost center as "CODE — Name" and a document number `BP-FY{year}`; the amount shown on the task is 0. |
| **BPAPR-005** | Opening a budget-plan task (EPMS Task Inbox, Portal Home, or notification email) opens the plan inside **Finance → Budget Plans**. |
| **BPAPR-006** | Approve / Return / Reject buttons appear only for a user holding the active approval task for that plan (System Admin holds every task). Submit, import, copy and line editing appear for users whose primary role is System Admin, Finance Manager, Finance BP or Department Manager while the plan is `draft`/`returned`. |

### 4.4 Actuals

#### 4.4.1 Source of actual spend

| FR ID | Requirement |
|---|---|
| **BACT-001** | **Actual Spent** for a CC × budget account × year is the NC actual supplied by the Finance module. Which NC lines map to which budget account and cost center is defined in Finance (FINANCE.md). |
| **BACT-002** | Posted vs unposted: reports offer *Include unposted vouchers* (§4.8, §4.9). The **balance used by the PR over-budget check always includes unposted vouchers** and the caller cannot change this. Discarded and NC-deleted vouchers never count. |
| **BACT-003** | **Committed** = ledger commits − releases − actualizations. Nothing in the PR/PO/PA chain writes these movements (decision 2026-09-20), so Committed is always 0 and a PR is not reserved against the budget. **Not implemented** (§11). |
| **BACT-004** | **Annual Budget** = sum of the current approved plan lines for the CC × account × year. With no approved plan the budget is 0, so any positive spend or request exceeds it. |
| **BACT-005** | **Available** = Annual Budget − Committed − Actual Spent; it can be negative. |
| **BACT-006** | If NC actuals cannot be obtained from Finance, the balance and the account summary fall back to the budget ledger (opening balances only) and log a warning. They never treat "unknown" as zero. Where no opening balance exists the fallback is 0, so the full budget appears available — keeping PRs flowing during an outage is a deliberate decision (2026-09-23). |
| **BACT-007** | Actuals are read live on every request; there is no batch refresh in the Budget module. NC data is as fresh as the Finance module's NC sync (FINANCE.md). |

#### 4.4.2 Opening balances

| FR ID | Requirement |
|---|---|
| **BOPEN-001** | Finance can import opening (pre-go-live) actuals per CC and fiscal year from a wide CSV (Account Code + Jan–Dec) on **Budget Config → Opening Balances**. Requires *Edit Opening Balances*; the section is shown to System Admin and Finance Manager (primary role). |
| **BOPEN-002** | Re-importing updates existing values; a blank cell leaves the value unchanged; an explicit number (including 0) sets it. |
| **BOPEN-003** | Imported opening balances are listed per CC/year, limited to the user's budget visibility scope. |
| **BOPEN-004** | Opening balances are stored in the budget ledger. Since actuals now come from NC, they are used only as the outage fallback (BACT-006) and in the legacy ledger reads; they no longer appear as actual spend on the Dashboard. |

#### 4.4.3 Figure lineage

| FR ID | Requirement |
|---|---|
| **BLIN-001** | The Budget service publishes a plain-language description of how its figures are made (which plan version counts, which ledger operations count, and how many ledger entries of each kind exist — counts only, no amounts). The AI Assistant uses it, together with Finance's equivalent for NC actuals, to explain a Budget Dashboard figure (see AI Assistant in EPMS.md §3.19). |

### 4.5 Service interfaces

| FR ID | Interface | Requirement |
|---|---|---|
| **BAPI-001** | Balance | Returns annual budget, committed, actual spent and available for one CC × account (by id or code) × year. Used by the PR Create/Edit budget widget and the PR over-budget check (EPMS.md §3.2.6) and by OA travel applications. **Deliberately not limited by budget scope**: limiting it would make another department's account read as unspent and silently pass the check. |
| **BAPI-002** | Account summary | Per active account for a year (one CC, or all CCs in the caller's scope): annual budget, committed, actual spent (NC **posted only**), available, utilisation %. Used by the OA expense-claim account picker and by the Budget Dashboard (budget and committed only). |
| **BAPI-003** | Monthly summary | Per active account: plan by month and year total for current approved plans in the caller's scope. Feeds the Dashboard grid. |
| **BAPI-004** | Scope | Returns whether the caller has company-wide access and the cost centers they may see (§4.7). |
| **BAPI-005** | Hierarchy | Active L1 → account tree (legacy shape for OA). |
| **BAPI-006** | Plan lines | Current approved plan lines for one year and month across all CCs; used by Finance's Account Balance budget column. Requires sign-in; not scope-limited. |
| **BAPI-007** | Ledger writes | Commit / release / actualize / book-expense endpoints exist and are idempotent per (source service, document type, document id, operation), but **have no callers**. |
| **BAPI-008** | All endpoints except health checks require a valid UniOps sign-in token. |

### 4.6 Budget Config

**Location:** Finance → Budget → Budget Config. The page opens for users whose primary role is System Admin, Finance Manager or AP Clerk; sections are shown as below.

| Section | Who sees it | Stored in |
|---|---|---|
| Cost Centers (create / edit / delete, filter by department) | System Admin, Finance Manager, AP Clerk | MDM |
| Opening Balances (§4.4.2) | System Admin, Finance Manager | Budget ledger |
| Available Fiscal Years | System Admin | EPMS company configuration |
| Budget Alert Thresholds | System Admin | EPMS company configuration |
| Over-Budget Approval Mode | System Admin | EPMS company configuration |
| Factor Decomposition (information only) and a link to the Account Catalog | System Admin | — |

Saving fiscal years, thresholds and mode requires the *Admin Panel* permission.

| FR ID | Requirement |
|---|---|
| **BCFG-001** | **Available fiscal years** (2000–2100, de-duplicated, ascending) feed the New Plan year list, the plan-list year filter and the Dashboard year selector. With none configured, plan creation is blocked with a message to ask an admin. |
| **BCFG-002** | Removing a year only hides it from the lists; existing plans for that year remain accessible. |
| **BCFG-003** | Fiscal year = calendar year everywhere; there is no fiscal start-month setting. |
| **BCFG-004** | **Alert thresholds**: warning (default 80%) and over-budget (default 100%), 1–200%. They colour the Budget Dashboard alerts only. The page text promises a daily email digest and email/Teams alerts at these thresholds; **no budget alert emails are sent — Not implemented** (§11). |
| **BCFG-005** | **Over-Budget Approval Mode** decides what happens when a PR exceeds its available balance (full behaviour in EPMS.md §3.2.6): |

| Mode | Label | Effect on an over-budget PR |
|---|---|---|
| `fm_gm_opm` (default) | Finance Manager + GM/OPM | Two pre-approval steps added before the normal chain. |
| `fm_only` | Finance Manager only | One pre-approval step added. |
| `allow` | Allow without extra approval *(new, 2026-10)* | No step added; the PR follows its normal chain. The over-budget justification is still required in the PR form and visible to every approver. |
| `hard_block` | Hard block | Submission refused. |

| FR ID | Requirement |
|---|---|
| **BCFG-006** | The mode is read by the approval engine each time it builds a PR's workflow, so a change applies to in-flight PRs from their next step. An unrecognised value behaves as *Finance Manager only*. |
| **BCFG-007** | The over-budget justification requirement is enforced by the PR form only, not by the server (EPMS.md OBG-007). |

### 4.7 Access and visibility

**Page and write access** (Access Control Matrix; a grant counts whether it sits on the user's primary or an additional role; System Admin always passes):

| Key | Default holders | Grants |
|---|---|---|
| *View Budget Dashboard* | per matrix | Budget Dashboard menu item |
| *View Budget Plans* | per matrix | Budget Plans menu item |
| *View Finance* | per matrix | Budget Actual, Account Catalog, Factor Library, Budget Config menu items |
| *Edit Budget Catalog* | System Admin, Finance Manager, Finance BP | Catalog, factor and Factor Library writes |
| *Edit Budget Plans* | System Admin, Finance Manager, Finance BP, Department Manager | Create, edit, import, copy, revise, delete plans |
| *Edit Opening Balances* | System Admin, Finance Manager, Finance BP | Opening-balance import |

Pages additionally show or hide controls by the user's **primary** role (Account Catalog edit controls: System Admin / Finance Manager / Finance BP; plan editing: those plus Department Manager; Budget Config and Factor Library page access as in §4.6 and §4.2.1). A user who holds the permission only through an additional role can call the service but does not see the controls (§11).

**Budget visibility scope** (who sees whose numbers):

| FR ID | Requirement |
|---|---|
| **BSCOPE-001** | *Full Access Budget View* → all cost centers. Default: GM, Finance Manager, Finance BP, AP Clerk, CFO, Auditor, System Admin. |
| **BSCOPE-002** | *Department-Related Budget View* → cost centers of the user's own department, every department they are the Director of in Approval Routing, and — if they hold the OPM role — every department whose GM/OPM step is routed to the OPM. Default: all other roles. OPM and Procurement Manager are department-scoped (decision 2026-08). |
| **BSCOPE-003** | Neither key, no department, or any error while resolving scope → nothing is shown (fail closed). |
| **BSCOPE-004** | Scope is applied on the server before aggregation to: the scope list, actuals, account summary, monthly summary and opening-balance list; and, by an identical rule in the Finance service, to the NC actuals and drill-downs behind the Dashboard. A request for a cost center outside the scope is narrowed to the scope, not refused. |
| **BSCOPE-005** | Not scope-limited: the balance check (BAPI-001, by design), plan lines for Finance (BAPI-006), plan lists/details/exports (BPLAN-013), and the Finance Budget Actual grid (§4.9). |

### 4.8 Budget Dashboard

Audience: department managers and anyone with *View Budget Dashboard*. Detailed behaviour is shared with EPMS.md §3.8.

| FR ID | Requirement |
|---|---|
| **BDASH-001** | Controls: fiscal year (from available fiscal years), cost center (within scope; *All Cost Centers* aggregates), scope chip (*Company-wide* / *My Department* / *My Departments*), *Include unposted vouchers* (ticked by default; one setting drives every NC figure on the page, the drill-downs and the export), *Manage Plans* link. A department-scoped user with no visible cost centers sees "No budget is visible for your account." |
| **BDASH-002** | Summary cards: Total Annual Budget (current approved plans), Total Committed (always 0 — BACT-003), Total Actual Spent (NC, labelled *NC posted* or *NC incl. unposted*), Total Available and % utilised. |
| **BDASH-003** | Alerts: accounts whose NC year-to-date actual ÷ annual budget reaches the over-budget threshold (red) or the warning threshold (amber); an account with spend but no budget counts as over. Clicking an alert scrolls to and highlights the account. |
| **BDASH-004** | Monthly grid: accounts grouped by L1 (collapsible, expand/collapse all), sorted by code; each cell shows plan and NC actual (red when actual exceeds plan); year column; sticky header and Total row. Payroll (CRM007), Depreciation (CRM004) and shut-down loss (CRM09912) accounts are excluded. The former middle line "actual (docs)" from the budget ledger was removed. |
| **BDASH-005** | Drill-down: an account expands to NC actual by vendor/customer and month; a vendor/month expands to its NC vouchers (date, voucher, account, summary, debit), with unposted vouchers marked. |
| **BDASH-006** | *Export XLSX*: one row per account, a Plan / NC column pair per month and for the year, two header rows, frozen panes; the subtitle states the NC basis (posted, or posted + unposted). An **Expand vendor** checkbox next to the button (off by default, export only — the grid is unchanged) adds *Vendor* and *Vendor Source* columns and follows every account row with one row per vendor: the same attribution as the on-screen vendor drill (line's own party, else the voucher's single party; *several parties* / *no vendor — internal entry* buckets), NC actual only with the Plan cells empty, grouped one Excel outline level under the account, with Category / Account Code / Account Name left empty on vendor rows so each account reads once; vendor rows are excluded from the Grand Total. File name gains `-by-vendor`. |
| **BDASH-007** | If no approved plan exists for the year in scope, the grid shows "No approved plan for FY … in this scope yet" with a link to create one. |

### 4.9 Budget Actual report (Finance)

A finance-facing report built on the same current approved plans and NC actuals; the computations live in the Finance service (FINANCE.md). Budget-side requirements:

| FR ID | Requirement |
|---|---|
| **BBVA-001** | Period selection by fiscal year and Month / Quarter / YTD / Custom month range; *Include unposted vouchers* ticked by default with a note that unposted figures can still change. |
| **BBVA-002** | Roll-up: whole company, then by expense centre or by department, down to cost center, with plan, actual, variance and variance % for the period and full-year plan, YTD actual, remaining and % consumed. Percentages with a zero budget show as blank, not 0%. Items tracked only at category level (payroll, depreciation, shut-down loss) appear as separate unallocated rows. |
| **BBVA-003** | Clicking any row opens its composition by budget account, whose total equals the clicked row; actuals drill to vouchers. |
| **BBVA-004** | *Expand by months* (multi-month windows only) adds Plan/Actual columns per month. *Export to Excel* exports the grouping on screen. |
| **BBVA-005** | Detail grid per cost center × income/expense item for the five expense categories, with category-level tie-out rows and an exceptions panel for unmapped NC lines. |
| **BBVA-006** | The report is company-wide for anyone with *View Finance*; it is not limited by budget visibility scope (§11). |

---

## 5. UI Summary

### 5.1 Account Catalog
L1 list on the left, accounts of the selected L1 on the right; an account expands to its factor panel with *From Library* and *Add Factor*. Header actions: New L1, Import CSV, Export CSV. Edit controls are hidden for users without a catalog-writer primary role. Import is shown to all catalog writers but succeeds only for System Admin (§11).

### 5.2 Budget Plans list
Filters: fiscal year (newest first; defaults to the current year), cost center, status, *Show superseded versions*. Columns: Cost Center, Fiscal Year, Version (v{n} with Current / Superseded badge), Status, Submitted, Approved, Last Updated, Actions. Default sort: CC code ↑, year ↓, version ↓. *New Plan* and a delete action on drafts are shown to plan writers.

### 5.3 Plan editor
Header: version badge, status, revision notes. Actions: History; *Open Draft v{n}* or *Revise Plan* (approved current plan); Export CSV; Import CSV, Copy FY n-1 and Submit (draft/returned, plan writers); Approve / Return / Reject (task holder). Grid: account column (sticky), 12 months, Q1–Q4, Year; L1 group rows; a lock icon on decomposed accounts opens the matrix editor; sticky Total row.

### 5.4 Budget Config, Factor Library, Dashboard, Budget Actual
As described in §4.6, §4.2.1, §4.8 and §4.9.

---

## 6. Non-Functional Requirements

| NFR | Requirement |
|---|---|
| **Consistency** | For decomposed accounts, plan line amount = sum of breakdowns, maintained in the same transaction. |
| **One current version** | At most one current version per (CC, year), enforced by the database. |
| **Single definition of actual** | The PR balance, the Dashboard and the Budget Actual report read NC actuals through the same Finance computation, so they agree for the same basis (posted vs incl. unposted). The OA expense picker uses the posted basis (§11). |
| **Failure behaviour** | Finance unreachable → fallback to ledger with a warning (BACT-006). Budget service unreachable from EPMS → the PR is treated as within budget (EPMS.md OBG-001b). Scope resolution failure → empty scope. |
| **Audit** | Plans record creator, submit/approve times and the full version chain; approval history is kept by the approval engine. |
| **Idempotency** | Ledger writes are de-duplicated per (source service, document type, document id, operation). |
| **Language** | UI text in English. |

---

## 7. Interfaces with Other Modules

| Module | Integration | Direction |
|---|---|---|
| EPMS (EPMS.md) | PR budget widget and over-budget check use the balance service; Budget Config values stored in EPMS configuration; Dashboard / Plans / Catalog pages are EPMS pages embedded in Finance; Task Inbox opens budget-plan tasks in Finance | both |
| Approval engine (EPMS.md §3.3) | `budget_plan` workflow; status updates; revision switch-over on approval; over-budget step injection for PRs | both |
| Finance (FINANCE.md) | NC actuals (per account/month, per vendor, vouchers), PR-check actual (always incl. unposted), Dashboard XLSX export, Budget Actual report; Finance reads current plan lines for Account Balance | both |
| OA (OA.md) | Expense-claim account picker uses the account summary; travel applications use the balance | OA → Budget |
| MDM | Cost centers and departments (edited from Budget Config) | Budget → MDM |
| Portal (PORTAL.md) | Access Control Matrix keys, Approval Routing (directors, GM/OPM), menu | Portal → Budget |
| AI Assistant (EPMS.md §3.19) | Figure lineage (BLIN-001) | Assistant → Budget |

---

## 8. Data Migration (historical)

At go-live (2026-05) the former per-cost-center budget tables were merged into the shared catalog, annual budgets were spread evenly over 12 months as approved FY2026 plans, and former committed/actual amounts were loaded into the ledger. On 2026-07-07 opening balances were imported (§4.4.2). No migration is pending.

---

## 9. Confirmed Decisions

| # | Decision | Date |
|---|---|---|
| 1 | One shared L1/L2 catalog for all cost centers | 2026-05-15 |
| 2 | Standalone Budget service | 2026-05-15 |
| 3 | Plan approval through the shared approval engine (`budget_plan`) | 2026-05-15 |
| 4 | Budget changes by versioned revisions; revision approval switches the current version | 2026-05-18 |
| 5 | Fiscal year = calendar year; configurable list of available fiscal years | 2026-05-19 |
| 6 | Budget pages embedded so the UniOps navigation stays in place; Budget belongs to the Finance module | 2026-05-19 / 2026-06 |
| 7 | Only draft plans can be deleted | 2026-05-20 |
| 8 | Plan CSV export for all, import for writers on draft/returned; decomposed accounts skipped | 2026-05-19 |
| 9 | Factor limit fixed at 3 (replaces the configurable limit of decision 2026-05-15) | 2026-05 |
| 10 | Budget visibility driven by Access Control Matrix keys; OPM and Procurement Manager department-scoped | 2026-08-18 |
| 11 | Actual spend = NC actuals; the purchase chain will not write to the budget ledger; Committed is not tracked | 2026-09-20 / 2026-09-22 |
| 12 | When NC actuals are unavailable, keep passing PRs (fallback, not error or zero) | 2026-09-23 |
| 13 | *Include unposted vouchers* on reports, ticked by default | 2026-10-08 / 2026-10-12 |
| 14 | PR over-budget check always includes unposted vouchers | 2026-10-12 |
| 15 | Over-budget mode *Allow without extra approval*, justification still required | 2026-10-15 |

---

## 10. Out of Scope for this Version

- Commitment / encumbrance tracking from PR, PO or PA.
- Cross-year budget transfers and carry-forward.
- Budget alert emails and digests.

---

## 11. Open Items

| ID | Item | Status |
|---|---|---|
| BUD-OI-01 | **Committed is always 0.** No PR/PO/PA writes commitments, so the same remaining balance can pass the over-budget check for any number of PRs until NC books them (see EPMS.md OI-K). The Dashboard "Total Committed — PA in-flight" card is always 0. | Not implemented |
| BUD-OI-02 | Budget alert emails (daily digest at the warning threshold; email + Teams at the over-budget threshold) are promised by the Budget Config page text but are not sent. Either build them or correct the page text. | Defect |
| BUD-OI-03 | Matrix editor reference panel: *current month actual* and *YTD actual* read unused ledger movements and are always empty; should read NC actuals. | Defect |
| BUD-OI-04 | OA expense-claim account picker shows NC **posted only**, while the PR check includes unposted vouchers; the two can show different remaining balances for the same account. | Open |
| BUD-OI-05 | Plan lists, plan details and plan exports are visible to every signed-in user regardless of budget visibility scope; plan writers (incl. Department Managers) can create and edit plans for any cost center. | Open |
| BUD-OI-06 | Budget-plan approval routes the Department Manager step by the submitter's department, not the plan's cost-center department. | Open |
| BUD-OI-07 | UI controls on Budget pages are gated by primary role, while the service accepts primary or additional roles; users with an additional Finance role do not see the controls (Budget Config, Factor Library, Catalog, plan editing). Opening Balances is hidden from Finance BP although the permission allows it. | Open |
| BUD-OI-08 | Catalog *Import CSV* is shown to all catalog writers but the service accepts it from System Admin only. | Defect |
| BUD-OI-09 | *Copy FY n-1* is offered on returned plans but the service accepts it only on drafts. | Defect |
| BUD-OI-10 | The Finance Budget Actual report and the NC drill endpoints it uses are company-wide for any *View Finance* user and not limited by budget visibility scope. | Open |
| BUD-OI-11 | EPMS role dashboards (Approver, Finance Manager, CFO) show "Over-Budget Depts / Accounts" and a budget overview that are always 0 / empty since budget moved to its own service. | Defect |
| BUD-OI-12 | Ledger write endpoints (commit / release / actualize / book-expense) have no callers and accept any signed-in user. Remove them, or restrict them to service callers, before anything relies on the ledger again. | Open |
| BUD-OI-13 | The balance check is not scope-limited by design; the NC actual it relies on is served by Finance to any signed-in user. A service-to-service credential is needed so the figure is available to the check without being readable by everyone. | Open |
| BUD-OI-14 | When NC actuals are unavailable, the fallback reports the full budget as available for any account without an opening balance (fail-open by decision 2026-09-23). Revisit if outages become frequent. | Open |
| BUD-OI-15 | Budget-plan tasks show an amount of 0 and the same document number `BP-FY{year}` for every cost center. | Open |
