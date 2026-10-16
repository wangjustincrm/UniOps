# VMS Visitor Self-Registration and QR Check-In — Product Requirements Document

- **Version:** 0.2
- **Date:** 2026-10-16
- **Status:** Proposed (v0.2, stakeholder decisions pending)
- **Scope:** Add an invitation-based way to register visitors in VMS — the visiting company's contact enters the visitors, the Host confirms, and each visitor gets a personal QR code for per-person check-in and check-out. Extends VMS.md; the existing Host-entered flow is unchanged.
- **Source:** VMS self-registration proposal v0.2 (2026-10-14) and stakeholder feedback from the review board (round 1, 2026-10-14).

### What changed in this version

Version 0.2 incorporates the round-1 answers from Kirk and Kris (2026-10-14):

- Approval happens when the invitation is created, before visitor names are known.
- A VMS administrator can list departments whose invitations skip approval.
- Check-in and check-out are per visitor, not per visit.
- Each invitation also gets its own QR code that opens the registration form.
- Every email to a contact or visitor is copied to the Host.
- Email templates and the privacy notice are editable by a VMS administrator.
- GMP and lab visitors complete the health declaration before arrival.
- Not adopted: the contact starting a registration without an invitation (sending a link already requires knowing the company and contact, which is what the invitation records).

Six round-2 decisions (R2-1 to R2-6) remain open. A first version has been built on the test system using the suggested defaults for these decisions, for UAT; it is not released.

---

## 1. Summary

For supplier groups, audits, contractor crews and events, the Host often does not know who is coming and today collects names by email and types them in. This proposal moves data entry to the visiting company and makes arrival faster: the Host creates an invitation for a company or event, the company's contact enters the visitors on a public web form, the Host confirms, and each visitor receives a personal QR code.

Stakeholder view (Kirk, round 1): the current registration is workload-heavy for the company, and the work should move to the guest.

## 2. Current flow vs proposed flow

| Step | Current flow (unchanged) | New flow: invitation |
| --- | --- | --- |
| Who enters visitor details | Host, inside VMS | Contact person at the visiting company, on a web form |
| What the Host enters | Every visitor's name, company, contact details | Company or event name, date and time, purpose, area, contact name and email |
| Approval (non-office areas) | When the visit is created, with names | When the invitation is created, before names; skipped for exempt departments |
| Host confirmation | Not needed | Host reviews the submitted list; can edit, remove or send back |
| What visitors receive | Nothing before arrival | A personal QR code by email; GMP and lab visitors also a health declaration link |
| Arrival | Reception finds the visit, prints a badge | Depends on decision R2-1 |
| Check-in and check-out | Whole visit at once | Each visitor individually |

## 3. Users

| User | Role in this flow |
| --- | --- |
| Host | Creates the invitation, reviews and confirms the visitor list |
| Approvers | Existing VMS approval chain (Department Manager, then Quality Manager where configured) approve the invitation |
| Contact person (external) | Enters and updates the visitor list on the public form, without a login |
| Visitor (external) | Receives a personal QR code; GMP/lab visitors complete a health declaration |
| Reception, HSE | Check visitors in and out; monitor who is on site |
| VMS administrator | Maintains the exempt-department list, email templates and privacy notice |

## 4. Functional requirements

### 4.1 Invitation and approval

| ID | Requirement |
| --- | --- |
| FR-VSR-01 | A new **Invite Visitors** page lets a Host create an invitation: company or event name, visit date, planned arrival and departure, purpose, access area, Host (defaults to self), contact name and email, optional message, expected number of visitors, PPE needed. |
| FR-VSR-02 | On creation, VMS generates a registration link and an **invitation QR code** that opens the same form; the Host can show, print or forward it, resend the link, or cancel the invitation. |
| FR-VSR-03 | Office-area invitations, and invitations whose Host's department is on the administrator's exemption list, need no approval (see R2-5 on whose department counts). |
| FR-VSR-04 | Other invitations go through the existing VMS approval chain, which approves company, purpose, area and date. If rejected, the Host is told and nothing is sent to the contact. |
| FR-VSR-05 | The contact receives the registration link by email, copied to the Host; for invitations needing approval, timing follows R2-3. |
| FR-VSR-06 | An **Invitations** list shows all invitations with status, with the same visibility rules as visits. |

### 4.2 Contact registration

| ID | Requirement |
| --- | --- |
| FR-VSR-10 | The public **Visitor Registration** form needs no login and shows only the company or event, date and Host name. |
| FR-VSR-11 | One row per visitor: first name, last name, company, job title, phone, email, visitor type. Email is optional; a visitor without email has their QR code sent to the contact. |
| FR-VSR-12 | The contact can change the list until the Host confirms. Whether the form accepts late additions after confirmation is decision R2-2. |
| FR-VSR-13 | A submission or update notifies the Host by email and creates a **Review Visitor Registration** task in the Task Inbox (PORTAL.md). |
| FR-VSR-14 | If nothing has been submitted two business days before the visit, one reminder goes to the contact, copied to the Host. |

