# UniOps Portal — Product Requirements Document

**Version:** 2.0
**Date:** 2026-10-16
**Status:** In Production
**Scope:** Portal web app (sign-in, launcher, Task Inbox, platform administration) and the platform services it administers: identity service (sign-in, MFA, password policy, users, roles, access control), approval engine (workflows, routing, delegation, re-sync), task-notification outbox, master-data service (MDM), file service, Data Maintenance and the AI Assistant panel.

**What changed in this version (v1.5 → v2.0)** — the document now matches the system as deployed on main. Every statement was checked against the code. Requirements that are wanted but not built are marked **Not implemented** and listed in §14.
- **Architecture.** Sign-in now runs in a dedicated identity service, reached through the EPMS API gateway. Portal is a launcher plus platform administration. All Finance and Budget pages moved to the standalone Finance app (FINANCE.md, BUDGET.md).
- **Sign-in.** MFA is an **emailed one-time code**, not TOTP. Users are forced to change their password on first sign-in and when it expires. Every module now signs in through Portal using a `returnUrl` allow-list. The session hand-off is UTF-8 safe.
- **Launcher.** Module cards and sidebar entries are gated by Access Control permissions. Finance, Meeting Rooms, MRP and Inventory were added. The Task Inbox groups tasks in two levels (module, then task type) and links to EPMS, OA, VMS, Finance or Portal. The AI Assistant is on every Portal page.
- **Access control.** New identity tables hold roles, permission keys, locked cells and additional roles. A user's access is the union of their primary and additional roles. Company-unique posts, additional-only roles and the Finance Director, Payment Officer and ERP PA Officer roles are new. The full permission-key catalogue is in §4.3.
- **Approval administration.** New: Approval Routing, Approval Delegation, automatic re-sync of in-flight approvals, PO sign-off and Travel Application workflows, Finance Director as an approval step, and resubmission resuming at the step that returned the document.
- **Notifications.** Notifications now go through a task outbox with a per-task-type policy (Immediate, Daily digest or Off). Role shared mailboxes and Remittance Advice settings are now in Portal.
- **Admin additions.** Units of Measure, ERP MDM, NC Sync (Purchase, JV and MDM schedules, plus a cutover date), WMS Sync and Data Maintenance.
- **Removed or replaced.** TOTP, the Daily Follow-up switch, Portal's Budget Config, Cost Centers and Factor Library pages, and the Personnel and Audit Log placeholders.

---

## 1. Overview

The **UniOps Portal** is the single entry point to every UniOps module. Every user signs in at Portal and is handed to a module without signing in again. Portal does three jobs:

1. **Authentication gateway.** It handles sign-in, MFA, forced password change and the session hand-off to modules.
2. **Launcher and work hub.** It shows module cards and a unified Task Inbox, and hosts the AI Assistant.
3. **Platform administration.** It configures the company, users, roles and permissions, approval workflows, routing and delegation, notifications, master data, ERP/WMS synchronisation and Data Maintenance.

### 1.1 Architecture decisions

| ID | Decision |
|---|---|
| P-A | Portal is the **single sign-in point**. A module that has no session sends the browser to Portal with a `returnUrl`. After sign-in, Portal returns the browser to that URL with the session attached (§2.5). |
| P-B | The session is handed over in the **URL fragment** (`#__session=<base64 UTF-8 JSON>`). Each module reads the fragment before it renders, stores the session under its own key, and removes the fragment from the address bar. |
| P-C | **Global configuration is administered only in Portal → Admin.** This covers company, security, users, roles and permissions, departments, units of measure, currency, notifications, approval workflows, routing, delegation, ERP/NC/WMS sync and Data Maintenance. Module admin panels keep only module-specific settings. |
| P-D | **Portal has no backend of its own.** The browser calls the EPMS API, which also acts as the gateway to the identity service and the approval engine. For specific screens it calls the OA, Finance, MDM, MRP and VMS APIs directly. The identity service and the approval engine have no public host name. |
| P-E | **Shared platform services.** These are identity (sign-in, roles, permissions), the approval engine, MDM, the file service, and the task-notification outbox, which runs in the EPMS API. Business modules call these services; they do not re-implement them. |
| P-F | **Permissions are server-enforced.** Portal hides navigation and pages the user cannot use, but every service checks permissions itself. Services read the shared permission tables directly through a common authorisation package. |

### 1.2 Platform services

| Service | Responsibility | Browser access |
|---|---|---|
| Portal web | Sign-in, launcher, Task Inbox, administration pages | portal host |
| Identity service | Login, MFA codes, tokens, password change, profile self-edit, role and permission tables, separation-of-duties rules | Through the EPMS API gateway (`/auth/*`, `/config/*`) |
| Approval engine | Workflow execution for every module, Approval Routing, Approval Delegation, in-flight re-sync, submit preflight | Through the owning module's API, and through the EPMS API gateway for routing and delegation administration |
| EPMS API (platform role) | Company configuration (branding, security, currency, SMTP, workflows, notification policy), user administration, Task Inbox, task-notification outbox and digest, AI Assistant, Data Maintenance for EPMS records | Public |
| MDM service | Departments, cost centres, units of measure, tax codes, business partners and vendors, ERP master-data mirrors, materials, BOMs, supply parameters | Public |
| File service | Attachment storage for every module | Public (separate file server) |

The public legal pages (EULA and privacy policy) are served from the Portal host without sign-in.

---

## 2. Authentication & Session

### 2.1 Login page

The login page reads company branding from a public endpoint that needs no sign-in. The page shows the logo (or an initials badge), the company name, the Portal tagline, a static set of module tiles, trust badges and a footer reading "{name} · Internal Platform · © year".

| FR ID | Requirement |
|---|---|
| **LP-001** | The login page fetches company branding without authentication. |
| **LP-002** | If a logo is configured it replaces the initials badge. |
| **LP-003** | The configured company name and Portal tagline replace the built-in defaults on the login page, the sidebar and the footer. |
| **LP-004** | A signed-in user who opens `/login` is redirected to Home. Any `returnUrl` in the query string is kept. |

> The static module tiles on the login page include "Sales CRM — Orders · EDI", which is not a UniOps module (**Defect**, §14).

### 2.2 Login flow and MFA

```
User opens Portal (or a module without a session → Portal ?returnUrl=…)
  ↓ email + password → identity service (via EPMS API gateway)
  ├─ MFA required → identity emails a 6-digit code → user enters it
  └─ tokens issued
       ↓ fetch current user (includes must_change_password)
       ↓ must_change_password? → full-screen Change Password page (§2.3)
       ↓ otherwise → returnUrl (if allow-listed) or Home
```

| FR ID | Requirement |
|---|---|
| **AUTH-001** | Credentials are checked against the active user with that email (case-insensitive). An inactive account, an unknown email and a wrong password all get the same "Incorrect email or password" response. Every failed sign-in is written to the audit log. |
| **AUTH-002** | **MFA = emailed one-time code.** MFA is required when the company switch (Admin → Security) **or** the user's own switch (EPMS My Profile) is on. If the company configuration cannot be read, MFA is treated as on. The code has 6 digits, is valid for 10 minutes and can be used once. It is sent through the task SMTP profile (§5.7). |
| **AUTH-003** | A new code may be requested once every 60 seconds; an earlier request is refused. *Portal has no "Resend code" button* (**Not implemented**, §14). |
| **AUTH-004** | While the company switch is on, a user cannot opt out of MFA for themselves. The personal switch matters only when the company switch is off. |
| **AUTH-005** | Approval actions do not ask for MFA again. MFA is asked once per password sign-in. |
| **AUTH-006** | The login and code screens must describe the code as **emailed**. *The Portal screens still say "authenticator app" and "Require TOTP"* (**Defect**, §14). |

TOTP and a "trusted for 8 hours" MFA window are not part of the product. The unused remnants in the client store are dead code.

### 2.3 Password policy and forced rotation

