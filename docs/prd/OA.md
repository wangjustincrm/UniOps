# OA — Product Requirements Document

**Version:** 3.3
**Date:** 2026-10-21
**Status:** In Production
**Scope:** Employee expense reimbursement and business travel in UniOps — General Expense (EXP), Mileage (MIL), Travel Application (TRA), Travel Expense (TRV) and admin-defined Custom Forms (CFM), with their approvals, attachments, claim PDF, payment hand-off and budget display.

**What changed in version 3.3** (2026-10-21)

- **A claim never waits on its own claimant (WF-SELF-001…004, §7.1).** A step whose only holder is the claimant — typically a Department Manager's own claim at the Department Manager step — is skipped and the next level approves. Before this, such a claim sat at that step for good: the claimant may not approve their own claim and nobody else held the step (TRA-20261020-0001/0002). Skipped steps no longer print the claimant as signer on the claim PDF or the Travel Application form, nor show them as approver in Approval Status.

**What changed in version 3.2** (2026-10-20)

- The Direct Payment Application now lives in EPMS (EPMS.md §3.22): invoice uploaded on EPMS → Invoices, matched with *Match → Direct PA*, paid from *Payment Applications → Create Direct PA*, approved on the same "PA (Direct)" workflow. OA's own Direct PA stays retired; its old records are tagged `oa_direct` (§15).

**What changed in version 3.1** (2026-10-19)

- The Expense Claims and Travel Applications lists sort by clicking a column header, ascending then descending, on the server across all pages (column sorting rule in EPMS.md §3.20 (UI-SORT-001…007)). The Date column sorts by submitted date, else created date, as shown.

**What changed in this version** (v2.0, 2026-06-02 → v3.0; every statement re-checked against the system as deployed):
- **Module re-scoped to reimbursement and travel.** The OA Direct Payment Application (PA-DIR) and the OA Invoices pages are **retired** (hidden in the UI, new Direct PAs refused by the server). PO-linked PAs belong to EPMS (see EPMS.md §3.15). The shared invoice, invoice-attachment and OCR services that EPMS still uses remain and are listed in §15.
- **New document: Travel Application (TRA)** — pre-approval for business travel with a traveller roster; a Travel Expense claim can only be raised against an approved TRA on which the claimant is a traveller (§3, §4).
- **Approvals run on the shared approval engine** (not an internal state machine): configurable chains per claim type, department-routed approvers, delegation, same-approver auto-skip, and **a returned claim resumes at the step that returned it** (§7).
- **Visibility rule tightened** (business rule 2026-09-11): an employee sees what they raised; an approver sees what is waiting for them and what they have acted on; payment roles see approved claims (§1.4).
- **Claim life-cycle features added:** Edit and resubmit of returned claims, hard delete of unapproved Travel Applications, **Send reminder** to the pending approver, **claim PDF with the approval record** filed automatically on approval plus **Regenerate PDF**, scanned receipts attached automatically, real approval timeline (§7, §8).
- **Budget figures from NC:** the budget-account picker on General Expense shows plan, NC posted actuals and available balance for the claimant's cost centres (§9).
- **Payment** is executed by the Finance payment executor (funding bank account / card, segregation of duties, posting); "Mark as Paid" with manual bank reference is replaced by **Mark as Processed** (§7.9).
- Per-line **tax codes** from MDM on General Expense; server-side check that every line's Total = Net + Tax.
- The two earlier PRD texts (main v2.0 and the short PA-DIR change note) are merged into this document; Portal-shell and Portal-home content now lives in PORTAL.md.

---

## 1. Product Overview

**OA** ("Office Automation" in the UniOps navigation) is where an employee claims back money they spent on the company's behalf, and where business travel is requested and approved. It is deliberately separate from Procurement (EPMS.md): if a vendor is invoicing the company, that is Procurement; if the employee paid and wants it back, that is OA.

OA consists of the **OA web app** and the **expense service** (backend). It relies on shared UniOps services:

| Shared service | What OA uses it for | Specified in |
|---|---|---|
| Portal / identity | Sign-in, users, primary + additional roles, departments, delegation | PORTAL.md, EPMS.md §1.2, §1.5 |
| Approval engine | Every submit / approve / return / reject / recall / cancel; tasks; approval history | EPMS.md §3.3 |
| Task inbox & notifications | Shared task table, per-task-type email policy, Send reminder | EPMS.md §3.7, §3.9 |
| File server | Storage of every attachment and generated PDF | EPMS.md §3.4 |
| Budget service | Account catalogue, plan figures, NC actuals | BUDGET.md |
| Finance | Payment execution, funding bank accounts, posting, payment batches | FINANCE.md |
| MDM | Tax codes | (MDM) |
| AI Assistant | Guide and query assistant mounted in the OA app | EPMS.md §3.19 |

### 1.1 Document Types

| Type | Code | Category | Status | Description |
|------|------|----------|--------|-------------|
| General Expense | `EXP` | Reimbursement | Live | Receipt-based reimbursement, up to 20 lines |
| Mileage Claim | `MIL` | Reimbursement | Live | Personal-vehicle trips at the policy rate per km |
| Travel Application | `TRA` | Pre-approval (no money) | Live | Request to travel; names the travellers |
| Travel Expense | `TRV` | Reimbursement | Live | Travel costs, grouped by category, against an approved TRA |
| Custom Form | `CFM_<CODE>` | Admin-defined | Live | Form built by an administrator from simple fields |
| Direct Payment Application | `PA-DIR` | Vendor payment | **Retired** | See §15 |
| PO-linked Payment Application | `PA` | Vendor payment | Owned by EPMS | See EPMS.md §3.15 |

### 1.2 Users and Roles

OA uses the platform role model (EPMS.md §1.1–§1.2): each user has one primary role plus any number of **additional roles**, and every OA permission check uses the **union** of the two — except where noted as "primary role only". Deactivated roles grant nothing.