### 4.3 Host review and visit creation

| ID | Requirement |
| --- | --- |
| FR-VSR-20 | On **Review Registration**, the Host can edit or remove visitors, confirm the list, or send it back to the contact with a note. |
| FR-VSR-21 | Visitors are matched to existing VMS visitor records by email, so training and PPE completed in the last 12 months carry over. |
| FR-VSR-22 | Confirming creates the visit as confirmed; names do not go through approval again. |
| FR-VSR-23 | The invitation keeps a link to the visit it created, so each visitor can be traced to who registered them and when. |

### 4.4 Visitor passes, arrival and departure

| ID | Requirement |
| --- | --- |
| FR-VSR-30 | On confirmation, each visitor receives a personal QR code by email (or via the contact), copied to the Host. |
| FR-VSR-31 | GMP and lab visitors also receive a personal health declaration link to complete before arrival (who completes it: R2-4; phase: R2-6). |
| FR-VSR-32 | **Scan Check-In / Check-Out** checks a visitor in or out from their QR code; how the code is used on site (badge and reception scan, paperless self check-in, or by area) is decision R2-1. Training, PPE and health-declaration checks stay as today. |
| FR-VSR-33 | Check-in and check-out are per visitor. The visit closes when the last visitor checks out. |
| FR-VSR-34 | **On-Site Now** lists people on site rather than visits. |
| FR-VSR-35 | Anyone who can see a visit can scan its visitors (subject to R2-1). |

### 4.5 Administration

| ID | Requirement |
| --- | --- |
| FR-VSR-40 | VMS Admin maintains an **approval-exempt departments** list; whether a department is exempt is the administrator's decision, not a system rule. |
| FR-VSR-41 | VMS Admin can edit **email templates** (logo, subject, wording) for every email in this flow, to company standards (phase per R2-6). |
| FR-VSR-42 | VMS Admin can edit the **privacy notice** shown on every public form. |

### 4.6 Emails

All emails to a contact or visitor are copied to the Host. VMS emails move from plain text to formatted emails so the QR code and logo can be shown.

| Email | To | When |
| --- | --- | --- |
| Registration request with link | Contact, copy Host | Invitation created (once approved, if approval applies) or resent |
| Invitation rejected | Host | Approver rejects the invitation |
| Registration received | Host, plus Task Inbox task | Contact submits or updates the list |
| Changes requested | Contact, copy Host | Host sends the list back |
| Visitor QR code | Each visitor (or the contact), copy Host | Host confirms the list |
| Health declaration request | Each GMP or lab visitor, copy Host | Host confirms the list |
| Reminder: registration not completed | Contact, copy Host | Two business days before the visit if nothing submitted |

## 5. Security and privacy

The public forms are the first VMS pages reachable without a login.

| ID | Requirement |
| --- | --- |
| FR-VSR-50 | Only the registration and health-declaration pages and their functions are reachable from the internet; the rest of VMS stays internal. |
| FR-VSR-51 | Links carry a long random code; VMS stores only a fingerprint of it. |
| FR-VSR-52 | Links expire at the end of the visit date or on cancellation. Issuing a fresh link invalidates the old one. |
| FR-VSR-53 | Anyone with the invitation QR code can add names, but nobody added this way receives a QR code until the Host confirms them. |
| FR-VSR-54 | The forms never show the Host's email or phone, other visits, or any other VMS data; they accept visitor fields only, with length limits and a maximum number of visitors. Area, purpose and date are fixed by the Host. |
| FR-VSR-55 | Rate limits apply to loading and submitting the forms from one address. |
| FR-VSR-56 | Anything typed on a form is treated as plain text in every email (no injected links or content). |
| FR-VSR-57 | Each visitor's QR code is a random code, not the internal visit number; it works only for that visit and stops working after check-out, cancellation or the visit date. |
| FR-VSR-58 | Every step (created, approved, link sent, submitted, sent back, confirmed, QR sent, checked in, checked out) is written to the VMS audit log. |
| FR-VSR-59 | A data-retention period for visitor personal data is applied once supplied by Legal or HR. |

## 6. Impact on existing VMS features