| FR ID | Requirement |
|---|---|
| **PWD-001** | Passwords have a minimum length of 8 characters. There is no character-class rule, no history and no reuse check. Stronger rules are **Not implemented** (§14). |
| **PWD-002** | **Password expiry** is set in Admin → Security as a number of days (1–365). Blank means never. When a user signs in with a password older than this, the account is flagged for a forced change and the expiry is written to the audit log. |
| **PWD-003** | **First sign-in.** An account created by an administrator, created by CSV import, imported from ERP, or given a new password by an administrator must choose a new password at the next sign-in. Accounts created by CSV or ERP import without a password receive the company-standard initial password. |
| **PWD-004** | While the flag is set, Portal shows only a full-screen **Change Password** page with a "Sign out instead" option. No Portal page or module launcher is shown and no session hand-off is issued. The flag also travels in the hand-off, so modules honour it. |
| **PWD-005** | Changing the password requires the current password. The new password must differ from the current one (checked in the browser only), and the confirmation must match. A successful change clears the flag and restarts the expiry clock. |
| **PWD-006** | *The forced change is enforced only by the user interface. The tokens issued at sign-in work on every API until the user changes the password* (**Defect**, §14). *The AI Assistant launcher is also still reachable on the Change Password page* (**Defect**, §14). |
| **PWD-007** | Self-service "forgot password" does not exist. An administrator resets the password in User Management (**Not implemented**, §14). |

### 2.4 Tokens and session lifetime

| Item | Value |
|---|---|
| Access token | 8 hours. Carries the user id and primary role. |
| Refresh token | 7 days. Rotated on every use; the old token is blacklisted. |
| MFA challenge token | 10 minutes |
| Portal storage | Browser local storage, key `portal-auth` |

| FR ID | Requirement |
|---|---|
| **SES-001** | Any HTTP 401 from the EPMS, Finance, MDM or MRP APIs signs the user out of Portal. *OA and Data Maintenance API errors do not trigger sign-out* (**Defect**, §14). |
| **SES-002** | *Portal never refreshes the access token, so users are signed out after 8 hours regardless of activity. Portal has no idle timeout; EPMS's idle timeout is 60 minutes* (**Not implemented**, §14). |
| **SES-003** | A refresh is refused for a deactivated user. *Access tokens cannot be revoked: a deactivated or demoted user keeps their old access until the token expires* (**Defect**, §14). |

### 2.5 Session hand-off to modules and return URLs

| FR ID | Requirement |
|---|---|
| **SSO-001** | Opening a module from Portal adds `#__session=<base64 of UTF-8 JSON {token, refreshToken, user}>` to the link. The encoding must survive non-Latin names, such as Chinese full names. |
| **SSO-002** | Each module decodes the fragment before rendering, stores the session under its own key (`epms-auth`, `oa-auth`, …), maps user fields to its own schema, and removes the fragment from the address bar. |
| **SSO-003** | A module that has no session redirects to Portal with `?returnUrl=<current URL>`. After password sign-in, after MFA, or straight away if the user is already signed in ("Signing you in…"), Portal returns the browser to that URL with the session attached. |
| **SSO-004** | `returnUrl` is honoured only for http(s) addresses whose origin is Portal or a configured module: EPMS, OA, VMS, Finance, Meeting Rooms, MRP or Inventory. Any other value lands the user on Home. |
| **SSO-005** | EPMS's own login page is kept only as a fallback. EPMS does not re-check MFA locally (see EPMS.md §3.1). |

> **Security note.** Both tokens travel in the URL fragment and are kept in browser storage. This is an accepted design for now and is listed for review in §14.

### 2.6 Global sign-out

Signing out from any module always ends at the Portal login page. Local storage is scoped to each origin, so every module removes only its own key and then sends the browser to Portal `/logout`. That route clears `portal-auth`.

| FR ID | Requirement |
|---|---|
| **SO-001** | Signing out from any module lands the user on the Portal login page. |
| **SO-002** | Each module removes only its own local-storage key. |
| **SO-003** | Modules redirect to `{PORTAL_URL}/logout` after clearing their own key. |
| **SO-004** | Portal `/logout` needs no sign-in. It clears Portal's session and redirects to `/login`. |
| **SO-005** | An HTTP 401 from a module's API triggers that module's sign-out flow. |
| **SO-006** | EPMS's idle timeout (60 minutes) blacklists the refresh token on the server before signing out. |
| **SO-007** | *Portal's own Sign Out does not revoke the refresh token on the server, and sessions of modules not visited stay in their browser storage until next used* (**Not implemented**, §14). |

### 2.7 User menu and profile

The top header shows the user's initials and name. *The search box in the header is a placeholder* (**Not implemented**, §14). The user menu contains:

- **Profile** — opens EPMS **My Profile**, carrying the session. That page holds: display name, Teams account, personal notification channel (Email only / Teams only / Both / None), the email-MFA switch, and **My Signature** (draw or upload; used on PO sign-off PDFs). See EPMS.md §3.1.1.
- **Change Password** — opens in place, using the same form as §2.3.
- **Sign Out** — global sign-out (§2.6).

---

## 3. Portal Home, Navigation & Workspace

### 3.1 Layout and sidebar

Home has a sidebar, a top header and a content area. The content area is one column below the extra-large breakpoint (1280 px); at that width and above it is `[1fr | 280px]`, with Platform Health and Recent Activity in the right column. There is no fixed max-width.

**Sidebar** (one configuration shared by Home and the Admin sub-pages):

```
Home                                   everyone
MODULES
  Procurement  → EPMS dashboard         everyone
  OA           → OA                     everyone
  VMS          → VMS                    everyone
  Finance      → Finance                any of view_finance, view_budget_dashboard,
                                        view_budget_plans, finance.analysis.view
  Meeting Rooms→ Booking                any of view_booking, manage_meeting_rooms
  MRP          → MRP                    mrp.report.view
  Inventory    → Inventory              inventory.view
ADMIN
  Admin                                 System Admin
  Data Maintenance                      data_maintenance
  Access Control                        System Admin
  Approval Routing                      System Admin
  Approval Delegation                   System Admin
```

| FR ID | Requirement |
|---|---|
| **PL-001** | System Admin sees every entry. For everyone else, an entry is shown when the user holds its permission (or any one of its listed permissions), computed as the union of primary and additional roles. Admin-only entries are hidden from non-admins. |
| **PL-002** | Module entries open the module in the same window and carry the session (§2.5). |
| **PL-003** | On desktop the sidebar collapses (224 px ↔ 64 px). When collapsed it shows icons with tooltips. The collapsed state is not remembered across page loads. |
| **PL-004** | On mobile (<768 px) the sidebar is hidden and opens from a hamburger button in the header, over a dimmed backdrop. Tapping the backdrop or a link closes it. |
| **PL-005** | Sidebar branding (name, tagline, logo) comes from the company settings, using the Portal tagline. |
| **PL-006** | *The OA entry and the OA card open OA's retired Payment Application route* (**Defect**, §14). |

Agreements is not a Portal entry; it is reached inside EPMS.

### 3.2 Module cards

| Card | Shown to | Health check |
|---|---|---|
| EPMS (Procurement) | everyone | EPMS API health |
| OA | everyone | OA API health |
| Finance | System Admin or any Finance permission (as in §3.1) | none (always "Active") |
| VMS | everyone | none |
| Meeting Rooms | System Admin or a Booking permission | none |
| MRP | System Admin or `mrp.report.view` | none |
| Inventory | System Admin or `inventory.view` | none |

Cards sit in a responsive grid (1, 2 or 3 columns) with equal heights and an "Explore Module →" link at the bottom. Health checks run every 60 seconds. A card whose check fails shows **Offline**, is greyed out and cannot be clicked. *Only EPMS and OA are health-checked* (**Not implemented** for the others, §14).

### 3.3 Unified Task Inbox

Home shows **My Task Inbox**, with a count badge that caps at "99+".

| Source | Content |
|---|---|
| EPMS API Task Inbox (open tasks) | Every task in the shared task table that the user can act on: EPMS tasks, approval-engine tasks for OA, Finance (budget plans) and VMS documents, and NC-sync and Finance finding tasks. The same rules as EPMS.md §3.7.1 apply: personal plus role-pool tasks for all of the user's roles, plus the approval tasks of anyone who has delegated to them today. |
| OA "my actions" | OA claims and Travel Applications needing the user's action (OA.md) |

