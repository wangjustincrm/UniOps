# UniOps OA — Expense & Payment Management Module
**Version:** 2.0  
**Date:** 2026-06-02  
**Status:** S1–S5 Implemented · OA Fix Sprint applied (see `SPRINT-OA-FIX.md`)  
**Depends on:** EPMS PRD v2.12 · PRD-PORTAL v1.0

> **v2.0 architecture notes (2026-06-02 — reflects shipped code):**
> - **Budget logic lives in a standalone `budget-api` (:8007)**, not expense-api. It serves `/accounts`, `/hierarchy`, `/balance`, and cross-service writes (`/commit`, `/release`, `/actualize`, `/book-expense`). OA expense spend is booked on `status → paid` via `/book-expense`. (§8 and §12 budget references are served by budget-api.)
> - **Approval is delegated to `approval-api` (:8003)** via `approval_client.delegate_action(action_key, …)` reading `CompanyConfig.workflow_defs`. The earlier "internal state machine" note (§10.0) is superseded. PA/expense History reads the shared `approval_events` table.
> - **OCR is server-side**: `POST /api/v1/ocr/{mode}` (`invoice` | `receipt`) in expense-api, model `claude-haiku-4-5`. The Anthropic key is no longer shipped to the browser. The PA-DIR invoice flow and EXP/TRV "Scan Receipt" both call this endpoint.
> - **CFM is a first-class table**: `custom_form_definitions` (§10.1) managed via `/api/v1/expenses/custom-forms` (§12). Migrated out of `expense_policy_config.custom_forms` (JSONB), which is retained as a rollback safety net until verified in production.

**Decisions recorded:**
- OA-D resolved: `expense-api` is a new standalone service (`:8006`); access via UniOps Portal
- OA-G **revised (S2):** Budget booked to `actual_spent` on **payment** (`status → paid`), not on approval. EXP/MIL represent money already spent — booking on the `pay` action aligns with cash-basis accounting. `BudgetAccount.committed` is not used for expense claims.
- OA-H resolved: **PA-DIR** (Direct Payment, no PO) is created in OA. **PA-PO** (PO-linked Payment) creation remains in EPMS — clicking "PO-Linked PA" in OA's PA list redirects to the EPMS Create PA page. OA's PA list aggregates both PA-PO and PA-DIR as a unified read-only view.
- OA-I resolved: OA's PA list reads PA-PO records from `epms-api` and PA-DIR records from `expense-api`. EPMS does not read OA PA data.
- OA-J resolved: PA document number format unchanged from EPMS Phase 1 (`PA-YYYYMMDD-NNNN`); no migration needed
- OA-K resolved: **Invoice-first rule** — all non-prepayment payments require an Invoice record as the starting point
- OA-L resolved: Cross-table invoice dedup — `(vendor_id, invoice_number)` unique across EPMS `invoices` and OA `expense_invoices`; hard block HTTP 409
- OA-M resolved: Shared OCR service — Claude API (`claude-haiku-4-5`), `receipt` mode for EXP/TRV, `invoice` mode for PA-DIR

---

## Implementation Status

| Sprint | Scope | Status |
|--------|-------|--------|
| **S1** | expense-api scaffold, PA migration, Portal launcher | ✅ Complete |
| **S2** | EXP + MIL expense claims | ✅ Complete |
| **S3** | PA-DIR + Invoice OCR | ✅ Complete |
| **S4** | TRV + Admin config + Custom Forms | ✅ Complete |
| **S5** | OA Task Inbox (`/tasks`) | ✅ Complete |
| **FIX** | OA Fix Sprint: TRV/CFM entry points, server-side OCR, receipt scan, attachment gate, CFM table migration | ✅ Complete (`SPRINT-OA-FIX.md`) |

### S2 Implemented — Actual vs Planned

| Item | Planned | Actual |
|------|---------|--------|
| Budget booking | On approval | **On payment** (more accurate for reimbursements) |
| Approval workflow | Via approval-api delegation | **Internal state machine** in expense-api (simpler for Phase 2; can delegate to approval-api in Phase 3) |
| Over-budget handling | Finance Manager conditional step | Finance Manager step triggered by `is_over_budget` flag on claim |
| Budget account picker | Via EPMS API proxy | **Via `GET /api/v1/budget/accounts`** served by expense-api reading shared DB directly |
| Attachment upload | Via file-api | Deferred to S3 (schema is ready, UI stub present) |

---

## 1. Product Overview

The **OA Module** (Expense & Payment Management) extends UniOps with two categories of outbound-payment workflows:

1. **Employee Expense Reimbursements** — employees claim back money they already spent (receipts required)
2. **Vendor Payment Applications** — finance initiates or approves payments to vendors, either linked to a PO/Invoice chain from EPMS or as a standalone direct payment

All forms share the same approval engine (`:8003`), budget integration, and file server (`:8005`) built for EPMS Phase 1. All payments are executed via direct bank transfer.

### 1.1 Form Types

| Type | Code | Category | Description |
|------|------|----------|-------------|
| General Expense | `EXP` | Reimbursement | Receipt-based employee reimbursement |
| Travel Expense | `TRV` | Reimbursement | Transportation, accommodation, meals |
| Mileage Claim | `MIL` | Reimbursement | Personal vehicle use, per-km rate |
| Custom Form | `CFM-{code}` | Reimbursement | Admin-defined forms |
| PO Payment Application | `PA-PO` | Vendor Payment | Vendor payment linked to EPMS PO + Invoice |
| Direct Payment Application | `PA-DIR` | Vendor Payment | Vendor payment with no PO dependency |

### 1.2 Target Users

Same role matrix as EPMS. Additional considerations:

| Role | OA Permissions |
|------|---------------|
| All employees | Submit any expense form for own department |
| Department Manager | Submit + approve expense claims from own department |
| Finance BP | Approve all expense claims (second-level) |
| Finance Manager | Over-budget approval; configure expense policy |
| System Admin | Full config: rates, account mappings, custom forms, policy limits |

### 1.3 Document Visibility — OA Module

**Rule:** All roles in OA can only see forms/PAs that are **associated with their own workflow process**. This is stricter than EPMS.

**Expense Claims (EXP / MIL / TRV / CFM):**

| What the user sees | Condition |
|--------------------|-----------|
| Own submissions | `employee_id = current_user.id` (any status) |
| Pending approvals | `status IN ('submitted', 'in_review') AND approval_step_idx` matches user's role step |
| Approved (for payment) | `status = 'approved'` — visible to `finance_bp`, `finance_manager`, `ap_clerk` |
| All claims | `system_admin` only |

**Payment Applications (PA / PA-DIR):**

| What the user sees | Condition |
|--------------------|-----------|
| Own PAs | `created_by = current_user.id` (any status) |
| All PAs | `system_admin` only |

**Enforcement:** Applied server-side in `GET /api/v1/expenses` and `GET /api/v1/pa`. Clients cannot bypass the scope. The `my_claims` query parameter is redundant for non-admin roles (their scope is always restricted); it only affects `system_admin`.

**FR IDs — OA Visibility:**

| FR ID | Requirement |
|-------|-------------|
| **OA-VIS-001** | `GET /api/v1/expenses` must enforce role-based visibility. Non-admin users see only their own submissions plus claims in their current approval queue. |
| **OA-VIS-002** | `GET /api/v1/pa` must return only PAs created by the current user, except for `system_admin` who sees all. |
| **OA-VIS-003** | Visibility filters are applied server-side using the JWT `sub` claim as `employee_id` / `created_by`. No client parameter can expand a user's view beyond their role scope. |

---

## 2. General Expense Form (EXP)

**Reference:** Attached CRM expense reimbursement form image.

### 2.1 Form Header

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Employee Name | Auto-filled from auth session | Yes | Read-only |
| Employee ID | Auto-filled from user record | Yes | Read-only |
| Department | Auto-filled from user record | Yes | Read-only |
| Submission Date | Date picker | Yes | Defaults to today |
| Currency | Select (CAD / USD) | Yes | Defaults to CAD |
| Project | Select from Projects master data | No | Optional project allocation |
| Notes / Purpose | Textarea | No | General note for the batch |

### 2.2 Line Items

Up to 20 line items per form. Each line item:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Receipt No | Auto-incremented (1…20) | — | Read-only, for matching physical receipts |
| Date | Date picker | Yes | Date of expense |
| Description | Text (100 chars) | Yes | What was purchased |
| Expense Code (Budget Account) | Searchable select | Yes | Shows L1 + L2 code + name; see §2.3 |
| Cost Centre | Auto-filled from Budget Account | Yes | Can override if account spans multiple CCs |
| Total Amount A (CAD) | Decimal input | Yes | Invoice total including tax |
| Tax B (GST/HST) | Decimal input | No | Tax portion; defaults to calculated 13% of A |
| Net Amount C (= A − B) | Calculated | — | Read-only; C = A − B |

**Tax calculation default:**
- HST rate is configurable per-org in Admin Panel (default 13% Ontario)
- When user enters Total Amount A, Tax B auto-calculates as `round(A × rate / (1 + rate), 2)`
- User can override Tax B manually (for receipts showing different tax amounts)
- Net C always recalculates as A − B

**Sub-total row:** Sum of all line items for A, B, C separately.  
**TOTAL row:** Same as Sub-total (shown for visual match to paper form).

### 2.3 Budget Account Selection (Live Balance)

When a user opens the Budget Account picker on a line item, the system shows:

```
CRM00301 — General IT Expense
Annual Budget: CA$45,000   Committed: CA$12,300   Actual: CA$8,200
Available: CA$24,500  ████████████░░░░ 46% used
```

Rules:
- Only Budget Accounts (L2) scoped to the user's department / cost centre are shown by default
- Finance Manager and System Admin can select any account
- If the selected account has < CA$0 available, a warning is shown (does not block submission, triggers Finance Manager approval — same over-budget rule as PR)

### 2.4 Attachments

- Upload receipts, invoices, photos (PDF, JPG, PNG, HEIC, WEBP, XLSX, CSV, DOC)
- Each attachment can be linked to one or more receipt numbers
- File size limit: 25 MB per file
- Stored via **file-api (`:8005`)** — same service as EPMS PR/GR/PA attachments
- DB stores only metadata (`file_id` = file-api UUID) — no binary data in the database (PRD §3.4.1 FS-006)
- Upload endpoint: `POST /api/v1/expenses/{id}/attachments` (multipart form)
- Download endpoint: `GET /api/v1/expenses/{id}/attachments/{att_id}/file` (proxied from file-api)
- Attachments can only be added/deleted when claim is in `draft` or `returned` status

### 2.5 AI Receipt Recognition (OCR)

When a user uploads an attachment on a line item, the system optionally auto-fills fields:

- Trigger: "Scan Receipt" button per attachment
- Calls OCR service (see §8.3); returns structured extraction:
  - `date`, `vendor_name`, `description`, `total_amount`, `tax_amount`, `currency`
- Pre-fills the line item fields (user reviews and confirms before submitting)
- OCR confidence score shown — low-confidence fields are highlighted for manual review
- OCR is optional; user can always fill manually

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| EXP-001 | Form must display Employee Name, Employee ID, Department auto-filled from session. |
| EXP-002 | Up to 20 line items per form. |
| EXP-003 | Each line item must have: Date, Description, Budget Account (L2), Cost Centre, Total Amount A, Tax B, Net Amount C. |
| EXP-004 | Net Amount C must be calculated automatically as A − B and be read-only. |
| EXP-005 | Budget Account picker must show live Available balance for the selected account at time of selection. |
| EXP-006 | HST/GST rate must be configurable in Admin Panel. Default 13%. |
| EXP-007 | Attachments are required before submission (at least one file per form). |
| EXP-008 | AI receipt scan is optional; pre-fills fields with confirmation step. |
| EXP-009 | Over-budget line items must trigger Finance Manager approval step. |

---

## 3. Travel Expense Form (TRV)

**Reference:** North American standard travel expense report (CRA-compliant).

### 3.1 Form Header

Same as EXP header fields (Employee Name, ID, Department, Date, Currency, Project) plus:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Travel Period From | Date | Yes | First day of travel |
| Travel Period To | Date | Yes | Last day of travel |
| Travel Purpose | Text | Yes | Business reason |
| Destination | Text | Yes | City/location |

### 3.2 Expense Categories

Fixed categories with configurable Budget Account mappings and per-day/per-trip spending limits:

