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
        # building excluded from LOCATION — floor/area only
        location = str(self.event.get("location"))
        assert location == "Maple Room (3F)"
        assert "Building A" not in location

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

    def test_dtstamp_present_and_utc(self):
        """DTSTAMP must be present and be a UTC datetime (Z suffix in raw bytes)."""
        assert "DTSTAMP" in self.raw
        # iCalendar serialises UTC datetimes with a trailing Z
        import re
        assert re.search(r"DTSTAMP:\d{8}T\d{6}Z", self.raw), (
            "DTSTAMP must be a UTC datetime (YYYYMMDDThhmmssZ)"
        )


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


class TestCancelInviteMultiAttendee:
    """FIX 2 (B2): CANCEL with multiple attendees — both ATTENDEE lines must appear."""

    def setup_method(self):
        booking = _make_booking()
        room = _make_room()
        self.ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=["alice@example.com", "bob@example.com"],
            method="CANCEL",
        )
        self.raw = self.ics.decode()
        self.cal = _parse(self.ics)
        self.event = _first_event(self.cal)

    def test_method_cancel(self):
        assert str(self.cal.get("method")) == "CANCEL"

    def test_status_cancelled(self):
        assert str(self.event.get("status")) == "CANCELLED"

    def test_both_attendees_present(self):
        """Both ATTENDEEs must appear on CANCEL — RFC 5546 §3.2.5."""
        attendees = self.event.get("attendee")
        if not isinstance(attendees, list):
            attendees = [attendees]
        assert len(attendees) == 2

    def test_both_attendee_emails_in_raw(self):
        unfolded = self.raw.replace("\r\n ", "").replace("\r\n\t", "").lower()
        assert "mailto:alice@example.com" in unfolded
        assert "mailto:bob@example.com" in unfolded


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
        """Building is excluded from LOCATION; if only building set, result is just room name."""
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
        # building is no longer included in LOCATION (floor/area only)
        assert location == "West Wing"
        assert "HQ" not in location


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


class TestUntilSeriesStoredAsCount:
    """FIX 1 (B1): an until-based series must produce a RRULE with COUNT=, not UNTIL=.

    This exercises the ical.py builder end-to-end: the rrule string passed in
    (produced by build_rrule_string after FIX 1) always uses COUNT.
    """

    def test_count_based_rrule_no_until(self):
        """RRULE string with COUNT passes through cleanly; no UNTIL in output."""
        booking = _make_booking()
        room = _make_room()
        # Simulate an until-based series that expanded to 5 occurrences:
        # build_rrule_string now always emits COUNT=5, never UNTIL.
        rrule = "FREQ=DAILY;INTERVAL=1;COUNT=5"
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=["alice@example.com"],
            method="REQUEST",
            rrule=rrule,
        )
        raw = ics.decode()
        assert "COUNT=5" in raw
        assert "UNTIL=" not in raw

    def test_truncated_series_count_reflects_actual(self):
        """If advance-window truncated a count=10 series to 3, stored rrule has COUNT=3."""
        booking = _make_booking()
        room = _make_room()
        rrule = "FREQ=WEEKLY;INTERVAL=1;COUNT=3"  # actual=3, not 10
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=["alice@example.com"],
            method="REQUEST",
            rrule=rrule,
        )
        raw = ics.decode()
        assert "COUNT=3" in raw
        assert "UNTIL=" not in raw
        assert "COUNT=10" not in raw


class TestMailtoDoublePrefix:
    """FIX 3 (B3): _ensure_mailto must strip a leading 'mailto:' if already present."""

    def test_bare_email_gets_mailto_prefix(self):
        booking = _make_booking()
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="org@example.com",
            attendee_emails=["att@example.com"],
            method="REQUEST",
        )
        unfolded = ics.decode().replace("\r\n ", "").replace("\r\n\t", "").lower()
        assert "mailto:org@example.com" in unfolded
        assert "mailto:att@example.com" in unfolded

    def test_already_prefixed_email_not_doubled(self):
        """If caller passes 'mailto:foo@bar.com', the output must not have 'mailto:mailto:'."""
        booking = _make_booking()
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="mailto:org@example.com",
            attendee_emails=["mailto:att@example.com"],
            method="REQUEST",
        )
        raw = ics.decode().lower()
        assert "mailto:mailto:" not in raw
        unfolded = raw.replace("\r\n ", "").replace("\r\n\t", "")
        assert "mailto:org@example.com" in unfolded
        assert "mailto:att@example.com" in unfolded


class TestEmptyDescription:
    """FIX 4 (B4): DESCRIPTION must be omitted entirely when booking.description is falsy."""

    def test_none_description_omits_property(self):
        booking = _make_booking(description=None)
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=[],
            method="REQUEST",
        )
        raw = ics.decode()
        assert "DESCRIPTION" not in raw

    def test_empty_string_description_omits_property(self):
        booking = _make_booking(description="")
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=[],
            method="REQUEST",
        )
        raw = ics.decode()
        assert "DESCRIPTION" not in raw

    def test_non_empty_description_is_present(self):
        booking = _make_booking(description="Quarterly review")
        room = _make_room()
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=[],
            method="REQUEST",
        )
        raw = ics.decode()
        assert "DESCRIPTION" in raw
        assert "Quarterly review" in raw


