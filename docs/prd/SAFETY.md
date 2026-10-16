# Safety (EHS) — Product Requirements Document

**Version**: 0.5
**Date**: 2026-10-16
**Status**: In Development (branch, not deployed)
**Scope**: A workplace health and safety module for the Kingston plant (Ontario OHSA / WSIB), replacing SiteDocs. It covers incidents and statutory clocks, corrective actions, inspections and forms, training, JHSC, chemicals/WHMIS, permits, equipment, documents, and dashboards.
**Source**: Consolidated from the Safety Module PRD series in `/srv/uniops/`. The baseline is the full requirement list in v0.2 (`Safety-Module-PRD-EN-v0.2.docx`, 28 Aug 2026). Changes come from v0.3 and the HSE reply to it (`Safety-Module-PRD-EN-v0.3_Reply.docx`, 31 Aug), and from v0.4 and the HSE reply to it (`Safety-Module-PRD-EN-v0.4_Reply.docx`, 1 Sep). v0.3 and v0.4 were delta documents, so they do not restate the full list.

**What changed in this version (0.4 → 0.5)**
- The HSE answers in the v0.4 reply are now recorded as decisions (§1.3): monthly hours worked entered by HSE, the staged area picker with optional line selection, the 12 incident types kept as one list, GPS dropped from all forms, and multi-select at any level of the cause list.
- The requirement list is consolidated into one table, E01–E57, so it no longer has to be pieced together from three delta documents.
- Added §9 Implementation status (branch `feature/ehs-safety`).
- Questions left unanswered in v0.4 are now Open Items (§10).

---

## 1. Context and Decisions

### 1.1 Background
Canada Royal Milk (Kingston, Ontario; one plant, about 100–300 staff; provincially regulated under the Ontario OHSA, not the Canada Labour Code) runs its safety programme on **SiteDocs**. This module replaces it inside UniOps. Every employee gets a UniOps account. The front end is responsive web, not a native app or PWA. The goals, in order: meet OHSA first, then work towards COR certification. **SiteDocs history is not imported.**

The module is named **"Safety"**, not "Security": in English, "security" means guards, access control and infosec. Services: `ehs-api` (port 8012) and the `ehs` front end (port 5180), with API prefix `/api/v1`.

### 1.2 Regulatory coverage

| # | Legal obligation | Reference | System capability |
|---|---|---|---|
| 1 | Fatality or critical injury: notify MOL immediately by phone, then a written report within 48 h | OHSA s.51(1) | Countdown from the **time of occurrence**; escalating reminders; report content pre-filled; the submitted report kept as evidence |
| 2 | Report to WSIB on Form 7 within three business days | WSIB / WSIA | Countdown from **employer awareness** to **receipt by WSIB**, skipping Ontario statutory holidays; fields pre-filled; export for transcription; WSIB confirmation filed against the incident |
| 3 | Record every first-aid incident; stations, first aiders and certificates; keep records ≥ 5 years | Reg. 1101 | First-aid recording; first-aider roster with expiry; records cannot be altered or deleted after entry |
| 4 | JHSC membership (certified members), meetings, minutes, monthly inspections, written employer response to recommendations within 21 days | OHSA s.9 | Roster (23 members, 20 certified); meetings, minutes, attendance, print-ready posting PDF; recommendations tracked with a 21-day countdown; inspections rotate by department |
| 5 | Mandatory awareness and competency training | Reg. 297/13 and related | Eight confirmed courses; per-worker records, certificate scans, expiry warnings at 90/60/30 days |
| 6 | Annual review of the H&S policy and the violence and harassment policy | OHSA s.25(2)(j); s.32.0.1 | Version-controlled document library with annual review reminders and recorded sign-off |
| 7 | WHMIS 2015: chemical inventory, current SDS reviewed at least every 3 years, readily available | Reg. 860 | Inventory by storage location; SDS library on a 3-year review clock; QR code per SDS |
| 8 | Conduct inspections and correct identified hazards | Reg. 851 | Scheduled inspections; every failed item raises a corrective action |
| 9 | Records readily available for inspection | OHSA general duty | Retrieval by date, area, worker or type; read-only auditor access |

