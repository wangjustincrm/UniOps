"""iCalendar / iMIP invite builder.

Produces RFC 5545 VCALENDAR bytes suitable for attaching to booking notification
emails (METHOD:REQUEST for new/update, METHOD:CANCEL for cancellations).

Outlook Classic requires:
  - DTSTART;TZID=<iana>:<localtime>  (NOT UTC Z suffix)
  - A VTIMEZONE component so the client can resolve the TZID
  - SEQUENCE monotonically increasing on updates
  - ORGANIZER + ATTENDEE lines with correct RSVP params
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone, vCalAddress, vRecur, vText

from app.core.config import settings
from app.models.booking import Booking
from app.models.room import MeetingRoom


def _location_string(room: MeetingRoom) -> str:
    """Build the LOCATION value from room fields.

    Format: "<name> (<building>, <floor>, <area>)"
    If all three location-detail fields are empty/None, just "<name>".
    """
    parts = [p for p in [room.building, room.floor, room.area] if p]
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
    vtimezone = Timezone.from_tzid(settings.DISPLAY_TIMEZONE)
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
    event.add("description", booking.description or "")

    # ORGANIZER with optional CN display name
    organizer = vCalAddress(f"mailto:{organizer_email}")
    organizer.params["CN"] = vText(organizer_cn or organizer_email)
    event.add("organizer", organizer)

    # One ATTENDEE per email
    for email in attendee_emails:
        attendee = vCalAddress(f"mailto:{email}")
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
        attendee.params["RSVP"] = vText("TRUE")
        event.add("attendee", attendee, encode=False)

    # STATUS:CANCELLED for cancel invites
    if method == "CANCEL":
        event.add("status", "CANCELLED")

    # RRULE for series invites
    if rrule is not None:
        event.add("rrule", vRecur.from_ical(rrule))

    cal.add_component(event)

    return cal.to_ical()