| FR ID | Requirement |
|---|---|
| **INB-001** | Rows from the two sources are de-duplicated. OA rows win. The key is the document number, except budget plans (keyed by plan id) and VMS tasks (keyed by task id). Rows are ordered newest first. |
| **INB-002** | **Two-level grouping.** First by module (EPMS → Expense → Visitor), then by task type in a fixed order, each with a header and a count. Task types use the canonical labels in EPMS.md §3.7.1; unknown types are shown in readable form and placed last. OA rows are grouped by claim status (Pending Dept. Approval / Pending Finance Review / Approved — Awaiting Payment). VMS rows are grouped as Visits, Training Confirmations or PPE Confirmations. |
| **INB-003** | Each row shows the module badge, time since creation, an "Urgent" chip when applicable, the title, the document number and the amount. *EPMS amounts are always formatted as CAD* (**Defect**, §14). |
| **INB-004** | **Each row opens the page where the work is done, carrying the session:** <br>• EPMS — PR, PO, GR, Invoice, PA and Agreement detail; PO sign-off opens the PO <br>• OA — expense claim or Travel Application <br>• VMS — the visit or the visitor compliance page <br>• Finance — budget plan, JV-validation findings or duplicate-invoice findings <br>• NC sync errors — Portal Admin → NC Sync (Import ERP Vendor opens EPMS Vendors) <br>*A row whose document type has no mapping (such as a Direct PA) links to an EPMS page that does not exist* (**Defect**, §14). |
| **INB-005** | The inbox is read once per page load; it is not polled. Tasks cannot be dismissed from Portal. *The "Mark all as read" button has no effect* (**Defect**, §14). |

### 3.4 Platform Health and Recent Activity

- **Platform Health** shows "Services Online x/2" (EPMS API and OA API), the average response time, a status dot per service, and a banner reading either "All systems are operational" or "Some services are degraded".
- **Recent Activity** shows the five newest inbox tasks. *It is not an activity log* (**Not implemented**, §14).

### 3.5 AI Assistant

Every signed-in Portal page has the **Assistant** launcher (a floating button, bottom-right) that opens a side panel. It is the same assistant as in EPMS (EPMS.md §3.19) and is served by the EPMS API under the user's own permissions and document scope.