| Who | What they do in OA |
|-----|--------------------|
| Any employee | Raise and submit their own claims and travel applications; edit and resubmit when returned; delete their own unapproved travel applications; send reminders |
| Approvers (Department Manager, Finance BP, Finance Manager, GM, and any role placed in a workflow) | Approve / return / reject claims at the step they hold, as resolved by the approval engine and its department routing; delegates act for them (EPMS.md §1.5) |
| Payment Officer, Finance Manager, Finance BP | Execute payment of approved claims ("Mark as Processed") — primary or additional role |
| AP Clerk | Sees approved and paid claims (read only); does not execute payment |
| Finance Manager | Edits the expense policy and custom forms (API accepts primary or additional role — see OI-11 for the page guard) |
| System Admin (primary role) | Sees every claim; edits policy and custom forms; may act on any approval step; executes payment |

**Module access:** the OA entry in the Portal is open to every signed-in user; there is no module-level permission.

### 1.3 Related Documents

EPMS.md (approval engine, tasks, notifications, delegation, PDF and file storage conventions, PO-linked PA), PORTAL.md (shell, sign-in, Admin → Approval Workflows / Approval Routing / Notification Settings / Data Maintenance), BUDGET.md (accounts, plans, NC actuals), FINANCE.md (payment execution, bank accounts, posting, payment batches).

### 1.4 Document Visibility

**Business rule (2026-09-11):** *an employee sees the documents they raised; an approver sees the documents they approve.* Enforced on the server for the list, the detail, attachments, approval status, PDF regeneration and reminders — the list and the detail use the same rule, so a document hidden from the list cannot be opened by URL.

| Who | Sees |
|-----|------|
| The claimant (employee on the claim) | Their own claims, any status including draft |
| An approver | Claims with an **open approval task** for them — pinned to them, a role-pool task whose role they hold (`gm_or_opm` satisfied by GM or OPM), or either of these for someone who has delegated to them today |
| Anyone who has acted on a claim | Claims they have approved / returned / rejected / submitted (from the approval history), so a claim does not vanish from an approver's list once approved |
| Payment roles (Finance BP, Finance Manager, AP Clerk, Payment Officer, System Admin — primary or additional) | In lists: approved claims (not TRAs). On detail: any claim |
| System Admin, AP Clerk (primary role) | All claims |

| FR ID | Requirement |
|-------|-------------|
| **OA-VIS-001** | The claim list must return only claims matching the rule above; filters and paging apply inside that scope. |
| **OA-VIS-002** | The claim detail and every claim sub-resource (attachments, approval status, reminder, PDF) must apply the same rule; out-of-scope requests are refused. |
| **OA-VIS-003** | "Waiting for me" is read from the shared task table written by the approval engine (department-routed), never re-derived from role names; it must include role union and active delegation. |
| **OA-VIS-004** | Travel Applications are excluded from the payment-role branch (they carry no money). |

---

## 2. General Expense (EXP)

Receipt-based reimbursement.

### 2.1 Header

| Field | Rule |
|-------|------|
| Employee, Department | Taken from the user record when the claim is created (not editable) |
| Submission Date | Required; defaults to today (local date) |
| Currency | CAD or USD; default CAD |
| Notes / Purpose | Optional free text |

### 2.2 Lines

Up to **20 lines**. Each line:

| Field | Rule |
|-------|------|
| Date | Date of the expense |
| Description | Required (200 characters) |
| Budget Account | Required. Two-level picker (L1 category → L2 account) showing each account's available balance and % used (§9) |
| Net Amount | Required |
| Tax Code | Optional; list from MDM tax codes. With no code, the policy default HST/GST rate applies |
| Tax | Calculated as Net × rate of the line's tax code; may be overridden |
| Total | Net + Tax |
| Receipt | Per-line **Scan** (AI receipt reading, §8.3) and attach |

Totals row shows Net, Tax and Total. The server rejects any line whose Total differs from Net + Tax by more than 0.01; negative lines (refunds) are allowed. Claim totals are the sums of the lines and are recomputed by the server on every save.

A warning banner appears when any account's available balance, less this claim's lines on that account, would go below zero. It is advisory only (see §9 and OI-01).

### 2.3 FR IDs

| FR ID | Requirement |
|-------|-------------|
| EXP-001 | Employee and department are recorded from the user record at creation. |
| EXP-002 | Up to 20 lines per claim. |
| EXP-003 | Each line carries Date, Description, Budget Account (L2), Net, Tax, Total; optional tax code. |
| EXP-004 | Total = Net + Tax, enforced by the server (tolerance 0.01). |
| EXP-005 | The budget-account picker shows the live available balance for the claimant's cost centres (NC actuals, §9). |
| EXP-006 | Tax defaults from the line's tax code, falling back to the policy HST/GST rate (default 13%). |
| EXP-007 | At least one attachment is required before submission (draft or returned). |
| EXP-008 | Receipt scan is optional and only pre-fills fields; the user can always enter manually. |
| EXP-009 | Over-budget lines require Finance Manager approval — **Not implemented** (warning only; OI-01). |

---

## 3. Travel Application (TRA)

Pre-approval for business travel, modelled on the paper travel approval form (content, not layout). A TRA carries **no money**: its totals are always zero and it never enters the payment path. One approved TRA can support several Travel Expense claims, by any traveller on it.

### 3.1 Form

| Field | Rule |
|-------|------|
| Application Date | Required; defaults to today |
| Travellers (Staff) | Required, at least one. Chosen from active system users (search by name or e-mail, 20 results). External companions go in Remarks |
| Number of Persons | Shown; equals the number of travellers |
| Destination | Required |
| From / To | Required travel dates; To not before From |
| Reasons and Explanation | Required |
| Transportation & Accommodation | Tick-boxes only (no amounts): Airplane, Train, Ship, Car, Accommodation, Meal, Other |
| Leave From / Leave To | Optional dates of personal leave combined with the trip |
| Remarks | Optional (external companions, notes) |

### 3.2 Rules

