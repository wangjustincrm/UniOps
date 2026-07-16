# Booking — Single-Occurrence Cancel (RECURRENCE-ID)

**Date:** 2026-07-16
**Module:** booking-api (8010) / booking (5178)
**Status:** Design approved, pending implementation plan

## Problem

Cancelling one meeting of a recurring series is impossible today. Both cancel
dialogs either offer only "Cancel series", or offer a "This occurrence" button
that fails.

Three findings from the current code drive this design:

1. `POST /bookings/{id}/cancel?series=false` already exists, but explicitly
   rejects series members with `400 series_member_use_series_cancel`
   (`booking-api/app/api/v1/bookings.py:1331-1335`).
2. `AllBookingsPage.tsx:108-117` already renders a "This occurrence" button
   wired to `series=false` — it hits the 400 above. `MyBookingsPage.tsx` has no
   such button at all. The feature is a half-built shell.
3. **The real difficulty is the calendar invite, not the database.** Every row
   in a series shares one `calendar_uid` and one RRULE
   (`booking-api/app/crud/booking.py:66-71`), and the ICS is re-rendered at send
   time from `booking.rrule` (`booking-api/app/services/notifications.py:326-334`).
   Simply removing the 400 would emit `UID + RRULE + STATUS:CANCELLED` with no
   `RECURRENCE-ID` — **Outlook reads that as cancelling the entire series**. The
   DB would lose one occurrence while every attendee's calendar is wiped.

This is the "单场例外 (RECURRENCE-ID)" item recorded as a phase-2 candidate when
the module shipped (TAG 0f7496d, 2026-07-09).

## Decisions

| Question | Decision |
|---|---|
| Series edit after a single cancel | Cancelled occurrence **stays cancelled** (via EXDATE), does not revive |
| Scope | Both My Bookings (organizer) and All Bookings (admin force cancel) |
| Cancelled row in DB | **Kept**, `status='cancelled'` — not deleted |
| Restore a cancelled occurrence | Out of scope — re-book a single meeting instead |

## Approach

Rejected: sending an **EXDATE series update** on every single-occurrence cancel
(`METHOD:REQUEST`, `SEQUENCE+1`, RRULE + EXDATE). It reuses the existing series
path with minimal change, but recipients see "meeting updated" rather than
"Sep 10 cancelled", and every single cancel re-prompts the whole series.

Chosen: **`RECURRENCE-ID` targeted cancel + EXDATE on subsequent series
updates.** The targeted `METHOD:CANCEL` is the standard iCalendar way to cancel
one instance and is exactly what Outlook's own "Cancel Occurrence" emits. The
EXDATE half is not optional — it follows from the "stays cancelled" decision:
without it, a later series update regenerates the cancelled occurrence from the
RRULE.

### The EXDATE anchor problem

EXDATE must match the instant Outlook computes from the **current** RRULE. If
the series later moves 8:30 → 9:00, the EXDATE must be `Sep 10 9:00`, not the
original 8:30, or the occurrence reappears.

Solution, requiring **no schema change**: the series edit **updates the times of
cancelled rows in lockstep** (without reviving their status). A cancelled row's
`starts_at` therefore always equals its current recurrence instant, so
`EXDATE = starts_at` is aligned by construction.

## Design

### 1. Behaviour / UX

The cancel dialog on a recurring booking offers three exits: **Keep**,
**This occurrence**, **Cancel series** (existing semantics = all future
occurrences). Non-recurring bookings are unchanged: Keep / Cancel only.

The two pages differ only in wording — My Bookings is an organizer cancelling
their own meeting, All Bookings is an admin force-cancelling someone else's. The
audit log already distinguishes these via `actor_id != organizer_id`
(`force_cancel` vs `cancel`); no change needed.

Dialog copy must state the consequence — "this occurrence" and "the series"
differ by an order of magnitude in blast radius. All user-facing strings are
English only.

A cancelled occurrence keeps its row in the list, greyed, with a Cancelled
badge. Its room slot is automatically freed (availability checks only look at
`confirmed` — no change needed).

**Edge case:** cancelling "this occurrence" when it is the only remaining future
one is effectively a series cancel. **No special handling** — it takes the
normal single-occurrence path. The occurrence disappears and an empty series
shell remains, matching Outlook's own behaviour.

### 2. Backend

**API shape unchanged.** Remove the `series_member_use_series_cancel` guard at
`bookings.py:1331-1335` so series members fall through to the single branch.
Response stays `{"cancelled": 1}`. The frontend hooks (`useCancelBooking`,
`useAdminForceCancel`) already pass `series` — only buttons and copy change.

The single branch previously assumed a non-series booking. It must now:

- keep the existing status guard (only `confirmed` is cancellable);
- **add a not-yet-started guard for series members** (`starts_at > now`). This
  currently exists only on the series branch. Without it an admin could cancel a
  meeting that finished yesterday and mail everyone about it.

### 3. iCal

`build_event_ics` (`app/services/ical.py:111-120`) gains two parameters:

