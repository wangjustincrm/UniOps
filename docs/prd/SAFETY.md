# Safety (EHS) — Product Requirements Document

**Version**: 0.19
**Date**: 2026-11-10
**Status**: In Development (branch, not deployed)
**Scope**: A workplace health and safety module for the Kingston plant (Ontario OHSA / WSIB), replacing SiteDocs. It covers incidents and statutory clocks, corrective actions, inspections and forms, training, JHSC, chemicals/WHMIS, permits, equipment, documents, and dashboards.
**Source**: Consolidated from the Safety Module PRD series in `/srv/uniops/`. The baseline is the full requirement list in v0.2 (`Safety-Module-PRD-EN-v0.2.docx`, 28 Aug 2026). Changes come from v0.3 and the HSE reply to it (`Safety-Module-PRD-EN-v0.3_Reply.docx`, 31 Aug), and from v0.4 and the HSE reply to it (`Safety-Module-PRD-EN-v0.4_Reply.docx`, 1 Sep). v0.3 and v0.4 were delta documents, so they do not restate the full list.

**What changed in this version (0.18 → 0.19)**
- §1.6 "Review of the third and fourth batches": permit photographs follow the permit; a record's action history follows who may see each action; saving unchanged keeps the wording of picks; links to a permit, a hazard's Edit and a draft JSA's details; incident type labels kept on the report; HSE's review tasks; JHSC verification once. The list standard applied to the equipment register, the hazard register and incident types.

**What changed in version 0.18 (0.17 → 0.18)**
- §1.6 "Round 12, fourth batch": hazards, job analyses and permits on HSE's answers Q11 and Q12 — a hazard's own page with every field, workers seeing a hazard once it is assessed, several kinds and controls, hazards scored on the incident's risk form, eliminated at assessment, newest first, the recorded date and time and "last revised on"; job analyses with a System, PPE from the list, steps on the create page and hazards and controls per step; permits with hazards and controls; photographs on all three. The chart by supervisor and hazard assessments by month now count hazards recorded, by their creator and create date. The review of the second batch, fixed.

**What changed in version 0.17 (0.16 → 0.17)**
- §1.6 "Round 12, third batch": incidents, investigations, corrective actions and JHSC on HSE's answers Q4–Q8 and Q10 and their JHSC workflow — the food-safety near miss, no injury question on a near miss, no signature on the report, investigations outstanding until signed off, product batch code, the 5 Whys, reassigning and verifying held to supervisors and HSE, HSE's review once a record's actions close, action history on the record, the eleven-step JHSC workflow, the report edited in the report's own words, and incident types HSE add.

**What changed in version 0.16 (0.15 → 0.16)**
- §1.6 "Round 12, second batch": the equipment register — every machine, a box per System, the System as a list HSE keep and merge, retire and delete, a template and an upload matched on the area's path; equipment pickers search as you type and offer the machines in the record's area; safety equipment can be retired and deleted.
- §2.11 E56 restated.

**What changed in version 0.15 (0.14 → 0.15)**
- §1.6 "Round 11, first batch": the faults HSE found in UAT 11, and the renames they asked for.
- `origin/main` merged into the branch again (Staff among it); §9.

**What changed in version 0.14 (0.13 → 0.14)**
- §1.6 "Round 10, fourth batch": the dashboard HSE laid out — five boxes coloured by HSE's thresholds, the injury bar, then the rates (moved from Reports), lost-time hours and cost; average days to resolve, corrective actions, hazard assessments and near misses by month; a chart by supervisor (HSE only); bar, circle or body-picture styles set by HSE. Settings → Dashboard colours; Lists → Body Part places entries on the picture.
- §1.6: the injury rates on Reports counted every year's incidents against this year's hours, and read the day boundaries in UTC; corrected. Figures read off Reports before 2026-10-29 were high.
- §1.6: Safety installs on a phone from the browser (a progressive web app), on https only; how to withdraw it.
- §1.6: two more questions for HSE (team leads in the chart by supervisor; how the resolution average treats incidents still open).
- §10: SF-15 narrowed to the lost-time and cost formula, and the phone check over https.

**What changed in version 0.13 (0.12 → 0.13)**
- §1.6 "Round 10, third batch": hazard, hazard control, PPE type and root cause are trees HSE extends in Settings → Lists ("Add inside"), up to four levels; the pickers on incidents, investigations, hazards, risk assessments, drills and forms walk them level by level; records keep the entry's full trail.
- §10: SF-15 narrowed (the lists are built); SF-16 added (an entry cannot be moved back to the top).

**What changed in version 0.12 (0.11 → 0.12)**
- §1.6 "Round 10, second batch": the equipment register on incidents, hazards, job analyses and permits, with reports by equipment; safety equipment out of service and back with its history, shown on My Actions; HSE news and its ticker; the Staff File; reassigning a leaver's open work. Also the review's further fixes (links into an already open tab, other people's draft forms, undelivered email, "today" as the plant's day).
- §1.6: one more question for HSE (whose staff file a supervisor may open).
- §10: SF-15 narrowed to what is still open from round 10.

**What changed in version 0.11 (0.10 → 0.11)**
- §1.6 "Review of round 10": the faults a code review of the branch found, fixed — a change of incident kind and back now restarts the legal clocks it had closed, and keeps the injury class; the investigation no longer saves an empty Section B; the medical section no longer names an anonymous reporter; HSE can reopen a closed course session to correct it; a month without the day now expires on its last day; employees can open the files on their own training records.
- §1.6: two questions for HSE from the review (how "everyone must take" courses are now held, and who may see the injury details on the people named on an incident).

**What changed in version 0.10 (0.9 → 0.10)**
- §1.6 "Round 10": the email-sending fault behind round 10's TC-04, the five other failures, the form register search and the company logo on printouts, built.
- §10: SF-15 lists the rest of round 10's requests.

**What changed in version 0.9 (0.8 → 0.9)**
- §1.6 "Round 9": UAT round 9 (2026-10-20, 7 pass / 4 fail) — the four failures and HSE's six answers, and the open box's clear requests, built: labels without H/P codes, kind changes and reopenings (with a mandatory reason) on the incident timeline, training provider, expiry as the course's period less a day, several files on a training record, the logo on certificates, a JHSC recommendation page answered with a corrective action, JHSC and training-expiry figures, emphasised form questions.
- §10: SF-15 updated: what still waits on HSE (the dashboard redesign and its formulas).