| Category | Description | Default Budget Account Mapping | CRA Deductibility |
|----------|-------------|-------------------------------|-------------------|
| Air / Rail | Flights, train tickets | Configurable (e.g., CRM-TRAVEL-AIR) | 100% |
| Ground Transport | Taxi, rideshare, rental car, parking | Configurable | 100% |
| Accommodation | Hotel, lodging | Configurable | 100% |
| Meals — Breakfast | Per CRA meal rate | Configurable | 50% |
| Meals — Lunch | Per CRA meal rate | Configurable | 50% |
| Meals — Dinner | Per CRA meal rate | Configurable | 50% |
| Incidentals | Tips, gratuities, misc | Configurable | 50% |
| Other | Anything not above | Configurable | User-specified |

**Per-day meal limits (configurable, CRA 2025 default):**

| Meal | CRA Rate (CAD) |
|------|---------------|
| Breakfast | $23.00 |
| Lunch | $23.00 |
| Dinner | $46.00 |
| Daily Total | $92.00 |

When a meal expense exceeds the configured limit, the system:
- Shows a warning on that line
- Allows submission but flags for Finance Manager review
- Generates a separate approval event for the over-limit amount

### 3.3 Line Items

One row per expense, grouped by category. Fields per row:

| Field | Type | Required |
|-------|------|----------|
| Date | Date | Yes |
| Category | Select (see §3.2) | Yes |
| Description / Vendor | Text | Yes |
| Receipt No | Auto-number | — |
| Amount (local currency) | Decimal | Yes |
| Exchange Rate | Decimal | If currency ≠ CAD |
| Amount (CAD) | Calculated | — |
| Budget Account | Auto-filled from category mapping; overridable | Yes |
| Receipt Attached | File upload indicator | Yes |

### 3.4 Summary Panel

Auto-calculated totals per category + grand total. Compared against applicable policy limits.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| TRV-001 | Form must include Travel Period (From/To), Purpose, and Destination. |
| TRV-002 | Eight fixed categories as defined in §3.2. |
| TRV-003 | Budget Account per category must be configurable in Admin Panel. |
| TRV-004 | Per-meal and per-day limits must be configurable; CRA defaults pre-loaded. |
| TRV-005 | Line items exceeding policy limits must be flagged but not blocked. Finance Manager approval required for over-limit items. |
| TRV-006 | Multi-currency line items must show local amount + exchange rate + CAD equivalent. |
| TRV-007 | Summary panel must show totals per category and grand total in CAD. |
| TRV-008 | At least one receipt attachment is required per claim before submission. |

---

## 4. Mileage Claim Form (MIL)

For personal vehicle used on company business.

### 4.1 Form Header

Same as EXP header plus:

| Field | Type | Required |
|-------|------|----------|
| Vehicle | Text (description) | Yes | e.g., "2022 Honda Civic" |
| Vehicle Owned By | Select: Self / Company | Yes | Mileage claim = Self only |

### 4.2 Trip Log

Each row = one trip:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Date | Date | Yes | |
| From | Text | Yes | Starting location |
| To | Text | Yes | Destination |
| Purpose | Text | Yes | Business reason |
| Round Trip | Checkbox | No | Doubles km automatically |
| Distance (km) | Decimal | Yes | Entered by employee |
| Rate ($/km) | Auto-filled from config | — | Read-only; configurable in Admin Panel |
| Amount (CAD) | Calculated: km × rate | — | Read-only |
| Budget Account | Auto-filled from config | Yes | Single configurable account for all mileage |

**Note on distance:** The system does not auto-calculate route distance. Employee enters the km. Finance Manager may request supporting evidence (e.g., Google Maps screenshot) via the comment field.

### 4.3 Summary

Total km × rate = Total Amount (CAD). Budget Account balance shown.

### 4.4 Configuration

In Admin Panel → Expense Config → Mileage:

| Setting | Default | Notes |
|---------|---------|-------|
| Rate per km (CAD) | $0.72/km | CRA 2025 prescribed rate for first 5,000 km |
| Budget Account | (required) | Single L2 account for all mileage claims |
| Max km per claim | 2,000 km | Warning (not block) if exceeded |

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| MIL-001 | Trip log with Date, From, To, Purpose, Round Trip toggle, Distance (km). |
| MIL-002 | Rate per km must be configurable; default $0.72 CAD (CRA 2025). |
| MIL-003 | Amount per trip must calculate automatically as km × rate. |
| MIL-004 | Budget Account for mileage is a single configurable account applied to all trips. |
| MIL-005 | Max km per claim is configurable; triggers warning (not block) if exceeded. |

---

## 5. Custom Form Builder (CFM)

Allows System Admin to define additional expense form types without code changes.

### 5.1 Form Definition

In Admin Panel → OA Forms → New Form:

| Setting | Description |
|---------|-------------|
| Form Name | Display name (e.g., "Training Expense") |
| Form Code | Short code, auto-uppercased (e.g., `TRN`) |
| Description | Shown to employees as help text |
| Approval Workflow | Select from existing workflow configs (same engine as PR/PO) |
| Default Currency | CAD / USD |
| Active | Toggle |

### 5.2 Field Types Available

| Type | Description |
|------|-------------|
| `text` | Single-line text |
| `textarea` | Multi-line text |
| `number` | Decimal number |
| `date` | Date picker |
| `select` | Dropdown (admin defines options) |
| `budget_account` | Budget Account picker (shows live balance) |
| `cost_centre` | Cost Centre picker |
| `attachment` | File upload |
| `calculated` | Formula field (references other field codes) |

### 5.3 Constraints

- All custom forms inherit the common header (Employee Name, Department, Submission Date, Currency)
- All custom forms route through the standard approval engine
- Budget Account and Cost Centre fields automatically trigger over-budget logic if selected
- Custom forms do NOT support AI receipt scanning (EXP-only feature in Phase 2)

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| CFM-001 | Admin can create a new form type with a unique code, name, and field list. |
| CFM-002 | Field types: text, textarea, number, date, select, budget_account, cost_centre, attachment, calculated. |
| CFM-003 | All custom forms share the standard approval workflow engine. |
| CFM-004 | Custom forms with Budget Account fields automatically trigger over-budget rules. |
| CFM-005 | Custom forms can be activated/deactivated without deleting them. |

---

## 6. Common Workflow Requirements

### 6.1 Approval Flow (all form types)

Uses the existing Approval Engine (`:8003`). Default workflow for all expense types:

```
Submitter
    ↓  submit
Department Manager
    ↓  approve
Finance BP
    ↓  approve
[Finance Manager — only if over-budget or over-policy-limit]
    ↓  approve
APPROVED → Pending Bank Transfer
```

- Workflow steps are configurable per form type in Admin Panel → OA Config → Approval Workflows
- Same auto-skip logic as PR/PO (if dept_manager IS the Finance BP, step auto-skips)
- Finance Manager step is conditional — only triggered if any line item is over-budget or over policy limit

### 6.2 Expense Claim Status Lifecycle

```
draft → submitted → in_review → approved → paid
                ↓            ↓
             returned      rejected
```

| Status | Description |
|--------|-------------|
| `draft` | Created, not yet submitted |
| `submitted` | Waiting for Dept Manager |
| `in_review` | Dept Manager approved, waiting for Finance BP |
| `approved` | All approvals done; pending payment |
| `paid` | Finance has recorded bank transfer |
| `returned` | Sent back to submitter for revision |
| `rejected` | Declined; no further action |

### 6.3 Document Number Format

| Type | Format | Example |
|------|--------|---------|
| General Expense | `EXP-YYYYMMDD-NNNN` | `EXP-20260501-0001` |
| Travel Expense | `TRV-YYYYMMDD-NNNN` | `TRV-20260501-0001` |
| Mileage Claim | `MIL-YYYYMMDD-NNNN` | `MIL-20260501-0001` |
| Custom Form | `{CODE}-YYYYMMDD-NNNN` | `TRN-20260501-0001` |

### 6.4 Mark as Paid

After approval, Finance records the bank transfer:

| Field | Required | Notes |
|-------|----------|-------|
| Payment Date | Yes | |
| Bank Reference / EFT No | Yes | |
| Amount Paid (CAD) | Yes | Must match approved amount |
| Notes | No | |

This creates a `ExpensePaymentRecord` and moves status to `paid`.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| WF-001 | All expense forms use the Approval Engine with configurable per-type workflow. |
| WF-002 | Finance Manager approval step is conditional — triggered only on over-budget or over-limit items. |
| WF-003 | Full lifecycle: draft → submitted → in_review → approved → paid (+ returned / rejected). |
| WF-004 | Finance must record a payment date, bank reference, and amount before status moves to `paid`. |
| WF-005 | All approval events are recorded in the audit trail (same as PR/PO/PA). |

---

## 7. List Pages & Navigation

### 7.1 Expense Inbox (`/expenses`)

All expense forms (EXP, TRV, MIL, CFM) appear in a unified list under `/expenses`:

- Filter by: Type / Status
- **Server-side pagination** via `GET /api/v1/expenses?page=&page_size=` (default 20, max 100)
- Quick view: claim number, type badge, employee name, net amount, status, over-budget flag, date
- "New Claim" dropdown: selects form type (EXP / MIL — TRV and CFM in S4)
- Pagination UI: shared `Pagination` component (First / Prev / pages / Next / Last + per-page selector 10/20/50/100 + "X–Y of Z" count)
- Tab or filter change resets to page 1

### 7.3 Payment Applications List (`/pa`)

All OA payment applications (PA-PO and PA-DIR) appear in a unified list:

- Filter by: Status (All / Submitted / In Review / Approved / Paid)
- **Server-side pagination** via `GET /api/v1/pa?page=&page_size=` (expense-api; default 20, max 100)
- Quick view: PA number, type badge (PO / Direct), vendor, payment amount, status, date
- Actions: "Direct PA" button → `/pa/new/direct` (stays in OA); "PO-Linked PA" button → redirects to EPMS Create PA page (`{EPMS_URL}/pa/new`) — PA-PO creation is performed in EPMS, not OA.
- Pagination UI: same shared component as Expense Inbox
- Tab change resets to page 1

### 7.4 Invoice List (`/invoices`)

Unified view of EPMS + OA invoices:

- Filter by: Source tab (All Invoices / EPMS PO-matched / OA Direct payment) + search
- **Server-side pagination** via `GET /api/v1/invoices/all?page=&page_size=` (expense-api; default 20, max 100)
- Stat cards above table show total, EPMS count, and OA count for the current page slice
- Pagination UI: same shared component
- Source tab or search change resets to page 1

### 7.5 OA List Pagination Standard

All OA list pages use the same server-side pagination model as EPMS (see PRD.md §10). The `Pagination` component is shared between EPMS and OA (`oa/src/components/ui/Pagination.tsx`, copied from `epms/src/components/ui/Pagination.tsx`).

| OA List page | Endpoint | Default | Max |
|---|---|---:|---:|
| Expense Claims | `GET /api/v1/expenses` | 20 | 100 |
| Payment Applications | `GET /api/v1/pa` | 20 | 100 |
| Invoices (unified) | `GET /api/v1/invoices/all` | 20 | 100 |

**FR IDs — OA Pagination:**

| FR ID | Requirement |
|---|---|
| **OA-PGN-001** | All OA list pages must use server-side pagination; loading the full dataset client-side is not permitted. |
| **OA-PGN-002** | Every paginated OA endpoint must return `{ items: [...], total: int }`. |
| **OA-PGN-003** | Changing any filter, status tab, or search resets `page` to 1. |
| **OA-PGN-004** | The pagination UI must show "X–Y of Z" using the server-returned `total`. |

### 7.2 OA Task Inbox (`/tasks`)

The OA module has its own **Task Inbox** — a unified view of all pending OA items the current user is responsible for. It is the default landing page after login (root `/` redirects to `/tasks`).

#### 7.2.1 Data Source

`GET /api/v1/tasks` (expense-api) aggregates items from two tables in real-time:

| Source table | Included when |
|---|---|
| `expense_claims` | status in `submitted / in_review / returned / approved` AND matches user's role scope |
| `payment_applications` | status in `submitted / in_review / returned / approved` AND matches user's role scope |

The endpoint returns at most 200 items per table; results are sorted by `created_at DESC`.

#### 7.2.2 Task Types

Each returned item has a `task_type` that drives the UI category, colour, and label:

| `task_type` | Trigger condition | Colour |
|---|---|---|
| `approve_expense` | Expense claim at step matching the current user's role | Teal |
| `approve_pa` | PA at step matching the current user's role | Teal |
| `revise_expense` | Own expense claim with `status = returned` | Amber |
| `revise_pa` | Own PA with `status = returned` | Amber |
| `pay_expense` | Expense claim with `status = approved`; shown to `finance_bp`, `finance_manager`, `ap_clerk`, `system_admin` | Indigo |
| `pay_pa` | PA with `status = approved`; same finance/AP roles | Indigo |
| `submitted_expense` | Own expense claim in `submitted` or `in_review` (informational — no action required) | Grey |
| `submitted_pa` | Own PA in `submitted` or `in_review` (informational) | Grey |

**Step → role mapping** used to determine whether an approver sees an item (mirrors workflow defaults; may drift if workflows are customised):

| Step index | Expense claim roles | PA roles |
|---|---|---|
| 0 | `dept_manager` | `dept_manager`, `finance_bp` |
| 1 | `finance_bp`, `finance_manager` | `gm_or_opm`, `finance_manager` |
| 2 | `finance_manager` | `finance_bp` |
| 3 | — | `finance_manager` |

`system_admin` always sees all items regardless of step index.

#### 7.2.3 Page Layout

```
┌─────────────────────────────────────────────────────────┐
│  Task Inbox                                             │
│  All pending OA actions                                 │
├────────────────┬──────────────┬─────────────────────────┤
│  Approve  [N]  │  Revise  [N] │  Pay  [N]               │
│  (teal)        │  (amber)     │  (indigo)                │
├─────────────────────────────────────────────────────────┤
│  [All]  [Needs My Action ①]  [My Submissions]           │
│                              [Type filter ▼]            │
├─────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────┐   │
│  │  [Teal] Approve Expense · Expense                │   │
│  │  EXP-20260507-0003                               │   │
│  │  John Smith · CA$1,240.00  [submitted]  May 7   │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  [Amber] Revise Payment · Direct Payment         │   │
│  │  PA-20260507-0001                                │   │
│  │  Acme Ltd · CA$5,500.00  [returned]  May 6      │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Stats row (top):** Three cards showing counts of items needing active decision:
- **Approve** — `approve_expense` + `approve_pa`
- **Revise** — `revise_expense` + `revise_pa`
- **Pay** — `pay_expense` + `pay_pa`

**Filter tabs:**

| Tab | Shows |
|---|---|
| All | All items returned by the API |
| Needs My Action | `task_type` in `{approve_*, revise_*, pay_*}` |
| My Submissions | `is_own = true` AND `task_type` in `{submitted_expense, submitted_pa}` |

**Type dropdown:** filter by `exp / mil / trv / cfm / pa / pa_dir`.

**Task card:** Clicking anywhere on the card navigates to the document detail page:
- Expense tasks → `/expenses/:doc_id`
- PA tasks → `/pa/:doc_id`

#### 7.2.4 Sidebar Badge

The "Task Inbox" sidebar nav item displays a red badge with the count of items needing active action (`approve_*` + `revise_*` + `pay_*`). The badge is visible in both expanded and collapsed sidebar states. It refreshes every 60 seconds via `refetchInterval`.

#### 7.2.5 API Response Shape

```ts
interface OaTaskItem {
  id: string               // "exp-<uuid>" or "pa-<uuid>"
  task_type: string        // see §7.2.2
  doc_type: string         // exp | mil | trv | cfm | pa | pa_dir
  doc_id: string
  doc_number: string       // EXP-... or PA-...
  title: string            // purpose/notes or vendor name
  submitter_name: string   // employee_name (expense) or vendor_name (PA)
  amount: number
  currency: string
  status: string
  submitted_at: string | null
  created_at: string
  is_own: boolean          // true if current user created the document
}

interface OaTaskListResponse {
  items: OaTaskItem[]
  total: number
}
```

**FR IDs — OA Task Inbox:**

| FR ID | Requirement |
|---|---|
| **TASK-001** | `GET /api/v1/tasks` must return all OA items (expense claims + PAs) relevant to the current user based on their role and the document's approval step. |
| **TASK-002** | Items must be classified into the eight `task_type` values defined in §7.2.2. Each item must appear at most once (deduplication by document ID). |
| **TASK-003** | The `approve_*` task types must only be assigned when the document is NOT owned by the current user (`is_own = false`), preventing users from seeing their own submissions as pending approvals. |
| **TASK-004** | `pay_*` task types must only be visible to `finance_bp`, `finance_manager`, `ap_clerk`, and `system_admin`. |
| **TASK-005** | The Task Inbox page must display stat cards for Approve, Revise, and Pay counts separately. |
| **TASK-006** | The "Needs My Action" tab must show only `approve_*`, `revise_*`, and `pay_*` task types. |
| **TASK-007** | Task cards must navigate to the corresponding OA document detail page (`/expenses/:id` or `/pa/:id`) on click. |
| **TASK-008** | The sidebar "Task Inbox" nav item must display a red badge with the count of actionable items; badge updates every 60 seconds. |
| **TASK-009** | `/` (OA root) must redirect to `/tasks`. |
| **TASK-010** | Step → role mapping in the backend must reflect the configured workflow defaults; a warning must be logged if `approval_step_idx` exceeds the known mapping range. |

---

## 8. Budget Integration

### 8.1 Budget Commitment on Approval

When status moves to `approved`:
- Each line item's Net Amount C is committed **and immediately actualized** against its Budget Account
- `BudgetAccount.committed` is NOT used as an intermediate state for expenses (unlike PRs which commit on approval then actualize on PA)
- `BudgetAccount.actual_spent += line.net_amount_c`
- Available balance decreases at approval

**Rationale:** Expense claims represent money already spent (receipts exist). The committed/actual distinction is meaningful for purchase orders (future spend) but not for reimbursements (past spend). Booking directly to actual_spent is the correct accounting treatment.

### 8.2 No Commitment on Submission

Budget balance is **not affected** when a claim is submitted or in-review. The approver sees the full available balance at approval time. This allows Finance to make informed approval decisions without pre-emptively locking budget.

### 8.3 Release on Rejection/Cancellation

When status moves to `rejected` or submitter cancels a `returned` claim:
- No budget adjustment needed (nothing was committed)

---

## 9. Configuration (Admin Panel additions)

### 9.1 Admin Panel → Expense Config

New section in Admin Panel with sub-sections:

| Sub-section | Configures |
|-------------|-----------|
| General Policy | HST/GST rate, default currency, max attachments |
| Travel Policy | Per-meal limits (breakfast/lunch/dinner/incidental), hotel rate cap |
| Mileage Policy | Rate per km, max km per claim, Budget Account |
| Category → Budget Account Mapping | TRV category to Budget Account assignment |
| Custom Forms | Create / edit / toggle custom form types |

### 9.2 Approval Workflows (Portal Admin Panel)

OA form types each have their own action key bound to a configurable workflow in **Portal Admin Panel → Approval Workflows**. Configured via the same interface as EPMS procurement workflows (see PRD.md §3.3.1).

| Action Key | Applies to | Default steps |
|------------|-----------|--------------|
| `pa` | PO-linked Payment Application | `dept_manager → gm_or_opm → finance_bp → finance_manager` |
| `pa_dir` | Direct Payment Application | `finance_bp → finance_manager` |
| `exp` | General Expense (EXP) | `dept_manager → finance_bp` |
| `trv` | Travel Expense (TRV) | `dept_manager → finance_bp` |
| `mil` | Mileage Claim (MIL) | `dept_manager → finance_bp` |
| `cfm` | Custom Form (default) | `dept_manager` |
| `cfm_<code>` | Named custom form override | (inherits `cfm` unless overridden) |

Each custom form (`CFM`) may reference the default `cfm` workflow or define its own `cfm_<code>` override. All workflows are fully configurable — no steps are hardcoded.

---

## 10. Data Model

### 10.0 S2 Implementation Notes

The S2 implementation diverges from the original spec in three places:

| Change | Original spec | Actual implementation |
|--------|--------------|----------------------|
| MIL trips storage | Unified in `expense_line_items` with nullable km/rate fields | Separate `expense_trip_items` table (cleaner schema) |
| Approval audit trail | Reuse EPMS `approval_events` | New `expense_approval_events` table (expense-api owns its own workflow) |
| Employee identity | `submitter_id FK users.id` only | `employee_id UUID` + denormalized `employee_name VARCHAR` (avoids cross-service joins) |
| Budget booking | On `approved` | On `paid` (see OA-G revised) |
| `is_over_budget` flag | Not in original spec | Added to `expense_claims`; drives Finance Manager conditional step |

### 10.1 New Tables

#### `expense_claims` *(S2 — implemented)*
```
id                UUID PK
claim_number      VARCHAR(30) UNIQUE  -- EXP-20260501-0001
claim_type        VARCHAR(10)         -- 'EXP' | 'TRV' | 'MIL' | custom code
status            VARCHAR(20)         -- draft | submitted | in_review | approved | paid | returned | rejected
submitter_id      UUID FK users.id
department_id     UUID FK departments.id
project_id        UUID FK projects.id NULLABLE
currency          VARCHAR(3)          -- CAD | USD
total_amount      NUMERIC(12,2)       -- Total Amount A (CAD equivalent)
total_tax         NUMERIC(12,2)       -- Total Tax B
total_net         NUMERIC(12,2)       -- Total Net C
approval_step_idx INT DEFAULT 0
submitted_at      TIMESTAMPTZ NULLABLE
notes             TEXT NULLABLE
travel_from       DATE NULLABLE       -- TRV only
travel_to         DATE NULLABLE       -- TRV only
travel_purpose    TEXT NULLABLE       -- TRV only
travel_dest       TEXT NULLABLE       -- TRV only
vehicle_desc      VARCHAR(100) NULLABLE  -- MIL only
form_code         VARCHAR(10)         -- for CFM types
created_at        TIMESTAMPTZ
updated_at        TIMESTAMPTZ
```

#### `expense_line_items`
```
id                UUID PK
claim_id          UUID FK expense_claims.id
line_number       INT              -- 1..20
line_date         DATE
description       VARCHAR(200)
category          VARCHAR(30) NULLABLE  -- TRV category
budget_account_id UUID FK budget_accounts.id
cost_centre_id    UUID FK cost_centres.id
total_amount      NUMERIC(12,2)    -- A (with tax)
tax_amount        NUMERIC(12,2)    -- B
net_amount        NUMERIC(12,2)    -- C = A - B
exchange_rate     NUMERIC(8,4) DEFAULT 1.0
distance_km       NUMERIC(8,2) NULLABLE  -- MIL only
rate_per_km       NUMERIC(6,4) NULLABLE  -- MIL only, snapshot at claim time
is_over_policy    BOOLEAN DEFAULT FALSE
created_at        TIMESTAMPTZ
```

#### `expense_trip_items` *(S2 — MIL only, replaces km/rate fields in line_items)*
```
id                UUID PK
claim_id          UUID FK expense_claims.id
trip_number       INT
trip_date         DATE
from_location     VARCHAR(255)
to_location       VARCHAR(255)
purpose           VARCHAR(500)
is_round_trip     BOOLEAN DEFAULT FALSE
distance_km       NUMERIC(10,2)
rate_per_km       NUMERIC(8,4)         -- snapshot of policy rate at claim time
amount            NUMERIC(15,2)        -- distance_km × rate × (2 if round_trip)
budget_account_id UUID NULLABLE
budget_account_code VARCHAR(50) NULLABLE
budget_account_name VARCHAR(255) NULLABLE
```

#### `expense_approval_events` *(S2 — internal workflow audit)*
```
id          UUID PK
claim_id    UUID FK expense_claims.id
actor_id    UUID
actor_name  VARCHAR(255)
action      VARCHAR(20)    -- submit | approve | reject | return | pay
comment     TEXT NULLABLE
from_status VARCHAR(20)
to_status   VARCHAR(20)
created_at  TIMESTAMPTZ
```

#### `expense_attachments`
```
id                UUID PK
claim_id          UUID FK expense_claims.id CASCADE
file_id           VARCHAR(255)     -- file-api storage key UUID (string form)
file_name         VARCHAR(255)
file_size_bytes   INT DEFAULT 0
mime_type         VARCHAR(100) NULLABLE
uploaded_at       TIMESTAMPTZ DEFAULT now()
```

> **Implementation note:** No `LargeBinary` / `file_data` column. All files stored on file-api (`:8005`).
> Upload: `POST /api/v1/expenses/{id}/attachments` (multipart, Bearer token forwarded to file-api).
> Download: `GET /api/v1/expenses/{id}/attachments/{att_id}/file` (proxied).
> Delete: `DELETE /api/v1/expenses/{id}/attachments/{att_id}` (removes from file-api + DB).
> Only allowed when claim status is `draft` or `returned` (FS-006).

#### `expense_payment_records`
```
id                UUID PK
claim_id          UUID FK expense_claims.id
payment_date      DATE
bank_reference    VARCHAR(100)
amount_paid       NUMERIC(12,2)
currency          VARCHAR(3)
recorded_by       UUID FK users.id
notes             TEXT NULLABLE
created_at        TIMESTAMPTZ
```

#### `expense_policy_config`  *(single-row table, per org)*
```
id                      UUID PK
hst_rate                NUMERIC(5,4) DEFAULT 0.13
default_currency        VARCHAR(3) DEFAULT 'CAD'
meal_breakfast_limit    NUMERIC(8,2) DEFAULT 23.00
meal_lunch_limit        NUMERIC(8,2) DEFAULT 23.00
meal_dinner_limit       NUMERIC(8,2) DEFAULT 46.00
hotel_rate_cap          NUMERIC(8,2) NULLABLE
mileage_rate_per_km     NUMERIC(6,4) DEFAULT 0.7200
mileage_max_km          NUMERIC(8,2) DEFAULT 2000.00
mileage_budget_account_id UUID FK budget_accounts.id NULLABLE
travel_category_mapping JSONB        -- category → budget_account_id
updated_at              TIMESTAMPTZ
```

#### `custom_form_definitions`
```
id                UUID PK
code              VARCHAR(10) UNIQUE
name              VARCHAR(100)
description       TEXT NULLABLE
workflow_key      VARCHAR(50)
default_currency  VARCHAR(3) DEFAULT 'CAD'
field_schema      JSONB        -- ordered list of field definitions
is_active         BOOLEAN DEFAULT TRUE
created_at        TIMESTAMPTZ
updated_at        TIMESTAMPTZ
```

---

## 11. UniOps Portal

### 11.1 Purpose

The **UniOps Portal** is the unified frontend shell that provides single-sign-on access to all UniOps modules. From Phase 2 onward, users log in once and navigate between modules without re-authenticating.

```
                    ┌──────────────────────────────┐
                    │       UniOps Portal           │
                    │    uniops-portal  :5174       │
                    │                               │
                    │  ┌──────────┐  ┌───────────┐  │
                    │  │  EPMS   │  │    OA     │  │
                    │  │ :5173   │  │  :5175    │  │
                    │  │Procure- │  │ Expense   │  │
                    │  │ment     │  │ Manage-   │  │
                    │  │         │  │ ment      │  │
                    │  └────┬────┘  └─────┬─────┘  │
                    └───────┼─────────────┼────────┘
                            │             │
                    ┌───────▼─────────────▼────────┐
                    │     Shared JWT (same secret)  │
                    │     PostgreSQL: epms DB       │
                    └──────────────────────────────┘

