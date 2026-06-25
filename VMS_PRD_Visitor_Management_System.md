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

**Document Version**: V2.5  
**Creation Date**: April 2026 (First Draft) / May 2026 (V2.0 — UniOps Integration) / May 2026 (V2.1 — Role Model Refactoring) / May 2026 (V2.2 — Approval Workflow Customization + Notification Contacts) / May 2026 (V2.3 — Integration Reconciliation: port → 8008, schema flattened to `public.vms_*`, approval-api cfm-style integration, VMS-local quality_manager, Portal Module + Task Inbox integration) / June 2026 (V2.4 — Multi-visitor visits, phone optional, Host defaults to current user, badge print loops per visitor, CFIA report one row per visitor) / June 2026 (V2.5 — Host-opt-in per-visitor PPE requests, per-visitor training/PPE compliance with 12-month TTL + HR/Janitor confirmation tasks, in-VMS approval actions + Task Inbox, VMS-local SMTP settings, training/PPE notifications deferred to post-approval)  
**Document Status**: Revised — V2.5 captures compliance + notification workflow changes from operator UAT (Justin / Reception / HR / Janitor) during S2-E rollout  
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
| **Employee (Host)** | `requester` (default) | Invite visitors, pre-register, print badges (= check-in), scan QR code to check out, view own visitor records | Appointment creation, badge printing, QR check-out, visitor history |
| **Department Manager** | `dept_manager` | All Host permissions + approve high-risk area access + view department visitor stats | Access approval, department statistics |
| **Auditor** | `auditor` | Export audit data, inspect compliance (read-only) | Audit log query, compliance report export |
| **System Administrator** | `system_admin` | System configuration, user management, badge template maintenance, full data access | Admin backend, badge template management |

> **Note**: VMS does not define standalone receptionist or security roles. All hosting operations (check-in, badge printing, check-out) are performed by the Host in a self-service model. The four roles above all reuse the existing UniOps `public.users.role` field — no new roles are created.

---

## 2. Core Functional Modules

### 2.1 Visitor Pre-Registration

#### 2.1.1 Overview

Employees (Hosts) can pre-register upcoming visitor information in the system to create appointment records. Pre-registered records can be retrieved upon visitor arrival to accelerate the on-site process.

#### 2.1.2 Detailed Requirements

##### (1) Appointment Creation

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-PR-001 | Host can create a visitor appointment with basic info: name (first / last, required), company (required), job title (optional), phone (optional — see V2.4 note), email (optional) | P0 (Must) |
| VMS-PR-002 | Select visitor type: Supplier, Contractor, Inspector, Auditor, Customer, Job Candidate, Other | P0 |
| VMS-PR-003 | Set visit date, planned arrival time, planned departure time | P0 |
| VMS-PR-004 | Select visit purpose: Business Meeting, Equipment Maintenance, Factory Tour, Audit/Inspection, Interview, Delivery, Other | P0 |
| VMS-PR-005 | Specify the Host (person being visited): select from employee directory with search. **Host defaults to the currently logged-in user** (the "I'm hosting this visitor" common case); operator can pick a different employee via the "Change" affordance | P0 |
| VMS-PR-006 | Select access area: Office, Production A, Production B, Warehouse, Laboratory, Entire Plant | P0 |
| VMS-PR-007 | For food production areas (GMP zones), the system automatically triggers additional requirements: whether a health questionnaire is needed, whether food safety training confirmation is needed → notifies Training Contact (see VMS-PR-021), whether PPE is needed → notifies PPE Contact (see VMS-PR-022) | P1 (Important) |
| VMS-PR-008 | Upload attachments: visitor ID, work permit, insurance certificate, etc. | P2 (General) |
| VMS-PR-009 | Appointment copy: for frequent visitors, copy the last appointment with one click | P2 |
| VMS-PR-010 | Batch import: import multiple appointments via Excel template | P2 |

##### (1.0.1) Multi-Visitor Visits (added in V2.4)

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-PR-030 | A single appointment can carry **one primary visitor + N companions**. Common case: a supplier tour where 3–5 people from the same company arrive together. Host registers them under one appointment (one date, one access area, one Host) | P0 |
| VMS-PR-031 | Companions share the appointment's schedule, access area, visit purpose, and Host with the primary visitor. Each companion keeps their own Visitor row (own name, company, optional phone/email, own ID-verification flag) | P0 |
| VMS-PR-032 | The New Visit form supports adding companions via an "Add another visitor" affordance (chip layout). Operator can search the visitor registry or register a new visitor inline for any slot. Single-visitor visits leave the companion list empty — backward-compatible with V2.3 visits | P0 |
| VMS-PR-033 | The visit detail page shows all visitors (primary + companions) with each visitor's ID-verified status. Removing the primary promotes the first companion so a visit always has a primary visitor | P1 |

> **Implementation note**: stored as `vms_visits.additional_visitor_ids JSONB` (UUID list). Single-visitor visits = empty list. We chose JSONB over a proper M2M join table to keep existing queries against `vms_visits` working unchanged (reports, search, badge printing iterate the list explicitly). Migrate to a join table if downstream consumers need to filter or join companions in SQL.