### 1.3 Decisions recorded (HSE answers, v0.1 → v0.4 reply)

| Topic | Decision |
|---|---|
| Phase 1 scope | Confirmed as proposed (v0.2). |
| Statutory clocks | MOL 48 h runs from **occurrence** (OHSA s.51(1)), in calendar hours. WSIB 3 business days runs from **employer awareness** to **receipt by WSIB**, skipping Ontario holidays. `occurred_at` and `employer_aware_at` are separate fields. The 48 h, 3-day and 21-day limits are fixed in code; only the holiday list can be maintained. |
| Classification | Two independent fields: **injury classification** (first aid / medical aid / lost time) drives WSIB and the injury rates; **MOL reportable** (critical injury / fatality) drives the 48 h clock. Both can apply, and the MOL flag can be set manually. Medical aid = clinic, hospital or other health-care facility (including chiropractor, physiotherapist, dentist). |
| Lost time (company measure) | Lost time is any period an employee is paid while not working. A worker sent home on the day who returns for their next shift **counts**. Modified duties **never** count: not at regular pay, not at reduced pay, not beyond 7 days. This is an internal management measure. It drives TRIR/LTIFR/DART and the WSIB *reminder*, but it does **not** decide whether a claim is reportable to WSIB; a person makes that call (see SF-01). |
| Twelve incident types (v0.4 reply) | The form shows HSE's 12 options as **one list**: First Aid, Medical Aid, Lost Time, Fatality, Unconscious, Near Miss, Property/Asset Damage, Report Only, Occupational Illness, Environmental, Security, Drug/Alcohol. Behind the scenes the system stores each choice in the separate fields (injury class, MOL flag, event kind, category). Choosing **Fatality or Unconscious** adds one follow-up question, the injury classification. **Confirmed acceptable.** |
| RIR | RIR and TRIR are the same metric. One figure: (recordable incidents × 200,000) ÷ total hours worked. |
| Total hours worked (v0.4 reply) | **The HSE officer enters total hours worked manually in Settings, one entry per month. Entries stay editable so later corrections can be made.** |
| Escalation (CAPA) | Owner 3 days before due → owner on the due date → owner + supervisor at 5 days overdue → owner + supervisor + HSE Manager at 10 days overdue. No escalation to senior management. |
| Mandatory training | All staff: WHMIS, Lockout/Tagout, Worker & Supervisor Safety Awareness, **Working at Heights**. Selected staff: Forklift, Mobile Equipment Work Platform, Confined Space, First Aid. |
| Course process | 1. Create the course title. 2. Attach the content. 3. Set date, time and location. 4. HSE assigns a supervisor as course administrator. 5. Supervisors add participants. 6. Participants get a reminder the day before. 7. HSE records the instructor. 8. HSE marks each participant passed or failed; a pass adds the certification to the user's profile. |
| JHSC | Meets monthly; 23 members, 20 certified. After each meeting, one department is inspected (department rotation). Recommendations go on assessment forms, then into the minutes, which are emailed to management and the JHSC and posted on plant bulletin boards (print-ready PDF). |
| Draft saving | Required on **all** forms in Phase 1, because the plant has Wi-Fi dead zones. |
| GPS (v0.4 reply) | **GPS coordinates are not required on any form, signatures included.** |
| Signatures | Name, role and timestamp fill in automatically. Role comes from the employee record and is stored as a snapshot with the signature. Timestamps record their time zone explicitly. |
| Toolbox talks | One device is passed around after the meeting, so many people sign on one device; there is no per-person login. |
| Investigation | Mandatory for every injury and every equipment-damage incident. HSE review is a gate: such incidents cannot be closed until HSE has reviewed them. |
| Medical form privacy | "Private" applies to the whole form. Medical incidents are access-restricted at record level, and Section B health information sits behind its own permission. Section B follows the SAMPLE order: signs, allergies, medicines, past history, last meal, events. |
| WSIB filing | WSIB submissions are prepared manually from the incident form and other sources. UniOps prepares the data and reminds; a person files. |
| Two-way email | Outbound email with full logging in Phase 1; inbound reply filing immediately after. |
| SiteDocs retirement | Staged. The three day-one incident forms (Medical, Equipment, Near Miss) move at the end of Phase 1, and both systems run in parallel while staff train. |
| Contractors | Configured the same way as VMS (VMS.md), reusing its health declaration, PPE issue and orientation validity. |
| Custom dashboards | Option A: managers arrange charts chosen from a library. HSE's existing management dashboard is to be reproduced. |
| AI GHS labels | HSE must approve before any label can be printed. Nothing is released automatically. |
| Reference lists | Maintained by HSE in Settings, not fixed in code. Entries are retired, never deleted, and records keep the wording used at the time. Immediate and root causes share one grouped list (7 standalone entries + 27 under 6 groups). Body part and nature of injury use WSIB wording. Any list not supplied gets manual entry plus spreadsheet upload. |
| Cause selection (v0.4 reply) | **Any entry at any level can be chosen on all forms**, not just the 7 top-level entries. **Multiple causes can be selected**, since an incident may have more than one root cause. |
| Plant area picker (v0.4 reply) | Whether an area can be selected is a **property of the area**, not a fixed rule. See §1.4. |
| Risk matrix | HSE RA ver 1.0: five likelihood bands (from once in 1,000 years to once a month), six severity bands judged across five consequence types (the type used is recorded), four ratings. The completed matrix is embedded in the form and kept with the record. |
| Hazard controls | Hazard controls is a separate two-level list, distinct from the hierarchy of controls. Both are needed. |