Future modules (Phase 3+):
  AR/CRM  :5176  |  Fixed Assets  :5177  |  Cost Accounting  :5178
```

### 11.2 Portal Features

| Feature | Description |
|---------|-------------|
| Unified login | Single login page; same JWT used across all modules |
| Module launcher | Card-based home screen showing available modules with role-based visibility |
| Global header | Persistent header with: user avatar, notification bell (aggregated across all modules), module switcher |
| Breadcrumb | Module name always visible as first crumb |
| Role-based module access | If a user has no relevant role for a module, its card is greyed out |

### 11.3 Module Cards (Phase 2)

| Module | Icon | Roles with Access | Port |
|--------|------|-------------------|------|
| EPMS — Procurement | Briefcase | All roles | :5173 |
| OA — Expense Management | Receipt | All employees | :5175 |

### 11.4 Technical Architecture

- **Portal frontend:** New Vite + React app at `uniops/portal` (`:5174`)
- **Module frontends:** Each module is an independent Vite app (iframe or navigation-based linking)
- **Auth sharing:** JWT stored in `localStorage`; same `JWT_SECRET_KEY` across all services
- **Navigation model (Phase 2):** Portal home page with links that open the respective module app in the same tab. No iframe embedding. Users return to portal via breadcrumb or module switcher in the global header.
- **Global header injection (Phase 2):** Each module app includes the portal header component via a shared package (`uniops/packages/portal-header`)

### 11.5 Portal Service Registry

| Service | Role | Port |
|---------|------|------|
| `uniops/portal` | Frontend shell | :5174 |
| `uniops/epms` | EPMS frontend | :5173 |
| `uniops/oa` | OA frontend | :5175 |
| `uniops/epms-api` | EPMS backend | :8000 |
| `uniops/approval-api` | Approval Engine | :8003 |
| `uniops/mdm-api` | MDM stub | :8002 |
| `uniops/finance-api` | Finance Core | :8004 |
| `uniops/file-api` | File Server | :8005 |
| `uniops/expense-api` | Expense backend | :8006 |

---

## 12. API Design

New service: `expense-api` (`:8006`) — standalone FastAPI service sharing the same PostgreSQL database and JWT secret as EPMS.

### 12.1 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/expenses` | List claims (filtered, paginated) |
| `POST` | `/expenses` | Create new claim (draft) |
| `GET` | `/expenses/{id}` | Get claim detail |
| `PATCH` | `/expenses/{id}` | Update claim (draft/returned only) |
| `POST` | `/expenses/{id}/action` | Workflow action (submit/approve/return/reject/cancel) |
| `POST` | `/expenses/{id}/pay` | Record payment (approved → paid) |
| `GET` | `/expenses/{id}/attachments` | List attachments (metadata only) |
| `POST` | `/expenses/{id}/attachments` | Upload file to file-api; store `file_id` reference |
| `GET` | `/expenses/{id}/attachments/{att_id}/file` | Proxy download from file-api |
| `DELETE` | `/expenses/{id}/attachments/{att_id}` | Delete from file-api + DB (draft/returned only) |
| `GET` | `/expenses/policy` | Get expense policy config |
| `PATCH` | `/expenses/policy` | Update policy config (admin) |
| `GET` | `/expenses/custom-forms` | List custom form definitions |
| `POST` | `/expenses/custom-forms` | Create custom form definition |
| `PATCH` | `/expenses/custom-forms/{code}` | Update custom form definition |
| `GET` | `/tasks` | Unified OA task list for current user (see §7.2) |

---

## 13. AI OCR Service

### 13.1 Scope (Phase 2)

One shared OCR service (`ocr_service.py` in `expense-api`) handles two document types with different extraction schemas. Same underlying model, different system prompts.

| Mode | Trigger | Used by | Extraction depth |
|------|---------|---------|-----------------|
| `receipt` | "Scan Receipt" on EXP/TRV line item | Expense reimbursements | Simple: vendor, date, total, tax, description |
| `invoice` | Invoice upload for PA-DIR | Payment Applications | Full: vendor, invoice number, due date, line items, subtotal, tax, total |

**Provider:** Claude API — `claude-haiku-4-5` (lowest latency, lowest cost for structured extraction)  
**Input:** JPEG / PNG / PDF (first page only for PDF)  
**Output:** Structured JSON per mode  
**Endpoint:** `POST /ocr/{mode}` in `expense-api`

### 13.2 Extraction Schemas

**Mode: `receipt`**
```json
{
  "vendor_name": "Best Buy Canada",
  "date": "2026-04-16",
  "description": "MSI Laptop C1MOG-275CA",
  "total_amount": 903.99,
  "tax_amount": 104.00,
  "net_amount": 799.99,
  "currency": "CAD",
  "confidence": 0.92,
  "low_confidence_fields": ["date"]
}
```

**Mode: `invoice`**
```json
{
  "vendor_name": "Acme Supplies Ltd.",
  "invoice_number": "INV-2026-00892",
  "invoice_date": "2026-04-20",
  "due_date": "2026-05-20",
  "currency": "CAD",
  "line_items": [
    {
      "description": "Office Chair Model X200",
      "quantity": 4,
      "unit_price": 249.00,
      "net_amount": 996.00,
      "tax_amount": 129.48,
      "total_amount": 1125.48
    }
  ],
  "subtotal": 996.00,
  "tax_amount": 129.48,
  "total_amount": 1125.48,
  "confidence": 0.89,
  "low_confidence_fields": ["due_date"]
}
```

### 13.3 UX Behaviour

**Receipt mode (EXP/TRV):**
1. User uploads receipt image on a line item
2. "Scan Receipt" button appears
3. On click: spinner (~2s), fields pre-filled
4. Low-confidence fields highlighted in amber
5. User confirms or overrides; raw result stored in `expense_attachments.ocr_result`

**Invoice mode (PA-DIR):**
1. User uploads vendor invoice
2. OCR runs automatically on upload (no separate button — invoice upload implies extraction intent)
3. Extracted data shown in review panel before PA form opens
4. Low-confidence fields highlighted in amber — user must explicitly confirm each before proceeding
5. Raw result stored in `expense_invoices.ocr_result`

### 13.4 Error Handling

| Error | Behaviour |
|-------|-----------|
| API unavailable | Show error banner; allow manual entry |
| Unreadable image (< 0.5 overall confidence) | Show warning: "Could not read document clearly. Please enter details manually." |
| Unsupported format | Block upload with message before OCR is attempted |
| Timeout (> 15s) | Fail gracefully; allow manual entry |

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| OCR-001 | Receipt OCR must be triggered manually ("Scan Receipt" button). |
| OCR-002 | Invoice OCR runs automatically on upload (PA-DIR flow). |
| OCR-003 | Low-confidence fields (< 0.75) must be visually highlighted in amber. |
| OCR-004 | For invoice mode, low-confidence fields require explicit user confirmation before PA form opens. |
| OCR-005 | Raw OCR result must be stored alongside the attachment/invoice record for audit. |
| OCR-006 | All OCR errors must degrade gracefully — show specific error, allow manual entry. |
| OCR-007 | Single `POST /ocr/{mode}` endpoint accepts `mode=receipt` or `mode=invoice`; returns mode-appropriate schema. |

---

## 14. Payment Applications (PA)

Payment Applications represent a request to pay a vendor. In EPMS Phase 1, PA was tightly coupled to the procurement chain. From Phase 2, PA is owned by OA and supports two modes:

| Mode | Code | Trigger | Linked to |
|------|------|---------|-----------|
| PO-Linked | `PA-PO` | **Created in EPMS** (from PO detail or via EPMS Create PA page). OA's "PO-Linked PA" button redirects here. | EPMS PO + Invoice(s) |
| Direct | `PA-DIR` | Created directly in OA without any PO | Vendor only |

### 14.1 PO-Linked Payment Application (PA-PO)

**PA-PO is created and managed in EPMS, not OA.** OA's role for PA-PO is read-only aggregation in its unified PA list (§13.3 / §14).

**Entry points (both route to the EPMS Create PA page):**
1. **From EPMS PO detail page** — "Create Payment Application" button → EPMS `/pa/new?po_id={uuid}` (in-app navigation)
2. **From OA PA list** — "New PA" dropdown → "PO-Linked PA" → redirects to EPMS Create PA page (`{EPMS_URL}/pa/new`)

**PA-PO form fields, pre-fill rules, approval workflow, and constraints:** See EPMS PRD §3.15 (Standard PA creation flow). OA does not duplicate the PA-PO form spec; it consumes PA-PO records via `epms-api` for display purposes only.

### 14.2 Direct Payment Application (PA-DIR)

For vendor payments not tracked in the procurement chain — e.g., rent, utilities, professional services invoiced directly, pre-negotiated contracts.

#### Invoice-First Rule

**All PA-DIR records (except Advance/Prepayment) must begin with an Invoice upload.**

The creation flow is:

```
Step 1 — Upload Invoice
  └── User uploads vendor invoice (PDF / JPG / PNG)
  └── AI OCR extracts: vendor, date, line items, amounts, tax, total
  └── User reviews and corrects extracted data
  └── Invoice saved to DB (status: draft)

Step 2 — PA auto-generated from Invoice
  └── PA form opens pre-populated from Invoice line items
  └── User fills in: payment method, expected date, budget accounts
  └── Invoice status → linked_to_pa

Step 3 — PA approval flow (standard)
```