##### (1.1) Notification Contact Configuration (Admin)

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-PR-020 | VMS Admin panel provides "Notification Contacts" configuration: **Training Contact Email** (HR-designated person, notified when a visitor requires food safety training) and **PPE Contact Email** (Janitor-designated person, notified when a visitor requires personal protective equipment) | P0 |
| VMS-PR-021 | When a GMP/Lab visit's access area triggers training requirements (see VMS-PR-007), the system sends an email to the Training Contact **after the visit is approved** (not at create time — see V2.5 note), including: visitor name, visit date, access area, Host name. Skipped when the visitor's training record is still fresh (VMS-CI-021) | P0 |
| VMS-PR-022 | When the Host opts into PPE staging (VMS-PR-040), the system emails the PPE Contact **after the visit is approved** with the per-visitor gear list (clothing size, footwear, shoe size). For auto-confirming visits (office / warehouse) the email fires immediately on create | P0 |
| VMS-PR-023 | Training Contact and PPE Contact email addresses can be modified by Admin in the VMS admin panel at any time; changes take effect immediately | P1 |
| VMS-PR-024 | VMS Admin panel provides an **Email Settings** tab: VMS-local SMTP host / port / user / password / STARTTLS / from-address. When set, VMS uses these for all outbound mail instead of the shared EPMS `company_config` SMTP. Includes a "Send test email" action that exercises the production send path. Password is masked on read; the literal mask round-trips as "keep existing" (V2.5) | P0 |

> **V2.5 deferral rule**: Training and PPE notifications (VMS-PR-021/022) and the HR/Janitor confirmation tasks (VMS-CI-022) fire **only after the visit is approved**, not at appointment creation. Rationale: every compliance-requiring area (GMP / Lab / all-zones) routes through approval, and pinging HR / Janitor for a visit that may still be rejected creates noise + wasted prep. Implementation: read-driven hook fires once when the visit first reaches the approved state (same mechanism as the Host result email).

##### (1.2) Per-Visitor PPE Request (added in V2.5)

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-PR-040 | At visit creation the Host can tick **"PPE needed"**. PPE is opt-in per visit — it is no longer auto-triggered by access area alone | P0 |
| VMS-PR-041 | When PPE is needed, the Host specifies, **per visitor on the appointment** (primary + companions): clothing size (XS / S / M / L / XL / XXL / other-with-free-text) and footwear (shoe covers, or safety shoes with US shoe size 7–14 / other-with-free-text) | P0 |
| VMS-PR-042 | An optional group-level notes field applies to the whole PPE request | P1 |
| VMS-PR-043 | After approval, the Janitor PPE Contact receives one email listing each visitor's name + requested gear so they can pre-stage everything in one trip. Idempotent — sent once (`ppe_notified_at`) | P0 |

##### (2) Access Area Control Rules

| Access Area | Risk Level | Pre-Registration Requirements |
|-------------|:----------:|-------------------------------|
| Office (Lobby / Office) | Low | Basic info only |
| Warehouse | Medium | Basic + hard hat / safety shoes confirmation |
| Production (Non-GMP) | Medium | Basic + dress code confirmation |
| Production (GMP / Clean Zone) | High | Basic + health declaration + food safety training + gowning procedure |
| Laboratory | High | Basic + lab safety briefing confirmation |

##### (3) Notification Mechanism

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-PR-011 | After appointment submission, system auto-sends email notification to Host | P0 |
| VMS-PR-012 | One day before visit, system auto-sends reminder to Host | P1 |
| VMS-PR-013 | Approval (if needed): high-risk area visits require Department Manager approval; Host is notified upon approval | P1 |
| VMS-PR-014 | Visitor receives confirmation email with visit instructions, navigation, parking info | P2 |

##### (4) Appointment Management

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-PR-015 | Status lifecycle: Pending Approval → Confirmed → Checked In → Checked Out → Cancelled | P0 |
| VMS-PR-016 | Host can modify/cancel their own appointments (only before check-in) | P1 |
| VMS-PR-017 | Host can view all their own appointments sorted by time; Manager can view all department appointments | P0 |
| VMS-PR-018 | Global search: by visitor name, company, Host, date range | P1 |
| VMS-PR-019 | Auto-mark no-show: appointments not checked in within 2 hours of planned arrival are marked "No Show" | P2 |

---

### 2.2 Visitor Check-In (via Badge Printing)

#### 2.2.1 Overview

VMS has no receptionist role. **Check-in is unified with badge printing**: when the Host prints the visitor badge, the system automatically completes check-in (records `actual_arrival`, status → `checked_in`).

The Host can print badges in two scenarios:
- **Desk Printing**: Host opens VMS on their office computer → finds the appointment → clicks "Print Badge" → browser prints → brings the badge downstairs to meet the visitor
- **Public Computer Printing**: Host goes to the shared computer downstairs, logs into VMS → finds the appointment → clicks "Print Badge" → prints on-site and hands the badge to the visitor

#### 2.2.2 Detailed Requirements

##### (1) Print = Check-In (With Appointment)

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CI-001 | Host finds the appointment in "My Appointments" or "Today's Appointments", clicks "Print Badge". System automatically: ① generates badge page → ② triggers browser print → ③ records `actual_arrival` → ④ status → `checked_in` | P0 |
| VMS-CI-002 | System records actual arrival time (to the second), equal to badge print time | P0 |
| VMS-CI-003 | Quick search by name / company | P0 |
| VMS-CI-004 | Before printing, Host can supplement/correct: accompanying count, license plate, equipment carried | P1 |
| VMS-CI-005 | Before printing, Host confirms they have verified the visitor's photo ID (driver's license, passport, etc.) and checks "ID Verified" | P1 |

##### (2) Instant Registration (No Appointment)

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CI-006 | For walk-in visitors, Host can instantly create a visitor record + print badge in one action (one-click registration + check-in) | P0 |
| VMS-CI-007 | Instant registration form contains the same required fields as pre-registration | P0 |
| VMS-CI-008 | System auto-associates the currently logged-in Host as the visited person | P1 |