### 1.4 Plant area picker (v0.4 reply)
1. The first level is an **editable** list of general plant areas: *Outside the Plant · Production · 107 – Office · 105 – Warehouse · 106 – Utilities · Wastewater Treatment Plant (WWTP)*.
2. Any choice except **Production** ends the selection at that level.
3. **Production** opens a second level of operational areas, chosen by name: *102 – Pretreatment – Milk Receiving · 102 – Pretreatment – Raw Milk Pasteurizer · 102 – Pretreatment – Dosing and Tipping · 102 – Pretreatment – Pasteurizer · 102 – Evaporator · 103 – Dryer · 103 – Filling · 103 – Filling – Blending · 103 – Can Infeed · 103 – Can Infeed – Buffer Room · 104 – Packaging*.
4. Each operational area has **Line 1 / Line 2**. **Line selection is optional**: the reporter must never be forced to pick a line they do not know.
5. Areas, departments and lines stay editable, so more can be added later.

Reason given by HSE: employees do not know building numbers, and choosing by operational-area name reduces mis-filed locations. The underlying tree is still Building → Department → Operational Line → Line 1/2. Intermediate levels can be empty.

### 1.5 Boundaries (accepted, unchanged since v0.1)

| # | Boundary | What the system does instead |
|---|---|---|
| B1 | UniOps does not submit WSIB Form 7 | Runs the 3-day countdown, pre-fills every field, exports a clean package for transcription, and files the WSIB confirmation |
| B2 | No full offline mode | Draft saving on every form covers dead Wi-Fi zones |
| B3 | No production-equipment maintenance | Safety equipment register only (maintenance is a separate CMMS scope, see CMMS.md) |
| B4 | Not an occupational-health record system | Only what safety administration requires, under restricted access |
| B5 | No automatic filing with the Ministry | Drives the 48 h clock, prepares the content, escalates, and keeps what was sent and when |

