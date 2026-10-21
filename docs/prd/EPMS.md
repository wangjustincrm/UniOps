# EPMS — Product Requirements Document
**Version:** 2.34
**Date:** 2026-10-21
**Status:** In Production

> **v2.34 (2026-10-21)** — Claimant skip for OA claims (§3.3.5 item 3): a step whose only holder is the claimant of an OA expense claim / Travel Application is skipped, because OA never lets a claimant approve their own claim (OA.md WF-SELF-001…004). EPMS documents are unchanged.

> **v2.33 (2026-10-21)** — New §3.22 Direct Payment Application (Direct PA), moved here from OA: an invoice with no PO and no agreement is matched with **Match → Direct PA** and paid from **Payment Applications → Create Direct PA**, charged to a department (the creator's by default), a cost center of it and a budget account, with a required reason; approved on the `pa_dir` ("PA (Direct)") workflow. PA `source` gains `direct` (EPMS) and `oa_direct` (OA's retired rows, §3.15.1); invoice route gains `direct` (§3.14, Route filter); action-key table (§3.3).

> **v2.32 (2026-10-20)** — A courier invoice is checked in the same Upload Invoice window as any invoice: the PDF preview stays on the left and only the form on the right becomes the courier one (FRT-016).

> **v2.31 (2026-10-19)** — Every list column header sorts the list, ascending then descending, across all pages for server-paged lists (UI-SORT-001…007, §3.20; shared by all UniOps modules). PR/PO/PA Status keeps its status → step → approver order, now applied by the server. The Vendors Inactive tab filters on the server; Vendor Credits is paginated; the Receiving Report export follows the on-screen sort.
>

> **v2.30 (2026-10-20)** — House-account receipt hand-off (AGR-032, AGR-034, task table): an invoice matched to a house account with no receipt evidence raises an `attach_receipts` task for the agreement's owner; the owner (and that task's holder) may now list and attach the agreement's receipts on the invoice. A caller without that right sees a "cannot attach" notice instead of a load error.

> **v2.29 (2026-10-20)** — One Upload Invoice form (FRT-005, FRT-014, FRT-016): no invoice-type choice and no carrier picker. A carrier's invoice is recognised by its vendor and always becomes a courier invoice; it is uploaded with the ordinary invoice-upload permission (matching and Freight PAs stay on `epms.freight.manage`), and the PO invoice upload refuses a carrier's vendor. AP can assign a courier invoice for matching like any invoice (FRT-017).

> **v2.28 (2026-10-19)** — Freight pages folded into the Invoice and PA pages (§3.21, FRT-009, FRT-014, FRT-015): courier invoices are uploaded, listed and matched on the Invoices page (Freight badge, Route filter), Freight PAs are raised from the PA page (New Freight PA); the Freight page keeps Shipments and Carriers. A PO-restricted `epms.freight.manage` holder sees courier invoices; other invoice readers see the matching panel read-only. Design: `docs/superpowers/specs/2026-10-19-freight-into-invoice-pa-design.md`.

> **v2.27 (2026-10-19)** — New §3.21 Courier Freight Payment (Freight): register courier tracking numbers, match carrier invoices by tracking number, and pay them with a Freight PA that has no PO, split by cost center, approved through its own chain (`pa_frt`). PA gains a `source` (§3.15.1); navigation (§6); open items OI-AH–OI-AK.

> **v2.26 (2026-10-16)** — PA line items follow line-matched invoices (PA-018); Data Maintenance no longer rewrites a PA's pre-tax amount from lines that disagree with it, and asks before a line edit changes it (DM-007). OI-AF / OI-AG added.
>
> **v2.25 (2026-10-16)** — Brought in line with the system as deployed on main (`33375e50`). Every change was checked against the code; requirements that are wanted but not built are marked **Not implemented** and listed in §11.
> - **Roles & access:** primary + additional roles; company-unique posts; new Finance Director, ERP PA Officer and Payment Officer roles; Absence Cover replaced by Approval Delegation (§1.5); role/matrix/routing administration moved to Portal.
> - **PR & budget:** Department selector, Service/Project Owner, catalog-only budget codes; over-budget check against plan minus NC actuals; fourth over-budget mode *Allow* (§3.2).
> - **Approval engine:** default chains, return resumes at the returning step, Approval Routing and in-flight re-sync, submit preflight (§3.3).
> - **PO / GR / Vendor:** NC65 PO mirror, Edit Details on NC POs, two-level PO sign-off, PO PDF content (§3.12.6–3.12.9); GR lifecycle rewritten, Receiving Report (§3.13); Vendor Master owned by MDM (§3.10).
> - **Invoice / PA / payment:** multi-PO line allocation and Unmatch (§3.14); multi-PO PAs, 7-day due-date window, tax on line items only, configurable PA workflow incl. Finance Director (§3.15); new §3.17 Purchase Agreements and §3.18 Payment Execution, Remittance Advice & Vendor Credits.
> - **Platform:** Task Inbox and Notifications rewritten (outbox, per-type immediate/digest policy) (§3.7, §3.9); Data Maintenance (§3.6.8); new §3.19 AI Assistant and §3.20 UI Conventions; §6–§8 and §11 rewritten.

---

## 1. Product Overview

The **Enterprise Procurement Management System (EPMS)** is a web-based procure-to-pay platform designed for food/dairy manufacturing environments. It digitises and enforces the full procurement lifecycle — from purchase requisition through payment — with multi-tier approvals, budget control, and audit trails.

The system consists of:
- **`epms`** — React/TypeScript frontend (Vite, TanStack Query, React Router v7)
- **`epms-api`** — Python/FastAPI backend with PostgreSQL, SQLAlchemy async, Alembic migrations

EPMS is one module of the UniOps platform and relies on shared UniOps services: sign-in, users and roles are owned by the **Portal** and its identity service (§3.1, §1.2); approval workflows, approval routing and approval delegation are run by the shared **approval engine** (§3.3, §1.5); budget plans and NC actuals come from the **Finance** module (§3.8).

### 1.1 Target Users

| Role | Code | Primary Tasks |
|------|------|--------------|
| Requester | `requester` | Create PRs, confirm service receipts, collect goods |
| Department Admin | `dept_admin` | Raise PRs for the department; view the department's documents; raise PAs for the department's own requisitions when granted Create/Edit PAs |
| Supervisor *(derived)* | — | Optional first PR/PA approval step for the requesters who report to them, only in departments where the supervisor layer is switched on (§3.3.8) |
| Department Manager | `dept_manager` | Approve documents routed to their department; view department budget |
| Director *(derived)* | — | Optional approval step for each department they are named Director of in Approval Routing; sees those departments' documents and budgets |
| General Manager | `gm` | Approves the GM/OPM step for departments routed to GM |
| Operations Manager | `opm` | Approves the GM/OPM step for departments routed to OPM; PO sign-off |
| Procurement Officer | `procurement_officer` | Create POs, manage vendors, match invoices |
| Procurement Manager | `procurement_manager` | PO approval / sign-off |
| Warehouse Staff | `warehouse_staff` | Receive goods |
| AP Clerk | `ap_clerk` | Upload invoices, resolve match exceptions |
| Finance Business Partner | `finance_bp` | PA review (only users explicitly assigned the Finance BP role act as approvers) |
| Finance Manager | `finance_manager` | Over-budget pre-approval, PA approval, budget configuration |
| Finance Director | `finance_director` | Payment sign-off step (additional role only) |
| Vendor Manager | `vendor_manager` | Vendor master data |
| ERP PA Officer | `erp_pa_officer` | Raises PAs for NC-imported POs (additional role only) |
| Payment Officer | `payment_officer` | Executes payment of approved PAs (additional role only; §3.18) |
| CFO / Auditor | `cfo` / `auditor` | Read-only oversight |
| System Admin | `system_admin` | Full configuration; may act on any approval step |

> **Note:** GM, OPM, Procurement Manager, Finance Manager, Vendor Manager and Finance Director are **company-unique posts** (one holder each). A post may be held as a user's primary role or as an additional role; both are assigned in **Portal → Admin → Access Control → User Roles**. Supervisor and Director are not assigned as roles: a Supervisor is whoever a user's *Supervisor* field names, and a Director is whoever **Portal → Admin → Approval Routing** names for a department (§3.3.8). The role set is fixed and built in; custom roles cannot be created. See §1.2 for the multi-role model.

### 1.2 Multi-Role Users

Every user has one **primary role** and any number of **additional roles**. A user's effective roles are the union of both. Primary and additional roles are edited in **Portal → Admin → Access Control → User Roles**; changes take effect on the next request (no re-login).

- **Posts.** GM, OPM, Procurement Manager, Finance Manager, Vendor Manager and Finance Director are company-unique posts. Assigning a post that another active user already holds (as primary or additional role) is refused. Approval steps for a post go to its current holder.
- **Finance BP** is a job function, not a post: any number of users can be approvers, but only users given Finance BP as an *additional* role are treated as Finance BP approvers.
- **Additional-only roles.** ERP PA Officer, Payment Officer and Finance Director can only be granted as additional roles.
- **Department Manager steps** are addressed to the active user whose **primary** role is Department Manager in the routing department (§3.3.2). Holding a post (e.g. GM) does not by itself make a user a Department Manager.
- **Missing holder.** If a post used in a workflow has no holder, the step is not skipped; the document waits until a holder is assigned (and in-flight documents are re-routed automatically when the post is assigned — §3.3.8).

**Approval workflow behaviour:**
- When a document reaches a step, the approval task is addressed to whoever holds that step in their own right — the resolved person for Supervisor / Department Manager / Director steps, or every current holder of the post for post steps (§3.3.2).
- **Multi-role auto-skip:** When a user holds several consecutive workflow steps in their own right, a single approval action cascades automatically through all consecutive steps where that same user is an approver. Each skipped step is recorded as a separate `ApprovalEvent` with comment `"Auto-approved (same approver holds both roles)"`. The cascade stops as soon as a step is reached that the user does not hold. It is not applied at submit (a requester who is also the first approver must still approve), does not pass through delegation, and System Admin's bypass never cascades. This rule applies equally to PR, PO, PA and Agreement workflows. See §3.3.5 for the engine-level specification.

**Task Inbox behaviour:**
- The Task Inbox shows every task addressed to the user personally, plus role-pool tasks for every role the user holds as primary **or** additional role — all aggregated into a single inbox. For example, a Department Manager who is also the Procurement Manager sees both `approve_pr` (Department Manager step) and `approve_po` (Procurement Manager step) tasks.
- Director and Supervisor tasks are always personal and are never shown to a role pool.
- While an Approval Delegation is active, the delegator's approval tasks also appear in the delegate's inbox (§1.5).

### 1.3 Access Control Matrix

The table below summarises the **default** permissions per role. Permissions are edited in **Portal → Admin → Access Control → Permission Matrix** (the EPMS Role Management and Access Control Matrix screens have been retired). All access is enforced server-side.

