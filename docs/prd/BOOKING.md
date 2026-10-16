# Booking (Meeting Rooms) — Product Requirements Document

**Version:** 1.1
**Date:** 2026-10-16
**Status:** In Production (first released 2026-07-09; single-occurrence cancellation and later shell changes are on main)
**Scope:** Company-internal web module for finding, booking, changing and cancelling meeting rooms, with email notification and Outlook-compatible calendar invitations sent over SMTP.

### What changed in this version

- **Translated from Chinese** (V1.0, 2026-07-07) and rewritten as a requirements catalogue with FR-IDs.
- **Brought in line with the code on main.** Every rule was checked against the implementation; rules that were planned but never built are marked **Not implemented** and collected in §16.
- **Platform integration documented:** Portal entry card, Access Control Matrix keys (`view_booking`, `manage_meeting_rooms`), primary + additional role union, module tabs (§3, §12).
- **Campus and Building removed from the user interface** (still stored on the room record) (§7.1, §7.2).
- **New Meeting Summary day view** — all rooms on one Outlook-style grid (§7.4).
- **Recurring meetings:** daily/weekly series, up to 60 occurrences, **series-level editing** and **single-occurrence cancellation** (§7.6, §7.8).
- **Outlook calendar behaviour specified in detail:** stable UID, SEQUENCE, RRULE with COUNT, EXDATE, RECURRENCE-ID, a hand-built time-zone block for Eastern Time (§7.10).
- **Delivery retry and compensation** as built: immediate send, 5 retries with exponential back-off, sync alert, manual resend (§7.11).
- **Statistics dashboard, blacklist, workday-only opening hours, organiser notification on room shutdown and RSVP write-back** are confirmed as not built (§16). RSVP write-back was evaluated on 2026-07-30 and **deliberately not pursued**.
- **Defects found during this review** are listed in §16.

---

## 1. Product Overview

### 1.1 Background

Meeting rooms are a heavily shared resource. Before this module, rooms were reserved by paper sign-up, verbal agreement or ad-hoc Outlook emails, which caused:

- no clear view of which rooms are free, so employees could not quickly find a suitable room;
- frequent double bookings and informal "squatting";
- room equipment information scattered, with no way to filter by capacity, location or equipment;
- meeting notices and calendar entries created by hand, so attendees were missed;
- rooms not released when meetings ended, lowering utilisation.

The Booking module gives the company one place to configure rooms and to book them. It uses the UniOps user directory for attendees and sends standard meeting-invitation emails that Outlook Classic Desktop turns into calendar entries. The company mail server is IMAP-based (no Exchange).

### 1.2 Goals

**Business goals**

- One place for meeting-room configuration and booking.
- An employee can find and book a room in under one minute.
- Fewer conflicts and double bookings.
- Booking, email notification and calendar invitation happen automatically in one flow.
- More transparent, better-utilised meeting rooms.

**User goals**

- See each room's current status and its free time slots.
- Filter rooms by time, location, capacity and equipment.
- After booking, attendees are notified automatically and the meeting appears in Outlook Classic Desktop.
- Reschedule or cancel easily, without manual follow-up.

### 1.3 Success Metrics (targets)

| Metric | Target | How it is measured today |
|---|---|---|
| Booking success rate | ≥ 95% | Not measured (no statistics module — §7.14) |
| Double-booking rate | ≤ 1% | Structurally 0%: the database rejects overlapping confirmed bookings for the same room (BK-BOOK-08) |
| Average time to complete one booking | ≤ 60 s | Not measured |
| Email / calendar-invitation delivery success | ≥ 99% | Visible per message in Admin → Notifications; no aggregate figure |
| Utilisation statistics cover all configured rooms | 100% | Not implemented (§7.14) |

---

## 2. Scope

### 2.1 In scope (built)

- Employees search for and book rooms in the web module.
- Room administrators configure rooms (details, equipment, opening hours, booking window, status, photos) and import them in bulk.
- Live room status and time-slot occupancy (list, room detail, day view across all rooms).
- Conflict pre-check while the form is being filled in, with suggested free times and alternative rooms.
- Single and recurring (daily / weekly) bookings, approved automatically when the room is free.
- Email notification to organiser, attendees and (optionally) a list of room-admin addresses.
- Outlook-compatible meeting invitations: create, update and cancel, including whole-series updates and cancelling a single occurrence.
- Changing and cancelling bookings, delivery-status tracking, automatic retry and manual resend.
- Admin list of all bookings with filters, forced cancellation and CSV export.

### 2.2 Out of scope

- Native mobile app (the web module is responsive).
- Booking from inside Outlook (add-in).
- Door access, e-paper door signs, IoT occupancy sensors.
- Check-in hardware and QR check-in.
- Visitor reception across sites (see VMS.md).
- Approval of bookings — bookings are never routed through the shared approval engine (see EPMS.md §3.3).
- Task Inbox items — the module creates no tasks (see EPMS.md §3.7.1).