##### (3) Health & Safety Confirmation (High Priority — Food Factory Specific)

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CI-010 | For GMP area visitors, before printing the badge the system forces a health declaration questionnaire: Have you had fever, cough, or diarrhea in the past 24 hours? Do you have open wounds or skin infections? Have you been in contact with anyone with an infectious disease? Are you carrying food allergen substances? The Host verbally asks the visitor and fills it in, or the visitor fills it in on the public computer | P0 |
| VMS-CI-011 | Visitors who fail the health declaration are marked "Restricted Access" — office area only; badge area indicator is automatically downgraded | P0 |
| VMS-CI-012 | Food safety training confirmation: GMP area visitors must confirm they have read and understood basic food safety requirements (Host confirmation or visitor e-signature) | P0 |
| VMS-CI-013 | PPE issuance record: Host can record equipment issued to the visitor (hard hat, protective clothing, shoe covers, etc.) | P1 |

##### (4) Per-Visitor Training + PPE Compliance (added in V2.5)

> Training and PPE compliance are tracked **on the Visitor record**, not per-visit — a frequent supplier who trained last month shouldn't re-train every visit. A 12-month rolling freshness window applies.

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CI-020 | Each Visitor carries `safety_training_confirmed_at` / `_by` and `ppe_issued_at` / `_by` timestamps. A record is "fresh" if confirmed within the last 12 months (window lives in code, not schema) | P0 |
| VMS-CI-021 | For a GMP / Lab visit, the badge cannot print until **every** visitor on the appointment (primary + companions) has a fresh training record AND a fresh PPE record. The print endpoint returns 422 naming the visitor + missing gate | P0 |
| VMS-CI-022 | After approval, the system opens a confirmation Task for each stale gate: a **training task** assigned to the HR Training Contact and a **PPE task** assigned to the Janitor PPE Contact. Tasks appear in the unified Portal Task Inbox and the VMS Task Inbox, deep-linking to the visitor's compliance page | P0 |
| VMS-CI-023 | Only the configured contact (HR for training, Janitor for PPE) — or a system_admin — can confirm the respective gate. Confirmation stamps the visitor's timestamp, completes the open task(s), and is audit-logged | P0 |
| VMS-CI-024 | The visitor compliance page (and the VisitDetail compliance banner) show each gate's freshness + last-confirmed date; stale gates link to the confirm action | P1 |

---

### 2.3 Visitor Badge Printing

#### 2.3.1 Overview

When the Host prints the badge, the system generates a browser print page with a dedicated badge layout. The Host prints to a standard office printer (A4 / Letter paper), cuts out the badge card, and inserts it into a standard visitor badge holder.

#### 2.3.2 Detailed Requirements

##### (1) Badge Content

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-LB-001 | Badge must include: Visitor full name (large font, prominent), Company name, Visit date, Validity period (day-specific / time window), Host name, Access area (color-coded), QR Code (containing visit UUID) | P0 |
| VMS-LB-002 | Area color coding: 🟢 Green = Office, 🟡 Yellow = Warehouse/Non-Production, 🟠 Orange = Production Non-GMP, 🔴 Red = GMP Clean Zone / Laboratory | P0 |
| VMS-LB-003 | "Escort Required" indicator when applicable | P1 |
| VMS-LB-004 | Footer text: "Must be accompanied by Host at all times", "Please return badge when leaving" | P1 |

##### (2) Printing Method

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-LB-005 | Browser native printing (`window.print()` + CSS `@media print`), output to standard office printer (A4 / Letter paper) | P0 |
| VMS-LB-006 | Print page uses a dedicated badge card layout; CSS controls sizing so one A4 sheet can fit 1–2 badge cards. Cut out and insert into a standard badge holder | P0 |
| VMS-LB-007 | Clicking "Print Badge": ① opens browser print dialog → ② user confirms → ③ system auto-executes check-in (records `actual_arrival` + `status=checked_in`) | P0 |
| VMS-LB-008 | Manual reprint supported (damaged badge, info change, etc.); reprint does NOT re-check-in | P1 |
| VMS-LB-009 | Badge print log: timestamp, printed by, print count, reprint reason | P2 |
| VMS-LB-013 | **Multi-visitor visits print one badge per visitor** (V2.4). The print page lays out N badge cards (primary + companions) with `page-break-after: always` between them; the single browser print dialog covers all of them. Check-in is a single event for the appointment (one `actual_arrival` timestamp for the visit row) | P0 |

> **Design Decision**: No dedicated label printer integration (Zebra/Brother/DYMO). Rationale: ① eliminates hardware procurement and maintenance cost; ② Host can print from their own desk without going to a specific printer; ③ plain paper + badge holder solution adequately meets visitor badging needs.

##### (3) Badge Template Management

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-LB-010 | Badge templates are HTML/CSS snippets; Admin can edit layout, field positions, font sizes, company logo via admin backend | P1 |
| VMS-LB-011 | Multiple templates supported: Standard Visitor, VIP Visitor, Contractor, etc., differentiated by CSS classes | P2 |
| VMS-LB-012 | Bilingual templates (English / French) | P2 |

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

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CO-001 | Badge QR Code contains the visit UUID. Host scans it; system auto-locates the visit record | P0 |
| VMS-CO-002 | Desktop: barcode scanner input or manual QR code entry into search field; displays visit details + confirmation button | P0 |
| VMS-CO-003 | Mobile: browser calls `getUserMedia` API to open camera, scans QR code in real-time; auto-popup confirmation dialog on recognition | P0 |
| VMS-CO-004 | Confirming check-out: ① system records `actual_departure` → ② status → `checked_out` → ③ badge return status recorded | P0 |