- Visits created from an invitation are normal visits: they appear in lists, reports, the dashboard, and no-show and overdue reminders.
- Invitations not yet confirmed are not visits and do not appear in reports or trigger no-show reminders.
- Approval for invitation visits moves to the invitation; hand-entered visits keep approval as today. The exemption list applies only to invitations.
- Per-visitor check-in and check-out changes On-Site Now, check-out, overdue reminders and reports for invitation visits.
- One VMS data change (invitations, visitor passes, admin settings). No change to other modules.
- VMS.md, the training deck and the in-app assistant's VMS guide are updated with this change.

## 7. Out of scope

- Self check-in kiosk (unless R2-1 B or C is chosen).
- Visitors editing their own details after Host confirmation.
- Languages other than English on forms and emails.
- Calendar invitations attached to the QR email.
- Recurring invitations for regular contractors.

## 8. Phasing and delivery

Proposed phase 1 (subject to R2-6): invitation, approval at creation, exemption list, contact form, invitation QR code, Host review, visitor QR codes, per-visitor check-in and check-out, copy to Host, privacy notice setting. Proposed phase 2: email template editor with logo, health declaration before arrival.

Delivery steps:

1. Round-2 decisions answered; email wording and privacy notice text agreed.
2. IT opens public access to the public forms only.
3. Build invitations, approval at creation, exemption list, public form, invitation QR code, Host review.
4. Build visitor QR emails and per-visitor check-in and check-out in the form chosen in R2-1.
5. Test on the test system with a real supplier contact; collect UAT feedback.
6. Release to production; brief Hosts and reception.

Timing will be estimated once R2-1 and R2-6 are answered.

**Test-system status (2026-10-15):** a first version is running on the test system for UAT, built with these interim defaults: R2-1 keep badge and reception scan (with a "check in without badge" option for comparison); R2-2 form closes on confirmation; R2-3 link sent after approval; R2-4 health declaration still completed on site; R2-5 Host's department; R2-6 template editor in phase 2. Email wording is fixed in this version. Test emails are captured and never reach real inboxes. These defaults do not pre-empt the stakeholder decisions.

## 9. Related documents

- VMS.md — base visitor management requirements this proposal extends.
- PORTAL.md — Task Inbox, notifications and the in-app assistant guide.
- SAFETY.md — HSE use of on-site lists.

## 10. Open Items

Round-1 decisions (Kirk, 2026-10-14; all round-1 comments are marked resolved) are reflected in the requirements above. Note: Kirk's round-1 answers confirm the proposed flow, statuses, screens, security and scope sections as "accurate, to be confirmed during UAT".

| ID | Item | Status |
| --- | --- | --- |
| R2-1 | Badge and reception scan, or paperless? A) keep badge and reception/Host scan; B) paperless — QR on the phone is the pass, visitors check themselves in and out with a link sent to the contact (requested by Kris); C) by area — paperless for office, badge and reception scan for production, GMP and lab. Owners: HSE, Quality, Reception, Engineering. Largest effect on scope. | Pending decision |
| R2-2 | Can people register through the invitation QR code after the Host has confirmed (last-minute additions)? A) no, form closes on confirmation; B) yes, late entries wait for Host confirmation without new approval. Kirk asked for the invitation QR code for last-minute additions and also chose "no additions after confirmation in phase 1" — these conflict. Owners: Kirk, Hosts. | Pending decision |
| R2-3 | For invitations needing approval, when does the contact get the link? A) after approval; B) immediately, in parallel with approval. Owners: Hosts, Department Managers. | Pending decision |
| R2-4 | Who completes the advance health declaration? A) each visitor via their own link (visitors without email on site); B) the contact for everyone. Owner: Quality. | Pending decision |
| R2-5 | Exemption list: Host's department or the department of the person who created the invitation? Owner: admin owner. | Pending decision |
| R2-6 | Phase-1 scope: proposed split in section 8 (template editor and advance health declaration in phase 2). Owners: Justin, Kirk. | Pending decision |
| VSR-OI-01 | Kris (round 1): the Host should not enter any initial data; the contact should supply all details via a link. Not adopted by the product owner (an invitation must record company and contact to send the link); Kris has not yet confirmed acceptance. | Closed by product owner; awaiting stakeholder acknowledgement |
| VSR-OI-02 | Kris (round 1): approval is redundant for Engineering visitors. Addressed by the admin exemption list (FR-VSR-40); whether Engineering is exempt is the administrator's decision. | Addressed; configuration pending |
| VSR-OI-03 | Data-retention period for visitor personal data to be supplied by Legal or HR. | Open |
| VSR-OI-04 | Final email wording and privacy notice text to be agreed before release. | Open |
| VSR-OI-05 | IT to open public internet access to the public forms only. | Open |
| VSR-OI-06 | Delivery timing to be estimated after R2-1 and R2-6. | Open |
| VSR-OI-07 | Proposed flow, statuses, screens, security and scope to be confirmed in UAT (Kirk). | Open — UAT |