**Advance/Prepayment exception:** Payment Type = "Advance" skips Step 1. No invoice exists yet (paying before delivery). User must provide a justification note and a supporting agreement attachment.

This Invoice-First rule applies across all of UniOps:
- **PA-PO:** Invoice already exists in EPMS (matched to PO lines) — PA reads from it ✅
- **PA-DIR Regular:** Invoice uploaded in OA before PA creation (this section) ✅
- **PA-DIR Advance:** No invoice required — exception ✅
- **Expense reimbursements (EXP/TRV/MIL):** Receipts uploaded per line (not invoices in the AP sense — different flow) ✅

#### Invoice Upload & AI Extraction

When the user uploads a vendor invoice for PA-DIR:

**Supported formats:** PDF (≤20 pages), JPG, PNG, HEIC

**AI extraction fields:**

| Field | Notes |
|-------|-------|
| Vendor Name | Matched against Vendor master by name |
| Invoice Number | Vendor's own invoice number (stored for dedup) |
| Invoice Date | Date on the invoice |
| Due Date | Payment due date if shown |
| Currency | CAD / USD detected from symbol/text |
| Line Items | Description, Qty, Unit Price, Amount (each line) |
| Subtotal | Pre-tax total |
| Tax Amount | GST/HST amount |
| Total Amount | Grand total |
| Confidence | Per-field confidence score |

Low-confidence fields (< 0.75) highlighted in amber — user must confirm before proceeding.

**Duplicate invoice detection:** `(vendor_id, invoice_number)` is checked across both `invoices` (EPMS) and `expense_invoices` (OA) before saving. If a match is found the save is **blocked** (HTTP 409) — not a warning. The error message includes the existing document reference so Finance can investigate.

#### PA-DIR Form (populated from Invoice)

| Field | Source | Editable |
|-------|--------|----------|
| PA Number | Auto-generated `PA-YYYYMMDD-NNNN` | No |
| Vendor | From Invoice (matched to master) | Yes (if unmatched) |
| Invoice Reference | Invoice number + date | Read-only link to Invoice |
| Line Items | Auto-populated from Invoice lines (see below) | Yes |
| Total Amount | From Invoice total | Read-only |
| Currency | From Invoice | Yes |
| Payment Type | Select: Regular / Advance | Yes |
| Bank / Payment Method | Select: EFT / Cheque / Wire / Other | Yes |
| Expected Payment Date | Date | Yes |
| Notes | Textarea | Yes |

#### PA-DIR Line Items (auto-populated from Invoice)

Each Invoice line item becomes a PA line item. User adds Budget Account and Cost Centre:

| Field | Source | Required | Notes |
|-------|--------|----------|-------|
| Description | From Invoice line | Read-only | |
| Amount (net) | From Invoice line | Read-only | Pre-tax |
| Tax (GST/HST) | From Invoice line | Read-only | |
| Total Amount | From Invoice line | Read-only | |
| Budget Account | User selects | Yes | Shows live balance |
| Cost Centre | Auto-filled from account | Yes | Can override |

**If user needs to split a line across multiple budget accounts:** They can expand a single invoice line into sub-lines. The sum of sub-line amounts must equal the original invoice line amount.

### 14.3 PA Detail Page

The PA detail page uses a **3-tab layout** consistent with EPMS PR/PO detail pages.

**Header card (always visible, above tabs):**
- PA number (monospace) + Title
- Status badge
- **Action buttons** (contextual by status):

| Status | Buttons shown |
|--------|---------------|
| `draft` | Submit for Approval (primary) · Cancel (secondary) |
| `returned` | Resubmit for Approval (primary) · Cancel (secondary); amber note to check History tab |
| `submitted` / `in_review` | Recall to Draft (orange) |
| `approved` / `processed` / `cancelled` / `paid` | No action buttons (terminal or pending AP) |

All actions call `POST /api/v1/pa/{id}/action` via approval-api. On success, both the PA record and the history query are invalidated and refreshed without page reload.

**Tab 1 — Details:**
- Meta grid: Type · Vendor · PO Reference (if PA-PO) · Budget Account · Submitted date · Created date · Notes
- **Payment Breakdown card:**

| Row | Description |
|-----|-------------|
| Subtotal | Pre-tax amount |
| Tax | Tax component |
| Shipping | Shown only when > 0 |
| Other Charges | Shown only when > 0 (with label from `other_charges_note`) |
| **Total Payment** | Highlighted; = sum of all components |

- **Invoice card(s)** (PA-DIR only, one card per linked invoice):
  - Invoice header: file name · invoice number · vendor name · invoice date · total
  - Line items table: Description (+ budget account sub-label) / Qty / Unit Price / Tax / Amount
  - Table footer: Subtotal → Tax → **Total** rows

**Tab 2 — Attachments:**
- Lists all invoice attachments linked to the PA (fetched via `GET /api/v1/invoice-attachments?invoice_id={id}`)
- Each row: filename · file size · Download link
- Empty state: Paperclip icon + "No attachments"

**Tab 3 — History:**
- Reads approval events from `GET /api/v1/pa/{id}/history` (queries shared `approval_events` table written by approval-api)
- Timeline layout: step number circle → action badge (colour-coded) → role label → timestamp
- Optional comment shown as indented quote block
- Empty state: Clock icon + "No approval history yet"

**FR IDs — PA Detail:**

| FR ID | Requirement |
|-------|-------------|
| **PA-D-001** | PA detail page must display a 3-tab layout: Details / Attachments / History. |
| **PA-D-002** | Payment Breakdown must show Subtotal, Tax, and Total; Shipping and Other Charges shown only when > 0. |
| **PA-D-003** | Invoice card(s) in Details tab must be fetched from `GET /api/v1/invoices/{invoice_id}` and display all line items. |
| **PA-D-004** | History tab reads from the shared `approval_events` table via `GET /api/v1/pa/{id}/history`; no separate audit table is maintained in expense-api for PA actions. |
| **PA-D-005** | Action buttons must be visible only for statuses where the current user can act; after action the page state refreshes without a full reload. |

### 14.4 PA Status Lifecycle (both types)

```
draft → submitted → in_review → approved → paid
              ↓            ↓
          returned       rejected/cancelled
```

Same status model as EPMS PA (Phase 1). The `paid` transition records the bank transfer details.

### 14.5 Approval Workflow

PA-PO and PA-DIR each have their **own independently configurable workflow** in the shared Approval Engine (`:8003`), bound via action keys defined in §3.3.1 of PRD.md.

| Action Key | Document | Default workflow |
|------------|----------|-----------------|
| `pa` | PO-linked Payment Application | `dept_manager → gm_or_opm → finance_bp → finance_manager` |
| `pa_dir` | Direct Payment Application | `finance_bp → finance_manager` |

Both defaults ship as seed data in `CompanyConfig.workflow_defs` and are fully overridable by System Admin in **Portal Admin Panel → Approval Workflows** — no code changes or service restart required. Steps can be added, removed, or reordered freely.

```
PA-PO (default):        dept_manager → gm_or_opm → finance_bp → finance_manager
PA-DIR (default):       finance_bp → finance_manager
```

There are no hardcoded mandatory steps. If an organisation wants PA-DIR to require only Finance BP, they remove the `finance_manager` step from the `pa_dir` workflow in Portal Admin.

### 14.6 Budget Integration

**PA-PO:** When approved:
- Budget was already committed when PO was approved (EPMS handles this)
- On PA approval: `BudgetAccount.committed -= pa.amount` + `BudgetAccount.actual_spent += pa.amount`
- This is the actualization step (commitment → actual)

**PA-DIR:** When approved:
- No prior commitment exists
- `BudgetAccount.actual_spent += line.net_amount` directly (same as expense reimbursements)

### 14.7 Mark as Paid

After approval, Finance records bank transfer. Same fields as expense `paid` step:

| Field | Required |
|-------|----------|
| Payment Date | Yes |
| Bank Reference / EFT No | Yes |
| Amount Paid (CAD) | Yes |
| Notes | No |

### 14.8 EPMS Integration — Deep Link

**EPMS PO Detail page changes:**

| Before (Phase 1) | After (Phase 2) |
|-----------------|-----------------|
| "Create PA" → opens EPMS PA create page | "Create PA" → still opens EPMS PA create page at `/pa/new?po_id={uuid}` (PA-PO creation unchanged — stays in EPMS) |
| PA list tab in PO detail | PA list tab remains in EPMS PO detail, shows PA records read-only (PA-PO from epms-api; linked PA-DIR if any from expense-api) |
| `/pa` route in EPMS sidebar | `/pa` in EPMS sidebar → redirects to OA `/pa` (unified read-only list). Clicking "PO-Linked PA" in OA bounces back to EPMS `/pa/new`. |

**Deep-link URL format:**
```
{OA_BASE_URL}/pa/new?po_id={po_uuid}&po_number={po_number}&source=epms
```
OA reads the query params, pre-fills the form, and shows a banner: "Creating payment for PO {po_number} from EPMS."

**EPMS Document Chain Tree:**
- PA nodes remain in the document chain tree (read-only display)
- Clicking a PA node opens OA PA detail in a new tab
- EPMS reads PA data via a shared `expense-api` read endpoint or directly from the shared DB view

**EPMS Sidebar:**
- "Payment Applications" nav item remains for discoverability
- Clicking it navigates to OA `/pa` (full-page navigation, not iframe)
- A small "→ OA" badge on the nav item signals the cross-module redirect

### 14.9 PA Document Numbers

All PA records (both PA-PO and PA-DIR) use the same format as EPMS Phase 1:

| Format | Example |
|--------|---------|
| `PA-YYYYMMDD-NNNN` | `PA-20260501-0001` |

No format change. PA-PO vs. PA-DIR distinction is stored in a `pa_type` field, not reflected in the document number. Existing Phase 1 PA records require no migration.

### 14.10 FR IDs

| FR ID | Requirement |
|-------|-------------|
| PA-001 | PA-PO is created in EPMS, not OA. The EPMS Create PA page must accept a `?po_id=` query parameter for PO pre-fill. OA's "PO-Linked PA" button must redirect to `{EPMS_URL}/pa/new` (optionally with `?po_id=` if a PO is preselected). |
| PA-002 | PA-PO total across all PA records for a given PO must not exceed PO.total. |
| PA-003 | PA-DIR (Regular) requires an Invoice upload before PA creation. PA line items are auto-populated from the Invoice. |
| PA-004 | PA-DIR (Advance/Prepayment) skips Invoice requirement; requires justification note + supporting agreement attachment. |
| PA-005 | PA-DIR uses action key `pa_dir` bound to its own configurable workflow (default: `finance_bp → finance_manager`). All steps are configurable in Portal Admin Panel → Approval Workflows; no steps are hardcoded. |
| PA-006 | AI OCR on invoice upload must extract: vendor, invoice number, date, line items, tax, total. Low-confidence fields (< 0.75) must be highlighted for user review. |
| PA-007 | Duplicate invoice detection: `(vendor_id, invoice_number)` must be unique across `invoices` and `expense_invoices`. Duplicate blocks save with HTTP 409 (not a warning). See INV-001–003. |
| PA-008 | EPMS PO detail "Create PA" button must open the EPMS Create PA page (`/pa/new?po_id={uuid}`) — not OA. |
| PA-009 | EPMS PA sidebar link must redirect to OA `/pa` (unified read-only PA list). Creating a new PA-PO from that list bounces the user back to EPMS Create PA. |
| PA-010 | EPMS Document Chain Tree must show PA nodes as read-only links to OA PA detail (data via `expense-api`). |
| PA-011 | `paid` status requires: payment date, bank reference, and amount paid. |
| PA-012 | PA-PO approval actualizes budget (committed → actual_spent). PA-DIR Regular/Advance books directly to actual_spent. |
| PA-013 | A single Invoice line item may be split across multiple Budget Accounts; sub-line amounts must sum to the original line amount. |

---

## 15. Invoice Architecture — UniOps Roadmap

### 15.1 Invoice Ownership by Phase

Invoice is a cross-cutting entity: every non-prepayment payment in UniOps must be backed by an Invoice record. Ownership migrates as Finance Core matures.

