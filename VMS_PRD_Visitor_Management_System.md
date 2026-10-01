---
AIGC:
    ContentProducer: DeepSeek Agent AI
    ContentPropagate: DeepSeek Agent AI
    Label: AIGC
    ProduceID: "00000000000000000000000000000001"
    PropagateID: "00000000000000000000000000000001"
    ReservedCode1: ""
    ReservedCode2: ""
---

# Visitor Management System (VMS) — Product Requirements Document

---

**Document Version**: V2.8  
**Creation Date**: April 2026 (First Draft) / May 2026 (V2.0 — UniOps Integration) / May 2026 (V2.1 — Role Model Refactoring) / May 2026 (V2.2 — Approval Workflow Customization + Notification Contacts) / May 2026 (V2.3 — Integration Reconciliation: port → 8008, schema flattened to `public.vms_*`, approval-api cfm-style integration, VMS-local quality_manager, Portal Module + Task Inbox integration) / June 2026 (V2.4 — Multi-visitor visits, phone optional, Host defaults to current user, badge print loops per visitor, CFIA report one row per visitor) / June 2026 (V2.5 — Host-opt-in per-visitor PPE requests, per-visitor training/PPE compliance with 12-month TTL + HR/Janitor confirmation tasks, in-VMS approval actions + Task Inbox, VMS-local SMTP settings, training/PPE notifications deferred to post-approval) / June 2026 (V2.6 — Doc reconciliation to as-built: compliance is post-entry not a print gate, reports ship as streamed CSV, background scheduler implemented for reminders/no-show/overdue) / September 2026 (V2.7 — full code audit against `origin/main @bb8d0a9a`: implementation status on every requirement, as-built routes / endpoints / roles / task types, production configuration snapshot, known defects list) / October 2026 (V2.8 — fixes for the §12 defects on branch `vms/known-issues-fixes`, pending release; see §13)  
**Document Status**: Revised — V2.7 is an as-built reconciliation. Every requirement table now carries a **Status** column (✅ Built · ◐ Partial / differs · ✗ Not built). Where the spec and the code disagree, the text states what the code does today; open defects are collected in [§12](#12-known-defects-and-gaps-v27).  
**Code baseline**: `origin/main @bb8d0a9a` (production as of 2026-09-30)  
**Scope**: Canada Royal Milk ULC factory and office areas  
**Integration Platform**: UniOps Enterprise Operations Platform (Monorepo)  

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Core Functional Modules](#2-core-functional-modules)
   - 2.1 [Visitor Pre-Registration](#21-visitor-pre-registration)
   - 2.2 [Visitor Check-In (via Badge Printing)](#22-visitor-check-in-via-badge-printing)
   - 2.3 [Visitor Badge Printing](#23-visitor-badge-printing)
   - 2.4 [Visitor Check-Out (via QR Scan)](#24-visitor-check-out-via-qr-scan)
   - 2.5 [Audit Trail](#25-audit-trail)
3. [User Roles and Permissions](#3-user-roles-and-permissions)
4. [User Interaction Flows](#4-user-interaction-flows)
5. [Data Model Design (UniOps ORM Specification)](#5-data-model-design-uniops-orm-specification)
6. [UniOps Platform Integration Architecture](#6-uniops-platform-integration-architecture)
   - 6.4 [Project Directory Structure](#64-project-directory-structure-vms-additions)
   - 6.5 [API Endpoint Design](#65-api-endpoint-design-uniops-rest-specification)
   - 6.7 [Docker Compose Integration](#67-docker-compose-integration)
   - 6.8 [Frontend Route Design](#68-frontend-route-design-standalone-vms-application)
7. [Non-Functional Requirements](#7-non-functional-requirements)
8. [Canadian Environment Adaptation](#8-canadian-environment-adaptation)
9. [Implementation Roadmap](#9-implementation-roadmap-uniops-integrated)
10. [Success Metrics](#10-success-metrics-kpis)
11. [Appendix](#11-appendix)
12. [Known Defects and Gaps (V2.7)](#12-known-defects-and-gaps-v27)
13. [V2.8 Behaviour Changes](#13-v28-behaviour-changes-branch-vmsknown-issues-fixes-pending-release)

---

## 1. Project Overview

### 1.1 Background

Canada Royal Milk ULC, as a dairy production enterprise based in Canada, involves a substantial volume of external visitors to its factory and office premises in daily operations. Visitor types include but are not limited to:

- **Suppliers / Contractors**: Equipment suppliers, raw material suppliers, maintenance contractors
- **Regulatory Inspectors**: CFIA (Canadian Food Inspection Agency) inspectors, provincial health inspectors
- **Auditors**: Third-party certification auditors (HACCP, GMP, ISO, etc.)
- **Customers / Partners**: Business meetings, factory tours
- **Job Candidates**: Interview applicants
- **Temporary Workers**: Short-term contractors, interns

The company already operates the UniOps enterprise platform (Monorepo), covering EPMS (Procurement / Inventory), OA (Expense Reimbursement / Payment), Approval Engine, MDM, and other modules. Visitor management still relies on paper records, disconnected from the UniOps system, creating the following pain points:

- Paper records are difficult to search and trace; audit efficiency is low
- No advance visibility of visit plans; Host preparation is inadequate
- Visitor badges are handwritten, inconsistent, and easily lost
- Departure times are not recorded; on-site headcount cannot be tracked accurately
- Lack of a complete data chain for compliance audits (e.g., who accessed food production areas, whether safety training was completed)
- No receptionist or security positions exist; all hosting tasks are performed by the Host (employee), requiring the system to support a full Host self-service workflow

### 1.2 Project Goals

Build a digital Visitor Management System (VMS) to manage the full lifecycle of "Appointment → Arrival → On-Site → Departure" electronically. Core goals:

1. **Host Self-Service**: The Host (visited employee) completes the entire workflow — appointment, badge printing (= check-in), QR code check-out — without relying on receptionist or security roles
2. **Improved Host Efficiency**: Pre-register visitors; one-click badge print = check-in
3. **Enhanced Safety & Compliance**: Ensure visitors complete required safety training confirmation and health declarations before entering food production areas
4. **Standardized Badge Management**: Print uniform visitor badges via browser, including QR code, access area, validity period, etc. Badges are inserted into standard card holders for wearing. (Note: per PIPEDA privacy regulations, badges do not include visitor photographs)
5. **Full Traceability**: Record complete visitor trajectories to satisfy CFIA, HACCP, and other audit requirements
6. **Platform Integration**: As a native UniOps module, reuse the platform's unified authentication (JWT), approval engine (approval-api), file service (file-api), and employee master data (epms-api) — no information silos
7. **Mobile Support**: Hosts can open VMS on a mobile browser, use the camera to scan badge QR codes and complete check-out
8. **Real-Time Analytics**: Provide multi-dimensional statistics — on-site headcount, visit frequency, visit purpose, etc.

### 1.3 Target Users

| User Role | UniOps Role | Responsibilities | Primary Features |
|-----------|:-----------:|------------------|------------------|
| **Employee (Host)** | any role (default `requester`) | Invite visitors, pre-register, print badges (= check-in), scan QR code to check out, view own visitor records | Appointment creation, badge printing, QR check-out |
| **Department Manager** | `dept_manager` | All Host permissions + first-step approval + sees visits whose Host is in their department | Access approval, department visits |
| **Quality Manager** *(VMS-local)* | any role, listed in VMS Admin → Quality Managers | Second-step approval for GMP / Laboratory / Entire Plant visits | Access approval |
| **HR Training Contact** *(VMS-local)* | any role, whose email = VMS Admin → Notification Contacts → Training | Confirms a visitor's food-safety training (12-month validity) | Task Inbox → visitor compliance page |
| **Janitor PPE Contact** *(VMS-local)* | any role, whose email = VMS Admin → Notification Contacts → PPE | Stages requested PPE; confirms PPE issuance (12-month validity) | Task Inbox → visitor compliance page |
| **Auditor** | `auditor` | Sees all visits; audit log, compliance reports, health declarations | Audit log query, CSV exports |
| **System Administrator** | `system_admin` | Everything, plus VMS Admin panel and end-of-day batch check-out | Admin panel |

> **Note**: VMS does not define standalone receptionist or security roles. All hosting operations (check-in, badge printing, check-out) are performed by the Host in a self-service model. Platform roles reuse `public.users.role`; the three VMS-local roles are configured inside VMS.
>
> **V2.7 — primary role only**: vms-api and the VMS frontend read the single `role` claim in the JWT (the user's *primary* role). Additional roles granted through the Portal permission matrix (`user_roles`) have **no effect** in VMS, and there are no `vms.*` permission codes in identity-api / `packages/authz`. Example: a user whose primary role is `requester` with `auditor` as an additional role does **not** see the Compliance menu in VMS.

---

## 2. Core Functional Modules

### 2.1 Visitor Pre-Registration

#### 2.1.1 Overview

Employees (Hosts) can pre-register upcoming visitor information in the system to create appointment records. Pre-registered records can be retrieved upon visitor arrival to accelerate the on-site process.

#### 2.1.2 Detailed Requirements

##### (1) Appointment Creation

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-PR-001 | Host can create a visitor appointment with basic info: name (first / last, required), company, job title, phone, email (all optional) | P0 (Must) | ◐ Company is **optional** in both the UI ("Company (optional)") and the API (defaults to an empty string) — the V2.4 intent was "required", the build relaxed it. Job title exists on the Visitor record but is not on the inline "Register a new visitor" form |
| VMS-PR-002 | Select visitor type: Supplier, Contractor, Regulatory Inspector, Third-party Auditor, Customer / Partner, Job Candidate, Other | P0 | ✅ Default = Supplier |
| VMS-PR-003 | Set visit date, planned arrival time, planned departure time | P0 | ✅ Defaults: today / 09:00 / 17:00; departure can be cleared. No validation that departure is after arrival. ⚠ "today" is computed in UTC — see §12 D-07 |
| VMS-PR-004 | Select visit purpose: Business Meeting, Equipment Maintenance, Factory Tour, Audit / Inspection, Interview, Delivery, Other | P0 | ✅ Default = Business Meeting |
| VMS-PR-005 | Specify the Host (person being visited): select from employee directory with search. **Host defaults to the currently logged-in user**; operator can pick a different employee via "Change" | P0 | ✅ Host search needs at least 2 characters |
| VMS-PR-006 | Select access area: Office / Lobby, Warehouse, Production (Non-GMP), Production (GMP Clean Zone), Laboratory, Entire Plant | P0 | ✅ Six fixed values. Admin can rename them and recolour them **on the badge only**; areas cannot be added or removed |
| VMS-PR-007 | For high-risk areas the system triggers additional requirements (health declaration, food-safety training, PPE) | P1 (Important) | ◐ As built, see the rule table in (2): health declaration is required for GMP + Laboratory only; training / PPE confirmation tasks are raised for GMP + Laboratory + Entire Plant **at check-in**, not at booking; PPE staging is Host opt-in (VMS-PR-040) |
| VMS-PR-008 | Upload attachments: visitor ID, work permit, insurance certificate, etc. | P2 (General) | ◐ Uploaded from the **visit detail page** after the visit is created (not on the New Visit form). Types: pdf, png, jpg, jpeg, gif, doc, docx, xls, xlsx. No delete. Auditors cannot upload. Download link may not open — §12 D-14 |
| VMS-PR-009 | Appointment copy: for frequent visitors, copy the last appointment with one click | P2 | ✗ Not built |
| VMS-PR-010 | Batch import: import multiple appointments via Excel template | P2 | ✗ Not built |

> **Visitor search (as built)**: the search box matches first name, last name, company and email **each separately** as a substring, returning at most 20 rows. Typing a full name such as "John Smith" matches nothing, and the UI then offers "Register a new visitor" — a common way to create duplicate visitor records. Search by first name *or* last name *or* company.

##### (1.0.1) Multi-Visitor Visits (added in V2.4)

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-PR-030 | A single appointment can carry **one primary visitor + N companions** (one date, one access area, one Host) | P0 | ✅ |
| VMS-PR-031 | Companions share the appointment's schedule, access area, purpose and Host. Each companion keeps their own Visitor row (own name, company, contact, own ID-verification flag) | P0 | ✅ |
| VMS-PR-032 | The New Visit form supports adding companions ("Add another visitor (companion)"). Operator can search the registry or register a new visitor inline for any slot | P0 | ✅ Already-added visitors show "(already added)" in results. Cards are labelled PRIMARY / COMPANION |
| VMS-PR-033 | The visit detail page shows all visitors with each one's ID-verified status. Removing the primary promotes the first companion | P1 | ◐ Promotion works on the **New Visit form only**. After creation the visitor list cannot be changed — there is no edit UI and the PATCH API does not accept visitor lists |

> **Implementation note**: stored as `vms_visits.additional_visitor_ids JSONB` (UUID list, no FK). Single-visitor visits = empty list. We chose JSONB over a proper M2M join table to keep existing queries against `vms_visits` working unchanged (reports, search, badge printing iterate the list explicitly). Migrate to a join table if downstream consumers need to filter or join companions in SQL.

##### (1.1) Notification Contact Configuration (Admin)

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-PR-020 | VMS Admin → "Notification Contacts": **Training Contact email** (HR) and **PPE Contact email** (Janitor) | P0 | ✅ Leaving a field blank disables that channel |
| VMS-PR-021 | Training Contact receives a heads-up email for GMP / Lab visits: visitor name, visit date, access area, Host | P0 | ◐ Sent at the **first badge print (= check-in)**, not after approval, and sent **whether or not** the visitor's training is still fresh (the freshness check only governs the confirmation *task*) |
| VMS-PR-022 | When the Host opts into PPE (VMS-PR-040), the PPE Contact receives the per-visitor gear list | P0 | ✅ Timing as built: **Office** visits (the only auto-confirmed area) email at creation; every other area emails once the visit is approved — the send happens the first time anyone opens the visit after approval (read-driven hook). V2.7: a **"Prepare PPE" task** is also opened for the Janitor (commit `7c75d728`) and auto-closes once the visit is checked in, cancelled or no-show |
| VMS-PR-023 | Contacts can be changed by Admin at any time; effective immediately | P1 | ✅ |
| VMS-PR-024 | VMS Admin → **Email Settings**: VMS-local SMTP (host / port / user / password / STARTTLS / from) with "Send test"; falls back to the shared `company_config` SMTP when unset. Password masked on read; the mask round-trips as "keep existing" | P0 | ✅ "Send test" uses the **saved** settings — save first |

> **V2.7 timing rule (replaces the V2.5 deferral rule)**:
> - **PPE staging email + "Prepare PPE" task** → at creation for Office; after approval for every other area.
> - **HR training email + HR / Janitor confirmation tasks** → at the first badge print (= check-in) of a GMP / Laboratory / Entire Plant visit.
>
> **Contacts must be real users**: a confirmation or "Prepare PPE" task is only created when the configured email equals the email of an **active** UniOps user. Otherwise only the email goes out and nobody can confirm in the system (confirmation is authorised by exact email match — VMS-CI-023).

##### (1.2) Per-Visitor PPE Request (added in V2.5)

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-PR-040 | At visit creation the Host can tick **"PPE needed"**. PPE is opt-in per visit, not auto-triggered by area | P0 | ✅ |
| VMS-PR-041 | Per visitor: clothing size (XS–XXL / other + text) and footwear (shoe covers, or safety shoes with US size 7–14 / other + text) | P0 | ✅ Defaults: M, Shoe covers, size 10. Shoe size is not enforced server-side |
| VMS-PR-042 | Optional group-level notes | P1 | ✅ max 500 chars |
| VMS-PR-043 | The PPE Contact receives one email listing each visitor's gear. Idempotent (`ppe_notified_at`) | P0 | ✅ + "Prepare PPE" task (see above) |

##### (2) Access Area Control Rules

> **V2.7 — as-built rule table** (replaces the V2.6 table; code: `services/approval.py`, `crud/badge.py`, `services/compliance.py`, `services/badge_config.py`).

| Access area (UI label) | Approval steps | Health declaration blocks the badge | Training / PPE confirmation tasks at check-in | Badge band (default) |
|---|---|:---:|:---:|---|
| Office / Lobby | none — auto-confirmed | — | — | Green `#10B981`, LOW RISK |
| Warehouse | Department Manager | — | — | Amber `#F59E0B`, MEDIUM RISK |
| Production (Non-GMP) | Department Manager | — | — | Orange `#EA580C`, MEDIUM RISK |
| Production (GMP Clean Zone) | Department Manager → Quality Manager | ✓ every visitor must pass | ✓ | Red `#DC2626`, HIGH RISK |
| Laboratory | Department Manager → Quality Manager | ✓ every visitor must pass | ✓ | Red `#DC2626`, HIGH RISK |
| Entire Plant | Department Manager → Quality Manager | ✓ every visitor must pass *(V2.8; was ✗ — §12 D-08)* | ✓ | Red `#DC2626`, HIGH RISK |

The per-area confirmations in the V2.6 table — hard hat / safety shoes for Warehouse, dress code for Non-GMP, lab safety briefing for Laboratory — are **✗ not built**.

##### (3) Notification Mechanism

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-PR-011 | After appointment submission, system emails the Host | P0 | ✗ Not built. Approvers also get **no email** for a new approval task — it appears only in the Task Inbox (VMS and Portal) |
| VMS-PR-012 | One day before the visit, reminder to Host | P1 | ✅ Sent on the first scheduler tick after midnight Toronto time (≈ 00:00–00:15) for the next day's **confirmed** visits. A visit still pending approval gets no reminder |
| VMS-PR-013 | High-risk visits need approval; Host notified of the result | P1 | ✅ Host is emailed once on final approval, rejection or cancellation. No email on "Return for edit" |
| VMS-PR-014 | Visitor receives confirmation email with instructions, navigation, parking | P2 | ✗ Not built — VMS never emails visitors |

##### (4) Appointment Management

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-PR-015 | Status lifecycle | P0 | ✅ As built (UI label ← stored value): **Pending Approval** ← `pending_approval`, **Confirmed** ← `confirmed`, **On-Site** ← `checked_in`, **Departed** ← `checked_out`, **Cancelled** ← `cancelled`, **No Show** ← `no_show`; plus the derived red **Overdue** badge. A rejected visit is shown as **Cancelled** — there is no separate "Rejected" status |
| VMS-PR-016 | Host can modify / cancel own appointments before check-in | P1 | ✅ *(V2.8)* "Edit visit" on the visit page (date, times, purpose, area, notes) and "Cancel visit", for the creator, the **Host**, the Host's dept_manager and system_admin; on a confirmed visit the area can only move to the same or a lower approval tier. *Before V2.8:* ◐ **Cancel**: UI button on Confirmed / Pending visits; the API allows the **creator** (not a Host who did not create it), a system_admin, or a dept_manager of the Host's department. **Modify**: API only (`PATCH`), no edit screen. ⚠ Changing `access_area` via PATCH does not re-run approval — §12 D-02 |
| VMS-PR-017 | Host sees own appointments; Manager sees department appointments | P0 | ✅ Visibility: creator or Host → own; dept_manager → plus visits whose Host is in their department; assigned Quality Manager → that visit; auditor / system_admin → all. Lists: Today's Visits, All Visits (**latest 50 only, no paging**), On-Site Now |
| VMS-PR-018 | Global search: visitor name, company, Host, date range | P1 | ✗ Not built — no search or filter on visit lists |
| VMS-PR-019 | Auto-mark no-show 2 hours after planned arrival | P2 | ✅ Confirmed **and** (V2.8) Pending Approval visits; a pending one also has its approval cancelled and its tasks closed |

---

### 2.2 Visitor Check-In (via Badge Printing)

#### 2.2.1 Overview

VMS has no receptionist role. **Check-in is unified with badge printing**: when the Host prints the visitor badge, the system automatically completes check-in (records `actual_arrival`, status → `checked_in`).

The Host can print badges in two scenarios:
- **Desk Printing**: Host opens VMS on their office computer → finds the appointment → clicks "Print Badge" → browser prints → brings the badge downstairs to meet the visitor
- **Public Computer Printing**: Host goes to the shared computer downstairs, logs into VMS → finds the appointment → clicks "Print Badge" → prints on-site and hands the badge to the visitor

#### 2.2.2 Detailed Requirements

> **V2.7 — what "print" actually does**: "Print badge & check in" on the visit detail page opens the badge page. **The badge page records the check-in as soon as it loads**, then opens the browser print dialog. Cancelling the print dialog does **not** undo the check-in; use "Reprint badge" (reason required) to print again.

##### (1) Print = Check-In (With Appointment)

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CI-001 | Host finds the appointment and clicks "Print badge & check in"; the system records `actual_arrival`, sets status to `checked_in`, and opens the browser print | P0 | ✅ Order as described in the note above |
| VMS-CI-002 | Actual arrival time recorded to the second = first print time | P0 | ✅ |
| VMS-CI-003 | Quick search by name / company | P0 | ✗ No search. Visits are found through Today's Visits, Dashboard → Today's appointments, or the Task Inbox |
| VMS-CI-004 | Before printing, Host can supplement: accompanying count, license plate, equipment | P1 | ✗ Not in the UI (the "Vehicle plate" field on the detail page is always "—") |
| VMS-CI-005 | Host confirms they checked each visitor's photo ID ("ID Verified") | P1 | ◐ One checkbox per visitor in the "ID verification" section. The print button stays disabled until **every** visitor is verified. The flag is stored **on the Visitor record, permanently** — a returning visitor is already verified next time — and cannot be unticked in the UI. The server does not enforce it |

##### (2) Instant Registration (No Appointment)

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CI-006 | Walk-in: create visitor + print badge in one action | P0 | ◐ No dedicated "Instant Registration" entry. Use **New Visit** with area **Office / Lobby** (auto-confirmed) → Create visit → tick ID Verified → Print badge & check in. Any other area waits for approval first |
| VMS-CI-007 | Same required fields as pre-registration | P0 | ✅ It is the same form |
| VMS-CI-008 | Logged-in user auto-set as Host | P1 | ✅ |

##### (3) Health & Safety Confirmation (High Priority — Food Factory Specific)

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CI-010 | Health declaration questionnaire forced before the badge prints for GMP visitors; Host asks verbally or visitor self-fills | P0 | ✅ **Per visitor**, for **GMP and Laboratory** (not Entire Plant). Filed from the visit detail page ("Declare"), **before check-in** — the Declare / Re-file buttons disappear once the visit is checked in. Questions come from VMS Admin → Health Questions; production uses the 4 built-in defaults (symptoms in last 24 h / open wounds / contact with infectious disease / carrying food allergens). A "Yes" to any default question = Failed. Submit needs every question answered **and** "Food-safety briefing confirmed" ticked |
| VMS-CI-011 | Failed visitors marked "Restricted Access", badge auto-downgraded to office | P0 | ◐ *(V2.8)* Manual downgrade: "Edit visit" → Office / Lobby, then print (no automatic restricted status). *Before V2.8:* ✗ Not built. A failed declaration blocks the badge for the **whole appointment**. The failure banner tells the user to change the access area, but the UI has no way to do that: cancel the visit and create a new Office visit instead |
| VMS-CI-012 | Food-safety training confirmation for GMP visitors | P0 | ◐ The "Food-safety briefing confirmed" checkbox inside the declaration (Host confirmation). A signature pad is shown but is **optional** |
| VMS-CI-013 | PPE issuance record at check-in | P1 | ✗ No issuance record. PPE is covered by the Janitor's 12-month confirmation (CI-020..023) and the "PPE returned" flag at check-out |

##### (4) Per-Visitor Training + PPE Compliance (added in V2.5)

> Training and PPE compliance are tracked **on the Visitor record**, not per-visit — a frequent supplier who trained last month shouldn't re-train every visit. A 12-month rolling freshness window applies (365 days, in code).

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CI-020 | Each Visitor carries `safety_training_confirmed_at` / `_by` and `ppe_issued_at` / `_by`; "fresh" = within the last 12 months | P0 | ✅ |
| VMS-CI-021 | Badge printing is **not blocked** on compliance freshness (see V2.6 note below) | P0 | ✅ |
| VMS-CI-022 | On the first print of a GMP / Lab / Entire Plant visit, open a **training task** (HR) and a **PPE task** (Janitor) for each stale gate; idempotent per (visitor, gate); HR heads-up email | P0 | ✅ Tasks appear in the VMS and Portal Task Inbox and open the visitor compliance page. Created only when the contact email maps to an active user |
| VMS-CI-023 | Only the configured contact (or a system_admin) can confirm; confirmation stamps the visitor, completes open tasks, is audit-logged | P0 | ✅ Non-contacts get "Only the configured HR training contact can confirm training" / "Only the configured PPE contact can confirm PPE issuance" |
| VMS-CI-024 | Visitor compliance page and VisitDetail banner show each gate's freshness | P1 | ✅ The VisitDetail banner appears for GMP and Laboratory only (not Entire Plant) |

> **V2.6 implementation note — compliance is post-entry, not a print gate**: V2.5 originally specified a hard 422 block on badge printing until every visitor's training + PPE records were fresh (the original VMS-CI-021). Operator UAT showed this deadlocks the real flow: a visitor cannot be issued PPE or briefed on training *before* they have a badge and are physically on-site, so blocking the badge blocks the very step that makes the record fresh. The implemented behavior instead lets the badge print unconditionally and, at check-in, **opens HR / Janitor confirmation tasks** for any stale gate (idempotent — one open task per visitor + gate) plus the HR training heads-up email. Compliance freshness remains fully visible on the visitor compliance page and the VisitDetail banner (VMS-CI-024) so an auditor can see who still owes a confirmation, but it is not an entry gate. The 12-month freshness math, the per-visitor timestamps, and the confirm-by-configured-contact authorization (VMS-CI-023) are unchanged.

---

### 2.3 Visitor Badge Printing

#### 2.3.1 Overview

When the Host prints the badge, the system generates a browser print page with a dedicated badge layout. The Host prints to a standard office printer (A4 / Letter paper), cuts out the badge card, and inserts it into a standard visitor badge holder.

#### 2.3.2 Detailed Requirements

##### (1) Badge Content

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-LB-001 | Badge includes: full name (large), company, visit date, validity, Host, access area (colour-coded), QR code (visit UUID) | P0 | ✅ Default layout: top band "VISITOR" + area name + "LOW / MEDIUM / HIGH RISK"; name (upper case) and company; detail rows Date / Host / Valid until (Vehicle, Accompanying, Purpose available but hidden by default); QR code with caption "Scan to check out"; footer lines |
| VMS-LB-002 | Area colour coding | P0 | ✅ Green = Office, Amber = Warehouse, Orange = Production Non-GMP, Red = GMP / Laboratory / Entire Plant (exact colours in §2.1.2(2)) |
| VMS-LB-003 | "Escort Required" indicator | P1 | ✗ No separate indicator; the footer line covers it |
| VMS-LB-004 | Footer: "Must be accompanied by Host at all times", "Please return badge when leaving" | P1 | ✅ |

##### (2) Printing Method

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-LB-005 | Browser native printing (`window.print()` + `@media print`) to a standard office printer | P0 | ✅ |
| VMS-LB-006 | Dedicated badge card layout sized for cutting out | P0 | ◐ Two identical cards side by side per visitor, one visitor per page, **landscape Letter** |
| VMS-LB-007 | Print dialog first, then check-in on confirmation | P0 | ◐ **Reversed**: check-in is recorded when the badge page loads, before the dialog (§2.2.2 note) |
| VMS-LB-008 | Manual reprint; reprint does NOT re-check-in | P1 | ✅ "Reprint badge" on the visit detail page (after check-in), reason required |
| VMS-LB-009 | Badge print log: timestamp, printed by, print count, reprint reason | P2 | ✅ "Badge prints" section: "Original" / "Reprint #n (reason)" |
| VMS-LB-013 | Multi-visitor visits print one badge per visitor in one print dialog; single check-in event | P0 | ✅ |

> **Design Decision**: No dedicated label printer integration (Zebra/Brother/DYMO). Rationale: ① eliminates hardware procurement and maintenance cost; ② Host can print from their own desk without going to a specific printer; ③ plain paper + badge holder solution adequately meets visitor badging needs.

##### (3) Badge Template Management

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-LB-010 | Admin can edit badge layout | P1 | ◐ Replaced by a **structured badge configuration** (VMS Admin → Badge, `vms_config.badge_config`, migration 0011) with live preview: top band title / show zone / show risk; per-area label, background, text colour, risk level; name & company; detail rows (whitelist: visit date, host, valid until, vehicle plate, accompanying count, purpose); QR on/off + caption; footer lines; font style. No free HTML and no logo. The legacy HTML-template API (`/badge/templates`) is still present but unused by the UI. ⚠ The configuration is applied only when a system_admin prints — §12 D-05. Production still uses the defaults (checked 2026-09-30) |
| VMS-LB-011 | Multiple templates (Standard / VIP / Contractor) | P2 | ✗ Not built — one configuration |
| VMS-LB-012 | Bilingual templates (EN / FR) | P2 | ✗ Not built |

##### (4) Badge Layout Mockup

```
+---------------------------------------------+
|  [Company Logo]          VISITOR            |
|                          [ RED ZONE ]       |
|                                             |
|  NAME: John Smith                           |
|  COMPANY: ABC Corp                          |
|  DATE: 2026-04-15                           |
|  HOST: Jane Doe                             |
|  AREA: Production-GMP                       |
|                                             |
|  +------------------------------------+     |
|  |       QR CODE (Visit UUID)         |     |
|  +------------------------------------+     |
|  ! MUST BE ACCOMPANIED BY HOST             |
|  ! RETURN BADGE WHEN LEAVING               |
+---------------------------------------------+
```

---

### 2.4 Visitor Check-Out (via QR Scan)

#### 2.4.1 Overview

When the visitor leaves, the Host scans the **QR Code** on the visitor badge to complete departure registration. Two operation modes:

- **Desktop**: Host opens the Check-Out page on a computer, uses a USB barcode scanner or manually enters the QR code content
- **Mobile**: Host opens the VMS Check-Out page on a mobile browser, uses the phone camera to scan the QR code

Upon scanning, the system identifies the visit ID, records departure time, and updates status.

#### 2.4.2 Detailed Requirements

##### (1) QR Code Check-Out

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CO-001 | Badge QR contains the visit UUID; scanning locates the visit | P0 | ✅ |
| VMS-CO-002 | Desktop: barcode scanner or manual entry, then details + confirm | P0 | ✅ Check out page → "Scanner / type" mode → "Visit ID" field → "Look up" |
| VMS-CO-003 | Mobile: camera scan via `getUserMedia`, auto-popup on recognition | P0 | ✅ "Camera" mode (default on screens narrower than 768 px). Needs HTTPS and camera permission |
| VMS-CO-004 | Confirm: record `actual_departure`, status → `checked_out`, badge return recorded | P0 | ✅ |

> **Who can check a visitor out**: the scanning user must be able to see the visit, otherwise the page reports "Visit not found". The API allows the visit's creator, its Host, a dept_manager of the Host's department, and system_admin. Scanning an already-departed badge shows "This visitor has already checked out." After a successful check-out the page clears, ready for the next scan. The same confirmation dialog is also reachable from the visit detail page ("Check out").

##### (2) Departure Confirmation

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CO-005 | Departure time to the second | P0 | ✅ |
| VMS-CO-006 | Host confirms badge returned | P1 | ✅ "Badge returned" checkbox (ticked by default) |
| VMS-CO-007 | PPE return confirmation | P1 | ✅ "PPE returned" checkbox (ticked by default), stored in `ppe_issued.returned` |
| VMS-CO-008 | "Your visitor has left" confirmation to Host | P2 | ✗ Not built |

##### (3) Overtime Alerts

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CO-009 | Visitors past planned departure are shown as "Overdue" | P1 | ✅ Derived red "Overdue" badge on lists and detail; Dashboard "Overdue" card (not clickable). Visits without a planned departure never become overdue |
| VMS-CO-010 | 1 hour overdue: reminder to Host | P1 | ✅ **Changed (commit `bf2d2f83`)**: the reminder is **repeated every 24 hours until check-out**, and a **"Check out visitor" task** is opened for the Host (closed automatically once the visit is no longer on-site) |
| VMS-CO-011 | 4 hours overdue or past business hours: escalate to Department Manager | P2 | ◐ 4 hours only (no business-hours branch). Sent once to the first active user with primary role `dept_manager` in the Host's department; if the department has none, it retries each tick until one exists |
| VMS-CO-012 | On-site visitor list with stay duration | P1 | ✅ "On-Site Now" page, refreshes every 60 s, filtered to what the user can see (the Dashboard counters are plant-wide) |

> **Background scheduler (V2.6, updated V2.7)**: an in-process asyncio loop in `vms-api` (`app/services/scheduler.py` → `app/services/scheduled_jobs.py`) runs every `SCHEDULER_INTERVAL_SECONDS` (default **900 s / 15 min**, minimum 60 s) under a Postgres advisory lock, in this order:
>
> | # | Job | Requirement | Rule | Repeat / idempotency |
> |---|---|---|---|---|
> | 1 | `mark_no_shows` | VMS-PR-019 | `confirmed`, never arrived, `planned_arrival + 2h < now` → `no_show` (audit actor "VMS Scheduler") | status transition |
> | 2 | `send_day_before_reminders` | VMS-PR-012 | `confirmed` and `visit_date` = tomorrow in America/Toronto → email Host | once (`reminder_sent_at`) |
> | 3 | `send_overdue_reminders` | VMS-CO-009/-010 | `checked_in`, `planned_departure + 1h < now` → email Host + "Check out visitor" task | **every 24 h until check-out** (`overdue_reminder_sent_at` = last send time) |
> | 4 | `escalate_overdue` | VMS-CO-011 | `checked_in`, `planned_departure + 4h < now` → email the Host's dept_manager | once (`overdue_escalated_at`) |
> | 5 | `close_settled_visit_tasks` | — (V2.7) | closes "Check out visitor" tasks once the visit is no longer `checked_in`, and "Prepare PPE" tasks once it is no longer pending / confirmed | every tick |
>
> Flags are set only when a send is attempted, so an SMTP outage retries on the next tick. `SCHEDULER_ENABLED` defaults to true and is not overridden in production. Ops can force a run via `POST /api/v1/admin/run-scheduled-jobs` (system_admin) — note this manual path does not take the advisory lock (§12 D-19). Times inside the overdue / escalation emails are printed in UTC (§12 D-18).

##### (4) Manual / Batch Check-Out

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-CO-013 | End-of-day one-click batch check-out with secondary confirmation | P1 | ◐ "Batch check-out" on On-Site Now — **system_admin only**, closes **every on-site visit in the plant**, browser confirmation "Close all N on-site visitors now?". Records badge returned = false and PPE returned = false for every visit (this raises the "Unreturned badges" KPI) |
| VMS-CO-014 | Batch check-out logged as "System Batch Check-Out" with reason | P2 | ◐ Logged as `visit.check_out` with the fixed note "System Batch Check-Out"; no custom reason |

---

### 2.5 Audit Trail

#### 2.5.1 Overview

The system must provide a complete audit trail — recording all user operations and visitor data change history — to satisfy CFIA, HACCP, GMP food safety audits and internal compliance audits.

#### 2.5.2 Detailed Requirements

##### (1) Operation Audit Log

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-AU-001 | Log all user operations | P0 | ◐ Logged action types: `visitor.create / update / confirm_training / confirm_ppe`; `visit.create / update / cancel / approve / reject / return / check_in / check_out / no_show / attachment.upload`; `badge.reprint`; `health_decl.submit`; `badge_template.upsert / update`; `admin.quality_managers.update`, `admin.notification_contacts.update`, `admin.health_questions.update`, `admin.smtp_settings.update`, `admin.smtp_test`, `admin.badge_config.update`, `admin.run_scheduled_jobs`; `export_cfia_visit_log`, `export_gmp_area_summary`. **Not logged**: the audit-log CSV export itself, scheduler emails and task creation, the read-side status sync after rejection, and Portal Data Maintenance edits / deletes (those go to the shared `admin_audit_log`) |
| VMS-AU-002 | Entry: timestamp, user, IP, action, entity type / id, before / after JSON | P0 | ✅ plus user agent and notes |
| VMS-AU-003 | Immutable | P0 | ✅ DB-level `REVOKE UPDATE, DELETE ... FROM epms` (migration 0002) |
| VMS-AU-004 | Retention ≥ 3 years, auto-archive | P1 | ✗ No retention or archive job |

##### (2) Visitor History Query

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-AU-005 | Search by visitor, date range, Host, area, type, status | P0 | ◐ Audit log filters only (action type, entity type, user ID, from / to date). Health declarations page filters by date and visitor / company. No visit search |
| VMS-AU-006 | Export to Excel / PDF | P0 | ◐ CSV only (audit log, two reports) |
| VMS-AU-007 | Single visitor profile: all visits, declarations, training | P1 | ◐ Only the visitor compliance page (training / PPE freshness). No visit history view |
| VMS-AU-008 | Full-text search | P2 | ✗ |

##### (3) Compliance Audit Reports

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-AU-009 | Pre-built reports: CFIA Visit Log, GMP Area Access Summary, Contractor Access Report, Monthly Visitor Statistics | P0 | ◐ **CFIA Visit Log** (16 columns, one row per (visit, visitor), excludes pending and cancelled visits; the health / training columns carry the visit-level value, not each visitor's own) and **GMP / Lab Area Summary** (per area: total, passed, failed, restricted, not required, no declaration, after-hours, unreturned — GMP and Laboratory only, Entire Plant excluded). Contractor and Monthly reports ✗ |
| VMS-AU-010 | One-click generation with time range | P0 | ✅ Reports page, From / To (default: first of the month → today) |
| VMS-AU-011 | Formats | P0 | ✅ streamed CSV (see V2.6 note) |
| VMS-AU-012 | Scheduled auto-generation by email | P2 | ✗ |

> **V2.6 implementation note — reports ship as streamed CSV**: Both pre-built reports (CFIA Visit Log, GMP Area Summary) are served as streamed `text/csv` via `StreamingResponse`, never materializing the whole report in memory. CSV was chosen over `.xlsx`/PDF as the interim format because Excel opens it natively and the regulator has not yet handed over a final mandated layout — a binary writer would be churn for no compliance value until that layout is fixed. The route + filter contract (date range, one-row-per-(visit, visitor) for CFIA) is final; only the serialization format is interim.

##### (4) Data Integrity

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-AU-013 | E-signature on health declarations and training confirmations, tamper-proof | P0 | ✗ Signature is **optional**. Re-filing a declaration **overwrites** the previous answers, result and signature in place — §12 D-04 |
| VMS-AU-014 | Critical-field changes record a reason and keep the original | P1 | ✗ Audit before / after snapshot only, no reason |
| VMS-AU-015 | Export hash checksum | P2 | ✗ |

##### (5) Audit Dashboard

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-AU-016 | Compliance indicators | P1 | ◐ Dashboard, visible to **every user**, numbers are **plant-wide** (not filtered to the user's visits): On-site now, Today's visits, Past 7 days, Overdue; "Compliance — this month": GMP/Lab visits, Health pass rate, Unreturned badges (all time, not this month), After-hours today (arrival before 07:00 or from 19:00, evaluated in the database time zone — §12 D-18) |
| VMS-AU-017 | Drill-down | P1 | ◐ On-site / Today / Past 7 days cards open the matching list (filtered to the user's scope, so counts can differ). Overdue and compliance cards do not drill down |

---

## 3. User Roles and Permissions

> **Core Design**: VMS reuses the UniOps `public.users` table as the user master. **Any authenticated UniOps user can act as a Host.** Platform tiers map to the user's **primary** UniOps role (`auditor`, `system_admin`, `dept_manager`); three VMS-local roles (Quality Manager, HR Training Contact, Janitor PPE Contact) are configured inside the VMS Admin panel. There are no `vms.*` permission codes and the Portal permission matrix does not apply to VMS (see §1.3 V2.7 note).

### 3.1 Role Definitions

| VMS Role | Source | Permission Scope | Typical Function |
|----------|:------:|------------------|------------------|
| **Host (Employee)** | Any UniOps user | Create visits; cancel visits they created; ID verification, health declaration, print badge (= check-in) and check-out on visits they can see | All employees |
| **Department Manager** | primary role `dept_manager` | Host rights + sees visits whose Host is in their department + approves the Department Manager step | Line managers |
| **Quality Manager** *(VMS-local)* | VMS Admin → Quality Managers roster | Approves the second step of GMP / Laboratory / Entire Plant visits; sees the visits assigned to them | QA |
| **HR Training Contact** *(VMS-local)* | VMS Admin → Notification Contacts (email must match an active user) | Confirms food-safety training on the visitor compliance page | HR |
| **Janitor PPE Contact** *(VMS-local)* | VMS Admin → Notification Contacts (email must match an active user) | Stages PPE; confirms PPE issuance | Janitorial |
| **Auditor** | primary role `auditor` | Sees all visits; Audit log, Reports, Health declarations | Compliance |
| **Admin (System Admin)** | primary role `system_admin` | Everything, VMS Admin panel, batch check-out, can act on any approval step | IT |

> **Quality Manager assignment**: vms-api picks the **first active user** in the roster (order matters, set with ▲▼ in the Admin panel) and stores it on `vms_visits.quality_approver_id` at submission. There is no round-robin and no "any QM can approve". If the roster is empty or everyone on it is inactive, the Quality Manager step is **skipped** and a GMP visit needs only the Department Manager.

### 3.2 Permission Matrix (as built, V2.7)

| Function | Host (any user) | Dept Manager | Quality Manager | HR / Janitor contact | Auditor | System Admin |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|
| Create visit | ✓ | ✓ | ✓ | ✓ | ✓ (not blocked) | ✓ |
| See visits | created by me or I am Host | + Host in my department | + visits assigned to me | as Host | all | all |
| Cancel visit (Confirmed / Pending) | only visits I **created** | + Host in my department | as Host | as Host | ✗ | ✓ |
| Edit visit (API only, no UI) | only visits I created | + Host in my department | as Host | as Host | ✗ | ✓ |
| Verify ID, health declaration | visits I can see | visits I can see | visits I can see | visits I can see | ID only | ✓ |
| Print badge (= check-in) / reprint | visits I can see | visits I can see | visits I can see | visits I can see | ✓ (all — not blocked) | ✓ |
| Check out | created by me or I am Host | + Host in my department | as Host | as Host | ✗ | ✓ |
| Batch check-out (whole plant) | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Upload attachments | visits I can see | visits I can see | visits I can see | visits I can see | ✗ | ✓ |
| Approve / return / reject | — | Department Manager step (see §6.2.1) | Quality Manager step (assigned) | — | ✗ | any step |
| Confirm training / PPE | ✗ | ✗ | ✗ | own gate only | ✗ | ✓ |
| Dashboard (plant-wide numbers) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Audit log, Reports, Health declarations menu | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |
| VMS Admin panel | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Approval chain shape (Portal Admin → Approval Routing → VMS Visit) | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ (Portal) |

> **V2.7 differences from the V2.6 matrix**: auditors are not blocked from creating visits or printing badges; a Host who did not create the visit cannot cancel it (the Cancel button is shown but returns "Cannot cancel a visit you did not create"); batch check-out is admin-only; "view on-site visitors" is scoped to the user's visibility; system_admin can approve any step. The approver list and matrix above are what the code enforces today; whether auditors *should* be able to check visitors in is an open product question (§11.4 #14).

---

## 4. User Interaction Flows

> **V2.7**: the flows below are rewritten to match the screens as built. UI labels are quoted exactly.

### 4.1 Standard Visitor Flow (With Appointment)

```
[Host: New Visit]
    ↓  Search visitor, or "Register a new visitor" → "Save visitor"
    ↓  (optional) "Add another visitor (companion)"
    ↓  Host = me (or "Change"), Date, Planned arrival / departure, Visit purpose, Access area
    ↓  (optional) "PPE needed" → size per visitor
    ↓  "Create visit"  → lands on the visit detail page
    ↓
[Access area decides the route]
    ├─ Office / Lobby ──────────────────────────────→ Confirmed
    ├─ Warehouse, Production (Non-GMP) → Dept Manager approves → Confirmed
    └─ GMP, Laboratory, Entire Plant → Dept Manager → Quality Manager → Confirmed
    ↓  Host is emailed the result; "Prepare PPE" email + task to Janitor if PPE was requested
    ↓
[Midnight before the visit: reminder email to Host]
    ↓
================ Visitor Arrival Day ================
    ↓
[Host opens the visit (Today's Visits / Dashboard)]
    ↓  "ID verification": tick "ID Verified" for EVERY visitor
    ↓  GMP / Laboratory only: "Health declarations" → "Declare" for EVERY visitor
    │        (all questions + "Food-safety briefing confirmed"; signature optional)
    │        Failed → this visit cannot print; cancel and book an Office visit instead
    ↓  "Print badge & check in"
    ↓  Badge page opens → CHECK-IN IS RECORDED NOW → browser print dialog
    ↓  GMP / Lab / Entire Plant: training / PPE confirmation tasks to HR / Janitor for stale visitors,
    │        HR heads-up email
    ↓  Cut out badge, insert in holder, meet the visitor, hand over PPE
    ↓
================ Visitor On-Site ================
    ↓  Status "On-Site"; listed on "On-Site Now"
    ↓  Past planned departure → red "Overdue"
    ↓  +1 h → email + "Check out visitor" task to Host (repeats daily)
    ↓  +4 h → email to the Host's Department Manager
    ↓
================ Visitor Departure ================
    ↓  "Check out" page → Camera or "Scanner / type" → scan badge QR
    │   (or "Check out" on the visit detail page)
    ↓  "Badge returned" / "PPE returned" → "Confirm departure"
    ↓  Status "Departed"
```

### 4.2 Walk-In Flow (No Appointment)

```
[Visitor arrives without an appointment]
    ↓
[Host: New Visit → Access area "Office / Lobby"] → "Create visit" (auto-confirmed)
    ↓
[Tick "ID Verified" for each visitor] → "Print badge & check in"
    ↓
[Same as the standard flow from here]

Any other access area goes through approval first — there is no instant path for Warehouse,
Production or Laboratory.
```

### 4.3 Approver Flow

```
[Task appears in VMS "Task Inbox" (group "Visits") and Portal "My Task Inbox" (group "Visitor")]
    │  (no email is sent for new approval tasks)
    ↓
[Open task → visit detail page → panel "This visit needs your approval (dept manager | quality manager)"]
    ↓
    ├─ "Approve"          → optional comment → next step or Confirmed
    ├─ "Return for edit"  → reason required  → Host sees the comment, uses "Edit visit", then "Submit for approval" (V2.8)
    └─ "Reject"           → reason required  → visit shows as Cancelled, Host emailed
```

### 4.4 HR / Janitor Compliance Flow

```
[Janitor: "Prepare PPE — <visitor>" task + email with each person's sizes]  (after approval)
    ↓  stage the gear before the visit (work from the email — opening this task shows
    │  "Visit not found" for a Janitor who is not the Host, §12 D-06; it closes by itself at check-in)
[Visitor checked in on a GMP / Lab / Entire Plant visit]
    ↓
[HR: "Confirm food-safety training — <visitor>" task]   [Janitor: "Confirm PPE issuance — <visitor>" task]
    ↓                                                     ↓
[Visitor compliance page → "Confirm now"] (valid 12 months; "Confirmed" greyed out when already fresh)
```

---

## 5. Data Model Design (UniOps ORM Specification)

> **Integration Note**: VMS data models follow the UniOps platform's SQLAlchemy conventions, using `UUIDPrimaryKey` + `TimestampMixin` base classes. Employee (Host) information reuses the `epms-api` `users` table — no redundant storage. The approval flow reuses the `approval-api` engine via a new `vms_visit` doc_type (see §6.2.1). All VMS tables live in the default `public` schema with a `vms_` prefix — same convention as every other UniOps service.

### 5.1 Core Entity Relationships (UniOps Style)

```
+------------------+          +------------------+
|   public.users   |          |  vms_visitors    |
|   (shared)       |          |                  |
|                  |  host_id |  id (UUID PK)    |
|  id (UUID PK)    |<---------|  first_name      |
|  full_name       |    FK    |  last_name       |
|  email           |          |  company_name    |
|  role            |          |  phone, email    |
|  department_id   |          |  visitor_type    |
+--------+---------+          +--------+---------+
         ^                             |
         | host_id / created_by /      |
         | quality_approver_id (FK)    v
+--------+---------------------+
|       vms_visits             |
|                              |
|  id (UUID PK)                |
|  visitor_id (FK vms_visitors)|
|  host_id (FK users)          |
|  created_by (FK users)       |
|  quality_approver_id (FK users, nullable — set when GMP/Lab) |
|  visit_date, planned_arrival, planned_departure              |
|  actual_arrival, actual_departure                            |
|  access_area, visit_purpose, status                          |
|  health_decl_status, safety_training_confirmed               |
|  approval_step_idx, submitted_at  ← consumed by approval-api |
+--+--------------------+------+
   |                    |
   v                    v
+--------------------+  +-------------------------+
| vms_badge_prints   |  | vms_health_declarations |
| id, visit_id (FK)  |  | id, visit_id (FK)       |
| printed_by, _at    |  | questionnaire (JSONB)   |
| template_used      |  | result, signature       |
+--------------------+  +-------------------------+

+--------------------------------------------------+
|  vms_audit_logs  (immutable audit trail)         |
|  id(BIGINT) | timestamp | user_id | action_type  |
|  entity_type | entity_id | old_value(JSONB)      |
|  new_value(JSONB) | ip_address | user_agent      |
+--------------------------------------------------+

+--------------------------------------------------+
|  vms_config (singleton)                          |
|  notification_contacts (JSONB):                  |
|    { training_email: "...", ppe_email: "..." }  |
|  quality_manager_user_ids (JSONB list of UUIDs)  |  ← VMS-local Quality Manager roster
|  badge_templates (JSONB, legacy, unused by UI)   |
|  badge_config (JSONB, structured badge — V2.7)   |
|  health_questions, smtp_settings (JSONB)         |
+--------------------------------------------------+

         ┌─── approval-api reads/writes ───┐
         v                                 v
+-------------------+               +-------------------+
|   public.tasks    |               | public.approval_  |
|   (reused)        |               |   events (reused) |
|  doc_type =       |               |  doc_type =       |
|    "vms_visit"    |               |    "vms_visit"    |
+-------------------+               +-------------------+

+--------------------------------------------------+
|  public.company_config (reused)                  |
|  workflow_defs["vms_visit"] = [                  |
|    {id, role, label} steps                       |
|  ]                                               |
+--------------------------------------------------+
```

### 5.2 SQLAlchemy ORM Models

> Following the existing UniOps pattern: `app/models/*.py`, inheriting from `UUIDPrimaryKey`, `TimestampMixin`, `Base`. **All VMS tables live in the default `public` schema with a `vms_` prefix** — same convention as existing UniOps tables (no use of non-public schemas anywhere in the codebase today). This avoids the Alembic `include_schemas` complication and keeps cross-table FKs straightforward.

#### Visitor

```python
# vms-api/app/models/visitor.py
import enum, uuid
from sqlalchemy import String, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

class VisitorType(str, enum.Enum):
    supplier = "supplier"; contractor = "contractor"; inspector = "inspector"
    auditor = "auditor"; customer = "customer"; interviewee = "interviewee"; other = "other"

class Visitor(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vms_visitors"   # public schema, vms_ prefix
    first_name:   Mapped[str] = mapped_column(String(100), nullable=False)
    last_name:    Mapped[str] = mapped_column(String(100), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_title:    Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone:        Mapped[str | None] = mapped_column(String(20), nullable=True)   # V2.4 — optional
    email:        Mapped[str | None] = mapped_column(String(200), nullable=True)
    visitor_type: Mapped[VisitorType] = mapped_column(SAEnum(VisitorType), nullable=False, default=VisitorType.other)
    id_verified:  Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # V2.5 — per-visitor training + PPE compliance (12-month TTL, see VMS-CI-020)
    safety_training_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safety_training_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ppe_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ppe_issued_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

#### Visit

```python
# vms-api/app/models/visit.py
import enum, uuid
from datetime import date, datetime
from sqlalchemy import String, Boolean, Date, DateTime, Enum as SAEnum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

class VisitStatus(str, enum.Enum):
    pending_approval = "pending_approval"; confirmed = "confirmed"
    checked_in = "checked_in"; checked_out = "checked_out"
    cancelled = "cancelled"; no_show = "no_show"

class AccessArea(str, enum.Enum):
    office = "office"; warehouse = "warehouse"
    production_non_gmp = "production_non_gmp"; production_gmp = "production_gmp"
    laboratory = "laboratory"; all = "all"

class VisitPurpose(str, enum.Enum):
    meeting = "meeting"; maintenance = "maintenance"; tour = "tour"
    audit = "audit"; interview = "interview"; delivery = "delivery"; other = "other"

class HealthDeclStatus(str, enum.Enum):
    not_required = "not_required"; passed = "passed"; failed = "failed"; restricted = "restricted"

class Visit(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vms_visits"
    visitor_id:             Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vms_visitors.id", ondelete="RESTRICT"), nullable=False, index=True)
    # V2.4 — companion visitors (JSONB list of Visitor UUIDs). Empty list = single-visitor visit.
    additional_visitor_ids: Mapped[list]      = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    host_id:                Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    visit_date:         Mapped[date] = mapped_column(Date, nullable=False)
    planned_arrival:    Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_departure:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_arrival:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_departure:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visit_purpose:      Mapped[VisitPurpose] = mapped_column(SAEnum(VisitPurpose), nullable=False)
    access_area:        Mapped[AccessArea] = mapped_column(SAEnum(AccessArea), nullable=False)
    status:             Mapped[VisitStatus] = mapped_column(SAEnum(VisitStatus), nullable=False, default=VisitStatus.confirmed)
    health_decl_status: Mapped[HealthDeclStatus | None] = mapped_column(SAEnum(HealthDeclStatus), nullable=True)
    safety_training_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    badge_returned:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ppe_issued:         Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # V2.5 — host-opt-in PPE request: {"items": [{visitor_id, clothing_size, footwear, shoe_size, ...}], "notes": str}
    ppe_requested:      Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ppe_notified_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # one-shot Janitor email flag
    accompanying_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vehicle_plate:      Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes:              Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    host_notified_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # one-shot Host approval-result email flag
    # V2.6 scheduler flags (migration 0013)
    reminder_sent_at:         Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # day-before reminder, once
    overdue_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # LAST overdue reminder (repeats every 24 h)
    overdue_escalated_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 4 h escalation, once

    # ── Approval workflow fields (consumed by approval-api, see §6.2.1) ────────
    # Mirror fields that approval-api reads/writes via its thin Visit model.
    approval_status:         Mapped[str | None] = mapped_column(String(20), nullable=True)  # engine state: draft / submitted / in_review / approved / returned / rejected / cancelled
    approval_step_idx:       Mapped[int | None] = mapped_column(Integer, nullable=True)  # current step in workflow_defs["vms_visit"]
    visit_title:             Mapped[str] = mapped_column(String(255), nullable=False, default="")   # "VMS Visit — First Last (Company)", shown as the task's document number
    submitted_at:            Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Per-instance approver assignment for the VMS-local Quality Manager step.
    # vms-api populates this from vms_config.quality_manager_user_ids when submitting a GMP-zone visit.
    # approval-api task creation uses this directly as assignee_id (no role resolution needed).
    quality_approver_id:     Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
```

#### BadgePrint / HealthDeclaration / AuditLog

```python
# vms-api/app/models/badge_print.py
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey
import uuid, datetime

class BadgePrint(UUIDPrimaryKey, Base):
    __tablename__ = "vms_badge_prints"
    visit_id:       Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vms_visits.id", ondelete="CASCADE"), nullable=False)
    printed_by:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    printed_at:     Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reprint_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    template_used:  Mapped[str] = mapped_column(String(100), nullable=False, default="standard")

# vms-api/app/models/health_declaration.py
from sqlalchemy import Enum as SAEnum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.visit import HealthDeclStatus
import uuid

class HealthDeclaration(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vms_health_declarations"
    visit_id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vms_visits.id", ondelete="CASCADE"), nullable=False)
    visitor_id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vms_visitors.id"), nullable=False)  # V2.7 doc: one declaration per visitor; UNIQUE (visit_id, visitor_id), migration 0012
    questionnaire_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result:             Mapped[HealthDeclStatus] = mapped_column(SAEnum(HealthDeclStatus), nullable=False)
    signature:          Mapped[str | None] = mapped_column(Text, nullable=True)   # base64 PNG, optional

# vms-api/app/models/audit_log.py
from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
import uuid, datetime

class AuditLog(Base):
    __tablename__ = "vms_audit_logs"
    id:          Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp:   Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_name:   Mapped[str] = mapped_column(String(200), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id:   Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    old_value:   Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value:   Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address:  Mapped[str] = mapped_column(String(45), nullable=False)
    user_agent:  Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes:       Mapped[str | None] = mapped_column(Text, nullable=True)
```

### 5.3 Schema Design Summary

All VMS tables live in the default `public` schema with a `vms_` prefix — consistent with how every other UniOps service (epms-api / expense-api / approval-api / budget-api / finance-api) names its tables. No additional Alembic configuration (e.g. `include_schemas=True`) or `CREATE SCHEMA` migration is required.

| Schema | Table | Purpose | Notes |
|--------|-------|---------|-------|
| `public` | `vms_visitors` | Visitor basic info | Core table |
| `public` | `vms_visits` | Visit records | `host_id`, `created_by`, `quality_approver_id` → `public.users.id` (real FKs) |
| `public` | `vms_badge_prints` | Badge print records | One row per print |
| `public` | `vms_health_declarations` | Health declarations | One per (visit, visitor); required for GMP + Laboratory |
| `public` | `vms_audit_logs` | Audit logs | BIGINT auto-increment, immutable (enforced via DB `REVOKE UPDATE, DELETE` migration; see §2.5.2 VMS-AU-003) |
| `public` | `vms_config` | VMS system configuration (notification contact emails, `quality_manager_user_ids: JSONB`, `badge_config: JSONB` (structured badge, replaces the legacy `badge_templates`), `smtp_settings: JSONB` (V2.5 — VMS-local outbound mail), health questions) | Admin-managed |
| `public` | `users` | Employees (reused) | epms-api existing table; no new table created |
| `public` | `tasks` | Approval + compliance tasks (reused) | approval-api existing table; populated with `document_type` in {`vms_visit`, `vms_train`, `vms_ppe`}; task types in §6.2.1(4.1) |
| `public` | `approval_events` | Approval audit (reused) | approval-api existing table |
| `public` | `company_config` | Workflow defs (reused) | `workflow_defs["vms_visit"]` configured here |

---

## 6. UniOps Platform Integration Architecture

> **Core Principle**: VMS is not a standalone system — it is a **native module** of the UniOps platform. It reuses the platform's existing authentication (Portal JWT), approval engine (approval-api), file service (file-api), and employee master data (epms-api). It adds the `vms-api` microservice and the VMS page group within the frontend ecosystem.

### 6.1 UniOps Overall Architecture (with VMS)

```
                                    +-----------------------------+
                                    |       Portal :5174           |
                                    | SSO + Module Launcher +      |
                                    | Task Inbox (incl. VMS tasks) |
                                    +-------------+----------------+
                                                  | JWT (session handoff via #__session=)
                    +-----------------------------+-----------------------------+
                    |                             |                             |
                    v                             v                             v
        +-------------------+        +-------------------+        +-------------------+
        |  EPMS Frontend    |        |   OA Frontend     |        |   VMS Frontend    |
        |  localhost:5173   |        |  localhost:5175   |        |  localhost:5176   |
        |  Procurement/     |        |  Expense/Payment/ |        |  Visitor Mgmt     |
        |  Inventory/GR/PA  |        |  EXP/MIL/TRV/CFM  |        |  Appt/Check-in/   |
        |                   |        |                   |        |  Badge / QR-out   |
        +-------+-----------+        +--------+----------+        +--------+----------+
                |                             |                            |
                +-----------------------------+----------------------------+
                                              |
                                              v
        +---------------------------------------------------------------------------+
        |                     Backend microservices (shared JWT, shared PG)         |
        +---------------------------------------------------------------------------+
          epms-api :8000 │ mdm-api :8002 │ approval-api :8003 │ finance-api :8004
          file-api :8005 │ expense-api :8006 │ budget-api :8007 │ vms-api :8008 [NEW]
                                              |
                                              v
                                  +-----------------------+
                                  |   PostgreSQL :5432    |
                                  |   public.* (single    |
                                  |   schema, vms_* prefix|
                                  |   for VMS tables)     |
                                  +-----------------------+
                                  +-----------------------+
                                  |   Redis :6379         |
                                  +-----------------------+
```

### 6.2 VMS Positioning in UniOps

| Dimension | Implementation | Notes |
|-----------|---------------|-------|
| **Frontend Entry** | New `vms/` frontend app (React, port 5176), **sibling** to EPMS / OA | Independent routing, independent AppLayout. Reuses the same Portal `#__session=` handoff pattern as OA (`localStorage` key: `vms-auth`, fallback to `portal-auth`). |
| **Backend Service** | New `vms-api` microservice (FastAPI, port **8008**) | Port 8007 is already occupied by `budget-api`. VMS uses 8008. Follows same project structure and code conventions as epms-api / expense-api / budget-api. |
| **Database** | Shared PostgreSQL, `public` schema with `vms_` table prefix | 6 VMS-owned tables (vms_visitors, vms_visits, vms_badge_prints, vms_health_declarations, vms_audit_logs, vms_config). FK references to `public.users` are real DB constraints. |
| **Authentication** | Reuses Portal JWT (issued by epms-api `/api/v1/auth/login`) | vms-api validates JWTs **locally** via shared `JWT_SECRET_KEY` (same pattern as all other UniOps services — no remote introspection). |
| **Approval** | Calls `approval-api` (:8003) via the new `vms_visit` doc_type | approval-api is extended (cfm-style) with a `vms_visit` entry in `_DOC_META` + `_WORKFLOW_DEFAULTS` + `_DOC_TYPES`. Default workflow: single-step `dept_manager`. GMP/Lab zones use 2-step: `dept_manager` → Quality Manager (resolved via per-instance `quality_approver_id` passed by vms-api, not via role lookup). See §6.2.1. |
| **Files** | Calls `file-api` (:8005) | Appointment attachment uploads. file-api `entity_type` accepts `vms_visit` (configuration only). |
| **Employee Data** | Calls a **new public** `epms-api` endpoint `GET /api/v1/users/directory` | The existing `GET /api/v1/users` is `system_admin`-only ([users.py:34](epms-api/app/api/v1/users.py#L34)). epms-api must expose a read-only directory endpoint returning `{id, full_name, email, department_id}` for any authenticated user — used by VMS Host search. Same endpoint is reusable by other modules. |
| **CORS** | epms-api / approval-api / file-api `ALLOWED_ORIGINS` must include `http://localhost:5176` | [epms-api/app/core/config.py:20-25](epms-api/app/core/config.py#L20-L25) currently hardcodes 5173/5174/5175 — VMS frontend port must be appended in defaults and Docker `ALLOWED_ORIGINS` env. |
| **Notifications** | Reuses existing email channel; **Teams channel deferred to Phase 2** | UniOps users have `notification_channel ∈ {email_only, teams_only, both, none}`. Phase 1 sends email only. Phase 2 routes per user preference, mirroring OA approval notifications. |

### 6.2.1 VMS Approval Workflow — cfm-style Integration with approval-api

VMS visitor access approval is added to the approval engine by extending `_DOC_META` / `_WORKFLOW_DEFAULTS` / `_DOC_TYPES` the **same way `cfm` was added** ([engine.py:46](approval-api/app/crud/engine.py#L46), [engine.py:152](approval-api/app/crud/engine.py#L152), [workflows.py:11](approval-api/app/api/v1/workflows.py#L11)). approval-api keeps a thin `Visit` mirror model (only the state-transition fields it needs to read/write) — identical to how it already keeps its own `BudgetPlan`, `PurchaseRequest`, `ExpenseClaim` copies pointing at the shared DB.

#### (1) approval-api changes (one-time)

```python
# approval-api/app/models/visit.py  [NEW]
class Visit(UUIDPrimaryKey, Base):
    __tablename__ = "vms_visits"
    visitor_id:          Mapped[uuid.UUID]
    host_id:             Mapped[uuid.UUID]
    created_by:          Mapped[uuid.UUID]
    access_area:         Mapped[str]
    status:              Mapped[str]           # uses VisitStatus enum values as strings
    approval_step_idx:   Mapped[int | None]
    submitted_at:        Mapped[datetime | None]
    quality_approver_id: Mapped[uuid.UUID | None]
    # …only fields approval-api needs; full model lives in vms-api
```

```python
# approval-api/app/crud/engine.py — additions
from app.models.visit import Visit

_DOC_META["vms_visit"] = {
    "model":        Visit,
    "number_attr":  "id",              # visits have no human-readable number; UUID is fine for display
    "amount_attr":  None,              # not money-based
    "vendor_attr":  None,
    "task_approve": "approve_vms_visit",
    "task_revise":  "revise_vms_visit",
    "valid_submit":  ("confirmed",),               # Host pre-registers as confirmed; submits for approval when GMP/Lab triggered
    "valid_approve": ("pending_approval",),
    "valid_return":  ("pending_approval",),
    "valid_cancel":  ("confirmed", "pending_approval"),
}

_WORKFLOW_DEFAULTS["vms_visit"] = [
    {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    # Optional second step is configured by Admin per access-area (see §6.2.1(2) below):
    # {"id": "quality_manager", "role": "quality_manager", "label": "Quality Manager"}
]
```

```python
# approval-api/app/api/v1/workflows.py — change
_DOC_TYPES = ("pr", "po", "pa", "vms_visit")     # add vms_visit so Portal Admin UI can configure it
```

> **Quality Manager resolution** — special-case for `vms_visit`: when a workflow step has `role == "quality_manager"` and `doc_type == "vms_visit"`, the engine **skips role lookup** and uses `Visit.quality_approver_id` (populated by vms-api before submit) directly as the task `assignee_id`. This keeps `quality_manager` a VMS-local concept — no entry needed in UniOps `VALID_ROLES` and no `role_management` mapping required.

#### (2) Workflow chains (configurable via `CompanyConfig.workflow_defs`)

After the engine seeds the default `[dept_manager]` chain at first launch ([main.py:14-34](approval-api/app/main.py#L14-L34)), Admin can override per VMS deployment. VMS does **not** select a different chain per area at runtime — the chain stored in `workflow_defs["vms_visit"]` is the single chain used for all access-area approvals. Whether a visit needs approval at all is decided by VMS (see (3)).

```json
// Default — single-step
{ "vms_visit": [
    {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}
]}

// Dual approval for GMP-zone-sensitive deployments
{ "vms_visit": [
    {"id": "dept_manager",    "role": "dept_manager",    "label": "Department Manager"},
    {"id": "quality_manager", "role": "quality_manager", "label": "Quality Manager"}
]}
```

#### (3) When VMS submits to approval-api

VMS decides per-visit whether to invoke approval based on `access_area`:

| `access_area` | Goes through approval-api? | Workflow steps applied |
|---|:---:|---|
| `office` | No (auto-confirmed) | — |
| `warehouse`, `production_non_gmp` | Yes | All steps from `workflow_defs["vms_visit"]`; the `quality_manager` step is auto-skipped because `quality_approver_id` is null |
| `production_gmp`, `laboratory`, `all` | Yes | All steps including `quality_manager` (skipped if the roster has no active user) |

**Submit sequence (as built, V2.7)**:

1. Host submits the New Visit form → vms-api inserts the `vms_visits` row with `status="pending_approval"`, `approval_status="draft"` and a `visit_title` ("VMS Visit — First Last (Company)") used as the task's document number. Office visits are inserted as `confirmed` and stop here.
2. GMP / Lab / Entire Plant: vms-api writes the **first active user** of `vms_config.quality_manager_user_ids` into `quality_approver_id`.
3. vms-api commits, then calls `POST /approval/v1/approvals/vms_visit/{visit_id}/action` with `{"action": "submit"}` and the user's token.
4. approval-api advances `approval_step_idx` and creates an `approve_vms_visit` task:
   - `dept_manager` step → the dept_manager of the **visit creator's** department (`_routing_user_id` falls back to `created_by` for `vms_visit`). If that department has no active dept_manager the submit fails — see ⚠ below.
   - `quality_manager` step → `quality_approver_id` directly.
5. The approver acts from the VMS visit detail page (the Portal task row only deep-links there). VMS calls `POST /api/v1/visits/{id}/action` (proxy → approval-api) with `approve` / `return` / `reject`. The UI requires a comment for Return and Reject; the API does not.
6. Final approve → approval-api's `_post_approve_vms_visit` sets `vms_visits.status='confirmed'`. Reject / cancel → vms-api maps `approval_status` to `status='cancelled'` on the next read (no engine hook). The Host result email is sent once, on that read.

> **⚠ V2.7 corrections to earlier versions**:
> - **No auto-skip at submit.** The engine's same-approver skip only runs *after* an approve, for the following steps. A dept_manager who books a visit gets the approval task for their own visit (the earlier "Host is also the dept_manager → step skipped" statement was wrong).
> - **Seeded chain**: the engine default for `vms_visit` is still the single `dept_manager` step (`approval-api/app/crud/engine.py` `_WORKFLOW_DEFAULTS`). The V2.5 note saying the seed "now includes" `quality_manager` is not what the code does; the second step exists only where an admin added it in Portal → Approval Routing. **Production has both steps configured** (checked 2026-09-30).
> - **Routing department vs. visibility department**: approval routes by the *creator's* department, while dept_manager visibility in vms-api uses the *Host's* department. When they differ, the approver can get "Visit not found" opening their own task (§12 D-10).
> - **Submit failure = no approval** (§12 D-01): if the submit call fails for any reason, vms-api resets the visit to `confirmed` and returns 502; the visit then prints without approval.
> - **Return for edit** has no resubmission path (§12 D-03).

##### (4.1) In-VMS Approval + Task Inbox (added in V2.5, updated V2.7)

| Requirement ID | Description | Priority | Status (V2.7) |
|----------------|-------------|----------|---------------|
| VMS-AP-001 | VMS **Task Inbox** lists the current user's pending VMS tasks, sourced from epms-api `/tasks?is_completed=false` filtered to `vms_visit` / `vms_train` / `vms_ppe`; sidebar shows a live count | P0 | ✅ Grouped "Visits", "Training Confirmations", "PPE Confirmations"; refreshes every 60 s |
| VMS-AP-002 | VisitDetail shows Approve / Return for edit / Reject when the user holds a task for the visit; Reject / Return require a comment | P0 | ✅ *(V2.8)* only for an open `approve_vms_visit` task; a returned visit shows the approver's comment with "Edit visit" / "Submit for approval". *Before V2.8:* ◐ The panel is shown whenever the user has **any** open `vms_visit` task on that visit — including the requester's "Revise" task after a return, where the buttons then fail (§12 D-03) |
| VMS-AP-003 | Dashboard nudge "N visit(s) waiting on your approval" | P1 | ✅ *(V2.8)* wording follows the task mix. *Before V2.8:* ◐ N counts **all** open VMS tasks (training, PPE, check-out, prepare-PPE), not only approvals |
| VMS-AP-004 | VMS doc types excluded from the EPMS inbox; shown in the Portal inbox with deep-links into VMS | P0 | ✅ Portal groups them under "Visitor" |

**Task types (V2.7)** — all live in the shared `tasks` table:

| `type` | `document_type` | Assignee | Opened | Closed | Opens in VMS |
|---|---|---|---|---|---|
| `approve_vms_visit` | `vms_visit` | Dept manager, then Quality Manager | submit / next step | by the approval action | visit detail |
| `revise_vms_visit` | `vms_visit` | visit creator | "Return for edit" | **never** (§12 D-03) | visit detail |
| `vms_confirm_training` | `vms_train` | HR Training Contact | first print, training stale | "Confirm now" | visitor compliance page |
| `vms_confirm_ppe` | `vms_ppe` | Janitor PPE Contact | first print, PPE stale | "Confirm now" | visitor compliance page |
| `check_out_visitor` *(V2.7)* | `vms_visit` | Host | 1 h overdue | scheduler, once no longer on-site | visit detail |
| `prepare_ppe` *(V2.7)* | `vms_visit` | Janitor PPE Contact | PPE email sent | scheduler, once checked in / cancelled / no-show | visit detail (⚠ §12 D-06) |

Task titles as shown: "Approve VMS_VISIT: VMS Visit — First Last (Company) — …" (the visitor text appears twice because `vms_visit` has no label mapping in the engine), "Confirm food-safety training — First Last (Company)", "Confirm PPE issuance — …", "Check out visitor — …", "Prepare PPE — …".

### 6.3 Technology Stack (Fully Aligned with UniOps)

| Tier | Technology | Version | Notes |
|------|-----------|---------|-------|
| **Frontend Framework** | React | 19.2+ | Unified with oa/epms/portal |
| **Build Tool** | Vite | 8.0+ | HMR dev experience |
| **Type System** | TypeScript | 6.0+ | Strict mode |
| **Styling** | TailwindCSS | 4.2+ | Utility-first |
| **Routing** | React Router | 7.14+ | Independent VMS route tree |
| **State Management** | Zustand | 5.0+ | Lightweight store |
| **Data Fetching** | TanStack React Query | 5.100+ | Cache / refetch / optimistic updates |
| **Icons** | Lucide React | 1.14+ | Unified icon library |
| **Backend Framework** | FastAPI (Python) | 0.100+ | async/await |
| **ORM** | SQLAlchemy | 2.0+ | AsyncSession |
| **Data Validation** | Pydantic | 2.0+ | schemas |
| **Database** | PostgreSQL | 15 | Shared instance, default `public` schema with `vms_*` table prefix (same convention as all other UniOps services) |
| **Cache** | Redis | 7 | Shared instance |
| **Containerization** | Docker + Compose | — | Local dev and production deployment |

### 6.4 Project Directory Structure (VMS Additions)

```
UniOps/                              # <- Existing Monorepo root
|
+-- vms-api/                         # [NEW] VMS backend microservice
|   +-- Dockerfile
|   +-- pyproject.toml
|   +-- requirements.txt
|   +-- alembic/
|   |   +-- versions/                # VMS schema migration scripts
|   +-- app/
|   |   +-- main.py                  # FastAPI app factory (same pattern as epms-api)
|   |   +-- api/
|   |   |   +-- v1/
|   |   |       +-- __init__.py      # api_router aggregation
|   |   |       +-- health.py        # /health liveness check
|   |   |       +-- visitors.py      # CRUD /api/v1/visitors
|   |   |       +-- visits.py        # CRUD /api/v1/visits (incl. check-in/check-out)
|   |   |       +-- badge.py         # POST /api/v1/visits/{id}/print-badge
|   |   |       +-- health_decl.py   # POST health-declaration
|   |   |       +-- audit.py         # GET /api/v1/audit-logs
|   |   |       +-- dashboard.py     # GET /api/v1/dashboard/overview
|   |   |       +-- reports.py       # CFIA / GMP audit reports
|   |   +-- models/
|   |   |   +-- __init__.py
|   |   |   +-- visitor.py
|   |   |   +-- visit.py
|   |   |   +-- badge_print.py
|   |   |   +-- health_declaration.py
|   |   |   +-- audit_log.py
|   |   +-- schemas/
|   |   |   +-- visitor.py
|   |   |   +-- visit.py
|   |   |   +-- badge.py
|   |   |   +-- health_decl.py
|   |   |   +-- audit.py
|   |   +-- core/
|   |   |   +-- config.py            # Settings (DATABASE_URL, JWT_SECRET, etc.)
|   |   |   +-- deps.py              # get_session, get_current_user (validates JWT)
|   |   |   +-- security.py          # JWT validation (validates epms-api issued tokens)
|   |   +-- crud/
|   |   |   +-- visitor.py
|   |   |   +-- visit.py
|   |   |   +-- audit.py
|   |   +-- db/
|   |       +-- base.py              # Base, TimestampMixin, UUIDPrimaryKey
|   |       +-- session.py           # AsyncSession factory
|   +-- tests/
|
+-- vms/                              # [NEW] VMS frontend app (sibling to EPMS / OA)
|   +-- .env
|   +-- .env.example
|   +-- package.json
|   +-- vite.config.ts
|   +-- index.html
|   +-- src/
|       +-- main.tsx                  # React entry
|       +-- App.tsx                   # Routes + AppLayout
|       +-- pages/
|       |   +-- VisitListPage.tsx     # Appointment list / Today's / On-site visitors
|       |   +-- VisitCreatePage.tsx   # Host creates appointment
|       |   +-- VisitDetailPage.tsx   # Visitor profile + visit details
|       |   +-- CheckInPage.tsx       # Badge print check-in page
|       |   +-- BadgePrintPage.tsx    # Badge printing / reprint
|       |   +-- DashboardPage.tsx     # Audit dashboard
|       +-- components/
|       |   +-- layout/
|       |   |   +-- AppLayout.tsx     # VMS standalone layout (sidebar nav)
|       |   +-- ui/                   # Reusable UI components (Pagination, etc.)
|       |   +-- BadgePreview.tsx      # Badge preview
|       |   +-- HealthDeclForm.tsx    # Health declaration form
|       |   +-- VisitorSearch.tsx     # Visitor search
|       |   +-- CheckOutConfirm.tsx   # Check-out confirmation dialog
|       +-- services/
|       |   +-- api.ts                # VMS API client (React Query hooks)
|       +-- store/
|           +-- auth.ts               # Auth state (Zustand)
|
+-- approval-api/                     # [MODIFIED]
|   +-- app/
|       +-- models/
|       |   +-- visit.py             # [NEW] Thin Visit mirror — same vms_visits table
|       +-- crud/
|       |   +-- engine.py            # [MODIFIED] add vms_visit to _DOC_META + _WORKFLOW_DEFAULTS + quality_manager special-case resolution
|       +-- api/
|           +-- v1/
|               +-- workflows.py     # [MODIFIED] add "vms_visit" to _DOC_TYPES
|
+-- epms-api/                         # [MODIFIED]
|   +-- app/
|       +-- api/
|       |   +-- v1/
|       |       +-- users.py         # [MODIFIED] Add public GET /api/v1/users/directory[/{id}]
|       +-- core/
|           +-- config.py            # [MODIFIED] ALLOWED_ORIGINS add http://localhost:5176
|
+-- portal/                           # [MODIFIED]
|   +-- src/
|       +-- pages/
|           +-- PortalHome.tsx       # [MODIFIED] NAV_SECTIONS + resolveHref + vmsHref + Task Inbox doc_type mapping
|
+-- docker-compose.dev.yml            # [MODIFIED] Added vms-api (8008) + vms-frontend (5176); appended 5176 to ALLOWED_ORIGINS of all backend services; added VITE_VMS_URL / VITE_VMS_API_URL to portal-frontend
+-- docs/
    +-- VMS_PRD_Visitor_Management_System.md  # <- This document
```

### 6.5 API Endpoint Design (as built, V2.7)

> All endpoints are prefixed `/api/v1/` and authenticated with `Authorization: Bearer <JWT>`. "Any" = any authenticated user; "+ scope" = further limited to visits the user can see (§3.2; out-of-scope visits return 404). Gates read the primary role only.

#### 6.5.1 Visitors

| Method | Path | Description | Access |
|--------|------|-------------|------|
| `GET` | `/visitors?search=&page=&page_size≤100` | Search by first / last / company / email (each column separately) | Any |
| `GET` | `/visitors/{id}` | Visitor details (no visit history) | Any |
| `POST` | `/visitors` | Create visitor | Any |
| `PATCH` | `/visitors/{id}` | Update visitor, incl. `id_verified` | Any (auditors not blocked) |
| `POST` | `/visitors/{id}/confirm-training` | Stamp training, close open training tasks | Configured HR contact or system_admin |
| `POST` | `/visitors/{id}/confirm-ppe` | Stamp PPE issuance, close open PPE tasks | Configured PPE contact or system_admin |

#### 6.5.2 Visits

| Method | Path | Description | Access |
|--------|------|-------------|------|
| `GET` | `/visits?status=&host_id=&date_from=&date_to=&page=&page_size≤100` | List (no area / text search) | Any + scope |
| `GET` | `/visits/active` | On-site visits | Any + scope |
| `GET` | `/visits/{id}` | Detail with primary visitor and companions (declarations and badge prints are separate calls) | Any + scope |
| `POST` | `/visits` | Create; auto-submits for approval by area (§6.2.1) | Any |
| `PATCH` | `/visits/{id}` | Edit date / times / purpose / area / count / plate / notes / `ppe_issued` while Confirmed or Pending (no UI) | Creator, dept_manager of Host's dept, system_admin |
| `POST` | `/visits/{id}/cancel` | Cancel; also cancels the in-flight approval | Creator, dept_manager of Host's dept, system_admin |
| `POST` | `/visits/{id}/action` | `approve` / `reject` / `return` proxy to approval-api | Any; approval-api checks the approver |
| `POST` | `/visits/{id}/check-out` | `{badge_returned, ppe_returned, notes}` | Creator, Host, dept_manager of Host's dept, system_admin |
| `POST` | `/visits/batch-checkout` | Check out every on-site visit | system_admin |
| `GET` / `POST` | `/visits/{id}/attachments` | List / upload (proxy to file-api) | Any + scope; auditors cannot upload |

There is **no** `/visits/{id}/check-in` endpoint — check-in happens only through `print-badge`.

#### 6.5.3 Badge

| Method | Path | Description | Access |
|--------|------|-------------|------|
| `POST` | `/visits/{id}/print-badge` | First call = check-in; later calls = reprint (reason required) | Any + scope |
| `GET` | `/visits/{id}/badge-history` | Print history | Any + scope |
| `GET` | `/badge/templates` | Legacy HTML templates (unused by UI) | Any |
| `PUT` | `/badge/templates/{name}` | Legacy template write | system_admin |

#### 6.5.4 Health

| Method | Path | Description | Access |
|--------|------|-------------|------|
| `GET` | `/health-questions` | Current questionnaire | Any |
| `POST` | `/visits/{id}/health-declaration` | Submit (or overwrite) one visitor's declaration: `visitor_id`, answers, `safety_training_confirmed`, optional `signature` | Any + scope, not auditor |
| `GET` | `/visits/{id}/health-declaration` | **List**, one entry per visitor | Any + scope |
| `GET` | `/health-declarations?from=&to=&q=` | Browse declarations across visits | Any + scope |

#### 6.5.5 Audit, Dashboard, Reports

| Method | Path | Description | Access |
|--------|------|-------------|------|
| `GET` | `/audit-logs?user_id=&action_type=&entity_type=&entity_id=&from=&to=` | Query | auditor, system_admin |
| `GET` | `/audit-logs/export` | CSV | auditor, system_admin |
| `GET` | `/dashboard/overview` | On-site, today, 7 days, overdue (plant-wide) | Any |
| `GET` | `/dashboard/compliance` | GMP visits, pass rate, unreturned badges, after-hours (plant-wide) | Any |
| `GET` | `/reports/cfia-visit-log?from=&to=` | CSV | auditor, system_admin |
| `GET` | `/reports/gmp-area-summary?from=&to=` | CSV | auditor, system_admin |

#### 6.5.6 Admin

| Method | Path | Description | Access |
|--------|------|-------------|------|
| `GET` / `PUT` | `/admin/quality-managers` | QM roster (ordered UUID list) | system_admin |
| `GET` / `PUT` | `/admin/notification-contacts` | Training / PPE emails | system_admin |
| `GET` / `PUT` | `/admin/health-questions` | Questionnaire (version + questions, replaced as a whole) | system_admin |
| `GET` / `PUT`, `POST` | `/admin/smtp-settings`, `/admin/smtp-test` | VMS-local SMTP | system_admin |
| `GET` / `PUT` | `/admin/badge-config` | Structured badge configuration | system_admin (⚠ the badge page also reads this — §12 D-05) |
| `POST` | `/admin/run-scheduled-jobs` | Run the scheduler once | system_admin |
| `GET` / `PATCH` / `DELETE`, `POST` | `/admin/entities`, `/admin/{entity}[/{id}]`, `/admin/{entity}/bulk-delete` | Portal Data Maintenance: edit any field incl. status, **hard-delete** visits / visitors (cascades to prints, declarations, tasks); logged in `admin_audit_log` | system_admin |

#### 6.5.7 Cross-Service Calls

| Call Direction | Endpoint | Purpose |
|----------------|----------|---------|
| VMS frontend → epms-api | `GET /api/v1/users/directory?search=` | Host search (returns id, full_name, email, department_id, role, department_name). vms-api itself does not call epms-api |
| VMS frontend → epms-api | `GET /api/v1/tasks?is_completed=false` | VMS Task Inbox |
| vms-api → approval-api | `POST /approval/v1/approvals/vms_visit/{visit_id}/action` — `submit` / `cancel` / `approve` / `reject` / `return` | Approval |
| vms-api ← approval-api | no callback; approval-api writes `vms_visits` directly (`approval_status`, `status` on final approve) | Status sync |
| Portal → approval-api | `GET/PUT /approval/v1/workflows/vms_visit` | Approval Routing editor |
| vms-api → file-api | upload with `entity_type="vms_visit"`; listing reads the shared `file_metadata` table | Attachments |

### 6.7 Docker Compose Integration

```yaml
# docker-compose.dev.yml — new vms-api service

  vms-api:
    <<: *python-base
    build:
      context: ./vms-api
      dockerfile: Dockerfile
    container_name: uniops_vms_api
    ports:
      - "8008:8008"     # 8007 is taken by budget-api — VMS uses 8008
    volumes:
      - ./vms-api:/app
    environment:
      DATABASE_URL: postgresql+asyncpg://epms:epms_dev@postgres:5432/epms
      POSTGRES_HOST: postgres
      JWT_SECRET_KEY: change-me-dev-only
      JWT_ALGORITHM: HS256
      SERVICE_NAME: vms-api
      PORT: "8008"
      DEBUG: "true"
      ALLOWED_ORIGINS: '["http://localhost:5173","http://localhost:5174","http://localhost:5175","http://localhost:5176"]'
      EPMS_API_URL: http://epms-api:8000/api/v1
      APPROVAL_ENGINE_URL: http://approval-api:8003/approval/v1
      FILE_SERVER_URL: http://file-api:8005/files/v1
    depends_on:
      postgres:
        condition: service_healthy
      epms-api:
        condition: service_healthy
      approval-api:
        condition: service_healthy
    command: uvicorn app.main:app --host 0.0.0.0 --port 8008 --reload
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8008/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s
```

> **CORS addition required on existing services** — append `"http://localhost:5176"` to the `ALLOWED_ORIGINS` env in each of: `epms-api`, `approval-api`, `file-api`, `expense-api`, `budget-api`, `mdm-api`. Also update default in [epms-api/app/core/config.py:20-25](epms-api/app/core/config.py#L20-L25) so non-Docker dev environments work.

VMS services communicate with the existing stack over the Docker Compose internal network (no extra config needed).

```yaml
# docker-compose.dev.yml — new vms-frontend service

  vms-frontend:
    image: node:20-alpine
    container_name: uniops_vms_frontend
    restart: unless-stopped
    working_dir: /app
    ports:
      - "5176:5176"
    volumes:
      - ./vms:/app
      - vms_node_modules:/app/node_modules
    environment:
      VITE_API_URL: http://localhost:8008
      VITE_PORTAL_URL: http://localhost:5174
      VITE_EPMS_API_URL: http://localhost:8000     # for /users/directory calls
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5176"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:5176"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

# volumes addition:
volumes:
  vms_node_modules:
```

> **Also**: portal-frontend env must add `VITE_VMS_URL: http://localhost:5176` and `VITE_VMS_API_URL: http://localhost:8008` so Portal can build the launcher link + Task Inbox deep links (see §6.8).

### 6.8 Frontend Route Design (as built, V2.7)

VMS runs inside the shared UniOps tab shell (`@uniops/shell`): every page opens as a tab; Dashboard is a pinned tab. Routes (`vms/src/app/routes.tsx`), sidebar menu gating by primary role (`vms/src/components/layout/AppLayout.tsx`):

| Path | Tab / page title | Sidebar | Who |
|---|---|---|---|
| `/dashboard` | Dashboard | yes (pinned tab) | everyone |
| `/tasks` | Task Inbox | yes, with count | everyone |
| `/` | Today's Visits | yes | everyone (scoped) |
| `/all` | All Visits (latest 50) | yes | everyone (scoped) |
| `/active` | On-Site Now | yes | everyone (scoped) |
| `/new` | New Visit | yes | everyone |
| `/check-out` | Check Out | yes | everyone |
| `/audit-log` | Audit Log | "Compliance" group | auditor, system_admin |
| `/reports` | Reports ("Compliance Reports") | "Compliance" group | auditor, system_admin |
| `/health-declarations` | Health Declarations | "Compliance" group | auditor, system_admin (menu); API scoped |
| `/admin/*` | VMS Admin — tabs Quality Managers, Notification Contacts, Email Settings, Health Questions, Badge | "Admin" group | system_admin |
| `/:visitId` | Visit *First Last* | — (from lists / tasks) | scoped |
| `/badge/:visitId` | Badge *First Last* | — (from "Print badge & check in") | scoped |
| `/visitor/:visitorId/compliance` | Compliance *First Last* | — (from training / PPE tasks) | everyone; confirm restricted |

The V2.6 `/check-in` route does not exist. Sidebar footer: "Back to UniOps Portal"; user menu: "Portal Home", "Sign Out". Without a token the app shows "Redirecting to portal for authentication…" and returns via Portal login (`#__session=` hash, stored as `vms-auth`); any 401 sends the user to the Portal logout.

### 6.9 Portal Integration (Module Launcher + Task Inbox)

- **Launcher**: Portal sidebar MODULES → "VMS", and the Portal home module card "VMS — Visitor appointments, badge printing, on-site tracking". Visible to every user (no permission gate).
- **Task Inbox**: VMS tasks show in Portal "My Task Inbox" under the **Visitor** group (Visits / Training Confirmations / PPE Confirmations), tag "VISITOR", title as in §6.2.1(4.1). A row click opens VMS at `/{visitId}` or `/visitor/{visitorId}/compliance`. **The Portal row has no approve buttons** — approval happens on the VMS visit page. ⚠ Portal de-duplicates rows by document number, which can hide a training or PPE task when the same person is both contacts (§12 D-13); the VMS Task Inbox shows all.
- **Approval chain**: Portal Admin → Approval Routing → "VMS Visit" tab edits `company_config.workflow_defs["vms_visit"]`. The Quality Manager roster itself lives in VMS Admin.
- **Branding**: company name, logo and the VMS tagline come from Portal Admin → Company Settings.

---

## 7. Non-Functional Requirements

### 7.1 Performance

| Metric | Target | Notes |
|--------|--------|-------|
| Page load time | < 2 seconds | First contentful paint for all pages |
| API response time | < 500ms | Core APIs (check-in, query) |
| Badge print delay | < 3 seconds | From click to print dialog ready |
| Concurrent users | 50+ | Simultaneous online operations |
| Data query | < 1 second (within 100K records) | Index-supported queries |

### 7.2 Availability

| Metric | Target |
|--------|--------|
| System uptime | 99.5% (business hours coverage) |
| Planned maintenance window | Off-hours (weekends / nights) |
| Backup frequency | Daily incremental + weekly full |
| RTO (Recovery Time Objective) | < 4 hours |
| RPO (Recovery Point Objective) | < 1 hour |

### 7.3 Security

| Requirement | Description |
|-------------|-------------|
| Authentication | Username / password login, extensible to LDAP / AD |
| Password policy | Minimum 8 characters; uppercase + lowercase + digit + special character; 90-day expiry |
| Session management | Auto-logout after 30 minutes of inactivity |
| Data transmission | Full-site HTTPS (TLS 1.3) |
| Data at rest | Sensitive fields (e.g., health declaration details) encrypted |
| SQL injection | ORM parameterized queries |
| XSS protection | Frontend output encoding, CSP policy |
| Access control | Role-Based Access Control (RBAC), least-privilege principle |
| Audit log protection | Immutable logs; written to independent storage |

### 7.4 Usability

| Requirement | Description |
|-------------|-------------|
| Host operation efficiency | From finding appointment to print completion: < 30 seconds (including browser print dialog confirmation) |
| QR check-out efficiency | Mobile scan to confirmation: < 10 seconds |
| Badge legibility | Name clearly readable from 2 meters; QR code reliably scannable by phone camera |
| Responsive design | Desktop (1920×1080), Tablet (iPad 10.2"), Mobile browser (Safari 17+, Chrome 90+ Mobile, 375~414px width) |
| Mobile camera | Check-Out page uses `getUserMedia` API for real-time QR scanning; HTTPS required |
| Operation feedback | Clear success / failure indicators for all actions |
| Error handling | Friendly error pages with guidance on next steps |
| Dual-scenario support | Same frontend codebase supports both desk printing and public PC printing |

### 7.5 Compatibility

| Platform | Supported Range |
|----------|-----------------|
| Desktop browsers | Chrome 90+, Edge 90+, Firefox 90+, Safari 15+ |
| Mobile browsers | Safari 17+ (iOS), Chrome 90+ (Android/iOS); must support `getUserMedia` API (QR check-out) |
| Server OS | Ubuntu 22.04 LTS / Windows Server 2019+ |
| Printer | Standard office printer (laser / inkjet), A4 / Letter paper; no dedicated label printer required |
| Scanner (desktop) | Standard USB barcode scanner (keyboard-emulation mode), or manual QR code entry |

---

## 8. Canadian Environment Adaptation

### 8.1 Language Support

| Requirement | Description |
|-------------|-------------|
| Interface language | English (primary), with French (Français) toggle |
| Badge content | Bilingual templates (EN + FR) |
| System notifications | Sent per user language preference (EN / FR) |
| Audit reports | Both English and French report templates |

### 8.2 Regulatory Compliance

| Regulation | Adaptation Requirement |
|------------|------------------------|
| **CFIA (Canadian Food Inspection Agency)** | Complete retention of records for visitors entering food production areas; support CFIA audit export |
| **HACCP** | GMP area visitor health declarations and training confirmations as HACCP system appendices |
| **PIPEDA (Personal Information Protection and Electronic Documents Act)** | Collection and storage of visitor personal information must comply with PIPEDA; explicitly prohibit biometric data collection (e.g., photos); defined data retention period |
| **CASL (Canada's Anti-Spam Legislation)** | Email notifications to visitors must include unsubscribe option |
| **Bill 96 (Quebec Charter of the French Language)** | If deployed in Quebec, French interface is mandatory |

### 8.3 Localization Settings

| Setting | Default |
|---------|---------|
| Timezone | America/Toronto (Eastern Time) |
| Date format | YYYY-MM-DD |
| Time format | 24-hour (HH:MM) |
| Currency | CAD (if fee-based scenarios arise) |
| Paper size | Letter (8.5"×11") |

### 8.4 Food Safety Special Requirements

```
                             +------------------------+
                             | GMP Area Access         |
                             | Compliance Process      |
                             +-----------+------------+
                                         |
                    +--------------------+--------------------+
                    |                    |                    |
                    v                    v                    v
            +--------------+    +--------------+    +--------------+
            |  Health      |    |  Food Safety |    |  Gowning/PPE |
            |  Declaration |    |  Training    |    |  Confirmation|
            |              |    |  Ack         |    |              |
            +------+-------+    +------+-------+    +------+-------+
                   |                   |                   |
                   v                   v                   v
              [E-Signature]       [E-Signature]        [Host Confirms]
                   |                   |                   |
                   +-------------------+-------------------+
                                       |
                                       v
                              +----------------+
                              | OK Allowed GMP |
                              +----------------+
```

---

## 9. Implementation Roadmap (UniOps Integrated)

### Phase 1 — MVP (6-8 Weeks) Core Features

| Week | Deliverable | Repo |
|------|-------------|------|
| Week 1 | vms-api scaffold (FastAPI + SQLAlchemy + Alembic, port 8008); `vms_*` tables in `public` schema migration | `vms-api/` |
| Week 2 | All ORM models + Pydantic schemas defined, `/health` liveness endpoint; **epms-api `GET /api/v1/users/directory` endpoint added** | `vms-api/app/models/` `app/schemas/` + `epms-api/app/api/v1/users.py` |
| Week 3 | visitors + visits CRUD endpoints; JWT auth (local validation with shared `JWT_SECRET_KEY`); Host-open access (any authenticated UniOps role); CORS additions for port 5176 across all backend services | `vms-api/`, all `*-api/` ALLOWED_ORIGINS |
| Week 4 | VMS frontend app scaffold: vms/ (Vite+React, port 5176), standalone AppLayout, `vms-auth` session handoff from Portal, route config, VisitListPage, VisitCreatePage; **Portal `NAV_SECTIONS` + `resolveHref` + `vmsHref` changes** | `vms/`, `portal/` |
| Week 5 | Badge printing module (API `print-badge` includes auto check-in + frontend BadgePrintPage) — HTML/CSS templates + browser `window.print()`, no printer driver needed | `vms-api/` + `vms/` |
| Week 6 | QR check-out module (API + frontend CheckOutPage) — desktop manual/barcode scanner + mobile `getUserMedia` camera scan | `vms-api/` + `vms/` |
| Week 7 | Audit logs + basic dashboard, docker-compose.dev.yml integration: vms-api + vms-frontend | `vms-api/` + `vms/` + `docker-compose.dev.yml` |
| Week 8 | Integration testing, VMS end-to-end workflow validation, bug fixes | All repos |

**Phase 1 Deliverable**: VMS module available within UniOps platform (standalone frontend `vms/`, port 5176), supporting appointment creation, badge printing (= check-in), QR code check-out, and basic querying. Full Host self-service closed loop functional.

### Phase 2 — Enhanced Features (4-6 Weeks, Deep UniOps Integration)

| Week | Deliverable | Repo |
|------|-------------|------|
| Week 9-10 | Health declaration + e-signature; **approval-api `vms_visit` doc_type added** (thin `Visit` mirror model, `_DOC_META` / `_WORKFLOW_DEFAULTS` / `_DOC_TYPES` entries, quality_manager special-case resolution); VMS Admin panel — Quality Manager roster + Notification Contacts pages | `vms-api/`, `approval-api/`, `vms/` |
| Week 11-12 | **Portal Task Inbox `vms_visit` integration** (doc_type mapping + deep-link); **Portal Admin Workflow Defs editor — add VMS Visit tab**; CFIA / GMP audit report generation, file upload integration with file-api (`entity_type="vms_visit"`) | `portal/`, `vms-api/`, `file-api/` (configuration) |
| Week 13-14 | Overtime alert notifications (email; Teams routing per `notification_channel` deferred to Phase 3), batch operations, data export to Excel / PDF | `vms-api/` + `vms/` |

**Phase 2 Deliverable**: VMS module with full compliance capabilities — approvals, health declarations, and audit reports all functional.

### Phase 3 — Advanced Features (4-6 Weeks, Optional)

| Week | Deliverable |
|------|-------------|
| Week 15-16 | Access control system integration (optional), self-service check-out kiosk |
| Week 17-18 | Data statistics and analysis reports, trend charts |
| Week 19-20 | French interface, Quebec compliance adaptation, performance optimization |

---

## 10. Success Metrics (KPIs)

### 10.1 Efficiency Metrics

| Metric | Current Baseline (est.) | Target | Measurement |
|--------|:-----------------------:|:------:|-------------|
| Average visitor check-in time | ~3 minutes (paper registration) | < 30 seconds | System records time from search to print completion |
| Monthly audit data collation | ~8 hours | < 30 minutes | One-click audit report export |
| Daily on-site headcount | ~15 minutes (manual count) | Real-time | System dashboard |
| Badge error rate | ~5% (handwriting errors) | < 0.5% | Badge info vs. registration info match rate |

### 10.2 Compliance Metrics

| Metric | Target |
|--------|:------:|
| GMP area visitor health declaration compliance rate | 100% |
| Visitor badge return rate | > 98% |
| Audit log completeness | 100% |
| Visitor record retention period | >= 3 years |

### 10.3 Satisfaction Metrics

| Metric | Target |
|--------|:------:|
| Host operational satisfaction | >= 4.0/5.0 |
| Visitor experience satisfaction | >= 4.0/5.0 |

---

## 11. Appendix

### 11.1 Glossary

| Term | Full Form | Description |
|------|-----------|-------------|
| VMS | Visitor Management System | Digital visitor management system |
| Host | Host | The visited employee who invites and receives the visitor |
| Check-In | Check-In | Visitor arrival registration |
| Check-Out | Check-Out | Visitor departure registration |
| Badge | Badge / Label | Visitor identification badge / name tag |
| GMP | Good Manufacturing Practice | Clean zone for food production |
| CFIA | Canadian Food Inspection Agency | Federal food safety regulator |
| HACCP | Hazard Analysis Critical Control Point | Food safety management system |
| PPE | Personal Protective Equipment | Hard hat, protective clothing, etc. |
| RBAC | Role-Based Access Control | Permission model based on roles |
| PIPEDA | Personal Information Protection and Electronic Documents Act | Canadian privacy law |

### 11.2 References

- CFIA Food Business Inspection Requirements: https://inspection.canada.ca/
- HACCP General Principles: https://www.canada.ca/en/health-canada/services/food-nutrition/food-safety/hazard-analysis-critical-control-point.html
- PIPEDA Privacy Guidance: https://www.priv.gc.ca/
- CSA Z460 Lockout/Tagout Standard (applicable to contractor access to equipment areas)

### 11.3 Next Steps

1. **Requirements Confirmation Meeting**: Review this PRD with IT, department head representatives, production department representatives, and compliance
2. **Printer Confirmation**: Confirm office printer models and paper specs; test browser printing results
3. **Access Control System Survey**: Confirm existing access control system brand and API capability; evaluate Phase 3 integration feasibility
4. **Data Migration**: Confirm whether historical visitor records (paper register data) need to be imported
5. **Pilot Scope**: Start with 2-3 Hosts in the office area for a 2-week pilot; collect feedback before full rollout

### 11.4 Items to Confirm

| # | Item | Confirming Party | Status (V2.7) |
|:-:|------|:----------------:|:------:|
| 1 | Current paper visitor registration form template | Administration | Open |
| 2 | GMP health declaration question checklist | QC / Quality Assurance | Built with 4 default questions (symptoms in 24 h, open wounds, infectious-disease contact, food allergens); Admin can edit. **QA sign-off of the wording still open** |
| 3 | Office printer model and paper (A4 / Letter) | IT | Badge prints landscape Letter; printer model open |
| 4 | Integration with the access card system | IT | Not built; open |
| 5 | CFIA audit report format | Compliance | Interim 16-column CSV; final layout open |
| 6 | Server resources for vms-api (8008) | IT | ✅ Deployed in production |
| 7 | Initial Quality Manager roster | QA Leadership + IT | ✅ Configured: 1 active user (checked 2026-09-30). One person = single point of failure for every GMP / Lab visit |
| 8 | QM assignment policy | QA Leadership | ✅ Decided by the code: first active user in the roster, single approver |
| 9 | Training Contact (HR) and PPE Contact (Janitor) emails | HR / Administration | ✅ Configured; both map to active users (checked 2026-09-30) |
| 10 | Default workflow chain for `vms_visit` | IT / Management | ✅ Engine default = `dept_manager` only; **production = `dept_manager` → `quality_manager`** |
| 11 | Approval by access area | Compliance / IT | ✅ See §6.2.1(3) |
| 12 | epms-api `GET /api/v1/users/directory` | IT | ✅ Built (also returns role and department_name) |
| 13 | Audit log immutability mechanism | Compliance / IT | ✅ DB-level REVOKE (migration 0002), effective for the `epms` login role |
| 14 | *(new)* Should auditors be able to create visits, verify ID and print badges (= check visitors in)? The code does not block them | Compliance | Open |
| 15 | *(new)* Should Entire Plant visits require a health declaration like GMP / Laboratory? (§12 D-08) | QA | Open |
| 16 | *(new)* Is Portal Data Maintenance hard-delete of visit records acceptable under CFIA / PIPEDA retention? | Compliance | Open |
| 17 | *(new)* Should additional roles from the Portal permission matrix apply in VMS (today only the primary role counts)? | IT | Open |

> **Integration plan reconciliation (V2.3)**: All items below are now ✅ resolved and require **no further confirmation**, because they were architecturally decided after reviewing the actual UniOps codebase:
>
> - **Port** — 8008 (8007 was already taken by budget-api)
> - **Schema** — `public` with `vms_*` prefix (no separate `vms` schema; consistent with all other UniOps services)
> - **Approval engine integration** — cfm-style extension of `_DOC_META` / `_WORKFLOW_DEFAULTS` / `_DOC_TYPES`, with a thin `Visit` mirror model in approval-api (same pattern that approval-api already uses for `BudgetPlan`, `PurchaseRequest`, etc.)
> - **Quality Manager** — VMS-local role; not added to UniOps `VALID_ROLES`; resolved per-visit via `vms_visits.quality_approver_id` set by vms-api at submit time
> - **User master** — shared `public.users`; any authenticated UniOps user can be a Host
> - **Portal integration** — VMS in MODULES section (sibling to EPMS/OA); Portal Task Inbox aggregates `doc_type="vms_visit"` tasks with deep-link to vms frontend

### 11.5 Production Snapshot (read-only check, 2026-09-30)

| Item | Value |
|---|---|
| Approval chain `workflow_defs["vms_visit"]` | Department Manager → Quality Manager |
| Quality Manager roster | 1 user, active |
| Training / PPE contacts | both set; both match active users |
| SMTP | VMS-local settings saved |
| Health questions / badge configuration | not customised — code defaults in use |
| Usage | 13 visits (2026-07-06 → 2026-09-15), 15 visitors: 11 departed (10 Office, 1 GMP), 2 no-show (1 Office, 1 Non-GMP); nothing pending or on-site |

---

## 12. Known Defects and Gaps (V2.7)

Found in the V2.7 code audit (`origin/main @bb8d0a9a`). Severity reflects compliance and data impact. V2.8 records the fix for each on branch `vms/known-issues-fixes` — **not yet released**; until it is, production behaves as in "Effect before V2.8".

| ID | Severity | Defect | Where | Effect before V2.8 | Status (V2.8, branch `vms/known-issues-fixes`) |
|---|:---:|---|---|---|---|
| D-00 | **High** | *(found while fixing D-01)* approval-api read `approval_step_idx < len(workflow)` before branching on the action (since `e9f39159`, 2026-09-13); `vms_visits.approval_step_idx` is NULL until the first submit, so **every** VMS submit raised TypeError | `approval-api/app/crud/engine.py` `_step_of` | Combined with D-01: every new non-office visit since that commit is confirmed **without approval**. No non-office visit was booked in production after 2026-08-19, so none slipped through yet | ✅ Fixed — NULL step = 0; regression test |
| D-01 | **High** | If submitting a new visit for approval fails for any reason (no dept_manager in the creator's department, approval-api down, engine 4xx), vms-api resets the visit to `confirmed` and returns 502 | `vms-api/app/api/v1/visits.py:246-257` | The visit exists, is Confirmed and prints a badge — **approval bypassed**, incl. GMP. The user sees an error and may create a duplicate | ✅ Fixed — visit stays Pending Approval ("draft"); "Submit for approval" retries |
| D-02 | **High** | `PATCH /visits/{id}` can change `access_area` without re-running approval | `vms-api/app/crud/visit.py:270-278` | Book Office (auto-confirmed) then patch to GMP → no approval. API only; no UI path | ✅ Fixed — area locked while approval is in flight; a confirmed visit may only move to the same or lower approval tier |
| D-03 | **High** | "Return for edit" is a dead end: `approval_status='returned'` but `status` stays Pending Approval; no edit / resubmit path (and the engine only accepts submit from `draft`); the requester's "Revise" task shows the approval panel whose buttons fail; cancelling fails silently in the engine (`valid_cancel` excludes `returned`) so the Revise task never closes | `approval-api/app/crud/engine.py:197-209`, `vms-api/app/services/approval.py:139-160` | Visit stuck until cancelled; orphan task. **Training guidance: use Reject, not Return** | ✅ Fixed — Edit visit + Submit for approval; engine accepts submit / cancel from `returned`; Revise task closes |
| D-04 | Medium | Re-filing a health declaration overwrites the earlier answers, result and signature in place; the audit log keeps only `result=` | `vms-api/app/crud/health_decl.py:125-129` | A Failed visitor can be re-declared Passed with no record of the original answers (conflicts with VMS-AU-013) | ✅ Fixed — the replaced declaration (answers, result, signature) is written in full to the audit log |
| D-05 | Medium | The badge page reads `GET /admin/badge-config`, which is system_admin-only; everyone else silently falls back to the code defaults | `vms/src/services/api.ts:936-941`, `vms-api/app/api/v1/admin.py:371-380` | Admin badge customisation applies only to badges printed by an admin. No effect yet (production uses defaults) | ✅ Fixed — printing reads `GET /badge/config` (any user) |
| D-06 | Medium | "Prepare PPE" task links to the visit page, but the Janitor usually cannot see the visit | `vms-api/app/crud/visit.py:210-234` | Janitor sees "Visit not found"; the email carries the details | ✅ Fixed — anyone holding a task on a visit can open it |
| D-07 | Medium | "Today" is taken from the UTC date in the frontend (Today's Visits, Dashboard, New Visit default date, Reports default range) and in the dashboard API | `vms/src/pages/VisitListPage.tsx:8`, `DashboardPage.tsx:18`, `VisitCreatePage.tsx:170`, `ReportsPage.tsx:39-40`; `vms-api/app/api/v1/dashboard.py:48,69-70` | After 20:00 EDT (19:00 EST) lists show tomorrow and New Visit defaults to tomorrow's date | ✅ Fixed — frontend `localToday()`; dashboard API uses plant time |
| D-08 | Medium | Entire Plant is treated inconsistently: QM approval and training / PPE tasks yes; health declaration no; frontend GMP hint and compliance banner no; GMP reports and KPI exclude it | `crud/badge.py:35-37`, `services/compliance.py:36-40`, `services/reports.py:193`, `VisitCreatePage.tsx:268-274` | The broadest-access visit has the weakest health gate | ✅ Fixed (QA decision 2026-10-01: Entire Plant = GMP-grade) — one rule source `services/area_rules.py`, served at `GET /area-rules` |
| D-09 | Medium | A Host who cancels their own pending visit is emailed "Visit rejected by an approver" (derived from code, not reproduced) | `vms-api/app/crud/visit.py:160-182` | Misleading email | ✅ Fixed — cancel sends "Visit cancelled", never "rejected" |
| D-10 | Medium | Approval routes by the **creator's** department; dept_manager visibility uses the **Host's** department | `approval-api/app/crud/engine.py:645-657` vs `vms-api/app/crud/visit.py:228-233` | When someone books for a Host in another department, the approver may get "Visit not found" on their own task | ✅ Fixed — same task-holder rule as D-06 |
| D-11 | Low | No same-approver skip at submit | `approval-api/app/crud/engine.py` | A dept_manager approves their own visits | Kept by decision (2026-10-01) — self-approval is recorded; changing submit-time skip would touch every doc type |
| D-12 | Low | Pending visits never become No Show | `services/scheduled_jobs.py:88-122` | Stale approval tasks can sit in inboxes indefinitely | ✅ Fixed — no-show also closes pending visits, cancels their approval and tasks |
| D-13 | Low | Portal inbox de-duplicates by document number; approval task title repeats the visitor text | `portal/src/pages/PortalHome.tsx:613-617`; engine title build | Training and PPE tasks for the same visitor collapse into one row when one person holds both roles | ✅ Fixed — Portal keys VMS tasks by task id; title now "Approve visitor visit: <visitor> — <area>, <date>" |
| D-14 | Low | Attachment `download_url` uses the internal file-api URL and opens without a Bearer token (not reproduced) | `vms-api/app/services/attachments.py:55` | Downloads likely fail from the browser | ✅ Fixed — download through `GET /visits/{id}/attachments/{file_id}/download` |
| D-15 | Low | Audit log "Action type" filter lists only a subset of the logged action types (no approve / reject / return, no_show, health_decl, confirm_*, admin.*, export_*); "Entity type" lacks `vms_config` and `report` | `vms/src/pages/AuditLogPage.tsx:306-314` | Those events can only be found unfiltered | ✅ Fixed — filters come from `GET /audit-logs/facets` (types actually logged) |
| D-16 | Low | Disabled "Print badge & check in" always says "Verify visitor ID first", even when the real blocker is a missing or failed health declaration | `vms/src/pages/VisitDetailPage.tsx:111` | User confusion | ✅ Fixed — the page lists the real blockers |
| D-17 | Low | Dashboard nudge "N visit(s) waiting on your approval" counts all VMS tasks | `vms/src/pages/DashboardPage.tsx:43-61` | Wrong wording for HR / Janitor / Hosts | ✅ Fixed — "waiting on your approval" only when every task is an approval |
| D-18 | Low | Overdue / escalation emails print UTC times; "after-hours" hour is extracted in the DB session time zone | `services/notifications.py` `_fmt_dt`; `services/reports.py:260-265` | Times off by 4–5 h | ✅ Fixed — emails in plant time (e.g. "2026-10-01 14:30 EDT"); after-hours evaluated in America/Toronto |
| D-19 | Low | `POST /admin/run-scheduled-jobs` does not take the advisory lock | `services/scheduler.py` | A manual run overlapping a tick could double-send | ✅ Fixed — manual run shares the lock; returns 409 while a run is in progress |
| D-20 | Policy | Portal Data Maintenance can hard-delete visits and visitors | `vms-api/app/api/v1/admin.py` generic entity routes | Conflicts with the ≥3-year retention goal (§10.2) — see §11.4 #16 | ✅ Fixed by decision (2026-10-01) — VMS records are edit-only in Data Maintenance (409 on delete; Portal hides Delete) |
| D-21 | Gap | Only the primary role counts in VMS; no `vms.*` permissions | `core/deps.py`, `AppLayout.tsx:53-54` | Additional roles granted in Portal do nothing in VMS | Deferred by decision (2026-10-01) — separate project (authz matrix + `vms.*` codes) |
| D-22 | Gap | Visitor search matches one column at a time | `vms-api/app/crud/visitor.py:21-30` | Full-name search finds nothing → duplicate visitors | ✅ Fixed — every word must match some column |

---

## 13. V2.8 Behaviour Changes (branch `vms/known-issues-fixes`, pending release)

| Area | Before | After |
|---|---|---|
| Approval hand-off fails at create | Visit confirmed, printable without approval | Saved as Pending Approval ("draft"); banner "Not sent for approval yet" + **Submit for approval** (`POST /visits/{id}/submit`) |
| Return for edit | Dead end | Banner shows who returned it and their comment; **Edit visit**, then **Submit for approval**; a visit edited down to Office is confirmed without approval |
| Who can edit / cancel | Creator, dept_manager, admin | + the **Host**; the page shows those buttons only to people who may use them (`can_manage`) |
| Changing access area | Any change, no re-approval | Locked while approval is in flight; a confirmed visit only to the same or a lower approval tier |
| Cancel | Engine refused Host / manager cancels → stale tasks; Host emailed "rejected" | vms-api closes the visit's tasks; Host emailed "Visit cancelled" when someone else cancels |
| Entire Plant | QM approval, no health declaration | Same as GMP: health declaration for every visitor; counted in GMP / Lab reports and KPIs |
| Area rules | Copied in five places | One source (`services/area_rules.py`); `GET /api/v1/area-rules` drives the frontend hints |
| Health declaration re-file | Overwrote the previous answers | Previous answers, result and signature kept in full in the audit log |
| Visibility | Creator / Host / QM / dept of Host | + anyone holding a task on the visit (Janitor "Prepare PPE", cross-department approver) |
| Pending visit past arrival + 2 h | Stayed pending | No Show; approval cancelled; tasks closed |
| Badge layout | Admin configuration applied only to admin prints | `GET /api/v1/badge/config` for every printer |
| Times | UTC "today", UTC in emails, after-hours in DB time zone | Plant time (America/Toronto) throughout |
| Attachments | Link to internal file-api, no token | `GET /visits/{id}/attachments/{file_id}/download` through vms-api |
| Visitor search | One column at a time | Every word must match some column ("John Smith" works) |
| Audit log filters | Hard-coded, outdated | `GET /api/v1/audit-logs/facets` (types actually logged) |
| Data Maintenance | Hard delete of VMS records | Edit-only (`allow_delete: false`; 409 on delete; Portal hides Delete) |
| Portal inbox | De-duplicated by document number (hid tasks); title repeated the visitor | VMS tasks keyed by task id; "Approve visitor visit: <visitor> — <area>, <date>" |
| Approval engine | NULL `approval_step_idx` crashed every VMS submit (since `e9f39159`) | NULL treated as step 0 |

**Release notes**: no database migration. Services touched: vms-api, approval-api, vms (frontend), portal (frontend), epms-api (guide-layer text only). The training deck's "Known issues" slide describes production **before** this release and must be revised when it ships.

---

**Document Version**: V2.8  
**Creation Date**: April 2026 (First Draft) / May 2026 (V2.0 — UniOps Integration) / May 2026 (V2.1 — Role Model Refactoring) / May 2026 (V2.2 — Approval Workflow Customization + Notification Contacts) / May 2026 (V2.3 — Integration Reconciliation) / June 2026 (V2.4 — Multi-visitor visits + intake-flow tweaks) / June 2026 (V2.5 — PPE + compliance + notification workflow) / June 2026 (V2.6 — as-built reconciliation + scheduler) / September 2026 (V2.7 — full code audit)  
**Document Status**: V2.7 — as-built. Requirement status columns and §12 reflect `origin/main @bb8d0a9a`.  
**Next Review Date**: after the §12 High items are fixed  

---


*This document V1.0 was generated by DeepSeek Agent AI in April 2026, based on 5 core requirements (visitor pre-registration, on-site registration, badge printing, departure status update, audit) provided by the user, with further elaboration.*  
*V2.0 (May 2026) rewrote the technical chapters (§5, §6, §9) to align with the actual UniOps platform architecture (Monorepo, microservices, Docker Compose, React+FastAPI stack), ensuring VMS is implemented as a native platform module.*  
*V2.1 (May 2026) confirmed through organizational research: no receptionist/security positions exist; full Host self-service workflow; badge printing switched to browser print + standard office printer; check-out switched to QR code scanning. Role model refactored from 7 roles (including 2 non-existent positions) to 4 roles (all reusing existing UniOps roles).*
*V2.2 (May 2026) added: ① Training/PPE notification contact configuration (Admin panel sets HR-designated Training Contact and Janitor-designated PPE Contact email addresses; system auto-notifies on appointment trigger); ② Visitor approval workflow customization (reuses approval-api, configurable approval chain via workflow_defs, default dept_manager single-step, extensible to multi-step).*  
*V2.3 (May 2026) reconciled against actual UniOps codebase after structural review:*  
*  ① **Port** 8007 → **8008** (8007 is taken by budget-api).*  
*  ② **Schema** moved from a dedicated `vms` schema to `public` with `vms_*` prefix (consistent with all other UniOps services; eliminates Alembic include_schemas complication).*  
*  ③ **approval-api integration** redesigned in cfm-style: add `vms_visit` doc_type to `_DOC_META` / `_WORKFLOW_DEFAULTS` / `_DOC_TYPES`, with a thin `Visit` mirror model in approval-api (same pattern as `BudgetPlan` / `PurchaseRequest`). Corrected endpoint URLs to the real `POST /approval/v1/approvals/{doc_type}/{doc_id}/action`.*  
*  ④ **Quality Manager** declared a VMS-local role (stored in `vms_config.quality_manager_user_ids`), not added to UniOps `VALID_ROLES`. Resolved per-visit via `vms_visits.quality_approver_id` passed to approval-api at submit-time.*  
*  ⑤ **User access** opened: any authenticated UniOps user can be a Host. epms-api gains a new public `GET /api/v1/users/directory` endpoint (the existing `GET /api/v1/users` is system_admin-only).*  
*  ⑥ **Portal integration** specified: VMS added to MODULES section (sibling to EPMS/OA), Task Inbox aggregates `doc_type="vms_visit"` tasks with deep-link to vms frontend, Portal Admin Workflow Defs editor gains a VMS Visit tab.*  
*  ⑦ CORS adjustments listed for all backend services (`ALLOWED_ORIGINS += "http://localhost:5176"`).*
*V2.4 (June 2026) intake-flow tweaks from operator UAT during S2-E rollout:*
*  ① **Visitor.phone** demoted from required (P0) to optional. Reception confirmed walk-ins are usually known to the Host by name + company; demanding a phone number created friction without compliance value. Schema column relaxed to NULLable.*
*  ② **Host field default**: New Visit form pre-populates the Host with the currently logged-in user (the "I'm hosting this visitor" common case). Operator can still pick a different Host via the "Change" affordance.*
*  ③ **Multi-visitor visits** (VMS-PR-030..033): a single appointment now carries one primary visitor + N companions (same date, same Host, same access area). Stored as `vms_visits.additional_visitor_ids JSONB`. Each visitor keeps their own Visitor row (ID verification is per-visitor). Single-visitor visits are unchanged — companions list defaults to empty.*
*  ④ **Badge printing** (VMS-LB-005..007 refined): one badge per visitor — clicking Print Badge for a multi-visitor visit lays out N badge cards with `page-break-after: always` between them and a single browser print dialog covers all of them.*
*  ⑤ **CFIA Visit Log** (VMS-AU-009 refined): emits one row per (visit, visitor) pair instead of one row per visit. A 3-person tour produces 3 rows so the regulator's headcount matches the actual on-site presence. GMP Area Summary still counts visits (= appointments).*
*V2.5 (June 2026) compliance + notification workflow changes from operator UAT (Reception / HR / Janitor):*
*  ① **Host-opt-in per-visitor PPE** (VMS-PR-040..043): Host ticks "PPE needed" at creation and specifies clothing size + footwear/shoe size **per visitor**; stored as `vms_visits.ppe_requested JSONB` (`{items:[{visitor_id,...}], notes}`). PPE is no longer auto-triggered by access area. Janitor receives a per-visitor gear email after approval.*
*  ② **Per-visitor training + PPE compliance** (VMS-CI-020..024): tracked on the Visitor row (`safety_training_confirmed_at/by`, `ppe_issued_at/by`) with a 12-month freshness window. Badge print for GMP/Lab blocks (422) until every visitor on the appointment has both records fresh. HR / Janitor confirm via Task Inbox (`vms_train` / `vms_ppe` doc types) — only the configured contact email (or system_admin) may confirm.*
*  ③ **In-VMS approval + Task Inbox** (VMS-AP-001..004): VMS has its own Task Inbox + Approve/Reject/Return controls on VisitDetail; backed by epms-api `/tasks` filtered to VMS doc types. VMS tasks excluded from the EPMS inbox; Portal unified inbox deep-links them into VMS.*
*  ④ **VMS-local SMTP** (VMS-PR-024): Admin "Email Settings" tab — VMS uses its own SMTP creds (with a Send-test action) instead of the shared EPMS config when set. `vms_config.smtp_settings JSONB`, password masked on read.*
*  ⑤ **Notification deferral**: training/PPE emails + HR/Janitor confirmation tasks fire only **after** the visit is approved (not at create), since every compliance area routes through approval. Avoids pinging contacts for visits that may be rejected.*
*  ⑥ **quality_manager step fix**: the seeded `vms_visit` workflow now actually includes the `quality_manager` step (was missing → QM approval never fired); engine auto-skips it for non-GMP areas; the assigned QM gets visit visibility so the task deep-link resolves.*
*V2.6 (June 2026) reconciliation of the spec with the as-built implementation + scheduler delivery:*
*  ① **Compliance is post-entry, not a print gate** (supersedes V2.5 ② / original VMS-CI-021): badge printing is NOT blocked by training/PPE freshness. A 422 block deadlocks the flow (you can't gear up a visitor who has no badge and isn't on-site). The badge prints unconditionally; at check-in the system opens idempotent HR/Janitor confirmation tasks for stale gates + the HR heads-up email. Freshness is surfaced (VMS-CI-024), not enforced. Per-visitor timestamps, 12-month TTL, and confirm-authorization (VMS-CI-023) unchanged.*
*  ② **Reports ship as streamed CSV** (VMS-AU-011): CFIA Visit Log + GMP Area Summary stream as `text/csv` via `StreamingResponse`. `.xlsx`/PDF deferred until CFIA fixes the mandated layout — Excel opens CSV natively, so a binary writer is churn for no compliance gain. Route/filter contract is final; only serialization is interim.*
*  ③ **Background scheduler implemented** (VMS-PR-012/-019, VMS-CO-009/-010/-011): in-process asyncio loop in vms-api (`app/services/scheduler.py` + `scheduled_jobs.py`), 15-min default tick, Postgres advisory lock for multi-replica safety. Jobs: auto no-show (2h), day-before reminder, 1h-overdue host reminder, 4h-overdue dept_manager escalation. One-shot timestamp flags added to `vms_visits` (`reminder_sent_at`, `overdue_reminder_sent_at`, `overdue_escalated_at`; migration 0013). Manual run via `POST /api/v1/admin/run-scheduled-jobs`; toggle with `SCHEDULER_ENABLED`. VMS-AU-012 (scheduled report auto-email) remains P2 / not yet implemented.*
*V2.7 (September 2026) full code audit against `origin/main @bb8d0a9a` plus a read-only production configuration check:*
*  ① **Status column** on every requirement table (✅ / ◐ / ✗); as-built access-area rule table; permission matrix rewritten to what the code enforces (primary role only, no `vms.*` codes, auditors not blocked from check-in, cancel = creator only, batch check-out = admin only).*
*  ② **Changes since V2.6 documented**: overdue reminder repeats every 24 h until check-out + "Check out visitor" task (`bf2d2f83`); "Prepare PPE" task for the Janitor (`7c75d728`); tab shell, Task Inbox grouping, date-only fix.*
*  ③ **Corrections**: check-in is recorded when the badge page loads (before the print dialog); HR training email fires at check-in regardless of freshness; PPE email is immediate for Office only; no auto-skip at submit; seeded chain is dept_manager only (production has both steps); approval routes by creator's department; health declaration is per visitor and not required for Entire Plant; ID verification is permanent on the visitor record.*
*  ④ **§4 flows, §6.5 endpoints, §6.8 routes, §6.9 Portal, task-type table** rewritten as built; **§11.4** updated, **§11.5** production snapshot and **§12** known defects (D-01 … D-22) added.*
*V2.8 (October 2026) fixes for §12 on branch `vms/known-issues-fixes` (not yet released): see §13. Decisions taken 2026-10-01: Entire Plant is GMP-grade (D-08); self-approval kept (D-11); VMS records edit-only in Data Maintenance (D-20); permission-matrix integration deferred (D-21). New defect D-00 (approval engine NULL step) found and fixed.*
