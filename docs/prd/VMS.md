# VMS (Visitor Management System) — Product Requirements Document

**Version**: V2.13
**Date**: 2026-10-20
**Status**: As-built. Describes the system on the main line as of 2026-10-16. Everything marked **Implemented** is in production; wanted-but-not-built items are marked **Not implemented** and collected in §13 Open Items.
**Scope**: Visitor appointments, approval, health and safety checks, badge printing (= check-in), QR check-out, overdue alerts and the compliance audit trail for Canada Royal Milk ULC's plant and offices.

**What changed in V2.13** (2026-10-20)

- **No Show is marked the next day**, not 2 hours after planned arrival: a visit not checked in by the end of its planned arrival's day (plant time) becomes No Show after midnight. Late arrivals the same day are no longer cut off, and a visit entered after its arrival time on the same day is no longer marked minutes later (VMS-PR-019; closes OI-35). A No Show visit rescheduled in Data Maintenance to today or later is restored.

**What changed in V2.12** (2026-10-20)

- Rescheduling a No Show visit in Data Maintenance undoes the No Show: the visit returns to Confirmed or Pending Approval, and a pending visit's approval and approval task are reopened (§2.5.5, VMS-PR-019).

**What changed in V2.11** (2026-10-20)

- Data Maintenance reschedules a visit through **Planned arrival / Planned departure** (shown and typed in plant time). **Visit date** is read-only there and follows the planned arrival's plant-local day. Before, only Visit date was editable: the Visit List, the visit page and the no-show job kept the old day (§2.5.5, D-24).
- New finding: a visit created with a planned arrival more than 2 hours in the past is marked No Show by the next scheduler run, minutes after it is created (OI-35).

**What changed in V2.10** (2026-10-19)

- Visit lists, the audit log, health declarations and active visits sort by clicking a column header, ascending then descending (column sorting rule in EPMS.md §3.20 (UI-SORT-001…007)); paged lists sort on the server.
- All Visits and Today now page on the server; they used to show only the first 50 / 100 visits.

**What changed in this version (V2.8 → V2.9)**

- The V2.8 fixes (previously "pending release") are released and folded into the body. Every requirement now states current behaviour only; the old "before V2.8" text, the V2.7 defect list and the V2.8 change table are condensed into a short defect history (§12).
- **Approval title length**: the visit title shown as the task's document number is now capped at 40 characters (longer names are shortened with "…"). Before the fix, a visitor with a long name plus company could not be sent for approval at all (§6.2, §12).
- **Resubmission after "Return for edit"** now resumes at the step that returned the visit; approvals already given stand (company rule for all document types, 2026-10-14) (§6.2).
- **Notifications**: VMS approval and compliance tasks are now picked up by the platform's task-notification outbox; they send no email unless an admin turns them on (§6.4).
- **New findings**: an approval delegate cannot open the visit they are asked to approve; any signed-in user can edit any visitor record, including the "ID verified" flag (§13).
- The **visitor self-registration + per-person QR check-in** proposal is *not* built; it is specified separately in VMS-SELF-REGISTRATION.md (§9, §13).
- Implementation-level material (source code listings, directory tree, container definitions) removed; the data model and API are described in business terms.

**Related documents**: EPMS.md (shared approval engine, Task Inbox, notifications, delegation), PORTAL.md (sign-in, module launcher, Portal Task Inbox, Approval Routing, Data Maintenance), VMS-SELF-REGISTRATION.md (proposed self-registration flow), SAFETY.md (EHS module).

**Status legend** used in requirement tables: **Implemented** · **Partial** (built, but differs from the requirement — the difference is stated) · **Not implemented**.

---

## Table of Contents

1. Project Overview
2. Functional Requirements
   - 2.1 Visitor Pre-Registration
   - 2.2 Check-In (via Badge Printing)
   - 2.3 Badge Printing
   - 2.4 Check-Out (via QR Scan)
   - 2.5 Audit Trail and Compliance Reporting
3. User Roles and Permissions
4. User Interaction Flows
5. Data Model
6. Platform Integration
7. Non-Functional Requirements
8. Canadian Environment Adaptation
9. Delivery History and Roadmap
10. Success Metrics
11. Appendix (glossary, references, decisions, production snapshot)
12. Defect History
13. Open Items
14. Revision History

---

## 1. Project Overview

### 1.1 Background

Canada Royal Milk ULC receives a steady flow of external visitors at its plant and offices:

- **Suppliers / Contractors** — equipment and raw-material suppliers, maintenance contractors
- **Regulatory Inspectors** — CFIA and provincial health inspectors
- **Auditors** — third-party certification auditors (HACCP, GMP, ISO)
- **Customers / Partners** — business meetings, plant tours
- **Job Candidates** — interviews
- **Temporary Workers** — short-term contractors, interns

Before VMS, visitors were recorded on paper, outside the UniOps platform. The resulting problems were: records hard to search and trace; no advance visibility of visits; handwritten, inconsistent badges; no departure times and therefore no reliable on-site headcount; no complete evidence chain for food-safety audits (who entered production areas, whether they declared their health and were briefed). The company has **no receptionist or security post**: every hosting task is done by the employee being visited (the Host), so the system must support a complete Host self-service workflow.

### 1.2 Goals

1. **Host self-service** — the Host books the visit, prints the badge (which checks the visitor in) and scans the badge to check the visitor out, without a receptionist.
2. **Efficiency** — pre-registration, one action to print and check in.
3. **Safety and compliance** — visitors to food-production areas pass a health declaration and are briefed before entry; training and PPE are confirmed by the responsible contacts.
4. **Standard badges** — uniform, colour-coded badges printed from the browser on an ordinary office printer and worn in a standard holder. Per PIPEDA, badges carry **no photograph**.
5. **Traceability** — a complete, immutable record of each visit for CFIA / HACCP / GMP audits.
6. **Platform integration** — VMS is a native UniOps module: Portal sign-in, the shared approval engine, the shared Task Inbox and notifications, the file service and the employee directory.
7. **Mobile support** — the Host can scan a badge QR code with a phone camera to check a visitor out.
8. **Live overview** — on-site headcount, today's visits, overdue visitors and compliance indicators.

### 1.3 Target Users

| User | How the role is obtained | Responsibilities | Main features |
|---|---|---|---|
| **Employee (Host)** | Any signed-in UniOps user | Book visits, verify ID, file health declarations, print badges (= check-in), check visitors out | New Visit, visit page, Check Out |
| **Department Manager** | Primary role `dept_manager` | Host rights; approves the Department Manager step; sees visits whose Host is in their department | Visit approval, department visits |
| **Quality Manager** *(VMS-local)* | Listed in VMS Admin → Quality Managers | Approves the second step of GMP-grade visits | Visit approval |
| **HR Training Contact** *(VMS-local)* | Email set in VMS Admin → Notification Contacts → Training | Confirms a visitor's food-safety training (valid 12 months) | Task Inbox → visitor compliance page |
| **Janitor PPE Contact** *(VMS-local)* | Email set in VMS Admin → Notification Contacts → PPE | Stages requested PPE; confirms PPE issuance (valid 12 months) | Task Inbox → visitor compliance page |
| **Auditor** | Primary role `auditor` | Sees all visits; audit log, reports, health declarations | Compliance menu, CSV exports |
| **System Administrator** | Primary role `system_admin` | Everything, VMS Admin panel, plant-wide batch check-out, may act on any approval step | Admin panel |

> **Primary role only.** VMS reads only the user's *primary* role from the sign-in token. Additional roles granted in Portal → Admin → Access Control have **no effect** in VMS, and there are no `vms.*` permission keys. Example: a `requester` who holds `auditor` as an additional role does not see the Compliance menu in VMS. Bringing VMS under the permission matrix is a separate, deferred project (§13).

---

## 2. Functional Requirements

### 2.1 Visitor Pre-Registration

Hosts register upcoming visits in advance; on the day, the visit is found in Today's Visits, on the Dashboard or in the Task Inbox.

#### 2.1.1 Appointment Creation

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-PR-001 | Create a visitor with first and last name (required), company, job title, phone, email | P0 | **Partial** — company is optional (form label "Company (optional)"); job title exists on the visitor record but is not on the inline "Register a new visitor" form. Phone is optional (since V2.4) |
| VMS-PR-002 | Visitor type: Supplier, Contractor, Regulatory Inspector, Third-party Auditor, Customer / Partner, Job Candidate, Other | P0 | **Implemented** — default Supplier |
| VMS-PR-003 | Visit date, planned arrival, planned departure | P0 | **Implemented** — defaults: today (plant time) / 09:00 / 17:00; departure may be cleared. No check that departure is after arrival |
| VMS-PR-004 | Visit purpose: Business Meeting, Equipment Maintenance, Factory Tour, Audit / Inspection, Interview, Delivery, Other | P0 | **Implemented** — default Business Meeting |
| VMS-PR-005 | Host chosen from the employee directory; defaults to the signed-in user, "Change" to pick another | P0 | **Implemented** — Host search needs at least 2 characters |
| VMS-PR-006 | Access area: Office / Lobby, Warehouse, Production (Non-GMP), Production (GMP Clean Zone), Laboratory, Entire Plant | P0 | **Implemented** — six fixed areas; Admin may rename and recolour them on the badge only |
| VMS-PR-007 | High-risk areas trigger extra requirements (approval, health declaration, training / PPE confirmation) | P1 | **Implemented** — see the area rule table in §2.1.5. PPE staging is Host opt-in (VMS-PR-040) |
| VMS-PR-008 | Attachments (visitor ID, work permit, insurance certificate) | P2 | **Partial** — uploaded from the visit page after the visit is created, not on the New Visit form. Allowed file types are enforced by the file service (PDF, images, Office documents). Attachments open through VMS for anyone who can see the visit. No delete. Auditors cannot upload |
| VMS-PR-009 | Copy the last appointment for a frequent visitor | P2 | **Not implemented** |
| VMS-PR-010 | Batch import of appointments from an Excel template | P2 | **Not implemented** |