**What changed in version 0.8 (0.7 → 0.8)**
- §1.6 "Round 8": the requests from round 8's open box that were clear are built — training courses by name only with no "everyone must take it" box, a certificate and a three-years-less-a-day expiry when recording training taken elsewhere, certificate expiry notice rules, a location on JHSC recommendations and their email, "JHSC" as the menu name, back to the document after its quiz, and a read-only injury and medical summary on the incident.
- §10: SF-15 narrowed to the three requests that still need HSE's answer.

**What changed in version 0.7 (0.6 → 0.7)**
- UAT round 8 (2026-10-20, 11 pass / 4 fail) answers recorded in §1.6 under "Round 8": label type size (b), the risk assessment form, notification content, investigation status left as built.
- §10: SF-13 resolved; SF-09 risk assessment built; SF-15 added for round 8's new requests.

**What changed in version 0.6 (0.5 → 0.6)**
- Records what UAT rounds 6 and 7 settled and what was built from them (§1.6): training passes, quizzes on sessions and documents, anonymous reports, legal clocks and closing, reclassification, documents, permits, labels.
- §9 Implementation status brought up to the current branch; round 7's defects and the first step of its free-text requirements are built.
- §10: SF-08 done; SF-09 split into what was built and what goes to HSE in round 8; new SF-13 (label type size) and SF-14 (task email duplication after merge).

**What changed in version 0.5 (0.4 → 0.5)**
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

### 1.6 Decisions from UAT rounds 6 and 7 (built on the branch)

Training
- A pass on a session's quiz does not pass the session or issue a certificate. The quiz marks the attendee "Quiz passed"; the person running the session marks them Passed; certificates are issued only when they close the session. Closing stops and names anybody who passed the quiz but is unmarked, and can be overridden on purpose (they are then recorded as not attended). A cancelled session cannot be closed.
- When nobody has entered an end time, the first passing attempt becomes the session's end, shown as such; it does not close scanning-in. Scanning in closes 30 minutes after an end time somebody entered.
- HSE can hand a session to another person; the person running it cannot.
- Training material on courses and sessions accepts documents, slides, pictures, text, video and audio up to 50 MB; a session starts with a copy of its course's material.

Documents
- A document can carry a quiz; signing waits for a pass. A new version asks for the quiz again, and the attempt limit counts attempts on the current version only.
- Who has to sign is chosen when the document is created — everybody in the plant, or named people — and publishing refuses a document that has to be signed and names nobody. Documents that had an empty audience before this change mean everybody, as they always did.
- A document can be archived. A version taken out of force — archived or replaced — stops asking for signatures and stops being due for review.

Incidents and anonymity
- An anonymous report stays anonymous to everybody except HSE: not in the report, its history, its attachments, a risk assessment the reporter made on it, or the injured-person record when the reporter was the injured person. HSE alone sees who sent it.
- Supervisors may edit the account of a report. The time it happened can be changed only by HSE once a legal clock runs from it, and the clocks still owed are recounted from the new time.
- The investigator may change the kind of incident before it closes, within the same form. The injury class and clocks follow: a clock the new kind owes starts; one it no longer owes closes as no longer applicable, with the reason.
- An incident cannot be closed while a corrective action on it is open, while its return-to-work plan is active, or while its Ministry or WSIB clock is running; nor can HSE's review be signed off while such a clock runs. HSE can mark a clock that turned out not to apply as not required, with a reason.
- Statutory and return-to-work sections show on medical incidents (and on any other that has a clock or a plan). The injury questions on the report appear once somebody is named as hurt. A new investigation starts with its opener as the lead.
- The owner of a corrective action raised on an incident is emailed, and the message is listed in that incident's notifications. The register shows counts of open, under-investigation and closed incidents, root causes and corrective actions.

Committee, permits, chemicals
- Printed minutes are numbered "Page N of M" and say, for each recommendation, who it was sent to and when the reply is due.
- HSE maintains each permit type's "before work starts" checks in Settings. An issued permit keeps the wording it was issued against. A prepared permit's start and end can be corrected; only whoever raised it or an issuer may change it.
- The chemical label's code opens the data sheet in force when it is scanned. The code is 25% larger than before.

Round 8 (answers of 2026-10-19/20)
- Label type: the small 4 × 6 label's body is 10 pt and does not shrink; the standard and large labels are 8.5 pt times their scale and shrink to a readable floor when needed (HSE chose option (b)). Filing a data sheet revision shows the superseded label at once, with the way to refresh it.
- Risk assessment, as HSE confirmed it: Existing Hazard from the hazard list; Judged on, a list HSE keeps under Settings → Lists with Equipment added, each entry carrying what every level of "how bad" means (an entry with none is not offered); how likely and how bad before controls; the hierarchy of controls and the hazard controls, any number of each; the hazard remaining after controls; how likely and how bad after controls — the final assessment, acceptable or still too high. On an incident the assessments form a table: Completed by, Date & time, Hazard, Judged on, How likely, How bad, Risk level, Hazard controls, How likely, How bad, Final assessment. List entries are kept as named when picked.
- The incident register holds every incident, and each figure above it opens the incidents behind it. A refused HSE review says why, as a refused close does. Changing the kind of incident is an orange button; the change is emailed to the "Kind of incident changed" list under Who is told and filed with the incident.
- Left as built by HSE's answer: no "investigation completed" status; the new-incident email keeps its content (no health information in it). "A stopped clock" meant one that no longer applies — marked not required.

