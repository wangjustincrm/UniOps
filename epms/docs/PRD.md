# EPMS — Product Requirements Document
**Version:** 2.24
**Date:** 2026-05-13
**Status:** In Development — Backend Live

---

## 1. Product Overview

The **Enterprise Procurement Management System (EPMS)** is a web-based procure-to-pay platform designed for food/dairy manufacturing environments. It digitises and enforces the full procurement lifecycle — from purchase requisition through payment — with multi-tier approvals, budget control, and audit trails.

The system consists of:
- **`epms`** — React/TypeScript frontend (Vite, TanStack Query, React Router v7)
- **`epms-api`** — Python/FastAPI backend with PostgreSQL, SQLAlchemy async, Alembic migrations

### 1.1 Target Users

| Role | Count | Primary Tasks |
|------|-------|--------------|
| Requester | Many | Create PRs, collect goods, settle prepayments |
| Department Manager (`dept_manager`) | Several | Approve PRs/documents for their own department; view department budget |
| General Manager (`gm`) | 1–2 | Approve documents for departments assigned in Dept-GM/OPM Mapping; budget view for mapped departments |
| Operations Manager (`opm`) | 1–2 | Approve documents for departments assigned in Dept-GM/OPM Mapping; budget view for mapped departments |
| Procurement Officer | Small team | Create POs, manage vendors, match invoices |
| Procurement Manager | 1–2 | First-level PO approval; assigned via Role Management |
| Warehouse Staff | Several | Receive goods, update GR condition |
| AP Clerk | Small team | Upload invoices, resolve match exceptions |
| Finance Manager | 1–2 | Over-budget approval, budget config; assigned via Role Management |
| Finance Business Partner (BP) | Several | PA approval |
| Vendor Manager | 1–2 | Maintain vendor master data; assigned via Role Management |
| System Admin | 1–2 | Full system configuration, user management; company-wide budget view |

> **Note:** GM, OPM, Procurement Manager, Finance Manager, and Vendor Manager are not system-level roles in the usual sense — they are **additional role assignments** made to specific users via Admin Panel → Role Management. See §1.2 for the multi-role model.

### 1.2 Multi-Role Users

Certain roles (GM, OPM, Procurement Manager, Finance Manager, Vendor Manager) are **layered on top of** a user's base `dept_manager` role. The assigned user simultaneously holds two roles:

| Special Role | Also Acts As | Example |
|---|---|---|
| GM | dept_manager for their own department | GM manages GMO dept → is GMO's dept_manager |
| OPM | dept_manager for their own department | OPM manages Operations dept → is that dept's dept_manager |
| Finance Manager | dept_manager for Finance dept | Finance Manager approves Finance dept PRs as dept_manager |
| Procurement Manager | dept_manager for Supply Chain / Procurement dept | — |
| Vendor Manager | dept_manager for their department | — |

**Approval workflow behaviour:**
- When a PR from the GM's own department (e.g. GMO) reaches the `dept_manager` step, the GM approves it — wearing their dept_manager hat
- When a PO or PA reaches the `gm` step, the same person approves it — wearing their GM hat
- The system routes tasks to the user's `assigned_user_id` (set in Role Management) for special roles, and by `department_id` for the dept_manager step
- **Multi-role auto-skip:** When a user holds multiple workflow roles, a single approval action cascades automatically through all consecutive steps where that same user is the assigned approver. Each skipped step is recorded as a separate `ApprovalEvent` with comment `"Auto-approved (same approver holds both roles)"`. The cascade stops as soon as a step is reached whose assigned approver is a different user. This rule applies equally to PR, PO, and PA workflows. See §3.3.4 for the engine-level specification.

**Task Inbox behaviour:**
- Multi-role users see **all tasks that match any of their active roles** — both their dept_manager tasks and their special-role tasks (procurement_manager, gm, opm, finance_manager, finance_bp, vendor_manager) aggregated into a single Task Inbox
- The JWT carries only the user's single base `role` field. Special roles are secondary assignments stored in `CompanyConfig.role_management`. The `GET /api/v1/tasks` endpoint expands the user's effective role set by reading Role Management before filtering — so a `dept_manager` who is also the configured `procurement_manager` sees both `approve_pr` (dept_manager step) and `approve_po` (procurement_manager step) tasks in a single inbox call

**Assignment:** GM, OPM, Procurement Manager, Finance Manager, and Vendor Manager are assigned to specific named users in Admin Panel → Role Management. If no user is assigned to a special role, that step is skipped or escalated per workflow config.

### 1.3 Access Control Matrix

The table below summarises which document types each role can create, approve, or view. All access is enforced server-side via RBAC middleware.

| Role | Create PR | Approve PR | **View PR** | Create PO | Approve PO | View GR | Create PA | Approve PA | Vendor Master | Budget View | Admin |
|------|-----------|-----------|------------|-----------|-----------|---------|-----------|-----------|--------------|-------------|-------|
| Requester | ✅ | — | **Own only** | — | — | Own + Service GR create | ✅ | — | — | Own dept | — |
| Department Admin | ✅ | — | **Own dept** | — | — | Dept | — | — | — | Own dept | — |
| Department Manager | ✅ | ✅ (own dept) | **Own dept** | — | — | Dept | — | — | — | Own dept | — |
| GM | ✅ | ✅ (mapped depts) | **Mapped depts** | — | ✅ | Mapped | — | ✅ | — | Mapped depts | — |
| OPM | ✅ | ✅ (mapped depts) | **Mapped depts** | — | ✅ | Mapped | — | ✅ | — | Mapped depts | — |
| Procurement Officer | — | — | **All** | ✅ | — | ✅ | — | — | Read | All | — |
| Procurement Manager | — | — | **All** | — | ✅ | ✅ | — | — | Read | All | — |
| Warehouse Staff | — | — | — | — | — | ✅ (receive/update) | — | — | — | — | — |
| AP Clerk | — | — | **All** | — | — | ✅ | ✅ | — | — | All | — |
| Finance BP | — | — | **All** | — | — | ✅ | ✅ | ✅ | — | All | — |
| Finance Manager | ✅ | ✅ (own dept as dept_manager; over-budget PRs company-wide) | **All** | — | — | ✅ | ✅ | ✅ | — | All | Partial |
| Vendor Manager | — | — | — | — | — | — | — | — | ✅ Full | — | Partial |
| System Admin | ✅ | — | **All** | ✅ | — | ✅ | ✅ | — | ✅ Full | All | ✅ Full |

**Document visibility scope definitions (applies to PR, PO, GR, Invoice, PA in EPMS):**

| Scope | Meaning |
|-------|---------|
| **Own only** | Documents created by the current user (`created_by = user_id`). For PO/GR/Invoice/PA: documents linked to PRs the user created, or directly created by them. |
| **Own dept** | All documents linked to the user's department (via cost_center chain). |
| **Mapped depts** | All documents from departments mapped to this GM/OPM in Dept-GM/OPM Mapping. |
| **All** | All documents with no filter. |
| — | No access to the list — default `view_<doc>` is OFF in the Access Control Matrix (§3.6.7.2). System Admin may enable the relevant View toggle to grant access. |

> **Configurability:** the scopes above are the defaults applied when the Matrix `view_<doc>` permission is granted. The "—" rows reflect the **default** Matrix settings only; toggling the corresponding View column ON in Admin Panel → Access Control Matrix grants list/detail access at the "All" scope (the scope filter applies to the five restricted roles only; warehouse_staff, vendor_manager, cfo, auditor, and custom roles see all rows once their View permission is enabled).

**Linked document chain — how scope propagates from PR:**

```
PR.created_by / PR.cost_center_id → CostCenter.department_id
   ↓
PO.pr_id IN (visible PRs)  OR  PO.created_by = user_id (for Requester direct POs)
   ↓
GR.po_id IN (visible POs)
Invoice.po_id IN (visible POs)
PA.po_id IN (visible POs)  OR  PA.created_by = user_id (for Requester direct PAs)
```

**Implementation:** Resolved server-side in `epms-api/app/core/access_scope.py` which builds SQLAlchemy subqueries (`pr_subq`, `po_subq`) injected into each list endpoint, plus an effective-permissions union (`_effective_permissions`) merged into the `scope["perms"]` dict that gates the View columns. Detail endpoints use the matching `is_pr_visible`, `is_po_visible`, `is_gr_visible`, `is_pa_visible`, and `invoice.is_visible` helpers — each enforces both the View permission and the scope filter, returning HTTP 404 on miss. Clients cannot bypass either layer by omitting or altering query parameters.

> **Enforcement rule:** All EPMS list endpoints (`GET /pr`, `GET /po`, `GET /gr`, `GET /invoices`, `GET /pa`) and their corresponding detail endpoints (`GET /pr/{id}`, `GET /po/{id}`, `GET /gr/{id}`, `GET /invoices/{id}`, `GET /pa/{id}`) apply role-based scoping automatically. The `mine` query parameter is ignored for Requesters (server always applies their scope). System Admin and other unrestricted roles receive no additional filter. Detail endpoints return HTTP 404 for any document outside the caller's scope (never 403, so existence cannot be inferred).
>
> **Two-layer access gate:** Each endpoint enforces two checks in order:
> 1. **Access Control Matrix `view_<doc>` permission** (Admin Panel → Role Management → Access Control Matrix, §3.6.7.2). Computed as the union of all active roles. Missing → empty list / 404 detail.
> 2. **Per-role scope** (this section). Defines *which* documents within the allowed type are visible.

> **Multi-role scope rule:** The JWT carries only the user's single base role. For users who also hold a special role via Role Management (`procurement_manager`, `finance_manager`, `vendor_manager`, `finance_bp`, `gm`, `opm`), the scope builder reads `CompanyConfig.role_management` at request time. If the user holds **any** special role whose default scope is unrestricted ("All"), the entire request is treated as unrestricted — the user sees all documents regardless of their base role. For example, a `dept_manager` who is also the configured `procurement_manager` sees all POs/GRs/Invoices/PAs, not just their department's. This expansion is applied in `_has_unrestricted_special_role()` inside `access_scope.py` and affects all five list endpoints simultaneously.

> **Note:** Department Supervisor is an optional/configurable role (see §3.2.6). It is not a default system role but can be enabled per-organisation. When enabled, it sits between Requester and Department Manager in the PR approval chain and has the same access as Department Manager scoped to its supervisor group.

> **Multi-role permission rule:** For roles that are layered on top of `dept_manager` (GM, OPM, Finance Manager, Procurement Manager, Vendor Manager — see §1.2), the permissions shown in this table are the **union** of all active roles. For example, Finance Manager shows ✅ for "Approve PR" because they can approve: (a) regular PRs from the Finance department (as dept_manager), and (b) over-budget PRs from any department (as finance_manager). Both scopes apply simultaneously to the same user.

### 1.4 Mobile & Remote Approval

Approvers can act on pending documents from any device via three supported channels:

| Channel | Mechanism | Supported Actions |
|---------|-----------|------------------|
| **In-App** | Responsive web app (mobile-optimised approval views) | Approve, Return, Reject, View detail |
| **Email Reply-to-Approve** | Approve by replying to the notification email with a designated keyword (e.g. `APPROVE`) | Approve only (Return/Reject require in-app comment) |
| **Microsoft Teams Card** | Adaptive Card posted to the approver's Teams channel; action buttons embedded in card | Approve, Return (with comment input in card) |

**Requirements:**

- **MOB-001** — The approval interface must be fully functional at viewport widths ≥ 360 px (mobile-first layout for approval views).
- **MOB-002** — Email Reply-to-Approve must validate the sender against the task's assigned user before acting; invalid or unauthorised replies are discarded and logged.
- **MOB-003** — Teams Card approval must trigger the same server-side approval action as an in-app approval, including MFA bypass rule (MFA is waived for single-tap card actions; full TOTP required for in-app approval actions per §3.1).
- **MOB-004** — All three channels must produce an identical audit trail entry (actor, action, timestamp, channel used).
- **MOB-005** — If a task is already resolved when a remote approval arrives (e.g. recalled or approved by another approver), the system must return a clear error message to the remote approver and take no action.

### 1.5 Absence Cover — Temporary Assignment

When an approver is unavailable (leave, travel, illness), a temporary delegate can be assigned to cover their approval tasks.

| FR ID | Requirement |
|-------|-------------|
| **ABS-001** | System Admin (and the absent user themselves, if they have advance notice) can assign a **temporary delegate** for any special role (GM, OPM, Procurement Manager, Finance Manager) or for the `dept_manager` role of any department. |
| **ABS-002** | A temporary assignment has a defined **start date and end date**. During the active window, all tasks that would normally route to the absent user are instead routed to the delegate. After the end date, routing reverts automatically with no manual intervention required. |
| **ABS-003** | The delegate receives the same notifications as the absent user would have received. The audit trail records both the delegate's name and a note that the action was taken under a temporary assignment (e.g. "Approved by Jane Smith [acting for John Doe]"). |
| **ABS-004** | Existing open tasks already assigned to the absent user at the time of delegation are **reassigned** to the delegate automatically. Tasks completed before the delegation window opens remain unchanged. |

Temporary assignments are configured in **Admin Panel → Role Management → Absence Cover**.

---

## 2. Procurement Types

| # | Name | Budget Check | Material ID | Notes |
|---|------|-------------|-------------|-------|
| 1 | Raw Materials / Packaging | No | Per line item | No budget code required |
| 2 | Misc / Consumables | Yes | No | General operating expenses |
| 3 | Spare Parts | Yes | Per line item (from Parts Catalog) | Items must be selected from Parts Catalog |
| 4 | Service | Yes | No | Requires expected completion date |
| 5 | Fixed Asset | Yes | No | Requires fixed asset ID |
| 6 | Project-Related | Yes | No | Requires project code |

---

## 3. Functional Requirements

### 3.1 Authentication & Security
- Email + password login
- MFA (TOTP) verification — required for approval actions; valid for 8 hours per session
- Role-based access control: navigation items and page content filtered by role
- Session persisted via `localStorage` (Zustand persist, key: `epms-auth`)
- JWT access + refresh tokens; real TOTP (pyotp)
- **Global sign-out** (see PRD-PORTAL §4.1 — cross-origin design): signing out from EPMS removes `epms-auth` from EPMS's own localStorage, then redirects to `{PORTAL_URL}/logout` so Portal clears its own session. Final destination: Portal `/login`.
- **Idle timeout**: after 15 minutes of inactivity, the server refresh token is blacklisted (`POST /auth/logout`), then global sign-out is triggered.
- **401 handling**: any HTTP 401 from the EPMS API (after a failed silent token refresh) triggers global sign-out.

### 3.1.1 User Profile
Each authenticated user has a **Profile** page (accessible from the header user menu) where they can manage personal settings:
- Display name, email, Teams account
- **Notification channel preference**: Email only / Teams only / Both / None — overrides the company default set in Admin Panel → Notification Settings
- Change password
- MFA device management (re-enrol TOTP)

### 3.1.2 Mobile & Remote Approval

See **§1.4** for the full channel specification (In-App, Email Reply-to-Approve, Microsoft Teams Card), supported actions per channel, and FR IDs MOB-001 through MOB-005.

### 3.1.3 Cross-Cutting UI Conventions — User Display

Wherever the system displays a person who performed an action (uploader, approver, matcher, resolver, settler, etc.), the UI must show the user's **full name**, never a raw UUID.

**Implementation pattern:** Names are stored denormalized on the document at write time (same approach as `vendor_name`, `po_number`). All `*_by` UUID fields have a paired `*_by_name VARCHAR(255)` column populated when the action is recorded. The frontend renders `name ?? uuid` so that any legacy records created before this field existed still display something meaningful.

| FR ID | Requirement |
|-------|-------------|
| **UI-USER-001** | Any field shown in the UI that identifies a user actor (uploaded by, matched by, resolved by, settled by, acknowledged by, etc.) must display the user's full name, not their UUID. |
| **UI-USER-002** | Names are stored denormalized on the document row at the time the action is performed, using a `*_by_name` companion column. No extra join is required at read time. |
| **UI-USER-003** | Frontend rendering uses `name_field ?? uuid_field` fallback so records that pre-date the `*_by_name` column continue to display correctly. |
| **UI-USER-004** | GR actor fields (`received_by`, `acknowledged_by`, `collected_by`) are already free-text strings entered by the warehouse operator — no UUID involved; no change required. |

**Implemented `*_by_name` fields:**

| Table | UUID field | Name field | Populated in |
|-------|-----------|-----------|-------------|
| `invoices` | `uploaded_by` | `uploaded_by_name` | `invoice_crud.create()` |
| `invoices` | `matched_by` | `matched_by_name` | `invoice_crud.match()` |
| `invoices` | `exception_resolved_by` | `exception_resolved_by_name` | `invoice_crud.resolve_exception()` |
| `payment_applications` | `settled_by` | `settled_by_name` | `pa_crud.settle()` |

### 3.2 Purchase Requisitions (PR)

#### 3.2.1 PR Create
- Procurement type selection (6 types, prominent tile selector)
- Conditional fields per type:
  - Types 1 & 3: Material ID per line item
  - Type 3: Line items must be selected from Parts Catalog (no free-text)
  - Type 4: Service expected completion date
  - Type 5: Fixed Asset ID
  - Type 6: Project code selector
- **Line Items table:**
  - **Type 3 (Spare Parts) columns:** #, Description (Parts Catalog picker), Material ID, Supplier Item ID, Qty, Unit, Unit Price, Line Total — **Notes column is hidden** to reduce visual clutter; the Notes field is still stored in state and submitted if previously populated
  - **All other types columns:** #, Description, Material ID (Types 1 only), Supplier Item ID, Qty, Unit, Unit Price, Line Total, Notes
  - Type 3 description cell: Parts Catalog picker (portal dropdown, fixed-position)
  - Selecting a part auto-fills: description, material ID, unit price, unit, supplier item ID
  - Minimum 1 line; lines can be added/removed
  - Total auto-sums and feeds into budget widget