---

## 2. Functional Requirements — 57 Items

Priority: **P0** legally required or the loop breaks without it · **P1** needed day to day · **P2** strengthens the system · **P3** needed for COR. **Phase** is as stated in v0.2–v0.4; "—" means the source did not assign one.

### 2.1 Foundation, master data, platform

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E01 | Worker safety profile | Employee number, position, hire date, employment type, shift, mobile, emergency contact, home area. | P0 | 1 |
| E02 | Site and area register | Building → Department → Operational Line → Line 1/2, maintained by HSE in Settings as an editable tree. QR code per location; scanning opens an incident report or that area's checklist. Area picker per §1.4. | P0 | 1 |
| E03 | Position requirement matrix | Required training, PPE and medical surveillance per position; drives the gap report. | P1 | — |
| E04 | Roles and permissions | Manager, HSE Manager, HSE Coordinator, Area Supervisor, JHSC Member, First Aider, Worker, read-only Auditor. | P0 | 1 |
| E05 | HSE trends and analytics | Current year vs. prior year or any chosen period. Rising incident types, problem departments and shifts, recurring hazards and root causes, repeat equipment failures and corrective actions, seasonality. Example: "hand injuries up 42%, 68% in Production Area 104, 54% on night shift". | P1 | 2 |
| E06 | Reporting engine | One common engine, not one-off reports. Any recorded field, by month/quarter/year; scheduled email distribution; PDF export. The common fields (occurred_at, location, department, shift, category, severity, owner, status, source, closed_at) are fixed in Phase 1 across Incident, Hazard, Observation, Inspection, Audit, CAPA, Training and Compliance. | P1 | 2 (fields: 1) |
| E07 | Communications and audit trail | Configurable email templates with record fields. Every message links to its record. Replies are received and filed against the record. All correspondence is traceable, and signed items show in the worker's record. | P1 | 1 outbound; inbound right after |
| E54 | Settings and reference lists | Flat and grouped list management, the plant tree, behaviour settings (escalation timing, certificate warning days, who is notified per incident category, email templates, anonymous reporting on/off, inspection rotation order, Ontario holidays), notification templates, spreadsheet upload. Statutory limits, Form 7 fields, the hierarchy of controls and GHS classes are deliberately **not** editable. | P0 | 1 |

### 2.2 Incident and injury management (statutory core)

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E08 | Incident reporting | Injury, near miss, property damage, environmental release, occupational illness, workplace violence or harassment. Any employee can report from a phone, anonymously if they choose. The three day-one forms (Medical, Equipment, Near Miss/Close Call/Good Catch) keep their section structure. Incident types per §1.3. | P0 | 1 |
| E09 | Classification and statutory notification | The two independent fields (injury class + MOL reportable) drive the MOL and WSIB clocks. Lost-time rule per §1.3. | P0 | 1 |
| E10 | Investigation and root cause | Mandatory for injury and equipment damage. Hierarchy of controls, witness statements, photos, team sign-off. Causes multi-selectable at any level. Corrective actions are linked to a specific cause, editable inline in the investigation and shown on the cause tree with status. HSE review gate before closure. | P0 | 1 |
| E11 | WSIB Form 7 support | Fields captured and pre-filled, export for transcription, confirmation filed (B1). | P0 | 1 |
| E12 | First-aid log | Reg. 1101 log of every treatment; first-aid box inspections; first-aider roster and certificate expiry. | P1 | 1 |
| E13 | Return to work | Functional abilities, modified-duty plan, scheduled check-ins through to full duties. | P2 | 1 |
| E14 | Incident communications | Every incident is emailed to a defined group, and all mail in and out is logged against the incident. | P1 | 1 (outbound) |

