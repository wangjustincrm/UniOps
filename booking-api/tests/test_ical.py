"""Tests for the iCalendar invite builder (app/services/ical.py).

These are pure unit tests — no DB, no HTTP, no fixtures from conftest.
Booking and MeetingRoom instances are constructed in-memory via object.__setattr__
to avoid triggering SQLAlchemy mapper machinery while still exercising the
builder against realistic model data.

Assertions cover:
  1. REQUEST invite: METHOD, UID, SEQUENCE, DTSTART with TZID (Outlook Classic
     compatibility), ORGANIZER, 2 ATTENDEEs with RSVP=TRUE, LOCATION.
  2. CANCEL invite: METHOD:CANCEL + STATUS:CANCELLED + same UID.
  3. Series invite: RRULE with FREQ=WEEKLY;COUNT=4 present.
  4. SEQUENCE reflects ical_sequence=2.
  5. Empty location parts → LOCATION = room.name only (no empty parens).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from icalendar import Calendar

from app.services.ical import build_event_ics


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_booking(
    *,
    calendar_uid: str = "test-uid-001@uniops",
    ical_sequence: int = 0,
    title: str = "Team Standup",
    description: str | None = "Daily sync",
    # 2026-07-08 18:00 UTC == 14:00 EDT (UTC-4 summer)
    starts_at: datetime = datetime(2026, 7, 8, 18, 0, 0, tzinfo=timezone.utc),
    ends_at: datetime = datetime(2026, 7, 8, 19, 0, 0, tzinfo=timezone.utc),
    status: str = "confirmed",
    rrule: str | None = None,
):
    """Return a Booking-like object without hitting SQLAlchemy.

    We use a plain namespace instead of the ORM class to avoid the mapper
    requiring a session or DB setup for attribute access.
    """
    import types
    b = types.SimpleNamespace()
    b.id = uuid.uuid4()
    b.calendar_uid = calendar_uid
    b.ical_sequence = ical_sequence
    b.title = title
    b.description = description
    b.starts_at = starts_at
    b.ends_at = ends_at
    b.status = status
    b.rrule = rrule
    return b


def _make_room(
    *,
    name: str = "Maple Room",
    building: str | None = "Building A",
    floor: str | None = "3F",
    area: str | None = None,
    code: str = "MAPLE-3F",
):
    import types
    r = types.SimpleNamespace()
    r.id = uuid.uuid4()
    r.name = name
    r.code = code
    r.building = building
    r.floor = floor
    r.area = area
    return r


def _parse(ics_bytes: bytes) -> Calendar:
    return Calendar.from_ical(ics_bytes)


def _first_event(cal: Calendar):
    from icalendar import Event
    for component in cal.walk():
        if isinstance(component, Event):
            return component
    raise AssertionError("No VEVENT found in calendar")


# ── Tests ────────────────────────────────────────────────────────────────────

class TestRequestInvite:
    """Test 1: Standard REQUEST invite."""

    def setup_method(self):
        booking = _make_booking()
        room = _make_room()
        self.ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=["alice@example.com", "bob@example.com"],
            method="REQUEST",
            organizer_cn="Meeting Organizer",
        )
        self.raw = self.ics.decode()
        self.cal = _parse(self.ics)
        self.event = _first_event(self.cal)

    def test_method_request(self):
        assert str(self.cal.get("method")) == "REQUEST"

    def test_prodid(self):
        assert "-//UniOps//Booking//EN" in str(self.cal.get("prodid"))

    def test_version(self):
        assert str(self.cal.get("version")) == "2.0"

    def test_uid_matches(self):
        assert str(self.event.get("uid")) == "test-uid-001@uniops"

    def test_sequence_zero(self):
        assert int(self.event.get("sequence")) == 0

    def test_dtstart_tzid_in_raw_bytes(self):
        """Outlook Classic requires DTSTART;TZID=America/Toronto:<localtime>."""
        assert "DTSTART;TZID=America/Toronto:20260708T140000" in self.raw

    def test_dtstart_local_time_correct(self):
        """UTC 18:00 should render as 14:00 EDT (UTC-4 in summer)."""
        dtstart = self.event.decoded("dtstart")
        from zoneinfo import ZoneInfo
        local = dtstart.astimezone(ZoneInfo("America/Toronto"))
        assert local.hour == 14
        assert local.minute == 0

    def test_organizer_mailto(self):
        organizer = str(self.event.get("organizer"))
        assert "mailto:organizer@example.com" in organizer.lower()

    def test_two_attendees(self):
        attendees = self.event.get("attendee")
        # icalendar returns a list when multiple ATTENDEEs exist
        if not isinstance(attendees, list):
            attendees = [attendees]
        assert len(attendees) == 2

    def test_attendees_rsvp_true(self):
        """Each ATTENDEE must carry RSVP=TRUE."""
        attendees = self.event.get("attendee")
        if not isinstance(attendees, list):
            attendees = [attendees]
        for att in attendees:
            assert att.params.get("RSVP", "").upper() == "TRUE", (
                f"RSVP missing/wrong on attendee: {att}"
            )

    def test_attendees_emails_present(self):
        # icalendar line-folds at 75 chars; unfold before searching.
        # RFC 5545: CRLF + single WSP = fold; strip to get logical lines.
        unfolded = self.raw.replace("\r\n ", "").replace("\r\n\t", "").lower()
        assert "mailto:alice@example.com" in unfolded
        assert "mailto:bob@example.com" in unfolded

    def test_location(self):
        location = str(self.event.get("location"))
        assert location == "Maple Room (Building A, 3F)"

    def test_summary(self):
        assert str(self.event.get("summary")) == "Team Standup"

    def test_vtimezone_present(self):
        """A VTIMEZONE component must be emitted for Outlook Classic."""
        from icalendar import Timezone
        found = any(isinstance(c, Timezone) for c in self.cal.walk())
        assert found, "No VTIMEZONE component in output"

    def test_no_status_cancelled(self):
        """REQUEST invite must not carry STATUS:CANCELLED."""
        assert "STATUS:CANCELLED" not in self.raw


class TestCancelInvite:
    """Test 2: CANCEL invite."""

    def setup_method(self):
        booking = _make_booking()
        room = _make_room()
        self.ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=["alice@example.com"],
            method="CANCEL",
        )
        self.raw = self.ics.decode()
        self.cal = _parse(self.ics)
        self.event = _first_event(self.cal)

    def test_method_cancel(self):
        assert str(self.cal.get("method")) == "CANCEL"

    def test_status_cancelled(self):
        assert "STATUS:CANCELLED" in self.raw

    def test_same_uid(self):
        assert str(self.event.get("uid")) == "test-uid-001@uniops"

    def test_status_field_parsed(self):
        assert str(self.event.get("status")) == "CANCELLED"


class TestSeriesInvite:
    """Test 3: Series invite with RRULE."""

    def setup_method(self):
        booking = _make_booking()
        room = _make_room()
        self.ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=["alice@example.com"],
            method="REQUEST",
            rrule="FREQ=WEEKLY;INTERVAL=1;COUNT=4",
        )
        self.raw = self.ics.decode()
        self.cal = _parse(self.ics)
        self.event = _first_event(self.cal)

    def test_rrule_in_raw(self):
        """RRULE line must be present in raw bytes."""
        assert "RRULE:" in self.raw

    def test_rrule_freq_weekly(self):
        """Parsed RRULE must have FREQ=WEEKLY."""
        rrule = self.event.get("rrule")
        assert rrule is not None
        assert "WEEKLY" in str(rrule.get("FREQ", []))

    def test_rrule_count_4(self):
        """Parsed RRULE must have COUNT=4."""
        rrule = self.event.get("rrule")
        assert rrule is not None
        count_vals = rrule.get("COUNT", [])
        assert 4 in count_vals

    def test_rrule_raw_contains_count(self):
        assert "COUNT=4" in self.raw


class TestSequenceNumber:
    """Test 4: SEQUENCE reflects ical_sequence field."""

    def test_sequence_two(self):
        booking = _make_booking(ical_sequence=2)
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=[],
            method="REQUEST",
        )
        cal = _parse(ics)
        event = _first_event(cal)
        assert int(event.get("sequence")) == 2


class TestEmptyLocationParts:
    """Test 5: Room with no building/floor/area → LOCATION = room.name only."""

    def test_location_no_parens(self):
        booking = _make_booking()
        room = _make_room(name="Boardroom", building=None, floor=None, area=None)
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=[],
            method="REQUEST",
        )
        cal = _parse(ics)
        event = _first_event(cal)
        location = str(event.get("location"))
        assert location == "Boardroom"
        assert "(" not in location

    def test_location_partial_parts(self):
        """If only some parts are set, only those appear."""
        booking = _make_booking()
        room = _make_room(name="West Wing", building="HQ", floor=None, area=None)
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=[],
            method="REQUEST",
        )
        cal = _parse(ics)
        event = _first_event(cal)
        location = str(event.get("location"))
        assert location == "West Wing (HQ)"


class TestOrganizerCn:
    """ORGANIZER CN param falls back to email when organizer_cn is None."""

    def test_cn_from_param(self):
        booking = _make_booking()
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="mgr@example.com",
            attendee_emails=[],
            method="REQUEST",
            organizer_cn="Jane Manager",
        )
        raw = ics.decode()
        assert "Jane Manager" in raw

    def test_cn_fallback_to_email(self):
        booking = _make_booking()
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="mgr@example.com",
            attendee_emails=[],
            method="REQUEST",
            organizer_cn=None,
        )
        raw = ics.decode()
        # CN should be set to the email address itself
        assert "mgr@example.com" in raw