- **Supplier Item ID** field per line item (e.g. Amazon ASIN, supplier catalog #)
- Preferred vendor search — sends `vendor_id` to backend (not just name)
- Cost Center selector — sends `cost_center_id` to backend; backend resolves and stores `vendor_name`, `cost_center_name`, `department_name` at creation time (denormalised)
- **Budget account code** — L1 → L2 two-level selector populated from the Budget Accounts defined in Admin Panel → Budget Config (§3.6.4a); hidden for Type 1 (Raw Materials / Packaging)
- **Decomposition Factors** — When the selected Budget Account has `decomposition_enabled = true`, a Factor block appears below the L2 picker showing one dropdown per active factor (1..3 dropdowns; see budget-api PRD §4.2 BFAC-001). All factor values must be picked before Submit. Stored on the PR as `factor_combo` JSONB (`{factor_code: value_code}`), matching the shape of `budget_plan_breakdowns.factor_combo` so PRs map directly onto a specific breakdown line for downstream balance / ledger reconciliation. Block hidden when the Account has no factors. See FR PR-FACTOR-001~004 below.
- Budget Balance Widget: shows annual budget, committed, actual spent, available balance, projected after this PR; turns red if over-budget
- Over-budget warning banner when projected balance < 0; justification textarea required (non-empty, no minimum character limit) unless `over_budget_mode = hard_block`, in which case Submit is disabled and a "Reduce budget code or split PR" message is shown instead (OBG-009)
- Delivery address — optional field; Required by date; File attachments; Requester notes
- Save as Draft / Submit for Approval
- Layout: `col-span-9` main form + `col-span-3` sidebar (budget widget + tips)

**FR IDs — PR Decomposition Factor selection:**

| FR ID | Requirement |
|-------|-------------|
| **PR-FACTOR-001** | When a user picks a Budget Account that has `decomposition_enabled = true` and at least one active factor, the Create PR form must render a Decomposition Factors block with one `<select>` per active factor, populated from `GET /accounts/{account_id}/factors` (budget-api). Inactive factor values are omitted from the dropdown. |
| **PR-FACTOR-002** | All visible factor dropdowns must have a non-empty value before Submit. Partial combos are rejected client-side with an inline error listing the missing factor name(s). Drafts may save a partial combo client-side, but the request body omits `factor_combo` until all values are set so the server-side shape validator (must be non-empty when present) does not reject the draft. |
| **PR-FACTOR-003** | The chosen combo persists on `purchase_requests.factor_combo` (JSONB, nullable) as `{factor_code: value_code}` — identical shape to `budget_plan_breakdowns.factor_combo` so PRs can be joined directly onto the matching breakdown line. Changing the L1/L2 selection resets the factor combo. |
| **PR-FACTOR-004** | PR Detail page renders a Decomposition Factors section directly under the Details grid whenever `pr.factor_combo` is non-empty. Each row shows `factor_name (factor_code) → value_code — value_name`, resolved from the live `/accounts/{id}/factors` call (falls back to raw codes if the factor was deactivated after the PR was filed). |

#### 3.2.2 PR Edit
- Available for PRs in `draft` or `returned` status
- Pre-populates all fields from existing PR data
- Vendor, cost center, budget L1/L2 reconstructed from API data
- "Save as Draft" — PATCH + navigate back; "Submit for Approval" — PATCH + action('submit')
- Guard: redirects to detail page with error if PR is not in editable status

#### 3.2.3 PR List

**Visibility scoping (enforced server-side — see §1.3):**

| Role | PRs shown |
|------|-----------|
| `requester` | Own PRs only (`created_by = current user`) |
| `dept_manager`, `department_admin` | All PRs in own department |
| `gm`, `opm` | All PRs from mapped departments (via Dept-GM/OPM Mapping) |
| `procurement_officer`, `procurement_manager`, `ap_clerk`, `finance_bp`, `finance_manager`, `system_admin` | All PRs |
| All other roles | No access (404 or empty list) |

- Filterable by status, type, cost center, date range
- Sortable table with status badges
- "New PR" CTA

**FR IDs — PR List visibility:**

| FR ID | Requirement |
|-------|-------------|
| **PL-001** | The server must apply role-based scoping to `GET /pr` unconditionally. A Requester cannot retrieve another user's PR by any API parameter combination. |
| **PL-002** | Department-scoped roles (`dept_manager`, `department_admin`) see PRs from their own `department_id` only, regardless of client-supplied filters. |
| **PL-003** | GM/OPM see PRs from departments mapped to them in Dept-GM/OPM Mapping. The mapping is resolved at request time; changes to the mapping apply immediately to subsequent requests. |
| **PL-004** | Procurement Officer, Procurement Manager, AP Clerk, Finance BP, Finance Manager, and System Admin see all PRs (and all linked POs/GRs/Invoices/PAs) with no department filter. Vendor Manager and Warehouse Staff default to **no PR visibility** (see §3.6.7.2 default `view_pr` settings) and can be granted access only by toggling `view_pr` ON in the Access Control Matrix. |
| **PL-006** | Multi-role users must receive the **union** of all their active roles' scopes. If a user's base JWT role is restricted (e.g. `dept_manager`) but they also hold an unrestricted special role via Role Management (e.g. `procurement_manager`), the scope resolver must grant unrestricted visibility. The resolver reads `CompanyConfig.role_management` on every list request — no re-login required when role assignments change. |
| **PL-005** | The frontend PR list page must not display PRs that fall outside the user's server-enforced scope, even if cached data contains them. On role change, the query cache for PR list must be invalidated. |
| **PL-007** | `GET /pr/{id}` must enforce the same per-role scope plus `view_pr` Matrix gate as the list endpoint. Out-of-scope or view-disabled requests return HTTP 404, not 403, so document existence cannot be probed. Same rule applies to `GET /po/{id}`, `GET /gr/{id}`, `GET /pa/{id}`, and `GET /invoices/{id}`. |

#### 3.2.4 PR Detail
- **Details section**: PR number, title, status, type, created date, submitted date
- **Vendor, Department, Cost Center**: displayed from denormalised name fields (`vendor_name`, `department_name`, `cost_center_name`) stored at creation time
- **Budget Code**: shows `code — name` (name resolved via budget overview lookup)
- **Decomposition Factors** *(rendered only when `pr.factor_combo` is non-empty)*: grid of `factor_name → value_code — value_name` rows, resolved via `GET /accounts/{id}/factors` (PR-FACTOR-004)
- **Line Items tab**: shows Description, Material ID, Supplier Item ID (column only shown when at least one item has a value), Qty, Unit, Unit Price, Line Total
- **Attachments tab**: file upload/download/delete; auto-populated with approved PR PDF upon final approval
- **History tab**: audit events showing action + actor role + comment + timestamp
- **Approval Timeline** (right panel): completed steps with actor, date, action; current step with waiting indicator; pending steps
- **Action buttons** based on status:
  - `draft` / `returned`: Edit button (link to `/pr/:id/edit`) + Withdraw (cancel)
  - `submitted` / `in_review`: Recall to Edit button (triggers `recall` action → navigates to edit) + Withdraw
  - `approved`: Create PO button

#### 3.2.5 PR Document Numbering

Purchase Requisitions are assigned a system-generated number on submission using the format:

```
PR-YYYYMMDD-XXXX
```

Where `YYYYMMDD` is the submission date (UTC) and `XXXX` is a zero-padded daily sequence number (resets to `0001` each calendar day). Example: `PR-20260410-0042`.

- **PR-006** — The document number is assigned by the backend at the point of `submit` action; it is never editable by users. Draft PRs display a placeholder (e.g. "DRAFT") until submitted.

#### 3.2.6 PR Approval Flow

PR approval uses the configurable step-based engine described in §3.3. The PR workflow is configured independently in **Admin Panel → Approval Workflows → PR**.

**Default PR approval chain:**

```
Step 1: dept_manager        — Department Manager (mandatory)
Step 2: gm_or_opm           — GM or OPM (mandatory; resolved at runtime via Dept-GM/OPM Mapping)
```

**Optional Department Supervisor step (configurable):**

An optional `dept_supervisor` step may be inserted between the Requester and the `dept_manager` step. This role is disabled by default and is activated per-organisation in Admin Panel → Approval Workflows → PR. When enabled, the Department Supervisor reviews the PR before it reaches the Department Manager.

> The Department Supervisor role is optional/configurable and not a default system role. See §1.3 note.

**Over-Budget pre-approval flow:**

When a PR is flagged as over-budget (projected balance < 0 for its budget account), the system reads **`CompanyConfig.budget_admin_config.over_budget_mode`** (configured in **Admin Panel → Budget Config → Over-Budget Approval Mode** — single source of truth) and applies one of three behaviours:

| Mode | Steps injected before the normal approval chain | Notes |
|------|------------------------------------------------|-------|
| `fm_gm_opm` (default) | `OB-1: finance_manager → OB-2: gm_or_opm` (then continues into the normal `dept_manager → gm_or_opm` chain) | Two-step pre-approval. |
| `fm_only` | `OB-1: finance_manager` (then continues into the normal chain) | Single pre-approval; bypasses GM/OPM visibility. |
| `hard_block` | None — submission is **rejected outright** at submit time (HTTP 4xx). Front-end disables the Submit button and shows: *"Reduce the budget code's commitment or split the PR into smaller items."* No justification field is shown. | No approval path. |

The pre-approval steps are injected at runtime by `approval-api`'s engine (`engine.py:execute_action`) when `pr.over_budget` is True, based on the current mode. They are **not** persisted in `CompanyConfig.workflow_defs["pr"]`.

Over-Budget FR IDs:

| FR ID | Requirement |
|-------|-------------|
| **OBG-001** | When a PR's total value causes its budget account's projected balance (annual_budget − committed − actual_spent − pr_amount) to be negative, the system must set the PR's `over_budget` flag on create/update and persist the user-supplied `over_budget_justification`. The approval engine must then inject pre-approval step(s) per `over_budget_mode` (see modes table above). |
| **OBG-002** | The task assigned to Finance Manager (and GM/OPM in `fm_gm_opm` mode) for over-budget pre-approval must display the PR number, total amount, and the requester's justification text inline in the task inbox — without requiring the approver to open the PR Detail page. |
| **OBG-003** | If Finance Manager rejects the over-budget request, the PR is returned to the Requester with the Finance Manager's comment; the `over_budget` flag remains until the line items or budget code change. |
| **OBG-004** | In `fm_gm_opm` mode, after Finance Manager approves the PR advances to the GM/OPM over-budget pre-approval step (OB-2). In `fm_only` mode this step is skipped and the PR proceeds directly into the normal `dept_manager → gm_or_opm` chain. |
| **OBG-005** | GM/OPM over-budget pre-approval (only in `fm_gm_opm` mode): if rejected, PR is returned to Requester; if approved, PR enters the normal `dept_manager → gm_or_opm` chain. |
| **OBG-006** | The budget account's `committed` amount is incremented only when the PR is fully approved (all steps, including any over-budget pre-approval steps, cleared). Note: in the current implementation `committed` is managed by the Finance Core / PA flow; PR approval itself triggers a `create_po` task but does not directly write to `committed`. No pre-mature commit can occur since committed is not touched during the PR workflow. |
| **OBG-007** | The over-budget justification text is required (non-empty, no minimum character count) when the PR is over-budget and `over_budget_mode` allows pre-approval (`fm_gm_opm` / `fm_only`). It is stored on the PR (`PurchaseRequest.over_budget_justification`) and displayed as a prominent amber block on the **PR Detail page** (visible to all approvers) and inline in the over-budget pre-approval task description (OBG-002). |
| **OBG-008** | If a PR is recalled or withdrawn after over-budget pre-approval has already been granted, the committed budget adjustment is reversed and the engine re-evaluates `pr.over_budget` (which `crud/pr.py:update()` recomputes on every edit) so the injected pre-approval chain reflects current state on resubmission. |
| **OBG-009** | In `hard_block` mode, submission of an over-budget PR must be refused at `submit` time with a user-readable error. The frontend must disable the Submit button and hide the justification field when this mode is active. |

**Value threshold escalation:**

If a PR's total value exceeds a configurable threshold (set in Admin Panel → Approval Workflows → PR → Value Thresholds), it automatically escalates to GM/OPM sign-off even when the budget account is not over budget. This escalation step uses the same `gm_or_opm` token and is resolved at runtime via Dept-GM/OPM Mapping.

**Rejection and resubmission rules:**

| FR ID | Requirement |
|-------|-------------|
| **REJ-001** | An approver at any step may **reject** a PR outright. Rejection is terminal — the PR moves to `rejected` status and cannot be resubmitted without creating a new PR. |
| **REJ-002** | An approver may **return** a PR for revision. The PR moves to `returned` status; the Requester receives a notification with the comment. The Requester may edit and resubmit, which restarts the workflow from Step 1. |
| **REJ-003** | When a returned PR is resubmitted, all prior approval history is retained in the History tab (with a "Returned — resubmitted" marker) and the new workflow run begins fresh from Step 1. |
| **REJ-004** | The Requester may withdraw (`cancel`) a PR at any status except `approved` or `rejected`. A cancelled PR cannot be reopened; a new PR must be created. |

### 3.3 Approval Workflow Engine

All approval-required actions across **all UniOps modules** (EPMS procurement documents, OA expense claims, OA payment applications) share the same configurable step-based approval engine hosted by `approval-api` (`:8003`).

The engine is designed around two concepts:

- **Workflow** — an ordered list of named approval steps, each specifying a role token.
- **Action Key** — a string identifier that binds a document/form type to a workflow. Every submission event carries an action key; the engine looks up the bound workflow and executes it.

Workflows and their bindings are configured in **Portal Admin Panel → Approval Workflows** (§5.8 of PRD-PORTAL) and stored in `CompanyConfig.workflow_defs[action_key]`.

#### 3.3.1 Action Keys

Every submittable document or form type has a unique action key. Each action key is independently bound to one workflow definition.

| Action Key | Document / Form | Module |
|------------|----------------|--------|
| `pr` | Purchase Request | EPMS |
| `po` | Purchase Order | EPMS |
| `pa` | Payment Application (PO-linked) | EPMS / OA |
| `pa_dir` | Direct Payment Application (no PO) | OA |
| `exp` | General Expense Claim | OA |
| `trv` | Travel Expense | OA |
| `mil` | Mileage Claim | OA |
| `cfm` | Custom Form (default workflow) | OA |
| `cfm_<code>` | Named Custom Form (per-form override) | OA |

If no workflow is bound to an action key, the engine defaults to a single `dept_manager` step. Admin can bind any action key to any workflow without code changes.

#### 3.3.2 Workflow Definition

Each workflow is an **ordered list of steps**, configured in Portal Admin Panel and stored in `CompanyConfig.workflow_defs[action_key]`. At runtime the engine reads this definition and routes tasks accordingly.

**Step node fields:**

| Field | Description |
|-------|-------------|
| `id` | Stable identifier for the step (e.g. `dept_manager`, `gm_or_opm`, `finance`) |
| `label` | Display label shown in Approval Timeline (e.g. "Department Manager", "GM / OPM") |
| `role` | Role token used for routing — see Role Tokens below |

**Role tokens and routing logic:**

| Token | Routes to |
|-------|-----------|
| `dept_manager` | User whose `department_id` matches the document creator's department |
| `gm` | User assigned as GM in Role Management |
| `opm` | User assigned as OPM in Role Management |
| `gm_or_opm` | **Auto-resolved at runtime**: looks up the document creator's department in Dept-GM/OPM Mapping → routes to GM or OPM accordingly. Admin only selects this single token; no per-department branching needed in the workflow definition |
| `procurement_manager` | User assigned as Procurement Manager in Role Management |
| `finance_manager` | User assigned as Finance Manager in Role Management |
| `finance_bp` | User(s) with Finance BP role |
| `ap_clerk` | User(s) with AP Clerk role |

**Example — PO workflow:**
```
Step 1: { id: "dept_manager",    role: "dept_manager",    label: "Department Manager" }
Step 2: { id: "proc_manager",    role: "procurement_manager", label: "Procurement Manager" }
Step 3: { id: "gm_or_opm",       role: "gm_or_opm",       label: "GM / OPM" }
```
When a PO from the Operations department reaches Step 3, the engine consults Dept-GM/OPM Mapping, determines Operations is mapped to OPM, and routes the task to the OPM — without any special configuration in the workflow definition.

**Default workflows (shipped, fully overridable):**

| Action Key | Default Steps |
|------------|--------------|
| `pr` | `dept_manager → gm_or_opm` |
| `po` | `procurement_manager → gm_or_opm` |
| `pa` | `dept_manager → gm_or_opm → finance_bp → finance_manager` |
| `pa_dir` | `finance_bp → finance_manager` |
| `exp` | `dept_manager → finance_bp` |
| `trv` | `dept_manager → finance_bp` |
| `mil` | `dept_manager → finance_bp` |
| `cfm` | `dept_manager` |

All defaults are stored as seed data in `CompanyConfig.workflow_defs` on first-boot. Admins may add, remove, or reorder steps freely without any code changes or service restart.

#### 3.3.3 State Transitions (all document types)

| Action | From | To | Task created |
|--------|------|----|-------------|
| `submit` | draft, returned | submitted | Task for step-0 approver |
| `approve` (non-final step) | submitted, in_review | in_review | Task for next step approver |
| `approve` (final step) | submitted, in_review | approved | PDF generated; document-specific post-approval tasks created (see §3.3.3) |
| `return` | submitted, in_review | returned | Revision task for requester |
| `reject` | submitted, in_review | rejected | None |
| `recall` | submitted, in_review | draft | None |
| `cancel` | draft, returned, submitted | cancelled | None |

#### 3.3.4 Per-Document Behaviour on Final Approval

| Document | On `approved` |
|----------|--------------|
| PR | PDF auto-generated and saved as attachment (§3.4); **`create_po` task created for all Procurement Officers**; immediate notification dispatched to each Procurement Officer via their preferred channel (Email / Teams / Both) with subject "Action Required: Create PO for PR {pr_number}" |
| PO | PDF auto-generated and saved as attachment; `place_order` task created for Procurement Officer (§3.12.3) |
| PA | PDF generated and saved as attachment (§3.4) |

#### 3.3.5 Multi-Role Auto-Skip

When a single user is assigned to multiple consecutive steps in the same workflow (as configured in Role Management), the engine **automatically cascades approvals** through all such consecutive steps upon a single approve action.

**Algorithm:**

1. Actor approves step N — a normal `ApprovalEvent` is recorded for step N.
2. Engine checks step N+1: if the actor is also the assigned approver for that step's role, it records an additional `ApprovalEvent` (action `"approve"`, comment `"Auto-approved (same approver holds both roles)"`) and advances to N+2.
3. Step 3 repeats until either a step is reached whose assigned approver is a different user, or the final step is passed (→ document moves to `approved`).

**Role resolution per workflow:**

| Role Token | Resolution method (applies to all action keys) |
|------------|------------------------------------------------|
| `dept_manager` | Department manager of the document creator's department (DB lookup by `created_by → department_id`) |
| `gm_or_opm` | Creator's department → Dept-GM/OPM Mapping → specific GM or OPM user |
| All other named roles | Role Management assignment (specific user assigned to that role) |

**`gm_or_opm` task assignment:** When a workflow step has `role = gm_or_opm`, the system looks up the document creator's department in the Dept-GM/OPM Mapping configuration. If the department maps to `"gm"`, the task is personally assigned to the GM user; if it maps to `"opm"`, the task is personally assigned to the OPM user. The task is **never broadcast** to all GM/OPM users — only the resolved specific user sees it in their Task Inbox.

**Audit trail:** Every auto-skipped step produces its own `ApprovalEvent` row, so the full history is preserved and visible in the Approval Timeline (shown with an "Auto-approved" indicator).

**System Admin exclusion / bypass:** System Admin users are never assigned as approvers in any workflow step definition, and the `system_admin` role cannot appear in a workflow definition. However, System Admin has an unconditional **bypass**: they may execute any approval action (`approve`, `return`, `reject`) on any document regardless of step assignment. The backend validates this bypass (WF-AUTH-003) and the frontend `canApprove` check grants it unconditionally when `user.role === "system_admin"`.

#### 3.3.6 Approval Authorisation Enforcement

The Approval Engine validates the actor's identity against the step's assigned approver **before** executing any approval action. Unauthorised approval attempts return HTTP 422.

| Step Role | Resolution | Who can approve |
|-----------|-----------|-----------------|
| `dept_manager` | Dept manager of the document creator's department (DB lookup by `created_by → department_id`) | Only that specific user |
| `gm_or_opm` | Creator's department → Dept-GM/OPM Mapping → GM or OPM user | Only the resolved GM or OPM user for that department |
| Named roles (`procurement_manager`, `finance_manager`, `finance_bp`, `ap_clerk`, etc.) | Role Management assignment | Only the user assigned to that role |
| `system_admin` | Always allowed to approve any step | System Admin bypass |

**`gm_or_opm` routing token vs. stored user role:** `gm_or_opm` is a **workflow routing token** — it is never stored as a user's `role` in the database. Users are stored with `role = "gm"` or `role = "opm"`. At task-creation time the engine resolves the token to a specific GM or OPM user via Dept-GM/OPM Mapping and sets `assigned_user_id` accordingly. The frontend must not compare `user.role === "gm_or_opm"` to determine button visibility; it must use the task-based check (WF-UI-001).

**`gm_or_opm` task routing:** When a workflow step uses the `gm_or_opm` token, the task is **personally assigned** (`assigned_user_id`) to either the GM or OPM user based on the document creator's department via Dept-GM/OPM Mapping. This ensures only the correct approver sees the task in their Task Inbox. Tasks are never broadcast to all GM/OPM users.

#### 3.3.7 Frontend `canApprove` Determination

The EPMS frontend (PR Detail, PO Detail, PA Detail pages) shows or hides the approval action bar (Approve / Return / Reject) based on a `canApprove` flag. This flag must not be derived from a simple `user.role === step.role` string comparison because:

- `gm_or_opm` is a routing token, not a stored user role — no user ever has `role = "gm_or_opm"`.
- Multi-role users (GM acting as dept_manager for their own department per §1.2) have a stored role that differs from the step token.

**Correct determination algorithm (implemented in frontend):**

```
canApprove =
  user IS authenticated
  AND document IS in an approvable status (submitted | in_review)
  AND (
    user has an active approve_<doc_type> task for this document  [primary — task-based]
    OR user.role === "system_admin"                               [bypass — WF-AUTH-003]
    OR (tasks still loading AND user.role matches step.role)      [provisional — prevent flicker]
  )
```

The **task-based check** is primary: the approval engine creates `approve_<doc_type>` tasks assigned to the exact user who should approve. If the current user has such a task for this document, they can approve — regardless of their stored role string. This is the only correct approach for `gm_or_opm` steps and multi-role users.

The backend always enforces WF-AUTH-001 on the action endpoint as a second gate.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **WF-AUTH-001** | The Approval Engine must reject `approve` actions where the actor is not the authorised approver for the current step, returning a 422 error with a descriptive message. |
| **WF-AUTH-002** | For `gm_or_opm` steps, task assignment must resolve to a specific user (GM or OPM) based on the document creator's department, not broadcast to the role group. |
| **WF-AUTH-003** | System Admin may approve any step regardless of role assignment. |
| **WF-UI-001** | The frontend `canApprove` flag for document detail pages must be derived from whether the current user holds an active `approve_<doc_type>` task for that document (via `GET /api/v1/tasks`), not from comparing `user.role` against the workflow step's role token string. |
| **WF-UI-002** | When the task list has not yet loaded (network in flight), the frontend may provisionally show the approval bar if `user.role` matches the current step's role token, to prevent button flicker. The backend (WF-AUTH-001) is the authoritative gate. |
| **WF-TASK-001** | `GET /api/v1/tasks` must expand the caller's effective role set before filtering. The JWT carries only the user's single base role; special roles (procurement_manager, gm, opm, finance_manager, vendor_manager, finance_bp) are secondary assignments in `CompanyConfig.role_management`. The endpoint reads Role Management and includes tasks for all roles the user holds — not just the base JWT role. Role-broadcast tasks (`assigned_user_id = NULL`) are matched against the full expanded role set; personally assigned tasks (`assigned_user_id = user_id`) are always included regardless of role. |

### 3.4 Document PDF Generation

All four procurement documents (PR, PO, GR, PA) generate PDFs server-side using ReportLab (Python). Each PDF is rendered using the corresponding template configured in **Admin Panel → PDF Templates** (§3.6.6).

**PR PDF:**
- Triggered automatically when PR reaches `approved` status (final workflow step), generated as a background task after the action response is returned
- Content: company logo (if enabled), PR number, meta grid (vendor, dept, cost center, budget code, dates), line items table (conditional columns for material ID / supplier ID), total amount, notes, delivery address, header/footer notes from template, T&C if enabled
- Uploaded to File Server; `storage_key` stored in `PrAttachment`; appears automatically in PR's Attachments tab

**PO PDF:**
- Generated automatically when PO reaches `approved` status (final workflow step)
- Uploaded to File Server; `storage_key` stored in `PoAttachment`; Procurement Officer downloads or sends it when placing the order
- **Header layout:** company logo (aspect-ratio preserved, longest side capped at 27 mm — 1.5× default size) on the left, "Purchase Order" document title below logo; PO number right-aligned top-right (no secondary "PURCHASE ORDER" sub-label)
- Content: meta grid (vendor, PO number, PO date, expected delivery date, delivery address, currency), line items table (description, qty, unit, unit price, line total), subtotal/tax/total, header/footer notes and T&C from PDF Template settings
- The meta grid shows **PO Number** (not PR Number); the PR linkage is an internal reference and is not printed on the vendor-facing document
- All line item cells are rendered as ReportLab `Paragraph` objects with `VALIGN=TOP`; long descriptions wrap within the Description column and never overflow into adjacent columns

**GR PDF:**
- Generated on GR acknowledgement
- Uploaded to File Server; `storage_key` stored in `GrAttachment`
- Content: GR number, PO reference, received items with quantities and conditions, receiver signature, header/footer/T&C from template

**PA PDF:**
- Generated on PA approval
- Uploaded to File Server; `storage_key` stored in `PaAttachment`
- Content: PA number, PO reference, payment amount, bank/payment details, approval record, header/footer/T&C from template

**Storage rule:** All attachments — whether auto-generated PDFs or user-uploaded files — are stored exclusively on the **File Server** (§3.4.1). The `*_attachments` tables store only metadata (`filename`, `content_type`, `file_size`, `storage_key`). No binary file data (`file_data`) is written to the database for any new attachment. The `file_data` column exists as a legacy fallback column and is not populated for new records.

| FR ID | Requirement |
|-------|-------------|
| **ATT-001** | Every attachment write (user upload or system-generated PDF) must upload the binary to the File Server and store the returned `storage_key` in the attachment record. Writing binary data to the `file_data` column is prohibited for new records. |
| **ATT-002** | Download endpoints must first check `storage_key` (proxy from File Server), then fall back to `file_data` (legacy records only). |
| **ATT-003** | The File Server (`file-api :8005`) must be running before any EPMS or OA service that generates attachments is started. |
| **PDF_TMPL-001** | Every server-side PDF generator (PR, PO, GR, PA) must read `CompanyConfig.pdf_templates[doc_type]` at generation time and apply: **Show Logo** (embed `logo_data_url` if true, aspect ratio preserved — default longest side capped at 18 mm, other side scaled proportionally; PO PDF uses 27 mm cap), **Header Note** (printed below the document title if non-empty), **Footer Note** (printed at the bottom of every page if non-empty), **Show Terms + Terms Text** (append a T&C section on the last page if enabled). |
| **PDF_TMPL-002** | If `CompanyConfig.pdf_templates` is absent or the key for the document type is missing, defaults apply: logo shown (if `logo_data_url` is set), no header/footer note, terms hidden. |
| **PDF_TMPL-003** | Template changes take effect immediately on the next PDF generated; existing stored PDFs are not regenerated. |
| **PDF_TMPL-004** | The shared utility module `app/services/pdf_template.py` provides `get_tmpl`, `build_logo`, `header_note_element`, `footer_note_element`, and `terms_element` helpers. All PDF generators must use these helpers — no duplicate template-reading logic in individual generators. |

#### 3.4.1 File Server

A dedicated file storage service manages **all** document attachments across every UniOps module (EPMS and OA). No binary file data is stored in the database — only metadata and `storage_key` references.

| Property | Value |
|----------|-------|
| Service | `file-api` (:8005) |
| Storage | Local filesystem organised as `{year}/{month}/{uuid}.{ext}` |
| Auth | Shared JWT (same secret as EPMS) |
| Max file size | 25 MB per file (configurable) |

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/files/v1/files` | Upload a file (multipart); query params: `doc_type`, `doc_id`, `service`; returns `{id, filename, content_type, file_size, download_url}` |
| `GET` | `/files/v1/files/{id}` | Download file (auth required) |
| `GET` | `/files/v1/files/{id}/meta` | File metadata |
| `DELETE` | `/files/v1/files/{id}` | Soft-delete (marks `is_deleted=true`, removes from disk) |

**Upload tagging — `doc_type` values by service:**

| Service | `doc_type` values |
|---------|------------------|
| `epms` | `pr`, `po`, `pa`, `gr` |
| `oa` | `invoice`, `exp`, `mil`, `trv`, `cfm` |

**Storage pattern (all modules):**

Every `*_attachments` table stores only metadata + `storage_key` (file-api UUID). The binary file lives exclusively on disk via file-api.

```
Upload flow:
  client → service API (POST /attachments, multipart)
    → service uploads to file-api (POST /files/v1/files)
    → file-api returns { id: UUID, ... }
    → service stores metadata + storage_key=id in DB
    → service returns AttachmentMeta to client

Download flow:
  client → service API (GET /attachments/{id}/file)
    → service reads storage_key from DB
    → service proxies GET /files/v1/files/{storage_key} from file-api
    → streams file bytes to client (auth enforced at both hops)
```

**Migration note:** EPMS Phase 1 attachment records that pre-date file-api integration may have a `file_data` (LargeBinary) column set and `storage_key` null. The download endpoint falls back to serving `file_data` for those legacy records. New uploads in all modules write exclusively to file-api.

**File Server FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **FS-001** | All new file uploads — user attachments, auto-generated PDFs, and invoice files — must be stored on the file server, not in the database. No new `LargeBinary` columns are allowed. |
| **FS-002** | File download must require a valid Bearer JWT at every hop (client→service, service→file-api); unauthenticated requests return 401. |
| **FS-003** | Each uploaded file must be tagged with `service` (epms/oa), `doc_type`, and `doc_id` for auditability. See tagging table above. |
| **FS-004** | Legacy records with `file_data` blobs (EPMS Phase 1) are served transparently from the database; no data migration is required at go-live. |
| **FS-005** | The `invoice_attachments` table (OA expense-api) must not use `LargeBinary`. All invoice file uploads go to file-api and are referenced by `storage_key`. Migration `0007_invoice_attachment_fileapi` removes the `file_data` column and adds `storage_key`. |
| **FS-006** | The `expense_attachments` table (OA expense-api) uses `file_id` (UUID string) as the file-api reference. No binary data is stored. Applies to EXP, MIL, TRV, and CFM claims. |

### 3.5 Parts Catalog

- Maintained by Warehouse Staff, Procurement Officer, System Admin
- Fields per part: Code, Category, Name, Description, Supplier, Supplier Item ID (ASIN/SKU/supplier catalog #), Unit Price, Unit, Active/Inactive
- **Data migration note:** Existing `supplier_part_no` column values are migrated into `supplier_item_id`; `supplier_part_no` column is then dropped
- CRUD: add, edit, inline delete with confirmation
- Filter by search text, category, active status
- CSV Import / Export

### 3.6 Admin Panel

Default section on load. Left-nav + right-content layout.

#### 3.6.1 Company Settings
- Company name, tagline, logo upload (base64 data URL)
- Live preview panel; changes reflected immediately in sidebar on save

#### 3.6.2 Security Settings

**MFA:**
- MFA toggle: on/off; takes effect on next login
- When disabled: `setMfaVerified()` called automatically after login

**SMTP Configuration:**
- Override server `.env` defaults with organisation-specific SMTP settings
- Fields: SMTP Host, Port, Username, Password, From Address, Use TLS/SSL toggle
- **Save SMTP** button persists settings to `CompanyConfig`
- **Test Connection**: enter a recipient email address → **Send Test Email** dispatches a test message using the saved settings to verify connectivity
- Used by the Notification Service (§3.9) for all outbound email; also used for PO order confirmation emails

**Password Policy:**
- **Password Expiry**: configurable dropdown — e.g. 30 / 60 / 90 days (recommended) / 180 days / Never
- When set, users are forced to change their password after the configured period
- Expiry status badge shown inline (e.g. "Expires every 90 days")

#### 3.6.3 User Management
- Full user table: name, email, role, department, Teams account, active status, created date
- Add user / edit user / deactivate / delete with confirmation
- **Reset Password**: portal modal (rendered via `createPortal` into `document.body`) to prevent browser password-manager autofill from targeting the user search box; `autoComplete="new-password"` on input; supports Enter key
- **CSV Export / Import**: `GET /users/export` → `users.csv`; `POST /users/import` (multipart) — matches by email, creates or updates; auto-generates password for new users if column blank

#### 3.6.3a Departments — MOVED to Portal → Admin → Departments

As of 2026-05-27 ownership of Departments has moved to **mdm-api** (the master-data service) and the management UI lives in **Portal → Admin → Departments**. The EPMS Admin Panel no longer exposes Departments. See [docs/superpowers/specs/2026-05-26-cost-center-dept-mdm-migration.md](../../docs/superpowers/specs/2026-05-26-cost-center-dept-mdm-migration.md) for the migration spec.

- Source of truth: `mdm-api` — endpoints `GET|POST|PATCH|DELETE /mdm/v1/departments`
- Write access: `system_admin | finance_manager | ap_clerk` (configured on the mdm-api endpoint dependencies)
- Reads: any authenticated user; `epms-api` retains read-only `GET /api/v1/departments` and `GET /api/v1/departments/{id}` for legacy EPMS pickers (PR Create, User forms)
- Delete guard: `DELETE` on mdm-api returns **HTTP 409** if the department has linked cost centers or assigned users; UI must offer "Deactivate instead" as the alternative
- Deactivate (soft): `PATCH` with `{ is_active: false }` — department is hidden from active-only lists but data is preserved

#### 3.6.3b Cost Centers — MOVED to Portal → Finance → Budget Config

As of 2026-05-27 ownership of Cost Centers has moved to **mdm-api** and the management UI lives in **Portal → Finance → Budget Config** (as a top-level section on the page). The EPMS Admin Panel no longer exposes Cost Centers.

- Source of truth: `mdm-api` — endpoints `GET|POST|PATCH|DELETE /mdm/v1/cost-centers`
- Write access: `system_admin | finance_manager | ap_clerk`
- Reads: any authenticated user; `epms-api` retains read-only `GET /api/v1/cost-centers` and `GET /api/v1/cost-centers/{id}` for legacy EPMS pickers (PR Create/Edit, Budget pages, Reports)
- Delete guard: `DELETE` on mdm-api returns **HTTP 409** if the cost center is referenced by any purchase request (`cost_center_id`) or budget L1 group (`cost_center_id`); UI must offer "Deactivate instead" as the alternative
- Deactivate (soft): `PATCH` with `{ is_active: false }` — cost center is hidden from active selectors but historical documents are unaffected

#### 3.6.4 Other Config Sections
- **Approval Workflows** — moved to **Portal Admin Panel → Approval Workflows** (§5.8 of PRD-PORTAL). Binds action keys to configurable step sequences; stored in `CompanyConfig.workflow_defs`; drives the shared approval engine for all modules (see §3.3 for full specification). EPMS Admin Panel shows a read-only summary with a link to Portal Admin for edits.
- **Dept → GM/OPM Mapping** — assigns each department a GM or OPM scope; determines which departments GM/OPM are responsible for approving and viewing budgets for. Default baseline mapping (all configurable):

  | Department | Default Scope |
  |-----------|--------------|
  | Engineering | OPM |
  | Production | OPM |
  | Supply Chain | OPM |
  | Marketing | GM |
  | Sales | GM |
  | HR | GM |
  | Finance | GM |
  | R&D | GM |
  | QA | GM |
  | BD (Business Development) | GM |
  | ECOM | GM |
  | GMO | GM |

  New departments must be assigned to GM or OPM scope before any PR, PO, or PA can be submitted from that department. Changes apply to new documents only; change history is logged.
- **Service GR SLA** — reminder/escalation day thresholds
- **GR Notification SLA** — acknowledgement reminder/escalation days
- **Prepayment Config** — max %, settlement SLA, blocking rules
- **Budget Config** — fiscal year dates; warning/over-budget alert thresholds (yellow %, red %); **Over-Budget Approval Mode** (`fm_gm_opm` / `fm_only` / `hard_block` — single source of truth for the over-budget pre-approval flow, see §3.2.6); Budget Accounts List with Import/Export CSV (see §3.6.4a)
- **Collection Config** — global toggle, reminder/escalation days
- **Role Management** — full role lifecycle management: create custom roles, assign built-in special roles to users, configure access permissions per role, and manage temporary delegation. See §3.6.7 for full specification.
- **Notification Settings** — default channel (Email / Teams / Both), Teams Webhook URL, per-event notification templates, test send button (see §3.9.4)
- **Currency Settings** — built-in currencies (CAD, USD, EUR, CNY) with enable/disable toggle and default selection; custom currencies can be added, edited, and deleted (each with code, symbol, and display name); custom currencies participate in the same enable/toggle/default flow as built-in ones
- **Email Settings** (renamed from "Email Templates" 2026-05-28) — owns (a) the **PO-to-vendor SMTP profile** `po_smtp_*` and (b) the PO email template (subject + body with `{variable}` substitution). See §3.6.5
- **PDF Templates** — header/footer/logo/terms per document type (PR, PO, GR, PA); used by the server-side PDF generator for all document PDFs (see §3.6.6)

#### 3.6.4a Budget Accounts List (within Budget Config)

The **Budget Config** section contains a **Budget Accounts** sub-panel that lets admins view, import, and export the full budget account hierarchy without leaving the Admin Panel.

**Account table (paginated by Cost Center):**

A Cost Center dropdown at the top of the table filters accounts to one CC at a time. Columns:

| Column | Description |
|--------|-------------|
| Code | L2 budget account code (e.g. `CRM00101`) |
| L1 Group | Parent L1 group name; shown only on the first account of each L1 group |
| Name | Budget account display name |
| Annual Budget | Annual budget amount (right-aligned) |
| Committed | Committed spend to date |
| Actual Spent | Actual spend to date; negative values displayed in red (credit notes / refunds) |
| *(edit icon)* | Pencil icon per row; click to enter inline edit mode |

**Inline editing:** clicking the pencil icon on any row switches that row to edit mode. The Name, Annual Budget, Committed, and Actual Spent fields become inputs. ✓ (save) and ✗ (cancel) buttons confirm or discard changes. Calls `PATCH /budget/accounts/{id}`. Rows re-sort by code after save.

**CSV Import rules:**
- Same account code is allowed across different Cost Centers (uniqueness enforced per L1 group, not globally)
- `actual_spent` may be negative (credit notes, refunds)
- Missing cost center codes generate row-level errors; valid rows still import

Rows are grouped by L1 within each Cost Center; L1 name is shown only on the first account of each group.

**Export CSV:**
- Button: **Export CSV** (with download icon)
- Downloads the full account list as a CSV file
- Columns: `code, l1Code, costCenterCode, name, annualBudget, committed, actualSpent`
- Calls `GET /budget/export` (existing endpoint)

**Import CSV:**
- Button: **Import CSV** (with upload icon)
- Opens a file picker (`.csv` only)
- On file select: calls `POST /budget/import` (multipart/form-data, existing endpoint)
- Shows a result toast: "Import complete — X created, Y updated" or error message
- Table refreshes automatically after a successful import

> This panel is the admin-facing counterpart to the read-only Budget Dashboard (§3.8). Admins manage the account hierarchy here; dept managers and finance users view aggregated budgets in the Budget Dashboard.

#### 3.6.5 Email Settings *(renamed from "Email Templates" 2026-05-28)*

Page houses two sub-sections, both configured in **EPMS Admin Panel → Email Settings**:

**1. PO Email SMTP** — Dedicated mailbox for vendor-facing PO emails (`po_smtp_*` on CompanyConfig). Fields: Host / Port / Username / Password (masked) / From Address / TLS. Any field left blank falls back to the **internal task notification SMTP** profile (Portal Admin → Notification Settings, `smtp_*`). Why split: many companies route procurement correspondence through a dedicated `purchasing@` mailbox while internal approvals flow through `noreply@`. "Test PO SMTP" calls `POST /api/v1/config/test-smtp` with `{ kind: "po" }` to exercise the full fallback path before issuing a real PO. See §PRD-EMAIL-SPLIT in PRD-PORTAL §5.7.

**2. PO Email Template** — Subject and body editable. Variable substitution uses `{variable_name}` syntax. Subject + body persist on `po_email_subject` / `po_email_body`. The template renders client-side before the Procurement Officer reviews it on the Send PO modal.

**PO Order Email** (sent to vendor when Procurement Officer places the order via email):
- Template configured in Admin Panel → Email Settings; subject and body editable per PO before sending
- Syntax: single-brace `{variable_name}` — e.g. `{po_number}`, `{vendor_name}`
- Available variables: `{po_number}`, `{po_date}`, `{vendor_name}`, `{vendor_email}`, `{expected_delivery}`, `{delivery_address}`, `{line_items}`, `{subtotal}`, `{tax}`, `{total}`, `{company_name}`, `{sender_name}`

**Notification Emails** — one template per event type (mirrors §3.9.2):

| Template Key | Trigger | Primary Variables |
|---|---|---|
| `pr_approval_request` | PR submitted / advanced to next step | `{pr_number}`, `{title}`, `{requester_name}`, `{amount}`, `{currency}`, `{step_label}`, `{link}` |
| `pr_approved` | PR reaches final approval | `{pr_number}`, `{title}`, `{link}` |
| `pr_returned` | PR returned for revision | `{pr_number}`, `{title}`, `{returned_by}`, `{comment}`, `{link}` |
| `pr_rejected` | PR rejected | `{pr_number}`, `{title}`, `{rejected_by}`, `{comment}`, `{link}` |
| `po_approval_request` | PO submitted for approval | `{po_number}`, `{vendor_name}`, `{amount}`, `{currency}`, `{link}` |
| `po_approved` | PO approved | `{po_number}`, `{vendor_name}`, `{link}` |
| `gr_created` | GR created — goods received | `{gr_number}`, `{po_number}`, `{link}` |
| `gr_acknowledgement_reminder` | GR acknowledgement SLA reminder | `{gr_number}`, `{days_waiting}`, `{link}` |
| `gr_collection_ready` | Goods ready for collection | `{gr_number}`, `{link}` |
| `service_gr_pending` | Service GR pending confirmation | `{gr_number}`, `{days_waiting}`, `{link}` |
| `invoice_matched` | Invoice matched to PO/GR | `{invoice_number}`, `{po_number}`, `{match_status}`, `{link}` |
| `pa_approval_request` | PA submitted for approval | `{pa_number}`, `{amount}`, `{currency}`, `{link}` |
| `pa_approved` | PA approved | `{pa_number}`, `{link}` |
| `prepayment_settlement_overdue` | Settlement SLA exceeded | `{pa_number}`, `{days_overdue}`, `{link}` |
| `sla_escalation` | SLA escalation triggered | `{document_type}`, `{document_number}`, `{days_waiting}`, `{link}` |
| `daily_pending_reminder` | Daily follow-up for open tasks | `{task_count}`, `{task_list}`, `{link}` |

All templates share common footer variables: `{company_name}`, `{company_logo_url}`, `{system_url}`.

#### 3.6.6 PDF Templates

Configurable in **Admin Panel → PDF Templates**. One template per document type: **PR, PO, GR, PA**.

Each template controls:

| Setting | Description |
|---------|-------------|
| **Show Logo** | Toggle — display company logo in PDF header |
| **Header Note** | Free-text printed below the document title (e.g. "Confidential — Internal Use Only") |
| **Footer Note** | Free-text printed at the bottom of every page |
| **Show Terms** | Toggle — include a Terms & Conditions section on the last page |
| **Terms Text** | Multi-line text for the T&C section (shown only when Show Terms is enabled) |

**Usage by PDF generator:**
- All server-side PDF generation for all four document types (PR, PO, GR, PA) reads the corresponding template from `CompanyConfig.pdf_templates[doc_type]` at generation time (see §3.4 — PDF_TMPL-001 through PDF_TMPL-004)
- If a template setting is not configured, sensible defaults apply (logo shown if `logo_data_url` is set, no header/footer note, terms hidden)
- Template changes take effect immediately on the next PDF generated — existing stored PDFs are not regenerated
- Implemented via the shared helper module `app/services/pdf_template.py` (`get_tmpl`, `build_logo`, `header_note_element`, `footer_note_element`, `terms_element`)

**Document coverage:**

| Document | PDF Trigger | Template Key | Implementation |
|----------|------------|--------------|----------------|
| PR | Auto-generated on final approval (background task) | `pr` | `app/services/pdf_pr.py` |
| PO | Auto-generated on final approval (background task) | `po` | `app/services/pdf_po.py` |
| GR | Generated on GR acknowledgement | `gr` | `app/services/pdf_gr.py` |
| PA | Generated on PA approval | `pa` | `app/services/pdf_pa.py` |

#### 3.6.7 Role Management

**Admin Panel → Role Management** is the central hub for all role-related configuration. It is divided into three sub-sections:

---

##### 3.6.7.1 Role List & Custom Roles

Displays all roles in the system — both **built-in roles** (Requester, Department Manager, GM, OPM, Procurement Officer, Procurement Manager, Warehouse Staff, AP Clerk, Finance BP, Finance Manager, Vendor Manager, System Admin) and any **custom roles** created by the System Admin.

**Add New Role:**
- Button: **+ New Role**
- Fields:
  | Field | Description |
  |-------|-------------|
  | Role Name | Unique display name (e.g. "Quality Auditor", "Plant Manager") |
  | Role Code | System-level identifier, auto-suggested from name (snake_case, e.g. `quality_auditor`); editable before save; immutable after first save |
  | Description | Optional free-text description of the role's purpose |
  | Base Permissions | Copy permissions from an existing role as a starting point (optional) |

- On save: the new role appears in the Role List and its permission row is added to the Access Control Matrix (§3.6.7.2) with all permissions **disabled** by default (unless "Copy from existing role" was selected).

**Edit Role:**
- Role Name and Description are editable at any time.
- Role Code is immutable after first save (used as a foreign key in user records and workflow definitions).

**Deactivate Role:**
- Inactive roles cannot be assigned to new users.
- Users currently holding the role retain it until reassigned. A warning lists affected users before deactivation.
- Built-in roles cannot be deactivated or deleted.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **RM-001** | System Admin can create a custom role with a unique name and auto-generated role code. Role code is immutable after first save. |
| **RM-002** | Custom roles appear in the User Management role dropdown and in the Access Control Matrix editor. |
| **RM-003** | Custom roles can be used as targets in Approval Workflow step definitions (same as built-in roles). |
| **RM-004** | Deactivating a role shows a warning listing all users currently assigned to that role. Deactivation does not automatically reassign those users. |
| **RM-005** | Built-in roles (Requester, Department Manager, GM, OPM, etc.) cannot be deactivated, deleted, or renamed. Their role codes are reserved system constants. |

---

##### 3.6.7.2 Access Control Matrix Editor

A visual grid editor that shows permissions for every role across all document/feature columns. System Admin can toggle individual cells on or off.

**Layout:**

- **Rows** — one per role (built-in + custom). Rows cannot be added or removed here; use Role List (§3.6.7.1) to manage roles.
- **Columns** — fixed permission scopes; columns cannot be renamed or reordered:

  | Column | Permission key | Scope |
  |--------|---------------|-------|
  | View PR | `view_pr` | Can open the Purchase Requisition list/detail (gates `GET /pr`, `GET /pr/{id}`). Per-role visibility scope (§1.3) still applies on top. |
  | View PO | `view_po` | Can open the Purchase Order list/detail (gates `GET /po`, `GET /po/{id}`). |
  | View GR | `view_gr` | Can open the Goods Receipt list/detail (gates `GET /gr`, `GET /gr/{id}`). |
  | View Invoice | `view_invoice` | Can open the Invoice list/detail (gates `GET /invoices`, `GET /invoices/{id}`). |
  | View PA | `view_pa` | Can open the Payment Application list/detail (gates `GET /pa`, `GET /pa/{id}`). |
  | Create PR | `create_pr` | Can create new Purchase Requisitions |
  | Approve PR | — | Can appear as a step approver in PR workflow |
  | Create PO | — | Can create new Purchase Orders |
  | Approve PO | — | Can appear as a step approver in PO workflow |
  | Create GR | `create_gr` | Can create Goods Receipt records |
  | Approve PA | — | Can appear as a step approver in PA workflow |
  | Invoice Upload | `invoice_upload` | Can upload and process vendor invoices |
  | Vendor Master | `vendor_master` | Can view/edit Vendor Master records |
  | Parts Catalog | `parts_catalog` | Can view/edit Parts Catalog |
  | Budget View | — | Can access Budget Dashboard |
  | Budget Admin | — | Can edit budget accounts and amounts |
  | Admin Panel | `admin_panel` | Can access Admin Panel |

  The five **View** columns are independent toggles per role. Disabling a View cell makes the corresponding list endpoint return an empty result set and the detail endpoint return HTTP 404 — there is no client-side data leak. View permissions stack with, not replace, the per-role scope defined in §1.3 (e.g. a Requester with `view_pr=✅` still sees only their own PRs).

- **Cells** — each cell is a **toggle switch** (enabled = ✅ / disabled = —). Clicking a cell flips its state. No inline text editing.

**Behaviour rules:**

- Changes take effect immediately on save (per-row **Save** button, or a global **Save All** button).
- Built-in role rows are partially locked: core permissions that are logically required for the role to function cannot be disabled (e.g. "Approve PO" cannot be disabled for the `procurement_manager` role; "Approve PA" cannot be disabled for the `finance_bp` role). Locked cells display a lock icon (🔒) and are not clickable.
- **Default locked View cells** (cannot be turned off via the Matrix):

  | Role | Locked View columns | Reason |
  |------|---------------------|--------|
  | `requester` | View PR | Requesters must always be able to see their own submissions |
  | `procurement_officer` | View PR, View PO, View GR | Required to create POs from approved PRs and follow up on receipts |
  | `procurement_manager` | View PR, View PO, View GR | Required to approve POs |
  | `warehouse_staff` | View GR | Required to record physical receipts |
  | `ap_clerk` | View Invoice, View PA | Required for invoice matching and `process_pa` completion |
  | `finance_bp` | View PA | Required to perform PA review |
  | `finance_manager` | View PA | Required to perform final PA approval |
- For multi-role users (§1.2), the Access Control Matrix reflects the **union** of all permissions held by that user across their active roles. For example, Finance Manager's "Approve PR" cell shows ✅ because they hold both the `finance_manager` role (over-budget PRs company-wide) and the `dept_manager` role for the Finance department (regular PRs for own dept). Both scopes are active simultaneously.
- Custom role rows are fully editable — any cell can be toggled.
- A **Reset to Default** button per row restores that role's permissions to the system defaults.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **RM-010** | The Access Control Matrix must display all roles (built-in + custom) as rows and all permission scopes as fixed columns. |
| **RM-011** | Each matrix cell is a toggle (on/off). System Admin can click any unlocked cell to change its state; clicking a locked cell shows a tooltip explaining why it cannot be changed. |
| **RM-012** | Column definitions (names and meaning) are fixed and cannot be added, removed, or renamed via the UI. Adding a new permission scope requires a software release. |
| **RM-013** | Changes to the matrix are saved explicitly (per-row Save or global Save All). Unsaved changes are highlighted in amber; navigating away with unsaved changes prompts a confirmation dialog. |
| **RM-014** | Permission changes take effect immediately for all users with that role upon save — no re-login required (enforced at the API level on each request). |
| **RM-015** | Every matrix change is recorded in the audit log: actor, timestamp, role, column changed, old value, new value. |
| **RM-016** | A **Reset to Default** action is available per row; it restores the built-in permission defaults. For custom roles, "default" means all permissions disabled (as at creation). Requires explicit confirmation before executing. |
| **RM-017** | The Access Control Matrix must include the five **View** columns (`view_pr`, `view_po`, `view_gr`, `view_invoice`, `view_pa`). When a View cell is unchecked for role R, every user holding R as their **only** active role receives an empty list response and HTTP 404 on the corresponding detail endpoint. |
| **RM-018** | View-permission evaluation must use the **union of all active roles** for the requesting user (base JWT role + special-role assignments from `CompanyConfig.role_management`). A user receives the permission if **any** of their active roles grants it. Implemented in `app/core/access_scope.py::_effective_permissions`. |
| **RM-019** | Default View settings for built-in roles must match the §1.3 visibility table. In particular `warehouse_staff` defaults to `view_gr` only and `vendor_manager` defaults to none of the five View columns. |

---

##### 3.6.7.3 Special Role User Assignment

Assigns specific named users to special roles (GM, OPM, Procurement Manager, Finance Manager, Vendor Manager) and manages temporary delegation.

| Config | Description |
|--------|-------------|
| GM Assignment | Assign one active user as GM. Optional absence backup. |
| OPM Assignment | Assign one active user as OPM. Optional absence backup. |
| Procurement Manager | Assign one or more users. |
| Finance Manager | Assign one or more users. |
| Vendor Manager | Assign one or more users. |
| Absence Cover | Assign a temporary delegate for any role with start + end date (see §1.5). |

These assignments layer on top of the user's base `dept_manager` role (see §1.2).

---

### 3.7 Dashboard

Role-specific dashboard via `DashboardRouter`. For multi-role users (e.g. a user who is both `dept_manager` and `gm`), the dashboard is determined by their **special role** (gm/opm/finance_manager/etc.) and must aggregate tasks from all active roles.

| Role(s) | Dashboard |
|---------|-----------|
| requester | RequesterDashboard: stats + task inbox + PR pipeline |
| dept_manager, gm, opm | ApproverDashboard: stats + **Pending Approvals** (functional Approve/Return/Reject buttons) + Budget Overview |
| procurement_officer, procurement_manager | ProcurementDashboard |
| warehouse_staff | WarehouseDashboard |
| ap_clerk | ApClerkDashboard |
| finance_manager | FinanceManagerDashboard |
| finance_bp | FinanceBpDashboard |
| vendor_manager | VendorManagerDashboard |
| system_admin | SystemAdminDashboard |

**Pending Approvals panel** (ApproverDashboard):
- Shows **only** documents where the current user has an open `approve_*` task assigned to them — either personally (`assigned_user_id = user_id`) or via role broadcast (`assigned_role` matches any of their effective roles). This is identical to the Task Inbox filter so the two surfaces always agree.
- A document must **not** appear in the Pending Approvals panel if the open approval task for its current step is assigned to a different user (e.g. an OPM-assigned PO must not appear on the GM's dashboard). Implemented via task subquery join in `_pending_approvals()` in `app/crud/dashboard.py`.
- Effective roles are expanded from Role Management config at request time (same `_effective_roles()` helper as Task Inbox) so multi-role users see tasks for all their active roles.
- Approve/Return/Reject buttons call the appropriate `POST /{doc_type}/{id}/action` endpoint; invalidate dashboard + document + task queries on success. Supported for PR, PO, and PA.

#### 3.7.1 Task Inbox — Task Types

The Task Inbox aggregates all open tasks assigned to the current user across all their active roles. The following task types are supported:

| Task Type | Assigned To | Trigger | Completed When |
|-----------|-------------|---------|----------------|
| `approval` | Approver at current workflow step (dept_manager, gm_or_opm, procurement_manager, finance_manager, finance_bp) | Document submitted or advanced to this step | Approver approves, returns, or rejects the document |
| `gr_acknowledgement` | Requester / Department acknowledgement role | Physical GR created by Warehouse Staff | Requester acknowledges receipt of GR notification |
| `goods_collection` | Requester | Warehouse marks goods ready for collection | Requester confirms goods collected |
| `service_gr_confirm` | Service requester (original PR requester) | PO for a service-type PR is in active status and expected completion date reached (or manually triggered) | Requester confirms service delivery via Service GR form |
| `settlement` | Requester | Prepayment PA approved; settlement SLA clock starts | Requester submits a Settlement PA to close the prepayment |
| `invoice_unmatched` | AP Clerk | Invoice uploaded but auto-matching failed to link to a PO | AP Clerk manually links invoice to PO/GR or raises exception |
| `invoice_exception` | AP Clerk | Three-way match completed with discrepancies (qty or price variance outside tolerance) | AP Clerk resolves exception (override or request credit note) |
| `create_po` | Procurement Officer | PR reaches `approved` status | Procurement Officer creates a PO linked to the approved PR. Task is auto-completed when the PO is created; stale tasks (PR already has a linked PO) are auto-resolved on next task list load. |
| `place_order` | Procurement Officer | PO reaches `approved` status (final workflow step) | Procurement Officer places the order with the vendor (via email or online) and PO transitions to `issued`. Backfilled automatically for any approved unplaced PO that has no open task. |
| `revise_po` | Original Requester (Procurement Officer who created the PO) | PO returned for revision by an approver | Procurement Officer edits and resubmits the PO |
| `returned_document` | Original Requester | Document returned for revision by an approver | Requester edits and resubmits, or withdraws the document |
| `collection_discrepancy` | Warehouse Staff + Requester | Requester reports a quantity discrepancy at goods collection | Warehouse Staff acknowledges and records discrepancy; Requester confirms resolution |
| `create_pa` | Requester | GR acknowledged and all conditions met for payment | Requester creates a Payment Application linked to the GR/PO |

**Task Inbox FR IDs:**
- **DSH-001** — Task Inbox must display all open tasks for the user across all active roles in a single consolidated list, sorted by due date ascending (SLA-based).
- **DSH-002** — Each task row must show: task type label, linked document number (hyperlinked), assigned role context, created date, and SLA due date (colour-coded: green > 3 days, amber 1–3 days, red overdue).
- **DSH-003** — Completed and cancelled tasks must be filterable (hidden by default; toggle to show).
- **DSH-004** — Every `create_po` task must be accompanied by an immediate outbound notification to the assigned Procurement Officer(s) via their preferred channel (see §3.9). The notification must include PR number, title, vendor name, amount, and a direct link to the PR detail page. Daily follow-up reminders apply until the task is completed (see §3.9.5).
- **DSH-005** — Every `place_order` task must be accompanied by an immediate outbound notification to the assigned Procurement Officer via their preferred channel. The notification must include PO number, vendor name, amount, and a direct link to the PO detail page.
- **DSH-006** — Dashboard Pending Approvals "Approve / Return / Reject" inline action buttons must work for all document types (PR, PO, PA). Clicking a button dispatches the action to the correct API endpoint based on `doc_type`; the dashboard refreshes on success.
- **DSH-007** — Approval buttons on a detail page (PO, PR, PA) must be visible to users who are assigned to the current workflow step via **Role Management** (e.g. a user assigned as Procurement Manager in Admin Panel → Role Management), not only to users whose base JWT role matches the step role.
- **DSH-008** — **Task personal isolation:** Tasks with a specific `assigned_user_id` are visible **only** to that user, regardless of their role. Tasks without `assigned_user_id` (broadcast tasks) are visible to all users whose JWT role matches `assigned_role`. This rule prevents tasks personally assigned to one Requester from appearing in the inbox of other Requesters with the same role.
- **DSH-009** — The header notification bell displays the real-time count of open tasks assigned to the current user, polling every 60 seconds. Clicking navigates to the Task Inbox page.

#### 3.7.2 Role Dashboard Layouts

Each role has a tailored dashboard layout. Multi-role users see the dashboard of their highest-priority special role, with tasks aggregated across all active roles.

**Requester Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | My Open PRs · My Pending Tasks · PRs Approved This Month · Prepayments Awaiting Settlement |
| To-Do List | All open tasks assigned to the user (approval, goods_collection, service_gr_confirm, settlement, returned_document, create_pa) sorted by SLA |
| PR Pipeline | Visual pipeline showing Requester's own PRs by status (Draft → Submitted → In Review → Approved → PO Created) |
| Quick Actions | New PR · View My PRs |

**Department Admin Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Department Open PRs · Pending Approvals In Department · Department Budget Utilisation % |
| To-Do List | Open tasks for the department (approval tasks if admin has dept_manager rights, returned_document tasks) |
| Department PR Summary | List of all PRs in the department with status and requester |
| Quick Actions | New PR · Department PR List |

**Department Supervisor Dashboard** *(optional/configurable — shown only when Dept Supervisor role is enabled)*

| Panel | Content |
|-------|---------|
| Stats Bar | Pending Supervisor Approvals · Supervisor Group Open PRs · Budget Utilisation % (supervisor group) |
| To-Do List | `approval` tasks assigned to this supervisor step |
| PR Queue | PRs awaiting supervisor review, sorted by submission date |

**Department Manager Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Pending Approvals · Department Open PRs · Department Budget Utilisation % · Overdue Approvals |
| To-Do List | `approval` tasks (dept_manager step) + `returned_document` tasks for own department |
| Budget Overview | Department budget summary (annual / committed / actual / available) with threshold alert banners |
| PR Activity Feed | Recent PR status changes in the department |

**GM & OPM Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Pending GM/OPM Approvals · Pending Dept Manager Approvals (own dept) · Mapped Departments Budget Health · Overdue Approvals |
| To-Do List | `approval` tasks for `gm_or_opm` step across all mapped departments + `dept_manager` step for own department |
| Budget Overview | Aggregated budget view for all mapped departments; drill-down by department |
| Approval Queue | Documents awaiting GM/OPM sign-off with value, department, and SLA |

**Finance BP Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Pending PA Approvals · PAs Approved This Month · Total PA Value This Month |
| To-Do List | `approval` tasks for `finance_bp` step |
| PA Queue | Payment Applications awaiting Finance BP review with amount, vendor, and due date |

**Procurement Officer Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Approved PRs Awaiting PO · Open POs · POs Awaiting Delivery · Invoices Unmatched |
| To-Do List | `po_create` tasks + `invoice_unmatched` tasks + `invoice_exception` tasks |
| PO Pipeline | Open POs by status (Draft → Approved → Issued → Partially Received → Closed) |
| Unmatched Invoices | Quick-access list of invoices needing manual matching |

**Warehouse Staff Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Pending GRs · Goods Ready for Collection · Collection Discrepancies Open |
| To-Do List | `gr_acknowledgement` tasks + `goods_collection` tasks + `collection_discrepancy` tasks |
| Incoming Deliveries | POs with expected delivery dates (next 7 days highlighted) |

**AP Clerk Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Invoices Uploaded Today · Unmatched Invoices · Match Exceptions · PAs Awaiting Prep |
| To-Do List | `invoice_unmatched` tasks + `invoice_exception` tasks |
| Invoice Queue | Uploaded invoices by match status (Matched / Partial / Unmatched / Exception) |
| Exception Details | Invoices with three-way match discrepancies and variance amounts |

**Finance Manager Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Over-Budget PRs Pending Approval · Budget Accounts in Breach · Monthly Actual vs Budget Variance |
| To-Do List | `approval` tasks for `finance_manager` step (over-budget pre-approval + PA final approval) |
| Budget Health | Company-wide budget utilisation; accounts over threshold highlighted |
| Over-Budget Queue | PRs flagged over-budget awaiting Finance Manager pre-approval |

**System Admin Dashboard**

| Panel | Content |
|-------|---------|
| Stats Bar | Active Users · Open Tasks (all roles) · System Errors (last 24 h) · Pending Config Items |
| To-Do List | Any tasks assigned to System Admin role |
| System Health | API response time, DB connection status, notification service status, last backup timestamp |
| User Activity | Recent logins, failed login attempts, password reset requests |
| Quick Actions | User Management · Role Management · Approval Workflows · Notification Settings |

**Dashboard FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **DSH-010** | All dashboard stats bars must refresh every 60 seconds automatically without a full page reload (polling or WebSocket). |
| **DSH-011** | The To-Do List on every dashboard must be the same data source as the Task Inbox page (§3.7.1); completing a task from the dashboard must remove it from the Task Inbox and vice versa. |
| **DSH-012** | The Pending Approvals panel must show **only** documents for which the current user holds an open `approve_*` task (personal assignment or role broadcast). The backend must filter using the same task-subquery logic as the Task Inbox — never by document status alone. A document assigned to User A's approval step must not appear on User B's dashboard even if both users share the same base role. |
| **DSH-012** | Multi-role users must see a unified To-Do List aggregating tasks from all active roles; the role context column indicates which role the task relates to. |
| **DSH-013** | Overdue tasks (SLA date < today) must be highlighted in red in all dashboard To-Do Lists. |
| **DSH-014** | Dashboard panels that reference budget data must respect role-based budget visibility rules (§3.8). |
| **DSH-015** | The GM & OPM dashboard's Pending Approvals count must include both the `gm_or_opm` step tasks AND the `dept_manager` step tasks for the GM/OPM's own department. |
| **DSH-016** | Role dashboards must be configurable in terms of which panels are visible; System Admin can hide/show panels per role in Admin Panel → Dashboard Config. |
| **DSH-017** | All dashboard Quick Action buttons must be conditionally rendered based on the user's active permissions (e.g. "New PR" shown only to roles that can create PRs). |

### 3.8 Budget Dashboard

**Data loading strategy (performance-first):**
- Page load: `GET /budget/summary` (pre-aggregated CC-level totals; lightweight) → populates summary cards and L1-level over/near-budget alerts
- Account Breakdown table: lazy — `GET /budget/l1?cost_center_id=xxx` fetched only when a specific Cost Center is selected (React Query cached per CC, 30 s stale)
- "All Cost Centers" selected: `GET /budget/l1` (all) fetched lazily; frontend aggregates account values by code across CCs before rendering

**Role-based visibility:** users who are special-role assignees in Role Management (Finance Manager, GM, OPM, etc.) are granted full-access view regardless of their JWT base role.

| Role / Assignment | Visibility |
|------|-----------|
| finance_manager, gm, opm, finance_bp, ap_clerk, system_admin + Role Management assignees | All cost centers; department and cost center filter dropdowns |
| dept_manager (not specially assigned) | Own department only; no filter dropdown |

**Summary cards:** Total Annual Budget / Committed / Actual Spent / Available — derived from `GET /budget/summary` pre-aggregated data.

**Over-budget and near-budget alerts:** detected at L1 group level from summary data; shown as tag pills above the account table.

**Account Breakdown table:**
- When "All Cost Centers" selected: L1 groups with per-account rows; numeric values are the sum of that account code across all cost centers
- When a specific CC selected: L1 groups with actual per-account rows for that CC only
- Each L1 group is collapsible; per-account rows show utilisation progress bars
- Threshold config from `CompanyConfig.budget_admin_config` (yellow %, red %)

- See §9 for full Committed vs Actual tracking definitions and alert threshold table

### 3.9 Notifications

Every task generated by the system (approval requests, revision requests, GR acknowledgements, collection confirmations, SLA escalations, etc.) triggers an outbound notification to the assigned user(s). Users can choose their preferred channel; Admins set the company default.

#### 3.9.1 Notification Channels

| Channel | Mechanism | Config |
|---------|-----------|--------|
| **Email** | SMTP via configured server (from Admin Panel → Company Settings → SMTP) | Subject + body template per event type |
| **Microsoft Teams** | Adaptive Card posted to user's Teams via Incoming Webhook or Bot | Card includes document number, title, amount, action buttons (deep-link to EPMS) |

#### 3.9.2 Notification Events

All task-generating events send a notification to the task's `assigned_user_id` (or all users with `assigned_role` if no specific user is set):

| Event | Recipient(s) | Content |
|-------|-------------|---------|
| PR submitted for approval | Step-0 approver — for `dept_manager` step: the dept_manager of the requester's own department; for other roles: the specific assigned user or all users with that role | PR number, title, amount, requester, link to PR detail |
| PR advanced to next approval step | Next step approver (same routing logic as above) | Same as above + step label |
| PR approved (final) | PR requester | Approval confirmed, link to PR |
| PR approved (final) — PO creation trigger | All active Procurement Officers | "Action Required: Create PO for PR {pr_number}" — PR number, title, vendor, amount, currency, link to PR detail page |
| PR returned for revision | PR requester | Returned by whom, comment, link to edit |
| PR rejected | PR requester | Rejected by whom, comment |
| PO submitted for approval | Procurement Manager | PO number, vendor, amount, link |
| PO approved | Procurement Officer who created it | PO number, confirmation |
| GR created (goods received) | The specific Requester who raised the linked PR (resolved via GR → PO → PR → `created_by`) | GR number, items, link to confirm |
| GR acknowledgement SLA reminder | Same Requester | Days waiting, link |
| GR collection ready | Same Requester | Items available for collection |
| Service GR pending confirmation | Same Requester | SLA reminder, link to confirm |
| Invoice matched to PO | The specific Requester who raised the linked PR (resolved via Invoice → PO → PR → `created_by`); a `create_pa` task is created (or re-notified if one already exists) for that Requester | Invoice number, PO number, action link to create PA |
| Invoice matched to PO/GR (exception) | AP Clerk | Match result, exceptions if any |
| PA submitted for approval | Finance BP / Finance Manager | PA number, amount, link |
| PA approved | PA requester | Approval confirmed |
| Prepayment settlement overdue | Requester | Settlement SLA exceeded |
| SLA escalation | Manager / escalation role | Document overdue, days waiting |

#### 3.9.3 User Notification Preference (Profile Settings)

Each user can override the company default in their **Profile** page:

| Setting | Options |
|---------|---------|
| Notification channel | Email only / Teams only / Both / None |

- Preference stored on the `User` record (`notification_channel`)
- "None" silences all outbound notifications for that user (in-app Task Inbox still populated)

#### 3.9.4 Admin Panel — Global Notification Settings (new section)

In **Admin Panel → Notification Settings**:
- **Default channel**: Email / Teams / Both — applied to all users who have not set a personal preference
- **Teams Webhook URL**: company-level or per-department webhook endpoint
- **Notification templates**: per-event subject and body (Email) or card text (Teams); support variables like `{document_number}`, `{requester_name}`, `{amount}`, `{link}`
- **Test send**: button to send a test notification to the admin's own email/Teams
- SMTP configuration is shared from **Admin Panel → Security Settings → SMTP Configuration** (not duplicated here)

#### 3.9.5 Delivery Rules

**Notification timing — two-phase model:**

1. **Immediate**: when a task is created (e.g. PR submitted, approval step advanced, GR received), a notification is dispatched immediately to the assigned user(s)
2. **Daily follow-up**: a scheduled job runs once per day (default 08:00); for every task that is still **open/pending**, it sends a reminder notification — until the task is completed or cancelled

This means every pending task generates at most one immediate alert, then one reminder per day until resolved. There is no user-configurable frequency toggle — all users receive both phases.

**Other rules:**
- Notifications are sent **asynchronously** — document state transitions are not blocked by notification failure
- Failed deliveries are logged; retry up to 3 times with exponential backoff
- If a user has no email address and no Teams webhook, notification is silently skipped (in-app Task Inbox still populated)
- Daily follow-up job respects the user's channel preference (Email / Teams / Both / None)

### 3.10 Vendor Master

The Vendor Master stores all approved suppliers and is maintained by the Vendor Manager and Procurement Officer (read access for other procurement roles).

**Vendor record fields:**

| Field | Description |
|-------|-------------|
| Vendor ID | System-generated unique identifier |
| POID | Up to 3 uppercase alphanumeric characters (e.g. `ABC`, `V01`). Must be unique across all vendors. Used as a component in PO document numbering (see §3.12.5). |
| Name | Legal vendor name |
| Category | Vendor category (e.g. Raw Materials, Services, Spare Parts, Logistics) |
| GST/HST Reg No | Canadian GST/HST registration number |
| Province | Operating province (used for tax jurisdiction) |
| Payment Terms | Standard payment terms (e.g. Net 30, Net 60, Prepayment) |
| Bank Details | Bank name, account number, transit number, institution number (encrypted at rest) |
| Primary Contact | Contact name, email, phone |
| Status | Active / Inactive |
| Max Prepayment % | Maximum prepayment percentage permitted for this vendor (1–100; overrides system-wide Prepayment Config cap when set). Stored as `max_prepayment_pct NUMERIC(5,2)` on the vendor record. Only shown in the vendor form when Payment Terms = Prepayment. |

**Vendor Master FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **VND-001** | Every active vendor must have a unique POID of 1–3 uppercase alphanumeric characters. The system must reject duplicates and enforce format validation on save. |
| **VND-002** | Inactive vendors cannot be selected on new PRs or POs but remain visible in historical documents. |
| **VND-003** | Bank details are stored encrypted (AES-256); only users with Vendor Manager or Finance Manager role can view decrypted bank details. |
| **VND-004** | Vendor Master supports CSV import/export for bulk onboarding. Import must validate POID uniqueness and format; invalid rows are rejected with a per-row error report. |
| **VND-005** | Any change to a vendor record (including status change) is recorded in an immutable audit log with the actor's name, role, timestamp, and before/after values for changed fields. |
| **VND-006** | When `max_prepayment_pct` is set on a vendor, it overrides the system-wide prepayment cap (Admin Panel → Prepayment Config) for all Prepayment PAs linked to that vendor's POs. If not set (NULL), the system-wide cap applies. |

### 3.11 Project Code Management

Project codes are maintained by the Procurement Officer, Finance Manager, and System Admin. They are required for Type 6 (Project-Related) PRs.

**Project record fields:**

| Field | Description |
|-------|-------------|
| Project Code | System-generated or manually assigned unique code (e.g. `PROJ-2026-001`) |
| Project Name | Descriptive name |
| Department | Owning department |
| Budget | Total approved project budget |
| Start Date | Project start date |
| End Date | Project end date (may be open-ended) |
| Status | Active / Completed / On Hold / Cancelled |
| Description | Free-text project description |

**Project Code FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PRJ-001** | Only Active projects can be selected on a new PR. Completed, On Hold, and Cancelled projects appear in historical documents but are not selectable. |
| **PRJ-002** | Each project has a budget total; committing a Type 6 PR against a project reduces the project's available budget. If the commitment would exceed the project budget, the over-budget warning is triggered (same as account-level over-budget, §3.2.1). |
| **PRJ-003** | Project budget utilisation is visible in the Budget Dashboard with a dedicated Project Budget tab (visible to Finance Manager, Finance BP, GM, OPM, System Admin). |
| **PRJ-004** | Projects support CSV import/export for bulk creation. |
| **PRJ-005** | When a project's end date is reached, the system sends a reminder notification to the **Project Owner** (not an auto-status change). The notification informs them that the project end date has passed and asks whether the project can be closed. The Project Owner reviews the project status and, if ready, manually navigates to the Project detail page and clicks **Close Project** to transition the status to `Completed`. The system does not change the project status automatically. |
| **PRJ-006** | All changes to project records are recorded in the audit log (actor, timestamp, before/after values). |

### 3.12 Purchase Orders (PO)

#### 3.12.1 PO Creation

A PO is created by a Procurement Officer, typically triggered by a `po_create` task following a PR's final approval (§3.7.1). A PO can also be created independently of a PR.

**PO fields:**

- PO Number (auto-generated on submission — see §3.12.5)
- Linked PR (optional; one PR → one PO; pre-fills vendor, line items, cost center, and other fields — see PO-002)
- Vendor (required; selected from active Vendor Master)
- Delivery address
- Expected Delivery Date (inherited from linked PR's "Required By" date if PR is linked)
- Payment terms (defaulted from vendor; overridable)
- Currency (defaulted from company settings)
- Line items: Description, Material ID (Types 1 & 3), Supplier Item ID, Qty, Unit, Unit Price, Line Total
- Delivery instructions / notes
- Prepayment Required checkbox (inherited from linked PR's `is_prepaid` flag if PR is linked; editable)
- Attachments
- Budget account code (inherited from linked PR if applicable)
- Project code (inherited from linked PR if applicable, Type 6 only)

**PO Creation FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PO-001** | A PO must be linked to at most one approved PR. A PR can have at most one non-cancelled PO. |
| **PO-002** | When a PO is created from an approved PR, the following fields are pre-populated from the PR and are editable by the Procurement Officer before submission: line items, vendor, cost center, budget code, procurement type, currency, delivery address, **Expected Delivery Date** (from PR's "Required By" date), and **Prepayment Required** flag (from PR's `is_prepaid`). |
| **PO-003** | The Procurement Officer can add, remove, or modify line items on the PO regardless of the linked PR's line items; the final PO reflects the actual order placed with the vendor. |
| **PO-004** | PO total value is computed from line items and displayed in real time. |
| **PO-005** | Currency selection is limited to currencies enabled in Admin Panel → Currency Settings. |
| **PO-006** | A PO cannot be submitted if the linked vendor is Inactive. |
| **PO-007** | Delivery address defaults to the company address (from Company Settings) but can be overridden per PO. |
| **PO-008** | File attachments (e.g. vendor quote, specification sheet) can be added to a PO before submission. |
| **PO-009** | A draft PO can be saved without submitting. Only submitted POs enter the approval workflow. |
| **PO-010** | Procurement Officer receives a notification when a PO they created reaches `approved` status, with a prompt to place the order. |
| **PO-011** | On PO final approval, the system generates the PO PDF and saves it as an attachment; a `place_order` task is created for the Procurement Officer. The vendor is **not** contacted automatically — the Procurement Officer initiates order placement manually (see §3.12.3). |
| **PO-012** | When a PR reaches `approved` status (final approval step), the system automatically creates a `create_po` task in the Task Inbox of **every active user with the `procurement_officer` role** and dispatches an immediate notification via their preferred channel (Email / Teams / Both). The notification subject is "Action Required: Create PO for PR {pr_number}" and the body includes PR number, title, vendor name, amount, currency, and a deep link to the PR detail page. If no Procurement Officer users exist, the task is still created (assigned by role) and will appear when a user with that role next logs in. |
| **PO-013** | When a PO is created from an approved PR, the open `create_po` task for that PR is automatically marked complete. Any stale `create_po` tasks (where the PR already has a linked PO) are auto-resolved on the next task list load. |
| **PO-014** | When a PO transitions to `approved` status, the system creates a `place_order` task for the Procurement Officer. For approved POs that have no open `place_order` task (e.g. data migrated before this feature was deployed), the task is auto-created on the next task list load (backfill logic). |

#### 3.12.2 PO Approval Flow

PO approval uses the configurable step engine (§3.3). The default PO workflow is:

```
Step 1: procurement_manager  — Procurement Manager
Step 2: finance_manager      — Finance Manager
```

The workflow can be reconfigured in Admin Panel → Approval Workflows → PO. Both `procurement_manager` and `finance_manager` are assigned to specific users via **Admin Panel → Role Management** (not base system roles). The approval button on PO Detail is visible to: (a) users whose base JWT role matches the step role, (b) users assigned to the step role via Role Management, or (c) System Admin.

#### 3.12.3 Order Placement & Tracking

**Order placement flow:**

When a PO reaches `approved` status, the Procurement Officer receives a `place_order` task. On the PO Detail page a **Place Order** action button is shown. The Procurement Officer chooses one of two methods:

```
PO Approved → PO PDF saved as attachment
    → place_order task created for Procurement Officer
    → Procurement Officer opens PO Detail → clicks "Place Order"
         ↓
    ┌────────────────────────────────────────┐
    │  Place Order via Email                 │
    │  Place Order Online                    │
    └────────────────────────────────────────┘
         ↓ Email                  ↓ Online
    Email Composer          Mark as Placed
    (pre-filled)            (manual confirmation)
         ↓ Send
    Email sent to vendor
    PO status → issued
```

**Option A — Place Order via Email:**

1. System fetches the vendor's primary contact email from the Vendor Master (§3.10).
2. System opens an **Email Composer** panel pre-filled with:
   - **To**: vendor primary contact email (editable)
   - **Subject**: pre-filled from the PO Order Email template (§3.6.5); editable
   - **Body**: pre-filled from the PO Order Email template with variables substituted (`{po_number}`, `{po_date}`, `{vendor_name}`, `{expected_delivery}`, `{delivery_address}`, `{line_items}`, `{subtotal}`, `{tax}`, `{total}`, `{company_name}`, `{sender_name}`); editable
   - **Attachment**: PO PDF (auto-attached; cannot be removed)
3. Procurement Officer reviews and edits the email as needed, then clicks **Send**.
4. On successful send: PO status → `issued`; email transmission date/time and recipient recorded on the PO; `place_order` task marked complete.

**Option B — Place Order Online:**

For vendors whose orders are placed via an external portal or procurement platform (e.g. supplier website, EDI portal).

1. Procurement Officer selects **Place Order Online**.
2. A confirmation dialog is shown: "Confirm you have placed the order with [Vendor Name] via their online portal."
3. Procurement Officer enters an optional reference number (e.g. portal order ID) and clicks **Confirm**.
4. PO status → `issued`; reference number stored on PO record; `place_order` task marked complete.

**Order Placement & Tracking FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PT-001** | On PO final approval, the system creates a `place_order` task for the Procurement Officer and sends a notification (Email/Teams per preference) with a deep-link to the PO Detail page. |
| **PT-002** | The PO Detail page displays a **Place Order** button for POs in `approved` status visible to Procurement Officer and Procurement Manager. The button is hidden for all other statuses. |
| **PT-003** | **Place Order via Email**: the system fetches the vendor's primary contact email from the Vendor Master. If no email is configured on the vendor record, the system shows a warning and prompts the user to add it before proceeding. |
| **PT-004** | The Email Composer pre-fills subject and body from the PO Order Email template (§3.6.5). All fields (To, Subject, Body) are editable before sending. The PO PDF is always attached and cannot be removed. |
| **PT-005** | On email send success: PO status transitions to `issued`; email recipient, timestamp, and email subject are recorded on the PO. On send failure: an error is shown; PO status remains `approved`; the Procurement Officer can retry. |
| **PT-006** | **Place Order Online**: Procurement Officer confirms manual placement via a confirmation dialog. An optional portal order reference number can be recorded. PO status transitions to `issued` on confirmation. |
| **PT-007** | Procurement Officer can update the PO's expected delivery date and add tracking notes at any time after issuance. |
| **PT-008** | When a GR is created against a PO, the PO status updates to `partially_received` (if not all line items are fully received) or `fully_received`. |
| **PT-009** | A PO can be manually closed by the Procurement Officer or Procurement Manager (`closed` status). Closing a PO with open GRs or unmatched invoices requires an explicit override confirmation. |
| **PT-010** | PO list view supports filtering by status (draft, submitted, in_review, approved, issued, partially_received, fully_received, closed, cancelled), vendor, date range, and linked PR. |

#### 3.12.4 Document Chain

**Document relationship model:**

| Relationship | Cardinality | Notes |
|---|---|---|
| PR → PO | 1 : 1 | Each approved PR produces exactly one PO |
| PO → GR | 1 : N | A PO can have multiple GRs (partial deliveries, service confirmations) |
| PO → Invoice | 1 : N | A PO can receive multiple invoices (e.g. monthly recurring, partial deliveries) |
| Invoice → PA | N : M | One or more invoices can be bundled into one PA; a PA must cover invoices from the same PO |

**Document chain tree (example for a PO with partial deliveries):**

```
PR (Purchase Requisition)
└── PO (Purchase Order)
    ├── GR-1 (Goods Receipt — items A, B, C)
    ├── GR-2 (Goods Receipt — items D, E)
    ├── Invoice-1 (items A, B, C)
    ├── Invoice-2 (items D, E)
    ├── PA-1 (Standard — covers Invoice-1 + Invoice-2 together)
    └── PA-PP (Prepayment PA — created before GR if prepayment vendor)
        └── PA-BAL (Balance / Settlement PA — closes prepayment)
```

Note: GRs, Invoices, and PAs are **siblings** under the PO — Invoices and PAs are not nested under GRs. PAs reference which invoices they cover but all hang off the PO in the tree.

**Document Chain UI component:**

A unified `DocumentChainTree` component is rendered in the right sidebar of PR, PO, and PA detail pages. It shows:
- **Ancestor cards** (full-width link cards): documents higher in the chain (e.g. on PA detail: PR → PO above the anchor)
- **Anchor card** (highlighted): the current document
- **Child rows** (tree branches below): all GRs, Invoices, and PAs linked to the anchor PO, with the current document's row highlighted if it appears in the list

The component is shared across PR Detail, PO Detail, and PA Detail pages, using a `currentType` prop (`'pr' | 'po' | 'pa'`) to determine which document is the anchor and which are ancestors/children.

#### 3.12.5 PO Document Numbering

POs are assigned a system-generated number on submission using the format:

```
PO-[POID]-YYMM-SEQ
```

Where:
- `[POID]` is the vendor's unique 1–3 character POID (from Vendor Master, §3.10)
- `YYMM` is the two-digit year and two-digit month of submission (e.g. `2604` for April 2026)
- `SEQ` is a zero-padded sequential counter per vendor per month, starting at `01`

Example: `PO-ABC-2604-03` (third PO issued to vendor ABC in April 2026).

The PO number is assigned by the backend at the `submit` action and is never editable.

### 3.13 Goods Receipt (GR)

GRs are created by Warehouse Staff (physical goods) or triggered by the Requester (service confirmation / collection). Three distinct paths are supported.

#### 3.13.1 Path A — Physical GR

Physical GRs are created by Warehouse Staff when goods arrive at the warehouse.

| FR ID | Requirement |
|-------|-------------|
| **GR-P-001** | Warehouse Staff can create a GR against any PO in `issued`, `partially_received`, or `fully_received` status. |
| **GR-P-002** | GR form fields: PO reference (searchable dropdown), actual delivery date, carrier / delivery reference, per-line received quantity, per-line condition (Good / Damaged / Rejected), per-line notes, overall GR notes, photo attachments. |
| **GR-P-003** | Received quantity can be less than the PO line quantity (partial delivery). If all lines are fully received, the PO transitions to `fully_received`; otherwise `partially_received`. |
| **GR-P-004** | Lines received with `Damaged` or `Rejected` condition are flagged; the Procurement Officer is notified to raise a return or credit note with the vendor. |
| **GR-P-005** | GR number is system-generated on save (format: `GR-YYYYMMDD-XXXX`, daily sequence). |
| **GR-P-006** | On GR creation, the system generates a `gr_acknowledgement` task for the Requester (or department acknowledgement role) and sends the GR notification (see §3.13.2). |
| **GR-P-007** | GR PDF is generated on acknowledgement completion (not on GR creation). |

#### 3.13.2 Requester GR Notification Specification

When a physical GR is created, the following notification and acknowledgement process applies:

> **Recipient identification:** The GR notification and all GR tasks are sent to the **specific Requester who created the PR** linked to the GR (resolved via: GR → PO → PR → `created_by`). Notifications are never broadcast to all users with the Requester role.

| Step | Actor | Action | Channel |
|------|-------|--------|---------|
| 1 | System | Sends GR notification to the PR's Requester immediately on GR creation | Email + Teams (per user preference) |
| 2 | Requester | Opens Task Inbox → clicks `acknowledge_gr` task → views GR detail | In-App |
| 3 | Requester | Acknowledges receipt (confirms they are aware goods have arrived) | In-App |
| 4 | System | Marks `acknowledge_gr` task complete; GR status → `acknowledged` | Automated |
| 5 | System | If acknowledgement not received within SLA threshold 1: sends reminder notification | Email + Teams |
| 6 | System | If acknowledgement not received within SLA threshold 2: escalates to Department Manager | Email + Teams |

**GR Notification FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **GR-N-001** | GR notification email/Teams card must include: GR number, PO number, vendor name, delivery date, list of received items with quantities and conditions, and a deep link to the GR detail page. |
| **GR-N-002** | SLA thresholds for GR acknowledgement (Threshold 1 reminder days, Threshold 2 escalation days) are configurable in Admin Panel → GR Notification SLA. |
| **GR-N-003** | Escalation notification (Step 6 above) is sent to the Department Manager of the Requester's department, with a copy to the Requester. |
| **GR-N-004** | Once the Requester acknowledges the GR, no further reminder or escalation notifications are sent for that GR. |
| **GR-N-005** | If the Requester is absent and has a temporary delegate assigned (§1.5), GR notifications and `acknowledge_gr` tasks are routed to the delegate. |

#### 3.13.3 Path B — Service Receipt Confirmation

For POs linked to Type 4 (Service) PRs, the Requester confirms service delivery via a Service GR form. **Two initiation paths are supported:**

**Path B-1 — Requester-initiated (self-created service GR):**
The Requester proactively creates a service GR directly from the PO Detail page or the GR List page once the service is complete. This requires no prior warehouse staff action.

**Path B-2 — System-triggered (task-driven):**
The system creates a `service_gr_confirm` task when the PO's expected completion date is reached or when the Procurement Officer manually triggers confirmation. The Requester confirms from the task.

**Service GR creation access:**

| Actor | Condition | Action |
|-------|-----------|--------|
| Requester | Service PO (type 4) in `approved`, `issued`, or `partially_received` status | Can create service GR directly via "Confirm Service" button on PO Detail page or "New GR" on GR List page |
| Warehouse Staff / Procurement Officer / System Admin | Any PO in `issued` or `partially_received` status | Can create GRs (physical or service) per normal GR creation flow |

**Service GR status flow:**

```
Requester creates Service GR → pending_ack
    → Requester clicks "Confirm Service Completion" on ServiceGrConfirmPage
         → GR status: confirmed (acknowledge + confirm merged into one step)
```

*Note: When a Requester creates and immediately confirms a service GR, the `acknowledge` and `confirm` steps are merged into a single action (confirm from `pending_ack` is allowed for service GRs). For task-driven confirmations (Path B-2), the GR may already be in `collection_pending` before the Requester confirms; both states are accepted by the confirm action.*

**Service GR form fields:**
- PO reference (pre-filled if accessed from task or PO Detail)
- Service completion date
- Quality assessment (Satisfactory / Has Issues / Reject Service)
- Acceptance notes (required, min 10 characters)
- Issue details (required if quality ≠ Satisfactory)
- Supporting documents (optional — completion certificate, photos, reports; max 25 MB per file)

**Service GR SLA ladder:**

| SLA Level | Days After Expected Completion | Action |
|-----------|-------------------------------|--------|
| Reminder 1 | Threshold 1 (configurable) | Send reminder to Requester |
| Reminder 2 | Threshold 2 (configurable) | Send second reminder to Requester |
| Escalation | Threshold 3 (configurable) | Notify Department Manager + Procurement Officer |

SLA thresholds are configured in Admin Panel → Service GR SLA.

**Service GR FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **GR-S-001** | A `service_gr_confirm` task is created for the Requester when: (a) the PO's expected completion date is reached, or (b) the Procurement Officer manually triggers confirmation. |
| **GR-S-002** | The Requester receives an immediate notification when the `service_gr_confirm` task is created. |
| **GR-S-003** | SLA reminders and escalations follow the ladder defined in Admin Panel → Service GR SLA. |
| **GR-S-004** | The Requester can confirm service delivery with notes and attachments. Confirmation transitions the Service GR to `confirmed` status. |
| **GR-S-005** | If the Requester indicates the service was not completed satisfactorily, they can flag it as `rejected` with a comment; the Procurement Officer is notified to liaise with the vendor. |
| **GR-S-006** | On Service GR confirmation, the GR transitions to `confirmed` status and PO received quantities are updated. No `create_pa` task is created at this point — PA creation is triggered by invoice matching (see §3.14.2). |
| **GR-S-007** | Service GR PDF is generated on confirmation. |
| **GR-S-008** | The Requester role has access to the "New GR" / "Confirm Service" entry point from: (a) the PO Detail page (button visible when PO is a service type and status is `approved` or later), and (b) the GR List page ("New GR" button). The GR creation form shows only eligible service POs in the dropdown. |
| **GR-S-009** | For Requester-created service GRs, the `acknowledge` step is automatically merged with the `confirm` step. A single "Confirm Service Completion" action from `pending_ack` status is sufficient to move the GR to `confirmed` without a separate acknowledgement step. |

#### 3.13.4 Path C — Physical Goods Collection Confirmation

For POs where goods are collected by the Requester directly (rather than delivered to the warehouse), a Collection path applies. This path is enabled globally in Admin Panel → Collection Config.

**Physical GR vs Collection — two-step distinction:**

| Aspect | Path A (Physical GR) | Path C (Collection) |
|--------|---------------------|---------------------|
| Who receives goods | Warehouse Staff | Requester collects directly |
| GR creator | Warehouse Staff | System auto-creates a preliminary GR record on collection confirmation |
| Acknowledgement | Requester acknowledges Warehouse GR | Not required (Requester is the collector) |
| Collection task | No | Yes — `goods_collection` task assigned to Requester |

**Updated flow for Path C:**

1. Warehouse Staff marks goods as **ready for collection** (updates GR record to `ready_for_collection`)
2. System creates a `goods_collection` task for the Requester and sends a notification
3. Requester visits warehouse, collects goods, then confirms collection via the Collection Confirmation form
4. If discrepancies found (wrong qty, wrong items, damage), Requester reports a discrepancy (creates a `collection_discrepancy` task)
5. On clean confirmation (or discrepancy resolution), GR transitions to `collected` and PO received quantities are updated; PA creation is triggered later by invoice matching (see §3.14.2)

**Collection Confirmation form fields:**
- GR reference (pre-filled)
- Collection date (defaults to today)
- Per-line collected quantity (editable; defaults to ready quantity)
- Per-line condition (Good / Damaged / Wrong Item)
- Discrepancy notes (required if any line is not Good)
- Collector signature / confirmation checkbox

**Collection SLA ladder:**

| SLA Level | Days After "Ready for Collection" | Action |
|-----------|----------------------------------|--------|
| Reminder 1 | Threshold 1 (configurable) | Remind Requester to collect |
| Reminder 2 | Threshold 2 (configurable) | Second reminder |
| Escalation | Threshold 3 (configurable) | Notify Department Manager + Warehouse Staff |

SLA thresholds configured in Admin Panel → Collection Config.

**Discrepancy handling:**

- A `collection_discrepancy` task is assigned jointly to Warehouse Staff and the Requester
- Warehouse Staff must acknowledge the discrepancy and update the GR record
- Requester confirms the resolution
- Unresolved discrepancies after SLA threshold escalate to Department Manager and Procurement Officer

**Collection FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **GR-C-001** | Collection path is enabled/disabled globally via Admin Panel → Collection Config toggle. When disabled, all GRs follow Path A (Warehouse → Acknowledgement). |
| **GR-C-002** | Warehouse Staff can mark individual GR line items as ready for collection independently (partial readiness supported). |
| **GR-C-003** | System creates `goods_collection` task immediately when any lines are marked ready for collection. |
| **GR-C-004** | Requester receives an immediate notification (Email/Teams per preference) when goods are ready for collection. |
| **GR-C-005** | Collection Confirmation form must default collected quantity to the ready quantity; Requester can adjust downward only (cannot collect more than marked ready). |
| **GR-C-006** | If collected quantity differs from ready quantity, Requester must enter a discrepancy note (min 20 characters). |
| **GR-C-007** | `collection_discrepancy` task is auto-created when any line is collected with a condition other than Good, or when collected quantity < ready quantity. |
| **GR-C-008** | Collection SLA reminders and escalations follow the ladder configured in Admin Panel → Collection Config. |
| **GR-C-009** | On clean collection (all lines Good, quantities match), GR immediately transitions to `collected` and PO received quantities are updated. No `create_pa` task is created at this point — PA creation is triggered by invoice matching (see §3.14.2). |
| **GR-C-010** | Collection discrepancy resolution requires explicit acknowledgement from both Warehouse Staff and the Requester before the GR transitions to `collected`. |

### 3.14 Invoice Management

#### 3.14.1 Invoice Upload & Auto-Routing

Invoices are uploaded by the AP Clerk. The system attempts to auto-match the invoice to a PO using a three-layer matching strategy.

**Three-layer matching strategy:**

| Layer | Method | Match Fields |
|-------|--------|-------------|
| **Primary** | AI / OCR extraction + exact match | PO number extracted from invoice text matched against PO table |
| **Fallback** | Fuzzy match on vendor + amount | Vendor name similarity ≥ 90% AND total amount within ±2% tolerance |
| **Manual** | AP Clerk selects PO manually | AP Clerk searches and links PO from invoice detail page |

**Invoice upload and routing flow:**

```
AP Clerk uploads invoice (PDF/image)
        ↓
OCR & AI parsing (async, < 30 s SLA)
        ↓
Primary match attempted
        ↓ (success)               ↓ (fail)
Invoice linked to PO        Fallback match attempted
        ↓                         ↓ (success)       ↓ (fail)
3-Way Match triggered     Invoice linked to PO   invoice_unmatched task
                                  ↓                  → AP Clerk manual link
                          3-Way Match triggered
```

**Invoice Upload & Routing FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **INV-001** | Supported upload formats: PDF, JPG, PNG, TIFF. Maximum file size: 20 MB. |
| **INV-002** | OCR and AI field extraction must complete within 30 seconds (P95). Extracted fields: invoice number, vendor name, invoice date, due date, line items (description, qty, unit price, total), overall total, GST/HST amount. |
| **INV-003** | Extracted fields are displayed to the AP Clerk for review and correction before the invoice is confirmed. |
| **INV-004** | Primary match: if a PO number is found in the invoice text and matches an existing PO, the invoice is automatically linked. |
| **INV-005** | Fallback match: if primary fails, the system attempts fuzzy vendor + amount match. If a single match above the threshold is found, it is auto-linked with a "suggested match — confirm required" flag; AP Clerk must confirm. |
| **INV-006** | If no match is found after both layers, an `invoice_unmatched` task is created for the AP Clerk. |
| **INV-007** | AP Clerk can manually link an unmatched invoice to any PO in `issued`, `partially_received`, or `fully_received` status. |
| **INV-008** | An invoice can be linked to at most one PO. A PO can have multiple invoices (for partial deliveries). |
| **INV-009** | Invoice status lifecycle: `uploaded` → `pending_ocr` → `extracted` → `matched` / `unmatched` → `exception` / `cleared`. |

#### 3.14.2 Auto PA Task Trigger

When an invoice is matched to a PO (either by automatic three-way match or by AP Clerk manual match/exception resolution), the system creates or re-notifies a `create_pa` task for the PR Requester.

**Trigger event:** Invoice `status` transitions to `matched` (linked to a PO) — whether via auto-match, manual link, or exception resolution.

**Recipient identification:** The system resolves the target Requester via the chain: Invoice → `po_id` → PO → `pr_id` → PR → `created_by`. The `create_pa` task is assigned to that specific Requester.

**Deduplication rule:** Only one open `create_pa` task is maintained per PO at a time (keyed on `document_type="po"`, `document_id=<po_id>`, `is_completed=False`). If an open task already exists for this PO, the system re-notifies the Requester with the new invoice reference instead of creating a duplicate task.

**Non-auto-completion:** `create_pa` tasks are **never automatically completed** by the system. This supports recurring invoice scenarios (e.g. a monthly software subscription PO that generates a new invoice and PA each month). The Requester dismisses or completes the task manually when they create a PA.

**Auto PA Task FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **INV-010** | When an invoice is matched to a PO, the system identifies the Requester who raised the linked PR (via Invoice → PO → PR → `created_by`) and creates a `create_pa` task assigned to that specific Requester. |
| **INV-011** | If an open `create_pa` task already exists for the same PO, no duplicate task is created. The system re-notifies the Requester with the new invoice number (the existing task remains open). |
| **INV-012** | The `create_pa` notification sent to the Requester includes: invoice number, PO number, vendor name, and a deep link to create a PA. |
| **INV-013** | If the invoice match is performed by the AP Clerk manually (after an exception), the same `create_pa` task logic applies. |

#### 3.14.3 Three-Way Match

The three-way match verifies that the invoice aligns with the PO and the GR across quantity, price, and vendor.

| FR ID | Requirement |
|-------|-------------|
| **INV-3WM-001** | Three-way match compares: (1) PO line quantities and unit prices vs. (2) GR received quantities and conditions vs. (3) Invoice line quantities and unit prices. |
| **INV-3WM-002** | A match is considered **clean** if: all invoice line totals are within the configured tolerance of PO line totals (default ±2%), and all invoiced quantities are ≤ GR received quantities for the same items. |
| **INV-3WM-003** | If the match is clean, the invoice transitions to `cleared` and the auto PA task logic (§3.14.2) is triggered. |
| **INV-3WM-004** | If discrepancies exist (quantity or price variance outside tolerance), the invoice transitions to `exception` and an `invoice_exception` task is created for the AP Clerk. |
| **INV-3WM-005** | AP Clerk can resolve an exception by: (a) overriding the match if the variance is acceptable (with justification note), or (b) requesting a credit note from the vendor (invoice remains in `exception` pending credit note receipt). |
| **INV-3WM-006** | Match tolerance percentages are configurable in Admin Panel → Invoice Settings. |
| **INV-3WM-007** | The actor who performed the match is displayed by full name (not UUID) per **UI-USER-001**. Name stored as `matched_by_name`; fallback to UUID for legacy records. |

#### 3.14.4 Invoice List — Role-Based Visibility

| Role | Invoices visible |
|------|-----------------|
| `requester` | Own uploads (`uploaded_by = user_id`) **OR** invoices whose `po_id` traces to a PO linked to any PR the requester created (`PR.created_by = user_id`) |
| `dept_manager` / `department_admin` | Invoices whose `po_id` traces to a PO linked to a PR whose `cost_center_id` belongs to the user's department |
| `gm` / `opm` | Invoices in the PO chain of departments mapped to their role in `CompanyConfig.dept_gm_opm_mapping` |
| All other roles | **All** invoices |

**Chain resolution:** `invoice.po_id` → `purchase_orders.pr_id` → `purchase_requests.cost_center_id` → `cost_centers.department_id`

**Implementation:** `build_scope()` in `epms-api/app/core/access_scope.py` returns `po_subq` (visible PO id subquery). `invoice.get_all()` accepts `po_ids_subq` and `own_uploads_user_id` parameters. For requester, both are applied with `OR` logic. Single-record `GET /invoices/{id}` enforces the same scope via `invoice.is_visible()`, returning HTTP 404 for out-of-scope records.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **INV-VIS-001** | `GET /api/v1/invoices` must restrict results for `requester`, `dept_manager`, `department_admin`, `gm`, and `opm`. All other roles see all invoices (subject to INV-VIS-004). |
| **INV-VIS-002** | A `requester` sees own uploads **and** chain invoices via the existing `build_scope()` / `po_ids_subq` mechanism, using OR logic. |
| **INV-VIS-003** | `GET /api/v1/invoices/{id}` must enforce the same scope for restricted roles and return HTTP 404 (not 403) for out-of-scope records. |
| **INV-VIS-004** | Both endpoints must additionally honour the `view_invoice` permission from the Access Control Matrix (§3.6.7.2). When the union of the caller's active-role permissions does not include `view_invoice`, the list returns empty and the detail returns HTTP 404, irrespective of the role's scope. |

#### 3.14.5 Invoice Edit

Invoices in editable statuses (`unmatched`, `exception`, `matched`) can be corrected by authorised users without deleting and re-uploading.

**Who can edit:** Any user with the `invoice_upload` permission (same permission as create/delete), plus System Admin.

**Editable statuses:** `unmatched`, `exception`, `matched`. Invoices in `approved` or `paid` status cannot be edited.

**Editable fields:**
- Vendor Invoice # (vendor is read-only after creation)
- Invoice Date
- Due Date
- Pre-tax Amount
- Tax Amount
- Currency
- Notes
- Line Items (add / edit / remove rows; row totals auto-computed from qty × unit price)
- Goods Receipts (multi-select, when invoice has a linked PO — see INV-EDIT-008)

**Re-match on save:** If the invoice already has a linked PO, saving the edit automatically re-runs the three-way match. `gr_value` is recomputed as the sum of all selected GRs' line-item totals. Status (`matched` / `exception`) and variance are recalculated from the updated `total_amount` and GR selection.

**UI entry point:** An **Edit** button (pencil icon) is shown in the Invoice Detail page header when the invoice is in an editable status and the user has `invoice_upload` permission. Clicking it opens an inline edit form in-place (Details tab), replacing the read-only view. Save / Cancel controls are shown at the bottom of the form.

**Data model:** The `invoices` table stores multiple linked GRs in a `gr_ids` JSONB array (UUID strings). For backward compatibility, `gr_id` and `gr_number` always reflect the first selected GR; `gr_number` may be a comma-separated list when multiple GRs are linked.

| FR ID | Requirement |
|-------|-------------|
| **INV-EDIT-001** | `PATCH /api/v1/invoices/{id}` accepts partial updates for: `vendor_invoice_number`, `amount`, `tax_amount`, `currency`, `invoice_date`, `due_date`, `notes`, `line_items`, `gr_ids`. All fields are optional; only provided fields are updated. |
| **INV-EDIT-002** | Editing is permitted only when invoice status is `unmatched`, `exception`, or `matched`. Attempting to edit an `approved` or `paid` invoice returns HTTP 409. |
| **INV-EDIT-003** | `total_amount` is recomputed as `amount + tax_amount` on every save; the client-submitted total is ignored. |
| **INV-EDIT-004** | If the invoice has a linked `po_id` at save time, the backend automatically re-runs the three-way match using the GR selection from `gr_ids` in the PATCH body (if provided), otherwise the invoice's existing `gr_ids`. An empty `gr_ids` list clears all GR links. Status and variance are recomputed. |
| **INV-EDIT-005** | After a successful edit+rematch on a previously-matched invoice, the `create_pa` task notification logic is **not** re-triggered (the task already exists from the original match). |
| **INV-EDIT-006** | The Edit button is visible in the Invoice Detail header when: invoice status ∈ {`unmatched`, `exception`, `matched`} AND the user has `invoice_upload` permission. |
| **INV-EDIT-007** | The Edit form is rendered inline within the Details tab. Switching to another tab while editing navigates away from the form (changes are discarded only on Cancel). |
| **INV-EDIT-008** | When the invoice has a linked PO, the edit form shows a **GR multi-select checklist** listing all non-cancelled GRs for that PO (GR number, title, status). The currently linked GRs are pre-checked. The user may check/uncheck any combination; zero checked = no GR linked. A badge shows the count of selected GRs. The selection is submitted as `gr_ids` (list of UUID strings) in the PATCH body. |
| **INV-EDIT-009** | `gr_value` on the invoice is always the **sum** of all selected GRs' line-item totals. `gr_id` and `gr_number` store the first selected GR for backward compatibility; `gr_number` may contain a comma-separated list when multiple GRs are selected. |
| **INV-EDIT-010** | The `POST /invoices/{id}/match` endpoint (used during invoice upload) continues to accept a single `gr_id` for backward compatibility. The `gr_ids` multi-select field is exclusive to the `PATCH` edit flow. |

### 3.15 Payment Applications (PA)

#### 3.15.1 PA Types

| PA Type | Description | Prerequisite | `pa_type` value |
|---------|-------------|-------------|-----------------|
| **Standard** | Full or partial payment for delivered and invoiced goods/services | PO in active status + at least one invoice matched to the PO | `regular` |
| **Prepayment** | Advance payment to vendor before delivery | Vendor Max Prepayment % > 0; no other open Prepayment PA on same PO | `prepayment` |
| **Settlement** | Closes out a prepayment PA when actual delivery value differs from prepayment | Open prepayment PA on same PO; `prepayment_pa_id` required | `settlement` |
| **Balance** | Remaining balance after a prepayment, once goods/services delivered | Prepayment PA settled; `prepayment_pa_id` required; only after Settlement PA approved | `balance` |

**PA linkage:** Settlement and Balance PAs carry a `prepayment_pa_id` foreign key referencing the original Prepayment PA (`payment_applications.id`). The backend validates this linkage at creation time — the referenced PA must be of type `prepayment` and belong to the same PO.

#### 3.15.2 Standard PA

**Who can create a Standard PA:**

Requester, AP Clerk, Finance BP, Finance Manager, and System Admin. The PA is typically created by the Requester in response to a `create_pa` task notification (triggered when an invoice is matched to their PO).

**Prerequisites for Standard PA creation:**

| Prerequisite | Condition |
|-------------|-----------|
| Linked PO | PO must be in `issued`, `partially_received`, or `fully_received` status |
| Invoice | At least one invoice linked to the PO must be matched to that PO |
| Invoice PO consistency | All invoices included in a single PA must belong to the same PO |
| Existing PA | No unresolved PA exists for the same PO/Invoice combination |

**3-step PA creation wizard:**

1. **Step 1 — Select PO**: Requester selects the PO for this payment application.
2. **Step 2 — Select Invoices & Verify Amounts**: Requester selects one or more invoices linked to the PO; system validates all selected invoices belong to the same PO; system displays the invoice summary; Requester reviews and confirms.
3. **Step 3 — Payment Details**: Requester enters payment details (see charge breakdown fields below) and submits.

**Charge breakdown fields:**
- Subtotal (auto-calculated from selected invoice lines)
- GST/HST amount (extracted from invoice; editable)
- Other charges (e.g. freight, handling — free-text label + amount)
- Discount/credit note amount (negative)
- **Total Payment Amount** (computed)
- Payment method (EFT / Cheque / Wire Transfer)
- Vendor bank details (read-only, from Vendor Master)
- Payment reference / memo
- Attachments

**PA line items:**

Each PA includes a line item breakdown mirroring the invoice lines selected, with:
- Description, Qty, Unit Price, Line Total, Invoice reference

**Standard PA FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PA-001** | PA number is system-generated on submission: `PA-YYYYMMDD-XXXX` (see §3.15.5). |
| **PA-002** | A PA can cover multiple invoices from the same PO in a single payment. |
| **PA-003** | All invoices selected for a PA must belong to the same PO. The system enforces this validation server-side and returns a 422 error if any invoice's `po_id` does not match the PA's `po_id`. |
| **PA-004** | Total payment amount cannot exceed the sum of invoice totals for the selected invoices. |
| **PA-005** | Payment method and vendor bank details are recorded on the PA record and included in the PA PDF. |
| **PA-006** | PA PDF is generated on final approval and stored as an attachment. |
| **PA-007** | Requester receives a notification when their PA is approved (final step). |
| **PA-008** | On final PA approval, a `process_pa` task is created and assigned to the `ap_clerk` role. The task appears in the AP Clerk's Task Inbox with the PA number, vendor, and payment amount. |
| **PA-009** | AP Clerk marks the PA as `processed` by clicking "Mark as Processed" on the PA Detail page. This action completes the `process_pa` task and marks all linked invoices as `paid`. Only users with the `ap_clerk` role may perform this action. |

#### 3.15.2.1 PA Edit

- Available for PAs in `draft` or `returned` status
- Pre-populates all fields from existing PA data; the linked PO is locked (read-only)
- Invoice selection, GR selection, line items, and all charge breakdown fields remain editable
- **"Save Draft"** — PATCH the PA and navigate back to the detail page; status stays `draft` / `returned`
- **"Save & Resubmit"** — PATCH the PA then immediately trigger the `submit` action; PA advances to `submitted` and re-enters the approval workflow from Step 1

| FR ID | Requirement |
|-------|-------------|
| **PA-EDIT-001** | Edit is accessible only when the PA is in `draft` or `returned` status. Any other status redirects to the detail page with an error. |
| **PA-EDIT-002** | The linked PO field is locked and cannot be changed when editing an existing PA. |
| **PA-EDIT-003** | When a returned PA is resubmitted via "Save & Resubmit", all prior approval history is retained in the History tab and the new workflow run begins from Step 1. |

#### 3.15.3 PA Approval Flow

| Step | Approver | Notes |
|------|----------|-------|
| 1 | Requester | Creates and submits the PA |
| 2 | Department Manager | Approves PA for their department |
| 3 | GM / OPM | Signs off via `gm_or_opm` token (resolved at runtime) |
| 4 | Finance BP | Finance Business Partner review |
| 5 | AP Clerk (prep) | AP Clerk marks as "ready for payment" (not a formal approval step — operational confirmation) |
| 6 | Finance Manager | Final payment authorisation |

PA approval uses the configurable step engine (§3.3). The default PA workflow implements the steps above and is configurable in Admin Panel → Approval Workflows → PA.

#### 3.15.4 Prepayment PA

A Prepayment PA allows the Requester to apply for an advance payment to a vendor before goods are delivered or services rendered.

**3-stage prepayment flow:**

```
Stage 1 — Prepayment PA
  Requester creates Prepayment PA → Approval Flow → Approved → AP Clerk processes payment

Stage 2 — Delivery / Service Completion
  Vendor delivers goods (GR Path A/C) or confirms service (GR Path B)
  Invoice received and matched

Stage 3 — Settlement PA
  Requester creates Settlement PA to reconcile prepayment against actual delivery value
  If actual < prepayment: vendor issues credit note / refund
  If actual > prepayment: Balance PA created for the difference
```

**Prepayment scenarios:**

| Scenario | Actual Value vs Prepayment | Resolution |
|----------|---------------------------|-----------|
| Exact match | Equal | Settlement PA closes out the prepayment |
| Under-delivery | Actual < Prepayment | Settlement PA + vendor credit note / partial refund |
| Over-delivery | Actual > Prepayment | Settlement PA for prepayment + Balance PA for difference |
| Cancellation | Delivery not made | Cancellation PA; vendor refund required |

**Prepayment FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PP-001** | Prepayment PA can only be created if the vendor's effective prepayment cap is > 0. The effective cap is `vendor.max_prepayment_pct` if set, otherwise `CompanyConfig.prepayment_config.max_prepayment_pct`. Enforced in `POST /pa` — returns HTTP 422 if cap ≤ 0. |
| **PP-002** | The requested `prepayment_pct` cannot exceed the vendor's effective cap (vendor-level override takes priority over system cap). Enforced in `POST /pa`. |
| **PP-003** | The requested `prepayment_pct` cannot exceed the system-wide cap in Admin Panel → Prepayment Config, regardless of vendor override. Enforced in `POST /pa` as a second independent check. |
| **PP-004** | A PO can have at most one open (non-cancelled, non-rejected) Prepayment PA at a time. `POST /pa` with `pa_type=prepayment` queries for existing open prepayment PAs on the same PO and returns HTTP 409 if found. |
| **PP-005** | Prepayment PA follows the same approval flow as Standard PA (§3.15.3). ✅ Implemented — all PA types share the same workflow engine. |
| **PP-006** | On Prepayment PA approval, the system starts the settlement SLA clock (configurable in Admin Panel → Prepayment Config). ⚠️ Pending — `expected_settlement_date` field exists on the PA model; automatic SLA enforcement not yet implemented. |
| **PP-007** | If the settlement SLA is exceeded, the system sends a `prepayment_settlement_overdue` notification to the Requester and Department Manager, and creates a `settlement` task if one does not exist. ⚠️ Pending — notification template exists; background scheduler not yet implemented. |
| **PP-008** | While a Prepayment PA is open (status not `cancelled`, `rejected`, or `processed`), no other PA type can be created on the same PO. Enforced in `POST /pa` — returns HTTP 409 with the blocking PA number. |
| **PP-009** | Settlement and Balance PAs must supply `prepayment_pa_id` referencing the original Prepayment PA on the same PO. Backend validates the FK at creation time (HTTP 422 if missing or mismatched). Stored as `payment_applications.prepayment_pa_id UUID → payment_applications(id)`. |
| **PP-010** | Balance PA can only be submitted after the Settlement PA is approved. ⚠️ Pending — `prepayment_pa_id` linkage exists; submit-time status check not yet implemented. |
| **PP-011** | All prepayment-related PAs (Prepayment, Settlement, Balance) are linked in the PO Document Tree (§3.12.4) and visible together on the PO Detail page. ✅ Implemented — `DocumentChainTree` renders all PA types with their `pa_type` label. |

#### 3.15.5 PA Document Numbering

Payment Applications are assigned a system-generated number on submission:

```
PA-YYYYMMDD-XXXX
```

Where `YYYYMMDD` is the submission date (UTC) and `XXXX` is a zero-padded daily sequence (resets to `0001` each calendar day). Example: `PA-20260410-0007`.

Applies to all PA types (Standard, Prepayment, Balance, Settlement). The number is assigned by the backend at the `submit` action and is never editable.

#### 3.15.6 PA Status Lifecycle

| Status | Description |
|--------|-------------|
| `draft` | PA created but not yet submitted; editable |
| `submitted` | Submitted; awaiting first approver |
| `in_review` | Progressing through approval steps |
| `approved` | Final approval granted; `process_pa` task created for AP Clerk; PA PDF generated |
| `processed` | AP Clerk has confirmed physical payment was made; linked invoices marked `paid` |
| `returned` | Returned for revision by any approver; Requester notified; **PA is editable** (Save Draft or Save & Resubmit) |
| `cancelled` | Requester cancelled before approval; terminal state |

---

### 3.16 Legacy PMS Data Migration (SharePoint Import)

One-time migration **and** ongoing manual sync of legacy procurement data from the
old SharePoint-based PMS (`https://canadaroyalmilk.sharepoint.com/sites/pr2`) into
EPMS: Purchase Requests, Purchase Orders, Payment Applications, their line items,
and Invoices (with attachments). Implemented as `epms-api/scripts/import_pms/`
(a CLI) plus an admin page that drives it.

#### 3.16.1 Admin Page & Operations

- **Location:** Admin Panel → **PMS Data Import**. Gated by the `admin_panel`
  permission (Access Control Matrix).
- **Operations (manual-trigger only — no scheduler):**
  - **Full import** — import everything; documents already present (by number) are
    skipped (idempotent insert).
  - **Incremental sync** — re-sync only documents whose SharePoint `Modified` is at
    or after the last successful sync (the "watermark"); existing documents get
    **header-level** field updates, new documents are inserted in full.
  - **Dry-run** toggle — builds/validates and reports what *would* happen, writing
    nothing (read-only; safe against production).
- Runs as a background task; the page polls live status and shows a per-run report
  (inserted/updated/skipped counts, auto-created vendors, temp users, attachment
  results, and unmatched samples) plus run history. **FR-PMS-001.**
- The whole load is a single atomic transaction; re-running is idempotent.
  **FR-PMS-002.**

#### 3.16.2 SharePoint Access

Legacy SAML cookie auth is disabled on the tenant; the importer authenticates via
**OAuth ROPC** (Microsoft Office first-party client, tenant `canadaroyalmilk.ca`)
and reads lists through the SharePoint REST API. Credentials come from environment
settings `SP_USER` / `SP_PASSWORD` / `SP_TENANT` / `SP_SITE` (never committed).
**FR-PMS-003.**

#### 3.16.3 Source → EPMS Mapping

| SharePoint source | EPMS target | Key |
|---|---|---|
| `Purchase Request` (+ Backup) | `purchase_requests` | `PR No` |
| `PR Item` | `pr_line_items` | parent = `Title` (PR No) |
| `PO List` (+ Backup) | `purchase_orders` | `Title` = PO No |
| `PO Item` | `po_line_items` | parent = PO No; `PRITEMID`→pr_line |
| `Payment Request` (+ Backup) | `payment_applications` | `Title` = PA No |
| `Payment Item` | `pa_line_items` | parent = PA No; `POITEMID`→po_line |
| `INVOICE` (+ attachments) | `invoices` + `invoice_attachments` | `internal_ref` = `INV-<spid>` |

Legacy document numbers, created/modified dates, and statuses are preserved.

#### 3.16.4 Matching Rules

- **Vendor** — resolved **only** by the POID embedded in the PO number
  (`PO-<POID>-…`, which equals `vendors.code`). No name matching. A POID with no
  EPMS vendor is auto-created (`code`=POID, details from the legacy `vendorlist`).
  **FR-PMS-004.**
- **Cost Center** — `Department` + `CostCenter` text mapped to an EPMS cost-center
  code via a business-maintained crosswalk; unmapped combos keep the text with a
  null FK. **FR-PMS-005.**
- **Applier → created_by** — resolved in order: (1) explicit name→email crosswalk,
  (2) EPMS user `full_name` = applier, (3) `<name>@canadaroyalmilk.com`, else
  (4) a **temporary EPMS account** is auto-created (`is_active=false`,
  `full_name` = applier) so per-person authorship is preserved. **FR-PMS-006.**
- **PR Type** — text → EPMS type int (Parts→2, Services→4, Inventory Restock→3,
  Fixed Asset→5, Project→6).
- **Currency** — `RMB`/`YMB`→`CNY`; blank→`CAD`; others passed through.
- **Skip list** — business-flagged junk PRs are excluded.

#### 3.16.5 Status Mapping

| Source | EPMS status |
|---|---|
| PR `Approved` | `approved` |
| PR `Manager Approving` | `in_review` · `Rejected To Edit` → `returned` · `*Rejected` → `rejected` |
| PO `GM APPROVED` + receive `PLACING ORDER` | `approved` (awaiting place-order) |
| PO `GM APPROVED` + `DELIVERING`/`RECEIVED` | `issued` · Status0 `COMPLETED`/`CLOSED` → `closed` · `CANCELED` → `cancelled` |
| PO `*APPROVING` / `*REJECTED` | `in_review` / `rejected` |
| PA `PAID` | `processed` · `WAITING PAYMENT` → `approved` · `*APPROVING`/`AP REVIEW` → `in_review` · `*REJECTED`/Status0 `CANCELLED` → `cancelled` |

#### 3.16.6 Incremental Sync & Conflict Handling

Incremental runs pull only rows changed since the watermark (`Modified ge …`) and
**update header-level fields** (status, amounts, dates) of existing documents;
line items are not modified on existing docs. To avoid clobbering EPMS edits, a
document is **skipped** when its EPMS `updated_at` is later than the SharePoint
`Modified` (i.e. it was edited locally after the last sync). The importer stamps
`updated_at` = SharePoint `Modified` on insert so this comparison is reliable. The
watermark advances only on a committed run. **FR-PMS-007.**

#### 3.16.7 Invoice Attachments

Each `INVOICE` list item's attachment files are downloaded from SharePoint and
uploaded to the File Server, with rows recorded in `invoice_attachments`
(`invoice_source='epms'`), idempotent by `(invoice_id, file_name)`. Runs after the
data load; on a committed run only. **FR-PMS-008.**

---

## 4. Non-Functional Requirements

### 4.1 Accessibility
- WCAG 2.1 AA target
- All interactive elements keyboard-navigable
- Focus ring: `ring-2 ring-primary-600 ring-offset-2`
- Skip-to-main-content link at page top
- Status colours always paired with icon or text
- ARIA: `aria-invalid`, `aria-describedby`, `aria-label`, `aria-busy`

### 4.2 Responsive Design

| Breakpoint | Target |
|------------|--------|
| Mobile < 768px | Approval actions, task inbox, status views |
| Tablet 768–1024px | Read tasks, simple forms |
| Desktop ≥ 1024px | Full feature set |

Line items table: horizontal scroll on narrow viewports (min-width: 860px).

### 4.3 Performance
- TanStack Query caching; queries invalidated on mutations
- PDF generation runs in thread executor (non-blocking async)
- Portal-based dropdowns for parts picker (avoids `overflow` clipping)

### 4.4 System NFRs

| Category | Requirement |
|----------|-------------|
| **Performance** | Page load < 2 s (P95). Approval routing < 5 s per step. OCR/AI invoice extraction < 30 s (P95). Supports 500 concurrent users. |
| **Availability** | 99.9% uptime SLA. Planned maintenance windows require 48-hour advance notice. |
| **Security** | RBAC enforced server-side on every API endpoint. MFA (TOTP) required for all approval actions. Data encrypted at rest (AES-256) and in transit (TLS 1.3). Bank details encrypted at field level; decryptable only by Vendor Manager and Finance Manager. |
| **Data Sovereignty** | All data stored in Canadian cloud regions (AWS ca-central-1 or Azure Canada East). No data leaves Canada. |
| **Audit** | Immutable audit log for all document state changes, approval actions, rejection comments, admin config changes, and vendor record edits. Log entries cannot be edited or deleted by any user including System Admin. |
| **Configurability** | All workflow definitions, SLA thresholds, approval chains, role mappings, and budget config must be changeable via Admin Panel without code deployment or server restart. |
| **Record Retention** | All procurement records (PR, PO, GR, Invoice, PA, audit log) retained for a minimum of 7 years. Deletion is not permitted within the retention window. |
| **Localisation** | English / French bilingual UI. Currency display: CAD default; multi-currency supported. Date format: YYYY-MM-DD (ISO 8601) in all data fields. |
| **Compliance** | PIPEDA (Canadian privacy law). Canadian GAAP / IFRS record-keeping obligations. 7-year CRA record retention. |

---

## 5. UI Design System

### 5.1 Colour Palette — Professional Teal / Modern Minimalist

| Token | Value | Usage |
|-------|-------|-------|
| `primary-600` | `#0A7C7C` | Buttons, active nav, links |
| `primary-700` | `#085E5E` | Sidebar background |
| `primary-50` | `#E5F2F2` | Hover states, selected rows |
| `success-600` | `#10B981` | Approved, good condition |
| `warning-500` | `#F59E0B` | Pending, near-breach |
| `danger-600` | `#DC2626` | Over-budget, rejected, urgent |
| `neutral-900` | `#1A2730` | Primary text (cool-tinted) |
| `neutral-200` | `#D9DFE3` | Borders |
| Page bg | `#FAFBFC` | Body background |

### 5.2 Elevation — Shadow-Based (No Card Borders)
- Cards: `box-shadow: 0 1px 3px rgba(10,124,124,0.08)`
- Dropdowns: `0 8px 24px rgba(10,124,124,0.12)`
- Focus ring: `0 0 0 3px rgba(10,124,124,0.10)`

### 5.3 Typography
- Font: Inter (system fallback)
- CAD amounts: monospace, right-aligned, 2 decimal places — `.amount` utility class
- Dates: YYYY-MM-DD (backend ISO format)

---

## 6. Navigation Structure

```
MY WORKSPACE
  Dashboard
  Task Inbox [badge]

PROCUREMENT
  Purchase Requisitions
  Purchase Orders
  Goods Receipt
  Invoices
  Payment Applications

FINANCE
  Budget
  Reports

MASTER DATA  (Procurement Officer / Vendor Manager / System Admin)
  Vendors
  Projects
  Parts Catalog  (Warehouse Staff / Procurement Officer / System Admin)

ADMIN  (System Admin / Finance Manager)
  Admin Panel
```

---

## 7. Module Status

> Legend: ✅ Done &nbsp;|&nbsp; 🟡 Partial &nbsp;|&nbsp; 🔲 Not started &nbsp;|&nbsp; — Not applicable

| Module | Frontend | Backend | Notes |
|--------|----------|---------|-------|
| Authentication & MFA | ✅ | ✅ | Login, JWT, TOTP MFA, token refresh |
| Purchase Requisitions (PR) | ✅ | ✅ | CRUD, approval workflow, history |
| PR Edit (draft/returned) | ✅ | ✅ | |
| PR Recall to Edit (submitted/in_review) | ✅ | ✅ | |
| PR PDF auto-generation on approval | ✅ | ✅ | ReportLab-based; attached as PrAttachment; uses PDF Template settings |
| PO / GR / PA PDF generation (template-driven) | ✅ | ✅ | All 4 docs: server-side ReportLab via `pdf_pr/po/gr/pa.py`; apply PDF Template settings (logo, header/footer note, T&C); stored on File Server |
| Admin Panel — PDF Templates (all 4 doc types) | ✅ | ✅ | |
| Admin Panel — Email Settings (was Email Templates) | ✅ | ✅ | Houses PO-to-vendor SMTP profile (`po_smtp_*` on CompanyConfig — added 2026-05-28) + PO email template. Internal task notification SMTP moved to Portal Admin → Notification Settings the same day. See PRD-PORTAL §5.7 PRD-EMAIL-SPLIT-001~004. |
| Purchase Orders (PO) | ✅ | ✅ | CRUD, workflow, approval history |
| PO Order Placement (Email Composer / Online Portal) | ✅ | ✅ | `PlaceOrderModal` in `PoDetailPage`; `POST /po/{id}/place-order` endpoint live; email template uses `{variable}` syntax; `place_order` task auto-created on approval and backfilled for existing approved POs |
| Goods Receipt — Physical | ✅ | ✅ | |
| Goods Receipt — Service | ✅ | ✅ | |
| Collection Confirmation | ✅ | ✅ | |
| Invoice Upload & AI Parsing | ✅ | ✅ | |
| Three-Way Matching | ✅ | ✅ | |
| Payment Applications (PA) | ✅ | ✅ | |
| Prepayment & Settlement | ✅ | ✅ | |
| Parts Catalog | ✅ | ✅ | With CSV import/export |
| Admin Panel — Core Sections | ✅ | ✅ | Company settings, user management, budget config, workflow config, temp assignments. (Departments + Cost Centers moved to Portal — owned by mdm-api as of 2026-05-27, see §3.6.3a/b) |
| Departments + Cost Centers (mdm-api) | ✅ | ✅ | Source of truth in mdm-api. Departments managed in Portal → Admin; Cost Centers managed in Portal → Finance → Budget Config. EPMS keeps GET-only fallback for legacy pickers. Migration spec: `docs/superpowers/specs/2026-05-26-cost-center-dept-mdm-migration.md` |
| Admin Panel — Role Management & Access Control Matrix Editor | ✅ | ✅ | Role List + Access Control Matrix editor live; 11 permission columns including 5 `view_<doc>` gates (PR/PO/GR/Invoice/PA) wired into list + detail endpoints via `_effective_permissions` union (2026-05-25) |
| Admin Panel — Notification Settings | 🔲 | 🔲 | |
| Configurable Approval Workflow Engine | ✅ | ✅ | Configurable step chain; gm_or_opm runtime resolution |
| Budget Dashboard | ✅ | ✅ | L1/account hierarchy, CSV import/export, summary aggregation |
| Task Inbox | ✅ | ✅ | Auto-generated tasks on state transitions |
| Vendors Master Data | ✅ | ✅ | With CSV import/export |
| Projects Master Data | ✅ | ✅ | |
| Role-specific Dashboards (9 variants) | ✅ | ✅ | Tech debt: `CfoDashboard.tsx` + `AuditorDashboard.tsx` still in codebase; to be removed |
| Notification Service (Email + Teams) | 🔲 | 🟡 | Backend: basic SMTP send (`email.py`) exists; Teams integration, per-event templates, daily scheduler not yet built |
| User Profile & Notification Preferences | 🔲 | 🔲 | No `ProfilePage.tsx`; no User Profile API |
| Reports | ✅ | ✅ | Frontend: 7 report types with filter/export UI; Backend: 7 report endpoints (PR/PO/GR/Invoice/PA/Budget/Vendor) |
| Legacy PMS Import (SharePoint) | ✅ | ✅ | Admin Panel → PMS Data Import. Full + incremental (Modified-based) sync; POID vendor match + auto-create; applier full_name match + temp accounts; invoice attachment import. See §3.16 (FR-PMS-001~008). `epms-api/scripts/import_pms/` |
| Accessibility Pass (WCAG 2.1 AA) | 🔲 | — | |

---

## 8. Sprint Plan

### Phase 1 — Frontend Prototype (Complete)

| Sprint | Deliverables | Status |
|--------|-------------|--------|
| 1 | Login, MFA, global layout | ✅ Done |
| 2–3 | PR List, PR Create, PR Detail | ✅ Done |
| 3.5 | Admin Panel: Company Settings + User Management | ✅ Done |
| 3.6 | Parts Catalog | ✅ Done |
| 3.7 | PR Type 3 — Parts Catalog picker; Supplier Item ID | ✅ Done |
| 3.8 | Design system overhaul: teal palette, shadow elevation | ✅ Done |
| 4 | PO List/Create/Detail; Approval workflow engine | ✅ Done |
| 5 | GR, Invoice, PA, remaining Admin Panel sections | ✅ Done |
| 10 | Budget Dashboard | ✅ Done |
| 11 | Role-specific dashboards (11 variants), Vendors, Projects, Task Inbox | ✅ Done |
| 12 | Report Centre | ✅ Done |
| 13 | PO Order Placement (`PlaceOrderModal`), User Profile page | 🟡 PO placement done; User Profile pending |
| 14 | Admin Panel — Role Management UI, Access Control Matrix Editor | 🔲 |
| 15 | Admin Panel — Notification Settings UI | 🔲 |
| 16 | WCAG 2.1 AA pass | 🔲 |

### Phase 2 — Backend Development (In Progress)

| Sprint | Deliverables | Status |
|--------|-------------|--------|
| B1 | FastAPI scaffold, DB schema, Alembic migrations, health check | ✅ Done |
| B2 | Auth API: login, JWT, TOTP MFA, password hashing, RBAC middleware | ✅ Done |
| B3 | User, Department, Company Config APIs | ✅ Done |
| B4 | PR API: CRUD, configurable approval workflow engine, approval history, PR PDF generation | ✅ Done |
| B5 | PO API: CRUD, approval workflow, approval history | ✅ Done |
| B5a | PO Order Placement API: `POST /po/{id}/place-order`; Email Composer (pre-filled from template + vendor email); Online confirmation + portal reference | ✅ Done |
| B6 | GR API: physical/service GR, collection confirmation, acknowledgement | ✅ Done |
| B7 | Invoice API: upload, AI parsing, three-way matching | ✅ Done |
| B8 | PA API: CRUD, prepayment & settlement workflow | ✅ Done |
| B9 | Budget API: L1/account CRUD, CSV import/export, dashboard aggregation | ✅ Done |
| B10 | Task engine: auto-generate tasks on state changes | ✅ Done |
| B11 | Notification service: async immediate dispatch + daily follow-up scheduler; Email (SMTP via aiosmtplib) + Teams (Adaptive Cards); per-event templates; retry logic; User Profile API (channel preference); Admin Panel Notification Settings API | 🔲 |
| B11a | Role Management API: custom role CRUD, Access Control Matrix editor (`PATCH /config` for `role_permissions` JSONB), Dept→GM/OPM mapping API | 🔲 |
| B12 | Reports API: PR/PO/GR/Invoice/PA/Budget/Vendor report endpoints with filters and CSV export | ✅ Done |
| B13 | PO / GR / PA PDF generation (server-side, template-driven) | 🔲 |
| B14 | Security hardening: rate limiting, CORS, secrets management | 🔲 |
| B15 | Production deployment: cloud infra, monitoring, load testing | 🔲 |

---

## 9. Budget Tracking — Committed vs Actual

Budget tracking operates at the L2 budget account level. All monetary amounts are in the fiscal year's configured currency (default CAD).

| Metric | Definition | When Updated |
|--------|-----------|--------------|
| **Annual Budget** | Total approved budget for the account code in the fiscal year | Set at fiscal year start; Finance Manager can add mid-year supplements |
| **Committed** | Sum of amounts from PRs in `approved` (or beyond) status for this L2 account + fiscal year | Increases on PR final approval. Decreases when a PA for the linked PO is paid (actual spend takes over). Reversed if PR/PO is cancelled after approval. |
| **Actual Spent** | Sum of all paid PA amounts linked to this L2 account + fiscal year | Updated when a PA transitions to `paid` status |
| **Available Balance** | Annual Budget − Committed − Actual Spent | Recalculated on every PR approval, PA payment, and cancellation |
| **Projected After PR** | Available Balance − current PR estimated amount | Displayed in real-time in Budget Balance Widget on PR Create/Edit form |

**Alert thresholds (configurable in Admin Panel → Budget Config):**

| Status | Default Threshold | Colour | Alert Sent To |
|--------|------------------|--------|---------------|
| On Track | Utilisation < 80% | 🟢 Green | No alert |
| Warning | 80% ≤ Utilisation < 100% | 🟡 Amber | Department Manager, Finance Manager (daily digest) |
| Over Budget | Utilisation ≥ 100% | 🔴 Red | Department Manager, Finance Manager, Finance BP, GM/OPM (immediate) |

> **Type 1 exemption:** Raw Materials / Packaging PRs are fully exempt from budget tracking. No budget account code is required; no committed or actual amounts are updated by Type 1 documents.

**Budget Balance Widget — scoping rule:** The Budget Balance Widget on the PR Create / Edit form resolves the displayed balance by looking up the selected account code within the **selected Cost Center + L1 group scope**. The same account code may exist under multiple cost centers with different budget amounts; the widget always shows the balance for the specific cost center and L1 the user has selected, not the first matching code found globally.

---

## 10. List Pagination

All major document list pages use **server-side pagination**. The backend applies `OFFSET`/`LIMIT` before returning results; the frontend never loads an entire table to paginate in memory.

### 10.1 Paginated Endpoints — EPMS

| List page | Backend endpoint | Default `page_size` | Max `page_size` |
|-----------|-----------------|--------------------:|----------------:|
| Purchase Requisitions | `GET /api/v1/pr` | 20 | 200 |
| Purchase Orders | `GET /api/v1/po` | 20 | 200 |
| Goods Receipts | `GET /api/v1/gr` | 20 | 200 |
| Payment Applications (EPMS) | `GET /api/v1/pa` | 20 | 200 |
| Invoices — All tab | `GET /api/v1/invoices` | 20 | 200 |
| Invoices — Unmatched tab | `GET /api/v1/invoices?status=unmatched` | 20 | 200 |
| Invoices — Exceptions tab | `GET /api/v1/invoices?status=exception` | 20 | 200 |
| Vendors | `GET /api/v1/vendors` | 20 | 500 |
| Parts Catalog | `GET /api/v1/parts` | 20 | 200 |

Every paginated endpoint accepts `page` (integer ≥ 1, default 1) and `page_size` query parameters and returns:

```json
{ "items": [...], "total": <int> }
```

`total` is the full filtered count (before slicing), used by the frontend to compute page count and "X–Y of Z" display.

### 10.2 Backend Implementation Contract

- The query applies all filters first, then runs `SELECT COUNT(*)` over the filtered subquery to get `total`.
- `OFFSET` = `(page − 1) × page_size`. `LIMIT` = `page_size`.
- Access-scope filters (role-based visibility) are applied **before** the count query, so `total` reflects only records the user is allowed to see.

### 10.3 Pagination UI Component

The shared `Pagination` component (`src/components/ui/Pagination.tsx`) is used on all list pages in EPMS and OA:

- **Per-page selector:** 10 / 20 / 50 / 100 items
- **Navigation:** First `«` · Previous `‹` · numbered pages (with `…` ellipsis for long ranges) · Next `›` · Last `»`
- **Count display:** "X–Y of Z" (shows "No results" when total = 0)
- **Reset on filter change:** any filter, tab, or search change resets `page` to 1

### 10.4 Pagination FR IDs

| FR ID | Requirement |
|-------|-------------|
| **PGN-001** | All list pages must use server-side pagination. Loading the full dataset and paginating in the browser is not permitted. |
| **PGN-002** | Every paginated endpoint must return a `total` field (full filtered count) alongside `items`. |
| **PGN-003** | Changing any filter, status tab, search term, or sort order must reset `page` to 1. |
| **PGN-004** | The pagination UI must display "X–Y of Z" using the server-returned `total`. |
| **PGN-005** | Pagination state (`page`, `page_size`) must be included in the TanStack Query cache key so different pages are cached independently and refetch correctly. |

---

## 11. Remaining Open Items

| ID | Topic | Notes |
|----|-------|-------|
| OI-A | GM/OPM temporary delegate during absence | Mechanism defined in §1.5; user assignment to be confirmed at go-live |
| OI-B | Finance BP department scope | Does Finance BP cover all departments or a scoped subset? To be confirmed before go-live |
| OI-C | GL account code mapping | GL codes for Prepaid Asset and expense/inventory accounts needed for PA records (Phase 1 manual; Phase 2 ERP-integrated) |
| OI-D | Microsoft Teams bot installation | Tenant admin approval needed for EPMS Teams bot; required before Teams notification channel is live |
| OI-E | Initial Budget Master data | Finance team to prepare L1/L2 account codes with annual budget amounts for CSV import at go-live |
| ~~OI-F~~ | ~~Over-budget approval mode~~ | **Resolved 2026-05-14:** three modes supported — `fm_gm_opm` (default), `fm_only`, `hard_block` — configurable in Admin → Budget Config (BudgetAdminConfig.over_budget_mode). See §3.2.6 OBG-001~009. |
| OI-G | GM/OPM over-budget + value-threshold consolidation | If a PR triggers both OB-2 and the value-threshold GM/OPM step, should these be merged into one approval request? |
| OI-H | Vendor POID assignment | All existing vendors must have a POID assigned before any PO can be issued |
| OI-J | File Server production deployment | File storage path and disk capacity must be provisioned before go-live; backup/DR strategy for `file-storage/` directory to be confirmed |
| OI-I | Phase 2 ERP integration target | ERP platform (SAP / Oracle / Dynamics) to be confirmed; affects GL posting and bank integration scope |