### 2.3 Inspections and forms

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E15 | Form builder | 13 question types: short/long text, date, time, single and multiple person pickers, department picker, area picker, single choice, Yes/No/NA, instruction text, photo, signature. Conditional display and mandatory marking on any question; question groups. Versioned, and a completed form keeps its version. Template audit trail; shared forms can be dispatched to several users. Linked forms can be raised from within a form (e.g. the MOL report). | P0 | 2 |
| E16 | Inspection scheduling | Monthly JHSC inspections by department rotation, pre-operational shift checks, periodic equipment checks. Due items go to the task inbox and are emailed. | P1 | 2 |
| E17 | Mobile completion | Phone completion with photos, handwritten signature and timestamp. **No GPS.** Save-as-draft on every form, held through dead zones. | P0 | 1 (drafts) / 2 |
| E18 | Automatic deficiency handling | Any failed item raises a corrective action with owner, description and due date. | P0 | 2 |

### 2.4 Corrective and preventive actions

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E19 | Single action register | Every open action whatever its origin (incident, inspection, JHSC, audit, observation, risk assessment), classified by hierarchy of controls. | P0 | 1 |
| E20 | Closure and effectiveness verification | Evidence of completion, then separate verification by a second person. Escalation per §1.3. | P0 | 1 |

### 2.5 Hazards and risk

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E21 | Hazard register | Site-wide hazards with existing controls and accountable owners. | P2 | 2 |
| E22 | Risk assessment | Likelihood × severity on HSE's matrix (§1.3), residual risk, controls by hierarchy. The completed matrix is kept with the record. | P2 | 2 |
| E23 | Job safety / hazard analysis | Task steps, hazards and controls; versioned; workers sign before the task. | P2 | 2 |
| E24 | Observations and near misses | Lightweight entry to keep reporting rates high (the leading indicator). | P1 | 2 |

### 2.6 Training and competency

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E25 | Training records | Course catalogue with the 8 statutory courses flagged; per-worker records with certificate scans and issue/expiry dates. The 8-step course process in §1.3 is the specification. | P0 | 1 |
| E26 | Training gap report | Live view of who is missing which required training, by position and area. | P1 | — |
| E27 | Certification expiry control | Warnings at 90/60/30 days; optional blocking so an expired certification prevents assignment. | P0 | 1 |
| E28 | New-hire safety orientation | Orientation checklist with worker sign-off, kept as evidence. | P2 | — |
| E29 | Toolbox talks | Topic library, delivery record, attendance signatures. Superseded in detail by E57. | P2 | 2 |
| E30 | External certificates | External training certificates can be uploaded to the worker record. | P2 | 3 |
| E31 | In-house certificates | In-house training generates a completion certificate, visible in the worker record. | P2 | 3 |
| E32 | Training material | Documents attached to a training subject. | P2 | 3 |
| E33 | Training quizzes | Quizzes on a subject. Students scan a QR code and answer on their phone; scores are visible to the instructor and HSE. | P2 | 3 |
| E57 | Toolbox talk monthly cycle | A talk is created automatically on the 1st of each month and placed in every supervisor's to-do list. The content is a one-page PDF; HSE writes the topic each month. After the team meeting the supervisor and every attendee sign electronically on one device, and the signatures go to the training record. Email reminders go every two weeks until sign-off. Completion rate shows on the management dashboard. | P1 | 2 |

### 2.7 Joint Health and Safety Committee

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E34 | Committee roster | Worker and employer representatives, certified flag, terms; sized for 23 members, 20 certified. | P1 | 2 |
| E35 | Meeting management | Scheduling, agenda, minutes, attendance, print-ready posting PDF. | P1 | 2 |
| E36 | Recommendation tracking | Recommendations as action items with the 21-day employer-response countdown; the written response is kept. | P1 | 2 |
| E37 | Monthly inspection rotation | One department per month after the meeting, feeding corrective actions. | P1 | 2 |