##### (2) Departure Confirmation

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CO-005 | System records actual departure time (to the second) | P0 |
| VMS-CO-006 | Host confirms badge returned; system records return status | P1 |
| VMS-CO-007 | PPE return confirmation: records whether hard hat, protective clothing, etc. have been returned | P1 |
| VMS-CO-008 | After check-out, system can send Host a "Your visitor has left" confirmation | P2 |

##### (3) Overtime Alerts

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CO-009 | Visitors still checked in past planned departure are auto-marked "Overdue" | P1 |
| VMS-CO-010 | 1 hour overdue: system sends reminder to Host | P1 |
| VMS-CO-011 | 4 hours overdue or past business hours: system escalates notification to Department Manager | P2 |
| VMS-CO-012 | On-site visitor dashboard: Host and Manager can view all currently on-site visitors and their stay duration | P1 |

##### (4) Manual / Batch Check-Out

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-CO-013 | End-of-day one-click batch check-out for all on-site visitors (with secondary confirmation) | P1 |
| VMS-CO-014 | Batch check-out auto-logged as "System Batch Check-Out" with reason recorded | P2 |

---

### 2.5 Audit Trail

#### 2.5.1 Overview

The system must provide a complete audit trail — recording all user operations and visitor data change history — to satisfy CFIA, HACCP, GMP food safety audits and internal compliance audits.

#### 2.5.2 Detailed Requirements

##### (1) Operation Audit Log

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-AU-001 | Auto-log all user operations: appointment create/modify/cancel, visitor check-in/check-out, badge print/reprint, info modification, permission changes, data export | P0 |
| VMS-AU-002 | Each log entry includes: timestamp (to second), user, IP address, action type, entity type, entity ID, before/after values (JSON) | P0 |
| VMS-AU-003 | Audit logs are immutable (Write-Once, Read-Many); query and export only | P0 |
| VMS-AU-004 | Retention: minimum 3 years (configurable); auto-archive on expiry | P1 |

##### (2) Visitor History Query

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-AU-005 | Search by: visitor name/company, date range, Host, access area, visitor type, visit status | P0 |
| VMS-AU-006 | Export results to Excel / PDF | P0 |
| VMS-AU-007 | Single visitor profile view: all historical visits, health declarations, training confirmations | P1 |
| VMS-AU-008 | Full-text search: visitor notes, visit purpose, etc. | P2 |

##### (3) Compliance Audit Reports

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-AU-009 | Pre-built report templates: CFIA Visit Log Report (CFIA-format production area visitor records — **emits one row per (visit, visitor)** so multi-visitor visits expand to N rows and the regulator headcount matches the actual on-site presence, V2.4), GMP Area Access Summary (clean zone visitor count, frequency, health decl compliance rate — counts visits / appointments, not individual visitors), Contractor Access Report, Monthly Visitor Statistics (by type, area, department) | P0 |
| VMS-AU-010 | One-click report generation with time range and filter selection | P0 |
| VMS-AU-011 | Report formats: PDF (formal archive) and Excel (data analysis) | P0 |
| VMS-AU-012 | Scheduled auto-generation and email delivery to designated personnel (e.g., monthly compliance report) | P2 |

##### (4) Data Integrity

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-AU-013 | Electronic signatures: health declarations and safety training confirmations require visitor e-signature; signature is bound to the record and tamper-proof | P0 |
| VMS-AU-014 | Data validation: modifications to critical fields (access area, arrival time) must record reason and retain original value | P1 |
| VMS-AU-015 | Export includes hash checksum to verify data has not been tampered with | P2 |

##### (5) Audit Dashboard

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-AU-016 | Dashboard displays key compliance indicators: monthly GMP area visits and health decl pass rate, unreturned badge count, overdue visitor count, abnormal visits (e.g., after-hours) | P1 |
| VMS-AU-017 | Drill-down support: click a metric to see detailed records | P1 |

---

## 3. User Roles and Permissions