- **`recurrence_id: datetime | None`** — when set, add `RECURRENCE-ID` (local
  DISPLAY_TIMEZONE time + TZID, same as DTSTART) and **skip RRULE entirely**.
  These two properties together tell Outlook to cancel the whole series. This is
  the single most dangerous line in the change.
- **`exdates: list[datetime] | None`** — when set, add `EXDATE` (also TZID-
  qualified). **Unlike `recurrence_id`, EXDATE coexists with RRULE.** The two
  rules are opposites and easy to invert; tests pin each separately.

**SEQUENCE:** a single-occurrence CANCEL uses that row's own
`ical_sequence + 1`. Per iCalendar, a RECURRENCE-ID instance counts
independently of the series master, so the row's own value is correct.

**Notification plumbing.** The ICS is not rendered at cancel time — it is
re-rendered at send time from the `NotificationLog`'s booking, taking
`rrule` straight from `booking.rrule`, and every series row carries the same
rrule. So adding parameters to `build_event_ics` is **not sufficient**: the
intent ("this is a single-occurrence exception" / "these dates are excluded")
must travel from the cancel action to the send moment rather than being guessed
at send time. Plan: `enqueue` carries an explicit intent, persisted on
`NotificationLog`, which the send path reads to pass `recurrence_id` / `exdates`
and to suppress the rrule. `recurrence_id` and `exdates` share this one path.

**Open item for the plan:** the exact `NotificationLog` field/shape must be
chosen after reading the physical table — column-by-column, not by convention.
(Prior lesson: mirror models must match the real table, never assume.)

### 4. Series edit — EXDATE linkage

`bookings.py:764-772` currently fetches series rows filtered by
`status == "confirmed"`, and four downstream consumers reuse that one list with
different semantics:

- `:774` — `if not all_series_bookings` → 404 "Series not found"
- `:780` — `[0].organizer_id` for authorization
- `:786-792` — `future_bookings`, empty → `series_fully_started`
- `:795` — `future_bookings[0].room_id` for room resolution

**Naively widening that filter is wrong**: a fully-cancelled series would stop
404-ing, and `:795` could resolve the room from a cancelled row.

Correct approach — fetch **all** rows (no status filter), split in memory:

- **`future_confirmed`** takes over every existing responsibility of
  `future_bookings`: authorization, 404 / `series_fully_started`, room
  resolution, conflict checks, the first-occurrence invite anchor, and the
  returned count. Semantics unchanged.
- **`future_cancelled`** does exactly two things: **times follow the series
  update** (status stays `cancelled`), and it **supplies the EXDATE list**. It
  is explicitly excluded from conflict checks — a cancelled occurrence does not
  occupy the room, so colliding it against real bookings would be wrong.

Times-follow-the-update is the linchpin: it keeps each cancelled row's
`starts_at` equal to its current recurrence instant, so
`EXDATE = future_cancelled[].starts_at` aligns with no extra field.

The series **cancel** (`series=true`) path needs no EXDATE — cancelling
everything makes exclusions moot.

## Testing

TDD: tests first. Three existing files map to the three layers.

**`tests/test_ical.py` — highest value.** Pure functions guarding the two most
dangerous lines. The two assertion groups must mirror each other:

- single-occurrence cancel: `RECURRENCE-ID` present **and `RRULE` absent**
- series update: `RRULE` **and** `EXDATE` both present

The negative assertion ("no RRULE") is the safety line of the whole change — if
it lapses, production wipes every attendee's calendar rather than raising an
error. Also assert `RECURRENCE-ID` and `EXDATE` carry the right TZID and are
DISPLAY_TZ local times, not UTC.

**`tests/test_booking_lifecycle.py`** — single-cancel API behaviour: after
cancelling one occurrence the row is `cancelled` **and its siblings are still
`confirmed`** (positive evidence, not just the target row); cancelling a
started/past occurrence returns 400; the room slot is freed in availability.
Regression: `series=true` still cancels all future occurrences.

**`tests/test_series_edit.py`** — the linkage: after a single cancel, editing
the series updates the cancelled row's times, leaves its status `cancelled`, and
the emitted invite's EXDATE **equals the new time** (this directly verifies the
linchpin above and is the easiest thing to get silently wrong). Plus two tests
guarding what the widened query could break: a nonexistent series still 404s;
`series_fully_started` still fires when every future occurrence was individually
cancelled.

**Test environment:** the host `.env` points at the production DB — booking-api
tests must run in-container or with `DATABASE_URL` explicitly overridden. Exact
command to be confirmed while writing the plan.

**Frontend:** no automated tests — the module has no frontend test
infrastructure and standing one up for three buttons is not worth it. Manual
verification: click "This occurrence" on both My Bookings and All Bookings,
confirm exactly one row greys out, and confirm the Outlook invite drops only
that occurrence.

## Out of scope

- Restoring / un-cancelling a single occurrence
- Editing a single occurrence (time/room/title) — separate RECURRENCE-ID
  exception work
- RSVP write-back, statistics dashboard (other phase-2 candidates)