### 2.8 Chemicals and WHMIS

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E38 | Chemical inventory | Hazard classification, storage location, quantity on site, accountable department. | P2 | 3 |
| E39 | SDS library | Current SDS on a 3-year review clock; QR per SDS for reading at point of use. | P2 | 3 |
| E40 | Workplace labels | AI extracts GHS label data from the SDS and generates a workplace label with a QR code linking to the SDS. **HSE approval is required before printing.** Replaces the v0.1 chemical approval gate. | P3 | 3 |

### 2.9 Permit to work

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E41 | Permit types | Confined space, hot work, working at heights, lockout/tagout, excavation, electrical. | P3 | 3 |
| E42 | Issue, execute, close | Hazard confirmation, atmospheric readings, multi-party signatures, automatic expiry, formal closure. | P3 | 3 |

### 2.10 Contractor safety

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E43 | Contractor qualification | Certificate of insurance, WSIB clearance, safety history, with expiry tracking. | P3 | 3 |
| E44 | Orientation and site-access gate | Orientation with sign-off; access blocked while documents are missing or expired. | P3 | 3 |
| E45 | Configured as per VMS | Built by configuring the existing visitor management application (VMS.md). | P3 | 3 |

### 2.11 Safety equipment and emergency preparedness

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E46 | Safety equipment register | Extinguishers, AEDs, eyewash stations, safety showers, first-aid cabinets, gas detectors, ladders, lift trucks, fall-arrest equipment (harnesses, lanyards). | P2 | 2 |
| E47 | Periodic equipment inspection | Scheduled checks via the checklist engine, so a missed monthly check is visible. | P2 | 2 |
| E48 | Emergency preparedness | Emergency plans, drill records with evaluation, first-aider and fire-warden rosters. | P2 | — |
| E56 | Production equipment register | A separate register from E46: production equipment that appears on incident forms, mapped to its department/area (e.g. Line 1 DSI → Evaporator). HSE maintains it, with spreadsheet upload. Choosing equipment on a form fills in the department. | P1 | 2 |

### 2.12 Documents and policies

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E49 | Policy library | Version control, publication and retirement, annual review reminders. | P1 | 2 |
| E50 | Read-and-sign tracking | Distribute a policy, toolbox talk or procedure to an audience and see who has and has not acknowledged it. | P2 | 2 |

### 2.13 Analytics and compliance

| ID | Requirement | Description | Pri | Phase |
|---|---|---|---|---|
| E51 | Safety performance dashboards | Lagging: TRIR (= RIR), LTIFR, DART, severity rate, using monthly hours worked from Settings. Leading: near misses, inspection completion, CAPA closed on time, training compliance, TBT completion. HSE's management dashboard reproduced; managers arrange their own from a chart library (Option A). | P1 | 3 (custom) |
| E52 | Compliance calendar | Everything coming due: annual reviews, certificate expiries, drills, inspections, SDS reviews, tasks, training. | P2 | 1 |
| E53 | Audit evidence package | One-click export of a year's records; read-only auditor access for COR, ISO 45001 or an MOL inspector. | P3 | 3 |
| E55 | Staff picker fills department | Picking a person on any form fills in their department from the employee record. | P1 | 1 |

---

## 3. Form Structure (day-one forms)

- **Medical Incident** (marked Private). **A**, initial information: description, who was injured, others injured, name/company if not a CRM employee, department, all workers involved, date, time, plant area, witnesses, reported to, incident type. **B**, injury/LTI information (only if there is an injury or lost time): injury type, body part, vitals, timeline, SAMPLE history. **C**, investigation report: investigator, identified hazards, PPE that could have prevented it, what happened, root cause. Signatures last.
- **Equipment Incident**: Person Involved → Details → Investigation (lead/supporting investigators, witnesses, who was notified, type and additional impacts, immediate cause, comments) → Identification of Root Causes → Corrective Action Plan → Sign Off → Signatures.
- **Near Miss / Close Call / Good Catch**: General Information → Close Call/Near Miss → Good Catch → Proactive Notice (Yes/No/NA "did you speak to the operator", with a conditional "why not") → Signatures.
- Every record has a printable reference number (SiteDocs had FORM ID + PDF ID). Where possible, the form ID in emails and dashboards is a link to the record.