| FR ID | Requirement |
|---|---|
| **AST-P-001** | The panel is available to every signed-in user on every Portal page. It passes `app = portal` and the current route; Portal pages carry no document context. |
| **AST-P-002** | It supports data answers (each shows the query that produced it), "can I submit this?" checks, "what should I do?" (the user's open tasks), "how does X work?" (generated from the live workflow configuration and the guide layer), "how many can we make from stock", and module and document-type explanations. |
| **AST-P-003** | An answer produced by a query can be exported to Excel. Each user can mark up to 10 questions to re-ask with one click; the marked list is shared across every app that has the panel. |
| **AST-P-004** | Answers about users and system settings require the permission `view_system_settings` (System Admin by default). |
| **AST-P-005** | The model is Claude Haiku only (company rule). |

### 3.6 Module workspace, embedding and tabs

Portal itself has no tabs and embeds no module. The conventions that apply across modules are listed here; module PRDs refer to them.

| FR ID | Requirement |
|---|---|
| **UI-TAB-001** | Every module except Portal opens its pages as keep-alive tabs from the shared shell. A list or create page gets one tab per route. A document detail page gets one tab per document, renamed to the document number once loaded. The module's home tab (e.g. EPMS Dashboard, OA Task Inbox) is pinned. |
| **UI-TAB-002** | At most 15 page tabs are kept. Opening more evicts the least recently used tab that is neither pinned nor carrying unsaved changes. Embedded (iframe) tabs do not count towards the limit. Closing a tab with unsaved changes asks for confirmation. |
| **UI-TAB-003** | Tabs live in memory only. A reload, leaving the module, or a different user signing in starts again from the pinned tabs. |
| **UI-TAB-004** | A submit page replaces its own tab with the resulting document. "Back" closes the current tab. A URL that matches no route shows "No route matched" instead of a blank page. |
| **UI-EMB-001** | When a module page runs inside another UniOps module's iframe, it shows only the page content (no sidebar, header or tabs), and the tab features fall back to plain navigation. Today Finance embeds EPMS's budget pages and opens EPMS/OA source invoices as iframe tabs. Modules do not send messages to each other. |
| **UI-DATE-001** | Calendar dates (no time) are parsed as local dates so they never move by a day. |
| **UI-LANG-001** | All user-interface text is English. |

---

## 4. Users, Roles & Access Control

### 4.1 Users

User records live in a shared users table, administered in Admin → User Management (§5.5). Additional roles are kept in the identity service.

| Field | Notes |
|---|---|
| Email | Unique, lower-cased |
| Full name | — |
| Primary role | One per user (§4.2) |
| Department | — |
| Supervisor | Optional. Must be a user in the same department. Drives the Supervisor approval step (§6). |
| ERP person code | Required when an administrator creates the user. Unique. |
| Teams account | For Teams notifications |
| Notification channel | Email only / Teams only / Both / None. Set by the user in My Profile. |
| MFA switch | Set by the user (§2.2) |
| Signature image | Set by the user in My Profile. Used on PO sign-off. |
| Active | Deleting a user only deactivates the account. |
| Must change password / password changed at | §2.3 |

### 4.2 Roles

Roles are a fixed built-in set; they cannot be created or renamed in the UI.

| Code | Label | Notes |
|---|---|---|
| requester | Requester | |
| dept_admin | Department Admin | |
| dept_manager | Department Manager | |
| supervisor / director | Supervisor / Director | Derived for approvals from the user's Supervisor field and Approval Routing; not normally assigned |
| gm | General Manager | **Post** |
| opm | Operations Manager | **Post** |
| procurement_officer | Procurement Officer | |
| procurement_manager | Procurement Manager | **Post** |
| warehouse_staff | Warehouse Staff | |
| ap_clerk | AP Clerk | |
| finance_bp | Finance BP | Many holders. Only users granted it as an **additional** role act as Finance BP approvers. |
| finance_manager | Finance Manager | **Post** |
| finance_director | Finance Director | **Post**, additional role only |
| vendor_manager | Vendor Manager | **Post** |
| cfo | CFO | |
| auditor | Auditor | |
| erp_pa_officer | ERP PA Officer | Additional role only |
| payment_officer | Payment Officer | Additional role only |
| system_admin | System Admin | |

| FR ID | Requirement |
|---|---|
| **ROLE-001** | Every user has one primary role and any number of additional roles. Their effective roles and permissions are the union of both, re-read on every request (no re-login needed). |
| **ROLE-002** | **Posts** (GM, OPM, Procurement Manager, Finance Manager, Finance Director, Vendor Manager) can each be held by only one active user, counting primary and additional roles together. Assigning a post that someone else holds is refused with a message naming the holder. |
| **ROLE-003** | ERP PA Officer, Payment Officer and Finance Director cannot be primary roles; an attempt is refused with a message pointing to Additional Roles. |
| **ROLE-004** | Changing a user's primary role, department, Supervisor or active status re-routes their in-flight approvals automatically (§6.4). The save reports how many documents were re-synced; a re-sync failure does not undo the user change. *Changing only additional roles does not trigger a re-sync* (**Open**, §14). |

Role-by-role business permissions and document visibility scopes are specified in EPMS.md §1.1–§1.3.

### 4.3 Access Control (Portal → Admin → Access Control)

System Admin only. There are two tabs.

**Permission Matrix.** Rows are roles and columns are permission keys, grouped by module. Each cell is a toggle.

| FR ID | Requirement |
|---|---|
| **AC-001** | A role holds a permission when its cell is granted **or locked**. Locked cells show a lock and cannot be switched off; an attempt is refused and the rejected cells are highlighted. |
| **AC-002** | Changes are saved explicitly. Unsaved cells are marked and counted. They take effect on every holder's next request. |
| **AC-003** | Granting or revoking **Vendor Master** also grants or revokes the coupled key `mdm.vendor.write`, which is hidden from the grid. |
| **AC-004** | System Admin passes every permission check in every service, whatever the matrix says. Keys that a user's permission list must show (e.g. for navigation) are also granted to System Admin explicitly. |
| **AC-005** | Adding a permission key needs a software release; keys cannot be added or renamed in the UI. |
| **AC-006** | *Matrix and role changes record only the last editor and time on granted cells. There is no change history* (**Not implemented**, §14). |

**User Roles.** This tab lists every user with a primary-role selector (only roles that may be primary) and additional-role checkboxes, saved per row. Post conflicts and additional-only violations are refused as in §4.2.

**Locked cells:** requester → View PR; procurement officer and procurement manager → View PR, PO, GR; warehouse staff → View GR; AP clerk → View Invoice, View PA; Finance BP, Finance Manager, Payment Officer, Finance Director → View PA; ERP PA Officer → View PO, View PA; System Admin → Admin Panel.

**Permission-key catalogue:**

| Module | Keys (label) |
|---|---|
| EPMS | view_pr, view_po, view_gr, view_invoice, view_pa; create_pr (not enforced — see EPMS.md §1.3); create_gr; invoice_upload; vendor_master; parts_catalog; admin_panel; data_maintenance; pa_override_receipt; epms.invoice.match (Match Invoices); epms.po.write (Create / Edit POs); epms.pa.write (Create / Edit PAs); epms.gr.receive (Receive Goods); epms.agreement.read (View Agreements); epms.agreement.write (Create / Edit Agreements); epms.agreement.receipt.write (Record Agreement Receipts); epms.po.edit_imported (Edit Imported (NC) POs); epms.vendor_credit.manage (Manage Vendor Credits); epms.po.signoff (Raise PO Sign-off) |
| Finance | view_finance; view_budget_dashboard; view_budget_plans; finance.coa.manage; finance.period.close; finance.jv.post; finance.budget.view_all (Full Access Budget View); finance.budget.view_dept (Department-Related Budget View); finance.bank.reconcile; finance.bank.settings; finance.analysis.view (AI Financial Analysis) |
| Budget | budget.catalog.write; budget.plan.write; budget.opening.write |
| MDM | mdm.finance.write (Edit Finance Master Data); mdm.vendor.write (Edit Vendor Master Data, coupled); mdm.bom.write (BOM / material governance fields) |
| MRP | mrp.demand.write; mrp.run.execute; mrp.proposal.confirm; mrp.proposal.export; mrp.exception.handle; mrp.param.write; mrp.report.view — none granted to any role by default |
| Inventory | inventory.view (quantities); inventory.cost.view (amounts); inventory.admin (settings & sync) — System Admin only by default |
| Booking | view_booking; manage_meeting_rooms |
| Portal | view_system_settings (Assistant answers about users and settings) |

The budget data-scope keys (`finance.budget.view_all` / `view_dept`) decide **which** cost centres a user sees; `view_budget_dashboard` decides whether the page opens at all (EPMS.md §3.8, BUDGET.md). `inventory.cost.view` controls whether amounts are returned at all (INVENTORY.md).

### 4.4 Separation-of-duties rules

The identity service holds switchable separation-of-duties rules. Anyone can read them; only System Admin or Finance Manager can switch them, and each change is audited with a reason. The only rule today is **self_payment** ("Creator cannot pay own document"), enforced at payment execution (FINANCE.md). *There is no Portal screen for these rules* (**Not implemented**, §14).

---

## 5. Admin Panel (Portal → Admin)

### 5.1 Navigation and access

`/admin` is for System Admin only; anyone else is sent to Home. It has its own section menu, and `?section=<key>` opens a section directly. Four further admin pages are separate routes, reached from the Portal sidebar:

| Section / page | Route | Access |
|---|---|---|
| Company Settings, Security, Departments, Units of Measure, User Management, Currency Settings, Notification Settings (incl. Task Notifications), Remittance Advice, Approval Workflows, ERP MDM, NC Sync, WMS Sync | `/admin` | System Admin |
| Data Maintenance (§5.14) | `/admin/data-maintenance` | System Admin or `data_maintenance` |
| Access Control (§4.3) | `/admin/access-control` | System Admin |
| Approval Routing (§5.9) | `/admin/approval-routing` | System Admin |
| Approval Delegation (§5.10) | `/admin/approval-delegation` | System Admin |

*The `/admin` page has its own layout with a fixed "UniOps" brand, no user menu and no links to the four separate admin pages* (**Open**, §14). *The underlying company-configuration API accepts changes from any holder of `admin_panel` (by default also Finance Manager and Vendor Manager), not only System Admin* (**Defect**, §14).

### 5.2 Company Settings

- Logo upload (PNG/JPG/WebP, stored as a data URL, with Remove). The logo also appears on document PDFs.
- Company name and delivery address (the default delivery address on POs).
- **Module taglines** for Portal, EPMS, OA, VMS, Finance and Meeting Rooms. A blank module tagline reuses the Portal tagline. *MRP and Inventory request a tagline but have no field* (**Not implemented**, §14).

### 5.3 Security

- **MFA (company switch)** — when on, every user receives an emailed code at sign-in (§2.2).
- **Password Expiry** — 1 to 365 days; blank means never (§2.3).

SMTP settings are not part of Security (§5.7).

### 5.4 Departments, Units of Measure and Cost Centres

**Departments** have a code (upper-cased), a name and an active flag, and are shared by every module. They are owned by MDM (§8).

| FR ID | Requirement |
|---|---|
| **DEP-001** | Creating, editing and deleting a department needs `mdm.finance.write` (default: System Admin, Finance Manager, AP Clerk). The Portal page itself is for System Admin only. |
| **DEP-002** | A department that still has cost centres or users cannot be deleted (HTTP 409); the message tells the user to deactivate it instead. |

**Units of Measure** (code, name, dimension, active) are owned by MDM with the same write permission. A unit used by a catalogue part cannot be deleted. *The delete check ignores materials, BOM lines and unit conversions* (**Defect**, §14).

**Cost Centres** are now managed in the Finance app → Budget Config (BUDGET.md). They are owned by MDM, need `mdm.finance.write` to write, and cannot be deleted while a PR references them. The **Factor Library** also moved to the Finance app. Portal no longer has Finance or Budget pages.

### 5.5 User Management

| FR ID | Requirement |
|---|---|
| **USR-001** | The list shows name/email, role, department, ERP code and status, 20 per page, with a name/email search and a role filter. |
| **USR-002** | **Add/Edit:** full name, email, primary role, department, Supervisor (only users of the chosen department), password, Teams account, ERP code (required on create) and active flag. On edit, a password typed into the field resets it; leaving it blank keeps the current one. |
| **USR-003** | **Delete** deactivates the user and re-syncs their in-flight approvals. |
| **USR-004** | **CSV export** columns: email, full_name, role, department_code, is_active, teams_account, password (always blank). **CSV import** matches users by email, creates or updates them, checks post uniqueness (also within the file), and reports created / updated / per-row errors. The template download gives the same columns. *The template has no ERP code or Supervisor column* (**Defect**, §14). |
| **USR-005** | **From ERP:** pick people from the ERP person mirror (optionally hiding those already imported), enter an email for each, and import them as Requesters with their department mapped automatically. The temporary passwords are shown once. |
| **USR-006** | Additional roles are assigned in Access Control (§4.3); signatures are set by the user. *The role selector on this page omits Finance Director, Payment Officer and ERP PA Officer. That is correct for a primary role, but the same list drives Role Shared Mailboxes* (**Defect**, §14). |

### 5.6 Currency Settings

The default currency and the enabled currencies are chosen from CAD, USD, EUR, GBP, CNY, JPY, AUD and CHF. Only enabled currencies appear in transaction forms. *The configuration supports custom currencies, but Portal has no screen to add them* (**Not implemented**, §14; EPMS.md §3.6.4 should be read accordingly).

### 5.7 Notification Settings (task SMTP and email split)

**SMTP — Task Notifications.** Host, port, username, password (masked), from address and TLS. This profile is used for all internal mail: approval and task notifications, the daily digest, reminders, admin alerts and MFA codes. Port 465 uses implicit TLS; any other port with TLS on uses STARTTLS. **Send test** sends a message through this profile.

**Delivery preferences:**

| Field | Description |
|---|---|
| Default channel | Email only / Teams only / Email + Teams / None. *None* is a master switch that stops all task notifications company-wide. |
| Teams webhook URL | One company-level incoming webhook |
| Role Shared Mailboxes | Per role, an address that receives role-pool task emails **instead of** every holder. Email only, so nothing is sent to it when the channel is Teams only. The address is validated as you type. |
| Daily Digest Time (UTC) | Default 08:00. A change takes effect within about 15 minutes. |
| Service Completion Reminder | On/off switch with a Dry-run option for the Service/Project PO completion-date reminder (EPMS.md §3.13.3) |

Saving keeps settings written by other screens.

**Task Notifications.** A table of every task type, grouped by module, with an **Email** switch and **When** = Immediately or Daily digest per type. The list is built from the system's task types, including OA claim types and custom forms, so new types appear automatically. Types that send their own email are read-only. Unsaved rows are highlighted and can be discarded. The policy applies to tasks created after the change. Behaviour, defaults and recipients are specified in EPMS.md §3.9.4–§3.9.5 (FR IDs NTF-001 … NTF-010) and summarised in §7.

**Email split:**

| FR ID | Requirement |
|---|---|
| **EMAIL-SPLIT-001** | The company configuration has two SMTP profiles: task notifications, and PO-to-vendor. Both may be empty. PO sending falls back field by field to the task profile when the PO profile is not set. |
| **EMAIL-SPLIT-002** | Portal → Admin → Notification Settings owns the task profile. Security does not show SMTP fields. |
| **EMAIL-SPLIT-003** | EPMS → Admin → Email Settings owns the PO-to-vendor profile and the PO email template (EPMS.md §3.6.5). |
| **EMAIL-SPLIT-004** | The SMTP test takes a kind (task, the default, or PO). The PO test uses the same fallback as a real PO send. |
| **EMAIL-SPLIT-005** | Remittance Advice (§5.12) sends through the PO profile, falling back to the task profile, unless its own SMTP user and password are set. |
| **EMAIL-SPLIT-006** | *SMTP passwords are included when any signed-in user reads the company configuration* (**Defect**, §14). They must be returned only to administrators, or masked. |

*Notification wording cannot be edited in any admin screen* (EPMS.md §11 OI-O). *The Daily Follow-up switch of earlier versions has been replaced by the per-type policy.*

### 5.8 Approval Workflows

This is the central configuration of approval chains for every module. Changes apply to the **next submission**; documents already in flight keep their current step, but see §6.4 for re-sync.

**Action keys and shipped defaults:**

| Action key | Document | Default steps |
|---|---|---|
| `pr` | Purchase Requisition | supervisor → dept_manager → director → gm_or_opm |
| `po` | Purchase Order | procurement_manager → gm_or_opm |
| `posign` | PO sign-off (NC-imported POs) | procurement_manager (initials) → opm (signature) |
| `agr` | Purchase Agreement | dept_manager → procurement_manager → finance_manager |
| `pa` | Payment Application (PO-linked) | dept_manager → director → gm_or_opm → finance_bp → finance_manager (a finance_director step may be added as payment sign-off) |
| `pa_dir` | Direct Payment Application | finance_bp → finance_manager |
| `exp`, `mil`, `trv` | Expense, mileage and travel claims | dept_manager → finance_bp |
| `tra` | Travel Application | dept_manager → finance_manager → gm |
| `cfm` / `cfm_<code>` | Custom forms (a named form falls back to `cfm`) | dept_manager |
| `budget_plan` | Budget Plan | dept_manager → finance_manager |
| `vms_visit` | Visitor visit | dept_manager (an optional quality_manager step can be added) |

**Step role tokens:** supervisor, dept_manager, director, gm_or_opm, gm, opm, procurement_manager, finance_manager, finance_director, finance_bp, ap_clerk, quality_manager (VMS only). How each resolves is specified in EPMS.md §3.3.2 and summarised in §6.

| FR ID | Requirement |
|---|---|
| **WF-P-001** | System Admin can add, remove, relabel and reorder steps for any action key without a code change or restart. |
| **WF-P-002** | Changes apply to new submissions. A document returned and resubmitted resumes at the step that returned it (§6.3). |
| **WF-P-003** | A key with no configured workflow uses its shipped default. A named custom form without its own workflow uses `cfm`. |
| **WF-P-004** | The editor refuses to save an action key with zero steps. |
| **WF-P-005** | Missing keys are seeded with the shipped defaults when the approval engine starts; existing workflows are never overwritten. *A brand-new installation is first seeded by the EPMS API, whose PO default (procurement_manager → finance_manager) differs from the engine's* (**Defect**, §14). |
| **WF-P-006** | **PO sign-off** steps may use only company-wide posts. Each step chooses a signature slot (Not on PDF / Initials / Signature), and two steps cannot use the same slot. *Both rules are enforced only in the editor. Vendor Manager is allowed but cannot be picked* (**Defect**, §14). |
| **WF-P-007** | Saving writes the whole workflow set at once. *A failed save shows no error* (**Defect**, §14). *Workflow definitions are not validated on the server, and saving does not re-sync in-flight documents* (**Open**, §14). |

The over-budget steps for PRs (Finance Manager, plus GM/OPM by default) are added by the engine at run time according to the Over-Budget Approval Mode in Finance → Budget Config (fm_gm_opm / fm_only / allow / hard_block; BUDGET.md, EPMS.md §3.2.6). They do not appear in this editor.

### 5.9 Approval Routing

For each active department, System Admin sets:
- **GM or OPM** — which post approves the department's GM/OPM step (a department never configured counts as GM);
- **Director** — optional, any active user; adds a Director step for that department;
- **Supervisor layer** — on/off (default off).

**Engine-wide** settings: the post that approves **payments spanning several departments** (GM or OPM, default GM); such PAs skip the Department Manager and Director steps. **GM and OPM backup** users can also be recorded.

| FR ID | Requirement |
|---|---|
| **ROUT-001** | Saving re-routes every document awaiting approval to the approver the new settings resolve to, and reports the result. |
| **ROUT-002** | The GM/OPM, backup and cross-department values must be GM or OPM. Departments must be active. *Director and backup users are not checked for existence or active status on save; an inactive Director is skipped at run time with a warning in the approval history* (**Defect**, §14). |
| **ROUT-003** | *GM and OPM backups are stored but not used by routing or authorisation* (**Not implemented**; EPMS.md §11 OI-L). |
| **ROUT-004** | Only the last editor and edit time are kept. |

### 5.10 Approval Delegation

System Admin can name a stand-in (delegate) for an approver (delegator) for an inclusive date range, with an optional note. The table shows delegator, delegate, from, to, note and status (Scheduled / Active / Ended / Revoked). The business rules are EPMS.md §1.5 (ABS-001 … ABS-006).

| FR ID | Requirement |
|---|---|
| **DLG-001** | Self-delegation and an end date before the start date are refused. One delegator cannot have overlapping live delegations (a readable conflict message is shown). Delegator and delegate cannot be changed afterwards; only dates and note can be edited, and a revoked delegation cannot be edited. Revoking takes effect immediately and cannot be undone. |
| **DLG-002** | A delegation is active while it is not revoked, today (plant-local calendar date, America/Toronto) falls within its window, and the delegate is active. The delegator keeps their own authority. Delegation is one level only and never passes on System Admin's bypass. |
| **DLG-003** | The delegate sees the delegator's approval tasks and receives their approval emails. *The PO sign-off signature is meant to be non-delegable (sign-off tasks are hidden from delegates), but the engine accepts a sign-off action from an active delegate* (**Defect**, §14). |

### 5.11 Access Control

See §4.3.

### 5.12 Remittance Advice

Settings for the remittance email sent to payees after payment (FINANCE.md, EPMS.md §3.18.3):
- enable switch, From email, From name, CC;
- optional SMTP user and password override (otherwise as EMAIL-SPLIT-005);
- template: subject, heading, greeting, intro, footer, brand colour, show-logo;
- placeholders `{{company_name}}`, `{{payee_name}}`, `{{doc_type}}`, `{{reference}}`, `{{total}}`, `{{payment_method}}`, `{{currency}}`;
- live preview with sample data. Fields left untouched are not saved, so improvements to the built-in defaults reach the user.

### 5.13 ERP MDM, NC Sync and WMS Sync

**ERP MDM** has tabs for Materials (filter by status A/B), Suppliers and Persons. They show the ERP (Yonyou NC65) master-data mirrors held in MDM, with **Incremental** and **Full Resync** buttons (Full asks for confirmation) and the last-sync status, refreshed every 10 seconds.

**NC Sync** has one section with three tabs. Each tab has a schedule card: "Run automatically every N minutes/hours", where 0 means off and the maximum is 1440 minutes. The card shows the next run in local 24-hour time. Scheduled runs are always incremental. A failed status read shows an error, not "Disabled".

| Tab | Content |
|---|---|
| Purchase | NC purchase-order and arrival import into EPMS (EPMS.md §3.12.6). A **cutover date** (only POs dated on or after it are imported; editable when permitted), a last-run summary (POs, PO lines, GRs, GR lines, skipped for no vendor, skipped as consumed, renumbered, skipped for no free number), Incremental, and Full reload (the user must type "FULL RELOAD"). Status refreshes every 2 seconds while a run is in progress. |
| JV | NC voucher import into Finance (FINANCE.md): the same incremental / full pattern |
| MDM | Schedule only for the ERP master-data sync (materials, then suppliers, then persons, incremental). Manual runs are in ERP MDM. |

**WMS Sync** shows the data freshness of the WMS stock snapshot used by Inventory and MRP: snapshot time, batches, last attempt and its status, next run, and last error. It has an interval setting (0 = off, maximum 1440 minutes) and **Sync now**. An empty extract keeps the previous snapshot (INVENTORY.md, MRP.md).

NC sync failures create Task Inbox items for System Admin that open the NC Sync section (§3.3).

### 5.14 Data Maintenance

Data Maintenance is for system-level corrections across modules (permission `data_maintenance`; the owning services enforce it too). Portal merges the record catalogues of the EPMS, OA, VMS and Finance services; a service that does not answer within 15 seconds is left out.

| System | Records |
|---|---|
| EPMS | PR, PO, GR, Invoice, PA, Purchase Agreement, Agreement Receipt, Task (delete only) |
| OA | Expense Claim, Expense Invoice, Payment Application |
| VMS | Visit, Visitor, Health Declaration (edit only) |
| Finance | Payment Batch (delete only) |

| FR ID | Requirement |
|---|---|
| **DM-P-001** | The record list has search, 20 rows per page, multi-select and "select all matching", single and bulk delete. Every delete first shows a cascade preview: what will be deleted, what will only be unlinked, and what will be queued for NC re-fetch. |
| **DM-P-002** | The edit form shows typed fields, with read-only fields disabled. Reference fields (requester, vendor, cost centre, owner, department, …) use type-ahead pickers. |
| **DM-P-003** | **Line items** (PR, PO, PA, GR) can be edited with a live subtotal; derived totals are read-only. GR lines cannot be added. Only the lines the operator touched are sent. |
| **DM-P-004** | A PA's **invoice and receipt links** can be repaired (only sent when touched). On a paid PA the repair also replays the invoice and AP write-back (EPMS.md §3.6.8). |
| **DM-P-005** | Changing a PO's vendor offers **Regenerate PO number** (on by default, asks for confirmation). The new number is carried to the linked PR, PA, GR, invoices, tasks and approval records. |
| **DM-P-006** | **Approval State** panel (PR, PO, PA, Agreement): shows the document's real workflow chain and its open approval tasks. Setting a step closes outdated approval tasks and issues the task for the chosen step, to the resolved approver or to a chosen role. It reports any step the engine settled on instead, any re-sync actions, and whether anyone will see an Approve button. |
| **DM-P-007** | Changing an agreement's owner reports how many open period-confirmation tasks moved to the new owner. |
| **DM-P-008** | Every change is written to the owning service's admin audit log. Rules specific to EPMS records: EPMS.md §3.6.8 (DM-001 … DM-006). |

---

## 6. Approval Engine (platform service)

The engine runs approvals for EPMS, OA, Finance and VMS. The full specification is EPMS.md §3.3. This section records the platform-level rules administered from Portal.

### 6.1 Role resolution

| Token | Resolved to | Task goes to |
|---|---|---|
| supervisor | The requester's Supervisor, only if the requester's department has the supervisor layer on and the Supervisor is active | That person; otherwise the step is skipped and the skip is recorded |
| dept_manager | The active user whose **primary** role is Department Manager in the routing department | That person. If there is none, submission is refused with a message naming the fix. |
| director | The routing department's Director, if active | That person; otherwise skipped |
| gm_or_opm | GM or OPM per the department's routing (no entry = GM). A cross-department PA uses the engine-wide setting. | Every current holder of the post (role pool) |
| gm, opm, procurement_manager, finance_manager, finance_director, vendor_manager | Current holder of the post | Role pool |
| finance_bp | Users granted Finance BP as an additional role | Role pool |
| ap_clerk | Users with the role | Role pool. *Only the primary role is honoured* (**Defect**, §14). |
| quality_manager | The Quality Manager chosen on the VMS visit | That person; skipped if none |

**Routing department:** an agreement's own department (for the agreement and its PAs); otherwise the PR's selected department (for the PR and the POs and PAs raised from it); otherwise the creator's department. A PA covering POs from several departments skips Department Manager and Director.

### 6.2 Actions and authority

The actions are Submit, Approve, Return, Reject (PA: cancelled), Recall (creator or System Admin) and Cancel (creator, System Admin, or the current approver). Approve, Return and Reject need the authority for the current step: the resolved person, any holder of the post, today's delegate, or System Admin. A post with no holder at all may be acted on by a user whose primary role is that post. Actions taken under delegation record "on behalf of <delegator>" on Approve and Reject.

Auto-skips are recorded as separate history entries. They are: conditional skips (no Supervisor or Director, Quality Manager not needed, cross-department PA), and the same-approver cascade after an approval (never at submit, never through delegation, never through System Admin's bypass).

*Submit and Cancel rely on the calling service to check who is acting. The engine itself accepts Submit from any signed-in user, and Cancel of a draft or returned document from whoever holds the first step* (**Defect**, §14).

### 6.3 Resubmission

A document that was **returned** resumes, on resubmission, at the step that returned it. A recalled document starts again from the first step. *The step is stored as a position in the list, so it lands on a different step if the workflow changed shape in between (for example, a PR that crossed the over-budget threshold, or an edited workflow)* (**Defect**, §14). *A returned PO sign-off clears all signatures but resumes mid-chain, so the earlier signature is lost* (**Defect**, §14).

### 6.4 Re-sync of in-flight approvals

| FR ID | Requirement |
|---|---|
| **RSY-001** | In-flight approvals are re-synced automatically after: an Approval Routing save; a user edit that changes primary role, department, Supervisor or active status; a user deactivation; and a primary-role change in Access Control. A re-sync closes leftover tasks on finished documents, skips steps that no longer apply, re-points tasks pinned to a person who is no longer the approver, and re-broadcasts post tasks. Each document is handled independently; one failure does not stop the rest. |
| **RSY-002** | System Admin can re-sync all documents or one document. Data Maintenance re-syncs a document after its requester or step is changed. |
| **RSY-003** | Re-issued tasks are notified again under the task-notification policy. |
| **RSY-004** | *Re-sync moves an over-budget PR that is waiting at its final GM/OPM step back to the earlier over-budget GM/OPM step* (**Defect**, §14). *Documents whose waiting step is a plain GM or OPM step (Travel Application, PO sign-off) are not realigned, and PO sign-offs get a duplicate task on each re-sync* (**Defect**, §14). *Skipped steps are recorded with the requester as the actor* (**Defect**, §14). |

### 6.5 Submit preflight and workflow read-out

The engine can report, without submitting, every reason a submission would currently be refused: status, over-budget hard block, and an approval step that cannot be routed. Each reason says who can fix it. Field-level checks are added by the owning module. The Assistant uses this (EPMS.md §3.3.9).

The effective step list of a document (including injected over-budget steps) is available for the Data Maintenance Approval State panel and the module timelines. The per-type workflow read-out used by the Assistant returns the chain the engine actually executes (configured definition, else the engine's own default) for PR, PO, PO sign-off, agreements, PA and Direct PA, Expense / Mileage / Travel Expense claims, Travel Application, Budget Plan and VMS visits; for a PR it also reports the current over-budget rule (mode, added steps, whether submission is blocked). *Custom-form (CFM) chains are not covered* (**Open**, §14).

*If every step of a workflow is skipped at submission, the document becomes Approved, but the follow-up actions do not run. Those actions are: activating an agreement, creating the Create PO / Process Payment / Process Expense task, confirming a VMS visit, and making a budget plan current* (**Defect**, §14).

---

## 7. Notifications & Task Outbox

The full specification is EPMS.md §3.9. In summary:

- Every task is created **un-notified**, whichever service writes it. A background notifier (every 30 seconds; it picks up tasks unchanged for 15 seconds) applies the per-type policy from §5.7. *Immediate* sends now. *Daily digest* adds the task to one digest per recipient at the Digest Time while the task is open. *Off* sends nothing.
- Default on/immediate types: approve_pr, revise_pr, create_po, approve_po, revise_po, place_order, create_prepayment_pa, sign_po, revise_po_signoff, acknowledge_gr, collect_goods, confirm_service_gr, gr_damage_report, match_invoice, review_match, resolve_exception, confirm_receipt. All other types (including PA, agreement, OA, budget-plan and VMS approvals, and Process Payment) are off until an administrator turns them on (EPMS.md §11 OI-R).
- Recipients, in order:
  1. a role shared mailbox, if one is set;
  2. otherwise the named assignee — or, for approval tasks, today's delegate;
  3. otherwise every active holder of the role.
  
  A requester task with no named requester is never broadcast; System Admins receive an alert instead.
- The company channel *None* switches all notifications off. A user's personal channel *None* stops personal emails.
- Sending is asynchronous, retried up to 3 times and logged, and never blocks a document action. Re-assignment re-notifies. Task types without their own template use the generic "New task" template. User-entered text is HTML-escaped.

*The digest time is entered and applied in UTC, not plant-local time* (**Open**, §14).

---

## 8. Master Data Service (MDM)

MDM owns the master data shared by every module. Portal administers departments and units of measure (§5.4), cost centres are administered in Finance → Budget Config, vendors in EPMS Vendor Master (EPMS.md §3.10), and ERP mirrors in ERP MDM (§5.13). Reads are open to signed-in users except where noted.

| Entity | Rules |
|---|---|
| Departments, Cost Centres, Units of Measure | Writes need `mdm.finance.write`. Delete is refused (409) while referenced: a department by cost centres or users, a cost centre by PRs, a unit by parts. Deactivate is the safe alternative. |
| Tax codes | Versioned by effective date; writes need `mdm.finance.write`. "Delete" only deactivates, because documents keep the code and rate they saved. A tax-determination service resolves the applicable treatment from direction, province, customer type, item tax class and date. |
| Business partners / vendors | One table for suppliers and customers; vendors are the supplier view. Create and edit need `mdm.vendor.write` (coupled to Vendor Master). There is no delete. Includes an optional **remittance email**; when it is empty, remittance falls back to the contact email. |
| ERP mirrors (materials, suppliers, persons) | Mirrored from NC65 incrementally. Manual sync is for System Admin. The schedule runs every N minutes (default and maximum 1440; 0 = off), materials then suppliers then persons, and a failure in one does not stop the others. A failed run is retried at the next interval. The supplier mirror is readable by holders of Vendor Master or `mdm.vendor.write` (it drives EPMS "Import from ERP"). Persons are System Admin only. |
| Materials | Promoted from the ERP mirror on demand (`data_maintenance`). ERP-owned fields are overwritten; local planning fields (item type, procurement type, factory) are never overwritten. ERP class `0101` (Raw Milk) is excluded by MRP. |
| Unit conversions | Mirrored from NC on demand (`data_maintenance`). Conversion is per material in NC (see MRP.md). |
| BOMs | Read with `mrp.report.view`. Sync from NC needs `data_maintenance` or `mdm.bom.write`; only one sync can run at a time, and rows that disappear from NC are removed — unless NC returns nothing. Offers the effective BOM for a date, multi-level explosion, where-used and "has BOM" checks. **Quantity per is stored per one unit of the parent** (NC's per-batch quantity ÷ batch size); the raw per-batch quantity is kept for reporting, and explosion does not also divide by the NC "yield rate", which is a unit ratio, not a production yield. |
| Supply parameters (material × supplier) | Writes need `data_maintenance` or `mdm.bom.write`. A material–supplier pair must be unique, with at most one primary supplier per material. Bulk upsert reports errors per row and keeps the good rows. |

*The scheduled sync does not run the materials promotion, unit conversions or BOM sync; these are manual only* (**Open**, §14). *MDM accepts only a fixed list of primary roles, so a user whose primary role is outside that list is refused even for reads* (**Defect**, §14).

---

## 9. File Service

The file service stores the attachments of every module (PR/PO/PA/GR/agreement documents, OA receipts, VMS visit files, bank statements, meeting-room images). It runs on a separate file server with dedicated storage.

| FR ID | Requirement |
|---|---|
| **FILE-001** | Uploads are limited to 50 MB. Accepted types: PDF; images (PNG, JPEG, GIF, WebP, HEIC); Office files (XLSX, XLS, DOCX, DOC); CSV; plain text; saved emails (Outlook .msg and .eml). Anything else is refused. |
| **FILE-002** | Every upload is tied to a service, a document type and a document id. Downloads keep the original file name. Delete marks the record deleted and removes the stored file. |
| **FILE-003** | Every call requires a valid signed-in session. Modules forward the user's own session. |
| **FILE-004** | *There is no per-file authorisation. Any signed-in user who knows a file's id can download or delete it* (**Defect**, §14). *The file type is taken from what the browser declares, and an upload is read fully into memory before its size is checked* (**Defect**, §14). |
| **FILE-005** | Backup and disaster recovery of file storage are still to be confirmed (EPMS.md §11 OI-J). |

---

## 10. Portal Routes

| Path | Access | Page |
|---|---|---|
| `/login` | Public (a signed-in user is redirected to Home) | Login |
| `/` | Signed in | Home (handles `returnUrl`) |
| `/admin` | System Admin | Admin Panel (`?section=` deep link) |
| `/admin/data-maintenance` | System Admin or `data_maintenance` | Data Maintenance |
| `/admin/access-control` | System Admin | Access Control |
| `/admin/approval-routing` | System Admin | Approval Routing |
| `/admin/approval-delegation` | System Admin | Approval Delegation |
| `/logout` | Public | Clears the session and goes to `/login` |
| anything else | — | Home |

Any signed-in route shows the forced Change Password page instead while a password change is pending (§2.3).

---

## 11. Tech Stack & Deployment

| Layer | Choice |
|---|---|
| Portal frontend | React 19, Vite, React Router v7, TanStack Query v5, Zustand (persisted session), Tailwind CSS v4 with the shared UniOps design tokens and UI primitives, Lucide icons; served by nginx |
| Shared shell | One package shared by all frontends: tab workspace, Assistant panel, design tokens, UI primitives |
| Identity / approval / MDM / file services | Python FastAPI, PostgreSQL (one shared database), Redis (MFA codes, refresh-token blacklist) |
| Authorisation | Shared server-side package that reads the permission tables directly; System Admin short-circuit |
| Hosting | One HTTPS edge with a certificate per host name, issued and renewed automatically; one host name per frontend and public API. The identity service and the approval engine have no public host name. The file service runs on a separate file server. |

The identity service's health check checks both the database and Redis. If the database is down it reports an error; if Redis is down it reports "degraded", because Redis affects only MFA and token refresh.

---

## 12. Settings Moved Out of Module Admin Panels

| Moved setting | Now in |
|---|---|
| Company, Security, Departments, Users, Currency, Notification Settings | Portal → Admin |
| Role Management, Access Control Matrix, custom roles, Director/Supervisor assignment | Portal → Admin → Access Control and Approval Routing |
| Absence cover | Portal → Admin → Approval Delegation |
| Approval Workflows, Dept → GM/OPM mapping | Portal → Admin → Approval Workflows / Approval Routing |
| Role Shared Mailboxes, Remittance Advice | Portal → Admin |
| Cost Centres, Budget Config, Factor Library | Finance app → Budget (BUDGET.md) |

EPMS Admin keeps Email Settings (PO-to-vendor SMTP and template), PDF Templates, Vendor Settings, the SLA settings, Prepayment Config, Collection Config and PMS Data Import (EPMS.md §3.6). Sections that moved show a card linking to their new location.

---

## 13. Security Requirements (platform)

| FR ID | Requirement | Status |
|---|---|---|
| **SEC-001** | Account creation must be possible only through administrator user management or ERP import. Self-registration must not exist, and a caller must never be able to choose their own role. | *A public registration endpoint, reachable through the EPMS API gateway, accepts a caller-chosen role including System Admin* — **Defect** |
| **SEC-002** | Repeated failed password and MFA-code attempts must be throttled or locked out. | **Not implemented** |
| **SEC-003** | Secrets (SMTP passwords) must never be returned to non-administrators. | **Defect** (EMAIL-SPLIT-006) |
| **SEC-004** | Deactivation and role changes must take effect promptly on every API. | **Defect** (SES-003) |
| **SEC-005** | A pending forced password change must block API use, not only the UI. | **Defect** (PWD-006) |
| **SEC-006** | MFA codes must never be written to logs. | *If email sending fails, the code is written to the server log* — **Defect** |
| **SEC-007** | Attachments must be readable and deletable only by users entitled to the owning document. | **Defect** (FILE-004) |
| **SEC-008** | Changes to roles, permissions and company configuration must be audited. | **Not implemented** |
| **SEC-009** | Sign-in, MFA, failed sign-in, password change and expiry, and MFA switch changes are written to an append-only audit log. | Implemented |

---

## 14. Open Items

| ID | Item | Status |
|---|---|---|
| PO-01 | Public self-registration with a caller-chosen role (incl. System Admin) through the gateway (SEC-001) | Defect |
| PO-02 | No throttling or lock-out on password or MFA-code attempts (SEC-002) | Not implemented |
| PO-03 | SMTP passwords returned to every signed-in user when the company configuration is read (EMAIL-SPLIT-006) | Defect |
| PO-04 | Company-configuration changes accepted from any `admin_panel` holder (Finance Manager and Vendor Manager by default), not only System Admin (§5.1) | Defect |
| PO-05 | Access tokens not revocable; deactivated or demoted users keep access for up to 8 h; admin checks trust the role in the token (SES-003) | Defect |
| PO-06 | Forced password change enforced only in the UI; the Assistant is usable on the Change Password page (PWD-006) | Defect |
| PO-07 | MFA code written to the server log when email fails (SEC-006) | Defect |
| PO-08 | Password complexity, history and reuse rules (PWD-001) | Not implemented |
| PO-09 | Self-service "forgot password" (PWD-007) | Not implemented |
| PO-10 | MFA screens say "authenticator app" / "TOTP"; no "Resend code" button (AUTH-003, AUTH-006) | Defect / Not implemented |
| PO-11 | Silent token refresh and idle timeout in Portal; server-side logout from Portal (SES-002, SO-007) | Not implemented |
| PO-12 | OA and Data Maintenance API 401s do not sign the user out (SES-001) | Defect |
| PO-13 | Tokens travel in the URL fragment and are kept in browser storage — security review of the hand-off design (§2.5) | Open |
| PO-14 | OA sidebar entry and card open OA's retired Payment Application route (PL-006) | Defect |
| PO-15 | Inbox rows with an unmapped document type (e.g. Direct PA) link to a non-existent EPMS page (INB-004) | Defect |
| PO-16 | "Mark all as read" has no effect; header search box is a placeholder (INB-005, §2.7) | Not implemented |
| PO-17 | EPMS inbox amounts always formatted as CAD (INB-003) | Defect |
| PO-18 | Health checks for Finance, VMS, Meeting Rooms, MRP and Inventory cards (§3.2) | Not implemented |
| PO-19 | Real Recent Activity / audit-log view (§3.4) | Not implemented |
| PO-20 | Login page advertises a non-existent "Sales CRM" module (§2.1) | Defect |
| PO-21 | `/admin` uses its own layout (fixed brand, no user menu, no links to the four separate admin pages) (§5.1) | Open |
| PO-22 | Tagline fields for MRP and Inventory (§5.2) | Not implemented |
| PO-23 | Custom currencies cannot be added in Portal (§5.6) | Not implemented |
| PO-24 | User CSV template lacks ERP code and Supervisor (USR-004) | Defect |
| PO-25 | Role list on Users / Role Shared Mailboxes omits Finance Director, Payment Officer, ERP PA Officer, so no shared mailbox can be set for them (USR-006) | Defect |
| PO-26 | Workflow save failure shows no error; workflows not validated on the server; PO sign-off rules only in the editor; Vendor Manager cannot be picked for sign-off (WF-P-006, WF-P-007) | Defect |
| PO-27 | First-install PO default differs between the EPMS seed and the engine (WF-P-005) | Defect |
| PO-28 | Saving workflows or changing additional roles does not re-sync in-flight approvals (ROLE-004, WF-P-007) | Open |
| PO-29 | GM/OPM backups stored but unused (ROUT-003; EPMS.md §11 OI-L) | Not implemented |
| PO-30 | Approval Routing does not validate the Director and backup users on save (ROUT-002) | Defect |
| PO-31 | A delegate can perform a PO sign-off by calling the action directly (DLG-003) | Defect |
| PO-32 | Resubmission resumes by step position; a returned PO sign-off loses earlier signatures (§6.3) | Defect |
| PO-33 | Re-sync rewinds over-budget PRs at the final GM/OPM step, ignores plain GM/OPM steps, duplicates sign-off tasks, and records the requester as actor (RSY-004) | Defect |
| PO-34 | All-skipped submission skips the follow-up actions (§6.5) | Defect |
| PO-35 | Pool-role steps (e.g. AP Clerk) honour only the primary role; Department Manager resolution is primary-role only and arbitrary when a department has two (§6.1) | Defect |
| PO-36 | Engine relies on the caller for Submit and Cancel actor checks (§6.2) | Defect |
| PO-37 | Workflow read-out for the Assistant does not cover custom-form (CFM) chains (§6.5). Fixed 2026-10: it no longer uses outdated defaults and covers agreements, OA claims, Travel Application and Budget Plan | Open |
| PO-38 | Admin checks in the approval engine honour System Admin only as a primary role (§5.9–§5.10) | Open |
| PO-39 | Digest time is UTC rather than plant-local (§7) | Open |
| PO-40 | Audit history for role, permission and configuration changes (AC-006, SEC-008) | Not implemented |
| PO-41 | Portal screen for separation-of-duties rules (§4.4) | Not implemented |
| PO-42 | MDM refuses users whose primary role is outside its fixed list (§8) | Defect |
| PO-43 | Unit-of-measure delete check ignores materials, BOM lines and conversions; cost-centre delete check treats a lookup error as "not referenced" (§5.4) | Defect |
| PO-44 | Scheduled ERP sync does not cover materials promotion, unit conversions or BOMs (§8) | Open |
| PO-45 | File service has no per-file authorisation; trusts the declared file type; reads uploads into memory before the size check (FILE-004) | Defect |
| PO-46 | File storage backup and disaster recovery (FILE-005; EPMS.md §11 OI-J) | Open |