| FR ID | Requirement |
|-------|-------------|
| TRA-001 | A TRA is saved as a draft, then submitted into the `tra` workflow (default: Department Manager → Finance Manager → General Manager; configurable, §7.1). |
| TRA-002 | Approval of a TRA creates no follow-up task (no reimbursement, no payment). |
| TRA-003 | A TRA is never payable: payment is refused and it never appears as a payment task or in the payment-role list branch. |
| TRA-004 | The TRA prints as its own English form (travel details, roster, transport, approval signatures from the approval history). The PDF is filed on approval and can be regenerated at any status from the Travel Application card. |
| TRA-005 | The applicant (or System Admin) may **hard-delete** a TRA in draft, returned, submitted or in-review status from the list or the detail page, after confirmation. Deletion removes the record, its approval tasks and approval history, and its attachment files; no reason is recorded. Approved TRAs cannot be deleted. Deletion is refused if any travel expense claim references the TRA. |
| TRA-006 | Only TRAs can be deleted; other claim types are refused (they have budget and payment consequences). |
| TRA-007 | TRAs appear only under **Travel Applications**, never in the Expense Claims list; their detail lives at the Travel route. |

---

## 4. Travel Expense (TRV)

### 4.1 Gate

| FR ID | Requirement |
|-------|-------------|
| TRV-GATE-001 | A new TRV must reference a Travel Application. The server refuses creation when none is given, when it is not a TRA, when it is not **approved**, or when the claimant is **not on its traveller list**. |
| TRV-GATE-002 | The form offers only eligible TRAs (approved, claimant listed); choosing one pre-fills destination, dates and purpose. If none exist the form says a TRA must be approved first. |
| TRV-GATE-003 | The gate must also hold when a TRV is edited — **Defect**: editing can change the referenced TRA without re-checking (OI-14). |

### 4.2 Header

Travel Application (required), From / To dates (required), Destination (required), Country (required, default Canada), Currency (fixed **CAD**), Business Purpose (required).

**CAD only.** A Travel Expense claim is always in CAD. Foreign-currency receipts are converted to CAD by the claimant before entry; the system holds no exchange rate. The server refuses a TRV in any other currency, on create and on edit.

**Country decides tax.** In Canada each line has a **Tax** picker listing the active MDM tax codes (Finance → Tax Settings — GST, provincial HST, PST, QST…) plus "No tax"; a new line starts on Ontario HST (`HST_ON`), and tax = net × the picked code's rate. Lines saved before the picker existed show `HST_ON` when they carry tax. Outside Canada there is no picker and lines carry no tax: the server refuses a line with tax or a tax code on a claim abroad. Changing the country re-taxes every line. The country list is a short set of common countries plus "Other (outside Canada)"; only Canada vs. abroad changes behaviour today.

### 4.3 Lines by Category

Lines are entered in collapsible sections per category:

| Category | Per-line policy cap |
|----------|---------------------|
| Transportation | — |
| Hotel / Accommodation | — |
| Meals — Breakfast | Breakfast limit (default $23.00) |
| Meals — Lunch | Lunch limit (default $23.00) |
| Meals — Dinner | Dinner limit (default $46.00) |
| Incidental | Incidental limit (default $17.30) |
| Other | — |

Each line is either **Single day** or **Date range** (toggle on the line).

- **Single day:** Date, Description, Amount (net), Tax code (Canada only, §4.2), Tax, Total, Budget Account (the same two-level picker as EXP — L1 category → L2 account, with available balance and % used, §9), Receipt (attach or Scan).
- **Date range** (long trips, e.g. a 2–3 month overseas posting): From / To (must lie inside the trip dates), **Days claimed** (defaults to the number of days in the range; may be lowered, never above it), **Daily rate** (net), Description, Budget Account. Net = days × daily rate; tax and total follow as above. For meal and incidental categories the daily rate **may not exceed** the policy limit — refused, not flagged. Hotel, Transportation and Other have no cap. An invoice is **optional**; when one is attached its **Invoice amount** must be entered and the line total may not exceed it (one invoice may cover the whole stay, e.g. 20 nights, $2,000). Scanning on a range line fills the description and invoice amount, not the line amount.

The category is stored as a prefix of the line description and is restored when the claim is edited. Receipt files attached on a line are filed on the claim with the category in the file name. Detail page and claim PDF show a range line's dates, "N days × rate" and invoice amount.

### 4.4 Over-limit

A single-day meal or incidental line whose net amount exceeds its cap shows an "Over limit" badge and a banner (a date-range line cannot exceed its cap — §4.3). The server sets the claim's **Over Budget** flag when any line exceeds its cap, on create and again on every edit; the flag is shown on the claim. The flag does **not** currently add an approval step (OI-01).

### 4.5 FR IDs

| FR ID | Requirement |
|-------|-------------|
| TRV-001 | Header includes Travel Application, travel dates, destination and business purpose. |
| TRV-002 | Seven fixed categories as in §4.3. |
| TRV-003 | Default budget account per category, configurable — **Not implemented** (OI-02). |
| TRV-004 | Meal and incidental caps are configurable (Expense Config); defaults pre-loaded. Caps apply per line — per-day totals are **Not implemented** (OI-04). |
| TRV-005 | Over-cap lines are flagged, not blocked. Finance Manager step for them — **Not implemented** (OI-01). |
| TRV-006 | CAD only; the claimant converts foreign receipts to CAD (business rule 2026-10-16). No exchange-rate handling in the system. |
| TRV-007 | Totals per category and a claim total are shown. |
| TRV-008 | At least one attachment is required before submission — unless every line is a date-range line. |
| TRV-009 | A line may cover a date range: days claimed (default = days in the range, never more) × daily rate; the range must lie inside the trip dates. Checked by the server on create and on every edit, including an edit that only moves the trip dates. |
| TRV-010 | A date-range meal / incidental line's daily rate may not exceed the policy cap (hard). |
| TRV-011 | Invoice optional on a date-range line; when an invoice amount is given, line total ≤ invoice amount (strict). |
| TRV-012 | Trip country: Canada → each line picks its tax code (default `HST_ON`, or No tax); elsewhere → no tax and no tax code (server refuses either abroad). |

---

## 5. Mileage Claim (MIL)

### 5.1 Form

Header: Submission Date, Vehicle description (required), Vehicle owned by (Self / Company), notes. Currency is CAD.

Trip log, one row per trip:

| Field | Rule |
|-------|------|
| Date, From, To, Purpose | Required |
| Round Trip | Doubles the distance and the amount |
| Distance (km) | One-way distance entered by the employee (no route calculation) |
| Rate ($/km) | Policy rate, read-only |
| Amount | Distance × rate (× 2 if round trip) — recomputed by the server |
| Budget Account | Policy mileage account when one is configured |

Summary: total km (round trips counted twice) and total amount. A banner warns when total km exceeds the policy maximum; submission is not blocked.

### 5.2 FR IDs

| FR ID | Requirement |
|-------|-------------|
| MIL-001 | Trip log with Date, From, To, Purpose, Round Trip, Distance. |
| MIL-002 | Rate per km configurable; default $0.72 (flat; tiered CRA rate not implemented, OI-19). The server should apply the policy rate — **Defect**: the rate sent by the form is trusted (OI-20). |
| MIL-003 | Amount and total km are computed by the server; round trip doubles both. |
| MIL-004 | One configurable mileage budget account — stored in policy but **not editable on the Expense Config page** (OI-09). |
| MIL-005 | Max km per claim is configurable; exceeding it warns only. |
| MIL-006 | Only MIL claims carry mileage; other claim types never show or store a km total. |
| MIL-007 | No attachment is required to submit. |

---

## 6. Custom Forms (CFM)

Lets an administrator add a simple claim form without code.

### 6.1 Definition (Admin → Custom Forms)

| Setting | Rule |
|---------|------|
| Form Code | Unique; upper-cased, spaces become underscores. The approval action key is `cfm_<code>` |
| Form Name, Description | Shown to employees |
| Workflow | `cfm_<code>` if configured in Portal → Approval Workflows, otherwise the default `cfm` workflow (default: Department Manager) |
| Default Currency | CAD / USD |
| Fields | Ordered list: label, generated field name, type (**Text, Number, Date, Select, Textarea**), required flag, options (Select) |
| Active | Inactive forms are hidden from employees but kept |

Only System Admin and Finance Manager can create or change forms; other users only ever see active forms.

### 6.2 Use

Active forms appear in the **New Claim** menu. The employee fills the fields and optional notes; answers are stored with the claim and printed on its PDF under "Form Details". The claim type is `CFM_<CODE>` and the number `CFM_<CODE>-YYYYMMDD-NNNN`.

### 6.3 FR IDs

| FR ID | Requirement |
|-------|-------------|
| CFM-001 | Admin can create a form with unique code, name and field list. |
| CFM-002 | Field types Text, Number, Date, Select, Textarea. Budget Account, Cost Centre, Attachment and Calculated fields — **Not implemented**; custom-form claims carry no lines and no amount (OI-06). |
| CFM-003 | Every custom form uses the shared approval engine, per-form workflow with fallback to `cfm`. |
| CFM-004 | Over-budget rules for custom forms — **Not implemented** (OI-06). |
| CFM-005 | Forms can be deactivated without deletion. |
| CFM-006 | Form codes must fit the claim-type field — **Defect**: codes longer than six characters fail when a claim is created (OI-07). |

---

## 7. Common Workflow

### 7.1 Approval Chains

All OA documents are approved by the shared engine (EPMS.md §3.3). Each type has its own action key, configured in **Portal → Admin → Approval Workflows**; no step is hard-coded.

| Action key | Document | Default chain |
|------------|----------|---------------|
| `exp` | General Expense | Department Manager → Finance BP |
| `mil` | Mileage Claim | Department Manager → Finance BP |
| `trv` | Travel Expense | Department Manager → Finance BP |
| `tra` | Travel Application | Department Manager → Finance Manager → General Manager |
| `cfm` | Custom Form (default) | Department Manager |
| `cfm_<code>` | One custom form | Falls back to `cfm` unless defined |

Engine behaviour that applies to OA exactly as to EPMS (see EPMS.md §3.3): department-routed approvers from Approval Routing (the claim's department is the claimant's); same-approver auto-skip (recorded as "Auto-approved (same approver holds both roles)"); a chain whose every step is skipped approves on submit; delegation (EPMS.md §1.5); role union and the `gm_or_opm` pseudo-role.

**The claimant never approves their own claim**, so the engine does not leave a claim waiting on them. This differs from EPMS, where a submitter who holds step 1 approves it themselves (EPMS.md §3.3.5).