> **Visitor search**: every word typed must match the first name, last name, company or email (so "John Smith" finds John Smith); up to 20 results. Already-added visitors show "(already added)". If nothing matches, the form offers "Register a new visitor".

#### 2.1.2 Multi-Visitor Visits

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-PR-030 | One appointment carries one primary visitor plus any number of companions (one date, area, purpose and Host) | P0 | **Implemented** |
| VMS-PR-031 | Each companion keeps their own visitor record (name, company, contact, ID-verified flag, training / PPE freshness) | P0 | **Implemented** |
| VMS-PR-032 | "Add another visitor (companion)" on New Visit; each slot can search the registry or register a new visitor | P0 | **Implemented** — cards labelled PRIMARY / COMPANION |
| VMS-PR-033 | Visit page lists every visitor with their ID status; removing the primary promotes the first companion | P1 | **Partial** — promotion works on the New Visit form only; once created, the visitor list of a visit cannot be changed (not in "Edit visit", not in the API) |

#### 2.1.3 Notification Contacts and Email Settings (Admin)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-PR-020 | VMS Admin → Notification Contacts: Training Contact email (HR) and PPE Contact email (Janitor) | P0 | **Implemented** — a blank field disables that channel |
| VMS-PR-021 | Training Contact gets a heads-up email for GMP-grade visits (visitor, date, area, Host) | P0 | **Partial** — sent at the **first badge print (= check-in)**, not after approval, and sent whether or not the visitor's training is still fresh (freshness only governs the confirmation task) |
| VMS-PR-022 | When the Host requests PPE, the PPE Contact receives the per-visitor gear list | P0 | **Implemented** — Office visits (auto-confirmed): at creation. All other areas: once the visit is approved — the email goes out the first time anyone opens the visit after final approval. A "Prepare PPE" task is opened for the Janitor at the same time and closes itself when the visit is checked in, cancelled or marked No Show |
| VMS-PR-023 | Contacts can be changed at any time, effective immediately | P1 | **Implemented** |
| VMS-PR-024 | VMS Admin → Email Settings: VMS's own outgoing mail server (host, port, user, password, STARTTLS, from) with "Send test"; falls back to the company mail settings when blank | P0 | **Implemented** — password masked on read; "Send test" uses the *saved* settings |

> **Contacts must be real users.** A confirmation or "Prepare PPE" task is created only when the configured email belongs to an **active** UniOps user. Otherwise only the email is sent and nobody can confirm in the system (confirmation is authorised by exact email match — VMS-CI-023).

#### 2.1.4 Per-Visitor PPE Request

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-PR-040 | Host ticks "PPE needed" at creation; PPE is opt-in per visit, not triggered by area | P0 | **Implemented** |
| VMS-PR-041 | Per visitor: clothing size (XS–XXL or other) and footwear (shoe covers, or safety shoes US 7–14 or other) | P0 | **Implemented** — defaults M, shoe covers, size 10 |
| VMS-PR-042 | Optional group notes | P1 | **Implemented** — max 500 characters |
| VMS-PR-043 | One email to the PPE Contact listing each visitor's gear; sent once per visit | P0 | **Implemented** — plus the "Prepare PPE" task |

#### 2.1.5 Access Area Rules

All area-dependent behaviour (approval routing, health-declaration gate, compliance tasks, reports and the hints shown in the New Visit form) is driven by one rule set, also served to the frontend. Entire Plant includes the GMP clean zone and is held to GMP rules (QA decision, 2026-10-01).

| Access area | Approval | Quality Manager step | Health declaration for every visitor before the badge prints | Training / PPE confirmation tasks at check-in | Counted in GMP / Lab reports | Badge band (default) |
|---|:---:|:---:|:---:|:---:|:---:|---|
| Office / Lobby | none — auto-confirmed | — | — | — | — | Green, LOW RISK |
| Warehouse | Department Manager | — | — | — | — | Amber, MEDIUM RISK |
| Production (Non-GMP) | Department Manager | — | — | — | — | Orange, MEDIUM RISK |
| Production (GMP Clean Zone) | Department Manager → Quality Manager | ✓ | ✓ | ✓ | ✓ | Red, HIGH RISK |
| Laboratory | Department Manager → Quality Manager | ✓ | ✓ | ✓ | ✓ | Red, HIGH RISK |
| Entire Plant | Department Manager → Quality Manager | ✓ | ✓ | ✓ | ✓ | Red, HIGH RISK |

"GMP-grade" in this document means GMP Clean Zone, Laboratory and Entire Plant. The Quality Manager step only applies where the company's approval chain for VMS visits includes it (§6.2). Per-area confirmations proposed in earlier versions — hard hat / safety shoes for Warehouse, dress code for Non-GMP, lab safety briefing for Laboratory — are **Not implemented**.

#### 2.1.6 Notifications to the Host

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-PR-011 | Host is emailed when an appointment is submitted | P0 | **Not implemented**. Approvers are not emailed by VMS either; their task appears in the VMS and Portal Task Inboxes, and the platform notification outbox emails it only if an admin has switched VMS approval tasks on (off by default — §6.4) |
| VMS-PR-012 | Reminder to the Host the day before the visit | P1 | **Implemented** — sent on the first scheduler run after midnight plant time for the next day's **Confirmed** visits; a visit still pending approval gets no reminder |
| VMS-PR-013 | Host is told the approval result | P1 | **Implemented** — one email on final approval or rejection. The email is triggered the first time anyone opens the visit after the decision (read-driven, so it can be delayed). No email on "Return for edit" — the Host sees the comment on the visit page |
| VMS-PR-013a | Host is told when someone else cancels the visit | P1 | **Implemented** — "Visit cancelled" email when the canceller is not the Host (a cancellation is never reported as a rejection) |
| VMS-PR-014 | Visitor receives a confirmation email with directions and parking | P2 | **Not implemented** — VMS never emails visitors (proposed in VMS-SELF-REGISTRATION.md) |

#### 2.1.7 Appointment Management

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-PR-015 | Status lifecycle | P0 | **Implemented** — Pending Approval, Confirmed, On-Site, Departed, Cancelled, No Show; plus a derived red **Overdue** badge. A rejected visit shows as Cancelled (no separate Rejected status) |
| VMS-PR-016 | Host can modify or cancel an appointment before check-in | P1 | **Implemented** — "Edit visit" (date, times, purpose, area, notes) and "Cancel visit" on the visit page, available to the creator, the **Host**, a Department Manager of the Host's department and System Admin; the buttons are shown only to those users. Rules for changing the access area: locked while approval is in progress; on a Confirmed visit only to an area of the same or a lower approval tier; on a not-yet-submitted or returned visit freely (the next submission routes by the new area) |
| VMS-PR-016a | A visit whose approval hand-off failed can be sent again | P0 | **Implemented** — the visit stays Pending Approval with a banner "Not sent for approval yet" and a **Submit for approval** button; it cannot be badged until submission succeeds |
| VMS-PR-017 | Host sees own visits; manager sees department visits | P0 | **Implemented** — see visibility rules in §3.2. Lists: Today's Visits, All Visits (**latest 50 only, no paging**), On-Site Now |
| VMS-PR-018 | Search by visitor name, company, Host, date range | P1 | **Not implemented** — the visit lists have no search or filter |
| VMS-PR-019 | Auto-mark No Show once the planned arrival day is over | P2 | **Implemented** — after plant-local midnight following the planned arrival's day (user rule 2026-10-20; was 2 hours after arrival); applies to Confirmed and Pending Approval visits; for a pending visit the approval is cancelled and its tasks closed. Undone when the visit is rescheduled in Data Maintenance (§2.5.5) |

---

### 2.2 Check-In (via Badge Printing)

VMS has no receptionist. **Check-in is the first badge print**: when the Host opens the badge page, the system records the actual arrival time and sets the visit On-Site. The Host can print at their desk and bring the badge down, or at a shared computer in the lobby.

> **Order of events.** "Print badge & check in" on the visit page opens the badge page; **the check-in is recorded as soon as that page loads**, then the browser print dialog opens. Cancelling the print dialog does not undo the check-in; use "Reprint badge" (reason required) to print again.