| Role | Create PR | Approve PR | **View PR** | Create PO | Approve PO | View GR | Create PA | Approve PA | Vendor Master | Budget data scope (§3.8) | Admin |
|------|-----------|-----------|------------|-----------|-----------|---------|-----------|-----------|--------------|-------------|-------|
| Requester | ✅ | — | **Own** | — | — | Own + Service GR create | ✅ (own PRs' POs) | — | — | Own dept | — |
| Department Admin | ✅ | — | **Department** | — | — | Dept | ✅ (dept PRs' POs, when granted Create/Edit PAs) | — | — | Own dept | — |
| Supervisor *(derived)* | ✅ | ✅ (direct reports, if layer on) | **Reports** | — | — | — | — | ✅ (Supervisor step, if configured) | — | Own dept | — |
| Department Manager | ✅ | ✅ (routed dept) | **Department** | — | — | Dept | — | ✅ (Dept Manager step) | — | Own dept | — |
| Director *(derived)* | ✅ | ✅ (directed depts) | **Directed depts** | — | — | — | — | ✅ (Director step) | — | Directed depts | — |
| GM | ✅ | ✅ (GM-routed depts) | **Routed depts** | — | ✅ | Routed | — | ✅ | — | All | — |
| OPM | ✅ | ✅ (OPM-routed depts) | **Routed depts** | — | ✅ | Routed | — | ✅ | — | Own dept + OPM-routed depts | — |
| Procurement Officer | ✅ | — | **All** | ✅ | — | ✅ | ✅ (any PO) | — | ✅ | Own dept | — |
| Procurement Manager | ✅ | — | **All** | — | ✅ | ✅ | — | — | ✅ | Own dept | — |
| Warehouse Staff | ✅ | — | — | — | — | ✅ (receive/update) | — | — | — | Own dept | — |
| AP Clerk | ✅ | — | **All** | — | — | ✅ | ✅ | — | — | All | — |
| Finance BP | ✅ | — | **All** | — | — | ✅ | ✅ | ✅ | — | All | — |
| Finance Manager | ✅ | ✅ (over-budget PRs company-wide) | **All** | — | — | ✅ | ✅ | ✅ | — | All | Partial |
| Finance Director | ✅ | — | **All** | — | — | ✅ | — | ✅ (payment sign-off step) | — | Own dept | — |
| ERP PA Officer | ✅ | — | **All** | — | — | ✅ | ✅ (NC-imported POs) | — | — | Own dept | — |
| Payment Officer | ✅ | — | — | — | — | — | — | — (executes payment, §3.18) | — | Own dept | — |
| Vendor Manager | ✅ | — | — | — | — | — | — | — | ✅ Full | Own dept | Partial |
| CFO / Auditor | ✅ | — | **All** | — | — | ✅ | — | — | — | All | — |
| System Admin | ✅ | ✅ (any step) | **All** | ✅ | ✅ (any step) | ✅ | ✅ | ✅ (any step) | ✅ Full | All | ✅ Full |

> **Create PR is not enforced.** Any signed-in user can create a PR. The `create_pr` key exists in the matrix but is not checked; reaching the PR list (and its *New PR* button) requires View PR.
>
> **Create PA** is gated by the Create/Edit PAs matrix permission. A user whose primary role is Requester may raise a PA only on POs from PRs they created, unless they also hold Procurement Officer (any PO), ERP PA Officer (NC-imported POs with no PR) or Department Admin (POs whose PR is in their own department) — see §3.15.
>
> **Approve** columns describe who the default workflows route to; the exact chain is configured in Portal → Admin → Approval Workflows and Approval Routing (§3.3, §3.3.8). Any approval step can also be acted on by today's delegate of the approver (§1.5).
>
> **New-role defaults:** Supervisor = View PR only; Director = View PR and View PA; Finance Director = all View columns + Finance; ERP PA Officer = all View columns; Payment Officer = View PO, View Invoice, View PA + Finance; CFO/Auditor = all View columns. Locked (cannot be switched off): ERP PA Officer {View PO, View PA}; Payment Officer and Finance Director {View PA}.
>
> **Budget data scope** follows the matrix: *Full Access Budget View* gives all cost centers (default GM, Finance Manager, Finance BP, AP Clerk, CFO, Auditor, System Admin); *Department-Related Budget View* gives the user's own department, every department they direct and — for the OPM — every department routed to OPM. Opening the Budget Dashboard additionally requires *View Budget Dashboard* (§3.8).

**Document visibility scope definitions (applies to PR, PO, GR, Invoice, PA in EPMS):**

| Scope | Who | PRs visible |
|-------|-----|-------------|
| **Own** | Requester | PRs the user raised |
| **Department** | Department Manager, Department Admin | PRs charged to a cost center of the user's department, or raised by a member of that department |
| **Routed depts** | GM, OPM | PRs charged to cost centers of every department whose GM/OPM step routes to that post (Portal → Admin → Approval Routing; a department with no routing entry counts as GM) |
| **Directed depts** | Director | PRs charged to, or raised in, each department the user directs |
| **Reports** | Supervisor | PRs raised by users whose Supervisor is this user |
| **All** | Any other role (Procurement, AP, Finance, CFO, Auditor, ERP PA Officer, Payment Officer, Finance Director, System Admin) | All PRs, provided the role has View PR |
| — | — | No access to the list — default `view_<doc>` is OFF in the Access Control Matrix. System Admin may enable the relevant View toggle in Portal to grant access. |

> **Always visible regardless of scope:** a document on which the user — or someone who has delegated approvals to them today — holds an open approval task, and a PR on which the user is the named Service/Project Owner. Delegation does not otherwise widen the delegate's scope.
>
> **Multi-role rule:** if any of the user's roles has scope "All", the user sees everything. Otherwise the user sees the **union** of the scopes of all restricted roles they hold (e.g. a Department Manager who is also GM sees their own department *and* every GM-routed department).

> **Configurability:** the scopes above apply once the Matrix `view_<doc>` permission is granted. The "—" rows reflect the **default** Matrix settings only; switching the corresponding View permission ON in Portal → Admin → Access Control grants list/detail access at the "All" scope for roles that are not one of the restricted roles above.

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

**Implementation:** Resolved server-side: each list endpoint is filtered by the caller's PR/PO scope, and the View permissions are computed as the union of the user's primary and additional roles. Detail endpoints enforce both the View permission and the scope filter, returning HTTP 404 on miss. Clients cannot bypass either layer by omitting or altering query parameters.

> **Enforcement rule:** All EPMS list endpoints (`GET /pr`, `GET /po`, `GET /gr`, `GET /invoices`, `GET /pa`) and their corresponding detail endpoints (`GET /pr/{id}`, `GET /po/{id}`, `GET /gr/{id}`, `GET /invoices/{id}`, `GET /pa/{id}`) apply role-based scoping automatically. The `mine` query parameter is ignored for Requesters (server always applies their scope). System Admin and other unrestricted roles receive no additional filter. Detail endpoints return HTTP 404 for any document outside the caller's scope (never 403, so existence cannot be inferred).
>
> **Two-layer access gate:** Each endpoint enforces two checks in order:
> 1. **Access Control Matrix `view_<doc>` permission** (Portal → Admin → Access Control → Permission Matrix). Computed as the union of all of the user's roles (primary and additional). Missing → empty list / 404 detail.
> 2. **Per-role scope** (this section). Defines *which* documents within the allowed type are visible.

> **Multi-role permission rule:** For users holding several roles, the permissions shown in this table are the **union** of all their roles. For example, a Department Manager who is also Finance Manager can approve (a) PRs routed to their department (as Department Manager), and (b) over-budget PRs from any department (as Finance Manager). Both apply simultaneously to the same user.

### 1.4 Mobile & Remote Approval

Approvers act on documents **in the web app only** (desktop or mobile browser). Notifications reach them by email and, optionally, Microsoft Teams; both only *link* to the document — neither channel can approve.

| Channel | Mechanism | Supported Actions |
|---------|-----------|------------------|
| **In-App** | Responsive web app; approval buttons on the document detail page and the Approver dashboard | Approve, Return, Reject, View detail |
| **Email** | Task notification / daily digest with a deep link to the document (§3.9) | Open document |
| **Microsoft Teams** | Adaptive Card posted to the company Incoming Webhook, with a "View in EPMS" button (sent only when the company default channel or the user's preference includes Teams, the user has a Teams account and a webhook is configured) | Open document |

**Requirements:**

- **MOB-001** — Approval views must be usable at viewport widths ≥ 360 px (mobile-first layout for approval views).
- **MOB-004** — Every approval action is recorded once in the approval history (actor, action, timestamp, comment), regardless of how the approver reached the page.
- **MOB-005** — If the task was already resolved by the time the approver opens the link (e.g. recalled or approved by another approver), the page shows the document's current state and no approval buttons (buttons are shown only to a user holding an open approval task).

*Email reply-to-approve and in-card Teams approval are **not implemented** — see §11.*

### 1.5 Approval Delegation

When an approver is away (leave, travel, illness), a System Admin can name a stand-in for a date range in **Portal → Admin → Approval Delegation**. Approval Delegation replaces the former "Absence Cover" design.

| FR ID | Requirement |
|-------|-------------|
| **ABS-001** | Only a System Admin can create, edit, or revoke a delegation. A delegation names a **delegator**, a **delegate**, a start date and an end date (both inclusive, plant-local calendar dates) and an optional note. Delegator and delegate cannot be changed afterwards — revoke and create a new one. |
| **ABS-002** | A user cannot delegate to themselves; the end date may not precede the start date; one delegator cannot have two live delegations whose dates overlap. A delegation is active only while not revoked, today falls inside its window, and the delegate's account is active. It ends automatically after the end date with no manual intervention required. One delegate may cover several people. |
| **ABS-003** | While active, the delegate may approve, return or reject any approval step the delegator holds in their own right — whatever role that step requires. The delegator keeps their own authority. Delegation is one level only (a delegate's delegate gains nothing) and does not pass on System Admin's bypass. |
| **ABS-004** | Open and new approval tasks are **not moved**: they stay addressed to the delegator and also appear in the delegate's Task Inbox and dashboard counts; the delegate can open the underlying documents. Non-approval tasks (Create PO, Create PA, goods receipt, PO sign-off) are not shared. |
| **ABS-005** | Approval-request emails for the delegator are sent to the delegate (to the delegator if the delegate's account has been deactivated). |
| **ABS-006** | Actions taken under delegation are recorded against the delegate, with "on behalf of <delegator>" appended to the comment; document PDFs name the delegation on the signature line, and the current-step display shows "Approver (delegated: Stand-in)". |

> Approval Routing can also record a "GM backup" and "OPM backup" user, but these are **not used** for routing or authorisation today; delegation is the only supported absence cover (see §3.3.8, §11).

---

## 2. Procurement Types

| # | Name | Selectable | Budget Check | Material ID | Own identifier / date | Receipt |
|---|------|-----------|-------------|-------------|----------------------|---------|
| 1 | Raw Materials / Packaging | **No** — tile disabled | No | Per line item (ERP material) | — | Warehouse GR |
| 2 | Misc / Consumables | Yes | Yes | No | — | Warehouse GR |
| 3 | Spare Parts | Yes | Yes | Per line item (from Parts Catalog) | — | Warehouse GR |
| 4 | Service | Yes | Yes | No | Expected completion date | Service/Project Owner confirms |
| 5 | Fixed Asset | Yes | Yes | No | Fixed Asset ID | Warehouse GR |
| 6 | Project-Related | Yes | Yes | No | Expected completion date **and** Project No. | Service/Project Owner confirms |

Every entry in the last two columns is **enforced at submit**, not merely shown
on the form — the Submit action rejects a PR that is missing them (HTTP 409).
For types 2–6 Submit also requires a cost center and a budget account. Only
Submit is gated; a PR already past draft stays approvable and payable whatever
it is missing, so historical records are not retrospectively blocked. The same
checks are available without submitting (§3.3.9).

For types 4 and 6 the PR names a **Service/Project Owner** (defaults to the
requester; any active user may be chosen) who confirms the service/project was
delivered (§3.2.1).

Type 1 is disabled on the Create PR tile selector: raw material and packaging
purchasing is done in the company ERP (Yonyou NC65), and those purchase orders
arrive in EPMS as a read-only mirror from NC (§3.12.6). The API still accepts
type 1 (1..6) because the mirror writes it.

> **This table is not the assistant's source.** The AI assistant (§3.19) answers
> "what types are there and what differs" from the PR-types knowledge file for
> the prose and by *calling* the submit checks for the rules, so its answer
> cannot drift from what runs. Keep this table in step with that file — see
> §2.1.

### 2.1 The guide layer — updated with every change

The AI assistant (§3.19) has a **guide layer**: the knowledge it answers "what
is this, how do I use it, what differs between these, what do I have to fill
in" from. It lives in `epms-api/app/knowledge/*.yaml` and is read by
`app/services/doc_type_guide.py`.

The guide layer has three tiers:

| Tier | Source | Covers |
|---|---|---|
| **Modules** | `modules.yaml` | What each UniOps module is for; its document list is test-checked against the document kinds the guide can explain |
| **Document kinds** | `*_types.yaml` (PR, PO, GR, Invoice, PA, Agreement) + derived gates | What each kind/type is for, what differs, what must be filled in |
| **Processes** | Nothing written — generated at question time | "How does PR/PO/PA approval work?" is answered from the **live approval-engine configuration**; "what do I owe?" runs the Task Inbox's own query |

**Any requirement change must update the guide layer in the same change.** This
is a rule, not a nicety, and the reason is visible in this document's own
history: before 2026-09-15 there were five descriptions of the procurement
types — this table, §3.2.1, `docs/training/generate_training_ppt.py`, the create
form, and the backend — and no two agreed. §2 promised a Fixed Asset ID
requirement that the form collected and threw away. The training deck asserted
that Type 5 gets "extra scrutiny in approval", which was never true: approval
routing reads department configuration and cross-department payments, and has
never read procurement type at all. Each of those was written correct and left
behind.

The split that keeps it honest:

| | Where it lives | Why |
|---|---|---|
| **Derived** | Nowhere — computed by *calling* the enforcing code | A rule change moves the answer in the same commit. Submit gates come from `field_checks_for`; the receipt flow from `schemas/gr.is_service`; processes from the live workflow configuration |
| **Written** | `app/knowledge/*.yaml` | What a thing is *for* and when to pick it. No code states this and none can — it is the half a training document supplies |

Rules for anyone extending it:

1. **If the running system can adjudicate it, derive it.** Never restate a rule
   in YAML that some function already enforces.
2. **Every derivation gets a test asserting reported == enforced.**
   `epms-api/tests/test_doc_type_guide.py` is the pattern.
3. **Frontend-only rules get pinned in the frontend.**
   `epms/src/components/pr/procurementTypes.test.ts` is the pattern — paired
   include/exclude assertions, because "type 3 uses the picker" also passes when
   every type does.
4. **Never point the assistant at a PRD.** This document describes intent and
   drifts; the assistant must answer from behaviour. The prose in the YAML was
   *sourced* from here, which is not the same thing.

---

## 3. Functional Requirements

### 3.1 Authentication & Security
- Sign-in is owned by the UniOps **Portal**. An unauthenticated visit to any EPMS URL redirects to Portal with `returnUrl`; after sign-in the user lands back on the page they asked for (only allow-listed UniOps module addresses are accepted as return targets). EPMS's own `/login` page is a fallback only.
- Email + password login
- **MFA (email one-time code)** is asked at sign-in when either the company MFA switch (Portal → Admin → Security) or the user's own MFA switch (My Profile) is on. The code is emailed and expires after 10 minutes. Approval actions do not ask for MFA again.
- **Password rotation:** a user must choose a new password (a) on first sign-in after an administrator creates, imports or resets the account, and (b) when the password is older than the company *Password Expiry* setting (Portal → Admin → Security: Never / 30 / 60 / 90 / 180 / 365 days). Until they do, Portal shows only the change-password page (with Sign Out); no module can be opened. Changing the password restarts the expiry clock.
- Role-based access control: navigation items and page content filtered by the user's effective roles and Access Control Matrix permissions (§1.3)
- Session persisted via `localStorage` (Zustand persist, key: `epms-auth`)
- JWT access + refresh tokens
- **Global sign-out** (see PORTAL.md §2.6 — cross-origin design): signing out from EPMS removes `epms-auth` from EPMS's own localStorage, then redirects to `{PORTAL_URL}/logout` so Portal clears its own session. Final destination: Portal `/login`.
- **Idle timeout**: after **60 minutes** of inactivity, the server refresh token is blacklisted (`POST /auth/logout`), then global sign-out is triggered.
- **401 handling**: any HTTP 401 from the EPMS API (after a failed silent token refresh) triggers global sign-out.

### 3.1.1 User Profile
Each authenticated user has a **Profile** page (accessible from the header user menu) where they can manage personal settings:
- Display name, email, Teams account
- **Notification channel preference**: Email only / Teams only / Both / None — overrides the company default notification channel (§3.9)
- Change password
- **Email MFA on/off switch** (MFA is also forced on for everyone when the company switch is on)
- **My Signature** — draw or upload a signature image, used when signing POs in PO Sign-off (§3.12.8)

### 3.1.2 Mobile & Remote Approval

See **§1.4** for the channel specification (In-App approval; Email and Microsoft Teams notifications that link to the document) and FR IDs MOB-001, MOB-004 and MOB-005. Email reply-to-approve and Teams in-card approval are not implemented.

### 3.1.3 Cross-Cutting UI Conventions — User Display

Wherever the system displays a person who performed an action (uploader, approver, matcher, resolver, settler, etc.), the UI must show the user's **full name**, never a raw UUID. Other cross-cutting UI conventions (tabs, embedded mode, date display, action buttons) are in §3.20.

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
- Any signed-in user can create a PR (see §1.3); reaching the PR list and its *New PR* button requires View PR.
- Procurement type selection (6 types, prominent tile selector; **Type 1 tile is
  disabled** — see §2)
- Conditional fields per type. Those marked **(gated)** are enforced server-side
  at submit, not just rendered with an asterisk:
  - Types 1 & 3: Material ID per line item
  - Type 3: Line items must be selected from Parts Catalog (no free-text)
  - Types 4 & 6: Service/Project expected completion date **(gated)**
  - Types 4 & 6: Service/Project Owner (see below)
  - Type 5: Fixed Asset ID **(gated)** — stored on `purchase_requests.fixed_asset_id`
  - Type 6: Project No. **(gated)** — free-text input, not a picker; there is no
    project master list behind it
  - Types 2–6: cost center + budget account **(gated)** — a submit without them is refused ("A cost center and budget code are required…")
  - All types: vendor **(gated)**
- **Department** (required) — defaults to the requester's own department and may be changed. It decides which cost centers are offered, and which Department Manager, Director and GM/OPM approve the PR (§3.3.2). Changing it clears the cost center, budget account and decomposition factors. If no department is set, routing falls back to the requester's own department; the stored department name falls back to the cost center's department.
- **Service/Project Owner** (types 4 and 6, required) — the person who will confirm the service/project was delivered. Searchable picker over active users; defaults to the requester; any active user may be chosen; it can never be left empty. The owner receives the service-receipt confirmation tasks and reminders, can open the PR and its downstream documents whatever their department, and is printed on the PR PDF. An inactive or unknown user is rejected by the server. The owner can later be changed through Data Maintenance (§3.6.8); open receipt tasks follow the new owner.
- **Line Items table:**
  - **Type 3 (Spare Parts) columns:** #, Description (Parts Catalog picker), Material ID, Supplier Item ID, Qty, Unit, Unit Price, Line Total — **Notes column is hidden** to reduce visual clutter; the Notes field is still stored in state and submitted if previously populated
  - **All other types columns:** #, Description, Material ID (Types 1 only), Supplier Item ID, Qty, Unit, Unit Price, Line Total, Notes
  - Type 3 description cell: Parts Catalog picker (portal dropdown, fixed-position)
  - Selecting a part auto-fills: description, material ID, unit price, unit, supplier item ID
  - Minimum 1 line; lines can be added/removed
  - Quantity must be greater than zero. Unit price is required and may be zero or negative (discount, rebate or credit lines). The PR total must not be zero to submit ("At least one line must have a price"). A PR whose total is zero or negative is never over budget.
  - Total auto-sums and feeds into budget widget
- **Supplier Item ID** field per line item (e.g. Amazon ASIN, supplier catalog #)
- Preferred vendor search — sends `vendor_id` to backend (not just name)
- Cost Center selector — limited to cost centers of the selected Department; sends `cost_center_id` to backend; backend resolves and stores `vendor_name`, `cost_center_name`, `department_name` at creation time (denormalised)
- **Budget account** (types 2–6, required at submit) — chosen as Cost Center → L1 category → L2 account (shown "code — name") from the budget catalog (Portal → Finance → Account Catalog); the L1 catalog is shared, not per cost center. Only the account code is stored. Create and edit reject a code that is not in the catalog (the check is skipped if the budget service is unreachable). The block is hidden for Type 1 (Raw Materials / Packaging). The PR PDF prints the account name beside the code.
- **Decomposition Factors** — When the selected Budget Account has `decomposition_enabled = true`, a Factor block appears below the L2 picker showing one dropdown per active factor (1..3 dropdowns; see budget-api PRD §4.2 BFAC-001). All factor values must be picked before Submit. Stored on the PR as `factor_combo` JSONB (`{factor_code: value_code}`), matching the shape of `budget_plan_breakdowns.factor_combo` so PRs map directly onto a specific breakdown line for downstream balance / ledger reconciliation. Block hidden when the Account has no factors. See FR PR-FACTOR-001~004 below.
- **Budget Balance Widget** — for the selected cost center and account in the current year: *Annual budget* (current approved budget plan), *Committed* (not yet tracked — always 0 today, see §9), *Actual spent* (NC actual, including vouchers entered in NC but not yet tallied; discarded/deleted vouchers excluded), *Available* = annual − committed − actual, and *Projected after this PR*. Turns red when the PR exceeds Available.
- **Over-budget warning** — shown when the PR total exceeds the account's Available balance, as determined by the server (§3.2.6; the form asks the server before saving, using the same computation as save). Under modes *Finance Manager + GM/OPM*, *Finance Manager only* and *Allow without extra approval* an **Over-Budget Justification** (non-empty, no minimum length) is required to submit. Under *Hard block* the justification field is hidden, a "Submission blocked — over budget" notice is shown and the PR cannot be submitted (OBG-009).
- Delivery address — optional field; Required by date; File attachments; Requester notes
- Save as Draft / Submit for Approval
- Layout: `col-span-9` main form + `col-span-3` sidebar (budget widget + tips)

**FR IDs — PR Department:**

| FR ID | Requirement |
|-------|-------------|
| **PR-DEPT-001** | The PR's selected department, not the requester's, determines the Department Manager, Director and GM/OPM steps of its approval chain and of the POs/PAs raised from it. |

**FR IDs — PR Decomposition Factor selection:**

| FR ID | Requirement |
|-------|-------------|
| **PR-FACTOR-001** | When a user picks a Budget Account that has `decomposition_enabled = true` and at least one active factor, the Create PR form must render a Decomposition Factors block with one `<select>` per active factor, populated from `GET /accounts/{account_id}/factors` (budget-api). Inactive factor values are omitted from the dropdown. |
| **PR-FACTOR-002** | All visible factor dropdowns must have a non-empty value before Submit. Partial combos are rejected client-side with an inline error listing the missing factor name(s). Drafts may save a partial combo client-side, but the request body omits `factor_combo` until all values are set so the server-side shape validator (must be non-empty when present) does not reject the draft. |
| **PR-FACTOR-003** | The chosen combo persists on `purchase_requests.factor_combo` (JSONB, nullable) as `{factor_code: value_code}` — identical shape to `budget_plan_breakdowns.factor_combo` so PRs can be joined directly onto the matching breakdown line. Changing the L1/L2 selection resets the factor combo. |
| **PR-FACTOR-004** | PR Detail page renders a Decomposition Factors section directly under the Details grid whenever `pr.factor_combo` is non-empty. Each row shows `factor_name (factor_code) → value_code — value_name`, resolved from the live `/accounts/{id}/factors` call (falls back to raw codes if the factor was deactivated after the PR was filed). |

#### 3.2.2 PR Edit
- Available for PRs in `draft` or `returned` status (any other status is refused)
- Only the requester who raised the PR (or a System Admin) can edit it — others are refused with "Only the requester who raised this PR can edit it"
- Pre-populates all fields from existing PR data
- Department, Service/Project Owner, vendor, cost center, budget L1/L2 reconstructed from API data; the Edit page offers the same fields and checks as Create, including the budget check and over-budget handling
- Every save re-runs the over-budget check; a justification is discarded if the PR is no longer over budget
- "Save as Draft" — PATCH + navigate back; "Submit for Approval" — PATCH + action('submit')
- Guard: redirects to detail page with error if PR is not in editable status

#### 3.2.3 PR List

**Visibility scoping (enforced server-side — see §1.3):**

| Scope | Role | PRs shown |
|-------|------|-----------|
| Own | `requester` | PRs the user raised |
| Department | `dept_manager`, `dept_admin` (Department Admin) | PRs charged to a cost center of the user's department, or raised by a member of that department |
| Routed departments | `gm`, `opm` | PRs charged to cost centers of every department whose GM/OPM step routes to that post (Portal → Approval Routing; a department with no routing entry counts as GM) |
| Directed departments | `director` | PRs charged to, or raised in, each department the user directs |
| Reports | `supervisor` | PRs raised by users whose Supervisor is this user |
| All | Any other role (Procurement, AP, Finance, CFO, Auditor, ERP PA Officer, Payment Officer, Finance Director, System Admin, custom roles) | All PRs, provided the role has View PR |

> **Always visible regardless of scope:** a PR on which the user — or someone who has delegated approvals to them today — holds an open approval task, and a PR on which the user is the named Service/Project Owner (PL-008).

- Filters: Status, Department, Requester, Type, Prepaid. Department and Requester choices are limited to what the viewer's scope covers; a user whose only role is Requester sees neither (their list is always their own PRs).
- Search box matches PR number, title, budget code and vendor name.
- Columns: PR Number, Title, Requester (omitted for pure requesters), Vendor, Amount (CAD), Type, Prepaid, Status, Submitted. For PRs awaiting approval, the Status cell also shows the current step, the approver's name (and stand-in, if delegated) and days waiting; sorting by Status groups by status, then step (approval-chain order), then approver (named approver before an unassigned role pool), across all pages. Every column sorts (UI-SORT-001); the list opens sorted by Submitted, newest first, with PRs not yet submitted last.
- Select a row and **Copy** to start a new PR pre-filled from it. **New PR** button.

**FR IDs — PR List visibility:**

| FR ID | Requirement |
|-------|-------------|
| **PL-001** | The server must apply role-based scoping to `GET /pr` unconditionally. A Requester cannot retrieve another user's PR by any API parameter combination. |
| **PL-002** | Department-scoped roles (`dept_manager`, `dept_admin`) see only PRs charged to a cost center of their own department or raised by a member of it, regardless of client-supplied filters. |
| **PL-003** | GM/OPM see PRs charged to cost centers of departments whose GM/OPM step routes to their post in Portal → Approval Routing (departments without an entry count as GM). Routing changes apply to the next request. |
| **PL-004** | Any role outside the restricted set (Requester, Department Manager, Department Admin, GM, OPM, Director, Supervisor) — e.g. Procurement Officer, Procurement Manager, AP Clerk, Finance BP, Finance Manager, CFO, Auditor, ERP PA Officer, Payment Officer, Finance Director, System Admin and custom roles — sees all PRs (and all linked POs/GRs/Invoices/PAs) with no department filter, provided the role has View PR. Vendor Manager and Warehouse Staff default to **no PR visibility** (see §3.6.7 default `view_pr` settings) and can be granted access only by switching View PR on in the Access Control Matrix (Portal → Admin → Access Control). |
| **PL-006** | Multi-role users receive the union of all their roles' scopes (primary + additional roles); if any role is unrestricted, the user sees all PRs. Role changes apply to the next request — no re-login required. |
| **PL-005** | The frontend PR list page must not display PRs that fall outside the user's server-enforced scope, even if cached data contains them. On role change, the query cache for PR list must be invalidated. |
| **PL-007** | `GET /pr/{id}` must enforce the same per-role scope plus `view_pr` Matrix gate as the list endpoint. Out-of-scope or view-disabled requests return HTTP 404, not 403, so document existence cannot be probed. Same rule applies to `GET /po/{id}`, `GET /gr/{id}`, `GET /pa/{id}`, and `GET /invoices/{id}`. |
| **PL-008** | Holding an open approval task on a PR (personally or as today's delegate), or being its Service/Project Owner, always makes it visible. Delegation does not otherwise widen the delegate's scope. |

#### 3.2.4 PR Detail
- **Details section**: PR number, title, status, type, created date, submitted date
- **Vendor, Department, Cost Center**: displayed from denormalised name fields (`vendor_name`, `department_name`, `cost_center_name`) stored at creation time
- **Service/Project Owner** row (types 4/6)
- **Budget Code**: shows `code — name` (name resolved via budget overview lookup)
- **Over-budget justification** — amber block shown to everyone who can open the PR whenever it was saved as over budget ("(no justification provided)" if blank)
- **Decomposition Factors** *(rendered only when `pr.factor_combo` is non-empty)*: grid of `factor_name → value_code — value_name` rows, resolved via `GET /accounts/{id}/factors` (PR-FACTOR-004)
- **Line Items tab**: shows Description, Material ID, Supplier Item ID (column only shown when at least one item has a value), Qty, Unit, Unit Price, Line Total
- **Attachments tab**: file upload/download/delete; the approved-PR PDF is generated automatically on final approval; a *Regenerate PDF* action re-creates it
- **History tab**: audit events showing action + actor role + comment + timestamp
- **Approval Timeline** (right panel): shows every step of this PR's effective chain, including injected over-budget steps; steps that were auto-skipped are shown struck through with the reason (e.g. "department has no Director"); completed steps show the approver's name, date and action; the current step names who it is waiting on and offers **Send reminder** (see §3.9); pending steps follow
- **Action buttons** based on status:
  - Requester actions (shown when the viewer is not the current approver):
    - `draft` / `returned`: Edit button (link to `/pr/:id/edit`) + Withdraw (cancel)
    - `submitted` / `in_review`: Recall to Edit button (triggers `recall` action → navigates to edit) + Withdraw
  - Approver bar (Approve / Return / Reject) is fixed at the bottom of the page; buttons are disabled while an action is in flight
  - `approved`: Create PO button
- **Known gap:** the *Withdraw* button is also shown for `in_review` PRs, but the engine only allows cancelling a PR in `draft`, `returned` or `submitted` (REJ-004), so withdrawing an in-review PR fails with an error. Decision pending — see §11 (OI-M).

#### 3.2.5 PR Document Numbering

Purchase Requisitions are assigned a system-generated number using the format:

```
PR-YYYYMMDD-XXXX
```

Where `YYYYMMDD` is the creation date (UTC) and `XXXX` is a zero-padded daily sequence number (resets to `0001` each calendar day). Example: `PR-20260410-0042`.

- **PR-006** — The number is assigned by the backend when the PR is first saved (including as a draft), using the UTC date of creation, and never changes. Users cannot edit it.

#### 3.2.6 PR Approval Flow

PR approval uses the configurable step-based engine described in §3.3. The PR workflow is configured independently in **Portal → Admin → Approval Workflows → PR**.

**Default PR approval chain (shipped default; production may be configured differently):**

```
Step 1: supervisor     — Requester's Supervisor (optional; auto-skipped unless the requester has an active supervisor
                          and their department has the supervisor layer switched on)
Step 2: dept_manager   — Department Manager of the PR's department (mandatory)
Step 3: director       — Director of the PR's department (optional; auto-skipped when the department has none)
Step 4: gm_or_opm      — GM or OPM, per the department's Approval Routing entry
```

> Skipped optional steps are recorded and shown in the timeline with the reason ("Auto-skipped (no Supervisor assigned)", "Auto-skipped (department has no Director)", or "⚠ configured Director is inactive/missing"). The Supervisor and Director layers are switched per department in **Portal → Admin → Approval Routing** (§3.3.8); the supervisor person is the requester's *Supervisor* field on their user record.

**Over-Budget pre-approval flow:**

| FR ID | Requirement |
|-------|-------------|
| **OBG-001** | A PR is **over budget** when its total exceeds the Available balance of its cost center × budget account for the current year, where Available = the current approved budget plan amount − NC actual for that cost center/account/year (posted plus entered-but-not-yet-tallied vouchers). The check runs, and the result is stored on the PR together with the user-supplied justification, every time the PR is saved (it is not re-evaluated at submit); the Create/Edit form shows the same server answer before saving. |
| **OBG-001a** | The check does not reserve budget: other PRs or POs not yet booked in NC are not subtracted, so several PRs can each pass against the same remaining balance (see §11 OI-K). |
| **OBG-001b** | The check fails open: if the budget service cannot answer, the account code is unknown, or the PR has no cost center/account/positive total, the PR is treated as within budget. If NC actuals cannot be read, the check falls back to the opening-balance figure. |

When a PR is over budget, the system reads the **Over-Budget Approval Mode** (configured in **Portal → Finance → Budget Config → Over-Budget Approval Mode** — single source of truth; saving requires the Admin Panel permission) and applies one of four behaviours:

| Mode (Budget Config label) | Effect on an over-budget PR |
|---|---|
| `fm_gm_opm` — Finance Manager + GM/OPM (default) | Two steps are added in front of the normal chain: *Over-Budget — Finance Manager*, then *Over-Budget — GM / OPM* (GM or OPM per the PR department's routing). |
| `fm_only` — Finance Manager only | One step added in front: *Over-Budget — Finance Manager*. |
| `allow` — Allow without extra approval (added 2026-10) | No step added; the PR follows its normal chain. The justification is still required and is visible to every approver on the PR Detail page. |
| `hard_block` — Hard block | Submission is refused with a message telling the requester to reduce the amount or split the PR. No justification field. |

> The pre-approval steps are added by the approval engine; they are **not** part of the PR workflow definition. **The mode is frozen on the PR when it is submitted** (`purchase_requests.ob_mode_applied`: `none` when the PR was within budget, otherwise the mode in force), and the PR's step list — and therefore its Approval Timeline — is always built from that frozen value, never from Budget Config as it stands later. Any unrecognised value behaves like *Finance Manager only*.

**OBG-010 — Changing the mode while PRs are in approval.** A change applies in full to PRs submitted afterwards. PRs already in approval only ever get *lighter*:

- **Looser mode** (`fm_gm_opm` → `fm_only`, or anything → `allow`): an over-budget step the new mode no longer requires and that the PR has **not yet passed** is skipped. Saving Budget Config re-aligns every in-flight over-budget PR at once: the Finance Manager's (or GM/OPM's) open task is closed, the step is recorded as *"Auto-skipped (over-budget approval no longer required — Budget Config mode changed to …)"* and shown struck through on the timeline, and the PR moves to its next approver. A step still pending when an approver acts is skipped the same way. The save confirmation says how many PRs were updated, and warns if the re-alignment could not run (saving again retries it).
- **Stricter mode** (e.g. `allow` → `fm_only`): never adds steps to a PR already submitted.
- **`hard_block`** does not waive pending over-budget steps (it forbids over-budget spend; in-flight PRs keep their approvals). It only refuses new submissions and resubmissions.

> Before 2026-10 the mode was re-read on every action. Switching `fm_only` → `allow` on 2026-10-16 removed the Finance Manager step from every submitted over-budget PR, so approved PRs showed their approvers under the wrong roles and PRs waiting on the Finance Manager could no longer be approved (ISS-0006). Existing PRs were backfilled from their recorded approvals when the frozen mode was introduced.

Over-Budget FR IDs:

| FR ID | Requirement |
|-------|-------------|
| **OBG-002** | The task assigned to Finance Manager (and GM/OPM in `fm_gm_opm` mode) for over-budget pre-approval must display the PR number, total amount, and the requester's justification text inline in the task inbox — without requiring the approver to open the PR Detail page. |
| **OBG-003** | If the Finance Manager (or GM/OPM) **rejects** at an over-budget step, the PR is rejected (terminal). If they **return** it, the PR goes back to the requester; on resubmission it resumes at the step that returned it under the mode frozen at its first submission (a looser current mode still waives pending over-budget steps, OBG-010). If the PR crossed the budget line while it was returned (now within budget, or newly over budget), the over-budget steps change, so it restarts from the first step under the current mode. |
| **OBG-004** | In `fm_gm_opm` mode, after Finance Manager approves the PR advances to the GM/OPM over-budget pre-approval step. In `fm_only` mode there is no such step and the PR proceeds directly into its normal chain. |
| **OBG-005** | GM/OPM over-budget pre-approval (only in `fm_gm_opm` mode): if approved, the PR continues into its normal chain; reject and return behave as in OBG-003. |
| **OBG-007** | The over-budget justification text is required (non-empty, no minimum character count) when the PR is over-budget and the mode is `fm_gm_opm`, `fm_only` or `allow`. This is enforced by the PR form; the API stores whatever is sent. It is stored on the PR and displayed as a prominent amber block on the **PR Detail page** (visible to all approvers) and inline in the over-budget pre-approval task description (OBG-002). |
| **OBG-009** | In `hard_block` mode, submission of an over-budget PR must be refused at `submit` time with a user-readable error ("This PR exceeds the available budget for its account. Submission is blocked by Budget Config (hard_block mode). Reduce the budget code's commitment or split the PR into smaller items."). The frontend must disable submission and hide the justification field when this mode is active. |

> PR approval, recall and withdrawal do not adjust any committed budget figure — committed budget is not tracked (§9). The former OBG-006 and OBG-008 have been removed for this reason.

**Rejection, return, recall and withdrawal rules:**

| FR ID | Requirement |
|-------|-------------|
| **REJ-001** | The current step's approver (or their delegate, or a System Admin) may **reject** a PR outright. Rejection is terminal — the PR moves to `rejected` status and cannot be resubmitted without creating a new PR. |
| **REJ-002** | The current approver (or their delegate, or a System Admin) may **return** a PR for revision; it moves to `returned` and the requester gets a revision task. When the requester resubmits, the PR **resumes at the step that returned it** — approvals already given stand and are not repeated; optional/conditional steps from that point are re-evaluated. If the return cannot be traced (e.g. imported data) or that step no longer exists in the workflow, the chain starts from the beginning. (Company rule for all document types.) |
| **REJ-003** | All approval history is kept; the timeline shows the return and the later approvals. |
| **REJ-004** | A PR can be **withdrawn** (cancelled) while `draft`, `returned` or `submitted` (i.e. before anyone has approved it) by its requester, a System Admin, or the approver currently holding it. Cancelled PRs cannot be reopened; a new PR must be created. |
| **REJ-005** | The requester (or a System Admin) may **recall** a `submitted`/`in_review` PR back to `draft` to edit it; a recalled PR starts its approval chain from the beginning when resubmitted. |

### 3.3 Approval Workflow Engine

All approval-required actions across **all UniOps modules** (EPMS procurement documents, OA expense claims and travel applications, OA payment applications, Finance budget plans, VMS visits) share the same configurable step-based approval engine hosted by `approval-api` (`:8003`).

The engine is designed around two concepts:

- **Workflow** — an ordered list of named approval steps, each specifying a role token.
- **Action Key** — a string identifier that binds a document/form type to a workflow. Every submission event carries an action key; the engine looks up the bound workflow and executes it.

Workflows and their bindings are configured in **Portal Admin Panel → Approval Workflows** (PORTAL.md §5.8) and stored in `CompanyConfig.workflow_defs[action_key]`. Department-level routing (GM or OPM, Director, Supervisor layer) is configured separately in **Portal → Admin → Approval Routing** (§3.3.8).

#### 3.3.1 Action Keys

Every submittable document or form type has a unique action key. Each action key is independently bound to one workflow definition.

| Action Key | Document / Form | Module |
|------------|----------------|--------|
| `pr` | Purchase Request | EPMS |
| `po` | Purchase Order | EPMS |
| `posign` | PO sign-off (NC-imported POs, §3.12.8) | EPMS |
| `agr` | Purchase Agreement (§3.17) | EPMS |
| `pa` | Payment Application (PO-linked) | EPMS / OA |
| `pa_frt` | Freight PA chain — a `pa` document whose `source` is `freight` walks this chain instead of `pa`'s (§3.21) | EPMS |
| `pa_dir` | Direct PA chain — a `pa` document whose `source` is `direct` walks this chain instead of `pa`'s (§3.22). OA's retired PA-DIRs (doc type `pa_dir`) used it too | EPMS |
| `exp` | General Expense Claim | OA |
| `trv` | Travel Expense | OA |
| `tra` | Travel Application | OA |
| `mil` | Mileage Claim | OA |
| `cfm` | Custom Form (default workflow) | OA |
| `cfm_<code>` | Named Custom Form (per-form override) | OA |
| `budget_plan` | Budget Plan | Finance |
| `vms_visit` | Visitor visit | VMS |

If no workflow is configured for a key, the engine uses its shipped default for that key (see §3.3.2). Admin can bind any action key to any workflow without code changes.

#### 3.3.2 Workflow Definition

Each workflow is an **ordered list of steps**, configured in Portal Admin Panel and stored in `CompanyConfig.workflow_defs[action_key]`. At runtime the engine reads this definition and routes tasks accordingly.

**Step node fields:**

| Field | Description |
|-------|-------------|
| `id` | Stable identifier for the step (e.g. `dept_manager`, `gm_or_opm`, `finance`) |
| `label` | Display label shown in Approval Timeline (e.g. "Department Manager", "GM / OPM") |
| `role` | Role token used for routing — see Role Tokens below |

**Role tokens and routing logic:**

| Token | Resolves to | Task addressed to |
|---|---|---|
| `supervisor` | Requester's *Supervisor* (active), only if the requester's department has the supervisor layer on | That person; skipped otherwise |
| `dept_manager` | The active user whose **primary** role is Department Manager in the routing department | That person; if none, **submission is refused** ("Cannot route approval: no active Department Manager is configured for this document's department…") |
| `director` | The routing department's Director in Approval Routing (active) | That person; skipped if none |
| `gm_or_opm` | GM or OPM per the routing department's Approval Routing entry (no entry = GM). For a PA spanning several departments: the engine-wide "cross-department" setting (default GM) | **All holders of the resolved post** (role pool; the post is company-unique) — resolved live, so reassigning the post moves the task |
| `gm`, `opm`, `procurement_manager`, `finance_manager`, `finance_director`, `vendor_manager` | Current holder(s) of the post (primary or additional role) | Role pool |
| `finance_bp` | Users assigned Finance BP as an additional role | Role pool |
| `ap_clerk` | Users with the role | Role pool |
| `zone_manager` | VMS only — every manager of the visit's access zone, snapshotted at submit (`quality_manager` = legacy alias, same behaviour) | One task each; any one approves; skipped if none |

**Routing department.** Department-scoped steps (Department Manager, Director, GM/OPM) use the department named on the document: the PR's selected department (for a PR and the POs/PAs raised from it) or the agreement's department (for an agreement and its PAs). Only when none is recorded does the engine fall back to the requester's/creator's own department. A PA covering POs from several departments skips Department Manager and Director and goes to the cross-department GM/OPM setting. The Supervisor step always follows the requester personally.

**Missing approver.** A Department Manager step with no active manager blocks submission with a message naming the fix. Supervisor/Director steps with no person are skipped and the skip is recorded. A post step (GM, OPM, Procurement Manager, etc.) with no holder is not skipped: the task goes to the role pool and the document waits until a holder is assigned (in-flight documents are re-routed automatically when the post is assigned — §3.3.8).

**Default workflows (shipped, fully overridable):**

| Action Key | Default Steps |
|------------|--------------|
| `pr` | `supervisor → dept_manager → director → gm_or_opm` |
| `po` | `procurement_manager → gm_or_opm` |
| `posign` | `procurement_manager` (initials) `→ opm` (signature) |
| `agr` | `dept_manager → procurement_manager → finance_manager` |
| `pa` | `dept_manager → director → gm_or_opm → finance_bp → finance_manager` (a `finance_director` step may be added as final payment sign-off) |
| `pa_dir` | `finance_bp → finance_manager` |
| `exp` | `dept_manager → finance_bp` |
| `trv` | `dept_manager → finance_bp` |
| `tra` | `dept_manager → finance_manager → gm` |
| `mil` | `dept_manager → finance_bp` |
| `cfm` | `dept_manager` |
| `budget_plan` | `dept_manager → finance_manager` |
| `vms_visit` | `dept_manager` (+ optional `zone_manager`) |

Admins may add, remove, or reorder steps freely in Portal → Admin → Approval Workflows without any code changes or service restart. The workflows actually configured in production may differ from these shipped defaults.

#### 3.3.3 State Transitions (all document types)

| Action | Who | From | To | Notes |
|---|---|---|---|---|
| `submit` | Creator | draft, returned | submitted | Starts at step 0 (or, from `returned`, at the step that returned it); leading auto-skips recorded; if every step is skipped → approved. Task created for the first approver. |
| `approve` | Current step's approver, their delegate, System Admin | submitted, in_review | in_review / approved | Same-approver and conditional auto-skips applied. Non-final: task for next step's approver. Final: PDF generated and document-specific post-approval tasks created (see §3.3.4) |
| `return` | Same as approve | submitted, in_review | returned | Revision task to creator |
| `reject` | Same as approve | submitted, in_review | rejected (PA: cancelled) | Terminal |
| `recall` | Creator, System Admin | submitted, in_review | draft | Next submit restarts from the top |
| `cancel` | Creator, System Admin, or current approver | PR/PO/AGR: draft, returned, submitted; PA: draft, returned | cancelled | Terminal |

> Approval tasks cannot be dismissed with "Mark done" in the Task Inbox — that action is refused for approval tasks. Approval tasks left open on a document that is no longer awaiting approval are closed automatically (§3.7.1).

#### 3.3.4 Per-Document Behaviour on Final Approval

| Document | On `approved` |
|----------|--------------|
| PR | PDF auto-generated and saved as attachment (§3.4); **`create_po` task created for the Procurement Officer pool**; an email notification goes out per the task-notification policy (§3.9 — `create_po` is on/immediate by default) |
| PO | PDF auto-generated and saved as attachment; `place_order` task created for Procurement Officer (§3.12.3) |
| PA | PDF generated and saved as attachment (§3.4); `process_pa` task created for the **Payment Officer** pool (§3.15, §3.18) |

#### 3.3.5 Multi-Role and Conditional Auto-Skip

The engine skips steps automatically in three ways (the third only on OA claims). Every skipped step produces its own `ApprovalEvent` row, so the full history is preserved and visible in the Approval Timeline (shown struck through / with an "Auto-approved" or "Auto-skipped" indicator and the reason).

1. **Conditional skip** (applied at submit and after each approval): a Supervisor or Director step that does not apply to this document (no active person, or layer switched off); Department Manager and Director steps on a cross-department PA; a VMS Zone Manager step (legacy name: Quality Manager) when the visit's zone has no zone managers. Recorded with the reason.
2. **Same-approver skip** (applied after an approval only):
   1. Actor approves step N — a normal `ApprovalEvent` is recorded for step N.
   2. Engine checks step N+1: if the actor also holds that step **in their own right** — including being any one holder of a post — it records an additional `ApprovalEvent` (action `"approve"`, comment `"Auto-approved (same approver holds both roles)"`) and advances to N+2.
   3. Step 2 repeats until either a step is reached that the actor does not hold, or the final step is passed (→ document moves to `approved`).

   The same-approver skip is **not** applied at submit (a requester who is also the step-1 approver must still approve), is **not** applied through delegation (a delegate's approval does not cascade into steps the delegator holds), and System Admin's bypass never cascades.
3. **Claimant skip — OA claims only** (applied at submit and after each approval, and by in-flight re-sync): on an OA expense claim, Travel Application or custom form, a step whose **only** holder is the claimant is skipped with the comment "Auto-skipped (claimant holds this step — cannot approve own claim)". OA never lets a claimant approve their own claim, so without this the claim would wait at that step for good. Submission is refused when every remaining step would be skipped this way. Details: OA.md §7.1 (WF-SELF-001…004). PR / PO / PA / Agreement / VMS keep the rule in item 2: no skip at submit.

Role resolution for every token is as described in §3.3.2. GM/OPM steps are addressed to the resolved post (all its current holders), not broadcast to both GM and OPM.

**System Admin exclusion / bypass:** System Admin users are never assigned as approvers in any workflow step definition, and the `system_admin` role cannot appear in a workflow definition. However, System Admin (as primary role) has an unconditional **bypass**: they may execute any approval action (`approve`, `return`, `reject`) on any document regardless of step assignment. The backend validates this bypass (WF-AUTH-003) and the frontend `canApprove` check grants it unconditionally when `user.role === "system_admin"`. The bypass is not passed on through delegation.

#### 3.3.6 Approval Authorisation Enforcement

The Approval Engine validates the actor's identity against the current step **before** executing any approval action.

| Step | Who may act |
|---|---|
| `dept_manager` / `director` / `supervisor` | The specific person resolved for this document (§3.3.2) |
| `gm_or_opm` | Any current holder of the resolved post (GM or OPM) |
| Posts (`procurement_manager`, `finance_manager`, `finance_director`, `gm`, `opm`, …) / `finance_bp` / `ap_clerk` | Any current holder of that post / any assigned Finance BP / any AP Clerk |
| A post with **no holder at all** | A user whose primary role equals the post's role code |
| Any step | Today's delegate of a person who may act (§1.5); System Admin |

> Approve, Return, Reject — and Cancel by anyone other than the document's creator — all require this authority. Unauthorised attempts are refused with an error naming the required role (HTTP 422 from the engine; surfaced as 409 by the EPMS API).

**`gm_or_opm` routing token vs. stored user role:** `gm_or_opm` is a **workflow routing token** — it is never stored as a user's role. Users hold `gm` or `opm` (as primary or additional role). At task-creation time the engine resolves the token to the GM or OPM post via the routing department's Approval Routing entry and addresses the task to that post's role pool, so whoever holds the post at the time can act. The frontend must not compare `user.role === "gm_or_opm"` to determine button visibility; it must use the task-based check (WF-UI-001).

#### 3.3.7 Frontend `canApprove` Determination

The EPMS frontend (PR Detail, PO Detail, PA Detail pages) shows or hides the approval action bar (Approve / Return / Reject) based on a `canApprove` flag. This flag must not be derived from a simple `user.role === step.role` string comparison because:

- `gm_or_opm` is a routing token, not a stored user role — no user ever has `role = "gm_or_opm"`.
- Multi-role users (primary + additional roles, §1.2) and delegates (§1.5) may be entitled to act on a step whose token differs from their primary role.

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

The **task-based check** is primary: the approval engine creates `approve_<doc_type>` tasks addressed to the exact person or post that should approve, and the Task Inbox also shows a delegator's approval tasks to their active delegate. If the current user has such a task for this document, they can approve — regardless of their stored role string. This is the only correct approach for `gm_or_opm` steps, multi-role users and delegates.

The backend always enforces WF-AUTH-001 on the action endpoint as a second gate.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **WF-AUTH-001** | The Approval Engine must reject `approve` actions where the actor is not authorised for the current step (§3.3.6), returning an error with a descriptive message. |
| **WF-AUTH-002** | Department-scoped steps (Department Manager, Director, Supervisor) are addressed to one resolved person. GM/OPM and other post steps are addressed to the post, so whoever holds it at the time can act. |
| **WF-AUTH-003** | System Admin may approve any step regardless of role assignment. |
| **WF-AUTH-004** | Return, Reject and Cancel (by anyone other than the creator) are gated exactly like Approve. |
| **WF-UI-001** | The frontend `canApprove` flag for document detail pages must be derived from whether the current user holds an active `approve_<doc_type>` task for that document (via `GET /api/v1/tasks`), not from comparing `user.role` against the workflow step's role token string. |
| **WF-UI-002** | When the task list has not yet loaded (network in flight), the frontend may provisionally show the approval bar if `user.role` matches the current step's role token, to prevent button flicker. The backend (WF-AUTH-001) is the authoritative gate. |
| **WF-TASK-001** | `GET /api/v1/tasks` must expand the caller's effective role set before filtering: it reads the user's primary role and additional roles (Portal → Admin → Access Control → User Roles) on every request. Role-pool tasks (`assigned_user_id = NULL`) are matched against the full role set; personally assigned tasks (`assigned_user_id = user_id`) are always included regardless of role. Director and Supervisor tasks are always personal and never matched by role. An active delegate also receives the delegator's approval tasks (personal and role-pool). |

#### 3.3.8 Approval Routing (Portal → Admin → Approval Routing)

System Admin maintains, per active department:
- **GM or OPM** — which post approves the department's GM/OPM step (default GM for a department never configured);
- **Director** — optional; adds the Director step for that department;
- **Supervisor** — on/off (default off); when on, requesters in the department who have a Supervisor get the Supervisor step.

Engine-wide: **Payments spanning several departments** are approved at GM or OPM (default GM) instead of by each department's manager/director.

GM/OPM **backup** users can be recorded but are not currently used for routing or authorisation (see §11 OI-L).

Only the last editor and edit time are kept; there is no change history.

| FR ID | Requirement |
|---|---|
| **ROUT-001** | Saving Approval Routing re-routes every document already awaiting approval to the approver the new settings resolve to, and reports how many were re-routed. |
| **ROUT-002** | Changing a user's primary role, department, active status or Supervisor likewise re-routes their in-flight approvals, so tasks never stay with someone who can no longer act. A failed re-sync does not undo the user change; it is reported. System Admin can also trigger a re-sync for all documents or for one document. |
| **ROUT-003** | Workflow step lists are edited in Portal → Admin → Approval Workflows; available step tokens: Supervisor, Department Manager, Director, GM/OPM, GM, OPM, Procurement Manager, Finance Manager, Finance Director, Finance BP, AP Clerk, Zone Manager (VMS — goes to every manager of the visit's access zone, any one approves; "Quality Manager" is its legacy name with the same behaviour). PO sign-off steps may use only company-wide posts. |

#### 3.3.9 Submit Preflight

The system can report, without submitting, every reason a document would currently be refused — status, over-budget hard block, missing required fields (e.g. the PR field gates in §3.2.1), and an approval step that cannot be routed — each with whether the user can fix it or must ask an admin. It uses the same rules as Submit and writes nothing. Today it is used by the AI Assistant (§3.19) to answer "can I submit this?"; no form button calls it.

### 3.4 Document PDF Generation

All four procurement documents (PR, PO, GR, PA) generate PDFs server-side using ReportLab (Python). Each PDF is rendered using the corresponding template configured in **EPMS Admin Panel → PDF Templates** (§3.6.6). Names are always printed as people's names, never as internal IDs. Quantities print as plain numbers.

**PR PDF:**
- Triggered automatically when PR reaches `approved` status (final workflow step), generated as a background task after the action response is returned
- Content: company logo (if enabled), PR number, meta grid (vendor, dept, cost center, budget code, dates), **Requested By**, **Service Owner** (Service / Project-Related PRs), line items table (conditional columns for material ID / supplier ID), total amount, notes, delivery address, an **Approvals table** (Step / Approved By / Date), header/footer notes from template, T&C if enabled
- Uploaded to File Server; `storage_key` stored in `PrAttachment`; appears automatically in PR's Attachments tab

**PO PDF:**
- Generated automatically when a locally created PO reaches `approved` status (final workflow step). For NC-imported POs the PDF is generated from the Attachments tab (statuses listed in §3.12.9, including `nc_pending`, because the printed PDF is what is signed before NC approval) and is **regenerated automatically when the last PO sign-off signature is captured** (§3.12.8)
- Uploaded to File Server; `storage_key` stored in `PoAttachment`; Procurement Officer downloads or sends it when placing the order
- **Header layout:** company logo (aspect-ratio preserved, longest side capped at 27 mm — 1.5× default size) on the left, "Purchase Order" document title below logo; PO number right-aligned top-right (no secondary "PURCHASE ORDER" sub-label)
- Content: meta grid (vendor, PO number, PO date, delivery date, delivery address, currency), line items table, subtotal/tax/total, header/footer notes and T&C from PDF Template settings. Detailed content rules — optional columns (Material ID, Supplier Item ID, Sample) dropped when blank on every line, delivery-date fallback to the earliest NC line arrival date, Incoterms and Buyer Notes (NC-internal notes never print), the Type 1 two-party signature block filled with sign-off signatures — are in **§3.12.9 PO PDF content**
- The meta grid shows **PO Number** (not PR Number); the PR linkage is an internal reference and is not printed on the vendor-facing document
- All line item cells are rendered as ReportLab `Paragraph` objects with `VALIGN=TOP`; long descriptions wrap within the Description column and never overflow into adjacent columns

**GR PDF:**
- Generated when the GR is **acknowledged**, and **regenerated when it is collected / confirmed** (replacing the first, so each GR has exactly one auto-PDF — the latest one carrying Collected By). Service GRs, which skip acknowledgement, get their PDF on confirmation. A GR that completes automatically at creation (PO with no PR requester) gets its PDF at creation.
- Uploaded to File Server; `storage_key` stored in `GrAttachment`
- Content: GR number, PO reference, received lines with quantities and conditions, Created By, Received By, Acknowledged By, Collected By, header/footer/T&C from template

**PA PDF:**
- Generated on PA **final approval**
- Uploaded to File Server; `storage_key` stored in `PaAttachment`
- Content: PA number, PO reference(s), payment amount, bank/payment details, **Applied By**, **Date Approved**, an **Approvals table** (Step / Approved By / Date), header/footer/T&C from template. PA unit prices are printed at full precision (not rounded to 2 dp).

**Approvals shown on PDFs (PR and PA):** an approval made by a delegate prints as "<delegate> on behalf of <approver>" (§1.5). Machine-written approvals (auto-skipped steps, legacy PMS-migration stamps) are not listed as approvers; an auto-approval for a person holding two consecutive steps is listed.

**Regenerate PDF:** PR, PO, GR and PA Detail → Attachments offer a **Regenerate PDF** button once the document is approved (GR: acknowledged) or later. It rebuilds the PDF with the current template and logo and replaces the previous auto-generated `<number>.pdf`, so there is always exactly one auto-PDF per document.

| Document | Statuses in which Regenerate PDF is offered |
|----------|-------------------------------------------|
| PR | `approved` |
| PO | `approved`, `issued`, `partially_received`, `fully_received`, `closed`, `nc_pending`, `nc_milk` |
| GR | `collection_pending`, `collected`, `confirmed`, `discrepancy` |
| PA | `approved`, `processed` |

**Storage rule:** All attachments — whether auto-generated PDFs or user-uploaded files — are stored on the **File Server** (§3.4.1). The `*_attachments` tables store only metadata (`filename`, `content_type`, `file_size`, `storage_key`). The `file_data` column exists as a legacy fallback column; the only new records that use it are GR PDFs generated while the File Server is unreachable (see ATT-001).

| FR ID | Requirement |
|-------|-------------|
| **ATT-001** | Every attachment write (user upload or system-generated PDF) must upload the binary to the File Server and store the returned `storage_key` in the attachment record. *Exception:* a GR PDF generated during acknowledge / confirm / auto-complete while the File Server is unreachable is stored inline in `file_data` so that the GR action does not fail; **Regenerate PDF** moves it to the File Server. No other new record may write `file_data`. |
| **ATT-002** | Download endpoints must first check `storage_key` (proxy from File Server), then fall back to `file_data` (legacy records and the ATT-001 exception only). |
| **ATT-003** | The File Server (`file-api :8005`) must be running before any EPMS or OA service that generates attachments is started. |
| **PDF_TMPL-001** | Every server-side PDF generator (PR, PO, GR, PA) must read `CompanyConfig.pdf_templates[doc_type]` at generation time and apply: **Show Logo** (embed `logo_data_url` if true, aspect ratio preserved — default longest side capped at 18 mm, other side scaled proportionally; PO PDF uses 27 mm cap), **Header Note** (printed below the document title if non-empty), **Footer Note** (printed at the bottom of every page if non-empty), **Show Terms + Terms Text** (append a T&C section on the last page if enabled). |
| **PDF_TMPL-002** | If `CompanyConfig.pdf_templates` is absent or the key for the document type is missing, defaults apply: logo shown (if `logo_data_url` is set), no header/footer note, terms hidden. |
| **PDF_TMPL-003** | Template changes take effect immediately on the next PDF generated; existing stored PDFs are not regenerated automatically (use **Regenerate PDF**). |
| **PDF_TMPL-004** | All PDF generators use one shared set of template helpers (template lookup, logo, header note, footer note, terms) — no duplicate template-reading logic in individual generators. |
| **PDF-REGEN-001** | PR, PO, GR and PA Detail → Attachments offer **Regenerate PDF** in the statuses listed above. It rebuilds the PDF with the current template/logo and replaces the previous auto-PDF. Anyone who can view the document can use it. |
| **PDF-REGEN-002** | The PO PDF is regenerated automatically when the last sign-off signature is captured (§3.12.8). |
| **PDF-ESC-001** | Any user-entered text printed on a PDF is escaped so that `&`, `<`, `>` and line breaks print literally. |

#### 3.4.1 File Server

A dedicated file storage service manages **all** document attachments across every UniOps module (EPMS and OA). Binary file data is stored on the File Server, not in the database — only metadata and `storage_key` references (see ATT-001 for the single fallback exception).

| Property | Value |
|----------|-------|
| Service | `file-api` (:8005) |
| Storage | Local filesystem organised as `{year}/{month}/{uuid}.{ext}` |
| Auth | Shared JWT (same secret as EPMS) |
| Max file size | 50 MB per file |
| Allowed types | PDF; PNG, JPEG, GIF, WEBP, HEIC; Excel (.xlsx/.xls); Word (.docx/.doc); CSV; plain text; saved emails (.msg, .eml). Other types are rejected with the list of allowed types. |

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

Every `*_attachments` table stores only metadata + `storage_key` (file-api UUID). The binary file lives on disk via file-api.

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

**Migration note:** EPMS Phase 1 attachment records that pre-date file-api integration may have a `file_data` (LargeBinary) column set and `storage_key` null. The download endpoint falls back to serving `file_data` for those legacy records. New uploads in all modules write to file-api.

**File Server FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **FS-001** | All new file uploads — user attachments, auto-generated PDFs, and invoice files — must be stored on the file server, not in the database (sole exception: ATT-001). No new `LargeBinary` columns are allowed. |
| **FS-002** | File download must require a valid Bearer JWT at every hop (client→service, service→file-api); unauthenticated requests return 401. |
| **FS-003** | Each uploaded file must be tagged with `service` (epms/oa), `doc_type`, and `doc_id` for auditability. See tagging table above. |
| **FS-004** | Legacy records with `file_data` blobs (EPMS Phase 1) are served transparently from the database; no data migration is required at go-live. |
| **FS-005** | The `invoice_attachments` table (OA expense-api) must not use `LargeBinary`. All invoice file uploads go to file-api and are referenced by `storage_key`; the former `file_data` column has been removed. |
| **FS-006** | The `expense_attachments` table (OA expense-api) uses `file_id` (UUID string) as the file-api reference. No binary data is stored. Applies to EXP, MIL, TRV, and CFM claims. |
| **FS-007** | Attachments on PR, PO and PA can be added or removed from both the Edit page and the Detail page at any time the user can edit/view the document; uploads and deletions apply immediately (not on Save). |
| **FS-008** | A failed download shows an error next to the attachment list (never fails silently). |
| **FS-009** | On a PA, the Document Chain **Attachments** button lists all files of the PA and its source documents (PR, PO(s), GR(s), invoice(s), agreement receipts), grouped by document, with download-one, download-all (zip) and print-all. |

### 3.5 Parts Catalog

- Maintained by holders of the **`parts_catalog`** Access Control Matrix permission (create / edit / delete); reading the catalog is open to every signed-in user
- Fields per part: Code, Category, Name, Description, Supplier, Supplier Part No, Supplier Item ID (ASIN/SKU/supplier catalog #), Unit Price (must be ≥ 0), Unit, **Image** (shown as a thumbnail in the list), Active/Inactive
- Supplier Part No and Supplier Item ID are both kept (Supplier Part No remains a required field and a CSV column)
- CRUD: add, edit, inline delete with confirmation
- Filter by search text, category, active status
- CSV Import / Export

### 3.6 Admin Panel

Global settings are managed in **Portal → Admin**; the EPMS Admin Panel keeps only procurement-specific settings. EPMS Admin sections whose content moved show a card linking to their Portal location. EPMS Admin Panel uses a left-nav + right-content layout.

| Setting | Where |
|---|---|
| Company name/logo, Currency, Security (MFA, Password Expiry), User Management, Departments, Units of Measure, Notification Settings (incl. task SMTP and per-task-type email policy), Remittance Advice, Approval Workflows & Approval Routing (§3.3.8), Approval Delegation (§1.5), Access Control (permission matrix and user roles, §3.6.7), NC / ERP MDM / WMS sync | **Portal → Admin** |
| Data Maintenance (§3.6.8) | **Portal → Data Maintenance** (permission `data_maintenance`) |
| Budget Config (incl. Cost Centers) | **Portal → Finance → Budget Config** |
| Email Settings (PO-to-vendor SMTP + PO email template), PDF Templates, Vendor Settings, Service GR SLA, GR Notification SLA, Prepayment Config, Collection Config, PMS Data Import | **EPMS → Admin Panel** |

#### 3.6.1 Company Settings

Moved to **Portal → Admin → Company** (EPMS shows a "moved" card).
- Company name, tagline, logo upload (base64 data URL)
- The logo is the one embedded in document PDFs (§3.4)

#### 3.6.2 Security Settings

Moved to **Portal → Admin → Security** (EPMS shows a "moved" card).

**MFA:**
- Company MFA switch: on/off; takes effect on next sign-in. When on, every user receives an emailed one-time code at sign-in; a user can also turn MFA on for themselves in My Profile (§3.1)

**Password Policy:**
- **Password Expiry**: Never / 30 / 60 / 90 days (recommended) / 180 / 365 days
- When set, users are forced to change their password at sign-in once it is older than the configured period; enforced by Portal as described in §3.1

SMTP configuration is no longer part of Security: the task-notification SMTP profile is in Portal → Admin → Notification Settings (§3.9.4) and the PO-to-vendor SMTP profile is in EPMS Admin → Email Settings (§3.6.5).

#### 3.6.3 User Management

Moved to **Portal → Admin → User Management** (EPMS shows a "moved" card).
- Full user table: name, email, role, department, Teams account, active status, created date
- Add user / edit user / deactivate / delete with confirmation
- **Reset Password**: portal modal (rendered via `createPortal` into `document.body`) to prevent browser password-manager autofill from targeting the user search box; `autoComplete="new-password"` on input; supports Enter key. Any admin-set, reset or imported password forces the user to choose a new password at next sign-in (§3.1)
- **CSV Export / Import**: export to `users.csv`; import matches by email, creates or updates; new accounts receive the company-standard initial password if the column is blank
- **Automatic approval re-sync:** changing a user's role, department, supervisor or active flag, or deactivating the user, automatically re-routes that user's in-flight approvals (department-level approval tasks pinned to the old holder move to the new holder). The save reports how many documents were re-synced; a re-sync failure does not undo the user edit. The same re-sync runs when roles are granted in Access Control and when Approval Routing is saved (§3.3.8).

#### 3.6.3a Departments — MOVED to Portal → Admin → Departments

As of 2026-05-27 ownership of Departments has moved to **mdm-api** (the master-data service) and the management UI lives in **Portal → Admin → Departments**. The EPMS Admin Panel no longer exposes Departments.

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

**Sections that live in the EPMS Admin Panel:**
- **Email Settings** — PO-to-vendor SMTP profile and PO email template (§3.6.5)
- **PDF Templates** — header/footer/logo/terms per document type (§3.6.6)
- **Vendor Settings** — the vendor category list offered when adding or editing a vendor
- **Service GR SLA** — reminder and manager-escalation days for the Service/Project PO completion-date reminder (two rungs; see §3.13.3)
- **GR Notification SLA** — stored, but **no scheduler reads it**: there is no GR acknowledgement reminder/escalation ladder (**Not implemented** — see §11). Open GR tasks are re-mailed only through the per-task-type notification policy and daily digest (§3.9.4)
- **Prepayment Config** — global prepayment cap (% of PO value, overridable per vendor category) and related prepayment rules (§3.15.4)
- **Collection Config** — stored, but not read by the system; there is no collection on/off switch (§3.13.4)
- **PMS Data Import** — legacy PMS import runs and attachment import (§3.16)

**Sections that moved out of EPMS:**
- **Approval Workflows** — moved to **Portal Admin Panel → Approval Workflows** (PORTAL.md §5.8). Binds action keys to configurable step sequences; stored in `CompanyConfig.workflow_defs`; drives the shared approval engine for all modules (see §3.3 for full specification). Department routing (who is the Department Manager / Director / GM-or-OPM for each department) is maintained in **Portal → Admin → Approval Routing** (§3.3.8). The former EPMS "Dept → GM/OPM Mapping" section has been removed.
- **Budget Config** — moved to **Portal → Finance → Budget Config**: cost centers, available fiscal years, warning/over-budget thresholds (default 80 % / 100 %), Over-Budget Approval Mode (`fm_gm_opm` / `fm_only` / `allow` / `hard_block`, see §3.2.6), factor-decomposition limits, opening balances. Saving requires the Admin Panel permission.
- **Role Management** — moved to Portal → Admin → Access Control (permission matrix and per-user primary/additional roles), Portal → Admin → Approval Routing (§3.3.8) and Portal → Admin → Approval Delegation (§1.5). See §3.6.7.
- **Notification Settings** — moved to Portal → Admin → Notification Settings: task SMTP, default channel (Email / Teams / Both / None), Teams Webhook URL, role shared mailboxes, daily digest time and the per-task-type email policy (see §3.9.4)
- **Currency Settings** — moved to Portal → Admin → Currency: built-in currencies (CAD, USD, EUR, CNY) with enable/disable toggle and default selection; custom currencies can be added, edited, and deleted (each with code, symbol, and display name); custom currencies participate in the same enable/toggle/default flow as built-in ones

#### 3.6.4a Budget Accounts List — REMOVED

The Budget Accounts sub-panel (inline-editable Annual Budget / Committed / Actual Spent with CSV import/export) is no longer part of Budget Config. The account catalog and budget plans are separate pages under **Portal → Finance** and are specified in the Finance / budget PRD. The read-only view of plan vs actual is the Budget Dashboard (§3.8).

#### 3.6.5 Email Settings *(renamed from "Email Templates" 2026-05-28)*

Page houses two sub-sections, both configured in **EPMS Admin Panel → Email Settings**:

**1. PO Email SMTP** — Dedicated mailbox for vendor-facing PO emails (`po_smtp_*` on CompanyConfig). Fields: Host / Port / Username / Password (masked) / From Address / TLS. Any field left blank falls back to the **internal task notification SMTP** profile (Portal Admin → Notification Settings, `smtp_*`). Why split: many companies route procurement correspondence through a dedicated `purchasing@` mailbox while internal approvals flow through `noreply@`. "Test PO SMTP" calls `POST /api/v1/config/test-smtp` with `{ kind: "po" }` to exercise the full fallback path before issuing a real PO. Port 465 uses implicit TLS; any other port with TLS on uses STARTTLS. See EMAIL-SPLIT in PORTAL.md §5.7.

**2. PO Email Template** — Subject and body editable. Variable substitution uses `{variable_name}` syntax. Subject + body persist on `po_email_subject` / `po_email_body`. The template renders client-side before the Procurement Officer reviews it on the Send PO modal.

**PO Order Email** (sent to vendor when Procurement Officer places the order via email):
- Template configured in Admin Panel → Email Settings; subject and body editable per PO before sending
- Syntax: single-brace `{variable_name}` — e.g. `{po_number}`, `{vendor_name}`
- Available variables: `{po_number}`, `{po_date}`, `{vendor_name}`, `{vendor_email}`, `{expected_delivery}`, `{delivery_address}`, `{line_items}`, `{subtotal}`, `{tax}`, `{total}`, `{company_name}`, `{sender_name}`

**Notification email templates** are system defaults keyed by task type (the mapping is described in §3.9.2). They are **not currently editable in any admin screen** (see §11). A stored template that still equals a superseded system default is upgraded automatically to the current default. A task type with no template of its own uses the generic **`task_assigned`** template ("New task: {task_title}").

Seeded template keys: `task_assigned` (generic), `pr_approval_request`, `pr_returned`, `pr_rejected`, `pr_approved`, `po_creation_request`, `po_approval_request`, `po_signature_request`, `po_signoff_returned`, `po_signoff_complete`, `po_place_order`, `gr_created`, `gr_collection_ready`, `service_gr_pending`, `service_gr_due`, `service_gr_escalation`, `create_pa_reminder`, `confirm_receipt`, `pa_approval_request`, `pa_approved`, `prepayment_settlement_overdue`, `manual_reminder`, `daily_pending_reminder`, `sla_escalation`, `match_invoice_assigned`, `match_review_request`, `exception_resolution_request`.

All substituted values are HTML-escaped in email bodies (§3.9, NTF-008).

#### 3.6.6 PDF Templates

Configurable in **EPMS Admin Panel → PDF Templates**. One template per document type: **PR, PO, GR, PA**.

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
- Template changes take effect on the next PDF generated — existing stored PDFs change only when **Regenerate PDF** is used (§3.4)

**Document coverage:**

| Document | PDF Trigger | Template Key |
|----------|------------|--------------|
| PR | Auto-generated on final approval (background task) | `pr` |
| PO | Auto-generated on final approval (local POs); on demand / after sign-off for NC-imported POs (§3.12.8, §3.12.9) | `po` |
| GR | Generated on acknowledgement, regenerated on collection/confirmation; at creation for auto-completed GRs | `gr` |
| PA | Generated on final approval | `pa` |

> Known label defect: the PDF Templates page describes the PA PDF as generated "when PA is created"; it is generated on approval (see §11).

#### 3.6.7 Role Management — MOVED to Portal → Admin → Access Control

The EPMS Role Management, Access Control Matrix, custom-role, temporary-assignment and Director/Supervisor tabs have been removed from EPMS. Roles and permissions are owned by the identity service and administered in **Portal → Admin → Access Control** (tabs *Permission Matrix* and *User Roles*). Department approver posts are set in **Portal → Admin → Approval Routing** (§3.3.8); absence cover is **Approval Delegation** (§1.5).

---

##### 3.6.7.1 Role List

Roles are a **fixed, built-in set** (§1.1): Requester, Department Admin, Department Manager, GM, OPM, Procurement Officer, Procurement Manager, Warehouse Staff, AP Clerk, Finance BP, Finance Manager, Finance Director, ERP PA Officer, Payment Officer, CFO, Auditor, Vendor Manager, System Admin. Supervisor and Director are derived from user records and Approval Routing rather than assigned. ERP PA Officer, Payment Officer and Finance Director can be held only as additional roles.

Creating, renaming or deactivating roles is not available. Each user has one primary role and any number of additional roles, assigned in Access Control → User Roles.

---

##### 3.6.7.2 Access Control Matrix Editor

A visual grid editor (Portal → Admin → Access Control → Permission Matrix) that shows permissions for every role across all document/feature columns. System Admin can toggle individual cells on or off.

**Layout:**

- **Rows** — one per built-in role. Rows cannot be added or removed.
- **Columns** — fixed permission scopes; columns cannot be renamed or reordered. The EPMS columns include:

  | Column | Permission key | Scope |
  |--------|---------------|-------|
  | View PR | `view_pr` | Can open the Purchase Requisition list/detail (gates `GET /pr`, `GET /pr/{id}`). Per-role visibility scope (§1.3) still applies on top. |
  | View PO | `view_po` | Can open the Purchase Order list/detail (gates `GET /po`, `GET /po/{id}`). |
  | View GR | `view_gr` | Can open the Goods Receipt list/detail (gates `GET /gr`, `GET /gr/{id}`). |
  | View Invoice | `view_invoice` | Can open the Invoice list/detail (gates `GET /invoices`, `GET /invoices/{id}`). |
  | View PA | `view_pa` | Can open the Payment Application list/detail (gates `GET /pa`, `GET /pa/{id}`). |
  | Create PR | `create_pr` | Shown in the matrix but **not enforced** — any signed-in user can create a PR (§1.3). |
  | Approve PR | — | Can appear as a step approver in PR workflow |
  | Create PO | — | Can create new Purchase Orders |
  | Approve PO | — | Can appear as a step approver in PO workflow |
  | Create GR | `create_gr` | Can create Goods Receipt records |
  | Approve PA | — | Can appear as a step approver in PA workflow |
  | Invoice Upload | `invoice_upload` | Can upload and process vendor invoices |
  | Vendor Master | `vendor_master` | Can view/edit Vendor Master records |
  | Parts Catalog | `parts_catalog` | Can create/edit/delete Parts Catalog entries |
  | Budget View | — | Budget Dashboard access and scope are governed by the budget view permissions described in §3.8 |
  | Admin Panel | `admin_panel` | Can access Admin Panel |

  Further permission keys introduced for agreements, invoice matching, vendor credits, PA writing and PO sign-off are listed in §1.3.

  The five **View** columns are independent toggles per role. Disabling a View cell makes the corresponding list endpoint return an empty result set and the detail endpoint return HTTP 404 — there is no client-side data leak. View permissions stack with, not replace, the per-role scope defined in §1.3 (e.g. a Requester with `view_pr=✅` still sees only their own PRs).

- **Cells** — each cell is a **toggle switch** (enabled = ✅ / disabled = —). Clicking a cell flips its state. No inline text editing.

**Behaviour rules:**

- Changes take effect on save, for all holders of the role, on their next request.
- Some built-in cells are locked: core permissions that are logically required for the role to function cannot be disabled; an attempt to clear a locked cell is refused. Locked cells display a lock icon (🔒) and are not clickable.
- **Default locked View cells** (cannot be turned off via the Matrix):

  | Role | Locked View columns | Reason |
  |------|---------------------|--------|
  | `requester` | View PR | Requesters must always be able to see their own submissions |
  | `procurement_officer` | View PR, View PO, View GR | Required to create POs from approved PRs and follow up on receipts |
  | `procurement_manager` | View PR, View PO, View GR | Required to approve POs |
  | `warehouse_staff` | View GR | Required to record physical receipts |
  | `ap_clerk` | View Invoice, View PA | Required for invoice matching and PA preparation |
  | `finance_bp` | View PA | Required to perform PA review |
  | `finance_manager` | View PA | Required to perform final PA approval |
  | `erp_pa_officer` | View PO, View PA | Required to raise PAs for NC-imported POs |
  | `payment_officer` | View PA | Required to execute PA payments (§3.18) |
  | `finance_director` | View PA | Required for the PA payment sign-off step |

- **Defaults for the newer roles:** Supervisor = View PR; Director = View PR, View PA; Finance Director = all View columns + Finance; ERP PA Officer = all View columns; Payment Officer = View PO, View Invoice, View PA + Finance; CFO and Auditor = all View columns.
- For multi-role users (§1.2), effective permissions are the **union** of the user's primary role and all additional roles.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **RM-010** | The Access Control Matrix must display all built-in roles as rows and all permission scopes as fixed columns. |
| **RM-011** | Each matrix cell is a toggle (on/off). System Admin can click any unlocked cell to change its state; locked cells cannot be changed. |
| **RM-012** | Column definitions (names and meaning) are fixed and cannot be added, removed, or renamed via the UI. Adding a new permission scope requires a software release. |
| **RM-013** | Changes to the matrix are saved explicitly. Unsaved cells are marked (amber dot) and a bar shows the number of unsaved changes. |
| **RM-014** | Permission changes take effect immediately for all users with that role upon save — no re-login required (enforced at the API level on each request). |
| **RM-017** | The Access Control Matrix must include the five **View** columns (`view_pr`, `view_po`, `view_gr`, `view_invoice`, `view_pa`). When a View cell is unchecked for role R, every user holding R as their **only** active role receives an empty list response and HTTP 404 on the corresponding detail endpoint. |
| **RM-018** | View-permission evaluation must use the **union of all active roles** for the requesting user (primary role + additional roles held in the identity service). A user receives the permission if **any** of their active roles grants it. |
| **RM-019** | Default View settings for built-in roles must match the §1.3 visibility table. In particular `warehouse_staff` defaults to `view_gr` only and `vendor_manager` defaults to none of the five View columns. |

The matrix records only the last editor and time per cell; there is no per-change audit history and no "Reset to Default" action.

---

##### 3.6.7.3 Special Role User Assignment

The former EPMS assignment table has been removed. Posts are assigned as follows:

| Need | Where |
|------|-------|
| Give a user a role such as GM, OPM, Procurement Manager, Finance Manager, Finance Director, Payment Officer, ERP PA Officer, Vendor Manager | Portal → Admin → Access Control → User Roles (primary or additional role) |
| Who approves for each department (Department Manager, Director, GM vs OPM, supervisor layer on/off) | Portal → Admin → Approval Routing (§3.3.8) |
| Temporary cover while an approver is away | Portal → Admin → Approval Delegation (§1.5) |

---

#### 3.6.8 Data Maintenance

System-level corrections to procurement records are made in **Portal → Data Maintenance** (permission `data_maintenance`). Records that can be viewed, edited or deleted: PR, PO, GR, Invoice, PA, Agreement, Agreement Receipt and Task. Every change is written to an admin audit log.

| FR ID | Requirement |
|-------|-------------|
| **DM-001** | Setting a document's approval step closes its outdated approval tasks and issues the approval task for the chosen step (to the resolved approver, or to an explicitly chosen role/user). The screen reports whether the task was issued and warns when no approver could be resolved. |
| **DM-002** | Changing a PR's requester re-routes its in-flight approval. Changing a PR's Service Owner moves the PR's open personal receipt tasks (`confirm_receipt`, `acknowledge_gr`, `confirm_service_gr`) to the new owner (§3.13.3). |
| **DM-003** | Tasks can be viewed and deleted here. Because EPMS and Portal are separate apps, the EPMS Task Inbox reflects such changes on its next poll or when the user presses **Refresh**. |
| **DM-004** | Agreements and Agreement Receipts can be maintained here (§3.17). |
| **DM-005** | A PA's invoice and receipt links can be repaired; on an already-paid PA the repair also replays the invoice and AP payment write-back. PA totals can be recomputed when a charge or tax rate is changed without editing lines. |
| **DM-006** | Deleting the last PA of a PO reopens that PO's Create PA task. |
| **DM-007** | A PA's pre-tax amount cannot be edited in Data Maintenance; its lines are the only input. (a) Changing only a charge or the tax rate recomputes from the lines only when they add up to the stored pre-tax amount; when they do not, the pre-tax amount is kept and the tax is re-derived from it only if the rate changed. (b) A line edit that would change the pre-tax amount is refused unless the operator confirms it; the edit dialog shows the current and the new amount and enables Save only once the change is confirmed. A line edit that leaves the pre-tax amount unchanged needs no confirmation. |

### 3.7 Dashboard

The dashboard shown is decided **by the server** from the user's roles; the frontend renders whichever kind the backend returns.

| Role(s) | Dashboard |
|---------|-----------|
| dept_manager, gm, opm, director, supervisor, dept_admin | Approver dashboard: KPI cards + **Pending Approvals** (functional Approve/Return/Reject buttons) + Budget Overview |
| procurement_officer, procurement_manager | Procurement dashboard |
| warehouse_staff | Warehouse dashboard |
| ap_clerk | AP Clerk dashboard |
| payment_officer | Payment Officer dashboard (also used when the primary role has no dashboard of its own and the user holds Payment Officer as an additional role) |
| finance_bp | Finance BP dashboard |
| finance_manager | Finance Manager dashboard |
| cfo | CFO dashboard |
| auditor | Auditor dashboard |
| vendor_manager | Vendor Manager dashboard |
| system_admin | System Admin dashboard |
| any other role (incl. requester) | Requester dashboard: KPI cards + task list + PR pipeline |

**Pending Approvals panel** (Approver dashboard):
- Shows **only** documents where the current user has an open `approve_*` task assigned to them — either personally (`assigned_user_id = user_id`) or via a role-pool task whose role the user holds (primary or additional). This is the same rule as the Task Inbox so the two surfaces agree.
- A document must **not** appear in the Pending Approvals panel if the open approval task for its current step is assigned to a different user (e.g. an OPM-assigned PO must not appear on the GM's dashboard).
- While an Approval Delegation is active (§1.5), the delegate also sees the delegator's pending approvals, and they count in the delegate's KPI cards.
- Approve/Return/Reject buttons call the appropriate `POST /{doc_type}/{id}/action` endpoint; invalidate dashboard + document + task queries on success. Supported for PR, PO, and PA.

#### 3.7.1 Task Inbox — Task Types

The Task Inbox lists every open task the current user can act on. Tasks live in one shared table written by EPMS, the approval engine, Finance and VMS, so the EPMS inbox also carries OA, Finance and VMS work; each card opens the page where the work is done (EPMS, Finance, VMS or Portal). This table is the canonical task-type list.

| Task type | Label | Assigned to | Raised when | Closes when |
|---|---|---|---|---|
| `approve_pr` / `approve_po` / `approve_pa` / `approve_agr` | Approve … | Approver of the current step (person, or role pool for pool roles) | Document submitted / advanced | Approved/returned/rejected, or document leaves submitted/in_review |
| `revise_pr` / `revise_po` / `revise_pa` / `revise_agr` | Revise … | Document creator | Document returned | Resubmitted (document leaves draft/returned) |
| `create_po` | Create Purchase Order | Procurement Officer pool | PR fully approved | PR has a PO |
| `place_order` | Place Order | Procurement Officer pool | PO fully approved | PO no longer `approved` |
| `sign_po` / `revise_po_signoff` | Sign Purchase Order / Revise PO Sign-off | Configured signatories / submitter | NC-imported PO enters sign-off / sign-off returned (§3.12.8) | Signed / resubmitted |
| `create_prepayment_pa` | Create Prepayment PA | PR requester (else PO sign-off submitter / creator) | Prepaid PO approved | A prepayment PA exists |
| `acknowledge_gr` | Acknowledge Goods Receipt | PR requester | Physical GR created | GR leaves `pending_ack` |
| `collect_goods` | Collect Goods | PR requester | GR acknowledged | GR leaves `collection_pending` |
| `confirm_service_gr` | Confirm Service Receipt | PR Service Owner (else requester) | Service GR awaiting confirmation | GR leaves `collection_pending` |
| `confirm_receipt` | Confirm Goods Receipt | PR Service Owner/requester (Service/Project POs); Warehouse Staff pool (physical POs) | Invoice matched to an unreceived PO, or Service/Project PO completion date reached (§3.13.3) | PO has receipt evidence |
| `gr_damage_report` | Report Goods Damage | Procurement Officer pool | GR line recorded damaged | Manually |
| `match_invoice` | Match Invoice to PO | Named AP user | Invoice assigned for matching | Invoice leaves unmatched/exception |
| `review_match` | Confirm Invoice Match | AP Clerk pool | Delegate matched an invoice | Invoice leaves match_review |
| `resolve_exception` | Resolve Match Exception | AP Clerk pool | Match outside tolerance | Invoice leaves exception |
| `attach_receipts` | Attach Invoice Receipts | Agreement owner (else an active Department Manager of the agreement's department; else AP Clerk pool) | Invoice matched to a house-account agreement with no receipts attached and no settlement declared (AGR-034) | Receipts attached or settled without receipt evidence; invoice unmatched, deleted or listed on a live PA |
| `create_pa` | Create Payment Application | PR requester; **ERP PA Officer pool** for NC-imported POs | Invoice matched (3-way) on a received PO | PA exists, or matched invoice removed |
| `confirm_settlement` | Confirm Settlement | Finance BP pool | Zero-cash settlement PA with a variance (prepayment exceeded final value) | Settlement confirmed |
| `process_pa` | Process Payment Application | Payment Officer pool | PA fully approved | PA processed (paid) or leaves approved |
| `confirm_period` / `chase_agreement_invoice` | Confirm Service Period / Chase Missing Invoice | Agreement owner | Agreement billing period due / overdue (§3.17) | Period accepted / no overdue period |
| `import_erp_vendor` / `resolve_nc_sync_error` | Import ERP Vendor / Resolve NC Sync Error | System Admin | NC purchase sync could not import a document (§3.12.6) | Next sync succeeds |
| `resolve_jv_validation`, `resolve_ap_duplicate_invoice` | Resolve JV Validation Findings / Resolve Duplicate NC Invoices | System Admin / AP Clerk | Finance sync finds issues | Findings cleared |
| `approve_budget_plan` / `revise_budget_plan`, OA `approve_<claim>` / `revise_<claim>` / `process_expense`, VMS tasks | (other modules) | per module | per module | closed by the owning module |

**Inbox layout:** tabs **All / Urgent / Normal / Completed** (Completed = tasks completed today); stat cards Urgent / Normal / Completed today; tasks grouped by type in a fixed order; each card shows type, document number, vendor, amount and due date when known; NC-sync, JV-validation and duplicate-invoice cards also show the reason text. A **Refresh** button re-reads the inbox. There is no "Mark Done" button.

**Task Inbox FR IDs:**
- **DSH-001** — The inbox lists all open tasks the user can act on, grouped by task type (fixed order: approvals, payment/placement, creates, receiving, invoice, settlement, revisions), newest first within a group. Tabs: All, Urgent, Normal, Completed (today's completions).
- **DSH-002** — Each card shows the task label, document number, vendor and amount (when known), due date (when set), and for ERP/finance finding tasks the reason text. The card opens the page where the work is done — in EPMS, Finance, VMS or Portal as appropriate. *SLA due dates and colour-coding (green / amber / red) are* ***Not implemented*** *— no task type sets a due date (see §11).*
- **DSH-003** — Tasks cannot be dismissed from the inbox. A task closes only when its work is done. Approval tasks can never be completed through the task API (409); a non-approval task closed through that API is not re-raised by the backfill.
- **DSH-004** — When a `create_po` task is created, the assigned Procurement Officer(s) are notified according to the per-task-type policy (§3.9.4); `create_po` is email-on / immediate by default. The notification includes PR number, title, vendor name, amount, and a direct link to the PR detail page.
- **DSH-005** — When a `place_order` task is created, the assigned Procurement Officer(s) are notified according to the per-task-type policy (§3.9.4); `place_order` is email-on / immediate by default. The notification includes PO number, vendor name, amount, and a direct link to the PO detail page.
- **DSH-006** — Dashboard Pending Approvals "Approve / Return / Reject" inline action buttons must work for all document types (PR, PO, PA). Clicking a button dispatches the action to the correct API endpoint based on `doc_type`; the dashboard refreshes on success.
- **DSH-007** — Approval buttons on a detail page (PO, PR, PA) are shown to users who hold an open approval task for the current step — through their primary role, an additional role, a personal assignment, or an active Approval Delegation (§1.5) — not only to users whose base role matches the step role.
- **DSH-008** — **Visibility:** a task with a named assignee is visible only to that person; a role-pool task is visible to every holder of the role (primary or additional). Director and Supervisor approval tasks are always personal and never broadcast. During an active approval delegation the delegate also sees the delegator's approval tasks (personal and role-pool); non-approval tasks are not shared. System Admin sees all tasks.
- **DSH-009** — The header task badge displays the real-time count of open tasks assigned to the current user, polling every 60 seconds. Clicking navigates to the Task Inbox page.
- **DSH-020** — **Self-healing:** each inbox load closes tasks whose document no longer needs that action (status-based rules declared per task type — e.g. approve_* only while submitted/in_review, revise_* only while draft/returned, match_invoice only while unmatched/exception, process_pa only while approved; plus the create_po / place_order / create_pa / create_prepayment_pa rules in the table) and creates missing `create_po` / `place_order` / `create_pa` tasks for documents created on or after 2026-06-01. Concurrent loads cannot create duplicate tasks. Every new task type must declare its liveness rule (enforced by an automated test).
- **DSH-021** — A **Refresh** button re-reads the inbox immediately (changes made in Portal, e.g. Data Maintenance, are otherwise picked up by the 60-second poll).
- **DSH-022** — When the approver behind a department role changes (user edit, role grant, Approval Routing save), open approval tasks are re-pointed to the new holder automatically (§3.6.3, §3.3.8).

#### 3.7.2 Role Dashboard Layouts

Each dashboard has four KPI cards plus the panels listed. Time-based KPIs ("this month", "today") are keyed on the event date (paid date, approval date), not on the record's last-updated time.

| Dashboard | KPI cards | Panels |
|-----------|-----------|--------|
| **Requester** | Active PRs · Pending Payments · Paid This Month · Overdue Tasks | Task list (8 most recent open tasks) · PR pipeline · Quick Actions (New PR · View My PRs) |
| **Approver** (Dept Manager, Dept Admin, Supervisor, Director, GM, OPM) | Pending Approvals · Open POs · Total Committed (FY) · Over-Budget Depts | Pending Approvals (inline Approve/Return/Reject) · Budget Overview |
| **Procurement** | Open POs · POs Pending Approval · Total Committed (FY) · Open GRs | Recent POs · Quick Actions |
| **Warehouse** | GRs Awaiting ACK · Collection Pending · Discrepancies · Completed This Month | Open GRs |
| **AP Clerk** | Invoices to Match · Exceptions · PAs Pending · Processed This Month | Recent Invoices |
| **Payment Officer** | PAs Awaiting Payment · Value Awaiting Payment · Processed This Month · Value Processed This Month | — |
| **Finance BP** | PAs In Review · Approved Today · Total PA Value Pending · Processed This Month | — |
| **Finance Manager** | PAs Pending Approval · Over-Budget Accounts · Open POs Value · Pending Invoices | Budget Overview |
| **CFO** | Total Budget (FY) · Total Committed · Total Spent (with utilisation) | — |
| **Auditor** | Total PRs · Total POs · Total Invoices · Total PAs | — |
| **Vendor Manager** | Total Vendors · Active Vendors · Inactive Vendors · Categories | — |
| **System Admin** | Total Users · Active Users · Departments · System Health | — |

Known limitations: the System Admin "System Health" card is a static label ("Healthy") — no API response time, DB, notification-service or backup monitoring is built, and there is no user-activity (logins / failed attempts) panel. "Overdue Tasks" is always 0 because no task type sets a due date (see §11). "Total Committed" reflects the budget ledger, which the purchase chain does not write (§3.8).

**Dashboard FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **DSH-010** | Dashboards load on open and refresh when the user returns to the tab or after an action on the page; they do not auto-poll. The Header task badge polls every 60 s (DSH-009). |
| **DSH-011** | The task list on a dashboard must be the same data source as the Task Inbox page (§3.7.1); completing a task from the dashboard must remove it from the Task Inbox and vice versa. |
| **DSH-012** | The Pending Approvals panel must show **only** documents for which the current user holds an open `approve_*` task (personal assignment, role-pool task for a role they hold, or active delegation). The backend must filter using task data, never by document status alone. A document assigned to User A's approval step must not appear on User B's dashboard even if both users share the same base role. |
| **DSH-012a** | Multi-role users see tasks from all their active roles (primary and additional) aggregated in one list. |
| **DSH-013** | Overdue tasks (due date < today) highlighted in red in dashboard task lists — **Not implemented** (no task type sets a due date); see §11. |
| **DSH-014** | Dashboard panels that reference budget data must respect role-based budget visibility rules (§3.8). |
| **DSH-017** | All dashboard Quick Action buttons must be conditionally rendered based on the user's active permissions (e.g. "New PR" shown only to roles that can create PRs). |
| **DSH-018** | The dashboard shown is decided by the server from the user's roles; a user whose primary role has no dashboard but who holds Payment Officer as an additional role gets the Payment Officer dashboard. |
| **DSH-019** | Delegates see their delegators' pending approvals in Pending Approvals, and in their Pending Approvals / Overdue Tasks counts, while the delegation is active. |

### 3.8 Budget Dashboard

Plan vs actual by budget account for one fiscal year. Opened from **Portal → Finance → Budget Dashboard** (requires *View Budget Dashboard*); it is no longer in the EPMS sidebar.

**Who sees what** (Access Control Matrix, applied server-side to every actuals request):

| Permission | Scope |
|---|---|
| Full Access Budget View (`finance.budget.view_all`) | All cost centers. Default: GM, Finance Manager, Finance BP, AP Clerk, CFO, Auditor, System Admin |
| Department-Related Budget View (`finance.budget.view_dept`) | Cost centers of the user's own department, every department they direct, and — for the OPM — every department routed to OPM. Default: all other roles |
| neither | Nothing; the page says no budget is visible |

OPM and Procurement Manager do not have company-wide budget view (decision 2026-08).

**Controls:** fiscal year (from Budget Config's available fiscal years); cost center (within scope); a scope chip ("Company-wide" / "My Department(s)"); *Include unposted vouchers* (ticked by default — counts vouchers entered in NC but not yet tallied; discarded/deleted vouchers never count); *Manage Plans* link.

**Summary cards:** Annual Budget (current approved plans), Committed, Actual Spent (NC), Available, % utilised.

**Alerts:** accounts whose NC actual for the year reaches the over-budget threshold (red) or the warning threshold (amber) of their annual budget, using the thresholds in Budget Config (`CompanyConfig.budget_admin_config`); clicking an alert scrolls to and highlights the account. Thresholds drive display colours only — no budget alert emails are sent.

**Monthly grid:** accounts grouped by L1 category (each group collapsible, with expand/collapse all), sorted by code; each month shows the plan amount and the NC actual (red when actual exceeds plan), with year totals, a sticky header and a Total row. An account can be expanded to show its NC actual by vendor/customer and month. *Export XLSX* downloads the grid.

**Exclusions:** Payroll (CRM007), Depreciation (CRM004) and shut-down loss (CRM09912) are not shown — they are tracked at category level only.

- See §9 for Annual Budget / Committed / Actual / Available definitions

### 3.9 Notifications

Notifications are driven by the shared task table: every task — from EPMS, OA, Finance or VMS — is picked up by one notification outbox, which decides per task type whether and when to email (§3.9.4). The Task Inbox is always populated regardless of email settings.

#### 3.9.1 Notification Channels

| Channel | Mechanism | Config |
|---------|-----------|--------|
| **Email** | SMTP via the task SMTP profile (Portal → Admin → Notification Settings). Port 465 uses implicit TLS; any other port with TLS on uses STARTTLS. | System default template per task type (§3.6.5) |
| **Microsoft Teams** | Adaptive Card posted to the company Incoming Webhook | Card shows the task details and a single **"View in EPMS"** button (deep link). No in-card Approve/Return — approval happens in the web app only (§1.4). Sent only when the channel setting includes Teams, the user has a Teams account, and a webhook URL is set. |

#### 3.9.2 Notification Events

Task-generating events notify according to the per-task-type policy in §3.9.4. Recipients follow NTF-004. Each task type maps to a template (§3.6.5); a type without its own template uses the generic `task_assigned` template. Links open the module that owns the document (EPMS, OA, Finance, VMS); `confirm_receipt` links to New GR pre-filled for the PO.

| Event | Recipient(s) | Content |
|-------|-------------|---------|
| PR submitted for approval | Step-0 approver — for `dept_manager` step: the Department Manager of the PR's department (per Approval Routing); for other roles: the specific assigned user or the role pool | PR number, title, amount, requester, link to PR detail |
| PR advanced to next approval step | Next step approver (same routing logic as above) | Same as above + step label |
| PR approved (final) — PO creation trigger | Procurement Officer pool (`create_po`) | "Action Required: Create PO for PR {pr_number}" — PR number, title, vendor, amount, currency, link to PR detail page |
| PR returned for revision | PR requester (`revise_pr`) | Returned by whom, comment, link to edit |
| PO submitted for approval | PO workflow step approver | PO number, vendor, amount, link |
| PO approved — place order | Procurement Officer pool (`place_order`) | PO number, vendor, amount, link to PO detail |
| PO sign-off requested / returned / complete | Signatories (`sign_po`) / submitter (`revise_po_signoff`) / submitter (completion email, no task) | PO number, link to PO detail (§3.12.8) |
| GR created (goods received) | The Requester who raised the linked PR (`acknowledge_gr`) | GR number, items, link to acknowledge |
| Goods ready for collection | Same Requester (`collect_goods`) | Storage location, link |
| Service GR pending confirmation | PR Service Owner (`confirm_service_gr`) | Link to confirm |
| Service/Project PO completion date reached | PR Service Owner (`confirm_receipt`); escalation email to the Owner's Department Manager only after the Owner was reminded (§3.13.3) | PO number, days since completion date, link to New GR |
| Invoice matched to PO (3-way) | The PR Requester, or the ERP PA Officer pool for NC-imported POs (`create_pa`; re-notified if another invoice is added to an open task) | Invoice number, PO number, action link to create PA |
| Invoice matched to an unreceived PO | Receipt owner (`confirm_receipt`) | PO number, link to New GR |
| Invoice assigned / match review / match exception | Named AP user (`match_invoice`) / AP Clerk pool (`review_match`, `resolve_exception`) | Invoice number, match result, link |
| PA submitted for approval | PA workflow step approver (`approve_pa`; default off) | PA number, amount, link |
| Payment ready to process | Payment Officer pool (`process_pa`; default off) | PA number, amount, link |
| Prepaid PO approved | PR requester (`create_prepayment_pa`) | PO number, link to create prepayment PA |
| Agreement invoice overdue | Agreement owner (`chase_agreement_invoice`; sends its own email) | Agreement, overdue period |

#### 3.9.3 User Notification Preference (Profile Settings)

Each user can override the company default in their **My Profile** page:

| Setting | Options |
|---------|---------|
| Notification channel | Email only / Teams only / Both / None |

- Preference stored on the `User` record (`notification_channel`)
- "None" silences personal outbound notifications for that user (in-app Task Inbox still populated). Emails to a role shared mailbox ignore personal preferences.

#### 3.9.4 Notification Settings (Portal → Admin)

In **Portal → Admin → Notification Settings**:
- **Task SMTP** (host / port / user / password / from / TLS) and **Send test**
- **Default Notification Channel**: Email only / Teams only / Email + Teams / **None**. *None* is a master switch that turns off all task notifications company-wide (immediate, digest, reminders and admin alerts)
- **Teams Webhook URL**: one company-level webhook
- **Role Shared Mailboxes**: per role, an address that receives role-pool task notifications instead of every role holder (email only; suppressed when the company channel is Teams only)
- **Daily Digest Time (UTC)**: default 08:00; a change applies within about 15 minutes
- **Task Notifications** table: for every task type (grouped by module), *Email* on/off and *Immediate* vs *Daily digest*. The list is derived from the system's task types (including OA claim types and custom forms), so new task types appear automatically. `chase_agreement_invoice` is shown as self-notifying (no checkbox)

Notification wording is not editable here (templates are system defaults, §3.6.5).

| FR ID | Requirement |
|---|---|
| **NTF-001** | A new task is evaluated within about a minute of creation. Email on + Immediate → notification sent then; Email on + Digest → included in the recipient's next daily digest while still open; Email off → no email. |
| **NTF-002** | Defaults: email on/immediate for approve_pr, revise_pr, create_po, approve_po, revise_po, place_order, create_prepayment_pa, sign_po, revise_po_signoff, acknowledge_gr, collect_goods, confirm_service_gr, gr_damage_report, match_invoice, review_match, resolve_exception, confirm_receipt; all other types (incl. approve_pa, process_pa, agreement, OA claim, budget-plan and VMS tasks) are off until an admin turns them on. |
| **NTF-003** | The daily digest is one email per recipient listing every open task of digest-mode types (task, document link, details, created date; tasks < 24 h marked NEW), newest first. |
| **NTF-004** | Recipients: the named assignee; for approval tasks during an active delegation, the delegate instead of the delegator (§1.5); for role-pool tasks, the role's shared mailbox if set, else every active holder of the role (primary or additional). A requester task with no named requester is never broadcast — System Admins receive an alert instead. |
| **NTF-005** | A user's personal channel (My Profile) applies to personal emails; *None* stops them. Shared-mailbox emails ignore personal preferences. |
| **NTF-006** | Reassigning a task (or adding another invoice to an open Create PA task) re-notifies the holder under the same policy. |
| **NTF-007** | Task types without a dedicated template use the generic "New task" template; links always open the module that owns the document. |
| **NTF-008** | All user-entered values placed in an HTML email are escaped; admin-written template markup is kept. |
| **NTF-009** | **Send reminder** (PR and PO Approval Timeline, current step; OA expense claim Approval Status): emails the current approver(s) immediately, independent of the type's policy; at most once per 24 h per approval task (otherwise refused with the next allowed time); refused when there is nothing to remind or nobody reachable; the user is told who was reminded. |
| **NTF-010** | Every send attempt is logged; failed sends are retried up to 3 times; a failure never blocks the document action. |

#### 3.9.5 Delivery Rules

**Notification timing — outbox model:**

1. **Outbox:** every task is created un-notified, whichever service wrote it. A background notifier (runs every 30 seconds, picks up tasks that have settled for at least 15 seconds) applies the per-type policy (§3.9.4) and marks the task as handled
2. **Immediate** types are emailed by the notifier; **Daily digest** types are collected into one digest email per recipient at the Daily Digest Time, for as long as the task is open
3. **Re-notification** (reassignment, another invoice on an open Create PA task) puts the task back into the outbox

**Scheduled notification jobs** (run inside epms-api, which assumes a single running instance): task notifier, daily digest, agreement-overdue sweep (chase task + email), Service/Project PO completion-date reminder (§3.13.3), and the NC purchase sync (§3.12.6).

**Other rules:**
- Notifications are sent **asynchronously** — document state transitions are not blocked by notification failure
- Failed deliveries are logged; retry up to 3 times
- If a user has no email address and no Teams account/webhook, notification is silently skipped (in-app Task Inbox still populated)
- Notifications respect the company master switch and the user's channel preference (Email / Teams / Both / None)
### 3.10 Vendor Master

The Vendor Master is owned by the master-data service (MDM business partners, supplier flag). EPMS reads it from the shared database and forwards every create or update to MDM. Creating, editing and importing vendors needs the **`vendor_master`** permission (Portal → Admin → Access Control). Viewing and CSV export are open to all EPMS users.

**Vendor record fields:**

| Field | Description |
|-------|-------------|
| Vendor ID | System-generated unique identifier |
| POID (`code`) | Required, 1–50 characters, unique across all vendors (a duplicate is rejected). Used as a component in PO document numbering (see §3.12.5). For vendors imported from the ERP it equals the NC supplier code (e.g. `0000415`). |
| ERP ID | Optional, up to 100 characters. The NC65 supplier code. This is how NC-mirrored POs find their vendor (§3.12.6). |
| Name | Legal vendor name (required) |
| Category | Vendor category (e.g. Raw Materials, Services, Spare Parts, Logistics) (required) |
| Contact Name | Optional |
| Contact Email | Optional. Accepts several addresses separated by `,` or `;` (saved comma-separated). Default recipient for Place Order via Email (§3.12.3). |
| Remittance Email | Optional; several addresses allowed as above. Used for payment remittance advice (§3.18). |
| Phone, Address, Notes | Optional |
| Payment Terms | Presets Net 15, Net 30, Net 60, Net 90, COD, Prepayment (default Net 30), plus **Custom (Net N days)** for any whole number of days 1–365 (e.g. Net 14, Net 21). Stored as `net<N>`, `cod` or `prepayment`; epms-api and mdm-api reject anything else on write (a "Net 21" spelling in a CSV import is normalised to `net21`). The ERP-import drawer offers the presets only. |
| Currency | Default CAD |
| Status | Active / Inactive |
| Max Prepayment % | Maximum prepayment percentage permitted for this vendor (1–100; overrides system-wide Prepayment Config cap when set). Only shown in the vendor form when Payment Terms = Prepayment. |

GST/HST registration number, province and bank details are not held on the EPMS vendor record.

**Vendor Master FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **VND-001** | Every vendor must have a POID (`code`, 1–50 characters), unique across vendors. Duplicates are rejected. |
| **VND-002** | Inactive vendors cannot be selected on new PRs or POs but remain visible in historical documents. |
| **VND-004** | CSV import upserts by POID (`code`): an existing code is updated, a new code is created. Required columns: code, name, category. Optional: erpId, contactName, contactEmail, remittanceEmail, phone, address, paymentTerms, currency, notes, isActive. Errors are reported per row. CSV export carries the same columns. |
| **VND-005** | **Not implemented** — see §11. Any change to a vendor record (including status change) should be recorded in an immutable audit log with the actor's name, role, timestamp, and before/after values for changed fields. |
| **VND-006** | When Max Prepayment % is set on a vendor, it overrides the system-wide prepayment cap (Admin Panel → Prepayment Config) for all Prepayment PAs linked to that vendor's POs. If not set, the system-wide cap applies. |
| **VND-007** | **From ERP.** Users with `vendor_master` can open the *From ERP* drawer on the Vendors page. It lists the NC supplier master mirrored in MDM, paginated and searchable, with an option to show only suppliers not yet imported. Selected suppliers are created as vendors with POID = ERP ID = NC supplier code, with name, phone and address from NC and the chosen default category, payment terms and currency. A supplier whose ERP ID already exists on a vendor is refused (duplicates are checked by exact ERP ID, not by POID). |
| **VND-008** | A vendor can be saved without a contact name or contact email. |
| **VND-009** | **Vendors list.** Tabs All / Active / Inactive filter on the server, so the page count and every page match the tab. Every column sorts on the server (UI-SORT-004); POID and ERP ID sort by numeric value when they are all digits, Payment Terms in term order (COD, Net N by days, Prepayment). |

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

A PO is created by a Procurement Officer, typically triggered by a `create_po` task following a PR's final approval (§3.7.1). A PO can also be created independently of a PR.

POs for Type 1 raw materials and packaging are not created here; they are mirrored from the ERP, Yonyou NC65 (§3.12.6).

**PO fields:**

- PO Number (auto-generated when the PO is first saved, so drafts carry a number — see §3.12.5)
- Linked PR (optional; one PR → one PO; pre-fills vendor, line items, cost center, and other fields — see PO-002)
- Vendor (required; selected from active Vendor Master)
- Delivery address
- Expected Delivery Date (inherited from linked PR's "Required By" date if PR is linked)
- Payment terms (defaulted from vendor; overridable)
- Currency (defaulted from company settings)
- Line items: Description, Material ID (Types 1 & 3), Supplier Item ID, Qty, Unit, Unit Price, Line Total. Unit Price may be zero or negative (discount / rebate / credit line); Qty must be > 0; Line Total = Qty × Unit Price. Unit Price is held to 5 decimal places; line totals and header amounts stay at 2.
- Delivery instructions / notes
- Prepayment Required checkbox (inherited from linked PR's `is_prepaid` flag if PR is linked; editable)
- Attachments
- Budget account (inherited from linked PR if applicable; otherwise picked from the budget-account catalog — no free text)
- Project code (inherited from linked PR if applicable, Type 6 only)

The PO Detail page always shows the Supplier Item ID column (on the PDF it is optional — PO-PDF-001). When any line carries an NC planned-arrival date, PO Detail also shows a read-only per-line **Delivery Date** column.

**PO Creation FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PO-001** | A PO must be linked to at most one approved PR. A PR can have at most one non-cancelled PO. **Not implemented** as a server-side check — see §11. |
| **PO-002** | When a PO is created from an approved PR, the following fields are pre-populated from the PR and are editable by the Procurement Officer before submission: line items, vendor, cost center, budget code, procurement type, currency, delivery address, **Expected Delivery Date** (from PR's "Required By" date), and **Prepayment Required** flag (from PR's `is_prepaid`). |
| **PO-003** | The Procurement Officer can add, remove, or modify line items on the PO regardless of the linked PR's line items; the final PO reflects the actual order placed with the vendor. |
| **PO-004** | PO total value is computed from line items and displayed in real time. |
| **PO-005** | Currency selection is limited to currencies enabled in Admin Panel → Currency Settings. |
| **PO-006** | A PO cannot be submitted if the linked vendor is Inactive. |
| **PO-007** | Delivery address defaults to the company address (from Company Settings) but can be overridden per PO. |
| **PO-008** | File attachments (e.g. vendor quote, specification sheet) can be added to a PO before submission. |
| **PO-009** | A draft PO can be saved without submitting. Only submitted POs enter the approval workflow. A PO can be edited only while `draft` or `returned`. |
| **PO-010** | Procurement Officer receives a notification when a PO they created reaches `approved` status, with a prompt to place the order. |
| **PO-011** | On PO final approval, the system generates the PO PDF and saves it as an attachment; a `place_order` task is created for the Procurement Officer pool. The vendor is **not** contacted automatically — the Procurement Officer initiates order placement manually (see §3.12.3). |
| **PO-012** | When a PR reaches `approved` status (final approval step), the system automatically creates a `create_po` task in the **Procurement Officer pool** (visible to every holder of the `procurement_officer` role, primary or additional). Whether and when it is emailed follows the per-task-type notification policy (§3.9.4; on and immediate by default for `create_po`), via each recipient's preferred channel. The notification includes PR number, title, vendor name, amount, currency, and a deep link to the PR detail page. If no Procurement Officer users exist, the task is still created (assigned by role) and will appear when a user with that role next logs in. |
| **PO-013** | When a PO is created from an approved PR, the open `create_po` task for that PR is automatically marked complete. Any stale `create_po` tasks (where the PR already has a linked PO) are auto-resolved on the next task list load. The same task-list load also creates a missing `create_po` task for any approved PR created on or after 2026-06-01 that has no PO and no open `create_po` task (§3.7.1). |
| **PO-014** | When a PO transitions to `approved` status, the system creates a `place_order` task for the Procurement Officer pool. For approved POs created on or after 2026-06-01 that have no open `place_order` task, the task is auto-created on the next task list load (backfill logic). |
| **PO-015** | The budget code on a PO must be an account code in the budget-account catalog; any other value is rejected. If the budget service is unreachable the value is accepted unchecked. Data Maintenance edits (§3.6.8) are not checked. |

#### 3.12.2 PO Approval Flow

PO approval uses the configurable step engine (§3.3). The default PO workflow (seeded when none is configured) is:

```
Step 1: procurement_manager  — Procurement Manager
Step 2: gm_or_opm            — GM or OPM, per the department's Approval Routing entry (§3.3.8)
```

The workflow can be reconfigured in Portal → Admin → Approval Workflows → PO. The Approve / Return / Reject controls on PO Detail are shown only to the user who holds the open `approve_po` task for the PO; there is no role-based fallback. The engine routes that task to whoever holds the step's role (primary or additional role, assigned in Portal → Admin → Access Control); an active approval delegate also sees it (§1.5); System Admin sees every task. Any viewer can press **Send reminder** under the current step in the approval timeline to re-notify the task holder. The PO List shows the current approval step under each status badge.

#### 3.12.3 Order Placement & Tracking

**Order placement flow:**

When a PO reaches `approved` status, a `place_order` task is raised for the Procurement Officer pool. On the PO Detail page a **Place Order** action button is shown. The Procurement Officer chooses one of two methods:

```
PO Approved → PO PDF saved as attachment
    → place_order task created for Procurement Officer pool
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

1. System fetches the vendor's Contact Email from the Vendor Master (§3.10).
2. System opens an **Email Composer** panel pre-filled with:
   - **To**: vendor contact email (editable)
   - **CC**: optional additional recipients
   - **Subject**: pre-filled from the PO Order Email template (§3.6.5); editable
   - **Body**: pre-filled from the PO Order Email template with variables substituted (`{po_number}`, `{po_date}`, `{vendor_name}`, `{expected_delivery}`, `{delivery_address}`, `{line_items}`, `{subtotal}`, `{tax}`, `{total}`, `{company_name}`, `{sender_name}`); editable
   - **Attachment**: PO PDF (auto-attached; cannot be removed)
3. Procurement Officer reviews and edits the email as needed, then clicks **Send**. Mail goes out through the PO-to-vendor SMTP profile (falling back to the main SMTP profile).
4. On send: PO status → `issued`; email transmission date/time and recipient recorded on the PO; `place_order` task marked complete.

**Option B — Place Order Online:**

For vendors whose orders are placed via an external portal or procurement platform (e.g. supplier website, EDI portal).

1. Procurement Officer selects **Place Order Online**.
2. A confirmation dialog is shown: "Confirm you have placed the order with [Vendor Name] via their online portal."
3. Procurement Officer enters an optional reference number (e.g. portal order ID) and clicks **Confirm**.
4. PO status → `issued`; reference number stored on PO record; `place_order` task marked complete.

**Order Placement & Tracking FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PT-001** | On PO final approval, the system creates a `place_order` task for the Procurement Officer pool and sends a notification (Email/Teams per preference and the task notification policy) with a deep-link to the PO Detail page. |
| **PT-002** | The PO Detail page displays a **Place Order** button for POs in `approved` status to users whose primary role is Procurement Officer, Procurement Manager or System Admin; the action itself requires the `epms.po.write` permission. The button is hidden for all other statuses. |
| **PT-003** | **Place Order via Email**: the system fetches the vendor's contact email from the Vendor Master. If no email is configured on the vendor record, the system shows a warning and prompts the user to add it before proceeding. |
| **PT-004** | The Email Composer pre-fills subject and body from the PO Order Email template (§3.6.5). All fields (To, CC, Subject, Body) are editable before sending. The PO PDF is always attached and cannot be removed. |
| **PT-005** | On send: PO status transitions to `issued`; the recipient, timestamp and subject are recorded on the PO and the `place_order` task completes. Send errors are logged but do not block placement: the PO is still marked `issued`. The Procurement Officer should verify delivery and resend from their own mail client if needed. |
| **PT-006** | **Place Order Online**: Procurement Officer confirms manual placement via a confirmation dialog. An optional portal order reference number can be recorded. PO status transitions to `issued` on confirmation. |
| **PT-007** | **Not implemented** — see §11. Procurement Officer can update the PO's expected delivery date and add tracking notes at any time after issuance. (Today a locally created PO can be edited only while `draft` or `returned`; NC-mirrored POs can have their Expected Delivery changed through Edit Details, §3.12.7.) |
| **PT-008** | The PO receipt status moves when a GR is **collected or confirmed**, not when it is created: the PO becomes `partially_received` (if not all line items are fully received) or `fully_received`. It moves back if a counted GR is deleted or cancelled (§3.13.6). It only moves between `issued`, `partially_received` and `fully_received`. |
| **PT-009** | **Not implemented** — see §11. A PO can be manually closed by the Procurement Officer or Procurement Manager (`closed` status). Closing a PO with open GRs or unmatched invoices requires an explicit override confirmation. |
| **PT-010** | PO list view filters: **Status** (draft, submitted, in_review, approved, returned, rejected, issued, partially_received, fully_received, closed, cancelled, nc_pending, nc_milk), **Department** (from the linked PR), **Type**, **Prepaid**, plus a search over PO number, title, vendor name and budget code. |

#### 3.12.4 Document Chain

**Document relationship model:**

| Relationship | Cardinality | Notes |
|---|---|---|
| PR → PO | 1 : 1 | Each approved PR produces at most one PO. NC-mirrored POs (§3.12.6) have no PR; legacy PMS-imported POs may also lack one. |
| PO → GR | 1 : N | A PO can have multiple GRs (partial deliveries, service confirmations). For NC-mirrored POs, GRs are mirrored from NC arrivals. |
| PO → Invoice | N : M | A PO can receive multiple invoices (e.g. monthly recurring, partial deliveries), and one invoice may be allocated by line across several POs (§3.14). |
| Invoice → PA | N : M | One or more invoices can be bundled into one PA. A regular PA may cover invoices from several POs of the same vendor and currency; prepayment, settlement and balance PAs cover a single PO (§3.15). |

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

Note: GRs, Invoices, and PAs are **siblings** under the PO — Invoices and PAs are not nested under GRs. PAs reference which invoices they cover but all hang off the PO in the tree. A PA that covers several POs appears under each of them.

**Document Chain UI component:**

A unified `DocumentChainTree` component is rendered in the right sidebar of PR, PO, and PA detail pages. It shows:
- **Ancestor cards** (full-width link cards): documents higher in the chain (e.g. on PA detail: PR → PO above the anchor)
- **Anchor card** (highlighted): the current document
- **Child rows** (tree branches below): all GRs, Invoices, and PAs linked to the anchor PO, with the current document's row highlighted if it appears in the list

The component is shared across PR Detail, PO Detail, and PA Detail pages, using a `currentType` prop (`'pr' | 'po' | 'pa'`) to determine which document is the anchor and which are ancestors/children.

#### 3.12.5 PO Document Numbering

POs created in EPMS get a system-generated number when first saved, using the format:

```
PO-[POID]-YYMM-SEQ
```

Where:
- `[POID]` is the vendor's POID (from Vendor Master, §3.10)
- `YYMM` is the two-digit year and two-digit month (UTC) of creation (e.g. `2604` for April 2026)
- `SEQ` is the highest existing sequence for that POID and month plus one, zero-padded to two digits (the first PO of the month is `01`). Numbers freed by deletion are not re-used. Allocation is serialised per prefix so concurrent saves cannot collide.

Example: `PO-ABC-2604-03` (third PO issued to vendor ABC in April 2026).

The PO number cannot be edited on the PO. Changing a PO's vendor in **Admin → Data Maintenance** (§3.6.8) mints a new number under the new vendor's POID and renames every copy of the old number on linked PRs, GRs, invoices and PAs. NC-mirrored POs keep the NC document number (§3.12.6), which follows the same scheme and therefore counts toward the sequence; on a collision they are suffixed `-2`, `-3` and so on.

#### 3.12.6 NC (Yonyou NC65) Purchase Order Mirror

Raw-material and packaging purchasing (Type 1) is done in the company ERP, Yonyou NC65. EPMS does not create these POs. It keeps a **read-only mirror** of NC purchase orders and their arrivals. The mirror lets the buyer print the UniOps PO PDF and collect sign-off (§3.12.8). It also lets AP match invoices and raise payments against them. NC is only ever read; EPMS never writes to NC.

**Which NC orders are mirrored**

| Rule | Detail |
|---|---|
| Version | Only the version in force. Superseded versions of a changed order are never mirrored. |
| Date | Order date on or after the **cutover** (Portal → Admin → NC Sync → Purchase; set by System Admin; date or date-time). |
| Approved | NC status approved, whatever the deletion flag. |
| Free state | NC status free (saved, not submitted), not soft-deleted, and created on or after 2023-01-01. Older free-state rows are abandoned drafts and are excluded. NC's own PO approval chain was switched off in 2026-09, so free state is now the normal "waiting for UniOps sign-off" state. |
| In approval | NC status in-approval, not soft-deleted. Kept for orders that were mid-chain when NC's chain was switched off. |
| Duplicate numbers | An unapproved order is dropped if another live order with the same NC number is further along (draft loses to submitted or approved; submitted loses to approved). |

**How a mirrored order looks in EPMS**

| NC condition | EPMS PO status | Meaning |
|---|---|---|
| Not yet approved in NC (free or in approval) | `nc_pending` ("NC Pending Approval") | Overrides every row below. PDF and sign-off are available. Not payable, receivable or invoice-matchable. |
| Raw-milk trade type | `nc_milk` ("Milk / NC") | Receipts are booked in NC. Not payable, receivable or invoice-matchable. PDF and sign-off are available. |
| Finally closed in NC, or every line payment-closed | `closed` | Archive only |
| Otherwise | `issued` | Open for invoice matching and payment |

- Mirrored POs carry: Type 1, no linked PR, creator "NC Sync", placement method "NC", and the NC number as the order reference.
- The NC memo and markers such as `[NC Paid]`, `[NC Partially Paid]`, `[NC Invoiced]`, `[NC Closed <date>]`, `[Milk / <type>]` and `[NC Pending Approval]` are kept in **NC Sync Notes**. These notes are shown on PO Detail but never printed on the PDF.
- The PO Detail page shows a "Synced from NC ERP" badge.
- Line items use these NC values:
  - material code and English material name (falling back to the Chinese name);
  - quantity in NC's quotation unit, with the price **per that same unit**, stored to 5 decimal places;
  - NC's tax-inclusive line amount;
  - received quantity, summed from NC arrivals;
  - NC's planned arrival date for each line (shown as "Delivery Date").
- The header subtotal and tax come from NC's pre-tax and tax line amounts.
- Each approved NC arrival against an approved order becomes a physical GR in status `collected`, received by "NC ERP". An arrival that spans several orders becomes one GR per order, numbered `<arrival no>-1`, `-2` and so on. A GR whose arrival already has a downstream NC invoice is marked `[NC Paid]`. No arrivals are mirrored for unapproved orders.

**Vendor resolution:** An NC order is linked to the EPMS vendor whose **ERP ID** equals the NC supplier code (a small alias table folds known duplicate NC supplier records). If no vendor matches, the order is **skipped** (NC-006).

**Cadence and triggers**

| Trigger | Who | Mode |
|---|---|---|
| Scheduler | System | Incremental only. Runs every *N* minutes, measured from the start of the last run whatever its outcome. *N* is set in Portal (System Admin). Default 60, range 0–1440; 0 turns the schedule off. A changed interval applies within a minute. |
| **Sync NC** button on the PO List | System Admin, Procurement Officer, ERP PA Officer (primary or additional role) | Incremental |
| Portal → Admin → NC Sync → Purchase | System Admin | Incremental, or **Full reload** after typing `FULL RELOAD` |

Only one run executes at a time; a second trigger is refused with "already running". A run that stops reporting for 30 minutes is marked failed. Portal shows the last run's counts (POs and lines upserted, GRs, skipped for no vendor, skipped because consumed, renumbered, skipped for no free number), the effective cutover, the interval and the next due time.

**Incremental reads:** An order is re-read when NC's latest change stamp on it, or on one of its arrivals, is at or after the watermark. So an NC approval or a new arrival always reaches the mirror on the next run.

| FR ID | Requirement |
|---|---|
| **NC-001** | A mirrored PO cannot be edited through the normal PO edit, submitted to the PO approval workflow or placed with a vendor. The only edits are Edit Details (§3.12.7) and sign-off (§3.12.8). |
| **NC-002** | **Freeze once invoiced.** When any invoice references a mirrored PO (any invoice status), the sync stops rewriting that PO, its lines and its GRs. |
| **NC-003** | **Buyer-owned fields survive re-sync.** Incoterms, Buyer Notes, line Supplier Item ID, line Sample and buyer-added lines are never overwritten. If the buyer changed the tax rate, the sync keeps it and recomputes tax and total from NC's fresh subtotal. The value of buyer-added lines stays in the header subtotal. |
| **NC-004** | **Withdrawn orders.** A mirrored `nc_pending` PO that NC no longer lists (rejected or deleted) is cancelled and tagged `[NC Pending Withdrawn]`, unless an invoice references it. Any open sign-off on it is cancelled and its `sign_po` tasks are closed. If NC later re-creates an order under the same number, the cancelled mirror is renamed `<number>-VOID<n>` so the live order gets its real number. |
| **NC-005** | **Number collisions.** If the NC number is already used by a different EPMS PO (a legacy PMS import or another NC order with the same number), the order is mirrored as `<number>-2`, `-3` and so on, up to 50. Past that it is skipped and reported (NC-006). The order that holds the number is never renamed. |
| **NC-006** | **Import errors raise Admin tasks.** These tasks go to the System Admin pool and also appear in the Portal inbox. Each one names the order and the reason, and closes itself only on evidence: the order is now mirrored, or NC no longer lists it. |
| | • Supplier has no EPMS vendor → `import_erp_vendor` task. It names the NC supplier code and name and links to Vendors → From ERP (VND-007). |
| | • No free document number → `resolve_nc_sync_error` task. |
| | • Whole run failed → one `resolve_nc_sync_error` task ("NC SYNC"). It closes on the next successful run. |
| | While an order is being skipped, the incremental watermark is held at that order. As soon as the cause is fixed (for example, the vendor is imported), the next run imports it with no manual step. |
| **NC-007** | **Deleting a mirror is not permanent.** Deleting an NC-sourced PO in Data Maintenance (§3.6.8) files a standing re-fetch request. The next incremental run re-reads that order regardless of the watermark. The request settles as "mirrored", or as "gone from NC" if NC no longer lists the order. |
| **NC-008** | **Full reload** (System Admin only, typed confirmation) deletes and rebuilds the NC mirror. It keeps every NC PO that an invoice references or that carries buyer-added lines. |
| **NC-009** | A 3-way-matched mirrored PO raises its `create_pa` task to the **ERP PA Officer** pool, because there is no PR requester. See §3.14 / §3.15. |
| **NC-010** | Mirrored GRs are excluded from the Receiving Report (§3.13.5) because receiving for these orders is done in NC. |

#### 3.12.7 Edit Details on NC-imported POs

NC carries no Supplier Item IDs, sample requirements, Incoterms, delivery address or buyer notes. Users holding **`epms.po.edit_imported`** can add these through **Edit Details** on the PO Detail page of any NC-sourced PO, in **any status**. By default that is the ERP PA Officer, plus System Admin.

| FR ID | Requirement |
|---|---|
| **PO-IMP-001** | **Editable fields:** Expected Delivery, Incoterms (≤100 chars), Delivery Address, Tax Code / Tax Rate (CAD orders only; non-CAD orders keep NC's tax untouched), Prepaid flag, Buyer Notes / Terms & Conditions, and per NC line the Supplier Item ID and Sample. **Read-only:** vendor, currency, budget code, NC line description, quantity, unit and price. |
| **PO-IMP-002** | **Delivery and address defaults.** If the PO has no header Expected Delivery, the earliest NC line planned-arrival date is shown read-only ("Tracks NC automatically"). Otherwise the date is editable. Delivery Address defaults to the company delivery address until the buyer edits it. |
| **PO-IMP-003** | **Buyer-added lines.** The buyer can add, edit and remove extra lines (description, qty > 0, unit, unit price, Supplier Item ID, Sample) for charges NC cannot hold, such as moulds or tooling. These lines sort after NC's lines and add to the header subtotal, tax and total. NC-sourced lines can never be edited here. |
| **PO-IMP-004** | **Invoice guard.** Once any invoice references the PO, changing the tax rate or the buyer-added lines is refused ("…cannot be changed once an invoice has been raised against this PO. Every other field is still editable."). The tax control is shown locked. |
| **PO-IMP-005** | Each save writes an audit-log entry containing only the fields that changed. A tax-rate change marks the PO as buyer-edited (see NC-003). |
| **PO-IMP-006** | The page shows where the PO's sign-off stands (§3.12.8). |

#### 3.12.8 PO Sign-off (NC-imported POs)

NC's own PO approval chain is off. The NC officer approves an order in NC only after UniOps has signed it. Sign-off replaces the paper form: the **Purchasing Manager initials** and the **Operations Manager signs** the UniOps PO PDF. Sign-off is a separate workflow (action key `posign`) on the same PO. It has its own status (`draft → submitted/in_review → approved`, or `returned` / `cancelled`) and never changes the PO's own status.

**Default steps** (configurable in Portal → Admin → Approval Workflows → PO Sign-off; only company-wide single-holder roles make sense here):

| Step | Role | Signature slot on PDF |
|---|---|---|
| 1 | `procurement_manager` "Purchasing Manager" | initials (centre of signature block) |
| 2 | `opm` "Operations Manager" | signature (company side) |

| FR ID | Requirement |
|---|---|
| **PO-SGN-001** | Sign-off can be raised only on NC-sourced POs in status `nc_pending`, `issued` or `nc_milk`. Milk POs are included. Locally created POs use the normal PO approval flow (§3.12.2) instead. |
| **PO-SGN-002** | **Raising** a sign-off (Submit, with a justification) needs the **`epms.po.signoff`** permission. By default that is the ERP PA Officer, plus System Admin. |
| **PO-SGN-003** | **Submit is blocked** when any of these holds: no sign-off workflow is configured; a step's role has no active holder ("Assign the role in Portal → Access Control"); or no holder of a step's role has a saved signature ("…has not set a signature in My Profile"). The panel lists these blockers before submit. |
| **PO-SGN-004** | **Signing** is limited to holders of the current step's role (primary or additional role). Permission keys and delegation are not checked: a `sign_po` task is not shown to an approval delegate (§1.5). A signer with no saved signature is refused before the step advances. |
| **PO-SGN-005** | At each signature the system stores a snapshot of the signer's name and signature image for that step. When the last step is signed, the PO PDF is regenerated and replaces the earlier one. The PDF then shows the OPM signature on the company side, the Purchasing Manager initials in the centre, and the signer's name. The person who raised the sign-off gets a completion email (no task, because the remaining step happens in NC). |
| **PO-SGN-006** | **Return.** A signer can return the sign-off with a comment, and it goes back to the person who raised it (`revise_po_signoff` task). Resubmitting starts a fresh round and clears earlier signatures. There is no Reject. |
| **PO-SGN-007** | **Notes thread.** The raiser and the signatories can add notes to an append-only justification thread without moving the workflow. Anyone else is refused. |
| **PO-SGN-008** | If NC withdraws the order (NC-004), any open sign-off is set to `cancelled` and its tasks are closed. |
| **PO-SGN-009** | `sign_po` tasks and emails deep-link to the PO Detail page. |

#### 3.12.9 PO PDF Content

| FR ID | Requirement |
|---|---|
| **PO-PDF-001** | Line columns: #, Material ID*, Supplier Item ID*, Description, Qty, UOM, Unit Price, Line Total, Sample*. A column marked * is left out when every line is blank for it, and Description widens to fill the space. |
| **PO-PDF-002** | The header "Delivery" shows the PO's Expected Delivery. If that is empty, it shows the earliest line planned-arrival date from NC. Incoterms and Buyer Notes print when present. NC Sync Notes never print. |
| **PO-PDF-003** | **Type 1 POs** print a two-party signature block: company on the left, vendor on the right (blank, signed by hand). The company Name is the signer from sign-off (§3.12.8). Before sign-off it is the OPM's name, printed only when exactly one active OPM holder exists, otherwise blank. Title is "Operations Manager". |
| **PO-PDF-004** | The PDF can be generated or regenerated (**Regenerate PDF** on the Attachments tab) for POs in `approved`, `issued`, `partially_received`, `fully_received`, `closed`, `nc_pending` or `nc_milk`. `nc_pending` is included on purpose: the printed PDF is what gets signed before NC approval. |

### 3.13 Goods Receipt (GR)

There are two GR types: **physical** (Types 1, 2, 3, 5) and **service** (Types 4, 6). Physical GRs are created by holders of the **`epms.gr.receive`** permission (default: Warehouse Staff, Procurement Officer, System Admin; primary or additional role). For Service/Project POs the PR's Service Owner or Requester can also create them (§3.13.3). Every physical GR on a PO with a PR goes Warehouse create → Requester acknowledge → Requester collect.

```
Create GR (pending_ack) ──► acknowledge_gr task → PR owner
   │                          │ Acknowledge → GR PDF generated
   │                          ▼
   │                   collection_pending ──► collect_goods task → PR owner (physical)
   │                          │              confirm_service_gr task → PR owner (service)
   │                          │ Collect → collected / Confirm → confirmed
   │                          │ → PO received qty updated
   │                          ▼
   │                   collected ──► discrepancy (optional flag)
   └─ PO has no PR → GR completes at creation (collected / confirmed), PDF generated, admin alert email
```

#### 3.13.1 Path A — Physical GR

Physical GRs are created by Warehouse Staff (or another `epms.gr.receive` holder) when goods arrive at the warehouse.

| FR ID | Requirement |
|-------|-------------|
| **GR-P-001** | A physical GR can be created against a PO in `issued` or `partially_received` status by a holder of `epms.gr.receive`. POs in `fully_received`, `nc_pending`, `nc_milk`, `closed` and `cancelled` status are refused. |
| **GR-P-002** | GR form fields: PO (searchable, eligible POs only; can be pre-filled from a link), title, storage location, received by, per-line received quantity (≥ 0), per-line condition (Good / Damaged / Discrepancy) and notes, overall notes, and pack-list attachments. |
| **GR-P-003** | Received quantity can be less than the PO line quantity (partial delivery). Received quantities are added to the PO lines when the GR is **collected or confirmed**; the PO then moves to `fully_received` if all lines are fully received, otherwise `partially_received`. |
| **GR-P-004** | If any line is not Good at creation, an **urgent `gr_damage_report` task** goes to the Procurement Officer pool, asking them to raise a return or credit note with the vendor. |
| **GR-P-005** | GR number is system-generated on save (format: `GR-YYYYMMDD-NNNN`; UTC date; highest existing number for the day + 1). |
| **GR-P-006** | On creation the GR is `pending_ack`, and an **`acknowledge_gr`** task is assigned personally to the linked PR's owner (§3.13.2). **If the PO has no PR** (NC or PMS import), the requester steps are skipped: the GR goes straight to `collected` (physical) or `confirmed` (service), received quantities are posted, the PDF is generated, and System Admins get an alert email to restore the PR link. No role-wide broadcast is ever sent. |
| **GR-P-007** | The GR PDF is generated on Acknowledge and regenerated on Confirm, so it carries Collected By; for no-PR GRs it is generated at creation. It prints the creator and the receive, acknowledge and collect names (never raw IDs). **Regenerate PDF** is available on the GR Attachments tab. |
| **GR-P-008** | **3-way hand-off.** When a GR is created, the system attaches it to any already-matched invoice that bills the same PO lines (or the whole PO for header-level matches) and refreshes that invoice's GR value. It closes any open `confirm_receipt` task on the PO. If the PO is now 3-way matched and has no PA or open `create_pa` task, it raises `create_pa`: to the PR requester, or to the ERP PA Officer pool for POs with no PR. |

#### 3.13.2 Requester GR Notification Specification

When a physical GR is created, the following notification and acknowledgement process applies:

> **Recipient identification:** The GR notification and all GR tasks are sent to the **PR's owner** — the Service/Project Owner named on a Service/Project PR, otherwise the PR's requester — resolved via GR → PO → PR. Notifications are never broadcast to all users with the Requester role.

| Step | Actor | Action | Channel |
|------|-------|--------|---------|
| 1 | System | Sends GR notification to the PR owner immediately on GR creation (per the task notification policy, §3.9.4) | Email + Teams (per user preference) |
| 2 | Requester | Opens Task Inbox → clicks `acknowledge_gr` task → views GR detail | In-App |
| 3 | Requester | Acknowledges receipt (confirms they are aware goods have arrived) | In-App |
| 4 | System | Marks `acknowledge_gr` task complete; GR status → `collection_pending`; a `collect_goods` task (physical) or `confirm_service_gr` task (service) is created for the PR owner | Automated |

Open GR tasks are re-sent only through the generic per-task-type notification policy and daily digest (§3.9). A dedicated GR acknowledgement reminder / escalation ladder is **not implemented** — see §11.

**GR Notification FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **GR-N-001** | GR notification email/Teams card must include: GR number, PO number, vendor name, delivery date, list of received items with quantities and conditions, and a deep link to the GR detail page. |
| **GR-N-002** | **Not implemented** — see §11. SLA thresholds for GR acknowledgement (reminder days, escalation days) configurable in Admin Panel → GR Notification SLA; the setting can be stored but nothing acts on it. |
| **GR-N-003** | **Not implemented** — see §11. Escalation of an unacknowledged GR to the Department Manager of the Requester's department, with a copy to the Requester. |
| **GR-N-004** | Once the Requester acknowledges the GR, the `acknowledge_gr` task closes and no further notifications are sent for it. |
| **GR-N-005** | GR tasks (`acknowledge_gr`, `collect_goods`, `confirm_service_gr`, `confirm_receipt`) are not approval tasks, so they stay with the PR owner and are not shown to an approval delegate (§1.5). Changing a PR's Service Owner in Data Maintenance moves its open GR tasks to the new owner (§3.13.3). |

#### 3.13.3 Path B — Service Receipt Confirmation

Service receipt applies to **Type 4 (Service) and Type 6 (Project-Related)** POs. The person who confirms is the PR's **Service Owner**: the Owner named on the PR, or the PR requester if none was named. The PR requester can still confirm as well. **Two initiation paths are supported:**

**Path B-1 — Owner-initiated (self-created service GR):**
The Service Owner or Requester proactively creates a service GR directly from the PO Detail page or the GR List page once the service is complete. This requires no prior warehouse staff action.

**Path B-2 — Completion-date reminder (task-driven):**
A daily sweep (same time of day as the task digest) finds Service/Project POs with a linked PR where the PR's **Expected completion date + reminder days** has passed, the PO is `approved`, `issued` or `partially_received`, and no live GR exists. For each such PO it raises (or reuses) a **`confirm_receipt`** task for the PR's Service Owner, deep-linked to New GR pre-filled with the PO, and emails a service-GR-due reminder. The sweep is controlled by two Admin settings: *Service GR due reminders enabled* (default **off**) and *dry run* (default **on**: only a list of matching POs is emailed to System Admin, and no task or email goes to users).

A `confirm_receipt` task is also raised when an invoice is matched to a Service/Project PO that has no receipt yet (§3.14). Physical POs in that case get the task in the Warehouse Staff pool. The task closes automatically once receipt evidence exists.

**Reassigning the receipt.** Changing a PR's Service Owner in Admin → Data Maintenance (§3.6.8) also moves that PR's open `confirm_receipt`, `acknowledge_gr` and `confirm_service_gr` tasks to the new owner. Pool tasks and completed tasks are left alone.

**Service GR creation access:**

| Actor | Condition | Action |
|-------|-----------|--------|
| PR Service Owner **or** PR Requester | Service/Project PO (Type 4 or 6) in `approved`, `issued`, or `partially_received` status | "Confirm Service" (Type 4) / "Confirm Completion" (Type 6) on PO Detail, or "New GR" on the GR List page |
| Holder of `epms.gr.receive` | Same statuses | Can create the GR |
| Anyone else | — | Refused |

A Service/Project PO with no PR can only be received by an `epms.gr.receive` holder.

**Service GR status flow:**

```
Owner creates Service GR → pending_ack
    → Owner clicks "Confirm Service Completion" on ServiceGrConfirmPage
         → GR status: confirmed (acknowledge + confirm merged into one step)
```

*Note: Confirm is allowed from `pending_ack`, `collection_pending` or `collected`. When the owner creates and immediately confirms a service GR, the `acknowledge` and `confirm` steps are merged into a single action. If the GR was acknowledged first, it is in `collection_pending` with a `confirm_service_gr` task when the owner confirms. Confirm posts the received quantities and generates the PDF.*

**Service GR form fields:**
- PO reference (pre-filled if accessed from task or PO Detail)
- Service completion date
- Quality assessment (Satisfactory / Has Issues / Reject Service)
- Acceptance notes (required, min 10 characters)
- Issue details (required if quality ≠ Satisfactory)
- Supporting documents (optional — completion certificate, photos, reports; max 25 MB per file)

**Service GR SLA (Admin → Service GR SLA), two rungs:**

| Rung | Default | Action |
|------|---------|--------|
| Reminder | 1 day after completion date | Reminder to the Service Owner (re-sent each daily run while the task is open) |
| Manager escalation | 3 days after completion date | One email to the **Service Owner's** Department Manager. Sent only if the owner has already received at least one reminder. |

**Service GR FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **GR-S-001** | A `confirm_receipt` task is created for the PR's Service Owner when: (a) the completion-date sweep finds the PO overdue, or (b) an invoice is matched to the PO before any receipt exists. |
| **GR-S-002** | The Service Owner receives a notification when the `confirm_receipt` task is created (per the task notification policy, §3.9.4). |
| **GR-S-003** | SLA reminders and escalations follow the two-rung ladder above (Admin → Service GR SLA). |
| **GR-S-004** | The Service Owner (or Requester) can confirm service delivery with notes and attachments. Confirmation transitions the Service GR to `confirmed` status. |
| **GR-S-005** | If the service was not completed satisfactorily, the GR can be rejected with notes (required) while it is in `collection_pending` (that is, after an acknowledge). Reject sets status `rejected` and closes the GR's tasks. No notification is sent to the Procurement Officer. |
| **GR-S-006** | On Service GR confirmation, the GR transitions to `confirmed` status and PO received quantities are updated. No `create_pa` task is created at this point — PA creation is triggered by invoice matching (see §3.14.2). |
| **GR-S-007** | Service GR PDF is generated on confirmation. |
| **GR-S-008** | The PR's Service Owner or Requester (not a generic requester role) has access to the "New GR" / "Confirm Service" / "Confirm Completion" entry point from: (a) the PO Detail page (button visible when the PO is Type 4 or 6 and status is `approved` or later), and (b) the GR List page ("New GR" button). The GR creation form shows only eligible POs in the dropdown. |
| **GR-S-009** | For owner-created service GRs, the `acknowledge` step is automatically merged with the `confirm` step. A single "Confirm Service Completion" action from `pending_ack` status is sufficient to move the GR to `confirmed` without a separate acknowledgement step. |

#### 3.13.4 Requester Collection (physical GRs)

After the requester acknowledges a physical GR, it moves to `collection_pending` and a `collect_goods` task is assigned to the PR owner. The task names the storage location ("Goods are ready for collection at: …"). The requester confirms collection on the **Collection Confirmation** page by marking each line Good / Damaged / Discrepancy (notes required for a line that is not Good), adding general notes, and submitting **Collect**. The GR moves to `collected` and the PO's received quantities and receipt status update (PT-008). PA creation is triggered later by invoice matching (see §3.14.2).

| FR ID | Requirement |
|-------|-------------|
| **GR-C-001** | Every physical GR on a PO with a PR goes through the collect step after acknowledgement. There is no global on/off switch. |
| **GR-C-002** | On Collect, the collector's name and the collection notes are recorded on the GR. The per-line conditions entered on the Collection Confirmation page are not stored separately. |
| **GR-C-003** | A `collected` GR can be flagged `discrepancy`. This closes the GR's open tasks but creates no new task. |
| **GR-C-004** | A GR can be cancelled while `pending_ack` or `collection_pending`. |

A "ready for collection" marking by Warehouse Staff, a collection discrepancy task with dual acknowledgement, and a collection SLA ladder are **not implemented** — see §11.

#### 3.13.5 Receiving Report

Goods Receipt → **Receiving Report** (button on the GR List) lists one row per received line. It rebuilds the warehouse's manual receiving sheet.

- **Columns:** GR, PO, Material ID, Description, Supplier, Unit, Quantity, Department, Requested By, Date Ordered, Arrival Date, Left-Warehouse Date (collected), Warehouse Receiver, Person Accepting, Lead Time (days).
- **Scope:** physical GRs only; cancelled or rejected GRs excluded; **NC-mirrored POs excluded** (NC-010).
- **Filters:** received-date range (plant local dates, America/Toronto), department, vendor, search.
- **Summary for the whole window:** line count, receipt count, order count, average lead time.
- **Sorting:** every column sorts on the server (UI-SORT-004); the Excel export uses the same order as the screen.
- **Lead time** = received date − ordered date. Ordered date is the Place-Order time, else the last PO approval, else PO creation.
- **Export:** xlsx export of the whole window, capped at 20,000 rows (the page warns when capped).
- **Visibility** follows the GR list (GR view permission and document scope).

#### 3.13.6 GR Line Rules and Maintenance

| FR ID | Requirement |
|-------|-------------|
| **GR-L-001** | GR line Unit Price may be zero or negative (discount lines carried over from the PO). Qty Received must be ≥ 0. Unit price is held to 5 decimal places. |
| **GR-L-002** | `confirm_receipt` task links and emails open **New GR pre-filled with the PO**, including the PO's line items. |
| **GR-L-003** | If a counted GR (`collected`, `confirmed`, `discrepancy`) is deleted or cancelled through Data Maintenance, PO line received quantities are recomputed from the GRs that remain, and the PO status can fall back from `fully_received`. |
| **GR-L-004** | Admin → Data Maintenance (§3.6.8) can edit GR line items. |
| **GR-L-005** | The GR list sorts by any column on the server (UI-SORT-004); Total Value sorts by the sum of line totals. It opens sorted by Received, newest first. |
### 3.14 Invoice Management

#### 3.14.1 Invoice Upload & Auto-Routing

Invoices are uploaded by any user holding the **Upload Invoice** permission (`invoice_upload`), typically AP Clerk. Upload is a single modal: the file is previewed side-by-side, AI extraction pre-fills the form, the uploader reviews and corrects, and the invoice is created. If a PO number was recognised on the document, the invoice is matched to that PO immediately (INV-MATCH-006, §3.14.3); otherwise it is created `unmatched` and waits in the invoice list for a person to match it. There is no fuzzy vendor/amount matching.

**Invoice upload and routing flow:**

```
Uploader (Upload Invoice permission) opens Upload Invoice
        ↓
(multi-page PDF → page splitter; each slice handled in turn)
        ↓
AI extraction pre-fills the form → uploader reviews / corrects
        ↓
Document-total check (pre-tax + tax vs printed grand total)
        ↓
Credit note detected? ── yes → Vendor Credit created (§3.18.2)
        ↓ no
Invoice created (INV-YYYY-NNNN)
        ↓
PO # recognised and open?
        ↓ (yes)                         ↓ (no / auto-match fails)
Auto-match to that PO             Invoice stays `unmatched`
        ↓                         → AP / uploader / assignee matches manually
Variance check (§3.14.3) → `matched` / `exception`
```

**Invoice Upload & Routing FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **INV-001** | Accepted files: PDF, JPG, JPEG, PNG; maximum 25 MB. |
| **INV-002** | AI extraction pre-fills vendor (as a hint — the user selects the vendor from Vendor Master), vendor invoice #, PO #, invoice date, due date, currency (CAD/USD/EUR/RMB), pre-tax amount, tax amount and line items. When the document has no due date but states NET *n* terms, due date = invoice date + *n* days. If due date is left blank it defaults to invoice date + 30 days. No extraction-time SLA is enforced. |
| **INV-003** | All extracted fields are editable before the invoice is created. |
| **INV-003a** | **Document-total check.** The form compares pre-tax + tax against the grand total printed on the document. A difference greater than 0.02 blocks upload until the uploader corrects the amounts (typically a missed line item or a freight/late-fee charge) or explicitly confirms the figures as entered. |
| **INV-003b** | **Multi-invoice PDFs.** A multi-page PDF opens in a page splitter; the uploader marks where each invoice starts (presets: every page, every 2 pages). Each slice is extracted, checked and created as its own invoice with its own pages attached, one at a time; the vendor selected for the first slice carries forward. Stopping part-way keeps the invoices already created. |
| **INV-003c** | **Credit notes.** If the document is detected as a credit note (AI flag or negative total) the modal switches to *Credit Note* and creates a Vendor Credit (§3.18.2) instead of an invoice. An invoice must have a pre-tax amount greater than zero. |
| **INV-004** | **Duplicate guard (upload form).** An invoice with the same vendor and the same vendor invoice # as an existing invoice cannot be uploaded. Credit notes use the Vendor Credit duplicate rule (VC-002). |
| **INV-005** | Invoice reference is system-generated: `INV-YYYY-NNNN` (next number = highest existing suffix + 1, so a deleted invoice can never cause a collision). |
| **INV-006** | On create, if the effective tax rate (tax ÷ pre-tax) matches exactly one active tax code within ±0.5 percentage points, a tax line with that code is pre-filled; otherwise the invoice is left uncoded (never guessed). |
| **INV-007** | Every invoice is mirrored to Finance AP: draft while `unmatched`/`exception`, posted once `matched`, voided on delete. |
| **INV-008** | *Retired.* An invoice is no longer limited to one PO — see INV-MATCH-002. |
| **INV-009** | Invoice status lifecycle: `unmatched` → (`match_review`) → `matched` / `exception`; `exception` → `matched` when accepted; `matched` → `paid` (or `partially_paid` for a prepayment) — the last two are written by Finance when the payment executes (§3.18.1). The uploaded file is stored in the shared invoice attachment store. |

A separate, Finance-owned duplicate check runs on the NC side (Finance → AP Duplicate Invoices, vendor name + invoice #, task `ap_duplicate_invoice`); it is outside EPMS and is only cross-referenced here.

**Matching an invoice:**

| FR ID | Requirement |
|-------|-------------|
| **INV-MATCH-001** | An invoice is matched on one of two routes: **PO route** (allocate to purchase orders) or **Agreement route** (§3.17.4). |
| **INV-MATCH-002** | PO route: each invoice line is allocated to a PO line (or PO header) on one or more POs of the **same vendor** in status `issued`, `approved`, `partially_received` or `fully_received`. Pre-tax allocations + non-PO fee lines must equal the invoice pre-tax amount (±0.01) or the match is refused. The candidate list shows, per PO line, what other invoices have already allocated. A PO can carry many invoices. |
| **INV-MATCH-003** | **Non-PO fee lines.** A line (freight, surcharge, handling) can be marked *Non-PO fee* (right-click). It is excluded from PO matching and variance, counted in the balance check, and paid with the invoice. A line cannot be both allocated and marked as a fee. |
| **INV-MATCH-004** | **Fee-only invoice.** An invoice whose lines are all fees must be linked to a *reference PO* of the same vendor; it matches with zero variance and does not raise a Create-PA task. |
| **INV-MATCH-005** | **Match by total.** For invoices without unit prices, the whole pre-tax amount can be matched to one PO in a single step. |
| **INV-MATCH-006** | **Auto-match on upload.** When the uploaded invoice's PO # matches an open PO, the system matches immediately — line-level when every invoice line pairs with an unused PO line of equal amount and the total balances, otherwise whole-invoice to that PO. On any failure the allocation panel opens for manual matching. |
| **INV-MATCH-007** | **Who may match:** AP staff (holders of *Match Invoice*, `epms.invoice.match`, as primary or additional role), the invoice's uploader, or the user holding an open *match_invoice* assignment for that invoice. Only `unmatched` and `exception` invoices can be (re)matched. |
| **INV-MATCH-008** | **Assignment.** AP can assign (or reassign) the matching of an invoice to any active user, creating a *match_invoice* task. The assignee may decline with a mandatory note; the task returns to the assigner (or the AP pool if there is no assigner). |
| **INV-MATCH-009** | **Review of delegated matches.** A match performed by someone other than AP or the uploader, with any non-zero line variance (or, on the agreement route, no period auto-claimed), lands in `match_review` with a *review_match* task for the AP pool. AP **approves** (tolerance then decides `matched` vs `exception`) or **rejects** with a mandatory note — the invoice returns to `unmatched` and the matcher gets a *Re-match* task. |
| **INV-MATCH-010** | If no goods receipt is selected, matching links the GRs that already received the allocated PO lines; a GR created or confirmed later back-links to invoices waiting on its PO lines (and back-fills their GR value). |
| **INV-MATCH-011** | The actor who matched is shown by full name (`matched_by_name`, per **UI-USER-001**; UUID fallback for legacy records) and keeps read access to the invoice after the task closes. |

#### 3.14.2 Auto PA Task Trigger

When an invoice becomes `matched`, the system decides, for each PO the invoice pays, whether the next step is to confirm receipt or to raise a Payment Application.

**Trigger event:** Invoice `status` transitions to `matched` — via auto-match, manual match, review approval, exception acceptance, or an edit that newly matches it.

**Recipient identification:** Invoice → PO(s) (header PO and every allocation PO) → PR → `created_by`. For an NC-imported PO that has no PR, the *create_pa* task goes to the **ERP PA Officer** role pool; a non-NC PO with no PR gets no task.

**Deduplication rule:** Only one open `create_pa` task is maintained per PO at a time. If an open task already exists for the PO, the requester is re-notified with the new invoice reference instead of a duplicate task being created.

**Completion:** `create_pa` tasks are completed when any PA covering the PO is created, and reopen if the PO later stops being covered by any PA (so recurring invoices on the same PO raise the task again).

**Auto PA Task FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **INV-010** | When an invoice becomes `matched` (auto, manual, review approval, exception acceptance, or an edit that newly matches it) the system evaluates **every** PO the invoice pays (header PO and allocation POs). |
| **INV-011** | PO already three-way matched → a *create_pa* task to the PR's requester; for an NC-imported PO with no PR, to the **ERP PA Officer** role pool. Any open *confirm_receipt* task on that PO is closed. One open *create_pa* per PO — if one exists the requester is re-notified with the new invoice number. |
| **INV-012** | PO not yet received → a *confirm_receipt* task: physical goods to the Warehouse Staff pool, services to the PR's service owner (falls back to the requester). Not raised when the PO already shows receipt evidence. Creating the GR closes it and raises *create_pa*. |
| **INV-013** | Creating any PA covering the PO completes its *create_pa* task(s); a prepayment PA also completes *create_prepayment_pa*. If the PO later is no longer covered by any PA (removed from a draft PA's PO set, or its PA deleted in Data Maintenance), the *create_pa* task reopens. Fee-only invoices never raise *create_pa*. |
| **INV-014** | The notification includes invoice number, PO number, vendor and a deep link; Create PA opened from the task pre-selects the PO. |

#### 3.14.3 Three-Way Match

Variance is checked per PO line against everything invoiced on that line. "Three-way" refers to the invoice being matched **and** a goods receipt being linked; there is no quantity comparison against the GR.

| FR ID | Requirement |
|-------|-------------|
| **INV-3WM-001** | Variance is measured per allocated PO line: (sum of pre-tax amounts allocated to that line by **all** invoices) − (PO line pre-tax total). Header-level allocations compare against the PO pre-tax subtotal. Non-PO fee lines are excluded. |
| **INV-3WM-002** | Under-invoicing (negative variance) always passes — a PO line may be billed across several invoices. Over-invoicing passes when \|variance %\| ≤ the match tolerance; an exact match always passes. |
| **INV-3WM-003** | Match tolerance is a company setting (`invoice_match_tolerance_pct`, default **5%**). It is not editable in the UI. |
| **INV-3WM-004** | A passing match sets the invoice to `matched`; the Invoice Detail shows the variance per line ("partially invoiced" or "matched within tolerance"). A failing match sets `exception` and opens a *resolve_exception* task for the AP pool. |
| **INV-3WM-005** | AP resolves an exception by **Accept with justification** (→ `matched`, PA tasks raised as for any match) or **Request credit note** (stays `exception`). Either way a note is required (the justification, or the instructions for the vendor); the API refuses a blank one. The note is kept separately from the system's variance description, so later re-matches do not overwrite it. It is shown on the Invoice Detail page in three places: the *Exception Resolution* card on the 3-Way Match tab, under the resolution entry in History, and in the exception banner while the invoice is still in `exception`. An accepted invoice is labelled *Exception accepted* (not *Passed*) on the 3-Way Match tab, and its match entry in History reads *Exception raised*. If a later edit changes the total or variance of an accepted invoice, the acceptance is revoked (note included) and the invoice returns to `exception`. |
| **INV-3WM-006** | "Three-way matched" (used by the PA receipt gate, PA-013) means: the invoice is `matched` to the PO (header or allocation) **and** a goods receipt for that PO is linked. No quantity comparison against the GR is performed. |
| **INV-3WM-007** | The *resolve_exception* task closes automatically when the invoice leaves `exception` (rematch, edit, accept) or is deleted. |

#### 3.14.4 Invoice List — Role-Based Visibility

| Role(s) held | Invoices visible |
|------|-----------------|
| Any unrestricted role (any role other than those below), as primary **or** additional role | **All** invoices |
| Only restricted roles (`requester`, `dept_manager`, `dept_admin`, `gm`, `opm`, `supervisor`, `director`) | Union across all held roles of: invoices whose header PO **or any allocated PO** is in the user's PO scope (own PRs / department / mapped departments / directed departments / direct reports); invoices the user uploaded (requester); invoices on which the user (or a delegator) holds an open task; invoices the user matched; agreement-route invoices of agreements the user can see (with *View Agreements*). |

**Chain resolution:** invoice → header PO and allocation POs → PR → cost center → department.

**FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **INV-VIS-001** | `GET /api/v1/invoices` (list) and `GET /api/v1/invoices/{id}` (detail) apply the same rule above. Users holding any unrestricted role see all invoices (subject to INV-VIS-004). |
| **INV-VIS-002** | For restricted users the visible set is the union of the PO-scope, own-upload (requester), open-task, matched-by and agreement rules in the table. |
| **INV-VIS-003** | `GET /api/v1/invoices/{id}` must enforce the same scope for restricted roles and return HTTP 404 (not 403) for out-of-scope records. |
| **INV-VIS-004** | Both endpoints must additionally honour the `view_invoice` permission from the Access Control Matrix (Portal → Admin → Access Control). When the union of the caller's active-role permissions does not include `view_invoice`, the list returns empty and the detail returns HTTP 404, irrespective of the role's scope. Invoice attachments are readable under `view_invoice`. |
| **INV-VIS-005** | The invoice list on an agreement's page (Agreement Detail) shows all invoices matched to that agreement to anyone who can open the agreement. |

#### 3.14.5 Invoice Edit

Invoices in editable statuses (`unmatched`, `exception`, `matched`) can be corrected by authorised users without deleting and re-uploading.

**Who can edit:** Any user with the `invoice_upload` permission (same permission as create/delete), plus System Admin.

**Editable statuses:** `unmatched`, `exception`, `matched`. Invoices in `approved` or `paid` status cannot be edited.

**Editable fields:**
- Vendor Invoice # (vendor is read-only after creation)
- Invoice Date
- Due Date
- Pre-tax Amount
- Tax Amount (ignored when the invoice has tax lines — see INV-EDIT-003)
- Currency
- Notes
- Line Items (add / edit / remove rows; row totals auto-computed from qty × unit price and saved)
- Goods Receipts (multi-select, when invoice has a linked PO — see INV-EDIT-008)

**Re-match on save:** If the invoice already has a linked PO, saving the edit automatically re-runs the match. `gr_value` is recomputed as the sum of all selected GRs' line-item totals. Status (`matched` / `exception`) and variance are recalculated from the updated amounts and GR selection. Editing an agreement invoice re-derives the agreement's consumed amount (AGR-005).

**UI entry point:** An **Edit** button (pencil icon) is shown in the Invoice Detail page header when the invoice is in an editable status and the user has `invoice_upload` permission. Clicking it opens an inline edit form in-place (Details tab), replacing the read-only view. Save / Cancel controls are shown at the bottom of the form.

**Data model:** An invoice can link several GRs. For backward compatibility, the first selected GR is also kept as the invoice's single GR reference; the GR number may be a comma-separated list when multiple GRs are linked.

| FR ID | Requirement |
|-------|-------------|
| **INV-EDIT-001** | `PATCH /api/v1/invoices/{id}` accepts partial updates for: `vendor_invoice_number`, `amount`, `tax_amount`, `currency`, `invoice_date`, `due_date`, `notes`, `line_items`, `gr_ids`. All fields are optional; only provided fields are updated. |
| **INV-EDIT-002** | Editing is permitted only when invoice status is `unmatched`, `exception`, or `matched`. Attempting to edit an `approved` or `paid` invoice returns HTTP 409. |
| **INV-EDIT-003** | `total_amount = amount + tax_amount` on every save; the client-submitted total is ignored. When the invoice has tax lines, `tax_amount` is the sum of those lines and any submitted tax amount is ignored. |
| **INV-EDIT-004** | If the invoice has a linked `po_id` at save time, the backend automatically re-runs the match using the GR selection from `gr_ids` in the PATCH body (if provided), otherwise the invoice's existing `gr_ids`. An empty `gr_ids` list clears all GR links. Status and variance are recomputed. If the existing allocations no longer sum to the edited pre-tax amount, they are cleared and the invoice returns to `unmatched` (orphaned *create_pa* tasks are closed). |
| **INV-EDIT-005** | An edit that moves the invoice from `unmatched`/`exception` to `matched` raises the Create-PA / Confirm-Receipt tasks (§3.14.2); an edit that leaves it `matched` does not re-notify; an edit that lands it in `exception` raises the *resolve_exception* task. |
| **INV-EDIT-006** | The Edit button is visible in the Invoice Detail header when: invoice status ∈ {`unmatched`, `exception`, `matched`} AND the user has `invoice_upload` permission. |
| **INV-EDIT-007** | The Edit form is rendered inline within the Details tab. Switching to another tab while editing navigates away from the form (changes are discarded only on Cancel). |
| **INV-EDIT-008** | When the invoice has a linked PO, the edit form shows a **GR multi-select checklist** listing all non-cancelled GRs for that PO (GR number, title, status). The currently linked GRs are pre-checked. The user may check/uncheck any combination; zero checked = no GR linked. A badge shows the count of selected GRs. The selection is submitted as `gr_ids` (list of UUID strings) in the PATCH body. |
| **INV-EDIT-009** | `gr_value` on the invoice is always the **sum** of all selected GRs' line-item totals. The first selected GR is stored as the single GR reference for backward compatibility; the GR number may contain a comma-separated list when multiple GRs are selected. |
| **INV-EDIT-010** | `POST /invoices/{id}/match` accepts `gr_ids` (multi) or `gr_id`; when neither is sent, GRs are auto-linked (INV-MATCH-010). |
| **INV-EDIT-011** | Tax lines (code, taxable amount, tax, recoverable) are edited on Invoice Detail by *Upload Invoice* holders; not permitted once the invoice is paid. |

#### 3.14.6 Unmatch

AP staff (holders of *Match Invoice*) and admins can reverse a match from Invoice Detail — **Unmatch PO** (invoice returns to `unmatched`, allocations removed, open exception task closed) or **Unmatch GR** (only the goods-receipt link is removed). A reason is mandatory and recorded in the audit log.

| FR ID | Requirement |
|-------|-------------|
| **INV-UNM-001** | Unmatch is refused when the invoice is matched to an agreement (use the agreement's receipt/period panels), is `approved`/`paid`, or is listed on any non-cancelled PA (the PA number and status are named). |
| **INV-UNM-002** | After unmatch, *create_pa* / *confirm_receipt* tasks on the affected POs are closed only if the PO no longer has any invoice that justifies them. |

#### 3.14.7 Invoice Delete, List & Detail

| FR ID | Requirement |
|-------|-------------|
| **INV-DEL-001** | An invoice can be deleted only in `unmatched` or `exception` (Upload Invoice permission). Deletion closes its open match/review/exception tasks, voids the Finance AP mirror, releases any agreement receipts or billing period it held, and recomputes the agreement's consumed amount. |
| **INV-LIST-001** | The invoice list offers a status filter, search across invoice ref, vendor, vendor invoice # and PO #, and stat cards (including Paid). It shows a Due Date column with ageing: *Overdue N d* (red), *Due today* / *Due in N d* for ≤ 5 days (amber); paid invoices are not aged. Every column sorts on the server across all pages (UI-SORT-004); the legacy `sort=due_date` / `sort=-due_date` form is still accepted. The Unmatched tab opens oldest upload first. An *Overdue* filter shows past-due, unpaid invoices. |
| **INV-LIST-002** | Clicking an invoice's due date opens a chain drawer showing Match PO → Link GR → Create PA → Payment for that invoice; PA steps are shown only to users with *View PA*. |

### 3.15 Payment Applications (PA)

#### 3.15.1 PA Types

| PA Type | Description | Prerequisite | `pa_type` value |
|---------|-------------|-------------|-----------------|
| **Standard** | Full or partial payment for delivered and invoiced goods/services | One or more POs **of the same vendor and currency**, each with a three-way matched invoice (or an authorised override, PA-013), **or** an active agreement (§3.15.7) | `regular` |
| **Prepayment** | Advance payment to vendor before delivery | Vendor Max Prepayment % > 0; no other open Prepayment PA on same PO; exactly one PO | `prepayment` |
| **Settlement** | Closes out a prepayment PA when actual delivery value differs from prepayment | Open prepayment PA on same PO; `prepayment_pa_id` required; exactly one PO | `settlement` |
| **Balance** | Remaining balance after a prepayment, once goods/services delivered | Prepayment PA settled; `prepayment_pa_id` required; exactly one PO | `balance` |

**PA linkage:** Settlement and Balance PAs carry a `prepayment_pa_id` referencing the original Prepayment PA. The backend validates this linkage at creation time — the referenced PA must be of type `prepayment` and belong to the same PO. Prepayment, Settlement and Balance PAs are always raised against exactly one PO; a regular PA may cover several POs. A PA with an agreement and no PO is an **agreement PA** (§3.15.7) and is always `regular`.

**PA source.** Every PA records the flow that raised it: `po`, `agreement`, `freight` (a **Freight PA**, raised from matched courier invoices with New Freight PA on the PA page — no PO and no agreement, §3.21), `direct` (a **Direct PA**, raised from invoices matched to no PO and no agreement with Create Direct PA on the PA page, §3.22) or `oa_direct` (OA's retired Direct PA — its invoice ids are OA expense invoices; migration `dp01_direct_pa` re-tagged those rows from `direct`). EPMS lists, opens and acts on `po`, `agreement`, `freight` and `direct` PAs; `oa_direct` PAs are not EPMS documents. A Freight PA is always `regular`; its amounts, invoices and cost split come from its courier invoices and only its title and notes can be edited on the PA.

#### 3.15.2 Standard PA

**Who can create:** any user with *Create/Edit PA* (`epms.pa.write`); the New PA button is shown only to them. A user whose primary role is Requester may only include POs raised from their own PRs, except when they also hold: **Procurement Officer** (any PO), **ERP PA Officer** (NC-imported POs that have no PR), or **Dept Admin** (POs whose PR belongs to their own department). The same check runs when the PO set is edited. Approval routing is unaffected by who created the PA. The PA is typically created in response to a `create_pa` task (§3.14.2).

**Prerequisites for Standard PA creation:**

| Prerequisite | Condition |
|-------------|-----------|
| PO(s) | One or more POs, same vendor and currency (regular PA only) |
| Invoice(s) | At least one invoice, each belonging to one of the PA's POs (header link or allocation). Required to **submit** a PO-route PA (except prepayment); a draft may be saved without one |
| Due dates | All invoices on one PO-route PA fall due within 7 days of each other |
| Receipt | Each PO has a three-way matched invoice, or an authorised override |
| Not already claimed | Invoices already on another live PA are not offered |

**3-step PA creation wizard:**

1. **Step 1 — Select PO(s)**: the user ticks one or more POs (same vendor and currency). POs whose invoices are all already claimed by other PAs are not offered.
2. **Step 2 — Select Invoices & Verify Amounts**: the user selects invoices belonging to the selected POs; the form pre-ticks the earliest-due group (PA-011); goods receipts default from the selected invoices (PA-014); the system displays the invoice summary.
3. **Step 3 — Payment Details**: the user reviews the charge breakdown below and submits.

**Charge breakdown:**
- **Pre-tax subtotal** — auto-filled from the PA's line items when they follow the invoices (PA-018) — for an invoice split across POs that is this PA's share only; otherwise from the selected invoices' pre-tax amounts, or from the selected PO lines if no invoice is selected. Editable.
- **Tax** — auto-filled from the selected invoices' tax amounts; with no invoice selected, pre-tax × the PO's (or agreement's) tax rate, CAD only, 13% if no rate is recorded. Editable; once edited it is no longer recalculated. Tax code and rate are stored with the PA; clearing the tax clears its rate and code.
- **Shipping / Freight** and **Other charges** (+ note) — added after tax; **never taxed**. A charge that is taxable must be entered as a line item so it falls in the subtotal.
- **Prepayment applied** (settlement PAs only).
- **Total payment amount** = pre-tax + tax + shipping + other − prepayment applied, floored at 0; always computed server-side.
- Payment reference / memo
- Attachments

Payment method and funding bank account are chosen when the payment is executed (§3.18.1), not on the PA. Vendor credits are netted at execution (§3.18.2), not entered on the PA.

**PA line items:**

Each PA carries line items: Description, Qty, Unit, Unit Price, Line Total, and a note naming the invoice the line came from.

- **When every selected invoice was matched line by line** (each invoice line allocated to a PO line, §3.14), the lines are built from those invoices and shown read-only: one line per invoice line allocated to one of the PA's POs, on that PO line, with the invoiced quantity and unit price and a line total equal to the allocated amount; plus one line, with no PO line, per non-PO fee on an invoice whose header PO is on the PA (so a fee on an invoice split across POs is paid once). A fee-only invoice headed on one of the PA's POs is treated the same way. The server builds the lines again on create and on edit and ignores any lines sent with the request (PA-018).
- **Otherwise** (any invoice matched only against the PO total, or no invoice selected) the user ticks the PO lines the PA covers; a ticked line is taken at the PO line's full quantity.

Discount and negative lines are allowed (unit price ≤ 0 permitted).

**Standard PA FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PA-001** | PA number is system-generated when the PA is created: `PA-YYYYMMDD-XXXX` (see §3.15.5). |
| **PA-002** | A regular PA can pay several invoices across several POs of the same vendor and currency. |
| **PA-003** | Every invoice on a PO-route PA must belong to one of the PA's POs (header PO or allocation); otherwise 422. Removing a PO from a draft re-checks this. |
| **PA-006** | PA PDF is generated on final approval and stored as an attachment; it includes the applicant and the named approval chain, and unit prices are not rounded. The PDF can be regenerated. |
| **PA-007** | Requester receives a notification when their PA is approved (final step). |
| **PA-008** | On final PA approval, a `process_pa` task is created for the **Payment Officer** role pool. The task appears in the Task Inbox with the PA number, vendor, and payment amount. |
| **PA-009** | The payment is executed with **Mark as Processed** on the PA Detail page (§3.18.1, PAY-001) by Payment Officer, Finance Manager or Finance BP (primary or additional role) or System Admin (primary role). This completes the `process_pa` task and marks the linked invoices `paid`. AP Clerk cannot execute payments. |
| **PA-010** | A non-prepayment PO-route PA cannot be **submitted** without at least one linked invoice (409). Prepayment PAs and agreement PAs are exempt (agreement PAs have their own gate, §3.15.7). |
| **PA-011** | Invoices on one PO-route PA must fall due within **7 calendar days** of each other (earliest vs latest due date), enforced on create and edit; the form pre-selects the earliest-due group. Agreement PAs are exempt. |
| **PA-012** | The invoice picker hides invoices already listed on another PA that is not cancelled/rejected, and names that PA ("already on PA-… and cannot be paid twice"). This is enforced in the form only; the server does not refuse a second PO-route PA for the same invoice. |
| **PA-013** | **Receipt gate.** A non-prepayment PO-route PA requires each PO to have a three-way matched invoice (INV-3WM-006); otherwise creation fails (422). Without one, the PA can only be created by a user holding *Override Receipt on PA* (`pa_override_receipt`, otherwise 403) who ticks the override and gives a reason; override, reason and user are stored on the PA. |
| **PA-014** | Goods receipts on the PA default from the selected invoices' linked GRs and are recomputed only when the invoice selection changes. |
| **PA-015** | If the PA is created but its submission fails, the user is taken to the saved draft with the error; nothing is lost. |
| **PA-016** | PA list search covers PA #, vendor, PO # (any linked PO) and agreement #; filters by status and department (PR department or agreement department); shows the current approval step under the status. |
| **PA-017** | PA Detail shows invoice-vs-PO variance per line (with line detail), house-account receipt-vs-invoice differences, and an *Attachments* button collecting every attachment along the chain (PR, PO, GR, invoices, agreement receipts) for ZIP download or printing. In the document chain the PA is nested under its source invoice. |
| **PA-018** | **Line items follow line-matched invoices.** When every invoice on a PO-route PA was matched line by line to the PA's POs, the PA's line items are derived from those invoices (see *PA line items* above) on create and on every edit that changes the invoices, the lines or the PO set; lines sent by the client are ignored. If any invoice was matched only against the PO total, the client's hand-picked lines are stored as sent. Agreement PAs carry no line items. |

PA-004 (total ≤ sum of invoice totals) and PA-005 (payment method / vendor bank on the PA) are retired: the total is computed as described above, and method and bank are chosen at execution.

#### 3.15.2.1 PA Edit

- Available for PAs in `draft` or `returned` status
- Pre-populates all fields from existing PA data; the PO set of a regular PA can be changed (see PA-EDIT-002); the agreement of an agreement PA is locked
- Invoice selection, GR selection, line items, and all charge breakdown fields remain editable
- **"Save Draft"** — PATCH the PA and navigate back to the detail page; status stays `draft` / `returned`
- **"Save & Resubmit"** — PATCH the PA then immediately trigger the `submit` action; the PA re-enters the approval workflow (see PA-EDIT-003)

| FR ID | Requirement |
|-------|-------------|
| **PA-EDIT-001** | Edit is accessible only when the PA is in `draft` or `returned` status. Any other status redirects to the detail page with an error. |
| **PA-EDIT-002** | The PO set of a regular PA may be changed while editing (add POs; remove any but the last), subject to the same rules as creation (same vendor/currency, ownership, every invoice belonging to a remaining PO); a PO removed from the last PA covering it gets its Create-PA task back. Agreement PAs have no PO and re-run the agreement invoice gate (§3.15.7) when invoices change; PO-route PAs re-run the 7-day due-date rule (PA-011). |
| **PA-EDIT-003** | Resubmitting a returned PA resumes the approval at the step that returned it; approvals given before that step stand and all prior history is retained in the History tab. A recalled PA restarts from the first step. |
| **PA-EDIT-004** | Clearing a field (e.g. tax rate/code, notes, other-charges note, prepayment %, expected settlement date) clears it; an omitted field is left unchanged. |

#### 3.15.3 PA Approval Flow

PA approval uses the configurable workflow engine (§3.3), document type `pa`, edited in Portal → Admin → Approval Workflows → PA. Default chain (engine fallback; production configuration may differ):

| Step | Approver | Notes |
|------|----------|-------|
| 1 | Department Manager | Of the routing department (PA-APR-001); auto-skipped on a cross-department PA |
| 2 | Director | Auto-skipped when the routing department has no active Director, or on a cross-department PA |
| 3 | GM / OPM | Resolved per routing department via Approval Routing (§3.3.8) |
| 4 | Finance BP | Finance Business Partner review |
| 5 | Finance Manager | Payment authorisation |

Available additional step: **Finance Director** — a company-unique post (held only as an additional role) intended as the final payment sign-off after Finance Manager; its task goes to whoever holds the post, regardless of the PA's department. Whether it is in the live PA workflow is configuration. AP Clerk is not a PA approver; payment execution follows approval (§3.18.1).

| FR ID | Requirement |
|-------|-------------|
| **PA-APR-001** | Department-scoped steps (Department Manager, Director, GM/OPM) route on: the agreement's department (agreement PA); else the department of the primary PO's PR; else the submitter's department. |
| **PA-APR-002** | **Cross-department PA.** When the PA's POs come from PRs of more than one department, the Department Manager and Director steps are auto-skipped (shown struck-through in the timeline with the reason) and the GM/OPM step is resolved from the engine-wide *cross-department GM or OPM* setting (Approval Routing; default GM). |
| **PA-APR-003** | Director / Supervisor steps are auto-skipped when the routing department has no active holder. |
| **PA-APR-004** | A returned PA resumes at the returning step on resubmit; an approver's **Reject** sets the PA to `cancelled`; the submitter (or System Admin) can **Recall** a submitted/in-review PA back to `draft`. |
| **PA-APR-005** | On final approval: the PA PDF (including applicant and the named approval chain) is generated and attached; a *process_pa* task is created for the **Payment Officer** role; for a settlement PA the source prepayment PA is marked settled with its variance. |

#### 3.15.4 Prepayment PA

A Prepayment PA allows the Requester to apply for an advance payment to a vendor before goods are delivered or services rendered.

**3-stage prepayment flow:**

```
Stage 1 — Prepayment PA
  Requester creates Prepayment PA → Approval Flow → Approved → Payment Officer executes payment

Stage 2 — Delivery / Service Completion
  Vendor delivers goods (GR Path A/C) or confirms service (GR Path B)
  Invoice received and matched

Stage 3 — Settlement PA
  Requester creates Settlement PA (Settle button on the prepayment PA) to reconcile prepayment against actual delivery value
  If actual < prepayment: vendor issues credit note / refund
  If actual > prepayment: Balance PA created for the difference
```

**Prepayment scenarios:**

| Scenario | Actual Value vs Prepayment | Resolution |
|----------|---------------------------|-----------|
| Exact match | Equal | Zero-cash Settlement PA reconciles immediately (PP-013) |
| Under-delivery | Actual < Prepayment | Settlement PA (net 0, confirmed by Finance BP — PP-013) + vendor credit note / partial refund |
| Over-delivery | Actual > Prepayment | Settlement PA for prepayment + Balance PA for difference |
| Cancellation | Delivery not made | Vendor refund required; there is no cancellation PA type |

**Prepayment FR IDs:**

| FR ID | Requirement |
|-------|-------------|
| **PP-001** | Prepayment PA can only be created if the vendor's effective prepayment cap is > 0. The effective cap is the vendor's Max Prepayment % if set, otherwise the system cap in Prepayment Config. Enforced in `POST /pa` — returns HTTP 422 if cap ≤ 0. |
| **PP-002** | The requested `prepayment_pct` cannot exceed the vendor's effective cap (vendor-level override takes priority over system cap). Enforced in `POST /pa`. |
| **PP-003** | The requested `prepayment_pct` cannot exceed the system-wide cap in Prepayment Config, regardless of vendor override. Enforced in `POST /pa` as a second independent check. |
| **PP-004** | A PO can have at most one open (non-cancelled, non-rejected) Prepayment PA at a time. `POST /pa` with `pa_type=prepayment` returns HTTP 409 if one exists. |
| **PP-005** | Prepayment PA follows the same approval flow as Standard PA (§3.15.3). All PA types share the same workflow engine. |
| **PP-006** | On Prepayment PA approval, the system starts the settlement SLA clock (configurable in Prepayment Config). **Not implemented** — see §11. An expected settlement date can be recorded on the PA, but no SLA is enforced. |
| **PP-007** | If the settlement SLA is exceeded, the system sends a `prepayment_settlement_overdue` notification to the Requester and Department Manager, and creates a `settlement` task if one does not exist. **Not implemented** — see §11 (no scheduled job exists). |
| **PP-008** | While a Prepayment PA is open (status not `cancelled`, `rejected`, or `processed`), no **regular** PA can be created on the same PO. Enforced in `POST /pa` — returns HTTP 409 with the blocking PA number. Settlement and Balance PAs are exempt (they require the open prepayment). |
| **PP-009** | Settlement and Balance PAs must supply `prepayment_pa_id` referencing the original Prepayment PA on the same PO. Backend validates the link at creation time (HTTP 422 if missing or mismatched). |
| **PP-010** | Balance PA can only be submitted after the Settlement PA is approved. **Not implemented** — see §11 (the prepayment link exists; no submit-time status check). |
| **PP-011** | All prepayment-related PAs (Prepayment, Settlement, Balance) are linked in the PO Document Tree (§3.12.4) and visible together on the PO Detail page with their PA type label. |
| **PP-012** | Settlement/Balance: *Prepayment applied* may not exceed the amount actually paid on the referenced prepayment PA, and the net payable may not be negative (a credit note is required instead). |
| **PP-013** | A settlement whose net payable is 0 is a reconciliation, not a payment: if final value equals the prepayment it is processed immediately (invoices paid, prepayment settled, Finance AP mirror settled, no approval, no bank movement); if the prepayment exceeded the final value, a *confirm_settlement* task goes to the Finance BP pool and the reconciliation completes on confirmation (*Create/Edit PA* permission). |
| **PP-014** | Approving a PO marked prepayment-required creates a *create_prepayment_pa* task for the PR requester (or, without a PR, the PO sign-off submitter / creator). |
| **PP-015** | A prepayment PA's Detail offers **Settle** (to a primary-role Requester or System Admin), opening New PA pre-filled as a settlement of that prepayment. |

#### 3.15.5 PA Document Numbering

Payment Applications are assigned a system-generated number when the PA is **created** (including drafts):

```
PA-YYYYMMDD-XXXX
```

Where `YYYYMMDD` is the creation date (UTC) and `XXXX` is a zero-padded daily sequence (highest existing number for that day + 1; starts at `0001` each calendar day). Example: `PA-20260410-0007`.

Applies to all PA types (Standard, Prepayment, Balance, Settlement). The number is assigned by the backend and is never editable.

#### 3.15.6 PA Status Lifecycle

| Status | Description |
|--------|-------------|
| `draft` | PA created but not yet submitted (or recalled); editable |
| `submitted` | Submitted; awaiting first approver. Also holds a zero-cash settlement awaiting Finance BP confirmation (PP-013) |
| `in_review` | Progressing through approval steps |
| `approved` | Final approval granted; PA PDF generated; *process_pa* task created for **Payment Officer** |
| `processed` | The payment was executed in Finance (§3.18.1) — or, for a zero-cash settlement, reconciled; linked invoices become `paid` (a prepayment's invoices `partially_paid`) |
| `returned` | Returned for revision by any approver; Requester notified; **PA is editable** (Save Draft or Save & Resubmit) |
| `cancelled` | Cancelled by the creator (draft/returned) **or rejected by an approver** (PAs have no separate `rejected` status); terminal state |

#### 3.15.7 Agreement PAs

Raised from Agreement Detail → *Create PA*. Type is always `regular`; the PA has no PO.

| FR ID | Requirement |
|-------|-------------|
| **AGR-060** | The agreement must be visible to the creator and admissible (active and within valid-to + grace days, AGR-012). |
| **AGR-061** | At least one invoice is required; every invoice must be matched to **this** agreement and be `matched`/`approved`. Recurring: each invoice must be linked to a billing period **and** that period's service confirmed. House account: each invoice must have receipts attached or be explicitly settled without receipt evidence. The same gate re-runs whenever the PA's invoices are edited. |
| **AGR-062** | The 7-day due-date rule (PA-011) does not apply — an agreement's accumulated statements may be paid together. |
| **AGR-063** | Tax defaults from the agreement's tax rate; currency from the agreement. Not-to-Exceed is shown as a warning only. |
| **AGR-064** | Approval routes department-scoped steps on the agreement's department (PA-APR-001). PA Detail shows linked invoices and, for house accounts, receipt-vs-invoice differences. |

---

### 3.16 Legacy PMS Data Migration (SharePoint Import)

One-time migration **and** ongoing manual sync of legacy procurement data from the
old SharePoint-based PMS (`https://canadaroyalmilk.sharepoint.com/sites/pr2`) into
EPMS: Purchase Requests, Purchase Orders, Payment Applications, their line items,
and Invoices, with attachments. Implemented as a command-line importer plus an
admin page that drives it.

#### 3.16.1 Admin Page & Operations

- **Location:** Admin Panel → **PMS Data Import**. Gated by the `admin_panel`
  permission (Access Control Matrix).
- **Operations (manual-trigger only — no scheduler):**
  - **Full import** — import everything; documents already present (by number) are
    skipped (idempotent insert).
  - **Incremental sync** — re-sync only documents whose SharePoint `Modified` is at
    or after the last successful sync (the "watermark"); existing documents get
    header field updates and line-item reconciliation (§3.16.6), new documents are
    inserted in full.
  - **Import Attachments** — imports invoice, PR, PO and PA attachments on their
    own, with a separate report.
  - **Dry-run** toggle — builds/validates and reports what *would* happen, writing
    nothing (read-only; safe against production).
- Runs as a background task; the page polls live status and shows a per-run report
  (inserted/updated/skipped counts, auto-created vendors, temp users, attachment
  results, discarded invoices, and unmatched samples) plus run history. **FR-PMS-001.**
- Run history is persisted and survives restarts; a run interrupted by a restart is
  shown as errored. **FR-PMS-012.**
- The whole load is a single atomic transaction; re-running is idempotent. The
  command-line importer refuses to run against the production database unless
  production is explicitly allowed. **FR-PMS-002.**

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

Imported PR/PO/PA receive a **reconstructed approval history**: submission is
credited to the creator and each approved workflow step to the current holder of
that role; machine "PMS migration" stamps are not credited as approvers. Documents
still in review get an open approval task at their current step. Follow-on inbox
tasks for imported documents are back-filled only for documents created on or after
2026-06-01 (§3.7.1). **FR-PMS-009.**

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
- **Invoices** — an invoice is imported only when a PMS PO Item or PA Item line
  references it and its PO exists in EPMS or in the PMS PO list; otherwise it is
  discarded and counted in the report. Invoices sharing the same vendor + invoice
  No are merged into one invoice allocated across their POs. Invoice vendor and PO
  are also resolved from POs imported in earlier runs. **FR-PMS-010.**

#### 3.16.5 Status Mapping

| Source | EPMS status |
|---|---|
| PR `Approved` | `approved` |
| PR `Manager Approving` | `in_review` · `Rejected To Edit` → `returned` · `*Rejected` → `rejected` |
| PO `GM APPROVED` + receive `PLACING ORDER` | `approved` (awaiting place-order) |
| PO `GM APPROVED` + `DELIVERING`/`RECEIVED` | `issued` · Status0 `COMPLETED`/`CLOSED` → `closed` · `CANCELED` → `cancelled` |
| PO `*APPROVING` / `*REJECTED` | `in_review` / `rejected` |
| PO PaymentStatus `PAID` | `closed` |
| Any PO from the **PO List Backup** archive | `closed` (unless rejected/cancelled, which win) |
| PA `PAID` | `processed` · `WAITING PAYMENT` → `approved` · `*APPROVING`/`AP REVIEW` → `in_review` · `*REJECTED`/Status0 `CANCELLED` → `cancelled` |

**FR-PMS-011:** PO status — payment status PAID → `closed`; anything in the PO List
Backup archive → `closed` (unless rejected/cancelled).

#### 3.16.6 Incremental Sync & Conflict Handling

Incremental runs pull only rows changed since the watermark (`Modified ge …`),
**update header-level fields** (status, amounts, dates) of existing documents and
**reconcile their line items** (add, change, remove — lines already referenced by a
GR are never removed). To avoid clobbering EPMS edits, a
document is **skipped** when its EPMS `updated_at` is later than the SharePoint
`Modified` (i.e. it was edited locally after the last sync). The importer stamps
`updated_at` = SharePoint `Modified` on insert so this comparison is reliable. The
watermark advances only on a committed run. **FR-PMS-007.**

#### 3.16.7 Attachments

Each `INVOICE` list item's attachment files are downloaded from SharePoint and
uploaded to the File Server, with rows recorded in `invoice_attachments`
(`invoice_source='epms'`), idempotent by `(invoice_id, file_name)`. PR, PO and PA
attachments are imported the same way. Attachments are imported after the data
load on a committed run, or on their own via **Import Attachments**. **FR-PMS-008.**

---

### 3.17 Purchase Agreements (AGR)

A Purchase Agreement is a standing authorisation with one vendor for spend that does not go through PR → PO → GR: house accounts, recurring charges (rent, subscriptions, service contracts) and milestone-billed contracts. It carries no quantities and is never received against; invoices are matched directly to it (2-way) and paid by an agreement PA (§3.15.7).

#### 3.17.1 Agreement Types

| Type | Use | Evidence before payment |
|---|---|---|
| `house_account` | Repeated small purchases billed periodically by the vendor | Receipts (§3.17.5) attached to the invoice, or an explicit "settle without receipt evidence" with a reason |
| `recurring` | Fixed repeating charge on a cycle | The invoice is linked to a billing period, and the agreement owner has **confirmed service** for that period |
| `milestone` | Staged contract billed on deliverables | The invoice is linked to a milestone stage |

#### 3.17.2 Fields and Rules

| FR ID | Requirement |
|---|---|
| **AGR-001** | Number `AGR-YYYYMM-NNNN`, system-generated on create. |
| **AGR-002** | Header: title, type, vendor, contract #, vendor reference, contact email, valid from/to (to ≥ from), grace days (default 30, 0–365), Not-to-Exceed (optional), currency, tax code/rate, department, cost center, budget code (must be a known budget code), **Owner**, notes. |
| **AGR-003** | Recurring only: cycle (`weekly`, `monthly`, `quarterly`, `yearly`, `special_monthly`), expected invoice day (weekly: 1–7, Monday = 1; otherwise 1–31), anchor month (required for quarterly/yearly), active months (required for, and only for, special monthly — the months that bill), optional schedule start date (inside the validity window, for an agreement entered in the system after it started), expected pre-tax amount per period, tolerance %, overdue-after days (default 7). Settings that produce no billing period are rejected. These fields are rejected on non-recurring agreements. |
| **AGR-004** | Milestone only: one or more stages (name, expected timing, amount **or** % of Not-to-Exceed — the other is derived; % requires a Not-to-Exceed). At least one stage is required to submit. |
| **AGR-005** | Not-to-Exceed is a **warning only**: Agreement Detail shows consumed vs ceiling and "Over ceiling — warning only"; no action is blocked. *Consumed* = sum of the total (tax-inclusive) of all invoices matched to the agreement, re-derived on every match/edit/unmatch/delete. |
| **AGR-006** | Create/edit requires *Create / Edit Agreements* (`epms.agreement.write`; seeded: System Admin, Procurement Officer, Procurement Manager). Editing is allowed only in `draft`/`returned`; after approval terms can only be changed through Data Maintenance (§3.6.8). |
| **AGR-007** | **Owner is required to submit.** The owner confirms each period's service and is chased for missing invoices. Changing the owner (Data Maintenance) re-assigns open confirmation tasks to the new owner. |

#### 3.17.3 Approval and Status

| FR ID | Requirement |
|---|---|
| **AGR-010** | Approval uses the workflow engine, document type `agr` (default chain: Department Manager → Procurement Manager → Finance Manager; configurable). Department-scoped steps route on the **agreement's own department**, not the submitter's. |
| **AGR-011** | Statuses: `draft` → `submitted` → `in_review` → **`active`** (final approval); `returned`; `rejected`; `cancelled`. The statuses `expired` and `closed` exist but are never set automatically — **Not implemented**, see §11; expiry is enforced only by the date test in AGR-012. |
| **AGR-012** | An agreement accepts new invoice matches and new PAs only while `active` (or `expired`) **and** today ≤ valid-to + grace days. Valid-from is not checked (back-dated agreements may clear older invoices). |
| **AGR-013** | On activation of a recurring agreement, the full set of billing periods for the validity window is generated (from the schedule start date if set). If generation fails, the agreement stays active and administrators are alerted. Changing the expected amount or tolerance after activation does not update periods already generated — see §11. |

#### 3.17.4 Matching Invoices to an Agreement

| FR ID | Requirement |
|---|---|
| **AGR-020** | In the invoice Match panel the user switches between **PO** and **Agreement** routes. Candidate agreements: same vendor, admissible per AGR-012. Same match permission as INV-MATCH-007. |
| **AGR-021** | **Recurring:** the invoice is linked to the period whose expected date is **nearest the invoice date** (ties → earlier period); waived periods are excluded. A period may hold **several invoices**. When the period has an expected amount and a tolerance, the period's **running pre-tax total** must stay within expected ± tolerance; otherwise no period is linked and the invoice goes to `match_review`. A user may name the period explicitly, bypassing the amount check. |
| **AGR-022** | Linking an invoice to a period that was already confirmed clears that confirmation and requires it again. |
| **AGR-023** | **Change period.** AP staff and the agreement's **owner** can move an invoice to another period (or link one that has none) from Invoice Detail, until a non-cancelled PA lists the invoice. |
| **AGR-024** | **Milestone:** the user must pick the stage the invoice pays; each stage takes one invoice. No amount check. |
| **AGR-025** | **House account:** matching is linkage only; receipts are attached as a separate action (§3.17.5). |
| **AGR-026** | Matching to an agreement sets variance to 0; agreement invoices have no PO, allocations or GR links. |

#### 3.17.5 Agreement Receipts (House-Account Evidence)

| FR ID | Requirement |
|---|---|
| **AGR-030** | A receipt (type: counter slip, delivery note, service sign-off) records date, reference, merchant (picked from Vendor Master; flagged when it differs from the agreement's vendor), amounts, received-by (user picker) and photos. It is recorded by Finance on the employee's behalf — never presented as the recipient's own confirmation. Recording needs *Record Agreement Receipts* (`epms.agreement.receipt.write`; seeded System Admin, AP Clerk, Dept Admin). Any agreement type may carry receipts. |
| **AGR-031** | A receipt entered with a "missing receipt" reason goes to `pending_ap_review`; AP approves (→ `open`) or rejects. Receipt statuses: `open`, `pending_ap_review`, `reconciled` (attached to an invoice), `rejected`, `voided` (shown as *Remove*). Receipt reference is unique per agreement among live receipts. |
| **AGR-032** | On Invoice Detail (3-Way Match tab), the receipts the invoice covers are attached (with a reason if amounts differ), or *Settle without receipt evidence* is declared with a mandatory reason. Allowed for: AP (`epms.invoice.match`), the invoice's uploader, the holder of its open `match_invoice` or `attach_receipts` task, and the **agreement's owner**. Anyone else who can open the invoice sees a notice that they cannot attach receipts. The invoice's own agreement always lists its receipts, even after it leaves the admission window. Neither can be changed once a non-cancelled PA lists the invoice. |
| **AGR-033** | Receipts have their own menu entry (*Agreement Receipts*), list across agreements, create page and detail page. |
| **AGR-034** | **Receipt hand-off.** When an invoice ends up `matched`/`approved` against a house-account agreement with no receipts attached and no settlement declared, an `attach_receipts` task goes to the agreement owner (fallback: an active Department Manager of the agreement's department; if neither, the AP Clerk pool). It is checked on every match, match review, invoice edit, exception resolution, receipt change and settlement, and again whenever the Task Inbox is read, so invoices that were already waiting get a task too. It closes by itself once the invoice no longer needs evidence. Changing the agreement's owner moves its open tasks. A task the user dismisses is not raised again. |

#### 3.17.6 Period Confirmation and Missing-Invoice Chase

| FR ID | Requirement |
|---|---|
| **AGR-040** | When an invoice is linked to a recurring period, a *confirm_period* task goes to the agreement owner (fallback: an active Department Manager of the agreement's department; if neither, System Admin with an admin alert). Only the task holder (or System Admin) can confirm. A PA cannot be raised for the invoice until its period is confirmed. |
| **AGR-041** | Daily sweep: a `pending` period whose expected date + overdue-after days (default 7) has passed becomes `overdue`; the owner gets one email per sweep listing newly overdue periods and a single open *chase_agreement_invoice* task, which closes automatically once no period is overdue. Cancelled/closed agreements are skipped. Toggle: Notification Settings → agreement overdue (default on). |

#### 3.17.7 Visibility and Pages

| FR ID | Requirement |
|---|---|
| **AGR-050** | *View Agreements* (`epms.agreement.read`) gates the module (menu entries *Agreements* and *Agreement Receipts*). Unrestricted roles see all agreements; restricted roles see: agreements they created or own (requester), of their department (dept manager/admin), mapped departments (GM/OPM), directed departments (director), created by direct reports (supervisor), and any agreement whose PA they have an open approval task for. Out-of-scope → 404. |
| **AGR-051** | Agreement Detail shows header, NTE bar, billing schedule (each period with its invoices, status and confirmation), milestones, receipts, matched invoices, attachments, document chain, approval timeline with approver names, and Audit History; *Create PA* is offered only when at least one invoice is payable (AGR-061). |
| **AGR-052** | The Agreements and Agreement Receipts lists sort by any column on the server (UI-SORT-004). Agreements open sorted by number, newest first. |

#### 3.17.8 Paying an Agreement

See §3.15.7 Agreement PAs.

---

### 3.18 Payment Execution, Remittance Advice & Vendor Credits

Payment of an approved PA is executed in Finance; EPMS PA Detail offers the same action. Vendor credits are netted at execution, and a remittance advice is emailed to the vendor afterwards.

#### 3.18.1 Executing a PA Payment

| FR ID | Requirement |
|---|---|
| **PAY-001** | An `approved` PA is paid with **Mark as Processed** on PA Detail (or in Finance → Payments / payment batches). Allowed for **Payment Officer**, **Finance Manager** and **Finance BP** (primary or additional role) and System Admin (primary role only). AP Clerk cannot execute payments. |
| **PAY-002** | The confirmation dialog requires the funding account (bank account or credit card, same currency as the PA) and shows Gross / Vendor credits applied / Net before anything is sent. |
| **PAY-003** | Segregation of duties: the PA's creator cannot execute its payment while the *self_payment* SoD rule is enabled. Payment is refused in a closed (soft or hard) fiscal period. |
| **PAY-004** | A PO-route, non-prepayment PA that links no invoice is refused at payment while its PO still carries a payable invoice that no live PA claims. |
| **PAY-005** | On execution: PA → `processed` (paid date recorded); the *process_pa* task closes; linked invoices and their Finance AP rows become `paid` (prepayment: `partially_paid`); a payment record and a GL payment posting (AP debit, bank credit, vendor-credit clearing credit) are created. |

#### 3.18.2 Vendor Credits

| FR ID | Requirement |
|---|---|
| **VC-001** | A vendor credit is money the vendor owes back. Sources: a **credit-note document** uploaded through Upload Invoice (§3.14.1, INV-003c), a **manual credit** (Vendor Credits → *Record Manual Credit*) when the vendor will not issue a credit note, or a QuickBooks import. Number `VC-YYYYMMDD-NNNN`; amounts are stored positive regardless of the document's sign. |
| **VC-002** | Duplicate guard: one live (non-void) credit per vendor + vendor credit-note number. |
| **VC-003** | A manual credit requires the reference number the vendor quoted (shown on the remittance), amount/tax, and a **basis** note (who at the vendor confirmed it, and when); the vendor's email (.msg/.eml/PDF) is attached as evidence and the UI will not approve a manual credit without an attachment. |
| **VC-004** | New credits are `pending_review`. Holders of *Manage Vendor Credits* (`epms.vendor_credit.manage`) approve (→ `available`) or reject with a reason (→ `void`); an `available` credit not yet applied can be voided. The Vendor Credits page (menu gated by `view_invoice`) lists credits with a detail drawer (fields, files, review actions, applications). |
| **VC-005** | At payment, available credits of the same vendor and currency are netted automatically, **oldest first**, up to the payment amount; the operator can untick individual credits in the dialog. A fully used credit becomes `exhausted`. |

#### 3.18.3 Remittance Advice

| FR ID | Requirement |
|---|---|
| **REM-001** | After payment, Finance sends a remittance advice email per payee, for a single payment, a payment batch, or a selection. Sending is open to AP Clerk as well as payment executors (it is not an act of paying). |
| **REM-002** | Recipient: the vendor's *Remittance email* (Vendor Master), falling back to the contact email; several addresses may be entered (comma or semicolon). Sender, CC, template and logo come from Portal → Admin → Remittance Advice settings. |
| **REM-003** | Each line shows the vendor's own invoice number (prepayment PAs with no invoice: "Prepayment - PO <number>"), payment date, and — when credits were netted — gross, each credit note number with its amount, and net. |
| **REM-004** | A payee is blocked from sending, with the reason shown, when it has no email, an invalid email, or a line with no vendor reference. Preview is live and refreshable; a **render** step shows the exact emails before sending. |
| **REM-005** | **Payment date picker** (default today, future dates refused): overrides the payment date printed on the advice only; the payment record's accounting date is never changed. |
| **REM-006** | An advice already sent to a payee for the same scope is skipped unless that payee is explicitly marked for resend. |
### 3.19 AI Assistant

Every EPMS page carries an **Assistant** panel (floating button, available to any signed-in user). It answers questions about the user's data, about why a document cannot be submitted, about what the user owes, and about how a process works. Its "how does X work" answers come from the guide layer (§2.1): module and document-kind descriptions, and processes generated at question time from the live approval-workflow configuration.

| FR ID | Requirement |
|---|---|
| **AST-001** | Every signed-in EPMS user has an Assistant panel on every page. It receives the current page context (module, route, and the PR/PO/GR/Invoice/PA open on screen). For an on-screen PR, PO or PA the server adds the document's **number** (only if the user may see it), so "who is the vendor on this one?" is answered from the document's data, not from its approval history. |
| **AST-002** | Data answers come only from rows the user may see: the entity must pass the user's Access Control Matrix view permission, and the user's document row scope is always applied server-side. The model never writes SQL; it picks entities/fields/operators from a whitelisted ontology (EPMS documents plus budget, finance, master-data, MRP and settings entities), and anything not in the ontology is rejected. Queries run read-only with a short timeout. |
| **AST-003** | Each answer shows the query that produced it. Lists are capped at 100 rows (200 for grouped results) and the answer says when it was cut. |
| **AST-004** | "Why can't I submit this document?" is answered by running the same submit checks the Submit button runs (§3.3.9). |
| **AST-005** | "What should I do?" lists the user's open Task Inbox items (same rule as §3.7.1). "How does <document> approval work?" is answered from the chain the approval engine actually runs (its configured definition, else the engine's own default) for PR, PO, PO sign-off, Purchase Agreement, Payment Application (incl. Direct PA), Expense / Mileage / Travel Expense claims, Travel Application, Budget Plan and Visitor Request. For a PR it also states the **current Over-Budget Approval Mode** and the steps it adds in front of the normal chain (or that submission is blocked), and that the mode is fixed when a PR is submitted (OBG-010). |
| **AST-006** | An answer can be exported to Excel (up to 50 000 rows). |
| **AST-007** | A user can mark up to 10 questions to re-ask with one click. |
| **AST-008** | The assistant uses the Claude Haiku model only; usage (tokens) is logged per call. |
| **AST-009** | **Reply language.** Every reply is in the language of the user's *current* question, whatever earlier turns used. The server decides it from the question (Chinese script → Chinese; an explicit "answer in English/中文/español" → that language) and tells every answering step; for other Latin-script questions the model is told to use the question's language. A message that only asks for another language ("用中文回答") repeats the previous answer in that language. System-written replies (refusals, "which document do you mean?") are rendered in the user's language too. |
| **AST-010** | **Fixed Chinese terms.** In Chinese replies, role, approval-level and document names use one fixed translation each, followed by the English name the first time (e.g. 总监 (Director), 主管 (Supervisor), 应付审核 (AP Review), 里程报销单 (MIL)). Names without a fixed translation stay in English. |
| **AST-011** | **Agreements.** Purchase Agreements are queryable (needs `epms.agreement.read`; same row scope as the Agreements page): number, kind, vendor, status, valid from/to, days until expiry, grace days, Not to Exceed, consumed and remaining. An empty Not to Exceed means *no ceiling*. Status `active` means approved, not "still in force" — the system never moves an agreement to `expired`. |
| **AST-012** | **Rules.** Questions about how the system behaves are answered from fixed rules, each tied to the code that enforces it, never from a data query or a guess. They cover: a rejected PA gets status Cancelled (PRs and POs get Rejected); Type 1 cannot be chosen when creating a PR; the number formats (PR/PA/GR-YYYYMMDD-NNNN, PO-<vendor code>-YYMM-NN, AGR-YYYYMM-NNNN, invoice reference INV-YYYY-NNNN; the date is the UTC date); invoice matching compares amounts per PO line (over-invoicing beyond tolerance → Exception) and never received quantities, so an invoice can be Matched with nothing received (INV-3WM-006) — receipt evidence is checked only when a PA is created (PA-013), and only for existence; an Active agreement is not necessarily in force; approvals are given only in UniOps (Teams cards and emails only notify). |
| **AST-013** | **Task counts.** "What do I have to do?" reports the total, the urgent count, the count per task type under the same labels as the Task Inbox group headers, and how many tasks reached the user personally vs through a role. All of these are counted by the server; the model does not count. Only the first 12 tasks (urgent first, then oldest) are named as examples. |
| **AST-014** | PR answers can include the Service/Project expected completion date, the Service/Project Owner (the owner set on a service or project PR, otherwise the requester, as for receipt tasks) and the requester's name. Replies never show internal tool markup. |
| **AST-015** | **Long lists.** When a list answer hits the row cap, the server also counts how many rows the query really matched. The answer states that total, lists at most 10 rows, and points to **Export to Excel** (named exactly so in every language) for the full list; the evidence line reads "N of TOTAL rows". |
| **AST-016** | **Invoices and receipt.** Invoices carry which route they are billed against (purchase order / agreement) and, for the PO route only, whether the PO has receipt evidence under the same rule as the PA receipt gate (a live GR, or a received quantity on a PO line). "Matched but not received" therefore never includes agreement invoices, which are backed by agreement receipts; an empty GR number is not read as "not received". |
| **AST-017** | **Formatting.** Replies are rendered as formatted text — bold, italics, inline code, headings, bullet and numbered lists, and tables — by a renderer that never interprets HTML in the reply, so quoted text (vendor names, comments) cannot inject markup. Error messages stay plain text. |

---

### 3.20 UI Conventions

**Tabbed workspace.** Every EPMS page opens in a keep-alive tab; the Dashboard tab is pinned. List pages are one tab each; each document's detail/edit page gets its own tab, titled with the document number. URL query strings are carried into the tab.

**Embedded mode.** When EPMS runs inside another UniOps module (for example the Budget pages under Portal → Finance), it renders the page content only. Cross-app links hand the session over so the user is not asked to sign in again.

**Other conventions.** Calendar dates (no time) are treated as local dates and never shift by a day; dates display as DD/MM/YYYY and date-times as DD/MM/YYYY HH:mm. Notes keep their line breaks on detail pages. Dropdowns render in a portal so they are not clipped by scrolling containers. UI text is English only.

| FR ID | Requirement |
|---|---|
| **UI-TAB-001** | Pages open as tabs; Dashboard is pinned. A list or create page has one tab; each document's detail/edit page has its own tab titled with the document number. |
| **UI-TAB-002** | Completing or cancelling a create/edit/confirm form replaces its tab with the resulting page; Back links close the current tab. |
| **UI-TAB-003** | Open tabs last for the browser session in EPMS only; a reload, leaving EPMS (Back to Portal), or a different user signing in starts from the pinned tabs. |
| **UI-EMB-001** | When EPMS is embedded in another UniOps module, it shows only the page content (no sidebar, header or tabs). |
| **UI-DATE-001** | Calendar dates (no time) display as the same calendar day in every time zone; display format DD/MM/YYYY. |
| **UI-BTN-001** | Action buttons that change document state (Approve, Return, Reject, Withdraw and similar) are disabled while their request is pending. |

**Sortable list columns (all UniOps modules).** Every list of records in every UniOps app (EPMS, OA, Finance, Inventory, MRP, Portal, VMS, Booking) can be sorted by clicking a column header. This rule is specified once here; the other PRDs refer to it.

| FR ID | Requirement |
|---|---|
| **UI-SORT-001** | Each data column header of a list sorts the list. The first click sorts ascending; the next click on the same header reverses it; clicking another header starts that column ascending. A sortable header shows ↕ until it is the active sort, then ↑ or ↓. Checkbox, action-button and expand columns do not sort. |
| **UI-SORT-002** | A column sorts by **what its cell shows**: a status or type by its label, a person or vendor by name (never by an internal id), an amount as a number, a date chronologically. Text sorts case-insensitively; document numbers sort in number-aware order where the list is loaded whole (508027 before 2026000066). Status columns sort alphabetically by label, except where a module specifies another order (EPMS PR/PO/PA, §3.2). |
| **UI-SORT-003** | Rows with no value in the sorted column stay at the bottom in **both** directions. Rows with equal values keep a stable order. |
| **UI-SORT-004** | A list that is paged by the server is **sorted by the server across all pages**, never by re-ordering the rows on the current page. Changing the sort goes back to page 1 (PGN-003). Every such list endpoint takes `sort` (a column key from a fixed allow-list; an unknown key is refused) and `dir` (`asc` / `desc`); without `sort` it keeps its own default order. |
| **UI-SORT-005** | A list loaded whole is sorted in the browser over all its rows. Lists that used to load only the newest N rows now page on the server instead, so sorting always covers every matching row. |
| **UI-SORT-006** | A list opens in its default order (stated in its module's section); where that default is a column sort, the header shows the active arrow. Sorting does not change filters, search or selection. |
| **UI-SORT-007** | Not sortable by design: line items inside a document's detail or edit form; reports and matrices with subtotal, group or total rows inside the table; and tables whose values depend on row order (running balance, cumulative totals, opening/closing roll-forwards). |

---

### 3.21 Courier Freight Payment (Freight)

Courier charges (UPS, FedEx, Purolator…) are paid without PR or PO. Buyers register the tracking number of each pickup with the cost center that bears it; when the carrier's invoice arrives, its per-shipment detail is read by AI, matched to the registered tracking numbers, and paid by a **Freight PA** split by cost center. All freight is charged to budget account **CRM0150102**. Design: `docs/superpowers/specs/2026-10-19-freight-payment-design.md`; page layout: `docs/superpowers/specs/2026-10-19-freight-into-invoice-pa-design.md`.

**Where it happens.** The Freight page holds only what is Freight's own — **Shipments** and **Carriers**. Courier invoices arrive and are matched on the **Invoices** page like every other invoice, and Freight PAs are raised on the **PA** page (FRT-014).

#### 3.21.1 Access

| Permission | Allows | Seeded |
|---|---|---|
| `epms.freight.register` | Register, edit and void one's own shipments; see one's own shipments | System Admin, Procurement Officer, Procurement Manager |
| `epms.freight.manage` | See every shipment; upload, match, finalise, reopen and delete carrier invoices; raise Freight PAs; see courier invoices on the Invoices page when otherwise PO-restricted. (Uploading a courier invoice is also open to the ordinary invoice-upload permission, FRT-016.) | System Admin (the supply-chain person is granted it in Access Control) |
| `epms.freight.admin` | Maintain the carrier list | System Admin |

#### 3.21.2 Requirements

| FR ID | Requirement |
|---|---|
| **FRT-001** | **Carriers.** A carrier links one vendor (unique) and has a code, a name, an optional tracking-number pattern (regular expression) and an optional default cost center for charges that have no tracking number. Only vendors set up as carriers can be paid by a Freight PA. |
| **FRT-002** | **Register a shipment.** Carrier, tracking number, cost center (required), direction (outbound / inbound), note, optional waybill photo. The tracking number is stored without spaces, hyphens or dots, upper case; it must match the carrier's pattern when one is set; a carrier + tracking number can be registered once (the refusal names who registered it). A waybill photo can be read by AI to fill the tracking number. |
| **FRT-003** | **Default cost center.** After a department is chosen: its only active cost center; otherwise the cost center whose current-year approved budget plan carries CRM0150102 (largest annual amount); otherwise none and the user picks. Choosing a cost center with no freight budget asks for confirmation; it is not blocked. |
| **FRT-004** | A shipment is `pending` until an invoice bills it, then `billed`. Only a pending shipment can be edited or voided (`void`). |
| **FRT-005** | **Upload a carrier invoice** (through *Invoices → Upload Invoice*, FRT-016). The PDF is read by AI (Haiku) for its per-shipment detail: header (invoice number, dates, currency, amount before tax for this invoice only, tax) and one line per shipment / pickup / fee / adjustment with every package tracking number, reference, date, amount before tax and the tax attributed to it. Tax printed per category is spread over that category's lines; any tax still unattributed is spread over the positive lines by amount. The reviewer corrects the result before saving; the form shows whether the lines add up to the header. Saving is refused when the same vendor invoice number already exists in EPMS or OA. The invoice is stored as an EPMS invoice (`match_route = freight`) and synced to Finance AP like any other. |
| **FRT-006** | **Auto-match** (on save and on demand; never changes a line set by hand): a shipment line matches the carrier's pending shipment whose tracking number is any of the line's package numbers → its cost center. A shipment already billed on another invoice is flagged **Billed before** and needs a decision. A line with no registered shipment is **Not registered** and needs a cost center. An adjustment follows its shipment wherever it was billed. A pickup on (or one day either side of) a matched shipment's date follows that shipment; other pickups and fees take the carrier's default cost center. |
| **FRT-007** | On an unfinalised invoice each line's cost center, amount, tax, tracking numbers and the shipment a pickup follows can be changed; a change marks the line as set by hand. |
| **FRT-008** | **Finalise** requires: lines before tax = invoice amount, line taxes = invoice tax, every line has a cost center, no undecided **Billed before** line. Finalising records unregistered shipments (source *from invoice*), marks matched shipments billed and the invoice `matched`. **Reopen** returns it to unfinalised (and its shipments to pending) only while no Freight PA holds it. An unfinalised invoice can be deleted. |
| **FRT-009** | A courier invoice is matched against shipments, never a PO or agreement: the general invoice actions (match to PO or agreement, assign, unmatch, edit, delete, exceptions) refuse it, and the lists offer **Match shipments** (to its invoice page) instead. Its invoice page shows the charges and their matching in place of the three-way match. |
| **FRT-010** | **Freight PA.** One or more finalised invoices of the same carrier and currency, none held by another non-cancelled PA, are locked and turned into one PA: `source = freight`, no PO, no agreement, subtotal and tax = the invoices' amounts, one line item per invoice, and a cost allocation per invoice × cost center (department, CRM0150102, amount, tax) snapshotted at creation. PA detail and PDF show the cost allocation instead of PO numbers. |
| **FRT-011** | **Approval.** A Freight PA is a `pa` document (same tasks, inbox, events and payment) that walks the `pa_frt` chain — default OPM → GM → Finance Manager → Finance BP, edited in Portal → Admin → Approval Workflows, where only company-level roles are offered. No department manager is involved or notified. |
| **FRT-012** | **Visibility.** Besides unrestricted roles, a Freight PA is visible to its creator, `epms.freight.manage` holders and anyone holding a role that is a step of the `pa_frt` chain (GM and OPM are restricted roles whose approval tasks are not personally assigned). It appears in the PA list; the Department filter matches any of its allocations. |
| **FRT-014** | **Pages.** *Invoices → Upload Invoice* is the one upload form for every invoice (FRT-016). The invoice lists mark courier invoices **Freight** and *All Invoices* filters by Route (PO / Agreement / Freight; PO includes invoices with no recorded route). *Payment Applications* offers **New Freight PA** to `epms.freight.manage` holders (`/pa/new?source=freight`; *Create PA* on a matched courier invoice opens it with that invoice ticked). The Freight page has the Shipments and Carriers tabs only; an old `/freight/invoices/<id>` link opens the invoice page. |
| **FRT-015** | **Courier invoice visibility.** Unrestricted roles see every invoice. A PO-restricted caller sees courier invoices (list, detail, document chain) when holding `epms.freight.manage`; no role code is hard-wired — anyone else (e.g. the `pa_frt` approvers) is granted through the Access Control Matrix. Every invoice reader sees the matching panel; only `epms.freight.manage` changes it. |
| **FRT-016** | **A carrier's invoice is a courier invoice.** The upload form reads the PDF as any invoice; when its vendor is an active carrier's — picked by the user, or the vendor name read by AI agreeing with the carrier's vendor name or the carrier's own name (equal once legal suffixes and punctuation are dropped, or one starting with the other over at least 4 characters — an invoice printing just "UPS" is recognised, "Upstate Supplies" is not) — the form on the right of the same Upload Invoice window becomes the courier review (the PDF preview stays on the left; the lines are listed one card per shipment, pickup or fee, with the window's own Save and match): the same PDF is read again for its shipment lines, the carrier is shown (no carrier picker), and *Different vendor* goes back. Credit notes keep the vendor-credit path. Uploading takes the ordinary invoice-upload permission (or `epms.freight.manage`); the PO invoice upload (`POST /invoices`) refuses an active carrier's vendor. |
| **FRT-017** | **Assigning the matching.** As with any invoice, AP assigns a courier invoice from the Unmatched Queue (*Assign* / *Reassign*, "Assign Shipment Matching"); the assignee gets a *Match Invoice* task ("Match courier invoice … to shipments") that opens the invoice page, and may — without `epms.freight.manage` — edit the lines, re-run auto-match and finalise. The open task also lets them open the invoice page when their role has no invoice view. Finalising (or deleting) closes the task. Reopen, delete and the Freight PA stay with `epms.freight.manage`. |
| **FRT-013** | **Payment and NC.** A Freight PA is paid through the standard payment executor as an EPMS PA (its invoices are marked paid); the due list labels it Freight PA. In the NC AP export a courier invoice is **one body row per cost center** with revexp CRM0150102 (all other invoices stay one summary row); a row with no tax is sent with tax rate 0. |

### 3.22 Direct Payment Application (Direct PA)

Some vendor invoices have no purchase order or agreement behind them — and never will (a one-off service, a fee, a bill paid on the department's say-so). They are paid by a **Direct PA**. The feature moved here from OA, where it was retired on 2026-08-07: the invoice is uploaded on the Invoices page like any other, and the Direct PA is raised on the Payment Applications page. There is no 3-way match; the payment names what it is charged to itself.

#### 3.22.1 Requirements

| FR ID | Requirement |
|---|---|
| **DPA-001** | **Match → Direct PA.** The invoice Match panel has a third tab, **Direct PA**, beside Purchase Orders and Agreements. *Match for Direct PA* (or *Match & Create Direct PA*, which then opens the create page with the invoice ticked) sets the invoice `matched` with `match_route = direct`, no PO, no agreement, no variance. Allowed to whoever may match the invoice (AP, its uploader, the holder of an open match task); only an `unmatched` invoice with no PO, agreement or allocation can take it; a courier invoice cannot. Open match / exception tasks on the invoice close; the AP mirror is posted as for any matched invoice. |
| **DPA-002** | **Unmatch.** On the invoice's 3-Way Match tab AP (`epms.invoice.match`) can take it back to `unmatched` with a reason (audited), unless a non-cancelled PA lists it or it is already in payment. While a live PA lists a Direct invoice, the invoice cannot be edited either — the PA copied its amounts. |
| **DPA-003** | **Create Direct PA.** *Payment Applications → Create Direct PA* (`/pa/new?source=direct`; also *Create Direct PA* on a Direct invoice) is shown to holders of *Create / Edit PAs* (`epms.pa.write`). It lists the Direct invoices the caller can open on the Invoices page that no live PA lists (oldest due first). One or more of **the same vendor and currency** go on one PA, falling due within 7 days of each other (as for PO PAs). The caller must be able to open every invoice on it (a requester can pay what they uploaded; AP anything). |
| **DPA-004** | **Charged to.** Required: **Department** (defaults to the creator's own), **Cost Center** (an active cost center of that department), **Budget Account** (L1 → L2 from the budget catalog, checked against it), and the **Reason for payment** (stored as the PA's notes). Title is optional (default "Direct Payment — vendor + invoice numbers"). |
| **DPA-005** | **The PA.** `source = direct`, `pa_type = regular`, no PO, no agreement; subtotal and tax are the invoices' own amounts (no rate re-applied); one line item per invoice. Created as a draft. On a draft or returned Direct PA only the title, reason, department, cost center and budget account can be edited; amounts and invoices follow the invoices (to change them: cancel, fix the invoice, raise a new PA). Submit (from the PA page or *Save & Submit*) is refused unless invoice, department, cost center, budget account and reason are all present. PA detail and PDF show *Charged To* (department · cost center · budget account) in place of PO numbers. |
| **DPA-006** | **Approval.** A Direct PA is a `pa` document (same tasks, inbox, events, payment) that walks the `pa_dir` chain — the "PA (Direct)" workflow in Portal → Admin → Approval Workflows, default Finance BP → Finance Manager. Department-based steps an admin adds there (Department Manager, Director, GM / OPM) route on the department named on the PA. |
| **DPA-007** | **Visibility.** Unrestricted roles see every Direct PA. A restricted caller sees one they created, one they hold an open task on, one charged to a department they oversee (Department Manager / Dept Admin: own department; GM / OPM: mapped departments; Director: directed departments) and — as Supervisor — one raised by a direct report. They can also open the invoices on a Direct PA they can see. The PA list's Department filter matches its department; Direct PAs carry a **Direct** badge with cost center and budget account. |
| **DPA-008** | **Payment and NC.** Paid through the standard payment executor as an EPMS PA (its invoices are marked paid); the Payment Batches due list labels it Direct PA. In the NC AP export a Direct invoice takes department, cost center, budget account (revexp) and employee from the Direct PA paying it; one with no live Direct PA yet is reported, not exported. |
| **DPA-009** | **Pages.** The invoice lists mark Direct invoices **Direct** and *All Invoices* filters Route = Direct PA. The invoice's document chain reads a Direct invoice as matched with no goods receipt to wait for. |

---

## 4. Non-Functional Requirements

### 4.1 Accessibility
- WCAG 2.1 AA target (a full accessibility pass has not yet been done — see §11)
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
- Background jobs (task notifier, daily digest, agreement overdue check, service-GR due-date reminder, NC sync) and fire-and-forget sends are tracked and drained on shutdown. They assume a **single epms-api instance** (see §11).

### 4.4 System NFRs

| Category | Requirement |
|----------|-------------|
| **Performance** | Targets (not currently measured or enforced): page load < 2 s (P95); approval routing < 5 s per step; OCR/AI invoice extraction < 30 s (P95); 500 concurrent users. |
| **Availability** | 99.9% uptime SLA. Planned maintenance windows require 48-hour advance notice. |
| **Security** | RBAC enforced server-side on API endpoints (known exceptions listed in §11). MFA by emailed one-time code at sign-in when enabled company-wide or by the user (§3.1); approval actions do not ask for MFA again. Data encrypted at rest (AES-256) and in transit (TLS 1.3). |
| **Data Sovereignty** | Production is self-hosted on company servers (not a public cloud). No data leaves the company's infrastructure in Canada. |
| **Audit** | Every document keeps its approval history (actions, approvers, comments). Corrections made through Data Maintenance (§3.6.8) are written to an admin audit log. A general audit log for admin configuration changes and vendor record edits is **not implemented** — see §11. |
| **Configurability** | Workflow definitions, approval routing, role permissions (Access Control Matrix), budget config and notification policy are changeable in Portal / EPMS admin screens without code deployment or server restart (the daily digest time takes effect within 15 minutes). |
| **Record Retention** | All procurement records (PR, PO, GR, Invoice, PA, approval history) retained for a minimum of 7 years. Note: Data Maintenance can delete records; deletion within the retention window is a matter of admin policy, not a system block. |
| **Localisation** | English UI. Currency display: CAD default; multi-currency supported. Dates displayed DD/MM/YYYY (§3.20); stored in ISO 8601. |
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
- Dates: displayed DD/MM/YYYY (date-times DD/MM/YYYY HH:mm); backend stores ISO format (§3.20)

---

## 6. Navigation Structure

Each item is shown only to users who hold the permission noted next to it.

```
MY WORKSPACE
  Dashboard
  Task Inbox [badge — open tasks, refreshed every 60 s]
  Reports

PROCUREMENT
  Purchase Requisitions             view_pr
  Purchase Orders                   view_po
  Goods Receipt                     view_gr   (→ Receiving Report button on the list)
  Invoices                          view_invoice
  Vendor Credits                    view_invoice
  Payment Applications              view_pa
  Freight                           epms.freight.register or epms.freight.manage
  Agreements                        epms.agreement.read
  Agreement Receipts                epms.agreement.read

MASTER DATA
  Vendors                           vendor_master
  Projects                          Procurement Officer / Procurement Manager / System Admin
  Parts Catalog                     parts_catalog

ADMIN
  Admin Panel                       admin_panel

— Back to Portal

Header: user menu → My Profile, Sign out · Assistant (floating button, all pages)
```

The former FINANCE section has been removed from the EPMS sidebar: Budget Dashboard, Budget Plans and the Account Catalog are reached from **Portal → Finance**, which opens the EPMS pages in embedded mode (§3.20). Global administration (users, roles and Access Control Matrix, approval workflows and routing, approval delegation, notification settings, Data Maintenance, NC sync) is in **Portal → Admin**.

---

## 7. Module Status

> Legend: ✅ Done &nbsp;|&nbsp; 🟡 Partial &nbsp;|&nbsp; 🔲 Not started &nbsp;|&nbsp; — Not applicable

| Module | Frontend | Backend | Notes |
|--------|----------|---------|-------|
| Sign-in via Portal, email MFA, password rotation | ✅ | ✅ | §3.1; idle timeout 60 min |
| User Profile (channel preference, MFA switch, password, My Signature) | ✅ | ✅ | §3.1.1 |
| Purchase Requisitions (create / edit / recall / approve, Department, Service/Project Owner) | ✅ | ✅ | CRUD, approval workflow, history |
| PR PDF auto-generation on approval | ✅ | ✅ | Uses PDF Template settings; attached to the PR |
| Purchase Orders, Order Placement (Email Composer / Online Portal) | ✅ | ✅ | `place_order` task created on approval |
| NC65 PO / arrival mirror | ✅ | ✅ | Hourly incremental (configurable), Sync NC button, Portal → Admin → NC Sync panel, error tasks. §3.12.6 |
| NC PO Edit Details | ✅ | ✅ | §3.12.7 |
| PO Sign-off (two-level electronic signature) | ✅ | ✅ | §3.12.8 |
| Goods Receipt — Physical | ✅ | ✅ | No acknowledgement SLA reminder/escalation scheduler (§11) |
| Goods Receipt — Service | ✅ | ✅ | |
| Service GR completion-date reminder | ✅ | ✅ | Ships disabled + dry-run. §3.13.3 |
| Collection Confirmation | ✅ | ✅ | Requester collect step only; Collection Config / discrepancy workflow not built (§3.13.4, §11) |
| Receiving Report | ✅ | ✅ | §3.13.5 |
| Invoices — upload, AI parsing, PDF split, matching (multi-PO), exceptions | ✅ | ✅ | §3.14 |
| Vendor Credits | ✅ | ✅ | §3.18 |
| Payment Applications incl. multi-PO, prepayment & settlement | ✅ | ✅ | §3.15 |
| Payment execution (Payment Officer) & Remittance Advice | ✅ | ✅ | §3.18 |
| Purchase Agreements + Agreement Receipts + Agreement PAs | ✅ | ✅ | §3.17, §3.15.7 |
| Document PDFs (PR/PO/GR/PA) + Regenerate | ✅ | ✅ | §3.4; server-side, apply PDF Template settings (logo, header/footer note, T&C); stored on File Server |
| Attachments (File Server, edit after create, PA chain bundle) | ✅ | ✅ | §3.4.1 |
| EPMS Admin Panel — PDF Templates (all 4 doc types) | ✅ | ✅ | |
| EPMS Admin Panel — Email Settings | ✅ | ✅ | PO-to-vendor SMTP profile + PO email template. Internal task notification SMTP is in Portal → Admin → Notification Settings |
| EPMS Admin Panel — procurement settings (Vendor Settings, Service GR SLA, GR Notification SLA, Prepayment Config, Collection Config, PMS Data Import) | ✅ | ✅ | Global sections show a "moved to Portal" link (§3.6) |
| Departments + Cost Centers (mdm-api) | ✅ | ✅ | Source of truth in mdm-api. Departments managed in Portal → Admin; Cost Centers in Portal → Finance → Budget Config. EPMS keeps read-only fallback for legacy pickers |
| Approval workflow engine, routing, delegation | ✅ (Portal) | ✅ | approval-api; §3.3, §1.5 |
| Roles & Access Control Matrix | ✅ (Portal) | ✅ | identity-api; EPMS Role Management tab retired |
| Budget Dashboard / Budget Plans / Budget Config | ✅ (Portal Finance) | ✅ | Plan vs NC actual; §3.8, §9 |
| Task Inbox (self-healing, grouped, no manual dismiss) | ✅ | ✅ | §3.7.1 |
| Notification outbox + per-type policy + daily digest | ✅ (Portal) | ✅ | §3.9 |
| Notification email template editor | 🔲 | ✅ | Templates are server defaults; no reachable editor (§11) |
| Send reminder (PR / PO / OA claim) | ✅ | ✅ | PA and Agreement timelines have none |
| Microsoft Teams notifications | ✅ | 🟡 | Outgoing webhook card with a link only; no in-card approval |
| Email reply-to-approve | 🔲 | 🔲 | §11 |
| Role dashboards (13 kinds) | ✅ | ✅ | System Health card is static; Overdue Tasks always 0 (no task due dates) |
| Data Maintenance | ✅ (Portal) | ✅ | §3.6.8 |
| AI Assistant | ✅ | ✅ | §3.19 |
| Vendors Master Data | ✅ | ✅ | With CSV import/export |
| Projects Master Data | ✅ | ✅ | |
| Parts Catalog | ✅ | ✅ | With CSV import/export |
| Reports | ✅ | ✅ | 7 report types (PR/PO/GR/Invoice/PA/Budget/Vendor) with filters and export |
| Legacy PMS Import (SharePoint) | ✅ | ✅ | Admin Panel → PMS Data Import. Full + incremental sync. See §3.16 (FR-PMS-001~008) |
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
| 11 | Role-specific dashboards, Vendors, Projects, Task Inbox | ✅ Done |
| 12 | Report Centre | ✅ Done |
| 13 | PO Order Placement, User Profile page | ✅ Done |
| 14 | Role Management UI, Access Control Matrix Editor | ✅ Done (in Portal → Admin) |
| 15 | Notification Settings UI | ✅ Done (in Portal → Admin) |
| 16 | WCAG 2.1 AA pass | 🔲 |

### Phase 2 — Backend Development (Complete except where noted)

| Sprint | Deliverables | Status |
|--------|-------------|--------|
| B1 | FastAPI scaffold, DB schema, Alembic migrations, health check | ✅ Done |
| B2 | Auth API: login, JWT, MFA, password hashing, RBAC middleware | ✅ Done (MFA is an emailed one-time code, §3.1) |
| B3 | User, Department, Company Config APIs | ✅ Done |
| B4 | PR API: CRUD, configurable approval workflow engine, approval history, PR PDF generation | ✅ Done |
| B5 | PO API: CRUD, approval workflow, approval history | ✅ Done |
| B5a | PO Order Placement API; Email Composer (pre-filled from template + vendor email); Online confirmation + portal reference | ✅ Done |
| B6 | GR API: physical/service GR, collection confirmation, acknowledgement | ✅ Done |
| B7 | Invoice API: upload, AI parsing, three-way matching | ✅ Done |
| B8 | PA API: CRUD, prepayment & settlement workflow | ✅ Done |
| B9 | Budget API: L1/account CRUD, CSV import/export, dashboard aggregation | ✅ Done |
| B10 | Task engine: auto-generate tasks on state changes | ✅ Done |
| B11 | Notification service: outbox notifier, per-type policy, daily digest; Email (SMTP) + Teams (link-only card); retry logic; User Profile API (channel preference); Notification Settings API | ✅ Done (Teams in-card approval not built) |
| B11a | Role Management API, Access Control Matrix editor, department routing API | ✅ Done (identity-api / approval-api; fixed built-in roles, no custom roles) |
| B12 | Reports API: PR/PO/GR/Invoice/PA/Budget/Vendor report endpoints with filters and CSV export | ✅ Done |
| B13 | PO / GR / PA PDF generation (server-side, template-driven) | ✅ Done |
| B14 | Security hardening: rate limiting, CORS, secrets management | 🔲 |
| B15 | Production deployment: self-hosted company servers, HTTPS | ✅ Done (monitoring / load testing not done) |

### Phase 3 — Production hardening & extensions (2026-06-25 → 2026-10-15)

| Period | Delivered |
|---|---|
| Jun–Jul | Tab workspace + shared shell; password expiry / initial password; PMS import v2 (approval reconstruction, invoice rules, attachments); roles/permissions moved to identity, routing to approval-api (Portal admin); task backfills; Data Maintenance; PA chain attachments; role shared mailboxes |
| Aug | NC purchase sync; Payment Officer; invoice match review / exception tasks; Mark Done removed; date-only fix; Approval Delegation; automatic re-sync on approver change; PO sign-off + My Signature; PDF approval chains; Agreements & receipts; Vendor Credits; service/project completion-date reminder |
| Sep | Multi-PO PA; Send reminder; task liveness rules; AI Assistant + guide layer across modules; Service/Project Owner; invoice PDF split; HTML-escaped emails; Finance Director |
| Oct | Notification outbox + per-type policy + daily digest; PA invoice due-date window; generic task email template; over-budget mode `allow` |

### Next

| Item | Status |
|---|---|
| Notification template editor (Portal) | 🔲 |
| Task due dates / SLA colouring | 🔲 |
| Teams in-card approval / email reply-to-approve | 🔲 (decide if still wanted) |
| WCAG 2.1 AA pass | 🔲 |
| Remove FR rows for unbuilt dashboard panels or build them | 🔲 |
| Remaining items in §11 | 🔲 |

---

## 9. Budget Tracking — Plan vs Actual

Budget tracking operates at the L2 budget account level per cost center. All monetary amounts are in the fiscal year's configured currency (default CAD). Budget figures come from approved **Budget Plans** (Portal → Finance) and **NC general-ledger actuals**; the PR/PO/PA chain does not write to the budget (decision 2026-09-20).

| Metric | Definition |
|---|---|
| **Annual Budget** | Sum of the current approved Budget Plan lines for the cost center × account × fiscal year |
| **Committed** | Not tracked today — nothing in the PR/PO/PA chain books commitments, so it is 0 (see §11) |
| **Actual Spent** | NC general-ledger actual for the cost center × account × year: posted vouchers only, or also vouchers entered in NC but not yet tallied (Budget Dashboard toggle *Include unposted vouchers*, ticked by default; the PR over-budget check always includes them). Discarded/deleted vouchers never count. |
| **Available Balance** | Annual Budget − Committed − Actual Spent |
| **Projected After PR** | Available Balance − current PR total; displayed in the Budget Balance Widget on the PR Create/Edit form |

A PR is over budget when its total exceeds the Available Balance; the over-budget modes (Finance Manager + GM/OPM, Finance Manager only, Allow without extra approval, Hard block) are described in §3.2.6. The Budget Dashboard (§3.8) excludes Payroll, Depreciation and shut-down loss accounts.

**Alert thresholds (configurable in Portal → Finance → Budget Config):** used as display colours on the Budget Dashboard and its per-account alert chips. No budget alert emails or digests are sent.

| Status | Default Threshold | Colour |
|--------|------------------|--------|
| On Track | Utilisation < 80% | 🟢 Green |
| Warning | 80% ≤ Utilisation < 100% | 🟡 Amber |
| Over Budget | Utilisation ≥ 100% | 🔴 Red |

> **Type 1 exemption:** Raw Materials / Packaging PRs are fully exempt from budget tracking. No budget account code is required, and Type 1 documents are not checked against the budget.

**Budget Balance Widget — scoping rule:** The Budget Balance Widget on the PR Create / Edit form resolves the displayed balance by looking up the selected account code within the **selected Cost Center + L1 group scope**, for the current year. The same account code may exist under multiple cost centers with different budget amounts; the widget always shows the balance for the specific cost center and L1 the user has selected, not the first matching code found globally.

---

## 10. List Pagination

All major document list pages use **server-side pagination**. The backend applies `OFFSET`/`LIMIT` before returning results; the frontend never loads an entire table to paginate in memory. Exception: the Task Inbox (returns all open tasks) is not paginated.

### 10.1 Paginated Endpoints — EPMS

| List page | Backend endpoint | Default `page_size` | Max `page_size` |
|-----------|-----------------|--------------------:|----------------:|
| Purchase Requisitions | `GET /api/v1/pr` | 50 (UI requests 20) | 200 |
| Purchase Orders | `GET /api/v1/po` | 20 | 200 |
| Goods Receipts | `GET /api/v1/gr` | 20 | 200 |
| Receiving Report | `GET /api/v1/gr/receiving-report` | 50 | 500 |
| Payment Applications (EPMS) | `GET /api/v1/pa` | 20 | 200 |
| Invoices — All tab | `GET /api/v1/invoices` | 20 | 200 |
| Invoices — Unmatched tab | `GET /api/v1/invoices?status=unmatched` | 20 | 200 |
| Invoices — Exceptions tab | `GET /api/v1/invoices?status=exception` | 20 | 200 |
| Agreements | `GET /api/v1/agreements` | 20 | 200 |
| Agreement Receipts | `GET /api/v1/agreement-receipts` | 20 | 200 |
| Vendors | `GET /api/v1/vendors` | 20 (min 5) | 2000 |
| Parts Catalog | `GET /api/v1/parts` | 20 | 500 |
| Users (admin) | `GET /api/v1/users` | 20 | 200 |
| Users directory | `GET /api/v1/users/directory` | 20 | 100 |
| Data Maintenance lists | `GET /api/v1/admin/{entity}` | 20 | 200 |
| Vendor Credits | finance `GET /api/v1/vendor-credits` (`limit`/`offset`) | 20 (API default 100) | 500 |

Every paginated endpoint accepts `page` (integer ≥ 1, default 1) and `page_size` query parameters and returns:

```json
{ "items": [...], "total": <int> }
```

`total` is the full filtered count (before slicing), used by the frontend to compute page count and "X–Y of Z" display.

### 10.2 Backend Implementation Contract

- The query applies all filters first, then runs `SELECT COUNT(*)` over the filtered subquery to get `total`.
- `OFFSET` = `(page − 1) × page_size`. `LIMIT` = `page_size`.
- Access-scope filters (role-based visibility) are applied **before** the count query, so `total` reflects only records the user is allowed to see.
- Sorting (`sort`, `dir`, UI-SORT-004) is applied in SQL before `OFFSET`/`LIMIT`, with a unique tiebreak so a row never appears on two pages; empty values sort last in both directions. Without `sort`, each endpoint keeps its default order.

### 10.3 Pagination UI Component

The shared `Pagination` component from the UniOps shared shell package is used on list pages in EPMS, OA and other modules:

- **Per-page selector:** 10 / 20 / 50 / 100 items
- **Navigation:** First `«` · Previous `‹` · numbered pages (with `…` ellipsis for long ranges) · Next `›` · Last `»`
- **Count display:** "X–Y of Z" (shows "No results" when total = 0)
- **Reset on filter change:** any filter, tab, or search change resets `page` to 1

### 10.4 Pagination FR IDs

| FR ID | Requirement |
|-------|-------------|
| **PGN-001** | All document list pages must use server-side pagination. Loading the full dataset and paginating in the browser is not permitted. (The Task Inbox is currently unpaginated.) |
| **PGN-002** | Every paginated endpoint must return a `total` field (full filtered count) alongside `items`. |
| **PGN-003** | Changing any filter, status tab, search term, or sort order must reset `page` to 1. |
| **PGN-004** | The pagination UI must display "X–Y of Z" using the server-returned `total`. |
| **PGN-005** | Pagination state (`page`, `page_size`) must be included in the TanStack Query cache key so different pages are cached independently and refetch correctly. |

---

## 11. Remaining Open Items

Status values: **Open** (decision or data needed), **Not implemented** (requirement wanted but not built), **Defect** (behaviour differs from the stated rule), **Resolved**.

| ID | Item | Area | Status |
|----|------|------|--------|
| ~~OI-A~~ | ~~GM/OPM temporary delegate during absence~~ — replaced by Approval Delegation (Portal → Admin → Approval Delegation; the delegate sees and is mailed the delegator's approval tasks, which stay with the absent user) | Approval (§1.5) | **Resolved** |
| OI-B | Finance BP department scope — does Finance BP cover all departments or a scoped subset? | Roles / access (§1.3) | Open |
| OI-C | GL account code mapping for Prepaid Asset and expense/inventory accounts on PA records | Finance / PA | Open |
| OI-D | Microsoft Teams works as an outgoing webhook card with a "View in EPMS" link. In-card approval (needs a Teams bot + tenant approval) and email reply-to-approve are not built — decide whether still required | Notifications (§1.4, §3.9) | Not implemented |
| OI-E | Initial Budget Master data — Finance to prepare L1/L2 account codes and budget plans | Budget (§3.8) | Open |
| ~~OI-F~~ | ~~Over-budget approval mode~~ — four modes: Finance Manager + GM/OPM (default), Finance Manager only, Allow without extra approval (added 2026-10-15), Hard block; set in Portal → Finance → Budget Config | Budget (§3.2.6) | **Resolved** |
| OI-G | Value-threshold escalation (PRs above a configured amount escalate to GM/OPM) — settings are stored but nothing reads them; the merge-with-over-budget question is moot until it is built | PR approval (§3.2.6) | Not implemented |
| OI-H | Vendor POID — every vendor needs a unique POID before a PO is issued; NC vendors use the NC supplier code as POID; the former 3-character rule no longer applies | Vendor (§3.10) | Open (data) |
| ~~OI-I~~ | ~~ERP integration target~~ — ERP is Yonyou NC65. EPMS reads NC purchase orders and arrivals (§3.12.6); Finance reads vouchers (Finance PRD) | ERP | **Resolved** |
| OI-J | File Server in production (50 MB limit, file-type allow-list). Backup / DR of file storage still to be confirmed | Infrastructure (§3.4.1) | Open (backup/DR) |
| OI-K | Committed budget is not tracked; the over-budget check does not reserve budget for in-flight PRs/POs | Budget (§9) | Not implemented |
| OI-L | GM/OPM backup approvers in Approval Routing are stored but not used by routing or authorisation | Approval (§3.3.8) | Not implemented |
| OI-M | PR Withdraw button is shown for `in_review` PRs, but the engine refuses cancel after the first approval | PR (§3.2.4) | Defect |
| OI-N | `create_pr` permission exists in the matrix but is not enforced — any signed-in user can create a PR. Decide whether it should be enforceable | Roles / access (§1.3) | Open |
| OI-O | Notification email templates cannot be edited in any admin screen. Build an editor in Portal Notification Settings or retire the requirement | Notifications (§3.9) | Not implemented |
| OI-P | Task due dates — no task type sets a due date, so SLA colouring and the Overdue Tasks KPI are inert. Define per-type SLAs or drop the requirement | Task Inbox / Dashboard (§3.7) | Not implemented |
| OI-Q | Background jobs (notifier, digest, agreement overdue, service-GR due date, NC sync) would run twice if epms-api is scaled to more than one instance | Infrastructure (§4.3) | Open |
| OI-R | Notification types that are off by default (approve_pa, process_pa, agreement, OA claim, budget-plan and VMS tasks) send no email until an admin enables them — business to confirm desired defaults | Notifications (§3.9) | Open |
| OI-S | PDF Templates admin label says the PA PDF is generated "when PA is created"; it is generated on approval | PDF (§3.4) | Defect |
| OI-T | GR acknowledgement reminder / escalation ladder (GR-N-002, GR-N-003) — the SLA setting is stored but no scheduler reads it | GR (§3.13.2) | Not implemented |
| OI-U | Collection Config toggle, `ready_for_collection` status, collection SLA and discrepancy workflow (original Path C design) | GR (§3.13.4) | Not implemented |
| OI-V | PO-001 "a PR can have at most one non-cancelled PO" — no server-side check found | PO (§3.12.1) | Not implemented |
| OI-W | PT-009 manual close of a PO — no manual-close action exists | PO (§3.12.3) | Not implemented |
| OI-X | VND-005 vendor change audit log; general audit log for admin configuration changes | Vendor / Audit (§3.10, §4.4) | Not implemented |
| OI-Y | Prepayment SLA and balance-after-settlement checks (PP-006, PP-007, PP-010) | PA (§3.15.4) | Not implemented |
| OI-Z | Invoice match tolerance (default 5%) is not editable in any admin screen | Invoice (§3.14.3) | Not implemented |
| OI-AA | Changing an agreement's expected amount per period or tolerance after activation (e.g. via Data Maintenance) does not update billing-period rows already generated | Agreements (§3.17) | Defect |
| OI-AB | Agreement statuses `expired` and `closed` are never set; expiry is enforced only by the date check | Agreements (§3.17) | Not implemented |
| OI-AC | Dashboard items not built: System Health card is a static label; User Activity panel and Dashboard Config page do not exist | Dashboard (§3.7) | Not implemented |
| OI-AD | Server-side permission gaps: GR actions (acknowledge / collect / confirm / reject / cancel) and PO "Regenerate PDF" do not check the user's permission or document visibility | Security (§4.4) | Defect |
| OI-AE | WCAG 2.1 AA accessibility pass | UI (§4.1) | Not implemented |
| OI-AF | A PO-route PA can be submitted with line items that do not add up to its pre-tax amount (about a fifth of PO-route PAs created since 2026-07 in production); there is no lines-vs-subtotal check on submit | PA (§3.15.2) | Not implemented |
| OI-AG | When an invoice was matched only against the PO total, PA lines are still ticked by hand at full PO quantity; lines already paid by another PA are not marked, so the same PO line can appear in full on several PAs | PA (§3.15.2) | Not implemented |
| OI-AH | Freight carriers (UPS / FedEx / Purolator vendors, tracking patterns, default cost center) and the supply-chain person holding `epms.freight.manage` must be set up before first use | Freight (§3.21) | Open (data) |
| OI-AI | Freight: NC import of a courier invoice exported as several body rows (one per cost center, zero-tax rows at rate 0) to be tried by AP before the first live export | Freight (§3.21) | Open |
| OI-AJ | Freight phase 2: import the carriers' per-shipment CSV instead of reading the PDF; suggest the cost center from a PO number in the shipment reference; read invoices over ~10 pages page by page; monthly freight report by cost center | Freight (§3.21) | Not implemented |
| OI-AK | Freight: the waybill-photo reading has not yet been tried on a real label | Freight (§3.21) | Open |