---

## 3. Users, Roles and Permissions

Booking uses the shared UniOps Access Control Matrix, administered in **Portal → Admin → Access Control → Permission Matrix** (see PORTAL.md and EPMS.md §1.3). A user's effective permissions are the **union of their primary and additional roles**; System Admin always passes (EPMS.md §1.2).

| User type | Matrix key | Default | Capabilities |
|---|---|---|---|
| Employee | `view_booking` | ON for every role | Browse rooms and status, open the Meeting Summary, check availability, book rooms, search the attendee directory, view **own** bookings, change or cancel **own** bookings |
| Room administrator | `manage_meeting_rooms` | OFF (granted per role in the matrix) | Everything above, plus: create/edit rooms, change room status, bulk import, upload photos, view and force-cancel **all** bookings, export CSV, edit any booking via the API, booking rules and email settings, notification monitor and manual resend |
| IT / system administrator | `system_admin` role | — | Passes every check; manages the matrix, users and roles in Portal |

| ID | Requirement | Status |
|---|---|---|
| BK-AUTH-01 | Sign-in is the UniOps single sign-on; the module opens from the **Meeting Rooms** card on the Portal home page, which is shown to users holding `view_booking` or `manage_meeting_rooms`. | Built |
| BK-AUTH-02 | Every API call is checked on the server against the matrix key it needs. Booking ownership is checked on the server: a user who is neither the organiser nor a room administrator gets "not found" (no information about other people's bookings is revealed). | Built |
| BK-AUTH-03 | The module's side menu shows *Rooms*, *Meeting Summary*, *My Bookings* to `view_booking` holders and *Rooms Admin*, *All Bookings*, *Notifications*, *Settings* to `manage_meeting_rooms` holders. Opening the module root lands on the first menu item the user may see; a user with no permission sees a "no access" page with a link back to the Portal. | Built (see D-05) |
| BK-AUTH-04 | Room titles and organiser names in a room's schedule are visible to every employee (internal transparency). Descriptions and attendee lists are visible only to the organiser and administrators. | Built |

---

## 4. Core Business Assumptions

- The module is internal and uses UniOps single sign-on.
- Attendees are chosen from the UniOps user directory (active users with an email address).
- **Booking is automatic:** if the room is free for the chosen time, the booking is confirmed immediately. There is no approval step.
- Staff use Outlook Classic Desktop on an IMAP mail server. The module sends standard meeting-invitation emails (iCalendar / iTIP / iMIP over SMTP); it never writes into mailboxes or calendars directly.
- All times are shown, entered and validated in the company time zone, **America/Toronto**, regardless of the browser's own time zone.
- The web module is the only booking channel.

---

## 5. User Scenarios

**5.1 Quick booking.** An employee needs a room this afternoon. On *Rooms* they pick the date, start and end time, minimum attendees and equipment; the list shows only rooms that are free for that window. They open a room, click *Book this room*, add a title and attendees and submit. The booking is confirmed and everyone receives an invitation.

**5.2 Conflict and alternatives.** The chosen room is taken. While the form is being filled in, the module shows the conflicting meeting(s), up to three nearest free slots in the same room that day, and up to five other rooms that are free at the requested time, ranked by closeness (same floor → adjacent floor / same area → others). One click applies a suggestion.

**5.3 Change and sync.** The organiser moves the meeting. The room occupancy is updated at once and an updated invitation (same calendar UID, higher sequence number) replaces the entry in every attendee's Outlook.

**5.4 Recurring meeting.** An organiser books a weekly meeting for eight weeks. Eight bookings are created; attendees receive one recurring invitation. Later the organiser cancels one week only — Outlook removes that single occurrence and keeps the rest.

**5.5 Room set-up.** An administrator imports the rooms of a newly fitted-out floor from a spreadsheet, then adds photos and equipment, so that staff can filter and book them.

---

## 6. Process Overview

1. Employee signs in via the Portal and opens Meeting Rooms.
2. Chooses date, start/end time, minimum attendees and filters (floor, area, room type, equipment).
3. The module lists rooms free for that window, with live status.
4. Employee opens a room (detail page: photos, equipment, day timeline, 7-day strip) and starts a booking.
5. Enters title, description, attendees, video-conference need and optional recurrence. A live conflict check runs while typing.
6. On submit the server re-validates time rules, room status, opening hours, booking window, equipment and conflicts.
7. If valid, the booking (or every occurrence of a series) is saved as **confirmed** in one transaction.
8. The room's occupancy and status update immediately for everyone.
9. An email with a meeting invitation is sent to the organiser, attendees and configured room-admin addresses.
10. Changes and cancellations send an update or cancellation invitation with the same calendar UID.
11. Failed deliveries are retried automatically; administrators can monitor and resend.

---

## 7. Functional Requirements

### 7.1 Room Configuration (Admin → Rooms Admin)

| ID | Requirement | Status |
|---|---|---|
| BK-ROOM-01 | Administrators can create and edit rooms. Rooms are never deleted; they are taken out of service by changing status. | Built |
| BK-ROOM-02 | Room fields: **name**, **code** (unique), floor, area, **capacity**, equipment, room type, opening time from/to, booking window (days ahead), owner / responsible department, notes, photos. Campus and building are kept on the record and accepted by import, but are **not shown or edited in the user interface**. | Built |
| BK-ROOM-03 | Equipment options: TV, projector, whiteboard, video conference, phone conference. Room types: standard, training room, boardroom, multi-function hall. | Built |
| BK-ROOM-04 | Opening hours and booking window may be left blank on a room; the module-wide defaults then apply (08:00–20:00, 30 days — §7.12). | Built |
| BK-ROOM-05 | Status: **available**, **disabled**, **maintenance**. Status is changed with a separate *Change status* action that can record a reason in the notes; maintenance notes are shown to employees on the room detail page. | Built |
| BK-ROOM-06 | When a room's status is changed, the result reports how many future confirmed bookings the room still has, with a link to *All Bookings* filtered to that room, so the administrator can cancel or move them. Bookings are **not** cancelled or notified automatically. | Built (automatic organiser notification **Not implemented**, OI-04) |
| BK-ROOM-07 | Photos are uploaded to the shared UniOps file service and shown on the room detail page; existing photos are kept when a room is edited. | Built |
| BK-ROOM-08 | Bulk import from an .xlsx file. The first row holds column names (any order): name, code, campus, building, floor, area, capacity, equipment (comma-separated), room_type, open_time_start, open_time_end. Each row is validated on its own; the result reports how many rooms were created and lists row-level errors (missing name/code, capacity not positive, duplicate code). | Built |
| BK-ROOM-09 | The admin room list can be filtered by floor, area and status. | Built |
| BK-ROOM-10 | Rules: capacity must be a positive whole number; code must be unique (duplicate → "Room code already exists"); disabled and maintenance rooms cannot be booked and are never suggested. | Built |
| BK-ROOM-11 | Changing room details does not alter existing bookings; it affects what is shown and searched from then on. | Built |
| BK-ROOM-12 | A configurable bookable **date range** per room (beyond the "days ahead" window) | **Not implemented** (OI-07) |

### 7.2 Room Search and Listing (Rooms)

| ID | Requirement | Status |
|---|---|---|
| BK-SRCH-01 | *Rooms* is the landing page. Without a time window it lists every room with its live status; with a date + start + end time it lists only rooms that are **available** and have no confirmed booking overlapping that window (meetings that merely touch the edges do not count as overlapping). | Built |
| BK-SRCH-02 | Filters (one row): date, start time, end time (15-minute steps), minimum attendees (rooms with at least that capacity), floor, area, room type, equipment (room must have **all** ticked items). | Built |
| BK-SRCH-03 | Each room card shows name, floor/area, capacity, equipment icons, live status and the next meeting start time within the next 7 days. | Built |
| BK-SRCH-04 | The list refreshes every 60 seconds. | Built |
| BK-SRCH-05 | Filter by current status on the employee list. | **Not implemented** (OI-08) |
| BK-SRCH-06 | **Room detail page:** photos, location, capacity, equipment, maintenance notes, live status, next meeting, a timeline of the selected day within opening hours, and a 7-day strip of upcoming bookings; *Book this room* opens the booking form. | Built |

### 7.3 Room Status

Status is computed each time it is read (no background job). Order of precedence:

| Status | Rule |
|---|---|
| Disabled | Room status is *disabled* (set by an administrator) |
| Maintenance | Room status is *maintenance*; not bookable; the reason (notes) is shown |
| In use | The current time falls inside a confirmed booking |
| Starting soon | The next confirmed booking today starts within 15 minutes |
| Booked | A confirmed booking remains later today, more than 15 minutes away |
| Free | None of the above |

| ID | Requirement | Status |
|---|---|---|
| BK-STAT-01 | Room list, room detail and alternative-room suggestions show the status above. | Built |
| BK-STAT-02 | Status is refreshed at least once a minute on screen (60-second polling). | Built |
| BK-STAT-03 | Room detail shows the next meeting start time (within 7 days). | Built |

### 7.4 Meeting Summary (day view)

| ID | Requirement | Status |
|---|---|---|
| BK-SUM-01 | *Meeting Summary* shows one day for **all** rooms on an Outlook-style day grid: one column per room (sorted by floor then name), rows for time, confirmed meetings drawn as blocks with title and organiser. | Built |
| BK-SUM-02 | Previous / next day and *Today* buttons; the day is a calendar day in America/Toronto, correct on daylight-saving change days. | Built |
| BK-SUM-03 | Disabled rooms are omitted; rooms under maintenance are shown greyed out. Clicking a room header opens its detail page. | Built |

### 7.5 Booking Creation

| ID | Requirement | Status |
|---|---|---|
| BK-BOOK-01 | Booking form fields: title (required, up to 255 characters), description, date, start time, end time, attendees (directory search, multi-select), "Needs video conference", recurrence. The organiser is the signed-in user. | Built |
| BK-BOOK-02 | **Automatic approval:** if all checks pass the booking is saved as *confirmed* immediately. | Built |
| BK-BOOK-03 | End must be after start. Start and end must fall on a **15-minute** boundary (company time zone). | Built (see D-02) |
| BK-BOOK-04 | Duration must be between the configured minimum and maximum (defaults 15 min and 4 h). | Built |
| BK-BOOK-05 | The meeting must start no earlier than the room's opening time and end no later than its closing time (a meeting ending exactly at closing time is allowed). Opening hours apply **every day of the week**; the module has no weekday/weekend distinction. | Built (workday-only rule **Not implemented**, OI-06) |
| BK-BOOK-06 | The start must lie within the booking window (room value or the default 30 days ahead) and not in the past (5-minute grace). | Built |
| BK-BOOK-07 | The room must exist and be *available*. If "Needs video conference" is ticked the room must have video-conference equipment; the form disables *Submit* in that case. The flag is a check only and is not stored. | Built |
| BK-BOOK-08 | **No double booking:** before saving, the server checks every occurrence for overlapping confirmed bookings in the same room. In addition the database itself rejects any two confirmed bookings of the same room whose times overlap, so simultaneous submissions cannot both succeed. A rejected booking returns the same conflict response (conflicts + suggestions) and nothing is saved. | Built |
| BK-BOOK-09 | **Live pre-check:** while the form is filled in (after a short pause in typing) the module checks for conflicts and shows either "Available" or the conflicts with suggestions (§7.7). Out-of-date responses are discarded. | Built |
| BK-BOOK-10 | After a successful booking an audit record is written and the notification is sent (§7.9). A delivery failure never undoes the booking. | Built |

### 7.6 Recurring Meetings

| ID | Requirement | Status |
|---|---|---|
| BK-REC-01 | Recurrence: none, **daily** or **weekly** (same weekday), every *N* days/weeks, ending after a number of occurrences (2–60) or on a date (inclusive). | Built |
| BK-REC-02 | A series may not exceed **60** occurrences; otherwise it is refused. | Built |
| BK-REC-03 | Occurrences beyond the booking window are dropped; the organiser is told the series was shortened. | Built |
| BK-REC-04 | Each occurrence is saved as its own booking (own occupancy record), all sharing one series ID and one calendar UID. | Built |
| BK-REC-05 | **All or nothing:** if any occurrence conflicts, no occurrence is created; the response lists the conflicting dates. | Built |
| BK-REC-06 | One recurring invitation is sent for the whole series. The recurrence rule always states the exact number of occurrences that were created (never an end date), so Outlook shows exactly what the module holds. | Built |
| BK-REC-07 | Individual occurrences cannot be edited one by one; editing a series member edits the series (§7.8). Single occurrences can be cancelled. | Built (per-occurrence edit **Not implemented**, OI-10) |
| BK-REC-08 | Recurrence patterns other than daily/weekly (e.g. monthly, specific weekdays) | **Not implemented** (OI-10) |

### 7.7 Conflict Hints and Suggestions ("Available time")

| ID | Requirement | Status |
|---|---|---|
| BK-SUG-01 | When the chosen room is taken, the module shows the conflicting meetings (title, organiser, time) — for a series, per conflicting date. | Built |
| BK-SUG-02 | **Nearest slots:** up to 3 free slots of the same length in the same room on the same day, inside opening hours, on the configured slot grid, closest to the requested start. | Built |
| BK-SUG-03 | **Alternative rooms:** up to 5 other *available* rooms free at the requested time, each with its live status, ranked: (1) same floor and capacity between the attendee count and twice that (or same floor if no attendee count); (2) adjacent floor or same area; (3) all others, ordered by capacity shortfall, closeness of capacity, then missing equipment. Alternatives are ranked, not filtered, by capacity. | Built |
| BK-SUG-04 | Clicking a suggested slot or room fills it into the form. | Built |
| BK-SUG-05 | Suggestions are offered when creating or editing a single booking. A series edit that conflicts returns the conflicting dates only, without suggestions. | Built |

### 7.8 Changing and Cancelling Bookings

| ID | Requirement | Status |
|---|---|---|
| BK-CHG-01 | *My Bookings* lists the user's bookings as organiser in two tabs: **Upcoming** (confirmed and not yet ended) and **Past** (ended or cancelled). Each row shows title, room, date/time, attendee count, status and invitation status (*Invite sent / pending / updating / failed*), with a recurring-series icon. Edit and Cancel are offered on Upcoming rows. | Built |
| BK-CHG-02 | **Edit single booking:** title, description, attendees, room, date and times. Time or room changes are fully re-validated (grid, duration, opening hours, room available, conflicts excluding itself) and the booking window is re-checked for non-administrators. Room status is re-checked even when only the time changes. | Built |
| BK-CHG-03 | Once a booking has **started**, only an administrator may change it (any field). | Built |
| BK-CHG-04 | Each change keeps the calendar UID, increases the sequence number and sends an updated invitation. | Built |
| BK-CHG-05 | **Edit series:** title, description (can be cleared), attendees, room, and a new start/end **time of day** applied to every **future** occurrence on its own date. All future occurrences are checked for conflicts (the series' own bookings excluded); any conflict rejects the whole edit. One update invitation is sent for the series. Past occurrences are left unchanged in the module. | Built |
| BK-CHG-06 | Individually cancelled occurrences move with series time changes but stay cancelled, so they remain excluded from the series invitation. | Built |
| BK-CHG-07 | **Cancel single booking:** the slot is released immediately and a cancellation invitation is sent. | Built (see OI-12) |
| BK-CHG-08 | **Cancel a recurring meeting:** the dialog offers *Keep*, **This occurrence** or **Cancel series**. *This occurrence* cancels only that date (refused once it has started) and Outlook removes only that instance. *Cancel series* cancels every occurrence that has not yet started; past and in-progress occurrences are kept. | Built |
| BK-CHG-09 | Administrators can force-cancel any booking from *All Bookings* (series-aware dialog, same three choices). Forced cancellations are audited as such. | Built |
| BK-CHG-10 | Administrators can edit other users' bookings through the API; the user interface edits only the user's own bookings. | Partially built (OI-11) |
| BK-CHG-11 | Every create, change and cancellation writes an audit record with the actor and before/after values. | Built (see §7.15) |

### 7.9 Email Notifications

| ID | Requirement | Status |
|---|---|---|
| BK-NOTIF-01 | Notifications are sent on: booking created, booking updated (single or series), booking cancelled, single occurrence cancelled; and a **sync alert** to administrators when a delivery finally fails (§7.11). | Built (see D-01) |
| BK-NOTIF-02 | Recipients: the organiser, every attendee, and — when *Notify room admins* is on — the room-admin email addresses configured in Settings. Users without an email address are skipped and noted on the notification record. | Built |
| BK-NOTIF-03 | Content: subject `[Booking] Created / Updated / Cancelled: <title> — <room> <date time>`; plain-text body with the action, title, time range and time zone, room and location, organiser, attendee list and booking reference; plus the calendar invitation (§7.10). | Built |
| BK-NOTIF-04 | One email per event: a series is announced by a single email. Recipients are fixed when the notification is created; retries and resends use the same list. | Built |
| BK-NOTIF-05 | Cancellation emails go to the people on the booking at the time of cancellation. | Built |
| BK-NOTIF-06 | **Sending account:** the module's own SMTP settings (Settings → Email); if no host is set there, the shared company SMTP settings are used. If neither has a host the module runs in **log-only mode**: notifications are recorded as sent with the note "SMTP not configured". | Built |
| BK-NOTIF-07 | Booking emails are independent of the platform Task Inbox / notification outbox and of user notification preferences (EPMS.md §3.9). | Built (by design) |
| BK-NOTIF-08 | Per-room owner notification (notify the owner/department of the specific room) | **Not implemented** — only one global room-admin list (OI-05) |

### 7.10 Outlook Calendar Invitations (IMAP mail environment)

Because the mail server is not Exchange, the module cannot write to calendars on the server side. Outlook Classic Desktop creates, updates and removes calendar entries itself when it receives a standard invitation email.

| ID | Requirement | Status |
|---|---|---|
| BK-CAL-01 | Each email is multipart: a readable text part, a `text/calendar` part with the method, and an `invite.ics` attachment — compatible with Outlook Classic Desktop. | Built |
| BK-CAL-02 | Standards: iCalendar (RFC 5545), iTIP (RFC 5546), iMIP (RFC 6047). Create and update use `METHOD:REQUEST`; cancellation uses `METHOD:CANCEL` with status *cancelled*. | Built |
| BK-CAL-03 | The event carries: stable UID, sequence number, start/end in the company time zone, title, description, location (room name with floor and area), organiser, and every attendee as a required participant with a reply requested. | Built |
| BK-CAL-04 | **Stable identity:** a single booking has its own UID; a series shares one UID. Every update or cancellation reuses the UID with a higher sequence number so Outlook updates the existing entry instead of creating a duplicate. | Built |
| BK-CAL-05 | **Time zone:** for Eastern Time the time-zone block is built with yearly rules (2nd Sunday in March / 1st Sunday in November), because Outlook Classic ignores the date-list form and would show summer meetings one hour late. | Built |
| BK-CAL-06 | **Series:** the series invitation includes the recurrence rule (daily/weekly, interval, exact count) and lists individually cancelled future dates as exceptions so Outlook does not recreate them. | Built |
| BK-CAL-07 | **Single-occurrence cancellation:** a `CANCEL` that names the occurrence (recurrence ID) and **omits the recurrence rule**, so Outlook removes only that instance (sending both would clear the whole series). | Built |
| BK-CAL-08 | **Organiser mode** (Settings): *System mailbox* (default — the invitation organiser is the module's sending address) or *Meeting organiser* (organiser is the booking user; the email is sent "on behalf of" them, which the mail gateway must permit). | Built |
| BK-CAL-09 | Attendee replies (accept / decline / tentative) are not read back into the module. Evaluated 2026-07-30 and **decided not to build**: the module only sends mail, and reading replies would require a shared organiser mailbox plus an inbound mail parser. | Not implemented (by decision, OI-09) |
| BK-CAL-10 | Known limitations accepted for series edits: Outlook also rewrites past occurrences when a series update arrives; and if the earliest future occurrence was cancelled before a series edit, Outlook can show one extra trailing occurrence. | Known (OI-13) |

### 7.11 Delivery Failure, Retry and Compensation

| ID | Requirement | Status |
|---|---|---|
| BK-SYNC-01 | The booking is saved first; sending is attempted immediately afterwards. A failed send never rolls back the booking. | Built |
| BK-SYNC-02 | Each booking carries an invitation status: *pending* (queued / changed), *sent*, *failed*, *compensating* (retry in progress). For a series the status of the series email is applied to all its confirmed occurrences; a single-occurrence cancellation affects only that occurrence. | Built |
| BK-SYNC-03 | A background job (every 5 minutes, one instance at a time) retries failed notifications with exponential back-off (5, 10, 20, 40, 80 minutes), up to **5** retries. Retries rebuild the invitation from the stored notification, so a single-occurrence cancellation is never retried as a series cancellation. | Built |
| BK-SYNC-04 | After the 5th failed retry a **sync alert** is sent to the configured room-admin addresses (regardless of the *Notify room admins* switch); if none are configured the alert is only recorded. | Built (see D-01) |
| BK-SYNC-05 | **Admin → Notifications** lists every notification (type, recipients, status, retries, error, sent time) with status/type filters and a *Resend* button. Resending a message that was already sent delivers a duplicate; the page warns about this. | Built |

### 7.12 Admin Settings

| ID | Requirement | Status |
|---|---|---|
| BK-ADM-01 | **Booking rules:** slot minutes (default 15), minimum duration (15 min), maximum duration (240 min), days bookable ahead (30), default opening time (08:00) and closing time (20:00), *Notify room admins* switch and room-admin email list. | Built (slot minutes see D-02) |
| BK-ADM-02 | **Email (SMTP):** host, port, username, password, TLS, from address, organiser mode. When the host is empty the shared company SMTP settings are used. | Built (see D-06) |
| BK-ADM-03 | **All Bookings:** every booking with filters (room, organiser name, date range, status), pagination, sync status, forced cancellation and CSV export of the filtered set (room, title, organiser, start, end in company time, status, attendee count, sync status, created). | Built (see D-03, D-08) |

### 7.13 Attendee Directory

| ID | Requirement | Status |
|---|---|---|
| BK-DIR-01 | Attendees are searched by name or email among **active** UniOps users that have an email address; up to 50 results, sorted by name. Search text is treated literally. | Built |

### 7.14 Reporting and Statistics

| ID | Requirement | Status |
|---|---|---|
| BK-RPT-01 | Room utilisation, booking / cancellation / conflict counts, average booking length, most-used rooms, email and invitation success rates, by floor / department / period. | **Not implemented** (OI-01). Today only the All Bookings list, CSV export and the Notifications monitor exist. |

### 7.15 Audit

| ID | Requirement | Status |
|---|---|---|
| BK-AUD-01 | Booking create, update, cancel and forced cancel are recorded with actor, time and before/after snapshot. | Built |
| BK-AUD-02 | Room configuration changes (create, edit, status change) are audited. | **Not implemented** (OI-03) |
| BK-AUD-03 | Audit records can be viewed in the module. | **Not implemented** (OI-03) — records are kept in the database only |

---

## 8. Key Business Rules (summary)

| Rule | Value as built |
|---|---|
| Approval | Automatic when the room is free |
| Conflict check | On every submit, for every occurrence; enforced again by the database |
| Status refresh delay | ≤ 60 s (screen polling) |
| Booking time grid | 15 minutes (fixed) |
| Duration | 15 min – 4 h (configurable) |
| Booking window | 30 days ahead (configurable globally and per room) |
| Opening hours | 08:00 – 20:00 every day (configurable globally and per room) |
| "Starting soon" | Next meeting within 15 minutes |
| Disabled / maintenance rooms | Not bookable, not suggested; maintenance reason shown |
| Recurring meetings | Daily/weekly, ≤ 60 occurrences, one occupancy record and one shared calendar UID per series |
| Calendar identity | Stable UID + increasing sequence for every update and cancellation |
| Invitation email | Readable text **and** standard calendar content |
| Attendee replies | Not read back |
| Time zone | America/Toronto for display, entry, validation, emails and export |
| Logs | Booking audit log, notification log (each message, its status and error) |

---

## 9. Exceptions and Handling

| Situation | Behaviour |
|---|---|
| Conflict at submit | Room reported as taken; conflicts, nearest slots and alternative rooms shown; nothing saved. |
| Two people submit the same slot at the same time | The database accepts only one; the other receives the normal conflict response. |
| Email sending fails | Booking stays confirmed; status *failed*; automatic retry (§7.11); after 5 retries a sync alert goes to the room-admin addresses; the failure is visible in Admin → Notifications and on the booking. |
| SMTP not configured | Log-only mode: notification recorded, nothing sent. |
| Room disabled or put under maintenance | New bookings and edits on that room are refused; the administrator is told how many future bookings remain and handles them from All Bookings. Organisers are not notified automatically (OI-04). |
| Booking already started | Organiser can no longer edit it; a recurring occurrence can no longer be cancelled individually. |

---

## 10. Non-Functional Requirements

| Area | Requirement | Status |
|---|---|---|
| Performance | Normal queries ≤ 2 s; booking submit ≤ 3 s (the first send attempt happens within the submit; SMTP timeout 10 s); 1,000 concurrent users; 200 rooms with live status. | Targets, not load-tested |
| Availability | 99.9% target; automatic retry and manual resend for email; audit for key actions. | Retry built; availability not measured |
| Security | UniOps single sign-on; matrix-based authorisation on every endpoint; ownership checks that do not reveal other users' bookings; attendee search escapes wildcard characters. | Built (see D-06, D-08) |
| Compatibility | Current desktop browsers; responsive layout; English-only interface. | Built |

---

## 11. Data Objects

**Meeting room** — ID, name, code (unique), campus, building, floor, area, capacity (> 0), equipment list, room type, opening time from/to, days bookable ahead, status, owner department, notes, photo references, created/updated.

**Booking** — ID, room, title, description, organiser, attendee list, start, end (end > start), status (*confirmed* / *cancelled*), series ID and recurrence rule (series only), calendar UID, invitation sequence, invitation status (*pending / sent / failed / compensating*), created/updated. Overlapping confirmed bookings of the same room are rejected by the database.

**Notification record** — ID, booking, type (*created / updated / cancelled / single-occurrence cancelled / sync alert*), recipient emails, status (*pending / sent / failed*), error, retry count, sent time, created time.

**Audit record** — ID, booking, action (*create / update / cancel / force_cancel*), actor, before, after, time.

**Module settings** (one record) — SMTP settings, booking rules, organiser mode.

---

## 12. Platform Integration

| Area | Behaviour |
|---|---|
| Portal | *Meeting Rooms* card on the Portal home page (see PORTAL.md); sign-in session handed to the module. |
| Permissions | Access Control Matrix keys `view_booking`, `manage_meeting_rooms`; union of primary and additional roles; System Admin passes (EPMS.md §1.2–§1.3). |
| Users | Organisers and attendees are UniOps users (name, email, active flag) maintained in Portal. |
| File service | Room photos are stored in the shared UniOps file service (EPMS.md §3.4.1). |
| Email | Own SMTP settings, falling back to the shared company SMTP settings. |
| Shell | Standard UniOps module shell: side menu, in-memory tabs (cleared when leaving the module), document tabs titled by room name, back links that close their tab. |
| Not used | Approval engine, Task Inbox, platform notification outbox, Finance/Budget. |

---

## 13. Milestones

| Phase | Content | Status |
|---|---|---|
| 1 — MVP | Room configuration, search and booking, status, conflict hints and suggestions, email notification, Outlook invitations (create / update / cancel), basic recurring meetings | **Delivered** 2026-07-09 |
| 1.5 | Meeting Summary day view; series-level editing; single-occurrence cancellation; UI removal of campus/building | **Delivered** (single-occurrence cancellation merged 2026-07-16) |
| 2 | Statistics dashboard, bulk admin operations, improved compensation back office, richer recurrence | Not started (OI-01, OI-10) |
| 3 | Outlook add-in, mobile H5, door signs and check-in | Not started (out of scope) |

---

## 14. Risks and Dependencies

**Dependencies:** UniOps single sign-on and user directory; SMTP server or mail gateway; Outlook Classic Desktop; the shared file service; the PostgreSQL range-exclusion extension used for the no-double-booking rule.

| Risk | Mitigation / status |
|---|---|
| The mail gateway may alter or strip `text/calendar` / `.ics` content | Verified with real Outlook Classic at go-live; organiser mode can be switched |
| Outlook versions render invitations differently | Hand-built Eastern Time zone block; single-occurrence cancel verified on a real client |
| Attendee replies are not read back | Accepted (BK-CAL-09) |
| Directory data quality (users without email) | Such users are excluded from search and skipped as recipients |
| Incomplete room master data at go-live | Bulk import with row-level error report |

---

## 15. Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Administrators can add, edit and disable rooms | Met |
| 2 | Employees can search rooms by time, location, capacity and equipment | Met |
| 3 | A free room is booked automatically; a taken slot cannot be double-booked | Met |
| 4 | Live room status and upcoming schedule are shown correctly | Met |
| 5 | On conflict the module offers free times and alternative rooms | Met |
| 6 | Attendees receive an email after booking | Met |
| 7 | Outlook Classic Desktop recognises the invitation and creates the calendar entry | Met (verified on real client, including daylight-saving times) |
| 8 | Outlook recognises updates and cancellations, including series edits and single-occurrence cancellation | Met |
| 9 | Administrators can see all bookings, notification status and sync status | Met |

---

## 16. Open Items

| ID | Item | Status |
|---|---|---|
| OI-01 | Statistics dashboard: utilisation, booking/cancellation/conflict counts, average length, top rooms, delivery success rates, by floor/department/period (BK-RPT-01). | Not implemented |
| OI-02 | Blacklist (blocking users from booking) from V1.0 §5.2. | Not implemented |
| OI-03 | Audit of room configuration changes, and an in-module audit viewer (BK-AUD-02/03). | Not implemented |
| OI-04 | When a room is disabled or put under maintenance, notify the organisers of affected future bookings and offer bulk move/cancel (BK-ROOM-06). | Not implemented |
| OI-05 | Notify a specific room's owner/department instead of one global room-admin list (BK-NOTIF-08). | Not implemented |
| OI-06 | Workday-only default opening hours (V1.0 said "working days 08:00–20:00"); weekends are currently bookable. | Not implemented |
| OI-07 | Per-room bookable date range (BK-ROOM-12). | Not implemented |
| OI-08 | Filter by current status on the employee room list (BK-SRCH-05). | Not implemented |
| OI-09 | Read back attendee accept/decline replies (BK-CAL-09) — evaluated and decided not to build (2026-07-30). | Not implemented |
| OI-10 | Edit an individual occurrence of a series; monthly or multi-weekday recurrence (BK-REC-07/08). | Not implemented |
| OI-11 | Administrator editing of other users' bookings from the user interface (API supports it; also needed for the "admin special handling" of started meetings). | Not implemented |
| OI-12 | A standalone (non-recurring) booking can be cancelled by its organiser even after it has started or ended, which sends a cancellation to all attendees; V1.0 allowed cancelling only meetings not yet started. Decide the intended rule. | Open |
| OI-13 | Series-edit limitations: Outlook rewrites past occurrences; an extra trailing occurrence can appear if the earliest future occurrence was cancelled first; cancelled occurrences keep invitation status *pending* after a series edit (display only). | Open |
| D-01 | The sync alert is sent through the booking-invitation path: administrators receive a calendar `REQUEST` for the meeting (which can add it to their calendars, even for a cancelled meeting) rather than a plain alert, and a successful alert send marks the booking's invitation status as *sent*, hiding the original failure. | Defect |
| D-02 | The *Slot minutes* setting only affects suggestions; booking validation is fixed at a 15-minute grid. | Defect |
| D-03 | All Bookings status filter offers *Completed* and *No Show*, which no booking can have. | Defect |
| D-04 | Rooms, Room detail, New booking and Edit pages take "today" from the UTC date, so after 20:00 (EDT) / 19:00 (EST) they default to tomorrow. | Defect |
| D-05 | The module side menu checks only the user's primary role, while the server uses primary + additional roles: a user granted `manage_meeting_rooms` through an additional role is authorised but sees no admin menu. | Defect |
| D-06 | The SMTP password is returned in clear text to every room administrator through the Settings page/API. | Defect |
| D-07 | Equipment and room-type values from the bulk import are not checked against the allowed lists. | Defect |
| D-08 | CSV export does not neutralise cell values beginning with `=`, `+`, `-` or `@` (spreadsheet formula injection via booking titles). | Defect |