## 4. Delivery Phases

| Phase | Contents | Outcome |
|---|---|---|
| 1 | Worker profiles; plant structure and reference lists in Settings; roles; incident and injury management in full (both clocks, investigation with inline CAPA, first aid, return to work); the three day-one forms; CAPA with escalation; statutory training and expiry; compliance calendar; draft saving; common fields fixed; mobile reporting; E55; outbound email with logging | Withstands an MOL inspection; the three day-one forms leave SiteDocs (both systems run in parallel during training) |
| 1.5 | Inbound email reply filing (needs a dedicated mailbox from IT) | — |
| 2 | Form builder and inspections; reporting engine and trends; hazards, risk, JSA, observations; JHSC; policies and read-and-sign; equipment registers (E46, E56); unified QR entry; toolbox-talk cycle (E57) | SiteDocs can be switched off |
| 3 | Permits; chemicals, SDS, AI labels; contractors via VMS; quizzes and certificates; custom dashboards; audit pack and auditor access | Ready for COR |

## 5. Effort (as of v0.4)

57 requirements · 381–508 person-days (about 19–25 person-months) · Phase 1 111–141 days. The v0.4 increase came from E57 (10–14 d), E56 (6–9), the 8-step course process (5–7), the mandatory investigation and HSE gate (3–4), spreadsheet upload (3–4), E55 (2–3), auto-filled signature details (1–2) and the risk-matrix snapshot (2–3). Later UAT rounds added scope on top of this (§10).

## 6. Platform Integration

- **Identity / authz**: `ehs.*` permission keys and Safety roles live in identity-api; `system_admin` short-circuits. Navigation goes through the Portal shell. See PORTAL.md.
- **Task Inbox**: CAPA, statutory clocks, inspections, toolbox talks, training sessions and signature requests become shared tasks; the Portal opens Safety tasks through Safety's own `/go/task/<id>` route. See PORTAL.md.
- **mdm**: the plant-area tree is the mdm `locations` table; departments and users are mirrored.
- **file-api**: attachments, training material (including video and slides), SDS.
- **VMS**: contractor management reuses VMS mechanisms (VMS.md).
- **AI**: SDS label extraction uses the platform's shared Anthropic key.

## 7. Related Documents

PORTAL.md (shell, identity, task inbox) · VMS.md (contractors) · CMMS.md (production maintenance, LOTO overlap; outside Safety under B3) · INVENTORY.md / EPMS.md (not used by Safety).

## 8. Version History

| Version | Date | Summary |
|---|---|---|
| 0.1 | 2026-08-27 | First proposal, 45 requirements. |
| 0.2 | 2026-08-28 | After HSE review: 53 requirements; clocks settled; drafts in Phase 1; GPS removed; contractors via VMS. |
| 0.3 | 2026-08-28 | Two-field classification; staged SiteDocs retirement; 13 question types; Settings (E54). HSE reply added E55/E56 and the TBT cycle. |
| 0.4 | 2026-08-31 | All 11 questions answered; E55–E57; files analysed; three new questions. |
| 0.5 | 2026-10-16 | This document: consolidated, v0.4 reply applied, implementation status added. |

---

## 9. Implementation Status

**Branch `feature/ehs-safety`**, local HEAD `7b737273` (2026-10-15). `origin/main` was merged into the branch locally on 2026-10-08 (`6cb8c00b`, plus migration merge `8e033cec`). About 180 commits are ahead of `origin/main`. **None of this is on main or deployed.** The remote `origin/feature/ehs-safety` is stale: it stops at `6a0acc16` (2026-08-30, 21 commits, early Phase 1 only). Seven rounds of HSE UAT have been run on dev; round 7 (2026-10-15) recorded 2 pass / 10 fail. Code: `ehs-api` (about 34 routers, 65 migrations) and the `ehs` front end (about 40 pages). The branch also carries identity, mdm, epms and file-api changes that must ship with it.