> **Core Design**: VMS reuses the UniOps `public.users` table as the user master. **Any authenticated UniOps user (regardless of role) can act as a Host** — appointment creation, badge printing, and QR check-out are open to all employees. Three platform-level permission tiers (Host / Auditor / Admin) map to existing UniOps roles in [VALID_ROLES](epms-api/app/schemas/user.py#L7-L12). One VMS-local role (**Quality Manager**) is defined inside VMS only — not added to the UniOps `VALID_ROLES` set.

### 3.1 Role Definitions

| VMS Role | Source | Permission Scope | Typical Function |
|----------|:------:|------------------|------------------|
| **Host (Employee)** | Any UniOps role (default: all logged-in users) | Create / modify / cancel own appointments, print badges (= check-in), QR scan check-out, view own visitor history as Host | All employees |
| **Auditor** | UniOps role `auditor` | Read-only: view all audit logs, export audit reports, query visitor history | Compliance / audit personnel |
| **Admin (System Admin)** | UniOps role `system_admin` | All functions + system configuration + notification contact management + badge template management + Quality Manager roster management | IT administrators |
| **Quality Manager** *(VMS-local)* | Configured in VMS admin panel; references `public.users.id` | Second-step approver for GMP / Laboratory access requests (see §6.2.1). Not a UniOps platform role; not added to `VALID_ROLES`. | QC / QA personnel designated by Admin |

> **Note 1 — Host eligibility**: VMS does not restrict Host creation by UniOps role. Any active user in `public.users` — `requester`, `dept_manager`, `gm`, `opm`, `procurement_manager`, `cfo`, etc. — can create visit appointments. Department-scoped visibility (a manager seeing department stats) is enforced via the `department_id` field, not via a separate role gate.
>
> **Note 2 — Quality Manager as VMS-local role**: The UniOps platform does not have a `quality_manager` role. Instead, VMS Admin maintains a roster (stored in `vms_config.quality_manager_user_ids: JSONB`) of UniOps users designated as Quality Managers. When a visit needs GMP-zone approval, vms-api resolves the second-step approver by reading this roster and passes a concrete `user_id` to approval-api at submit-time (see §6.2.1). This keeps the UniOps role system untouched while supporting the dual-approval workflow.

### 3.2 Permission Matrix (Core Functions)

> Manager-level permissions (approve high-risk access, view department stats) are derived from the underlying UniOps role of the logged-in user: a user with UniOps role `dept_manager` automatically gets dept-scoped views. There is no separate "VMS Manager" gate.

| Function | Admin | Host (any user) | Auditor | Quality Manager *(VMS-local)* |
|----------|:-----:|:---------------:|:-------:|:------------------------------:|
| Create appointment | ✓ | ✓ | ✗ | ✓ (as Host) |
| Modify / cancel appointment | ✓ | Own only | ✗ | Own only |
| Print badge (= check-in) | ✓ | Own only | ✗ | Own only |
| QR scan check-out | ✓ | Own only | ✗ | Own only |
| View on-site visitors | ✓ | ✓ | ✓ | ✓ |
| Query visitor history | ✓ | Own only (Dept-wide if UniOps role is `dept_manager`) | ✓ | Dept-wide |
| Export audit reports | ✓ | ✗ | ✓ | ✗ |
| View operation logs | ✓ | ✗ | ✓ | ✗ |
| Approve dept_manager step | ✗ | Auto (if UniOps role is `dept_manager` and dept matches) | ✗ | ✗ |
| Approve Quality Manager step (GMP / Lab) | ✗ | ✗ | ✗ | ✓ (only for visits assigned to them) |
| Quality Manager roster management | ✓ | ✗ | ✗ | ✗ |
| Badge template management | ✓ | ✗ | ✗ | ✗ |

---

## 4. User Interaction Flows

### 4.1 Standard Visitor Flow (With Appointment)

```
[Host creates appointment]
    ↓
[Fill in visitor info, visit time, access area]
    ↓
[High-risk area?]
    ├─ Yes → [Submit for approval] → [Manager approves] → [Approved]
    └─ No → [Direct confirmation]
    ↓
[System sends confirmation email to visitor and Host]
    ↓
[1 day before visit: system sends reminder]
    ↓
================ Visitor Arrival Day ================
    ↓
[Visitor arrives, calls Host]
    ↓
[Host opens VMS]
    ├─ Desk scenario: open VMS on office computer, find appointment
    └─ Public PC scenario: go to shared computer downstairs, log into VMS, find appointment
    ↓
[Host clicks "Print Badge"]
    ↓
[Identity verification: Host confirms they have checked visitor's photo ID]
    ↓
[GMP area access?]
    ├─ Yes → [Health declaration questionnaire pops up] → [Host asks visitor verbally and fills in / visitor fills in on public PC]
    │       ↓ [Fail → area restricted, badge zone indicator auto-downgraded]
    │       ↓ [Pass]
    │       [Food safety training confirmation pops up] → [Visitor e-signature]
    │       ↓
    └─ No → [Continue]
    ↓
[Browser opens print dialog → Host confirms print]
    ↓
[System auto-completes check-in: records actual_arrival + status=checked_in]
    ↓
[Host takes printed badge, inserts into card holder]
    ↓
[Host goes downstairs to greet visitor, hands over badge + PPE if needed]
    ↓
================ Visitor On-Site ================
    ↓
[System displays: on-site visitors + stay duration]
[Overtime auto-alert → notify Host]
    ↓
================ Visitor Departure ================
    ↓
[Host opens VMS Check-Out page on desktop or mobile]
    ↓
[Scan badge QR code]
    ├─ Desktop: barcode scanner / manual input
    └─ Mobile: browser camera scans QR code
    ↓
[System identifies visit → confirmation dialog pops up]
    ↓
[Host confirms departure → confirms badge returned → confirms PPE returned]
    ↓
[System records departure: actual_departure + status=checked_out]
    ↓
[Visit record archived]
```

### 4.2 Instant Registration Flow (No Appointment)

```
[Visitor arrives, calls Host, no appointment]
    ↓
[Host opens VMS, clicks "Instant Registration"]
    ↓
[Fill in visitor basic info (name, company, phone, etc.)]
    ↓
[Host auto-set as visited person]
[Select access area, visit purpose]
    ↓
[GMP area? → Health declaration + training confirmation]
    ↓
[Click "Print Badge" → browser print → auto check-in]
    ↓
[Host brings badge downstairs to greet visitor]
    ↓
[Same as standard flow from this point]
```

### 4.3 Host Creates Appointment (Web)

```
[Host logs in]
    ↓
[Clicks "New Appointment"]
    ↓
[Enter visitor information]
    ├─ First-time visitor: complete all fields
    └─ Returning visitor: select/search from history → auto-fill
    ↓
[Select visit date and time]
    ↓
[Select access area]
    ↓
[Visit purpose notes (optional)]
    ↓
[Upload attachment (optional)]
    ↓
[Submit appointment]
    ↓
[System auto-notifies: visitor confirmation email]
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
|  badge_templates (JSONB)                         |
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

    # ── Approval workflow fields (consumed by approval-api, see §6.2.1) ────────
    # Mirror fields that approval-api reads/writes via its thin Visit model.
    approval_step_idx:       Mapped[int | None] = mapped_column(Integer, nullable=True)  # current step in workflow_defs["vms_visit"]
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
    questionnaire_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result:             Mapped[HealthDeclStatus] = mapped_column(SAEnum(HealthDeclStatus), nullable=False)
    signature:          Mapped[str | None] = mapped_column(Text, nullable=True)

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
| `public` | `vms_health_declarations` | Health declarations | Required for GMP areas |
| `public` | `vms_audit_logs` | Audit logs | BIGINT auto-increment, immutable (enforced via DB `REVOKE UPDATE, DELETE` migration; see §2.5.2 VMS-AU-003) |
| `public` | `vms_config` | VMS system configuration (notification contact emails, `quality_manager_user_ids: JSONB`, badge templates, `smtp_settings: JSONB` (V2.5 — VMS-local outbound mail), health questions) | Admin-managed |
| `public` | `users` | Employees (reused) | epms-api existing table; no new table created |
| `public` | `tasks` | Approval + compliance tasks (reused) | approval-api existing table; populated with `doc_type` in {`vms_visit`, `vms_train`, `vms_ppe`} rows |
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
| `warehouse`, `production_non_gmp` | Yes | All steps from `workflow_defs["vms_visit"]` except `quality_manager` |
| `production_gmp`, `laboratory`, `all` | Yes | All steps including `quality_manager` |

**Submit sequence**:

1. Host creates visit → vms-api persists `vms_visits` row with `status="confirmed"` (or `"pending_approval"` if approval needed).
2. If GMP/Lab: vms-api reads `vms_config.quality_manager_user_ids`, picks one (round-robin or first-active), and writes the chosen `user_id` into `vms_visits.quality_approver_id`.
3. vms-api calls approval-api: `POST /approval/v1/approvals/vms_visit/{visit_id}/action` with `{"action": "submit"}` (the real endpoint — there is no `/approval/v1/submit`).
4. approval-api applies `_DOC_META["vms_visit"]`, advances `approval_step_idx`, creates a `tasks` row for the resolved step approver:
   - `dept_manager` step → resolved via existing `_get_dept_manager_id` lookup on the Host's department
   - `quality_manager` step → uses `quality_approver_id` directly
5. Approver acts via **either** the Portal Task Inbox (§6.8) **or the in-VMS approval UI** (V2.5). Both call `POST /api/v1/visits/{id}/action` (vms-api proxy → approval-api) with `approve` / `reject` / `return` + optional comment. The assigned approver sees Approve / Reject / Return buttons on the VisitDetail page when a task for that visit is in their inbox.
6. On final approve: approval-api flips `vms_visits.status` to `confirmed`; on `return` / `reject`: VMS notifies Host. No callback endpoint needed — approval-api updates the shared row directly.

> **No new auto-skip needed**: existing approval-api auto-skip logic (same user holds consecutive roles) still applies. If the Host is also the `dept_manager` of their own department, that step is skipped automatically.

> **V2.5 quality_manager step fix**: the seeded `vms_visit` workflow now includes the `quality_manager` step in `company_config.workflow_defs` (previously only `dept_manager` was seeded, so the QM step never fired). The engine auto-skips the QM step when `quality_approver_id is None` (non-GMP areas). The assigned QM also gets explicit visibility on the visit (`Visit.quality_approver_id == user_id`) so the task deep-link doesn't 404 for an approver whose UniOps role has no dept claim on the visit.

##### (4.1) In-VMS Approval + Task Inbox (added in V2.5)

| Requirement ID | Description | Priority |
|----------------|-------------|----------|
| VMS-AP-001 | VMS has its own **Task Inbox** page listing the current user's pending VMS tasks (visit approvals `vms_visit`, training confirmations `vms_train`, PPE confirmations `vms_ppe`), sourced from epms-api `/tasks` filtered to VMS doc types. Sidebar shows a live unread badge | P0 |
| VMS-AP-002 | The VisitDetail page shows Approve / Reject / Return controls when the current user is the assigned approver for that visit's pending step. Reject / Return require a comment | P0 |
| VMS-AP-003 | The Dashboard surfaces a "visits waiting on your approval" nudge when the user has pending VMS approval tasks | P1 |
| VMS-AP-004 | VMS doc types (`vms_visit` / `vms_train` / `vms_ppe`) are excluded from the **EPMS** task inbox (they belong to VMS), while the **Portal** unified inbox shows them with correct deep-links into the VMS frontend | P0 |

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

### 6.5 API Endpoint Design (UniOps REST Specification)

> All endpoints prefixed `/api/v1/`, authentication `Authorization: Bearer <JWT>`, fully consistent with epms-api / expense-api.

#### 6.5.1 Visitors

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/api/v1/visitors` | Search visitors `?search=&type=&page=1&page_size=20` | Host |
| `GET` | `/api/v1/visitors/{id}` | Visitor details + visit history | Host |
| `POST` | `/api/v1/visitors` | Create visitor (instant registration) | Host |
| `PATCH` | `/api/v1/visitors/{id}` | Update visitor info | Host, Admin |

#### 6.5.2 Visits

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/api/v1/visits` | Query `?date=&status=&host_id=&area=&page=1&page_size=20` | All |
| `GET` | `/api/v1/visits/{id}` | Visit details (incl. health declaration, badge records) | All |
| `POST` | `/api/v1/visits` | Create appointment (Host pre-registration) | Host, Manager |
| `PATCH` | `/api/v1/visits/{id}` | Update appointment (before check-in) | Host |
| `POST` | `/api/v1/visits/{id}/check-in` | Execute check-in -> record actual_arrival (usually auto-triggered by print-badge) | Host |
| `POST` | `/api/v1/visits/{id}/check-out` | Execute check-out -> record actual_departure (usually triggered by QR scan) | Host |
| `POST` | `/api/v1/visits/{id}/cancel` | Cancel appointment | Host |
| `GET` | `/api/v1/visits/active` | Currently on-site visitors (status=checked_in) | Host |
| `POST` | `/api/v1/visits/batch-checkout` | Batch check-out (end of day) | Host |

#### 6.5.3 Badge

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `POST` | `/api/v1/visits/{id}/print-badge` | Print badge -> auto-records badge_prints + auto-executes check-in (records actual_arrival, status->checked_in) | Host |
| `GET` | `/api/v1/visits/{id}/badge-history` | Badge print history | Host, Admin |
| `GET` | `/api/v1/badge/templates` | Available badge template list | Admin |
| `PUT` | `/api/v1/badge/templates/{id}` | Update badge template | Admin |

#### 6.5.4 Health

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `POST` | `/api/v1/visits/{id}/health-declaration` | Submit health declaration (incl. e-signature, Host fills in on behalf of visitor or visitor self-fills) | Host |
| `GET` | `/api/v1/visits/{id}/health-declaration` | View health declaration details | Host, Auditor |

#### 6.5.5 Audit & Dashboard

| Method | Path | Description | Role |
|--------|------|-------------|------|
| `GET` | `/api/v1/audit-logs` | Audit log query `?user_id=&action=&entity=&from=&to=` | Auditor, Admin |
| `GET` | `/api/v1/audit-logs/export` | Export audit logs (PDF/Excel) | Auditor |
| `GET` | `/api/v1/dashboard/overview` | Dashboard overview data | Manager, Auditor |
| `GET` | `/api/v1/reports/cfia-visit-log` | CFIA-format visitor log report | Auditor |
| `GET` | `/api/v1/reports/gmp-area-summary` | GMP area access summary report | Auditor |

#### 6.5.6 Cross-Service Calls

| Call Direction | Endpoint | Purpose |
|----------------|----------|---------|
| vms-api → epms-api | `GET /api/v1/users/directory?search=` *(new public endpoint, see §6.2)* | Host search by any authenticated user |
| vms-api → epms-api | `GET /api/v1/users/directory/{id}` *(new)* | Brief Host details (full_name / department_id) |
| vms-api → approval-api | `POST /approval/v1/approvals/vms_visit/{visit_id}/action` with `{"action": "submit"}` | Submit visit for approval |
| vms-api → approval-api | `POST /approval/v1/approvals/vms_visit/{visit_id}/action` with `{"action": "cancel"}` | Cancel an in-flight approval |
| vms-api ← approval-api | (no callback; approval-api updates `vms_visits.status` directly on the shared DB row) | Status sync |
| Portal → approval-api | `GET /approval/v1/workflows/vms_visit` | Admin reads/edits VMS workflow chain |
| Portal → approval-api | (existing task inbox query, now includes `doc_type="vms_visit"`) | Task Inbox aggregation |
| vms-api → file-api | `POST /files/v1/upload` (`entity_type="vms_visit"`) | Upload appointment attachment |

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

### 6.8 Frontend Route Design (Standalone VMS Application)

```typescript
// vms/src/App.tsx — React Router 7 route configuration
{
  path: "/",
  element: <AppLayout />,  // VMS standalone layout (sidebar nav: Visits / Dashboard / Admin)
  children: [
    { index: true, element: <VisitListPage /> },          // /
    { path: "new", element: <VisitCreatePage /> },         // /new
    { path: ":visitId", element: <VisitDetailPage /> },    // /:visitId
    { path: "check-in", element: <CheckInPage /> },        // /check-in
    { path: "dashboard", element: <DashboardPage /> },     // /dashboard
    { path: "badge/:visitId", element: <BadgePrintPage /> }, // /badge/:visitId
    { path: "admin/quality-managers", element: <QualityManagerRosterPage /> }, // Admin-only
    { path: "admin/notification-contacts", element: <NotificationContactsPage /> }, // Admin-only
  ]
}
```

The VMS frontend, as a standalone UniOps application (port 5176), uses the Portal (`:5174`) for unified authentication login and is sibling to EPMS (`:5173`) and OA (`:5175`). Auth flow is identical to OA: when no local token is present, redirect to Portal; after Portal login, the token is returned via the `#__session=` hash fragment. localStorage key: `vms-auth` (with fallback to `portal-auth`), mirroring the [oa/src/lib/api.ts:9-10](oa/src/lib/api.ts#L9-L10) pattern.

### 6.9 Portal Integration (Module Launcher + Task Inbox)

VMS is added to the Portal as a top-level Module — same level as EPMS and OA — and its approval tasks are surfaced in the Portal Task Inbox.

#### (1) Portal Sidebar — add VMS to the Modules section

```diff
// portal/src/pages/PortalHome.tsx — NAV_SECTIONS
  {
    title: 'MODULES',
    items: [
      { label: 'Procurement', icon: ShoppingCart, href: 'epms' },
      { label: 'OA',          icon: Wallet,       href: 'oa' },
+     { label: 'VMS',         icon: UserCheck,    href: 'vms' },
    ],
  },
```

```diff
// portal/src/pages/PortalHome.tsx — resolveHref
  const resolveHref = (key: string | null) => {
    if (key === null) return '#'
    if (key === 'epms') return epmsHref
    if (key === 'oa')   return oaHref
+   if (key === 'vms')  return vmsHref
    // …
  }
```

Mirror the `epmsHref` / `oaHref` construction to build `vmsHref` — a redirect to `http://localhost:5176/#__session=<jwt>` so the session handoff lands a logged-in user directly in VMS without re-authenticating.

#### (2) Portal Task Inbox — surface `vms_visit` tasks

Portal's existing task aggregation (which today pulls tasks for `doc_type ∈ {pr, po, pa, exp, mil, trv, cfm*}`) gains `vms_visit` as another doc_type. Each task row deep-links to VMS:

| Task field | Value for VMS visit |
|---|---|
| `doc_type` | `"vms_visit"` |
| Display label | `"Visitor approval: {visitor.first_name} {visitor.last_name} ({company_name}) → {access_area}"` |
| Deep-link URL | `${VITE_VMS_URL}/${visit_id}#__session=<jwt>` |
| Approval actions | `approve` / `return` / `cancel` — submitted to `POST /approval/v1/approvals/vms_visit/{visit_id}/action` (same as PR/PO) |

Implementation:

- The Portal task fetcher currently queries the approval-api `tasks` table filtered by `assignee_id == current_user`. Once approval-api accepts `vms_visit` in `_DOC_META`, tasks for visits land in the same table automatically — Portal only needs to register the `doc_type → label + deep-link` mapping.
- Number display: visits have no `number` field; the Portal task row uses `Visit.id` short prefix or the `display_name` rendered from the visitor name + date. The mapping is configured in Portal frontend (`DOC_PATH` / `STATUS_LABEL` constants in [PortalHome.tsx:100-112](portal/src/pages/PortalHome.tsx#L100-L112)).

#### (3) Portal Admin — Workflow Defs editor

The existing Portal Admin "Workflow Defs" page (which today edits `workflow_defs["pr"|"po"|"pa"]`) gains a fourth tab: **VMS Visit**. Editing it writes to `company_config.workflow_defs["vms_visit"]`. This works automatically once `vms_visit` is added to approval-api `workflows.py:_DOC_TYPES` (see §6.2.1(1)).

> **Quality Manager roster is *not* edited here** — it's a VMS-local concept and lives in the VMS Admin panel (`vms_config.quality_manager_user_ids`). Portal Admin only edits the workflow chain shape; VMS Admin controls who fills the `quality_manager` step.

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

| # | Item | Confirming Party | Status |
|:-:|------|:----------------:|:------:|
| 1 | Current paper visitor registration form template (to understand existing data fields) | Administration | Open |
| 2 | Specific GMP area health declaration question checklist | QC / Quality Assurance | Open |
| 3 | Existing office printer model and paper spec (A4 / Letter) | IT | Open |
| 4 | Whether integration with existing access card system is needed | IT | Open |
| 5 | Specific format requirements for audit reports (CFIA standard) | Compliance | Open |
| 6 | UniOps server resource assessment for vms-api (port 8008) deployment | IT | Open |
| 7 | Initial Quality Manager roster — list of UniOps users to seed into `vms_config.quality_manager_user_ids` | QA Leadership + IT | Open (direction confirmed: VMS-local roster in Admin panel) |
| 8 | Quality Manager assignment policy: single-approver (round-robin) vs all-approvers (any-can-approve) for parallel routing | QA Leadership | Open |
| 9 | Training Contact (HR) and PPE Contact (Janitor) email addresses | HR / Administration | Open (personnel confirmed; emails pending) |
| 10 | Final default workflow chain for `workflow_defs["vms_visit"]` at system seed time | IT / Management | Confirmed (single-step `dept_manager`; Admin may add `quality_manager` step) |
| 11 | When approval is triggered per `access_area` — confirm office=no, warehouse=yes, GMP=yes-with-QM | Compliance / IT | Confirmed (see §6.2.1(3) table) |
| 12 | epms-api public `GET /api/v1/users/directory` endpoint — fields exposed: `id`, `full_name`, `email`, `department_id` | IT (epms-api owner) | New endpoint — needs design review |
| 13 | Audit log immutability mechanism: DB-level `REVOKE UPDATE, DELETE ON vms_audit_logs FROM epms` migration vs service-layer only | Compliance / IT | Open (Compliance to confirm DB-level requirement) |

> **Integration plan reconciliation (V2.3)**: All items below are now ✅ resolved and require **no further confirmation**, because they were architecturally decided after reviewing the actual UniOps codebase:
>
> - **Port** — 8008 (8007 was already taken by budget-api)
> - **Schema** — `public` with `vms_*` prefix (no separate `vms` schema; consistent with all other UniOps services)
> - **Approval engine integration** — cfm-style extension of `_DOC_META` / `_WORKFLOW_DEFAULTS` / `_DOC_TYPES`, with a thin `Visit` mirror model in approval-api (same pattern that approval-api already uses for `BudgetPlan`, `PurchaseRequest`, etc.)
> - **Quality Manager** — VMS-local role; not added to UniOps `VALID_ROLES`; resolved per-visit via `vms_visits.quality_approver_id` set by vms-api at submit time
> - **User master** — shared `public.users`; any authenticated UniOps user can be a Host
> - **Portal integration** — VMS in MODULES section (sibling to EPMS/OA); Portal Task Inbox aggregates `doc_type="vms_visit"` tasks with deep-link to vms frontend

---

**Document Version**: V2.5  
**Creation Date**: April 2026 (First Draft) / May 2026 (V2.0 — UniOps Integration) / May 2026 (V2.1 — Role Model Refactoring) / May 2026 (V2.2 — Approval Workflow Customization + Notification Contacts) / May 2026 (V2.3 — Integration Reconciliation) / June 2026 (V2.4 — Multi-visitor visits + intake-flow tweaks) / June 2026 (V2.5 — PPE + compliance + notification workflow)  
**Document Status**: V2.5 captures compliance + notification workflow changes from operator UAT during S2-E rollout (no platform-architecture changes — same ports, role model as V2.3; additive schema columns only).  
**Next Review Date**: TBD  

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