```
Phase 1 (current)
  EPMS owns Invoice
  └── PO-matched vendor invoices only
  └── Stored in epms DB, managed by epms-api

Phase 2 (this PRD)
  EPMS continues to own PO-matched invoices
  OA (expense-api) owns vendor invoices for PA-DIR
  └── Both stored in shared epms DB
  └── Two separate tables: epms.invoices (PO-matched), expense_invoices (PA-DIR)
  └── Both consumed by Approval Engine

Phase 3 — Finance Core Invoice Module
  Finance Core (finance-api) takes ownership of ALL vendor invoices
  └── Single unified invoice inbox (AP inbox)
  └── EPMS PO-matched invoices migrate to Finance Core
  └── OA PA-DIR invoices migrate to Finance Core
  └── Both EPMS and OA read invoices from Finance Core via API
  └── Finance Core owns: upload, OCR, approval, dedup, GL posting
```

### 15.2 Phase 2 Invoice Table (OA-owned, interim)

For PA-DIR in Phase 2, OA introduces a new `expense_invoices` table. This is intentionally separate from EPMS's `invoices` table to avoid coupling, and is designed to migrate cleanly into Finance Core in Phase 3.

```
expense_invoices
  id                UUID PK
  invoice_number    VARCHAR(100)        -- vendor's own invoice number
  vendor_id         UUID FK vendors.id
  invoice_date      DATE
  due_date          DATE NULLABLE
  currency          VARCHAR(3)
  subtotal          NUMERIC(12,2)
  tax_amount        NUMERIC(12,2)
  total_amount      NUMERIC(12,2)
  status            VARCHAR(20)         -- draft | reviewed | linked_to_pa | paid
  ocr_result        JSONB NULLABLE      -- raw extraction
  ocr_confidence    NUMERIC(4,3)        -- overall confidence score
  uploaded_by       UUID FK users.id
  storage_key       UUID                -- file-api reference
  linked_pa_id      UUID FK payment_applications.id NULLABLE
  created_at        TIMESTAMPTZ
  updated_at        TIMESTAMPTZ

expense_invoice_lines
  id                UUID PK
  invoice_id        UUID FK expense_invoices.id
  line_number       INT
  description       VARCHAR(500)
  quantity          NUMERIC(10,3) NULLABLE
  unit_price        NUMERIC(12,4) NULLABLE
  net_amount        NUMERIC(12,2)
  tax_amount        NUMERIC(12,2)
  total_amount      NUMERIC(12,2)
  -- budget mapping filled by user when creating PA:
  budget_account_id UUID FK budget_accounts.id NULLABLE
  cost_centre_id    UUID FK cost_centres.id NULLABLE
  created_at        TIMESTAMPTZ
```

### 15.3 Invoice Attachments (`invoice_attachments` table)

The `invoice_attachments` table stores metadata for files attached to vendor invoices (both OA and EPMS sources). All file binaries are stored on **file-api** — no `LargeBinary` column.

```
invoice_attachments
  id                UUID PK
  invoice_id        UUID NOT NULL       -- FK to expense_invoices.id or EPMS invoices.id
  invoice_source    VARCHAR(10)         -- 'oa' | 'epms'
  file_name         VARCHAR(255)
  content_type      VARCHAR(100) DEFAULT 'application/octet-stream'
  file_size_bytes   BIGINT DEFAULT 0
  storage_key       UUID NULLABLE       -- file-api storage UUID (set on upload)
  uploaded_by       UUID NOT NULL
  uploaded_at       TIMESTAMPTZ DEFAULT now()
```

**Alembic migration history:**
- `0005_invoice_attachments` — created table with `file_data LargeBinary` (binary-in-DB pattern)
- `0007_invoice_attachment_fileapi` — **adds `storage_key`, drops `file_data`** (file-api pattern)

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/invoice-attachments?invoice_id=&invoice_source=oa` | Upload file to file-api; store `storage_key` |
| `GET` | `/api/v1/invoice-attachments?invoice_id=&invoice_source=oa` | List attachment metadata |
| `GET` | `/api/v1/invoice-attachments/{att_id}/file` | Proxy download from file-api |
| `DELETE` | `/api/v1/invoice-attachments/{att_id}` | Delete from file-api + DB |

**File-api tagging:** `doc_type="invoice"`, `service="oa"`.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **INV-ATT-001** | Invoice files (PDF/image uploaded during PA-DIR 3-step flow) must be stored on file-api, not in the database. |
| **INV-ATT-002** | The `invoice_attachments` table must not contain a `file_data` (LargeBinary) column in Phase 2. Migration `0007_invoice_attachment_fileapi` enforces this. |
| **INV-ATT-003** | Download is proxied through expense-api (Bearer token forwarded to file-api); the file-api URL is never exposed directly to the client. |

### 15.4 File Storage Policy (all OA tables)

**Rule:** No file binary data is stored in the OA database. All uploaded and generated files go to **file-api (:8005)**. DB tables store only metadata + a file-api UUID reference.

| Table | Reference column | file-api `doc_type` |
|-------|-----------------|-------------------|
| `expense_attachments` | `file_id VARCHAR(255)` | `exp` / `mil` / `trv` / `cfm` |
| `invoice_attachments` | `storage_key UUID` | `invoice` |

Both tables are managed by `expense-api`. The `attachment_helper.py` service module provides shared `upload_to_file_server()`, `proxy_download()`, and `delete_from_file_server()` helpers used by all attachment endpoints. This mirrors the EPMS `attachment_helper.py` pattern (PRD §3.4.1).

### 15.5 Unified AP Flow (Phase 3 target)

```
Vendor Invoice arrives
       ↓
Finance Core — Invoice Inbox
  ├── Upload (email/manual/EDI)
  ├── AI OCR extraction
  ├── Human review & correction
  ├── Dedup check
  └── Saved → status: reviewed
       ↓
PA created from Invoice
  ├── PA-PO: matches against EPMS PO
  └── PA-DIR: standalone vendor payment
       ↓
Approval Engine (approval-api:8003)
       ↓
Paid → GL posting (Finance Core)
```

### 15.6 Cross-Table Invoice Dedup (Phase 2)

**Rule:** `(vendor_id, invoice_number)` must be unique across both `invoices` (EPMS) and `expense_invoices` (OA). This prevents paying the same vendor invoice twice, regardless of whether it entered via procurement (PO-matched) or direct payment (PA-DIR).

**Enforcement:** Application-level check in `expense-api` before saving a new `expense_invoice` record:

```
On expense_invoice save (POST /api/v1/invoices):
  Precondition: client must supply both vendor_id AND invoice_number.
  If either is absent → dedup check is skipped (uniqueness cannot be asserted without vendor scope).

  1. Query: SELECT 1 FROM expense_invoices
            WHERE vendor_id = :vendor_id AND invoice_number = :invoice_number
              AND id != :current_id          -- (excluded on PATCH/update)
  2. Query: SELECT 1 FROM invoices           -- EPMS table (shared DB)
            WHERE vendor_id = :vendor_id AND vendor_invoice_number = :invoice_number
  3. If either query returns a row → BLOCK with HTTP 409:
        { "message": "Invoice {invoice_number} from this vendor already recorded",
          "duplicate": { "source": "oa"|"epms", "document_ref": "PA-... or INV-..." } }