| ID | Requirement |
|----|-------------|
| WF-SELF-001 | When the claimant is the **only** holder of a step (the Department Manager of their own department; the sole holder of a post such as Finance Manager or GM; their own Director or Supervisor), the step is skipped and the claim moves to the next step. It is recorded as an approval event with the comment "Auto-skipped (claimant holds this step — cannot approve own claim)". Applies at submit, at resubmission, and when a later step is reached after an approval. |
| WF-SELF-002 | A step that someone other than the claimant also holds (a post with two holders, a Finance BP pool) is **not** skipped; the other holder approves it. Delegation does not count: a step is skipped even if the claimant has a delegate today. |
| WF-SELF-003 | If every remaining step would be skipped this way, submission is refused ("every approval step of this claim would be approved by the claimant themselves…") and the submit preflight reports it, owner Admin. A claim is never approved with no approver other than the claimant. |
| WF-SELF-004 | Claims already waiting on their claimant are moved on by an in-flight re-sync (Portal → Data Maintenance → the claim's approval state → Re-sync, or any Approval Routing save). On the Approval Status card a skipped step shows Approved with no approver name; the Approval Timeline shows it as "System"; the claim PDF shows "System" with the reason, and the Travel Application form's signature line reads "Skipped". |

**Conditional Finance Manager step for over-budget or over-limit claims — Not implemented.** The engine injects over-budget steps for PRs only (OI-01).

### 7.2 Status Life-cycle

```
draft ──submit──► submitted ──approve──► in_review ──approve (last)──► approved ──pay──► paid
  ▲                  │  │                    │  │
  │ recall           │  └─return──► returned ─┘  └─reject──► rejected
  └──────────────────┘              │  (edit, resubmit → resumes at returning step)
draft / returned ──cancel──► cancelled
```

| Status | Meaning |
|--------|---------|
| `draft` | Saved, not submitted |
| `submitted` / `in_review` | In approval |
| `returned` | Sent back to the claimant for changes |
| `approved` | Fully approved; awaiting payment (TRA: final) |
| `paid` | Payment executed by Finance |
| `rejected` | Declined; final |
| `cancelled` | Withdrawn; final (engine supports it; no OA button — OI-08) |

### 7.3 Actions and Who May Take Them

| Action | Allowed from | Who | OA UI |
|--------|-------------|-----|-------|
| Save / Edit | draft, returned | Claimant; also System Admin or Finance Manager (primary role) | Save as Draft / Edit → Save Changes |
| Submit | draft, returned | Claimant (employee or creator); System Admin | Submit (disabled with a hint until an attachment exists for EXP/TRV; not for a TRV of only date-range lines) |
| Approve / Return / Reject | submitted, in_review | Holder of the current step (task-based), delegate, System Admin; never the claimant (a step only the claimant holds is skipped — WF-SELF-001) | Approve / Return / Reject with comment dialog |
| Recall | submitted, in_review | Creator or System Admin | **Not exposed** (OI-08) |
| Cancel | draft, returned | Creator, System Admin, or current step holder | **Not exposed** (OI-08) |
| Delete | TRA only: draft, returned, submitted, in_review | Applicant or System Admin | Delete (list row and detail) |
| Pay | approved (not TRA) | Payment Officer / Finance Manager / Finance BP (primary or additional), System Admin (primary) | Mark as Processed |

The Approve / Return / Reject buttons and the pay and delete buttons are shown from a server-computed permission answer, so a button shown is a button the server will honour.

### 7.4 Return and Resubmit

| FR ID | Requirement |
|-------|-------------|
| WF-RET-001 | A returned claim offers both **Edit** and **Submit** to its owner; saving changes keeps it `returned` until submitted. |
| WF-RET-002 | Edit opens the same form as creation, pre-filled (all types, including TRA travellers and TRV categories, CFM answers). |
| WF-RET-003 | On resubmission the claim **resumes at the step that returned it**; approvals given before that stand (company rule for all document types). A recalled claim is a draft and starts from the top. |
| WF-RET-004 | Every field the edit form sends must be saved (travel dates, destination, transport, leave, travellers, TRA reference). |

### 7.5 Document Numbers

`<TYPE>-YYYYMMDD-NNNN` (e.g. `EXP-20261016-0001`, `TRA-…`, `CFM_<CODE>-…`). The sequence is the highest existing suffix + 1 for that prefix and day, allocated under a lock, so numbers never collide and are never reused (deleted TRA numbers are retired).

### 7.6 Approval Status and Timeline

The claim detail has a right-hand column with:
- **Approval Status** — each step of the configured chain with its state (Approved / Pending / Waiting / Returned / Rejected), who approved, when, and their comment.
- **Approval Timeline** — every event with its comment, oldest first: submit, approve, return, reject (from the shared approval history) and the payment entry.

The comment typed on Submit appears in the timeline.

### 7.7 Send Reminder

| FR ID | Requirement |
|-------|-------------|
| WF-REM-001 | Under the pending step of a submitted / in-review claim, **Send reminder** e-mails whoever holds that step (the delegate if they are away), regardless of the task type's e-mail policy, and reports who was reminded. |
| WF-REM-002 | Anyone who may open the claim may send it. Refused (with the reason shown) when the claim is not awaiting approval, nobody is reachable, or a reminder for that step went out within 24 hours. Behaviour is shared with EPMS (EPMS.md §3.9, NTF-009). |

### 7.8 Claim PDF

| FR ID | Requirement |
|-------|-------------|
| PDF-001 | When a claim becomes **approved** (by the last approval, or on submit when every step is skipped) a PDF named `<claim number>.pdf` is generated and filed in its Attachments. A PDF failure never fails the approval. |
| PDF-002 | Content: company name, form title, claim header (employee, department, dates, purpose / travel / vehicle details), lines or trip log or custom-form answers, totals (Net, Tax, Total Reimbursable; total distance for MIL), notes, and the **Approval Record**: "Submitted" with its comment, then each configured step with approver, date/time and comment, or "Skipped — no approval required". Only the latest submission round counts. Non-Latin names print correctly. |
| PDF-003 | **Regenerate PDF** on the Attachments card (approved or paid claims; anyone who may view the claim) rebuilds the PDF and **replaces** the earlier file. |
| PDF-004 | Travel Applications use their own form (TRA-004). |

### 7.9 Payment

| FR ID | Requirement |
|-------|-------------|
| PAY-001 | On approval of EXP / MIL / TRV / CFM, a **Process Reimbursement** task is created for the Finance BP pool; it closes when the claim is paid. TRAs create none. |
| PAY-002 | **Mark as Processed** (claim detail, approved, user with pay authority) asks for the funding **bank account or credit card** — active accounts in the claim's currency from Finance → Bank & Cards — and executes the payment through the Finance payment executor. Approved claims can also be paid from Finance payment batches (FINANCE.md). |
| PAY-003 | The executor enforces segregation of duties (a claimant cannot pay their own claim while the rule is enabled), sets the claim to **paid**, records the payment (date = today unless back-dated in Finance; amount = claim total), adds a "pay" entry to the claim history, books budget (§9) and emits the accounting event (employee expense = net, tax lines, bank = total). Vendor credits never apply to claims. |
| PAY-004 | The v2.0 "Mark as Paid" form (manual payment date, bank reference, amount) is superseded by PAY-002/003. |

---

## 8. Attachments and Receipt Reading

### 8.1 Attachments

| FR ID | Requirement |
|-------|-------------|
| ATT-OA-001 | Files are stored on the File Server; the database keeps metadata only. Maximum 25 MB per file; any file type. |
| ATT-OA-002 | Files can be added and deleted only while the claim is draft or returned; deleting requires the claimant or System Admin. The upload itself must also be limited to people who may edit the claim — **Defect** (OI-13). |
| ATT-OA-003 | Listing and downloading follow the claim's visibility rule (§1.4); downloads are proxied through the expense service with the user's token. |
| ATT-OA-004 | EXP and TRV require at least one attachment to submit; MIL, CFM and TRA do not. A TRV whose lines are all date-range lines is exempt (TRV-008). |
| ATT-OA-005 | A receipt picked with **Scan** on a line is kept as that line's receipt and attached to the claim automatically when the claim is saved (also if reading fails). Rescanning replaces the previously scanned file, not a manually attached one. Uploads run inside the save, which stays disabled until they finish; each failed upload is reported by name. |
| ATT-OA-006 | Generated PDFs are attachments named after the claim and replaced on regeneration. |

### 8.2 AI Reading — Model

Receipt reading runs on the server; no AI key reaches the browser. The model is **Claude Haiku** (company rule: application AI calls use Haiku only, sharing one key).

### 8.3 Receipt Scan (EXP / TRV)

| FR ID | Requirement |
|-------|-------------|
| OCR-001 | Receipt reading is triggered by the user (**Scan** on a line). Accepts JPEG, PNG, WebP, GIF, PDF up to 25 MB. |
| OCR-002 | Extracts vendor, date, description, total, tax and currency, and pre-fills the line (EXP: total and tax, net derived; TRV: the line amounts). The user reviews before saving. |
| OCR-003 | A reading outage and an unreadable document are reported differently; both leave manual entry available. The reading runs without blocking other users' requests. |
| OCR-004 | Low-confidence highlighting and storing the raw reading with the attachment — **Not implemented** for receipts (OI-05). |

---

## 9. Budget

| FR ID | Requirement |
|-------|-------------|
| BUD-OA-001 | The General Expense account picker shows, per active L2 account for the current fiscal year: annual budget, used %, and **available** — plan minus **NC posted actuals** (not the internal opening ledger), restricted by the budget service to the claimant's cost-centre scope. If NC cannot be reached, the budget service falls back to its ledger figure. |
| BUD-OA-002 | The over-budget banner on General Expense compares available balance with this claim's lines per account. It is advisory. |
| BUD-OA-003 | Submitting or approving a claim does not reserve or commit budget. |
| BUD-OA-004 | On payment the executor books the claim's net amounts per account and cost centre to the budget service (idempotent). **Open:** OA claim lines carry no cost centre, so in practice nothing is booked (OI-10); budget actuals shown anywhere come from NC regardless. |
| BUD-OA-005 | Travel Expense and Mileage pickers show account names only, no balance. |

Budget plans, NC actuals and the over-budget modes used for PRs are specified in BUDGET.md and EPMS.md §3.2.

---

## 10. Pages and Navigation

### 10.1 Sidebar

**Task Inbox**, **Expense Claims**, **Travel Applications**; an **Admin** group (Expense Config, Custom Forms) shown to System Admin; "Back to UniOps Portal". The app opens on the Task Inbox. Pages open as tabs in the shared multi-tab shell; detail tabs are titled by document number.

### 10.2 OA Task Inbox

Lists what the user must do in OA, from the expense service (independent of, and consistent with, Portal's unified Task Inbox, PORTAL.md §3.3). OA tasks are not shown in the EPMS Task Inbox (EPMS.md DSH-023).

| Task type | When | Label |
|-----------|------|-------|
| `approve_expense` | The user has an open approval task on the claim (same rule as §1.4), claim submitted / in review, not their own | Approve Expense |
| `revise_expense` | Own claim returned | Revise Expense |
| `pay_expense` | Approved claim (not TRA) and the user holds a payment-visibility role | Record Payment |
| `submitted_expense` | Own claim submitted / in review (informational) | In Review |

| FR ID | Requirement |
|-------|-------------|
| TASK-001 | Approval items come from the shared task table (department-routed, role union, delegation); there is no System Admin bypass — the admin's company-wide view is the list page. |
| TASK-002 | Only OA document types are read (EXP, MIL, TRV, TRA, CFM); EPMS tasks and PAs never appear here. |
| TASK-003 | Each document appears once; up to 200 claims; newest first; grouped by task type. |
| TASK-004 | Stat cards for Approve, Revise and Pay; tabs All / Needs My Action / My Submissions; filter by document type. |
| TASK-005 | A card opens the claim (TRAs open on the Travel route and are labelled Travel Application). |
| TASK-006 | The sidebar badge counts Approve + Revise + Pay items and refreshes every 60 seconds. |
| TASK-007 | `pay_expense` should only be offered to users who can execute payment — **Defect**: AP Clerk sees the card but cannot pay (OI-12). |

### 10.3 Expense Claims List

All reimbursement claims (EXP, MIL, TRV, CFM — never TRA) within the visibility scope. Status tabs All / Draft / Submitted / In Review / Approved / Paid; columns Claim #, Type, Employee, Net Amount, Status, Date; server-side paging (default 20, maximum 100). **New Claim** menu: General Expense, Mileage, Travel Expense and each active custom form — no default selection.

### 10.4 Travel Applications List

TRAs within scope; status tabs; columns Application #, Applicant, Department, Destination, Date, Status, Actions (Delete when permitted). **New Travel Application** button. Server-side paging.

### 10.5 Claim Detail

Header: number, status, Over Budget badge when flagged, type · employee · department, action buttons (§7.3). Left column: claim details (TRA card with travellers, destination, dates, transport, leave, remarks and Regenerate PDF; or lines / trip log / form answers), attachment-required notice, Attachments card. Right column (sticky): Approval Status and Approval Timeline. A TRV does not currently show a link to its Travel Application (OI-16).

### 10.6 Paging Standard

All OA lists page on the server and return `{ items, total }`; changing a filter or tab returns to page 1; the shared Pagination component shows "X–Y of Z" (EPMS.md §10).

---

## 11. Configuration

### 11.1 Expense Config (OA → Admin)

| Setting | Default |
|---------|---------|
| HST / GST default rate | 0.13 |
| Mileage rate per km (CAD) | 0.72 |
| Max km per claim | 2,000 (warning only — the page hint saying "blocked" is wrong, OI-09) |
| Meal caps: Breakfast / Lunch / Dinner / Incidental | 23.00 / 23.00 / 46.00 / 17.30 |

Saving requires Finance Manager or System Admin (primary or additional role). Mileage budget account is part of the policy but has no field on this page (OI-09).

### 11.2 Custom Forms (OA → Admin)

See §6.1.

### 11.3 Elsewhere

Approval chains (Portal → Admin → Approval Workflows, includes `exp`, `mil`, `trv`, `tra`, `cfm`, `cfm_<code>`), Approval Routing and Delegation (PORTAL.md, EPMS.md §3.3, §1.5); task e-mail policy (Portal → Admin → Notification Settings, EPMS.md §3.9.4); funding bank accounts (FINANCE.md); tax codes (MDM). Expense claims are also reachable from Portal → Data Maintenance (PORTAL.md).

---

## 12. Notifications and Portal Integration

| FR ID | Requirement |
|-------|-------------|
| NTF-OA-001 | OA approval, revise and reimbursement tasks are written to the shared task table and e-mailed by the platform notification outbox according to the per-task-type policy. OA claim task types are **off by default** until an administrator enables them (EPMS.md §3.9, NTF-002; OI-15). |
| NTF-OA-002 | E-mail links for OA tasks open the claim in OA. |
| NTF-OA-003 | The Portal home task feed includes OA items (approvals waiting for the user and approved claims for payment roles), merged and de-duplicated with the platform task feed; TRA items link to the Travel route. |
| NTF-OA-004 | Send reminder: §7.7. |

---

## 13. Data Model (summary)

| Table | Purpose |
|-------|---------|
| `expense_claims` | One row per EXP / MIL / TRV / TRA / CFM document: number, type, employee and department (recorded at creation), submission date, currency, notes, purpose, vehicle fields and total km (MIL), travel dates, destination and country (TRV; empty = Canada), transport modes, leave dates (TRA), reference to the TRA (TRV), totals (total, tax, net), status, current step, over-budget flag, submitted / approved / paid timestamps, creator |
| `expense_line_items` | EXP / TRV lines: number, date, description, budget account (id, code, name), cost centre (unused), total, tax, net, tax code; TRV date-range lines also period from / to, days claimed, daily rate, and optional invoice amount |
| `expense_trip_items` | MIL trips: date, from, to, purpose, round trip, distance, rate, amount, budget account |
| `expense_travelers` | TRA traveller roster (user, name, order) |
| `expense_attachments` | File metadata and File Server reference |
| `expense_approval_events` | Payment entries written by Finance (approval events live in the shared approval history) |
| `expense_policy_config` | Single row: HST rate, mileage rate, mileage account, max km, meal caps (and an unused legacy custom-forms column, OI-18) |
| `custom_form_definitions` | Code, name, description, workflow key, default currency, field list, active |

Approval tasks and approval history are the platform's shared tables (EPMS.md §3.3, §3.7). Payment records and accounting events belong to Finance.

---

## 14. Interfaces (expense service, `/api/v1`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/expenses` | List within scope; `type`, `exclude_type`, `status`, paging |
| POST | `/expenses` | Create draft (TRV gate applies) |
| GET | `/expenses/my-actions` | Portal home feed |
| GET | `/expenses/{id}` | Detail with merged history |
| PATCH | `/expenses/{id}` | Edit (draft / returned) |
| DELETE | `/expenses/{id}` | Hard-delete an unapproved TRA |
| GET | `/expenses/{id}/permissions` | is_owner, can_approve, can_pay, can_delete |
| GET | `/expenses/{id}/approval-status` | Step-by-step status |
| POST | `/expenses/{id}/action` | submit / approve / return / reject / recall / cancel (forwarded to the approval engine) |
| POST | `/expenses/{id}/remind` | Send reminder |
| POST | `/expenses/{id}/regenerate-pdf` | Rebuild the claim PDF |
| POST | `/expenses/{id}/pay` | Execute payment (forwarded to Finance) |
| GET / POST / DELETE | `/expenses/{id}/attachments…` | Attachments and download |
| GET / POST / PATCH | `/expenses/custom-forms…` | Custom form definitions |
| GET / PATCH | `/policy` | Expense policy |
| GET | `/tasks` | OA Task Inbox |
| GET | `/travel-applications/eligible` | TRAs the user may claim against |
| POST | `/travel-applications/{id}/pdf` | Regenerate TRA PDF |
| GET | `/users/directory` | Traveller search |
| POST | `/ocr/{mode}` | AI reading: `receipt`, `invoice`, `slip` |

---

## 15. Retired: Direct Payment Applications and OA Invoices

**Decision (2026-08-07, confirmed 2026-09-11/12):** OA's own vendor-payment path is withdrawn.

| Item | Current state |
|------|---------------|
| Direct PA (PA-DIR) pages and sidebar entry | Removed from the OA app; code kept so it can be restored |
| Creating a Direct PA | Refused by OA's server ("gone"). Direct PAs are now raised in EPMS (EPMS.md §3.22); OA's old records carry `source = oa_direct` so EPMS and Finance never take them for EPMS Direct PAs |
| Existing PA records | Readable, approvable and payable server-side; none existed in production at retirement. The PA list used by EPMS's document chain (by PO) is still served |
| OA Invoices list / detail pages | Removed with the Direct PA |
| Shared services kept for EPMS | Unified invoice list (EPMS + OA invoices, role-scoped), invoice attachments (File Server), AI invoice reading (`invoice` mode, with credit-note recognition and printed-total check) and pickup-slip reading (`slip` mode, purchase agreements), vendor search |
| PAs in the OA Task Inbox | Removed; EPMS PAs are in the platform Task Inbox |

The v2.0 Direct PA specification (invoice-first rule, three-step upload → review → payment details, PDF preview rendered with PDF.js, vendor matching, budget cascade Cost Centre → L1 → L2, default title "Direct Payment — {Vendor} {Invoice No}", additional attachments, cross-table duplicate-invoice block) describes the retired feature and is not a current requirement. PO-linked PAs, invoice matching and duplicate-invoice rules are specified in EPMS.md §3.14–§3.15 and FINANCE.md.

---

## 16. App Shell and UI Conventions

| FR ID | Requirement |
|-------|-------------|
| OA-SH-001 | OA uses the shared UniOps shell: collapsible sidebar (240 / 64 px), 60 px header with user menu (name, role, Portal Home, Sign Out), multi-tab work area, company branding (logo / name / tagline) from Portal settings. |
| OA-SH-002 | Mobile: sidebar hidden by default, opened from the header; overlay closes it. |
| OA-SH-003 | Without a session the app redirects to the Portal with a return address; any 401 from a UniOps service signs the user out through the Portal (PORTAL.md). |
| OA-SH-004 | When embedded in another module (e.g. a Finance drill-down tab) OA renders a single page without its own chrome or tabs. |
| OA-SH-005 | The AI Assistant is available in OA (EPMS.md §3.19); the OA guide text is updated with every OA change. |
| OA-SH-006 | Visual language follows the EPMS design system (EPMS.md §5, §3.20): shared status badges and buttons, labels above inputs, English UI, plant-local dates (date-only values never shift a day). |
| OA-SH-007 | Action buttons are disabled while their request is in flight (no double submission). |

The v2.0 design notes for the PA-DIR stepper and OCR review panel apply to the retired feature only.

---

## 17. Non-Functional and Security

| ID | Requirement |
|----|-------------|
| NFR-OA-001 | Every read endpoint applies object-level authorisation (§1.4); approval actions are authorised by the approval engine (return and cancel carry the same checks as approve / reject). |
| NFR-OA-002 | Only access tokens are accepted (refresh tokens are refused). |
| NFR-OA-003 | No AI key or third-party secret in the browser. |
| NFR-OA-004 | Long-running AI reading must not block other requests. |
| NFR-OA-005 | Lists that depend on approval history use database sub-queries, not in-memory id lists (history exceeds 80k rows). |

---

## 18. Out of Scope

Payroll integration; per-diem auto-calculation; corporate-card reconciliation; multi-entity claims; native mobile app (responsive web only); automated FX rates; repeat / nested sections in custom forms; vendor payments from OA (retired, §15).

---

## 19. Implementation History (summary)

| Period | Delivered |
|--------|-----------|
| 2026-05 | S1–S5: service, EXP + MIL, Direct PA + invoice OCR, TRV + admin config + custom forms, OA Task Inbox |
| 2026-06-02 | Fix sprint: TRV / CFM entry points, server-side OCR, receipt scan, attachment gate, custom-form table |
| 2026-06 → 07 | Multi-tab shell, shared tokens and badges, role union from identity, number allocation fix |
| 2026-08 | Object-level authorisation on all reads; config-driven inbox; Travel Application + TRV gate; TRA delete; TRA kept out of Expense Claims; delegation in OA |
| 2026-09-10 → 13 | Pre-launch hardening: edit page for returned claims, return/cancel authorisation, line total check, edit-time meal re-check, deactivated roles ignored, task inbox on the task table, narrowed visibility, Direct PA and OA Invoices retired |
| 2026-09-29 | NC actuals in the EXP account picker; scanned receipts attached on save; real approval timeline; two-column detail |
| 2026-10-08 → 14 | Send reminder; claim PDF on approval + Regenerate; returned claims resubmittable; resume at returning step; non-MIL edits no longer zero totals |
| 2026-10-16 | TRV date-range lines (days × daily rate, hard meal cap, optional invoice with cap), trip country (no tax abroad), per-line tax code picker in Canada, CAD only, attachment not required for an all-range TRV |

---

## 20. Open Items

| ID | Item | Status |
|----|------|--------|
| OI-01 | Conditional Finance Manager step for over-budget (EXP) and over-limit (TRV, max km) claims. Forms tell the user "Finance Manager approval will be required", but the engine adds no step for claims and EXP never sets the over-budget flag | Defect |
| OI-02 | Default budget account per TRV category (TRV-003) | Not implemented |
| OI-03 | Foreign-currency lines on TRV — closed 2026-10-16: TRV is CAD only, the claimant converts (TRV-006) | Closed |
| OI-04 | Per-day meal totals (caps are checked per line) | Not implemented |
| OI-05 | Receipt reading: confidence highlighting and storing the raw result with the attachment | Not implemented |
| OI-06 | Custom forms: Budget Account / Cost Centre / Attachment / Calculated fields, amounts and over-budget rules; approved CFM claims (amount 0) still reach the payment queue | Not implemented |
| OI-07 | Custom-form codes longer than six characters cannot be used — claim creation fails because the claim-type field is ten characters | Defect |
| OI-08 | Recall and Cancel are supported by the engine but have no OA buttons; drafts of EXP / MIL / TRV / CFM cannot be discarded | Open |
| OI-09 | Mileage budget account not editable on Expense Config; "Max km" hint says claims are blocked (they are only warned) | Defect |
| OI-10 | Budget booking at payment books nothing because OA lines carry no cost centre; decide whether to add cost centre to claim lines or drop the booking (actuals come from NC) | Open |
| OI-11 | Admin sidebar group shown only to primary System Admin; primary Finance Manager reaches the pages by URL only; additional-role Finance Manager is refused by the page although the API accepts the save | Open |
| OI-12 | AP Clerk receives "Record Payment" task cards but cannot execute payment | Defect |
| OI-13 | Attachment upload does not check the caller's relationship to the claim — any signed-in user can add a file to another person's draft or returned claim | Defect |
| OI-14 | Editing a TRV can change its Travel Application reference without re-applying the approved / traveller gate | Defect |
| OI-15 | OA claim task e-mails are off by default (business to confirm defaults); the Travel Application task type is not pre-listed in the notification policy until its first task exists; Direct PA e-mail links still point to the retired OA route | Open |
| OI-16 | TRV detail does not show or link its Travel Application | Open |
| OI-17 | Payment from OA captures only the funding account; bank reference and payment date are set only via Finance (confirm this replaces the v2.0 three-field rule) | Open |
| OI-18 | Remove the unused legacy custom-forms column from the expense policy | Open |
| OI-19 | Tiered CRA mileage rate (first 5,000 km / after) | Not implemented |
| OI-20 | Mileage amount uses the rate sent by the form, not the policy rate | Defect |
| OI-21 | Traveller search returns any active user's name and e-mail to any signed-in user (accepted for intranet; confirm) | Open |