| Status | Requirements |
|---|---|
| **Implemented on branch** | E01–E04, E08–E11, E13, E15–E16, E18–E23, E25–E26, E29–E42, E46–E50, E52, E54–E57. Also implemented from §1.3: staged area picker, monthly hours worked, 12-type mapping with the Fatality/Unconscious follow-up, multi-select causes, no GPS, signature role snapshot, HSE review gate, draft saving (E17), management dashboard plus per-user layout (E51), audit pack via the built-in auditor role (E53) |
| **Partial** | E05/E06: the engine has group, period-compare, drill-down and injury rates, but no scheduled email distribution or report PDF export was found. E07/E14: outbound email and logging are done; inbound filing is not built (the columns and Message-ID are prepared). E12: first aid is recorded on the incident form (the standalone register was folded in); no dedicated first-aid-box inspection was found, only as a safety-equipment check (E46/E47). E24: near misses go through the incident form; no separate lightweight observation entity was found. E27: an `is_blocking` flag is stored, but nothing enforces it at assignment. E28: "orientation" exists only as a form-template kind. E53: auditor access is a standing role, not time-limited grants. |
| **Not implemented** | E43–E45 contractor qualification and site-access gate via VMS (the only trace is a free-text contractor company on permits) |

---

## 10. Open Items

| ID | Item | Status |
|---|---|---|
| SF-01 | Confirm the WSIB wording: the countdown is a *prompt to assess and file*, and internal lost-time figures may differ from WSIB claim counts by design (v0.4 §2.1/§4.3). Not answered in the v0.4 reply. | Open |
| SF-02 | Confirm E55–E57 and the v0.4 changes to existing requirements (they came from HSE's own answers but were not explicitly confirmed). | Open (built on that assumption) |
| SF-03 | Employee records: six of seven SiteDocs signatures showed "No title". HSE will update job titles and departments but says it is manual and slow, and asks for an **employee record that is simpler, with bulk update of several users and fields at once**. That is a Portal/identity (or ADP-integration) requirement, not a Safety one. Needs an owner. Data must be clean before migration, or auto-filled roles and departments will be blank. | Open |
| SF-04 | WSIB internal target one day before the legal deadline (receipt vs. despatch). Asked in v0.2 §7.8; no explicit answer recorded. | Open |
| SF-05 | Working at Heights as mandatory for all staff: asked to confirm it is intentional (v0.2 §1.2); no answer recorded. | Open |
| SF-06 | Inbound email (Phase 1.5) needs a dedicated mailbox and credentials from IT; auto-reply filtering is required. | Open — not started |
| SF-07 | Contractor management via VMS (E43–E45) not designed or built. | Open |
| SF-08 | UAT round 7 answers (2026-10-15): passing the document quiz stays mandatory; **a new document version must require the quiz again** (currently an earlier pass carries over, so a change is needed). | Open — change pending |
| SF-09 | UAT round 7 free-text input added three blocks of new requirements: incident management (9 items), risk assessment (10), permits (2). Not yet analysed into E-numbers or started. | Open |
| SF-10 | Before go-live: HSE must configure area owners (Settings → Area responsibility) and load the reference lists; the label and certificate PDF fonts need CJK-capable embedding checked in the production image. | Open |
| SF-11 | Release: merge to main, single alembic head per service, and build ehs-api, identity, mdm, epms and file-api images together with their migrations (the branch carries non-ehs migrations). Push the branch: the remote copy is two months stale. | Open |
| SF-12 | Supervisor dropdowns list only same-department users, and some supervisors have no department in the user record. Data fix needed (related to SF-03). | Open |