class TestLocationFloorAreaOnly:
    """Change 2: LOCATION must use floor/area only — building is dropped."""

    def test_location_uses_floor_and_area_not_building(self):
        """When building, floor and area all set, only floor+area appear in LOCATION."""
        booking = _make_booking()
        room = _make_room(name="Maple Room", building="HQ", floor="3F", area="East Wing")
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
        assert "HQ" not in location, f"building should be omitted from LOCATION: {location!r}"
        assert "3F" in location
        assert "East Wing" in location
        assert location == "Maple Room (3F, East Wing)"

    def test_location_floor_only(self):
        """Only floor set — no area, no building."""
        booking = _make_booking()
        room = _make_room(name="Room B", building="Tower", floor="5", area=None)
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
        assert "Tower" not in location
        assert location == "Room B (5)"


class TestNonAsciiAndUtf8:
    """FIX 5 (B5): non-ASCII title and room name must round-trip correctly."""

    def test_non_ascii_title_round_trips(self):
        """Title with Chinese + accented characters must parse back equal to input."""
        title = "会议室 Café"
        booking = _make_booking(title=title)
        room = _make_room(name="Salle Réunion", building=None, floor=None, area=None)
        ics = build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=[],
            method="REQUEST",
        )
        cal = _parse(ics)
        event = _first_event(cal)
        assert str(event.get("summary")) == title

    def test_non_ascii_location_round_trips(self):
        """Room name with non-ASCII chars must survive ical serialisation/parse.

        Building is excluded from LOCATION (floor/area only); room.name must appear.
        """
        room = _make_room(name="Salle Réunion", building="Bâtiment B", floor="2", area=None)
        booking = _make_booking()
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
        assert "Salle Réunion" in location
        # building excluded; floor present
        assert "Bâtiment B" not in location
        assert "2" in location


class TestVtimezoneRruleBased:
    """FIX 6 (B6): VTIMEZONE must use RRULE-based observances for Eastern zones.

    Outlook Classic Desktop ignores RDATE year-lists (emitted by from_tzid())
    and falls back to the first STANDARD offset (-0500 EST), causing EDT
    meetings (UTC-4 summer) to display 1 hour late (09:00 EDT → shown 10:00).
    The fix hand-builds RRULE-based STANDARD/DAYLIGHT blocks.
    """

    def _build_ics(self):
        # UTC 13:00 on a summer date → 09:00 EDT (UTC-4)
        booking = _make_booking(
            starts_at=datetime(2026, 7, 8, 13, 0, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc),
        )
        room = _make_room()
        return build_event_ics(
            booking=booking,
            room=room,
            organizer_email="organizer@example.com",
            attendee_emails=["alice@example.com"],
            method="REQUEST",
        )

    def test_vtimezone_rrule_standard_november(self):
        """VTIMEZONE STANDARD block must use RRULE with BYMONTH=11;BYDAY=1SU."""
        raw = self._build_ics().decode()
        # Line-unfold before searching (RRULE may be folded across lines)
        unfolded = raw.replace("\r\n ", "").replace("\r\n\t", "")
        assert "RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11" in unfolded or \
               "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU" in unfolded, (
            "VTIMEZONE STANDARD block missing expected RRULE"
        )

    def test_vtimezone_rrule_daylight_march(self):
        """VTIMEZONE DAYLIGHT block must use RRULE with BYMONTH=3;BYDAY=2SU."""
        raw = self._build_ics().decode()
        unfolded = raw.replace("\r\n ", "").replace("\r\n\t", "")
        assert "RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3" in unfolded or \
               "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU" in unfolded, (
            "VTIMEZONE DAYLIGHT block missing expected RRULE"
        )

    def test_vtimezone_no_rdate(self):
        """VTIMEZONE must NOT contain RDATE lines (Outlook ignores them)."""
        raw = self._build_ics().decode()
        assert "RDATE" not in raw, "VTIMEZONE must not use RDATE observances"

    def test_vtimezone_tzid_matches_dtstart_tzid(self):
        """VTIMEZONE TZID must be identical to the TZID param on DTSTART."""
        raw = self._build_ics().decode()
        unfolded = raw.replace("\r\n ", "").replace("\r\n\t", "")
        # Extract VTIMEZONE TZID
        import re
        tz_match = re.search(r"BEGIN:VTIMEZONE\r?\n(.*?)END:VTIMEZONE", raw, re.DOTALL)
        assert tz_match, "No VTIMEZONE block found"
        vtimezone_block = tz_match.group(1)
        tzid_match = re.search(r"TZID:(.+)", vtimezone_block)
        assert tzid_match, "No TZID in VTIMEZONE"
        vtimezone_tzid = tzid_match.group(1).strip()
        # Extract DTSTART TZID param
        dtstart_match = re.search(r"DTSTART;TZID=([^:]+):", unfolded)
        assert dtstart_match, "No DTSTART;TZID= found"
        dtstart_tzid = dtstart_match.group(1).strip()
        assert vtimezone_tzid == dtstart_tzid, (
            f"VTIMEZONE TZID {vtimezone_tzid!r} != DTSTART TZID param {dtstart_tzid!r}"
        )

    def test_dtstart_local_time_summer_09h(self):
        """UTC 13:00 on a summer date must render as 09:00 local (EDT, UTC-4)."""
        raw = self._build_ics().decode()
        unfolded = raw.replace("\r\n ", "").replace("\r\n\t", "")
        assert "DTSTART;TZID=America/Toronto:20260708T090000" in unfolded, (
            f"Expected 09:00 EDT in DTSTART. Raw (unfolded):\n{unfolded}"
        )