```

**Frontend requirement:** The invoice creation call (`POST /api/v1/invoices`) **must** include `vendor_id` as part of the request body. In the PA-DIR 3-step flow, `vendor_id` is resolved in Step 2 (vendor matching) and must be forwarded to Step 3 when the invoice record is created. Omitting `vendor_id` disables the dedup check and allows duplicate invoices to be saved.

**Behaviour:**
- This is a **hard block**, not a warning — duplicate invoices cannot be saved.
- The error message includes the existing document reference so Finance can investigate.
- The check applies on both create (`POST`) and edit (`PATCH`) if `vendor_id` or `invoice_number` change.
- Prepayment PAs (no invoice required) are exempt from this check.

**Phase 3 migration:** When Finance Core takes ownership of all invoices, this constraint becomes a database-level unique index on a single unified `vendor_invoices` table.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| INV-001 | `(vendor_id, invoice_number)` must be unique across `invoices` (EPMS) and `expense_invoices` (OA). Duplicate save returns HTTP 409 with reference to the existing record. |
| INV-002 | Dedup check runs on both tables before any `expense_invoice` record is committed. Check is skipped only when `vendor_id` or `invoice_number` is absent. |
| INV-003 | The PA-DIR invoice creation call must include `vendor_id` (resolved from vendor matching in Step 2 of the 3-step flow); omitting it disables INV-001. |
| INV-004 | On `PATCH /api/v1/invoices/{id}` (OCR correction), the dedup check re-runs if `vendor_id` or `invoice_number` changes, excluding the record being edited. |

### 15.7 Invoice List Page

The Invoice List (`/invoices`) is a unified view aggregating invoices from both EPMS and OA in a single sortable table.

**Columns:**

| Column | Content | Notes |
|--------|---------|-------|
| Source | `OA` (amber) / `EPMS` (teal) badge | Indicates which system owns the record |
| Invoice # | **Vendor invoice number** (primary, monospace) | For EPMS: `vendor_invoice_number`; for OA: `invoice_number` |
| | Internal reference (small grey sub-label, EPMS only) | `internal_ref` (e.g. `INV-2026-0006`) shown below the vendor invoice number |
| Vendor | Vendor name | |
| Amount | Total amount with currency | |
| Status | Status badge | OA statuses: `uploaded / reviewed / used`; EPMS: `unmatched / matched / approved / paid` |
| Reference | PA number (OA) or PO number (EPMS) | `—` if not yet linked |
| Attachments | Attachment count with paperclip icon | `—` if none |
| Date | Record creation date | |

**Behaviour:**
- Filter tabs: All Invoices / EPMS (PO-matched) / OA (Direct payment)
- Search: by vendor name or invoice number (debounced, case-insensitive)
- **Each row is clickable** → navigates to Invoice Detail page (`/invoices/{source}/{id}`)
- Stat cards above the table show total, EPMS count, and OA count for the current filter

#### Invoice List — Role-Based Visibility

| Role | EPMS invoices visible | OA (expense) invoices visible |
|------|-----------------------|-------------------------------|
| `requester` | Own uploads (`uploaded_by = user`) **OR** invoices whose PO chain traces back to a PR the requester created | Own uploads (`created_by = user`) |
| `dept_manager` / `department_admin` | Invoices whose PO chain traces back to a PR in the user's department (via `cost_centers.department_id`) | Own uploads (`created_by = user`) |
| `gm` / `opm` | Invoices in the PO chain of departments mapped to their role in `CompanyConfig.dept_gm_opm_mapping` | Own uploads (`created_by = user`) |
| All other roles | **All** invoices | **All** invoices |

**Chain resolution for PO scope:**
`invoice.po_id` → `purchase_orders.pr_id` → `purchase_requests.cost_center_id` → `cost_centers.department_id`

**Enforcement:** Applied server-side in `GET /api/v1/invoices/all` (OA unified list, expense-api) and `GET /api/v1/invoices` (EPMS list, epms-api). Single-record `GET /api/v1/invoices/{id}` in epms-api also validates visibility for restricted roles, returning HTTP 404 (not 403) to avoid leaking record existence.

**FR IDs — Invoice List:**

| FR ID | Requirement |
|-------|-------------|
| **INV-L-001** | The "Invoice #" column must display the **vendor invoice number** as the primary value (not internal ref). For EPMS invoices, the internal ref is shown as a secondary sub-label in smaller grey text. |
| **INV-L-002** | Every row must be clickable and navigate to the Invoice Detail page for the corresponding source and ID. |
| **INV-L-003** | The unified list endpoint (`GET /api/v1/invoices/all`) must be registered with higher routing priority than the parameterised `GET /api/v1/invoices/{invoice_id}` route to prevent UUID validation rejection for the literal `/all` path segment. |
| **INV-VIS-001** | `GET /api/v1/invoices` (epms-api) must restrict results for `requester`, `dept_manager`, `department_admin`, `gm`, and `opm` roles. Unrestricted roles (`procurement_officer`, `procurement_manager`, `finance_bp`, `finance_manager`, `ap_clerk`, `system_admin`) see all invoices. |
| **INV-VIS-002** | A `requester` must see: (a) all EPMS invoices where `uploaded_by = user_id`; (b) all EPMS invoices whose `po_id` resolves to a PO linked to a PR the requester created. These two sets are union-ed. |
| **INV-VIS-003** | A `dept_manager` must see: EPMS invoices whose `po_id` resolves to a PO linked to a PR whose `cost_center_id` belongs to the user's department. OA invoices: own uploads only. |
| **INV-VIS-004** | `GET /api/v1/invoices/{id}` (epms-api) must perform the same visibility check as the list endpoint for restricted roles. An out-of-scope invoice must return HTTP 404 (not 403) to avoid leaking record existence. |
| **INV-VIS-005** | `GET /api/v1/invoices/all` (OA unified list, expense-api) must apply the same visibility rules to both the EPMS and OA portions of the combined result set. |

### 15.8 Invoice Detail Page

Accessed via `/invoices/{source}/{id}` where `source` is `oa` or `epms`.

**OA invoice detail** (`source=oa`): fetched from `GET /api/v1/invoices/{id}` (expense-api).

| Section | Content |
|---------|---------|
| Header | Source badge · Invoice # (monospace) · File name · Status badge |
| Meta grid | Vendor · Invoice Date · Due Date · Currency · PA Reference (if linked) · Uploaded date |
| Line items table | Description (+ budget account sub-label) / Qty / Unit Price / Tax / Amount; footer rows: Subtotal → Tax → **Total** |

**EPMS invoice detail** (`source=epms`): fetched from `GET /api/v1/invoices/{id}` (epms-api).

| Section | Content |
|---------|---------|
| Header | Source badge · Vendor invoice number (monospace) · Internal ref sub-label · Status badge |
| Meta grid | Vendor · Invoice Date · Due Date · Currency · PO Reference (if matched) · Total |
| Line items table | Description / Qty / Unit Price / Amount |

**FR IDs — Invoice Detail:**

| FR ID | Requirement |
|-------|-------------|
| **INV-D-001** | Invoice detail must display vendor invoice number as the primary heading identifier, not the internal reference. |
| **INV-D-002** | For OA invoices, all line items including description, quantity, unit price, tax, and amount must be fetched and displayed. |
| **INV-D-003** | For EPMS invoices, the detail is fetched from epms-api and displayed read-only; no actions are available from OA. |

---

## 16. OA App Shell

The OA module (`oa`, `:5175`) has its own full-page app shell — a `Sidebar + Header` layout consistent with EPMS and Portal. The shell wraps all authenticated OA routes via `AppLayout`.

### 16.1 Layout Structure

```
┌──────────────────────────────────────────────────────────┐
│  Sidebar (desktop: 240px expanded / 64px collapsed)      │
│  bg-[#085E5E]                                            │
│  ┌─ Brand area ───────────────────────────────────────┐  │
│  │  "OA" badge (32×32 bg-white/15) + "UniOps OA"      │  │
│  │  tagline: "Expense & Payment"                      │  │
│  │  [ChevronLeft collapse toggle — desktop only]      │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ── Modules ─────────────────────────────────────────── │
│  ├─ Payment Applications  /pa                            │
│  ├─ Expense Claims        /expenses                      │
│  └─ Invoices              /invoices                      │
│                                                          │
│  ── (collapsed: border divider instead of heading) ───── │
│  [ChevronRight expand button — bottom, when collapsed]   │
│                                                          │
│  ─── Footer ─────────────────────────────────────────── │
│  [← Back to UniOps Portal]  (hidden when collapsed)     │
├──────────────────────────────────────────────────────────┤
│  Header (60px, white, border-b)                          │
│  ├─ [☰ Hamburger — mobile only, md:hidden]               │
│  ├─ Spacer (flex-1)                                      │
│  └─ User avatar + name + dropdown                        │
│      ├─ Avatar: initials chip (bg-[#085E5E]/10, ring)    │
│      ├─ Full name (hidden on mobile)                     │
│      ├─ ChevronDown (rotates 180° when open)             │
│      └─ Dropdown menu:                                   │
│          ├─ Identity: Full name + role label             │
│          ├─ Portal Home → {PORTAL_URL}/                  │
│          └─ Sign Out (red) → global sign-out (§SO)       │
├──────────────────────────────────────────────────────────┤
│  Main content                                            │
│  └─ <Outlet /> wrapped in max-w-[1440px] centred padded  │
└──────────────────────────────────────────────────────────┘
```

### 16.2 Sidebar — Collapse Behaviour (Desktop)

- **Expanded:** `w-60` (240 px). Shows "OA" badge + "UniOps OA" name + tagline, nav item labels and section heading text.
- **Collapsed:** `w-16` (64 px). Shows "OA" badge only; each nav item shows icon only with `title` tooltip. The `ChevronLeft` collapse toggle is inside the brand row when expanded; a `ChevronRight` expand button appears at the bottom of the collapsed sidebar.
- "Back to UniOps Portal" link is hidden when the sidebar is collapsed.
- State is stored in local `useState(false)`; not persisted.
- Desktop collapse toggle is hidden on mobile.

### 16.3 Sidebar — Mobile Behaviour

- On screens `< md` (< 768 px) the sidebar is `fixed inset-y-0 left-0 z-30` and starts hidden (`-translate-x-full opacity-0`).
- The Header hamburger (`Menu` icon, `md:hidden`) sets `mobileOpen = true`, sliding the sidebar in (`translate-x-0 opacity-100`).
- A `bg-black/50` overlay (`z-20`) covers the main content; clicking it closes the sidebar.
- On mobile the sidebar is always `w-60` (never collapses to icon-only on small screens).
- Nav link clicks call `onClose()` to dismiss the sidebar on mobile.

### 16.4 Header — User Menu

The Header reads the authenticated user from the shared auth session (first checking `oa-auth`, then `portal-auth` as fallback). It renders:

| Element | Detail |
|---------|--------|
| Initials avatar | `bg-[#085E5E]/10` chip with `ring-2 ring-[#085E5E]/30`; initials = first letter of each word in `full_name`, max 2 chars |
| Full name | Shown on `sm:` and above; hidden on mobile |
| ChevronDown | Rotates 180° when dropdown is open |
| Dropdown — Identity | Full name (semibold) + role (formatted: underscores → spaces, title-case) |
| Dropdown — Portal Home | `<a>` to `{PORTAL_URL}/` — navigates away to Portal |
| Dropdown — Sign Out | Removes `oa-auth` from OA's localStorage (same origin), then redirects to `{PORTAL_URL}/logout` so Portal clears its own session. Final destination: Portal `/login`. See PRD-PORTAL §4.1 for the cross-origin sign-out design. |

Dropdown closes on outside click (mousedown listener attached while open).

### 16.5 Auth Guard

`AppLayout` checks for a valid session token on mount. If no token is found it immediately redirects to `{PORTAL_URL}?returnUrl={encodeURIComponent(window.location.href)}` so the Portal login page can redirect back after authentication.

While the redirect is in progress a centered "Redirecting to portal for authentication…" message is shown to prevent a blank flash.

### 16.6 Layout Consistency Across UniOps

All three frontends (Portal, EPMS, OA) implement the same app-shell pattern:

| Feature | Portal | EPMS | OA |
|---------|--------|------|-----|
| Sidebar brand area | UniOps logo badge + "Admin Portal" | Company logo or initials badge + configurable name/tagline | "OA" badge + "UniOps OA" |
| Sidebar collapse (desktop) | w-56 ↔ w-16 with ChevronLeft/Right | w-60 ↔ w-16 with ChevronLeft/Right | w-60 ↔ w-16 with ChevronLeft/Right |
| Mobile sidebar | Slide-in overlay, hamburger in header | Slide-in overlay, hamburger in header | Slide-in overlay, hamburger in header |
| Header height | 60 px | 60 px | 60 px |
| Header user menu | Avatar + name + role + sign-out | Avatar + name + role + sign-out | Avatar + name + role + sign-out |
| Back to Portal link | — (is Portal) | Sidebar footer | Sidebar footer |

**FR IDs — OA App Shell:**

| FR ID | Requirement |
|-------|-------------|
| **OA-SH-001** | OA `AppLayout` must include a collapsible sidebar (w-60 expanded, w-16 collapsed) with a brand badge, nav items, and a "Back to UniOps Portal" footer link. |
| **OA-SH-002** | Desktop sidebar collapse toggle (`ChevronLeft`/`ChevronRight`) must be present and functional. |
| **OA-SH-003** | On mobile (< md), sidebar must be off-screen by default and toggled via a hamburger button in the Header. |
| **OA-SH-004** | Header must display the authenticated user's initials avatar, full name, and role. |
| **OA-SH-005** | Header user dropdown must include: user identity, Portal Home link, and Sign Out. Sign Out must: (1) remove `oa-auth` from OA's own localStorage; (2) redirect to `{PORTAL_URL}/logout`. See PRD-PORTAL §4.1 (SO-001–SO-007) for the cross-origin sign-out design. |
| **OA-SH-006** | If no auth token is present, `AppLayout` must redirect to the Portal login URL with a `returnUrl` parameter before rendering any content. |
| **OA-SH-007** | Layout visual language (colours, sizing, motion) must match EPMS and Portal shells: `bg-[#085E5E]` sidebar, 60 px header, `FAFBFC` page background. |

---

## 17. UI/UX Design Specifications

*Added during /plan-design-review 2026-04-28. These specs must be implemented alongside features.*

### 17.1 Design System Alignment

All OA screens must follow the existing EPMS design system:
- **Font:** Inter (loaded via Google Fonts)
- **Brand colour:** `#085E5E` (teal)
- **App shell:** OA-specific `AppLayout` as specified in §16. Sidebar + Header pattern is consistent with EPMS and Portal.
- **Spacing:** 4px/8px base scale
- **Status badges:** Same `StatusBadge` component as EPMS (colour + dot)
- **Tables / lists:** Same table pattern as PR/PO list pages (sortable headers, row hover, pagination)
- **Form fields:** Same label-above-input pattern, never placeholder-as-label

### 17.2 Information Architecture

#### Screen Hierarchy

| Screen | Primary | Secondary | Tertiary |
|--------|---------|-----------|---------|
| Expense list | Claim number + type badge + status | Submitter + total amount | Date |
| EXP/TRV/MIL form | Form header (employee, date, currency) | Line items table | Sidebar summary |
| PA list | PA number + type badge + status | Vendor + amount | Date |
| PA-DIR flow | Current step (Stepper) | Step content | Help text |
| Portal home | Module cards | Notification count per module | Last visited |

#### PA List — Type Badge Colours

| Type | Badge | Colour |
|------|-------|--------|
| PO-Linked | `PO` | `primary-100` text `primary-700` (teal) |
| Direct | `Direct` | `neutral-100` text `neutral-600` (grey) |

The list must support filtering by: Type (All / PO-Linked / Direct), Status, Department, Date range, Submitter.

#### PA-DIR Creation — 3-Step Stepper

Fixed stepper at page top, consistent with EPMS tab components:

```
(✓) 1. Upload Invoice  ──►  (●) 2. Review & Correct  ──►  ( ) 3. Payment Details
```

- Step 1 and 2 occur on the same page (upload zone → OCR result panel below)
- Step 3 is the full PA form page
- Completed steps show checkmark + teal colour; current step is bold; future steps are grey
- **Back** button available between steps; going back does not lose data

### 17.3 OCR Review Panel (PA-DIR Step 2)

The most novel UX in the system. After upload, the page shows:

```
┌─────────────────────────────────────┬──────────────────────────────────┐
│  INVOICE PREVIEW                    │  EXTRACTED DATA                  │
│                                     │                                  │
│  [PDF/image rendered inline]        │  Vendor         Acme Ltd ✓       │
│  Scrollable if multi-page           │  Invoice No     INV-2026-00892 ✓ │
│                                     │  Invoice Date   2026-04-20 ✓     │
│                                     │  Due Date       [amber] 2026-05- │
│                                     │                 ← low confidence │
│                                     │  Currency       CAD ✓            │
│                                     │  ─────────────────────────────── │
│                                     │  LINE ITEMS                      │
│                                     │  1. Office Chair ×4  $996.00 ✓   │
│                                     │  + Add line manually             │
│                                     │  ─────────────────────────────── │
│                                     │  Subtotal       $996.00 ✓        │
│                                     │  Tax (HST 13%)  $129.48 ✓        │
│                                     │  Total          $1,125.48 ✓      │
│                                     │                                  │
│                                     │  [Continue to Payment →]         │
└─────────────────────────────────────┴──────────────────────────────────┘
```

**Low-confidence fields** (score < 0.75): amber left border on the field + amber warning icon. User must click to confirm or edit before "Continue" is enabled.

**Confidence indicator**: Small chip below each field showing confidence %: `92% confident` (green) / `61% confident` (amber). Visible by default; collapses after user confirms.

**"Continue to Payment"** button: disabled until all amber fields are confirmed.

### 17.4 Budget Account Picker — Live Balance Display

Used in EXP line items and PA-DIR line items. When user opens the picker:

```
Search accounts...
────────────────────────────────────────────────────────
CRM00301  General IT Expense
  Annual: CA$45,000 | Committed: CA$12,300 | Actual: CA$8,200
  Available: CA$24,500  ███████████░░░░░  54%
────────────────────────────────────────────────────────
CRM01004  Office Supplies                           ⚠ Over budget
  Annual: CA$5,000  | Committed: CA$4,900 | Actual: CA$3,200
  Available: CA$-100   ████████████████  102%
```

Colour coding:
- > 50% available: green progress bar
- 20–50% available: amber progress bar
- < 20% available: red progress bar
- Negative: red with `⚠ Over budget` badge; still selectable (triggers Finance Manager approval)

**After selection**: a compact chip appears below the field label:
`CRM00301 — Available: CA$24,500 (54%)` — updates live as user edits the amount.

### 17.5 EXP Form — Line Items Table Layout

20-row line items table. Dense layout for desktop; must remain usable.

**Column widths (1280px viewport):**

| Col | Width | Content |
|-----|-------|---------|
| # | 36px | Receipt number (read-only, small) |
| Date | 110px | Date picker |
| Description | flex 2 | Text input |
| Budget Account | flex 2 | Searchable select + balance chip |
| Cost Centre | 120px | Auto-filled, muted |
| Total A | 110px | Decimal input, right-aligned |
| Tax B | 90px | Decimal, editable, smaller text |
| Net C | 90px | Calculated, read-only, muted |
| Actions | 40px | Delete row (×) icon |

**Row states:**
- Normal: white background
- Over-budget: `warning-50` (`#FEF3C7`) background + amber left border (2px)
- Focused: `primary-50` subtle tint
- Row with attachment: paperclip icon at row end

**Add row button**: `+ Add Line` below the last row, left-aligned, ghost/text style.

**Mobile (< 768px)**: table collapses to stacked card-per-line. Each card shows all fields vertically. Receipt number as card header badge.

### 17.10 Responsive Specifications

| Screen | Desktop (≥ 1024px) | Tablet (768–1023px) | Mobile (< 768px) |
|--------|-------------------|--------------------|--------------------|
| PA-DIR Step 2 | Two-column: invoice preview left, OCR data right | Two-column (narrower) | Two tabs: "Extracted Data" (default) / "Invoice Preview" |
| EXP form line items | Full table (9 cols) | Collapsed table (hide Tax B, Cost Centre) | Card per line item |
| TRV form categories | Collapsible sections full-width | Same | Same, narrower cards |
| Expense/PA list | Full table with all columns | Hide "Department" and "Date" columns | Summary cards: type badge + amount + status |
| Portal home | Full-width stacked cards | Same | Same (cards go edge-to-edge) |
| Admin config panels | Two-pane: nav left, content right | Tab-based nav at top | Nav as dropdown selector |

**Touch targets:** All interactive elements minimum 44×44px. Budget Account picker dropdown items minimum 44px row height.

**Keyboard navigation:** All forms fully keyboard-navigable. Tab order follows visual reading order (left-to-right, top-to-bottom). EXP line items: Tab moves across the row, then to next row. ESC closes any picker/dropdown.

**Screen reader:** All form fields have visible labels (never placeholder-only). Status badges include sr-only text (e.g., `<span class="sr-only">Status: </span>Approved`). OCR confidence chips include sr-only context ("Confidence: 97%").

### 17.11 Form Draft & List Defaults

**Draft saving:** Manual only. EXP/TRV/MIL/PA forms have a visible "Save Draft" button (outline style) alongside the "Submit" button. No auto-save. If user navigates away without saving, browser `beforeunload` prompt: "You have unsaved changes. Leave?" — standard browser confirmation, no custom modal.

**List default sort:** `submitted_at` descending (newest first) — consistent with PR/PO/GR/PA lists in EPMS. Users can re-sort by clicking any column header. Sort preference is not persisted across sessions.

**New Claim dropdown default:** No default — user must explicitly select the form type (EXP / TRV / MIL / custom). Do not pre-select to avoid accidental wrong-type submissions.

### 17.12 OCR Success State

When all extracted fields have confidence ≥ 0.75, show a success banner above the extracted data panel:

```
✓  All fields extracted successfully — please verify before continuing.
```

- Green (`success-50` bg, `success-700` text)
- Fields populate with a brief fade-in animation (150ms, `ease-out`)
- Confidence percentage chips appear alongside each field
- "Continue to Payment →" button becomes enabled immediately (user can still edit any field before clicking)

### 17.6 TRV Form — Category Group Layout

Travel form groups line items by category (not a flat table like EXP). Each category is a collapsible section:

```
▼ Ground Transport                      CA$234.00  (2 items)
  | Date     | Description    | Amount | Receipt |
  | Apr 20   | Uber to airport| $45.00 | 📎 1    |
  | Apr 21   | Taxi from hotel | $189.00| 📎 2    |
  [+ Add Ground Transport expense]
▶ Accommodation                          CA$0.00   (0 items)  [+ Add]
▼ Meals — Dinner                         CA$156.00 ⚠ Over limit
  Policy limit: $46/day × 2 days = $92.00. You have $156.00 (+$64.00 over)
  ...
```

Over-limit category: amber header background, warning icon, shows policy limit vs. actual.

### 17.7 Interaction State Table

| Screen / Feature | Loading | Empty | Error | Success |
|-----------------|---------|-------|-------|---------|
| Expense list | Skeleton rows (5 placeholder rows) | "No claims yet. Create your first expense claim." + [New Claim] button | Toast: "Failed to load claims. Retry." | — |
| OCR scan trigger | Left panel: invoice preview stays visible. Right panel: skeleton rows for each field + sequential progress text "Extracting vendor details (1/3)…" → "Reading line items (2/3)…" → "Calculating totals (3/3)…" | — | Panel: "Could not read this document clearly. Please enter the details manually." + [Enter Manually] button | Fields populate with a brief fade-in animation; confidence chips appear alongside each field |
| Budget Account picker open | Skeleton list (3 rows) | "No budget accounts found for your department." | "Unable to load accounts. Try again." | — |
| PA list | Skeleton rows | "No payment applications yet." + [New PA] button | Toast error | — |
| Form save (draft) | Button: spinner + "Saving…" | — | Inline error below form | Toast: "Draft saved" (auto-dismiss 3s) |
| Form submit | Button: spinner + "Submitting…" | — | Toast with specific error message | Toast: "Submitted for approval" + redirect to detail page |
| PA-DIR duplicate invoice blocked | — | — | Full-width red banner: "Invoice [INV-2026-00892] from this vendor was already recorded on Apr 15. See [PA-20260415-0003]." | — |
| Mark as paid | Button spinner | — | Toast error | Status badge updates to "Paid"; confetti effect (optional) |

### 17.8 Empty States

Each empty state = warm message + primary action + context.

| Page | Empty state copy | Primary CTA |
|------|-----------------|-------------|
| Expense list (submitter, no claims) | "Nothing to reimburse yet. Submit your first expense claim and it'll show up here." | [+ New Claim] |
| Expense list (approver, nothing to approve) | "You're all caught up. No claims waiting for your review." | — |
| PA list (no PAs yet) | "No payment applications. Create one from a PO or start a direct payment." | [+ New PA] |
| Task inbox (expense tasks) | "All clear. No approvals waiting." | — |

### 17.9 Portal Home — Module Cards

**Layout: horizontal full-width cards (not a grid).** With only 2 modules in Phase 2, a 2-column card grid would look like every SaaS feature page (AI Slop #2). Instead, each module gets a full-width card stacked vertically — they feel like real destinations, not bullet points.

```
Welcome back, Justin ─────────────────────────────────────

┌──────────────────────────────────────────────────────────┐
│  EPMS — Enterprise Procurement                      →    │
│                                                          │
│  3 tasks pending approval  ·  Last visited 2 hours ago   │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  OA — Expense & Payment Management                  →    │
│                                                          │
│  1 claim awaiting review  ·  Last visited Yesterday      │
└──────────────────────────────────────────────────────────┘
```

- Cards span full content width (~800px max)
- Module name: large, bold (`text-xl font-semibold`)
- Activity line: small, muted (`text-sm text-neutral-500`)
- Arrow (`→`) right-aligned; entire card is clickable
- Card hover: `shadow-teal-md` lift + cursor pointer
- **No module logo/icon** — the module name IS the identity, no decoration needed
- Unavailable module: card greyed-out, "Unavailable — service is starting up" label, not hidden
- Notification count per module fetched from respective API health endpoints on page load

---

## 16. Remaining Open Items

| ID | Topic | Notes |
|----|-------|-------|
| OA-A | Custom form budget account field | Can a single custom form line have multiple budget account fields? Confirm scope. |
| OA-B | TRV multi-currency FX rate source | Manual entry (Phase 2) vs. automatic FX API (Phase 3). Bank of Canada rate as default? |
| OA-C | OCR provider fallback | If Claude API unavailable, fail gracefully (manual entry only) or use secondary OCR? |
| ~~OA-D~~ | ~~expense-api service boundary~~ | **RESOLVED:** New standalone `expense-api:8006`; accessed via UniOps Portal. |
| OA-E | CRA mileage rate tier | CRA 2025: $0.72/km for first 5,000 km, $0.66/km after. Phase 2: flat rate. Phase 3: tiered. |
| OA-F | Approval workflow per form type | Confirm whether EXP/TRV/MIL all share `expense_default` or each needs its own configurable workflow. |
| ~~OA-G~~ | ~~Budget commitment timing~~ | **RESOLVED:** Budget booked directly to `actual_spent` on approval. No intermediate commitment state for expenses. |
| ~~OA-H~~ | ~~PA module ownership~~ | **RESOLVED:** PA moves to OA. EPMS keeps deep-link entry from PO detail and read-only Document Chain display. |
| ~~OA-I~~ | ~~PA-PO data read in EPMS Document Chain~~ | **RESOLVED:** EPMS reads PA data via `expense-api` HTTP interface. |
| ~~OA-J~~ | ~~EPMS PA existing records migration~~ | **RESOLVED:** Number format unchanged (`PA-YYYYMMDD-NNNN`). No migration. `pa_type` field distinguishes PA-PO vs PA-DIR. |
| ~~OA-K~~ | ~~Invoice-first rule~~ | **RESOLVED:** All non-prepayment PAs require Invoice upload → AI OCR → human review → PA auto-populated from Invoice. See §15. |
| ~~OA-L~~ | ~~expense_invoices vs invoices dedup~~ | **RESOLVED:** `vendor_id + invoice_number` must be unique across both `invoices` (EPMS) and `expense_invoices` (OA). Application-level cross-table check enforced in `expense-api` before save. See §15.4. |
| ~~OA-M~~ | ~~OCR provider for invoice vs receipt~~ | **RESOLVED:** Shared OCR service (`ocr_service.py`) with two modes — `invoice` (line-item extraction) and `receipt` (simpler). Same Claude API, different system prompts. See §16.1. |

---

## 15. Out of Scope (Phase 2)

| Item | Rationale |
|------|-----------|
| Payroll integration | Reimbursements are bank transfers; ADP integration deferred |
| Per-diem auto-calculation (fixed daily rate) | Not standard at CRM; receipt-based only |
| Expense card reconciliation | No corporate card program in Phase 2 |
| Multi-entity claim (claim spans two companies) | Phase 4+ after multi-entity GL |
| Mobile app | Progressive Web App is acceptable for Phase 2 |
| Automated FX rate fetch | Manual exchange rate entry in Phase 2 |
| Custom form: repeat/nested sections | Single flat list of fields only in Phase 2 |
| PA-DIR batch payments | Pay multiple vendors in one PA — deferred to Phase 3 |
| Automated bank feed reconciliation | Match paid PA against bank statement — Phase 3 |
| EPMS PA pages kept as duplicates | EPMS PA create/edit pages removed; only read-only Document Chain display retained |