#### 2.2.1 Print = Check-In (with appointment)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CI-001 | Host opens the visit and clicks "Print badge & check in"; arrival recorded, status On-Site, browser print opens | P0 | **Implemented** — order as in the note above |
| VMS-CI-002 | Arrival time recorded to the second (= first print time) | P0 | **Implemented** |
| VMS-CI-003 | Quick search by name / company at check-in | P0 | **Not implemented** — visits are reached through Today's Visits, the Dashboard or the Task Inbox |
| VMS-CI-004 | Host adds accompanying count, licence plate, equipment before printing | P1 | **Not implemented** in the UI (the fields exist in the data model; "Vehicle plate" on the visit page always shows "—") |
| VMS-CI-005 | Host confirms each visitor's photo ID ("ID Verified") | P1 | **Partial** — one checkbox per visitor; the print button stays disabled until every visitor is verified. The flag is stored on the **visitor record permanently** (a returning visitor is already verified next time) and cannot be unticked in the UI. The server does not enforce it |
| VMS-CI-006 | Print blockers are explained | P1 | **Implemented** — a disabled print button lists the actual blockers (pending approval, ID not verified, health declaration missing or failed) |

#### 2.2.2 Walk-In (no appointment)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CI-007 | Walk-in: create visitor and print badge in one go | P0 | **Partial** — no dedicated entry. Use New Visit with area Office / Lobby (auto-confirmed) → Create visit → ID Verified → Print badge & check in. Any other area waits for approval |
| VMS-CI-008 | Same required fields as pre-registration | P0 | **Implemented** — same form |
| VMS-CI-009 | Signed-in user defaults to Host | P1 | **Implemented** |

#### 2.2.3 Health and Safety Confirmation (food-plant specific)

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CI-010 | Health declaration required before the badge prints for GMP-grade visits | P0 | **Implemented** — **per visitor**, for GMP Clean Zone, Laboratory and Entire Plant. Filed from the visit page ("Declare" / "Re-file") before check-in; the buttons disappear once the visit is checked in, and the server refuses a declaration for a visit that is no longer Pending or Confirmed. Questions come from VMS Admin → Health Questions (built-in defaults: symptoms in the last 24 h, open wounds, contact with infectious disease, carrying food allergens). A "Yes" to any default question = Failed. Submitting requires every question answered and "Food-safety briefing confirmed" ticked. The badge prints only when **every** visitor has passed |
| VMS-CI-011 | A visitor who fails is restricted / downgraded to office access | P0 | **Partial** — no automatic restricted status. A failed declaration blocks the badge for the whole visit; the page tells the Host to use "Edit visit" to move the visit to Office / Lobby (then it prints) or to cancel it |
| VMS-CI-012 | Food-safety training confirmation for GMP visitors | P0 | **Partial** — the "Food-safety briefing confirmed" tick inside the declaration (Host confirmation). The signature pad is optional |
| VMS-CI-013 | PPE issuance recorded at check-in | P1 | **Not implemented** — PPE is covered by the Janitor's 12-month confirmation (VMS-CI-020..023) and the "PPE returned" tick at check-out |
| VMS-CI-014 | Re-filing a declaration keeps the earlier one | P0 | **Implemented** — the visit page shows the latest declaration; the replaced answers, result and signature are kept in full in the audit log |

#### 2.2.4 Per-Visitor Training and PPE Compliance

Training and PPE are tracked **on the visitor record**, not per visit, so a frequent supplier does not retrain every visit. "Fresh" = confirmed within the last 12 months (365 days).

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CI-020 | Each visitor carries training-confirmed and PPE-issued dates and confirmers | P0 | **Implemented** |
| VMS-CI-021 | Badge printing is **not** blocked by training / PPE freshness | P0 | **Implemented** — by design: a visitor cannot be briefed or fitted with PPE before they are on site with a badge, so blocking the badge would deadlock the flow |
| VMS-CI-022 | At the first print of a GMP-grade visit, open a training task (HR) and a PPE task (Janitor) for each visitor whose record is stale; one open task per visitor and gate; HR heads-up email | P0 | **Implemented** — tasks appear in the VMS and Portal Task Inboxes and open the visitor compliance page. Created only when the contact email belongs to an active user |
| VMS-CI-023 | Only the configured contact (or System Admin) can confirm; confirmation stamps the visitor, completes open tasks and is audit-logged | P0 | **Implemented** — others see "Only the configured HR training contact can confirm training" / "Only the configured PPE contact can confirm PPE issuance" |
| VMS-CI-024 | Visitor compliance page and visit-page banner show each gate's freshness | P1 | **Implemented** |

---

### 2.3 Badge Printing

The badge page lays out the badges for browser printing on an ordinary office printer; the Host cuts out the card and puts it in a standard holder. No label printer is required — no hardware cost, and any Host can print from their own desk.

#### 2.3.1 Badge Content

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-LB-001 | Name (large), company, visit date, validity, Host, colour-coded access area, QR code identifying the visit | P0 | **Implemented** — default layout: top band "VISITOR" + area + risk level; name (upper case) and company; rows Date / Host / Valid until (Vehicle, Accompanying, Purpose available but hidden by default); QR code captioned "Scan to check out"; footer lines |
| VMS-LB-002 | Area colour coding | P0 | **Implemented** — Green Office, Amber Warehouse, Orange Non-GMP, Red GMP / Laboratory / Entire Plant |
| VMS-LB-003 | Separate "Escort Required" indicator | P1 | **Not implemented** — covered by the footer line |
| VMS-LB-004 | Footer: "Must be accompanied by Host at all times", "Please return badge when leaving" | P1 | **Implemented** |

#### 2.3.2 Printing

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-LB-005 | Native browser printing to an office printer | P0 | **Implemented** |
| VMS-LB-006 | Badge card layout sized for cutting out | P0 | **Implemented** — landscape Letter, one visitor per page, two identical cards side by side, printed automatically at 70% of the design size (about 98 × 70 mm per card). Leave the print dialog at 100%. Only the badges print (no app chrome, white background) |
| VMS-LB-007 | Print dialog first, check-in on confirmation | P0 | **Partial** — reversed: check-in is recorded when the badge page loads (§2.2) |
| VMS-LB-008 | Manual reprint that does not re-check-in | P1 | **Implemented** — "Reprint badge" after check-in, reason required |
| VMS-LB-009 | Print log: time, printed by, original / reprint and reason | P2 | **Implemented** — "Badge prints" section on the visit page |
| VMS-LB-013 | Multi-visitor visits print one badge per visitor in a single print dialog; one check-in event | P0 | **Implemented** |

#### 2.3.3 Badge Configuration

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-LB-010 | Admin can change the badge layout | P1 | **Implemented** as a structured configuration (VMS Admin → Badge) with live preview: top band title and toggles; per-area label, background, text colour and risk level; name and company; detail rows (from a fixed list); QR on/off and caption; footer lines; font style. No free-form HTML and no logo. The configuration applies to every user's prints. The older HTML-template endpoints remain but are unused |
| VMS-LB-011 | Several templates (Standard / VIP / Contractor) | P2 | **Not implemented** — one configuration |
| VMS-LB-012 | Bilingual (EN / FR) badges | P2 | **Not implemented** |

#### 2.3.4 Badge Layout (illustrative)

```
+---------------------------------------------+
|  VISITOR            PRODUCTION (GMP)        |
|                     HIGH RISK               |
|                                             |
|  JOHN SMITH                                 |
|  ABC Corp                                   |
|  Date: 2026-04-15                           |
|  Host: Jane Doe                             |
|  Valid until: 17:00                         |
|                                             |
|        +-------------------+                |
|        |  QR (visit ID)    |                |
|        +-------------------+                |
|        Scan to check out                    |
|  ! MUST BE ACCOMPANIED BY HOST AT ALL TIMES |
|  ! PLEASE RETURN BADGE WHEN LEAVING         |
+---------------------------------------------+
```

---

### 2.4 Check-Out (via QR Scan)

When the visitor leaves, the Host scans the badge QR code — with a USB scanner or by typing on a desktop, or with the phone camera — and confirms departure.

#### 2.4.1 QR Check-Out

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CO-001 | Badge QR identifies the visit; scanning finds it | P0 | **Implemented** |
| VMS-CO-002 | Desktop: scanner or manual entry, then details and confirm | P0 | **Implemented** — Check Out page → "Scanner / type" → "Visit ID" → "Look up" |
| VMS-CO-003 | Mobile: camera scan, automatic pop-up on recognition | P0 | **Implemented** — "Camera" mode (default on narrow screens); needs HTTPS and camera permission |
| VMS-CO-004 | Confirm: departure time, status Departed, badge return recorded | P0 | **Implemented** |

> **Who can check a visitor out**: the visit's creator, its Host, a Department Manager of the Host's department, and System Admin. A user who cannot see the visit gets "Visit not found"; scanning an already-departed badge shows "This visitor has already checked out." The same dialog is available from the visit page ("Check out").

#### 2.4.2 Departure Confirmation

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CO-005 | Departure time to the second | P0 | **Implemented** |
| VMS-CO-006 | Host confirms badge returned | P1 | **Implemented** — "Badge returned", ticked by default |
| VMS-CO-007 | PPE returned | P1 | **Implemented** — "PPE returned", ticked by default |
| VMS-CO-008 | "Your visitor has left" confirmation to the Host | P2 | **Not implemented** |

#### 2.4.3 Overdue Alerts

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CO-009 | Visitors past planned departure shown as Overdue | P1 | **Implemented** — red "Overdue" badge on lists and the visit page; Dashboard "Overdue" card (no drill-down). Visits without a planned departure never become overdue |
| VMS-CO-010 | 1 hour overdue: remind the Host | P1 | **Implemented** — email repeated every 24 hours until check-out, plus a "Check out visitor" task for the Host (closed automatically once the visitor is no longer on site) |
| VMS-CO-011 | 4 hours overdue or after business hours: escalate to the Department Manager | P2 | **Partial** — 4 hours only (no business-hours rule). Sent once to the first active user with primary role `dept_manager` in the Host's department; if there is none, retried on each run until one exists |
| VMS-CO-012 | On-site list with stay duration | P1 | **Implemented** — "On-Site Now", refreshes every 60 s, limited to visits the user can see (Dashboard counters are plant-wide) |

