"""iCalendar / iMIP invite builder.

Produces RFC 5545 VCALENDAR bytes suitable for attaching to booking notification
emails (METHOD:REQUEST for new/update, METHOD:CANCEL for cancellations).

Outlook Classic requires:
  - DTSTART;TZID=<iana>:<localtime>  (NOT UTC Z suffix)
  - A VTIMEZONE component so the client can resolve the TZID
  - SEQUENCE monotonically increasing on updates
  - ORGANIZER + ATTENDEE lines with correct RSVP params

VTIMEZONE / Outlook RDATE limitation
-------------------------------------
icalendar.Timezone.from_tzid() generates VTIMEZONE observances using RDATE
year-lists (one per historical transition year).  Outlook Classic Desktop does
NOT process RDATE-based observances — it falls back to the first STANDARD
offset only, causing EDT meetings to display 1 hour late (e.g. 09:00 EDT →
shown as 10:00).

For America/Toronto (and its common aliases America/New_York, America/Montreal)
we therefore hand-build a RRULE-based VTIMEZONE that Outlook understands.
For any other DISPLAY_TIMEZONE we fall back to from_tzid() since we cannot
guarantee the hand-built rules are correct for arbitrary zones.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone, TimezoneStandard, TimezoneDaylight, vCalAddress, vRecur, vText, vUTCOffset

from app.core.config import settings
from app.models.booking import Booking
from app.models.room import MeetingRoom

# Timezone IDs for which we emit a hand-built RRULE-based VTIMEZONE.
# Outlook Classic ignores RDATE observances (from_tzid default) and falls back
# to the first STANDARD offset, shifting EDT meetings by +1 hour.
_EASTERN_TZIDS = frozenset({
    "America/Toronto",
    "America/New_York",
    "America/Montreal",
})


def _build_vtimezone(tzid: str) -> Timezone:
    """Return a VTIMEZONE component for *tzid*.

    For Eastern time zones (America/Toronto, America/New_York, America/Montreal)
    we hand-build RRULE-based STANDARD/DAYLIGHT observances.  Outlook Classic
    Desktop does not process RDATE year-lists emitted by from_tzid(), so we
    must use RRULE or Outlook falls back to the first STANDARD offset only.

    For all other zones, fall back to icalendar's from_tzid() (RDATE-based).
    Outlook may display those incorrectly if they have DST, but we cannot
    guarantee correctness for arbitrary zones without hand-crafted rules.
    """
    if tzid not in _EASTERN_TZIDS:
        # Non-Eastern zone: use the library default (RDATE observances).
        # Outlook Classic may misbehave for DST zones, but we have no reliable
        # hand-built fallback for the full tz database.
        return Timezone.from_tzid(tzid)

    # Hand-built RRULE-based Eastern Time VTIMEZONE.
    # Post-2007 US/Canada DST rules (Energy Policy Act 2005):
    #   STANDARD: first Sunday in November at 02:00 (clocks fall back to EST -0500)
    #   DAYLIGHT: second Sunday in March at 02:00 (clocks spring forward to EDT -0400)
    vtimezone = Timezone()
    vtimezone.add("TZID", tzid)

    standard = TimezoneStandard()
    standard.add("DTSTART", datetime(2007, 11, 4, 2, 0, 0))
    standard.add("RRULE", vRecur.from_ical("FREQ=YEARLY;BYMONTH=11;BYDAY=1SU"))
    standard.add("TZOFFSETFROM", vUTCOffset(timedelta(hours=-4)))
    standard.add("TZOFFSETTO", vUTCOffset(timedelta(hours=-5)))
    standard.add("TZNAME", "EST")
    vtimezone.add_component(standard)

    daylight = TimezoneDaylight()
    daylight.add("DTSTART", datetime(2007, 3, 11, 2, 0, 0))
    daylight.add("RRULE", vRecur.from_ical("FREQ=YEARLY;BYMONTH=3;BYDAY=2SU"))
    daylight.add("TZOFFSETFROM", vUTCOffset(timedelta(hours=-5)))
    daylight.add("TZOFFSETTO", vUTCOffset(timedelta(hours=-4)))
    daylight.add("TZNAME", "EDT")
    vtimezone.add_component(daylight)

    return vtimezone


def _ensure_mailto(address: str) -> str:
    """Return a mailto: URI, stripping a leading 'mailto:' if already present."""
    stripped = address[len("mailto:"):] if address.lower().startswith("mailto:") else address
    return f"mailto:{stripped}"


def _location_string(room: MeetingRoom) -> str:
    """Build the LOCATION value from room fields.

    Format: "<name> (<floor>, <area>)"
    Building is intentionally excluded so calendar invites match the UI
    (which displays floor/area only).
    If neither floor nor area is set, just "<name>".
    """
    parts = [p for p in [room.floor, room.area] if p]
    if parts:
        return f"{room.name} ({', '.join(parts)})"
    return room.name


def build_event_ics(
    *,
    booking: Booking,
    room: MeetingRoom,
    organizer_email: str,
    attendee_emails: list[str],
    method: Literal["REQUEST", "CANCEL"],
    rrule: str | None = None,
    organizer_cn: str | None = None,
    recurrence_id: datetime | None = None,
    exdates: list[datetime] | None = None,
) -> bytes:
    """Build a complete VCALENDAR iMIP payload as bytes.

    Args:
        booking:         ORM Booking instance (provides UID, SEQUENCE, times,
                         title, description).
        room:            ORM MeetingRoom instance (provides LOCATION parts).
        organizer_email: Organizer's email address for the ORGANIZER property.
        attendee_emails: List of attendee email addresses (each becomes an
                         ATTENDEE with ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;
                         RSVP=TRUE).
        method:          "REQUEST" (new/update) or "CANCEL".
        rrule:           Optional RRULE value string, e.g.
                         "FREQ=WEEKLY;INTERVAL=1;COUNT=4".  When provided a
                         RRULE property is added to the VEVENT.  Series invites
                         should be built from the FIRST occurrence booking + rrule.
        organizer_cn:    Optional display name for the ORGANIZER CN param.
                         Falls back to organizer_email if not provided.
        recurrence_id:   When set, this VEVENT is a single-instance exception of
                         a series: RECURRENCE-ID is emitted and RRULE is
                         suppressed.  Pass the occurrence's scheduled start.
                         RRULE + RECURRENCE-ID together instruct Outlook to act
                         on the WHOLE series — never emit both.
        exdates:         Occurrence start times to exclude from the RRULE
                         expansion (occurrences cancelled individually).  Unlike
                         recurrence_id this COEXISTS with rrule.  Ignored unless
                         the RRULE is actually emitted (rrule is not None AND
                         recurrence_id is None) — there is nothing to subtract
                         from otherwise.

    Returns:
        UTF-8 encoded iCalendar bytes.
    """
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)

    # ── Convert stored UTC datetimes → display timezone ───────────────────────
    starts_local = booking.starts_at.astimezone(tz)
    ends_local = booking.ends_at.astimezone(tz)

    # ── Calendar envelope ─────────────────────────────────────────────────────
    cal = Calendar()
    cal.add("prodid", "-//UniOps//Booking//EN")
    cal.add("version", "2.0")
    cal.add("method", method)

    # ── VTIMEZONE (required by Outlook Classic for TZID resolution) ───────────
    # Use RRULE-based observances for Eastern zones; RDATE fallback otherwise.
    # See module docstring for the Outlook RDATE limitation.
    vtimezone = _build_vtimezone(settings.DISPLAY_TIMEZONE)
    cal.add_component(vtimezone)

    # ── VEVENT ────────────────────────────────────────────────────────────────
    event = Event()

    event.add("uid", booking.calendar_uid)
    event.add("sequence", booking.ical_sequence)
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("dtstart", starts_local)
    event.add("dtend", ends_local)
    event.add("summary", booking.title)
    event.add("location", _location_string(room))
    if booking.description:
        event.add("description", booking.description)

    # ORGANIZER with optional CN display name
    organizer = vCalAddress(_ensure_mailto(organizer_email))
    organizer.params["CN"] = vText(organizer_cn or organizer_email)
    event.add("organizer", organizer)

    # One ATTENDEE per email
    for email in attendee_emails:
        attendee = vCalAddress(_ensure_mailto(email))
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
        attendee.params["RSVP"] = vText("TRUE")
        event.add("attendee", attendee, encode=False)

    # STATUS:CANCELLED for cancel invites
    if method == "CANCEL":
        event.add("status", "CANCELLED")

    # RECURRENCE-ID marks this VEVENT as ONE instance of the series identified
    # by UID. Emitted in DISPLAY_TIMEZONE local time; icalendar adds the TZID.
    if recurrence_id is not None:
        event.add("recurrence-id", recurrence_id.astimezone(tz))

    # RRULE for series invites — deliberately suppressed for single-instance
    # exceptions. RRULE alongside RECURRENCE-ID makes Outlook apply the action
    # to the entire series, i.e. wipe every attendee's calendar.
    emits_rrule = rrule is not None and recurrence_id is None
    if emits_rrule:
        event.add("rrule", vRecur.from_ical(rrule))

    # EXDATE subtracts individually-cancelled occurrences from the RRULE
    # expansion, so it only means something alongside the RRULE it subtracts
    # from. Passing a list yields one comma-separated EXDATE property;
    # icalendar adds the TZID.
    if exdates and emits_rrule:
        event.add("exdate", [ex.astimezone(tz) for ex in exdates])

    cal.add_component(event)

    return cal.to_ical()