Round 8, open box (built)
- A training course is its name. It is added by name alone, and a second course of the same name is refused; the code other pages link by is made from the name and not shown. No course is required of everyone any longer: the "everyone must take it" box is gone, and who has to take a course is set on the position (Settings → Positions). The four courses seeded as "everyone" were cleared once.
- Recording training taken elsewhere: the certificate (a PDF or an image) can be chosen on the same form. The expiry date is filled in as three years less a day after the completion date, again whenever the completion date changes, and can still be changed; left empty, the course's own validity period applies. If the certificate fails to upload, the training stays recorded and only the file is retried. (Both changed in round 9, below: any number of files, and the course's own period less a day.)
- Certificate expiry notices (Settings → Training notifications, HSE only): a rule is an email address and a number of days (1–365). On each scan, one email per rule lists every certificate expiring within that many days that the rule has not listed before: training records with an expiry date and certifications on worker profiles. A certificate renewed to a new date is listed again when the new date comes near. Not listed: training replaced by a later record of the same course, and anything held by someone no longer active. A new rule's first email lists everything already inside its window.
- A JHSC recommendation can say where it happened, picked as on an incident. It is shown under the recommendation and leads its email ("Where: …"), in the preview as well.
- The menu and tab say "JHSC"; the page heading reads "JHSC — Joint Health and Safety Committee", and its roster tab is "Members".
- Passing a document's quiz returns to the document, closing the quiz tab.
- The incident page has a read-only "Injury and medical summary" on the right, under People: what happened, the kind of incident, where, each injured person's part of the body, kind of injury, treatment, whether sent off site and anything owed afterwards, who gave treatment, what else we should know, and the whole medical section. It is shown only to holders of the medical-information permission (HSE and the first-aider role).

Round 9 (results and answers of 2026-10-20/22; built)
- Printed chemical labels show the hazard and precautionary statements without their codes (H302, P210); a statement with no wording keeps its code. The record and the label editor keep the codes.
- The incident timeline lists each change of the kind of incident (from what to what) and each reopening, with who and when. Reopening requires a reason, which is on the timeline and in the reopened email. Reopenings from before this show "No reason recorded".
- Training taken elsewhere records its provider; it is shown on the record and is a column of the printed register and the spreadsheet.
- A training record's expiry is the course's validity period, in calendar months, less a day — for records entered by hand and for records written by a completed session. Where the month has no such day (31 January plus one month; 29 February plus a year), the expiry is that month's last day, as round 8 put it: "29 February plus three years falls on 1 March; less a day is 28 February". The form fills it in when the course or the completion date changes and leaves a typed date alone otherwise; a course that does not expire leaves it empty. The rule applies to records written from now on; expiry dates already recorded are not recalculated (the test server holds records from the earlier thirty-day rule; there are none in production).
- A training record holds any number of files (any document, picture, video or sound file, or a link; pages and programs are refused), chosen on the form or added later. If some fail to upload, only those are retried.
- The in-house training certificate carries the company logo from Admin → Company Settings, centred at the top.
- A JHSC recommendation has its own page, which the reply task and the emails open. Whoever it was sent to may open and answer it without the committee's permissions; the answer is the corrective action form (what will be done, detail, owner, due date, priority, type of control). Submitting it raises the action, records the reply (stopping the 21 days) and removes the reply task from everybody's My Actions. HSE can correct the reply afterwards. Settings → Who is told → JHSC names who is emailed when one is answered, and whoever sent it (on by default); the email is listed on the recommendation.
- The recommendations list opens with four figures — total, closed on time, closed late, open — each narrowing the list. On time means the reply was recorded by the end of the 21 days.
- The compliance screen opens with four training expiry figures — expired, within 30 days, within 6 months, within a year (each including the shorter ones). Each opens the records behind it; the print and export take the same filter. Counted like the expiry emails: each person's latest record of a course, held by somebody still active.
- A form question can be marked "Emphasise (bold, larger)" in the form builder: its label is bold and two sizes larger when the form is filled in, and its label and answer when the JHSC minutes are read back and printed. HSE marks the three inspection questions on the JHSC Minutes Form.
- Left as HSE answered: the injury and medical summary stays visible to HSE and first aiders; HSE's "close everything outstanding" was for clearing test records on the test server, not a feature.

Round 10 (results of 2026-10-27; first batch built)
- Email is sent only after the record it is about has been saved. It used to be handed to the sender before then, and the sender, reading from a connection of its own, found nothing: every email the scheduled check produced (expiry notices, toolbox talk reminders) was filed and never sent, and some made by people's actions went the same way. Emails filed before this are not resent.
- A closed course session is read only for everybody, HSE included. To correct a result, HSE reopens it (below).
- The printed minutes list who attended under Present and who did not under Absent, three names to a row, without roles; regrets are under Absent, marked so.
- Every HSE review and every close is a row on the incident timeline, so a review and a close after a reopening appear as well as the first ones.
- The incident register and the JHSC recommendations tab count corrective actions raised, completed and outstanding; each figure opens what is behind it. A figure counts actions while the incident list shows incidents, and says both.
- A JHSC recommendation has a state from a list HSE keeps (Settings → Lists → JHSC recommendation status; New request, Under review by maintenance, Work in progress, Completed). A new one starts at the first; HSE and whoever it was sent to change it.
- The form register searches by form name, number, area and who filed it, on the server, beyond the latest two hundred.
- The company logo is on the printed training register, the JHSC minutes, and incidents and permits printed from the browser (each has a Print button; a permit prints on its own). Printing no longer drops page headings, and buttons, tabs and filters stay off the paper.
- HSE's answers on the dashboard (thresholds and chart styles set by HSE in Settings; supervisors from the reporting lines in Admin; the body diagram; the cost formula, still to be confirmed) and the other round 10 requests are in SF-15.

Review of round 10 (code review of the branch, 2026-10-27; fixed)
- Changing the kind of incident and changing it back restarts what the first change closed: a legal clock it closed as "no longer applies" is reopened, from when it first started, and again holds the review and the close. A clock HSE closed by hand as not required stays closed.
- Changing the kind of incident keeps how badly the person was hurt, unless the new kind is itself the severity (First Aid, Medical Aid, Lost Time, Report Only). The dialog shows it and it can be changed there; a change to it alone is on the timeline and notified like a change of kind. Report Only records no injury.
- The investigation form appears only once everything it is filled from has loaded, Section B included; if Section B fails to load the page stops, as for the investigation itself. Someone without the medical permission sees the form without Section B.
- On an anonymous report, who recorded the medical section is withheld from everyone but HSE, as for the reporter's photos and risk assessments.
- HSE can reopen a closed course session, saying why; the reason, who and when are shown on it. When it closes again, the training record (and so the certificate) it issued to anyone no longer marked passed is withdrawn; one that has files attached is refused until they are moved.
- An employee can list and open the files on their own training records from My Training — the certificate HSE filed for training taken elsewhere is one of them.
- The JHSC recommendation figures show a dash while loading and a note if the list fails to load, rather than zeros.
- Questions for HSE: (1) Since round 8 removed "everyone must take it", the four courses every worker needs (WHMIS, LOTO, Worker/Supervisor Awareness, Working at Heights) count as owed only where each position lists them; a position that misses one shows nobody in it as overdue, and nothing warns of it. Should the compliance screen list positions missing any of the four? (2) The injury details on the people named on an incident (body part, nature of injury) are visible to everyone who can open the incident; only Section B is limited to HSE and first aiders. Is that the intended line?