**Background scheduler.** VMS runs its own scheduled jobs every 15 minutes (configurable, minimum 60 s), guarded so that only one run executes at a time:

| # | Job | Rule | Repeat |
|---|---|---|---|
| 1 | No-show | Confirmed or Pending Approval, not arrived, planned arrival's plant day over (after midnight) → No Show (pending: approval cancelled, tasks closed) | status change |
| 2 | Day-before reminder | Confirmed, visit date = tomorrow (plant time) → email Host | once per visit |
| 3 | Overdue reminder | On site, planned departure + 1 h passed → email Host + "Check out visitor" task | every 24 h until check-out |
| 4 | Escalation | On site, planned departure + 4 h passed → email the Host's Department Manager | once per visit |
| 5 | Task housekeeping | Close "Check out visitor" tasks once the visit is no longer on site, and "Prepare PPE" tasks once it is no longer Pending / Confirmed | every run |

A send is recorded only when it was attempted, so a mail outage retries on the next run. Times in emails are in plant time (e.g. "2026-10-01 14:30 EDT"). System Admin can trigger a run from the API; a manual run shares the same guard and is refused while another run is in progress.

#### 2.4.4 Batch Check-Out

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-CO-013 | End-of-day batch check-out with confirmation | P1 | **Partial** — "Batch check-out" on On-Site Now, **System Admin only**, closes **every** on-site visit in the plant after the browser confirmation "Close all N on-site visitors now?". Records badge and PPE as *not* returned for every visit (raises the "Unreturned badges" indicator) |
| VMS-CO-014 | Logged as "System Batch Check-Out" with a reason | P2 | **Partial** — logged as check-out with the fixed note "System Batch Check-Out"; no custom reason |

---

### 2.5 Audit Trail and Compliance Reporting

#### 2.5.1 Operation Audit Log

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-AU-001 | Log every user operation | P0 | **Partial** — logged: visitor create / update / training and PPE confirmation; visit create / update / submit / cancel / approve / reject / return / check-in / check-out / no-show / attachment upload; badge reprint; health declaration submit (with the replaced declaration on re-file); every VMS Admin change, mail test and manual scheduler run; report exports. **Not logged**: the audit-log export itself, scheduler emails and task creation, and Portal Data Maintenance edits (those go to the platform admin audit log) |
| VMS-AU-002 | Entry: time, user, IP, action, entity, before / after values | P0 | **Implemented** — plus browser and notes |
| VMS-AU-003 | Immutable | P0 | **Implemented** — update and delete are revoked at database level |
| VMS-AU-004 | Retention ≥ 3 years with automatic archive | P1 | **Not implemented** — no retention or archive job (records are kept indefinitely) |

Audit Log page filters: action type and entity type (lists built from what has actually been logged), user, date range.

#### 2.5.2 Visitor History

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-AU-005 | Search by visitor, dates, Host, area, type, status | P0 | **Partial** — only the audit-log filters, and the Health Declarations page (date range, visitor / company). No visit search |
| VMS-AU-006 | Export to Excel / PDF | P0 | **Partial** — CSV only (audit log and two reports) |
| VMS-AU-007 | Single visitor profile: all visits, declarations, training | P1 | **Partial** — only the visitor compliance page (training / PPE freshness); no visit history |
| VMS-AU-008 | Full-text search | P2 | **Not implemented** |

#### 2.5.3 Compliance Reports

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-AU-009 | Pre-built reports: CFIA Visit Log, GMP Area Access Summary, Contractor Access Report, Monthly Visitor Statistics | P0 | **Partial** — **CFIA Visit Log** (one row per visitor per visit; pending and cancelled visits excluded; the health and training columns carry the visit-level value, not each visitor's own) and **GMP / Lab Area Summary** (per GMP-grade area: total, passed, failed, restricted, not required, no declaration, after-hours, unreturned). Contractor and Monthly reports **Not implemented** |
| VMS-AU-010 | One-click generation for a date range | P0 | **Implemented** — Reports page, From / To (default: first of the month → today) |
| VMS-AU-011 | Report format | P0 | **Implemented** — CSV, streamed. CSV is the interim format until CFIA confirms the required layout; the date-range filter and one-row-per-visitor rule are final |
| VMS-AU-012 | Scheduled reports by email | P2 | **Not implemented** |

#### 2.5.4 Data Integrity

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-AU-013 | Tamper-proof e-signature on health declarations and training confirmations | P0 | **Partial** — the signature is optional; a re-filed declaration replaces the visible one, but the previous answers, result and signature are preserved in the immutable audit log |
| VMS-AU-014 | Critical-field changes record a reason and keep the original | P1 | **Partial** — before / after values are kept in the audit log; no reason is captured |
| VMS-AU-015 | Hash checksum on exports | P2 | **Not implemented** |
| VMS-AU-018 | VMS records cannot be deleted through Portal Data Maintenance | P0 | **Implemented** — visits and visitors are edit-only there (decision 2026-10-01) |

#### 2.5.5 Dashboard

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-AU-016 | Compliance indicators | P1 | **Partial** — visible to every user, **plant-wide** numbers: On-site now, Today's visits, Past 7 days, Overdue; "Compliance — this month": GMP-grade visits, health pass rate, unreturned badges (all time), after-hours today (arrival before 07:00 or from 19:00, plant time). A nudge shows open VMS tasks ("waiting on your approval" only when all are approvals) |
| VMS-AU-017 | Drill-down | P1 | **Partial** — On-site / Today / Past 7 days open the matching list (scoped to the user, so counts can differ); Overdue and compliance cards do not drill down |

---

## 3. User Roles and Permissions

Any signed-in UniOps user can act as a Host. Platform tiers come from the user's **primary** role (`auditor`, `system_admin`, `dept_manager`); the three VMS-local roles (Quality Manager, HR Training Contact, Janitor PPE Contact) are configured in VMS Admin. The Portal permission matrix does not apply to VMS (§1.3).

### 3.1 Role Definitions

| VMS role | Source | Scope | Typical holder |
|---|---|---|---|
| Host | Any UniOps user | Book visits; edit / submit / cancel visits they created or host; verify ID, file declarations, print and check out on visits they can see | All employees |
| Department Manager | Primary role `dept_manager` | Host rights + visits whose Host is in their department + the Department Manager approval step | Line managers |
| Quality Manager | VMS Admin → Quality Managers roster | Second approval step of GMP-grade visits; sees the visits assigned to them | QA |
| HR Training Contact | VMS Admin → Notification Contacts (must match an active user) | Confirms food-safety training | HR |
| Janitor PPE Contact | VMS Admin → Notification Contacts (must match an active user) | Stages PPE; confirms PPE issuance | Janitorial |
| Auditor | Primary role `auditor` | All visits; Audit Log, Reports, Health Declarations | Compliance |
| System Admin | Primary role `system_admin` | Everything; VMS Admin; batch check-out; may act on any approval step | IT |

> **Quality Manager assignment**: at each submission VMS picks the **first active user** in the roster (order set with ▲▼ in VMS Admin) and assigns the step to that one person. No round-robin, no "any QM may approve". If the roster is empty or nobody on it is active, the Quality Manager step is **auto-skipped** and a GMP-grade visit needs only the Department Manager.

### 3.2 Permission Matrix

| Function | Host (any user) | Dept Manager | Quality Manager | HR / Janitor contact | Auditor | System Admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Create visit | ✓ | ✓ | ✓ | ✓ | ✓ (not blocked) | ✓ |
| See visits | created by me, I am Host, or I hold a task on it | + Host in my department | + visits assigned to me | + visits I hold a task on | all | all |
| Edit / submit / cancel (Pending or Confirmed) | created by me or I am Host | + Host in my department | as Host | as Host | ✗ | ✓ |
| Verify ID | visits I can see | ✓ | ✓ | ✓ | ✓ (not blocked) | ✓ |
| File health declaration | visits I can see | ✓ | ✓ | ✓ | ✗ | ✓ |
| Print badge (= check-in) / reprint | visits I can see | ✓ | ✓ | ✓ | ✓ (not blocked) | ✓ |
| Check out | created by me or I am Host | + Host in my department | as Host | as Host | ✗ | ✓ |
| Batch check-out (whole plant) | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Upload attachments | visits I can see | ✓ | ✓ | ✓ | ✗ | ✓ |
| Approve / return / reject | — | Department Manager step | assigned QM step | — | ✗ | any step |
| Confirm training / PPE | ✗ | ✗ | ✗ | own gate only | ✗ | ✓ |
| Dashboard (plant-wide numbers) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Audit Log, Reports, Health Declarations menu | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |
| VMS Admin | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |
| VMS approval chain (Portal → Admin → Approval Workflows) | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ (Portal) |

Notes:
- "Holding a task on a visit" (open or completed) grants visibility of that visit — this is how a Janitor opens a "Prepare PPE" task and how an approver from another department opens the visit they must approve. Tasks received through **approval delegation** do not count (§13, defect).
- Whether auditors should be able to create visits, verify ID and print badges is an open question (§13).
- Any signed-in user can edit any visitor record through the API (§13, defect).

---

## 4. User Interaction Flows

### 4.1 Standard Visit (with appointment)

```
[Host: New Visit]
    ↓  Search visitor, or "Register a new visitor" → "Save visitor"
    ↓  (optional) "Add another visitor (companion)"
    ↓  Host = me (or "Change"), date, planned arrival / departure, purpose, access area
    ↓  (optional) "PPE needed" → sizes per visitor
    ↓  "Create visit" → visit page
    ↓
[Access area decides the route]
    ├─ Office / Lobby ─────────────────────────────────────→ Confirmed
    ├─ Warehouse, Production (Non-GMP) → Dept Manager ─────→ Confirmed
    └─ GMP, Laboratory, Entire Plant → Dept Manager → Quality Manager → Confirmed
    │   If the hand-off to approval fails: banner "Not sent for approval yet" → "Submit for approval"
    ↓  Host emailed the result; Janitor emailed + "Prepare PPE" task if PPE was requested
    ↓
[Just after midnight before the visit: reminder email to Host]
    ↓
================ Arrival day ================
[Host opens the visit (Today's Visits / Dashboard)]
    ↓  "ID verification": tick "ID Verified" for every visitor
    ↓  GMP-grade only: "Health declarations" → "Declare" for every visitor
    │     (all questions + "Food-safety briefing confirmed"; signature optional)
    │     Failed → "Edit visit" to Office / Lobby, or cancel
    ↓  "Print badge & check in"
    ↓  Badge page opens → CHECK-IN RECORDED → browser print dialog
    ↓  GMP-grade: training / PPE tasks to HR / Janitor for stale visitors; HR heads-up email
    ↓  Cut out badge, hand it over with any PPE
================ On site ================
    ↓  Status "On-Site"; listed on "On-Site Now"
    ↓  Past planned departure → red "Overdue"
    ↓  +1 h → email + "Check out visitor" task to Host (email repeats daily)
    ↓  +4 h → email to the Host's Department Manager
================ Departure ================
    ↓  "Check out" page → Camera or "Scanner / type" → scan badge
    │   (or "Check out" on the visit page)
    ↓  "Badge returned" / "PPE returned" → "Confirm departure"
    ↓  Status "Departed"
```

### 4.2 Walk-In (no appointment)

```
[Host: New Visit → area "Office / Lobby"] → "Create visit" (auto-confirmed)
    ↓
["ID Verified" for each visitor] → "Print badge & check in"
    ↓
[Same as the standard flow from here]
```

Any other area goes through approval first; there is no instant path for Warehouse, Production, Laboratory or Entire Plant.

### 4.3 Approver Flow

```
[Task in VMS "Task Inbox" (group "Visits") and Portal "My Task Inbox" (group "Visitor")]
    │  (email only if an admin has enabled VMS approval tasks in Portal notification settings)
    ↓
[Open task → visit page → panel "This visit needs your approval (dept manager | quality manager)"]
    ├─ "Approve"          → optional comment → next step or Confirmed
    ├─ "Return for edit"  → reason required → Host sees who returned it and why,
    │                       uses "Edit visit", then "Submit for approval";
    │                       the visit resumes at the step that returned it
    │                       (a visit edited down to Office / Lobby is confirmed without approval)
    └─ "Reject"           → reason required → visit shows as Cancelled; Host emailed
```

### 4.4 HR / Janitor Compliance Flow

```
[Janitor: "Prepare PPE — <visitor>" task + email with sizes]   (after approval; Office: at creation)
    ↓  stage the gear before the visit; the task closes itself at check-in / cancel / no-show
[Visitor checked in on a GMP-grade visit]
    ↓
[HR: "Confirm food-safety training — <visitor>"]   [Janitor: "Confirm PPE issuance — <visitor>"]
    ↓                                                ↓
[Visitor compliance page → "Confirm now"]  (valid 12 months; shows "Confirmed" when already fresh)
```

---

## 5. Data Model

VMS keeps its own tables (prefixed `vms_`) in the shared UniOps database and reuses the platform's users, tasks, approval events and company configuration. Host, creator and Quality Manager are real references to platform users.

### 5.1 Entities

| Entity | Purpose | Key content |
|---|---|---|
| **Visitor** | A person who visits; reused across visits | First / last name, company, job title, phone, email, visitor type, ID-verified flag (permanent), training-confirmed date and confirmer, PPE-issued date and confirmer |
| **Visit** | One appointment | Primary visitor, companion list, Host, creator, visit date, planned and actual arrival / departure, purpose, access area, status, aggregated health-declaration status, briefing-confirmed flag, badge returned, PPE issued / returned, PPE request (per-visitor sizes and notes), accompanying count, vehicle plate, notes; approval fields (engine state, current step, title used as the task's document number, submitted at, assigned Quality Manager); notification markers (approval-result email, PPE email, day-before reminder, last overdue reminder, escalation) |
| **Badge print** | One row per print | Visit, printed by, printed at, reprint reason |
| **Health declaration** | One current declaration per visitor per visit | Answers (questionnaire snapshot), result (passed / failed / restricted / not required), optional signature |
| **Audit log** | Immutable trail | Time, user, IP, browser, action, entity, before / after values, notes |
| **VMS configuration** (single record) | Admin settings | Quality Manager roster (ordered), notification contacts, health questions, VMS mail settings, badge configuration |
| *Platform:* Users | Employees, reused | Name, email, primary role, department |
| *Platform:* Tasks | Approval and VMS work items, shared with every module | See §6.3 |
| *Platform:* Approval events | Approval history | Shared approval engine |
| *Platform:* Company configuration | Approval chain for VMS visits | Edited in Portal |

Status values: **Visit** — Pending Approval, Confirmed, On-Site (checked in), Departed (checked out), Cancelled, No Show. **Approval state** (engine) — draft, submitted, in review, returned, approved, rejected, cancelled. **Access area** — office, warehouse, production non-GMP, production GMP, laboratory, entire plant. **Visit purpose** — meeting, maintenance, tour, audit, interview, delivery, other. **Visitor type** — supplier, contractor, inspector, auditor, customer, interviewee, other.

Companions are stored as a list on the visit rather than a separate link table, so existing reports and lists work unchanged; reports iterate the list explicitly.

---

## 6. Platform Integration

### 6.1 Positioning

| Dimension | Implementation |
|---|---|
| Frontend | Standalone VMS web app inside the shared UniOps tab shell (every page opens as a tab; Dashboard is pinned). Launched from Portal with session hand-off |
| Backend | Dedicated VMS service |
| Database | Shared UniOps database, VMS tables prefixed `vms_` |
| Sign-in | Portal sign-in token, validated locally by VMS; primary role only |
| Approval | Shared approval engine, document type "VMS Visit" (`vms_visit`) — see EPMS.md §3.3 |
| Tasks | Shared task table; VMS Task Inbox and Portal "My Task Inbox" |
| Files | Shared file service (attachments) |
| Employee data | Platform employee directory (Host search) |
| Email | VMS's own mail settings for VMS emails; platform notification outbox for task emails (§6.4) |

### 6.2 Approval Workflow

VMS visits use the shared approval engine (EPMS.md §3.3). VMS decides **whether** a visit needs approval (by access area, §2.1.5); the engine runs **the chain**.

**Chain.** One chain for all VMS visits, edited in Portal → Admin → Approval Workflows → VMS Visit. Engine default: Department Manager only. **Production: Department Manager → Quality Manager.** The Quality Manager step is auto-skipped (and shown struck through with the reason) when the visit's area does not require it or no Quality Manager is available.

**Submission.**
1. On "Create visit", an Office visit is saved as Confirmed. Any other area is saved as Pending Approval and handed to the engine.
2. For GMP-grade visits VMS assigns the first active user on the Quality Manager roster (re-resolved on every submission, since the area may have changed after a return).
3. The visit title "VMS Visit — First Last (Company)" is used as the task's document number. It is limited to **40 characters**; longer titles are shortened with "…".
4. The Department Manager step goes to the Department Manager of the **creator's** department (not the Host's). If that department has no active Department Manager, the hand-off fails.
5. If the hand-off fails for any reason, the visit **stays Pending Approval** (it cannot be badged) and the page shows "Not sent for approval yet" with **Submit for approval**.
6. Final approval sets the visit Confirmed. Rejection or cancellation shows the visit as Cancelled. The Host's result email is sent once, the first time the visit is read after the decision.

**Approver actions** happen on the VMS visit page (the Portal task row only links there): Approve (optional comment), Return for edit and Reject (comment required in the UI). The engine checks that the actor holds the current step; System Admin may act on any step.

**Return and resubmission.** A returned visit shows who returned it and their comment, with **Edit visit** and **Submit for approval** for the creator, Host, Host's Department Manager and System Admin. On resubmission the visit **resumes at the step that returned it** — earlier approvals stand (company rule for all document types, 2026-10-14). If the visit was edited down to Office / Lobby, it is confirmed without approval and the returned approval is withdrawn. The creator's "Revise" task closes on resubmission or cancellation.

**Cancellation.** VMS cancels the in-flight approval and closes all of the visit's open tasks itself, so no stale task is left even when the engine refuses the cancel for a Host or manager who is not the submitter.

**Self-approval.** A Department Manager who books a visit receives the approval task for their own visit and must approve it; the engine's same-approver skip applies only after an approval, never at submission (EPMS.md §3.3.5). Kept by decision (2026-10-01).

**Delegation.** Approval delegation (EPMS.md §1.5) puts the delegator's VMS approval tasks into the delegate's Task Inbox, but VMS does not let the delegate open the visit (§13, defect).

### 6.3 Task Types

All VMS tasks live in the shared task table.

| Task | Document type | Assignee | Opened | Closed | Opens |
|---|---|---|---|---|---|
| Approve visitor visit | VMS Visit | Department Manager, then Quality Manager | submission / next step | approval action | visit page |
| Revise visitor visit | VMS Visit | visit creator | Return for edit | resubmission or cancellation | visit page |
| Confirm food-safety training | VMS training | HR Training Contact | first print, training stale | "Confirm now" | visitor compliance page |
| Confirm PPE issuance | VMS PPE | Janitor PPE Contact | first print, PPE stale | "Confirm now" | visitor compliance page |
| Check out visitor | VMS Visit | Host | 1 h overdue | automatically, once the visitor is no longer on site | visit page |
| Prepare PPE | VMS Visit | Janitor PPE Contact | PPE email sent | automatically, at check-in / cancel / no-show | visit page |

Task titles: "Approve visitor visit: VMS Visit — First Last (Company) — <area>, <date>", "Confirm food-safety training — First Last (Company)", "Confirm PPE issuance — …", "Check out visitor — …", "Prepare PPE — …".

| ID | Requirement | Priority | Status |
|---|---|---|---|
| VMS-AP-001 | VMS Task Inbox lists the user's open VMS tasks, grouped Visits / Training Confirmations / PPE Confirmations; sidebar count | P0 | **Implemented** — refreshes every 60 s |
| VMS-AP-002 | Visit page shows Approve / Return for edit / Reject to the holder of an open approval task | P0 | **Implemented** |
| VMS-AP-003 | Dashboard nudge for open tasks | P1 | **Implemented** — wording follows the task mix |
| VMS-AP-004 | VMS tasks shown in the Portal inbox with links into VMS; not in the EPMS inbox | P0 | **Implemented** — Portal group "Visitor", each task its own row |

### 6.4 Notifications

VMS sends two kinds of email:

**VMS's own emails** (VMS mail settings, plain text): Host approval result; Host "Visit cancelled"; day-before reminder; overdue reminder (daily); 4-hour escalation to the Department Manager; HR training heads-up at check-in; Janitor PPE request. These are independent of Portal notification settings and of users' personal notification preferences.

**Platform task notifications.** Every VMS task is also seen by the platform's task-notification outbox (EPMS.md §3.9). Under the default policy **VMS task types send no email**; an admin can switch each one on (immediate or daily digest) in Portal → Admin → Notification Settings. Platform emails for VMS tasks link to the VMS home page, not to the specific visit. Switching on "Check out visitor" or "Prepare PPE" would duplicate the emails VMS already sends for them.

Microsoft Teams notifications are **Not implemented** for VMS.

### 6.5 API Overview

All endpoints sit under `/api/v1/` of the VMS service and require the Portal sign-in token. "Any" = any signed-in user; "scoped" = only visits the user can see (§3.2; others return "not found"). Only the primary role is checked.

**Visitors**

| Method | Path | Purpose | Access |
|---|---|---|---|
| GET | `/visitors` | Search (every word must match a name, company or email column) | Any |
| GET | `/visitors/{id}` | Visitor details | Any |
| POST | `/visitors` | Create | Any |
| PATCH | `/visitors/{id}` | Update, including ID-verified | Any |
| POST | `/visitors/{id}/confirm-training` | Stamp training, close training tasks | HR contact, System Admin |
| POST | `/visitors/{id}/confirm-ppe` | Stamp PPE, close PPE tasks | PPE contact, System Admin |

**Visits**

| Method | Path | Purpose | Access |
|---|---|---|---|
| GET | `/visits` | List (status, Host, date range; no area or text search) | Any, scoped |
| GET | `/visits/active` | On-site visits | Any, scoped |
| GET | `/visits/{id}` | Detail with all visitors | Any, scoped |
| POST | `/visits` | Create; hands off to approval by area | Any |
| POST | `/visits/{id}/submit` | (Re)submit a not-yet-submitted or returned visit | Creator, Host, Host's Dept Manager, System Admin |
| PATCH | `/visits/{id}` | Edit while Pending / Confirmed (area rules in VMS-PR-016) | Creator, Host, Host's Dept Manager, System Admin |
| POST | `/visits/{id}/cancel` | Cancel; withdraws approval and closes tasks | Creator, Host, Host's Dept Manager, System Admin |
| POST | `/visits/{id}/action` | Approve / reject / return | Any; the approval engine checks the approver |
| POST | `/visits/{id}/check-out` | Departure with badge / PPE returned | Creator, Host, Host's Dept Manager, System Admin |
| POST | `/visits/batch-checkout` | Check out every on-site visit | System Admin |
| GET / POST | `/visits/{id}/attachments` | List / upload | Scoped; auditors cannot upload |
| GET | `/visits/{id}/attachments/{file_id}/download` | Open an attachment | Scoped |

There is no separate check-in endpoint: check-in happens only through the first badge print.

**Badge, health, area rules**

| Method | Path | Purpose | Access |
|---|---|---|---|
| POST | `/visits/{id}/print-badge` | First call = check-in; later calls = reprint (reason required) | Scoped |
| GET | `/visits/{id}/badge-history` | Print history | Scoped |
| GET | `/badge/config` | Badge layout for printing | Any |
| GET | `/health-questions` | Current questionnaire | Any |
| POST / GET | `/visits/{id}/health-declaration` | File one visitor's declaration / list declarations for the visit | Scoped (auditors cannot file) |
| GET | `/health-declarations` | Browse declarations across visits | Scoped |
| GET | `/area-rules` | Area rule table (§2.1.5) | Any |

**Audit, dashboard, reports**

| Method | Path | Purpose | Access |
|---|---|---|---|
| GET | `/audit-logs`, `/audit-logs/facets`, `/audit-logs/export` | Query, filter values, CSV | Auditor, System Admin |
| GET | `/dashboard/overview`, `/dashboard/compliance` | Plant-wide counters | Any |
| GET | `/reports/cfia-visit-log`, `/reports/gmp-area-summary` | CSV reports | Auditor, System Admin |

**Admin** (System Admin): Quality Manager roster, notification contacts, health questions, mail settings and test, badge configuration, manual scheduler run, and the Portal Data Maintenance entity endpoints (edit-only for VMS records).

**Cross-service**: the VMS frontend reads the employee directory and the user's open tasks from the platform; the VMS service calls the approval engine (submit / cancel / approve / reject / return) and the file service; the approval engine writes the visit's approval state and, on final approval, its Confirmed status directly.

### 6.6 Frontend Pages

| Page | Sidebar | Who |
|---|---|---|
| Dashboard | yes (pinned tab) | everyone |
| Task Inbox | yes, with count | everyone |
| Today's Visits | yes | everyone (scoped) |
| All Visits (latest 50) | yes | everyone (scoped) |
| On-Site Now | yes | everyone (scoped) |
| New Visit | yes | everyone |
| Check Out | yes | everyone |
| Audit Log, Reports, Health Declarations | "Compliance" group | Auditor, System Admin |
| VMS Admin — Quality Managers, Notification Contacts, Email Settings, Health Questions, Badge | "Admin" group | System Admin |
| Visit *First Last* | opened from lists / tasks | scoped |
| Badge *First Last* | opened from "Print badge & check in" | scoped |
| Compliance *First Last* | opened from training / PPE tasks | everyone; confirmation restricted |

Without a session the app redirects to Portal sign-in; an expired session returns the user to Portal.

### 6.7 Portal Integration

- **Launcher**: Portal sidebar → Modules → "VMS" and the home-page card "VMS — Visitor appointments, badge printing, on-site tracking"; visible to every user.
- **Task Inbox**: VMS tasks appear in Portal "My Task Inbox" under **Visitor**; a click opens the visit or the visitor compliance page in VMS. Portal has no approve buttons for VMS — approval happens on the VMS visit page.
- **Approval chain**: Portal → Admin → Approval Workflows → VMS Visit. The Quality Manager roster itself lives in VMS Admin.
- **Data Maintenance**: VMS visits and visitors can be edited (not deleted) by System Admin. A visit is rescheduled through Planned arrival / Planned departure, shown in plant time; a time typed without a UTC offset is taken as plant time. Departure must be after arrival. Visit date is read-only and is reset to the planned arrival's plant-local day on save, and the audit entry records it. Saving a visit that was automatically marked No Show undoes the No Show if its planned arrival is no longer overdue (today or later, plant time). The visit gets back the status it had before (Confirmed or Pending Approval), along with its approval state and the Host's approval-result email flag. For a pending visit, the approval tasks the No Show closed are reopened, dated with the new visit date, and queued for the task-notification outbox again. A task a person completed is never reopened. The undo is written to the VMS audit log as "visit.no_show_undone". It does not happen when the schedule is still overdue, the visitor already arrived, the status was set to No Show by hand rather than by the scheduler, or the same save sets another status.
- **Branding**: company name, logo and tagline come from Portal company settings.

See PORTAL.md.

---

## 7. Non-Functional Requirements

Targets below are product targets; they have not been re-measured in this revision.

### 7.1 Performance

| Metric | Target |
|---|---|
| Page load | < 2 s |
| Core API response (check-in, lists) | < 500 ms |
| Click to print dialog | < 3 s |
| Concurrent users | 50+ |
| Queries on 100k records | < 1 s |

### 7.2 Availability

| Metric | Target |
|---|---|
| Uptime | 99.5% during business hours |
| Maintenance window | Off-hours |
| Backups | Daily incremental, weekly full (platform) |
| RTO / RPO | < 4 h / < 1 h |

### 7.3 Security

| Requirement | Status |
|---|---|
| Sign-in through Portal; password policy and MFA per PORTAL.md | Implemented (platform) |
| HTTPS for the whole site (required for camera scanning) | Implemented (platform) |
| Parameterised database access | Implemented |
| Role-based access; least privilege | **Partial** — primary role only; visitor records editable by any user (§13) |
| Immutable audit log | Implemented (database-level) |
| Encryption at rest of sensitive fields (health declarations) | **Not implemented** |
| No photographs or biometrics collected | Implemented |
| Emails are plain text (no HTML injection from user-entered text) | Implemented |

### 7.4 Usability

| Requirement | Target |
|---|---|
| Find appointment to printed badge | < 30 s |
| Mobile scan to confirmed check-out | < 10 s |
| Badge legibility | Name readable at 2 m; QR scannable by phone |
| Responsive | Desktop, tablet, mobile browser (375–414 px) |
| Feedback | Clear success / failure messages; print blockers explained |
| Desk and lobby printing | Same app supports both |

### 7.5 Compatibility

| Platform | Range |
|---|---|
| Desktop browsers | Current Chrome, Edge, Firefox, Safari |
| Mobile browsers | Safari (iOS), Chrome (Android / iOS) with camera access |
| Printer | Any office laser / inkjet printer, Letter paper |
| Desktop scanner | USB barcode scanner in keyboard mode, or manual entry |

---

## 8. Canadian Environment Adaptation

### 8.1 Language

| Requirement | Status |
|---|---|
| English interface | Implemented |
| French interface toggle | **Not implemented** |
| Bilingual badges | **Not implemented** |
| Notifications in the user's language | **Not implemented** (English only) |
| French report templates | **Not implemented** |

### 8.2 Regulatory Compliance

| Regulation | Requirement | Current coverage |
|---|---|---|
| CFIA | Complete records of visitors to food-production areas; audit export | CFIA Visit Log (interim CSV layout) |
| HACCP | Health declarations and briefing confirmations for GMP areas | Per-visitor declarations; training / PPE confirmations |
| PIPEDA | Lawful collection of visitor personal data; no biometrics; defined retention | No photos; retention period **not defined / not enforced** |
| CASL | Visitor emails must offer unsubscribe | Not applicable today (VMS does not email visitors); applies if self-registration is built |
| Quebec Bill 96 | French interface if deployed in Quebec | Not applicable to current sites |

### 8.3 Localisation

| Setting | Value |
|---|---|
| Time zone | America/Toronto — used for "today", reminders, email times and after-hours |
| Date format | YYYY-MM-DD |
| Time format | 24-hour |
| Paper | Letter |

### 8.4 GMP Area Access — Compliance Chain

```
         Approval (Dept Manager → Quality Manager)
                         │
        ┌────────────────┼──────────────────┐
        v                v                  v
  Health declaration   Food-safety       ID verified
  per visitor          briefing tick     per visitor
  (must pass)          (in declaration)
        └────────────────┼──────────────────┘
                         v
              Badge prints = check-in
                         │
        ┌────────────────┴──────────────────┐
        v                                   v
  HR confirms training               Janitor confirms PPE
  (if older than 12 months)          (if older than 12 months)
```

---

## 9. Delivery History and Roadmap

### 9.1 Delivered

| Period | Delivered |
|---|---|
| May 2026 | MVP: visitors and visits, badge printing = check-in, QR check-out, audit log, dashboard, Portal launcher |
| May–Jun 2026 | Approval through the shared engine (Department Manager → Quality Manager), VMS Admin, Portal Task Inbox and Approval Workflows tab, health declarations, CFIA / GMP CSV reports, attachments |
| Jun 2026 | Multi-visitor visits, per-visitor PPE requests, per-visitor training / PPE compliance with HR / Janitor tasks, VMS mail settings, background scheduler (no-show, reminders, overdue, escalation), structured badge configuration |
| Jul–Sep 2026 | Daily overdue reminders + Overdue badge, "Check out visitor" and "Prepare PPE" tasks, tab shell |
| 1 Oct 2026 | Defect release (§12): approval can no longer be skipped, Edit / Submit / Cancel flows, Entire Plant at GMP grade, plant-time dates, printable badge configuration for all users, attachment download, edit-only records in Data Maintenance, 70% badge print |
| 15 Oct 2026 | Approval title limited to 40 characters |
| 14 Oct 2026 (platform) | Resubmission resumes at the returning step; per-task-type email policy |

### 9.2 Roadmap (not committed)

| Item | Notes |
|---|---|
| Visitor self-registration and per-person QR check-in | Host sends a link to the visitor company's contact, who registers the visitors; each visitor receives a personal QR code to check in. Under stakeholder review; specified in VMS-SELF-REGISTRATION.md. Nothing of it is in the current system |
| VMS under the Portal permission matrix | Additional roles and `vms.*` permission keys |
| Visit search and paging | VMS-PR-018, VMS-CI-003, VMS-AU-005 |
| Final CFIA report layout; Excel / PDF | Once Compliance confirms the layout |
| Retention policy and archive | VMS-AU-004, PIPEDA |
| French interface and bilingual badges | §8.1 |
| Access-card system integration, self-service kiosk | Requires survey of the existing access-control system |

---

## 10. Success Metrics

### 10.1 Efficiency

| Metric | Baseline (estimate) | Target |
|---|:---:|:---:|
| Check-in time | ~3 min (paper) | < 30 s |
| Monthly audit data collation | ~8 h | < 30 min |
| On-site headcount | ~15 min manual | Real time |
| Badge error rate | ~5% | < 0.5% |

### 10.2 Compliance

| Metric | Target |
|---|:---:|
| GMP-grade visitors with a passing health declaration | 100% (enforced by the print gate) |
| Badge return rate | > 98% |
| Audit log completeness | 100% |
| Record retention | ≥ 3 years |

### 10.3 Satisfaction

| Metric | Target |
|---|:---:|
| Host satisfaction | ≥ 4.0 / 5 |
| Visitor satisfaction | ≥ 4.0 / 5 |

---

## 11. Appendix

### 11.1 Glossary

| Term | Meaning |
|---|---|
| Host | The employee being visited, who books and receives the visitor |
| Check-in | Arrival registration — in VMS, the first badge print |
| Check-out | Departure registration — by scanning the badge QR |
| GMP-grade | GMP Clean Zone, Laboratory, Entire Plant |
| CFIA | Canadian Food Inspection Agency |
| HACCP | Hazard Analysis and Critical Control Points |
| PPE | Personal Protective Equipment |
| PIPEDA | Personal Information Protection and Electronic Documents Act |
| QM | Quality Manager (VMS-local approver) |

### 11.2 References

- CFIA: https://inspection.canada.ca/
- HACCP (Health Canada): https://www.canada.ca/en/health-canada/services/food-nutrition/food-safety/hazard-analysis-critical-control-point.html
- PIPEDA guidance: https://www.priv.gc.ca/
- CSA Z460 (Lockout / Tagout — contractor access to equipment)

### 11.3 Decisions Taken

| # | Topic | Decision |
|:-:|---|---|
| 1 | Approval by access area | §2.1.5 |
| 2 | Default chain | Engine default Department Manager only; production Department Manager → Quality Manager |
| 3 | Quality Manager assignment | First active user on the roster, single approver |
| 4 | Entire Plant | GMP-grade: health declaration required (QA, 2026-10-01) |
| 5 | Self-approval by a booking Department Manager | Kept (2026-10-01) |
| 6 | Deleting VMS records in Data Maintenance | Not allowed; edit-only (2026-10-01) |
| 7 | Training / PPE as a print gate | No — post-entry confirmation tasks (UAT, June 2026) |
| 8 | Report format | CSV until CFIA confirms a layout |
| 9 | Resubmission after a return | Resumes at the returning step; earlier approvals stand (all document types, 2026-10-14) |
| 10 | Training / PPE contacts | Configured; both match active users (checked 2026-09-30) |
| 11 | Health questions | Four built-in defaults in use; editable by Admin; QA sign-off of wording still open (§13) |

### 11.4 Production Snapshot (read-only check, 2026-09-30, plus 2026-10-15 observation)

| Item | Value |
|---|---|
| Approval chain | Department Manager → Quality Manager |
| Quality Manager roster | 1 active user (single point of failure for every GMP-grade visit) |
| Training / PPE contacts | Both set; both match active users |
| Mail | VMS mail settings saved |
| Health questions / badge configuration | Defaults in use |
| Usage to 2026-09-15 | 13 visits, 15 visitors (mostly Office) |
| 2026-10-15 | A five-person Entire Plant contractor visit could not be submitted for approval because the title exceeded 40 characters; fixed the same day. Visits affected by this need "Submit for approval" again |

---

## 12. Defect History

The September 2026 code audit (V2.7) listed defects D-00 to D-22. All except two were fixed in the 1 October 2026 release; the remaining two are decisions or deferrals. Summary:

| ID | Severity | Defect | Outcome |
|---|:---:|---|---|
| D-00 | High | Approval engine crashed on every first VMS submission (unset step number) — combined with D-01, non-office visits were confirmed without approval | Fixed (no production visit was affected) |
| D-01 | High | A failed approval hand-off left the visit Confirmed and printable | Fixed — visit stays Pending; "Submit for approval" |
| D-02 | High | Access area could be changed through the API without re-approval | Fixed — area rules in VMS-PR-016 |
| D-03 | High | "Return for edit" was a dead end with an orphan task | Fixed — Edit visit + Submit for approval |
| D-04 | Medium | Re-filing a declaration overwrote the original | Fixed — original kept in the audit log |
| D-05 | Medium | Badge configuration applied only to admins' prints | Fixed |
| D-06 | Medium | Janitor could not open the "Prepare PPE" task's visit | Fixed — task holders see the visit |
| D-07 | Medium | "Today" computed in UTC | Fixed — plant time |
| D-08 | Medium | Entire Plant inconsistently treated (no health declaration) | Fixed — GMP-grade |
| D-09 | Medium | Cancelling own visit emailed "rejected" | Fixed — "Visit cancelled" |
| D-10 | Medium | Cross-department approver could not open the visit | Fixed — task holders see the visit |
| D-11 | Low | No same-approver skip at submission | Kept by decision |
| D-12 | Low | Pending visits never became No Show | Fixed |
| D-13 | Low | Portal inbox merged VMS tasks; repeated title | Fixed |
| D-14 | Low | Attachment links did not open | Fixed |
| D-15 | Low | Audit-log filters incomplete | Fixed |
| D-16 | Low | Wrong print-blocker message | Fixed |
| D-17 | Low | Dashboard nudge wording | Fixed |
| D-18 | Low | UTC times in emails; after-hours in database time | Fixed |
| D-19 | Low | Manual scheduler run could overlap a scheduled run | Fixed |
| D-20 | Policy | Hard delete of VMS records in Data Maintenance | Fixed by decision — edit-only |
| D-21 | Gap | Primary role only; no VMS permission keys | Deferred (§13) |
| D-22 | Gap | Visitor search matched one column at a time | Fixed |
| D-23 | High | Approval submission failed when the visit title exceeded 40 characters (long visitor name + company) | Fixed 2026-10-15 — title shortened to 40 characters |
| D-24 | Medium | Data Maintenance could edit only Visit date, not the planned arrival / departure. The list and visit page kept the old day, and the no-show job fired on it | Fixed 2026-10-20 — planned times editable, Visit date derived |

---

## 13. Open Items

| ID | Item | Status |
|---|---|---|
| OI-01 | Visitor self-registration via link and per-person QR check-in (VMS-SELF-REGISTRATION.md) | Not implemented |
| OI-02 | Approval delegate sees the delegated VMS approval task but gets "Visit not found" when opening it, so cannot approve from VMS (visibility counts only tasks assigned directly to the user) | Defect |
| OI-03 | Any signed-in user can edit any visitor record through the API, including the permanent "ID verified" flag (changes are audit-logged) | Defect |
| OI-04 | VMS honours only the primary role; no `vms.*` permission keys (D-21) | Open |
| OI-05 | Should auditors be able to create visits, verify ID and print badges (= check visitors in)? Not blocked today | Open |
| OI-06 | Quality Manager roster has one active person — single point of failure | Open |
| OI-07 | Host approval-result email and post-approval PPE email are triggered by the first read of the visit, not at the moment of approval | Open |
| OI-08 | VMS task emails through the platform outbox are off by default and link to the VMS home page rather than the visit; enabling "Check out visitor" / "Prepare PPE" would duplicate VMS's own emails | Open |
| OI-09 | Host email on submission (VMS-PR-011) | Not implemented |
| OI-10 | Visitor confirmation email (VMS-PR-014) | Not implemented |
| OI-11 | Copy last appointment (VMS-PR-009); batch import (VMS-PR-010) | Not implemented |
| OI-12 | Visit search / filter (VMS-PR-018, VMS-CI-003, VMS-AU-005); All Visits limited to latest 50 with no paging | Not implemented |
| OI-13 | Change the visitor list of an existing visit (VMS-PR-033) | Not implemented |
| OI-14 | Accompanying count, licence plate, equipment captured at check-in (VMS-CI-004) | Not implemented |
| OI-15 | Server-side enforcement of ID verification; ID verification is permanent per visitor rather than per visit (VMS-CI-005) | Open |
| OI-16 | Automatic restricted status / downgrade for a failed declaration (VMS-CI-011) | Not implemented |
| OI-17 | PPE issuance record at check-in (VMS-CI-013) | Not implemented |
| OI-18 | Per-area confirmations: hard hat / safety shoes (Warehouse), dress code (Non-GMP), lab safety briefing (Laboratory) | Not implemented |
| OI-19 | Check-in recorded before the print dialog rather than after (VMS-LB-007) | Open |
| OI-20 | "Escort Required" indicator (VMS-LB-003); multiple badge templates (VMS-LB-011); bilingual badges (VMS-LB-012) | Not implemented |
| OI-21 | "Visitor has left" confirmation (VMS-CO-008); business-hours escalation (VMS-CO-011); custom reason on batch check-out (VMS-CO-014) | Not implemented |
| OI-22 | Audit retention ≥ 3 years and archive (VMS-AU-004); PIPEDA retention period to be defined | Not implemented |
| OI-23 | Excel / PDF export (VMS-AU-006); final CFIA report layout | Open |
| OI-24 | CFIA Visit Log health / training columns show the visit-level value, not each visitor's own | Open |
| OI-25 | Contractor Access and Monthly Statistics reports (VMS-AU-009); scheduled report emails (VMS-AU-012) | Not implemented |
| OI-26 | Visitor profile with visit history (VMS-AU-007); full-text search (VMS-AU-008) | Not implemented |
| OI-27 | Mandatory e-signature (VMS-AU-013); reason for critical-field changes (VMS-AU-014); export hash (VMS-AU-015) | Not implemented |
| OI-28 | Dashboard counters are plant-wide and visible to every user | Open |
| OI-29 | Encryption at rest of health-declaration data | Not implemented |
| OI-30 | French interface, bilingual notifications and reports | Not implemented |
| OI-31 | Microsoft Teams notifications for VMS | Not implemented |
| OI-32 | Access-card system integration; self-service kiosk | Not implemented |
| OI-33 | QA sign-off of the health-declaration questions | Open |
| OI-34 | Paper visitor-register template and historical data migration; printer model confirmation | Open |
| OI-35 | A visit created (or rescheduled) with a planned arrival already more than 2 hours past is marked No Show by the next scheduler run, and a pending approval is cancelled with it. Seen 2026-10-20: created 16:30 for 08:00 the same day, No Show 3 minutes later | Closed 2026-10-20 — No Show now waits until the arrival day is over |

---

## 14. Revision History

| Version | Date | Summary |
|---|---|---|
| V1.0 | Apr 2026 | First draft from five core requirements (pre-registration, on-site registration, badges, departure, audit) |
| V2.0 | May 2026 | Rewritten as a native UniOps module |
| V2.1 | May 2026 | No receptionist / security posts: full Host self-service; browser badge printing; QR check-out; role model reduced to existing UniOps roles |
| V2.2 | May 2026 | Training / PPE notification contacts; configurable approval chain |
| V2.3 | May 2026 | Reconciled with the platform: shared database with `vms_` tables, shared approval engine with a VMS-local Quality Manager, public employee directory, Portal launcher and Task Inbox |
| V2.4 | Jun 2026 | Multi-visitor visits; phone optional; Host defaults to current user; one badge per visitor; CFIA log one row per visitor |
| V2.5 | Jun 2026 | Host opt-in PPE per visitor; per-visitor training / PPE compliance with HR / Janitor tasks; in-VMS approval and Task Inbox; VMS mail settings |
| V2.6 | Jun 2026 | Compliance as post-entry tasks, not a print gate; streamed CSV reports; background scheduler |
| V2.7 | Sep 2026 | Full as-built audit: status on every requirement, as-built rules, permission matrix, defects D-01..D-22 |
| V2.8 | Oct 2026 | Fixes for the V2.7 defects (released 1 Oct 2026); D-00 found and fixed |
| V2.9 | 16 Oct 2026 | V2.8 folded into the body; 40-character approval title; resubmission resumes at the returning step; platform notification outbox; new findings (delegation visibility, visitor edit rights); Open Items consolidated; self-registration cross-referenced as not built; implementation detail removed |
| V2.10 | 19 Oct 2026 | Column-header sorting on visit lists, audit log, health declarations and active visits; All Visits and Today page on the server |
| V2.11 | 20 Oct 2026 | Data Maintenance reschedules visits through planned arrival / departure; Visit date derived (D-24); new open item OI-35 (past planned arrival is no-showed at once) |
| V2.12 | 20 Oct 2026 | Rescheduling a No Show visit in Data Maintenance undoes the No Show and reopens a pending approval |
| V2.13 | 20 Oct 2026 | No Show marked after the planned arrival day ends (plant time) instead of 2 hours after arrival; OI-35 closed |