Round 10, second batch (built)
- The equipment register (Settings → Equipment) is picked on every incident report, the incident page, the hazard register, job analyses and work permits; optional, and a piece since retired stays on the records that name it. The free-text "equipment involved" stays for what the register does not list. Reports group incidents and first aid by equipment, and count hazards, job analyses and permits too, so "the equipment that causes the most injuries" is incidents grouped by equipment narrowed to an injury class; each bar opens the records. Drafts are not counted.
- Safety equipment that fails its check goes out of service and raises a high-priority action to repair or replace it, due in a week (a checklist's failures raise its own actions, now tied to the item). The item comes back into service when every action on it is verified; putting it back by hand needs a reason. Each change — out, back, when, by whom, why, which action — is in the item's history. My Actions shows everybody what is out of service (name, area, since when) on the same line as This month and Next month. A failed checklist check had been stored in a way the out-of-service rule did not recognise, so it never took its item out; fixed, and the stored checks corrected.
- HSE news: HSE writes items on My Actions (message, and importance as a red, orange or yellow pill) and takes them down; everybody sees them in a list at the top of My Actions, newest first and one under another (round 11; a ticker until then) — date, time, importance, message — which pauses when pointed at and stands still for anyone who asks for less motion.
- Staff File (under Records and compliance): everything in Safety with one person's name on it, grouped with counts, then listed — employee, date, register, number (a link), title — and exported. HSE opens anybody's, a supervisor their direct reports', everybody their own. A group the viewer may not open is not shown at all. Anonymous reports are never counted for anyone; being the injured person is its own group, shown only with the medical permission; the medical section is never included. On their own file a person also sees what they can already open as theirs.
- Reallocate Items (Settings, HSE; named Reassign a leaver until round 11): somebody who has left also has their own tasks closed and their certificate clocks stopped; somebody still active keeps both, and only the work moves. What is still waiting on somebody — corrective actions, investigations, sessions they run, JHSC recommendations awaiting their reply, toolbox talks to deliver, hazards and documents they own, safety equipment they check, scheduled forms, areas — is handed, kind by kind, to someone who may take it on, or left where it is. All or nothing; every problem is listed. Only whose it is changes, with its tasks; what anyone signed, reported or completed is untouched. A recommendation keeps who it was sent to and gains the new person, who is emailed its words and the time left. The leaver's own tasks close with a note that they left (nothing reads as done by them); their open permits are listed to close or reissue. Each handover is recorded with its reason.
- Also from the review: links from the dashboard, My Actions, the calendar, reports and an action's source to a page told what to show by its address now move that page when it is already open in a tab; the form register no longer lists, opens or lets anyone fill in somebody else's draft (shared drafts — an equipment check, JHSC minutes, a scheduled round — stay shared); Settings → Undelivered email lists email Safety could not send, to send again; an expiry notice rule's days can be changed; "today" is the plant's day throughout, not the server's.
- Questions for HSE (added): may a supervisor open the staff file of anybody, or only of their own reports (built: own reports)?

Round 10, third batch (built)
- Lists as trees. HSE asked for hazard, hazard control, PPE type and root cause to work like the plant areas: a master entry with entries inside it, their example three deep (Quality › Sanitization process › Not followed). In Settings → Lists each entry of those four lists has "Add inside"; the list shows as a tree, a group showing how many it holds. Up to four levels. A code is optional — it is made from the wording and does not change when the wording does. This answers "how do I add a new physical hazard": "Add inside" on Physical.
- Only an entry with nothing inside it can be chosen; a group is a heading. Adding inside an entry that records already name says how many, and that they keep it — it simply cannot be chosen again. An entry's full trail must fit 200 characters, the width a record keeps it in.
- Retiring a group retires everything inside it. Restoring brings back that entry only (what is inside may have been retired on its own before), and not while the group above it is retired.
- The pickers walk the tree one level at a time: one dropdown per level on a single choice; on several choices (hazards and PPE on an investigation, controls on a risk assessment), step into a group and back. Search cuts across levels and shows the trail. Looking inside a group does not change what is saved. A saved entry that has since been retired, or has become a heading, is shown and marked, not dropped from the field.
- What a record keeps is the entry's full trail ("Quality › Sanitization process › Not followed"), taken by the server from the list — not the words a page sent. It is taken when the entry is chosen: a record that keeps the same entry keeps the wording it was recorded with, through edits and renames. Records made before this keep theirs ("Not followed"); reports group by the entry, so old and new records count together.


Round 10, fourth batch (built)
- The dashboard in the order HSE gave in round 9. Five boxes across the top: days since the last medical incident, medical incidents this year, first aid, medical aid, lost time. A medical incident is any injury class; drafts are not counted, and an incident re-classed as no injury stops counting. Days since is counted in the plant's days; with no injury ever recorded it says "No injuries recorded", not 0. Each count opens the incidents it counted.
- Under them, injuries this year as one bar (first aid, medical aid, lost time), then the recordable incident rate, the lost-time rate and the DART rate — moved from Reports, which now points to the dashboard — lost-time hours, and the incident cost. The cost is only what has been entered and says so: "N of M incidents have a cost". The formula that will fill it is still to be confirmed (SF-15).
- Colours, set once by HSE for everybody (Settings → Dashboard colours): for each of the five figures, the recordable rate and this month's average days to resolve, a green number and a red number; yellow between. Which way is better belongs to the figure (more days since is better; fewer of everything else), so a pair that contradicts it is refused with the reason. Edges are strict, as HSE wrote it: above 15 is green, below 5 is red, 15 and 5 are yellow. Days since starts at 15 and 5; nothing else is coloured until HSE sets it. A box says what its colour means in words as well.
- Chart styles, set by HSE for everybody: "where injuries are happening" and "injured by department" as bars or a circle; "part of the body affected" as a body picture, bars or a circle. The body picture is a front and a back outline, each part coloured by how many incidents injured it (yellow to red since round 11), from the Body Part list: the entries the module was seeded with are placed by their code, and HSE places any entry, or keeps it off the picture, in Settings → Lists → Body Part. What is not on the picture — an unplaced entry, or a part typed in on an old report — is named under it, never guessed into a region. The list stays beside the picture and is what opens the incidents. Counts are incidents: one incident with two people hurt on the same part counts once.
- Average days to resolve, month by month: from the report being sent to the incident being closed (the last close, if it was reopened), for the incidents that happened in that month. ★ The average is over the incidents that are closed; ones still open are shown beside it, and open the register filtered the same way. HSE's formula divides the days by every incident created in the month; counting the open ones as zero days would make a month with a backlog look best. The month is the one the incident happened in, so opening a month shows exactly those incidents.
- Corrective actions raised and closed in each month (closed means closed, not marked done); hazard assessments each month — since round 12 the hazards recorded on the register, by the date they were saved; near misses with this year's total against last year's.
- The chart by supervisor, for this month or last: near misses (counted for the reporter's supervisor, by the date sent), hazard assessments (hazards recorded on the register, for the supervisor of whoever recorded them, by the date saved — round 12; until then risk scorings by the assessor's supervisor) and toolbox talks (for the supervisor who held them), stacked. Since round 12 a supervisor's own near misses and hazards count in their own column. Supervisor means the person's supervisor in Portal → Admin → Users **as it is now**: someone who changes team takes their earlier months with them. An anonymous near miss is never counted in the reporter's supervisor's column — a column of one beside it would say who wrote it — and goes with records whose person has no supervisor set, under "No supervisor set". Only HSE sees this chart; whether supervisors may see it is the same open question as the staff file.
- The "Safety participation" panel (counts per named person for anyone who could read incidents) is retired in favour of the chart by supervisor, so nobody but HSE now sees activity grouped by person or supervisor.
- A panel added to the dashboard now reaches people who had already arranged theirs; one they took off stays off. A dashboard that fails to load says so and offers to try again.
- ★ Injury rates on Reports were wrong until 2026-10-29: with no dates given they counted every incident ever recorded against this year's hours, so each rate read higher by however many earlier years there were; and the period's edges were read in UTC, four or five hours off the plant's day. The dashboard and the API now count both over the same window, on the plant's day; given only an end date, the window starts on 1 January of that year. A rate HSE took from Reports before then should be checked against the dashboard. The hours are by month: the current month counts whatever figure has been entered for it.
- Safety on a phone (round 10, option A): opened on https, Safety can be added to the home screen and opens full screen with its own icon. Nothing is stored for offline: every page and every update comes from the server as in a browser tab, and when the server cannot be reached a plain page says so and offers to try again. The test server is plain http, so there it stays a website. Not yet checked on a real phone: installing, and signing in through Portal from the installed app — to be checked on production. Sign-in is the step to watch: Portal is another host, so an installed app on an iPhone opens it in an in-app browser, and the return to Safety may land in Safari rather than back in the app. If it does, the fallback is to drop the worker and the full-screen setting and keep only the home-screen icon, which opens Safety in the browser. To withdraw it, replace `sw.js` with the version written in its header and remove the line that registers it; installed phones drop it on their next open.
- Deployment note found on the way: the merge from main had left the two Safety hosts in the Caddyfile importing a certificate snippet main had removed, which would have stopped Caddy loading its configuration. They now use the same per-host certificates as everything else. Caddy issues those certificates itself, which needs `safety.canadaroyalmilk.com` and `safety-api.canadaroyalmilk.com` to resolve to the production server before the release.
- Questions for HSE (added): (1) The chart by supervisor counts a record for the person's direct supervisor, so a worker who reports to a team lead counts in the team lead's column. Should team leads' columns be folded into the supervisor above them, and if so, how is a supervisor told apart from a team lead (both are simply people others report to)? (2) Average days to resolve averages the closed incidents and shows the still-open ones beside it, rather than dividing by every incident created that month. Is that acceptable?
---


Round 11, first batch (built)

UAT 11 closed at 10 passed and 9 failed (ver 11.3). HSE's 91 further requests are designed separately; this batch needed no answers.

- A closed incident no longer offers Close incident (TC-09).
- Editing an investigation or a hazard: a further corrective action on a root cause is raised. Only one with the same title as an action still open on that cause is skipped, because the screen saves the whole set. Saved actions are listed under their cause. Save waits while a started action is unfinished or its cause is not chosen; Not now releases it.
- Editing equipment changes only the fields the form sends. It used to blank manufacturer, model, serial number, description and department, and put a retired machine back in service.
- Every JHSC recommendation has a state (migrations 0085, 0087). A new one starts at New request wherever HSE has moved it on the list. A reply moves it: accepted with its corrective action closed is Completed; declined or deferred stays at New request; anything else is Work in progress. Closing the action moves it to Completed. A state a person chose is not overwritten. Correcting a reply afterwards does not change the state; HSE sets it by hand if it should. The state also shows on the meeting page. This rule is awaiting HSE's confirmation.
- The body picture runs from yellow (fewest) to red (most), in proportion; a part nobody hurt stays neutral.
- Staff File: Near misses have their own box, apart from Incidents.
- Section B can be minimised: closed by default on the report, open on the investigation. Closed, it keeps what was typed.
- Training print: Entered by is gone, and Length has room for "1 h 30 min". Excel keeps the column.
- The session form finds a course by typing, across the whole list. "Required by law" is gone from the screens; `is_statutory` stays.
- Compliance says its courses come from the Positions register. HSE decided compliance stays driven by Positions, so there is no separate list of required courses.
- Closing a form returns to the list of forms, not to "Which area?".
- Renamed: Expiry notices → Training notifications, Reassign a leaver → Reallocate Items, Undelivered email → Communications. Paths and tables are unchanged. The Communications page still lists undelivered mail only.
- The Portal rule that a supervisor is in the same department stays (PORTAL.md USR-002). TC-10 failed because the test accounts had no department.

Round 12, second batch (built)

UAT 12 closed at 13 passed and 1 failed. HSE answered every question; this batch is the equipment register (EQ-01 to EQ-06, INC-10, INC-11, INC-13) on answers Q1 and Q3.

- The register shows every machine. It returned the first 200 of 7,170 by name and said nothing; it now pages through all of them and says how many there are. Search covers name, asset number, System and area.
- "Kind" is now System, an entry of the Equipment System list (Settings → Lists), seeded from the 148 values the machines had (migration 0088). A machine shows its System's current name, so renaming or merging a System changes every machine at once. This is a deliberate exception to "a record keeps the label it was given": a machine is reference data, not a signed record.
- A box per System across the top counts the machines in service; the largest show first, and all of them one press away. A box shows only that System. With a System chosen, HSE can merge it into another: every machine moves, and the emptied System is retired from the list.
- Retire (Q3) takes a machine out of every picker; the records that already name it still show it, marked retired. When and by whom are kept, and Retired is a box of its own. Put back in service undoes it. Delete is offered and refused unless no incident, hazard, job analysis or permit names the machine.
- The same for safety equipment: Retire takes the item off its check cycle and closes the checks it was still owed, with a reason in its history; Show retired brings it back into the list. Delete only before anything has been recorded against it (a check, a change of status, a corrective action, a form).
- The edit form has every field: name, asset number, System, area, the department it belongs to, manufacturer, model, serial number, description. The department is its own choice; the plant areas do not carry one.
- A CSV template to download (EQ-04). The upload reads description, System, manufacturer, model and serial too; a System not on the list is added and named in the result. The area is matched on its full path ("Production/103 – Dryer/Line 1"), because several areas share a name (four are called Line 1); a bare name still matches when only one area has it, and a name several share is reported, not guessed.
- Every equipment picker (incident report, incident page, hazards, job analyses, work permits) finds a machine by typing, across the whole register (INC-13). It offered the first 500 by name, so the WWTP's and the warehouse's could not be chosen.
- Given the area the record is filed against, the picker offers that area's machines and everything beneath it, plus the 15 with no area (INC-10, INC-11). An area with none of its own — 46 of the 68 — offers the nearest area above it that has some, and says which. Show all areas is the way out when a machine was moved and the register not told.
- A record names its machine by looking it up, retired or not; a list of records looks its machines up together. Previously a machine outside the first 500 read "Equipment not in the register".
- Not done: whether the 208 machines whose System is "Archive" should start retired was asked and not answered; they are in service until HSE retires them or merges the System.

Round 12, third batch (built)

On HSE's answers Q4–Q8 and Q10, and their JHSC workflow.

- Near Miss — Food Safety is a type on the near-miss form, in the Food Safety category, offered only to coordinators, supervisors and HSE (the holders of ehs.incident.investigate). It counts with the near misses wherever they are counted, and under Food Safety on Reports (migration 0089 adds the category where it was missing). The near-miss form opens on Near miss (INC-07).
- A near miss does not ask how badly anybody was hurt and stores no injury (Q4: "If a worker is injured it is no longer a near miss"). This reverses round 4 for near misses only. A near-miss investigation does not ask what PPE could have prevented it; an older answer is kept.
- Changing an incident's type removes the category the old type set; a category chosen by hand stays (REP-01).
- The report has no signature (Q6). The investigation is complete when it is signed off, and outstanding until then: the register's box "Investigations not signed off" counts open incidents whose investigation is owed (every injury and equipment incident) or begun and not signed, and opens exactly those (TC-05). The status Under investigation is written by nothing, so its box always read 0.
- Product batch code: one field on the incident (migration 0090), beside the date and time on the report and under who investigated on the investigation (INC-04, D3).
- The 5 Whys on the investigation (Q8, INC-15/16, migration 0091): up to five answers. An incident in Food Safety, or a food-safety near miss, is signed off only with all five; elsewhere they are optional.
- Only supervisors and HSE reassign a corrective action (Q7): the open task moves with it and the change — from whom, to whom, why — goes into its history. Verifying is held to ehs.action.verify alone; the owner verifying their own (round 2) is gone, and JHSC members no longer hold the key (identity 0027). No named verifier.
- When the last open corrective action raised from a record is verified, HSE get a task in My Actions to review the record (Q5): incident, form, hazard, drill, permit, safety equipment or JHSC recommendation. It opens the record; Reviewed ends it, and so does closing an incident.
- A record shows the history of each corrective action raised from it — raised, progress, reassigned, done, verified — on incidents, forms and JHSC recommendations (JHSC-01). A JHSC recommendation also shows what the action says to do (TC-05), and opens from its text in bold, with a green Open button, on both lists (JHSC-02).
- Failing a safety equipment check opens the corrective action form the incidents use, filled in from the item and the notes; nothing is raised while the item has one open (TC-19, D16).
- JHSC recommendations follow HSE's workflow (migration 0092): Being reviewed when recorded; Under Review the first time somebody it was sent to opens it; Work in progress once the reply with what will be done is in; Awaiting HSE verification when its corrective action is verified; Completed when HSE verify it on the recommendation. Each step moves it only from the states before it, so a state set by hand further on stays; a reply that declines or defers leaves it where it is. The recommendation lists its history. This replaces round 11's rule.
- Editing a report asks what the report asked, in the same words and order; the three fields the report no longer asks show only on records that have them; photos can be added (INC-17).
- Settings → Incident types (GEN-01, Q10, migration 0093): the thirteen built-in types are listed and fixed; HSE add their own with a name, the form that offers it, a category and who may pick it, and retire them. An added type sets no injury class and never tells the Ministry. Retired types still read on the incidents that name them.
- Waiting on HSE: TC-08, whether supervisors may see Section B on the report.

Round 12, fourth batch (built)

On HSE's answers Q11 ("one overall score works … complete once it has at least one assessment") and Q12 ("one per step"), and the defaults D5–D8.

- A hazard has its own page (/hazards/:id; HAZ-04/09/10, D7), read-only, with every field: who recorded it and when, last revised, owner, kinds, area and department, equipment, description, what was found, controls, kind of control, causes, the history of each corrective action, every risk assessment, photographs and, for those who may read the audit trail (ehs.incident.read), the change history. Every link to a hazard (tasks, actions, reports, the staff file, reallocation) opens it.
- Someone who cannot write hazards sees a hazard once it has at least one assessment (Q11); before that it is not listed and its page says it is shown once assessed.
- The person recording a hazard is shown and cannot be changed; Owner is filled in with them and stays editable (HAZ-01, D5).
- A hazard can be several kinds and have several controls, picked from the Hazard and Hazard Control lists (HAZ-11, migration 0094); the single kind each had is carried over. Controls in words and the kind of control stay.
- A hazard is scored on the incident's risk form (HAZ-02/06): judged on (in bold, HAZ-08), how likely and how bad before and after, the hierarchy and hazard controls, and what remains. One score for the hazard however many kinds it is. "The hazard has been eliminated" can be ticked there (HAZ-12); until now nothing set Eliminated. Assessments made on the old form keep what they recorded as the controls in place, shown as such and not as what remains.
- The register lists newest first, or highest risk first on request (HAZ-07; round 3 had risk only) — since the review below, by sorting the Risk column (worst first on the first click), the register being paged on the server. Each row shows the date and time it was recorded under its number, department and owner, and when it was last revised. A revision is an edit that changes something, not an assessment. The create date is the one Reports, the staff file and the dashboard count by.
- A job analysis records its equipment System, which narrows its equipment picker and is shown by the System's current name (JSA-01/02, D8); on incidents, hazards and permits the equipment picker offers a System that only narrows the list. PPE is picked from the PPE list (JSA-03); PPE typed before stays, read-only. Steps are written on the create page and saved with it (JSA-04). Each step picks its hazards and controls from the lists, beside its own words (JSA-05, Q12); approval refuses a step with a hazard and no control however either was given (migration 0095). A new version copies all of it.
- A permit names its hazards and controls from the lists (PMT-01, migration 0096); they are on the permit and its printout, and can be changed while it is a draft or suspended. A link to a permit opens it.
- Photographs on hazards, job analyses and permits (HAZ-03, JSA-06, D6), on the create form and on the record. Seen by whoever may see the record; added by whoever may write it (a permit: whoever raised it or issues permits); removed by whoever added it or may write the record. A permit's photographs are seen as the permit is, added until it is closed out and removed only while it is a draft or suspended.
- The chart by supervisor counts hazards recorded, as above. Kirk's November count read 0 because it counted risk scorings in the column of the scorer's supervisor. Where supervisors come from is unchanged until the organisation chart in MDM is ready.
- Review of the second batch (uniops-95): a merged System is remembered, so loading an old sheet follows the merge and does not put machines back into the retired System, and a retired System that was not merged is reported; the moves of a merge are in each machine's change history; an equipment name that fails to load says so instead of "not in the register"; retiring safety equipment closes its owed checks without stamping the person who retired it, and says why.

Review of the third and fourth batches (uniops-95, built)

- A permit's photographs follow the permit (H1): seen by whoever raised it or holds the register, added by whoever may edit it until it is closed out, removed only while a draft or suspended. They asked only for the key everyone holds.
- A record's corrective-action history (M1) shows someone who cannot browse the register the actions they own, and every action of a record they may open themselves (their own report, an assessed hazard, a form they can see) — not everyone's.
- Saving a record unchanged (M2) keeps the wording of the list entries it already had; only entries new to it take today's wording. A rename by HSE no longer renames old hazards, JSA steps or permits on their next save, or counts as a revision.
- A link to a permit (M3) opens that permit, fetched by its id, in a tab of its own, and says so when it cannot be shown. A hazard (M4) is edited on its own page. A draft JSA's details, System and PPE can be corrected (M5); an approved one changes by a new version; an empty title is refused (L8).
- The history of changes (L1) is offered to those who may read the audit trail (ehs.incident.read).
- A report keeps the name of the incident type it was filed or reclassified as (L2); a type reports already use cannot move to another form; a retired type is not chosen anew (L7).
- HSE's review of a record (L3) is withdrawn when another action is raised on it and asked for again when that closes; a JHSC recommendation's review is done by verifying it, not ticked off in My Actions; closing an incident closes its review in the closer's name. A JHSC recommendation is verified once, and one already Completed before step 11 is not offered it again (L4). Completed is HSE's verification, not a state anyone picks (re-review N1): only whoever may verify is offered it in the state list, and picking it verifies the recommendation and closes its review; those already Completed when step 11 arrived are marked so (migration 0097). Retrying an edited report does not resend what went through (L5).
- Waiting on HSE: whether adding only the product batch code on the investigation should withdraw HSE's review, as any edit to the report does (L6).
- The list standard (decided 10-29, applied to Safety 11-10): the equipment register and the hazard register are tables paged, searched and sorted on the server (every column; unknown keys refused; blanks last both ways), with the standard pager and a header locked while it scrolls; incident types are one sortable table. The hazard register is no longer the latest 300. Safety's other lists are not changed yet.
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
| E56 | Production equipment register | A separate register from E46: production equipment that appears on incident forms, with its area, department and System (a list HSE maintain and merge). HSE maintains it by hand or by spreadsheet (template provided). Pickers search the whole register and offer the machines in the record's area first. Retired machines leave the pickers and stay on the records that name them. | P1 | 2 |

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
| 0.5 | 2026-10-16 | Consolidated, v0.4 reply applied, implementation status added. |
| 0.6 | 2026-10-16 | UAT rounds 6–7 decisions recorded (§1.6); status and open items brought up to the branch. |
| 0.7 | 2026-10-20 | UAT round 8 answers recorded; label type, risk assessment and the incident register built. |
| 0.8 | 2026-10-20 | Round 8's clear open-box requests built (§1.6); SF-15 narrowed. |
| 0.9 | 2026-10-23 | UAT round 9 fixes and requests built (§1.6); SF-15 now the dashboard redesign. |
| 0.10 | 2026-10-27 | UAT round 10 first batch built (§1.6); SF-15 extended. |
| 0.11 | 2026-10-27 | Code review fixes (§1.6, "Review of round 10"); two questions for HSE. |
| 0.12 | 2026-10-28 | Round 10 second batch built (§1.6); SF-15 narrowed. |
| 0.13 | 2026-10-28 | Round 10 third batch built: lists as trees (§1.6); SF-15 narrowed; SF-16. |
| 0.14 | 2026-10-29 | Round 10 fourth batch built: the dashboard, phone install (§1.6); Reports rates corrected; SF-15 narrowed. |
| 0.15 | 2026-11-06 | Round 11 first batch: UAT 11 faults and renames (§1.6); `origin/main` merged in with Staff (§9). |
| 0.16 | 2026-11-09 | Round 12 second batch: the equipment register and pickers; retiring safety equipment (§1.6, E56). |
| 0.17 | 2026-11-09 | Round 12 third batch: incidents, investigations, corrective actions, JHSC workflow, incident types (§1.6). |
| 0.18 | 2026-11-10 | Round 12 fourth batch: hazards, job analyses, permits, photographs; the chart by supervisor (§1.6). |
| 0.19 | 2026-11-10 | Review of the third and fourth batches; the list standard on three lists (§1.6). |

---

## 9. Implementation Status

**Branch `feature/ehs-safety`**, local HEAD as of this version (2026-11-06). `origin/main` was merged in again on 2026-11-06 at `0eb74d69` (`24696674`), bringing Staff. `origin/main` was merged into the branch locally on 2026-10-08 (`6cb8c00b`, plus migration merge `8e033cec`). About 180 commits are ahead of `origin/main`. **None of this is on main or deployed.** The remote `origin/feature/ehs-safety` is stale: it stops at `6a0acc16` (2026-08-30, 21 commits, early Phase 1 only). Ten rounds of HSE UAT have been run on dev; round 10 (2026-10-27) recorded 6 pass / 6 fail, and every defect it reported has since been fixed on the branch, with the decisions in §1.6. Code: `ehs-api` (about 37 routers, 84 migrations) and the `ehs` front end (about 44 pages). The branch also carries identity, mdm, epms, file-api and portal changes that must ship with it.

| Status | Requirements |
|---|---|
| **Implemented on branch** | E01–E04, E08–E11, E13, E15–E16, E18–E23, E25–E26, E29–E42, E46–E50, E52, E54–E57. Also implemented from §1.3: staged area picker, monthly hours worked, 12-type mapping with the Fatality/Unconscious follow-up, multi-select causes, no GPS, signature role snapshot, HSE review gate, draft saving (E17), management dashboard plus per-user layout (E51), audit pack via the built-in auditor role (E53) |
| **Partial** | E05/E06: the engine has group, period-compare, drill-down and injury rates, but no scheduled email distribution or report PDF export was found. E07/E14: outbound email and logging are done; inbound filing is not built (the columns and Message-ID are prepared). E12: first aid is recorded on the incident form (the standalone register was folded in); no dedicated first-aid-box inspection was found, only as a safety-equipment check (E46/E47). E24: near misses go through the incident form; no separate lightweight observation entity was found. E27: warnings ahead of expiry are HSE's expiry notice rules, any number of days (§1.6); an `is_blocking` flag is stored, but nothing enforces it at assignment. E28: "orientation" exists only as a form-template kind. E53: auditor access is a standing role, not time-limited grants. |
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
| SF-08 | UAT round 7 answers (2026-10-15): passing the document quiz stays mandatory; a new document version requires the quiz again. | Done (§1.6) |
| SF-09 | UAT round 7 free-text requirements. **Built**: return-to-work and legal clocks hold the close, statutory/return-to-work sections on medical incidents, reclassification, default lead investigator, injury questions after a name, editable permit checks; the permit end-time defect is fixed. **For HSE in round 8**: "investigation completed" as a status (3); removing a stopped clock from My Actions and the calendar (4 — completed clocks already leave both; the request may mean a clock that no longer applies, now covered by "not required"); notification content with injury detail (9 — health information would go to the whole notification list, PHIPA); and the risk-assessment redesign. Round 8 answers: no new status (3), "not required" was meant (4), email content unchanged (9); the risk assessment is built as confirmed. | Done (§1.6) |
| SF-15 | Waiting, from rounds 9 and 10. **Lost time and cost**: three fields on the return-to-work form for a lost-time injury, seen only by HSE (hours per shift, hours missed on the first day, full shifts missed), four rates under Rules, and HSE's formula, which needs confirming (it multiplies lost hours by the investigator's rate where the employee's wage seems meant). Until then the dashboard's cost is what has been entered, and says how many incidents carry one. **Phone install** to be checked on a real phone over https, sign-in included. (The dashboard and phone install were built in round 10's fourth batch; the lists as trees in the third; the rest of round 10 in the second, §1.6.) | Open |
| SF-16 | A list entry can be moved under another entry, but not back to the top, and Settings → Lists offers no move: entries are added where they belong. If HSE need to reorganise a list, a move tool is a separate piece of work. | Open — by design for now |
| SF-13 | Label body text size. HSE chose 10 pt on the small label only (round 8); of 10 approved labels on dev, the standard size prints 9 again and "fit automatically" picks standard for 7. | Done (§1.6) |
| SF-14 | After merging with main, the Task Inbox email sender may also email corrective-action tasks, depending on its per-type setting; Safety already emails the owner of an action raised on an incident. Check the setting for the corrective-action task type at release, or owners receive two emails. | Open — release check |
| SF-10 | Before go-live: HSE must configure area owners (Settings → Area responsibility) and load the reference lists; the label and certificate PDF fonts need CJK-capable embedding checked in the production image. | Open |
| SF-11 | Release: merge to main, single alembic head per service, and build ehs-api, identity, mdm, epms and file-api images together with their migrations (the branch carries non-ehs migrations). Push the branch: the remote copy is two months stale. | Open |
| SF-12 | Supervisor dropdowns list only same-department users, and some supervisors have no department in the user record. Data fix needed (related to SF-03). | Open |
