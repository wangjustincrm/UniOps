# Booking Single-Occurrence Cancel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an organizer or admin cancel one occurrence of a recurring booking without destroying the series — in the database *and* on every attendee's Outlook calendar.

**Architecture:** The cancelled occurrence's invite is a targeted `METHOD:CANCEL` carrying `RECURRENCE-ID` and deliberately **no** `RRULE`. Later series updates carry `EXDATE` so Outlook does not regenerate it. Cancelled rows keep their times in lockstep with series edits, which makes `EXDATE = starts_at` correct by construction — no schema change, no migration.

**Tech Stack:** FastAPI + SQLAlchemy 2 (async) + `icalendar`, pytest/pytest-asyncio, React + TypeScript (Vite).

**Spec:** `docs/superpowers/specs/2026-07-16-booking-single-occurrence-cancel-design.md`

## Global Constraints

- **Branch/worktree:** `feature/booking-single-occurrence-cancel`, worktree `.worktrees/booking-single-cancel` (already created off `main` @ `bdf6be8`).
- **All user-facing frontend strings are English only.** Chinese is allowed in code comments.
- **Test command (backend):** `docker exec uniops_booking_api python -m pytest tests/ -q`
- **Never run booking-api pytest on the host.** `settings.DATABASE_URL` is a **`@property` computed from `POSTGRES_*`** (`app/core/config.py:44`) — setting a `DATABASE_URL` env var does **nothing**. `conftest.py:34` derives the test DB by swapping the database name, so a wrong `POSTGRES_HOST` points the test suite at another server. Inside `uniops_booking_api`, `POSTGRES_HOST=postgres` and the `booking_test` database already exists.
- **Green baseline: `310 passed`** (verified 2026-07-16, before any change). Every task compares against this number — the suite must never drop below `310 + <tests you added>`.
- `notif_type` is **`String(16)`** (`alembic/versions/20260707_0001_booking_initial.py:99`). The new value `cancelled_occ` is 13 chars. Longer values raise a DB error.
- No new alembic migration is required by this plan. If you think you need one, re-read the spec — you have taken a wrong turn.

---

## Design Decisions Resolved During Planning

These were open in the spec and are now settled — do not re-litigate them:

1. **Intent is persisted as a new `notif_type` value, `cancelled_occ`** — not a new column. `NotificationLog` (`app/models/notification.py`) has no field for ICS context, and `notif_type` already *is* "what kind of notification is this", persisted, and read by the send path.
2. **Intent must be persisted, not passed as an argument.** The scheduler retries failed sends by re-rendering the ICS from the stored `NotificationLog` (`app/services/scheduler.py:65`). A transient kwarg would be lost on retry and the retry would mail a whole-series cancellation.
3. **`enqueue(..., rrule=...)` is dead.** The body (`notifications.py:414-490`) never reads it; the ICS uses `booking.rrule` (`:332`). Task 7 removes it.
4. **Do not infer "single occurrence" from the data at send time.** The tempting rule "series member + other confirmed rows exist" is **wrong**: a series cancel only cancels *future* occurrences, so past confirmed rows survive and a series cancel would be misread as a single-occurrence cancel — wiping one instance instead of the series.
5. **EXDATE is derived at send time by querying cancelled future rows**, not passed from the endpoint. Unlike intent, this is a fact in the database, and re-deriving it makes retries self-healing.
6. **`icalendar` emits TZID automatically** for tz-aware datetimes; `event.add("exdate", [d1, d2])` yields one comma-separated `EXDATE;TZID=America/Toronto:...`. Verified in-container 2026-07-16.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `booking-api/app/services/ical.py` | VCALENDAR bytes | Modify: `+recurrence_id`, `+exdates` params |
| `booking-api/app/services/notifications.py` | recipients, log, send | Modify: `cancelled_occ` handling, EXDATE derivation |
| `booking-api/app/api/v1/bookings.py` | cancel + series edit | Modify: `:1328-1364` cancel, `:764-901` series edit |
| `booking/src/pages/MyBookingsPage.tsx` | organizer cancel dialog | Modify: add "This occurrence" |
| `booking/src/pages/admin/AllBookingsPage.tsx` | admin force-cancel dialog | Modify: copy only (button exists) |
| `booking/src/pages/admin/NotificationsPage.tsx` | notification log list | Modify: label for `cancelled_occ` |
| `booking-api/tests/test_ical.py` | ICS unit tests | Modify: +4 tests |
| `booking-api/tests/test_booking_lifecycle.py` | cancel API tests | Modify: +4 tests |
| `booking-api/tests/test_series_edit.py` | series edit tests | Modify: +4 tests |

---

### Task 1: iCal — `recurrence_id` (RECURRENCE-ID, and never RRULE)

This is the safety-critical task. If `RRULE` survives alongside `RECURRENCE-ID`, Outlook deletes every attendee's whole series instead of one meeting.

**Files:**
- Modify: `booking-api/app/services/ical.py:111-196`
- Test: `booking-api/tests/test_ical.py`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces: `build_event_ics(*, booking, room, organizer_email, attendee_emails, method, rrule=None, organizer_cn=None, recurrence_id: datetime | None = None, exdates: list[datetime] | None = None) -> bytes`. Task 2 adds `exdates`; Task 3 calls both.

- [ ] **Step 1: Write the failing tests**

Append to `booking-api/tests/test_ical.py`:

```python
def test_single_occurrence_cancel_has_recurrence_id_and_no_rrule():
    """A single-occurrence CANCEL must identify ONE instance via RECURRENCE-ID
    and must NOT carry RRULE.

    RRULE + RECURRENCE-ID together tell Outlook to cancel the entire series.
    The absent-RRULE assertion is the safety line of this whole feature: if it
    lapses, production wipes every attendee's calendar rather than raising.
    """
    booking = _make_booking(rrule="FREQ=WEEKLY;INTERVAL=1;COUNT=4")
    room = _make_room()

    raw = build_event_ics(
        booking=booking,
        room=room,
        organizer_email="organizer@example.com",
        attendee_emails=["a@example.com"],
        method="CANCEL",
        rrule=booking.rrule,
        recurrence_id=booking.starts_at,
    ).decode()

    assert "RECURRENCE-ID;TZID=America/Toronto:20260708T140000" in raw
    assert "RRULE" not in raw
    assert "METHOD:CANCEL" in raw
    assert "STATUS:CANCELLED" in raw


def test_recurrence_id_absent_keeps_rrule():
    """Regression guard: without recurrence_id a series invite still gets RRULE."""
    booking = _make_booking(rrule="FREQ=WEEKLY;INTERVAL=1;COUNT=4")
    raw = build_event_ics(
        booking=booking,
        room=_make_room(),
        organizer_email="organizer@example.com",
        attendee_emails=["a@example.com"],
        method="REQUEST",
        rrule=booking.rrule,
    ).decode()

    assert "RRULE:FREQ=WEEKLY" in raw
    assert "RECURRENCE-ID" not in raw
```

Note: `_make_booking` defaults to `2026-07-08 18:00 UTC` = `14:00` EDT, hence `20260708T140000`.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_ical.py -q -k "recurrence_id"
```
Expected: FAIL — `TypeError: build_event_ics() got an unexpected keyword argument 'recurrence_id'`.

- [ ] **Step 3: Add the parameter**

In `booking-api/app/services/ical.py`, extend the signature (currently ending `organizer_cn: str | None = None,` at `:119`):

```python
    organizer_cn: str | None = None,
    recurrence_id: datetime | None = None,
) -> bytes:
```

Add to the docstring's Args block, after `organizer_cn`:

```
        recurrence_id:   When set, this VEVENT is a single-instance exception of
                         a series: RECURRENCE-ID is emitted and RRULE is
                         suppressed.  Pass the occurrence's scheduled start.
                         RRULE + RECURRENCE-ID together instruct Outlook to act
                         on the WHOLE series — never emit both.
```

- [ ] **Step 4: Emit RECURRENCE-ID and suppress RRULE**

Replace `ical.py:190-192`:

```python
    # RRULE for series invites
    if rrule is not None:
        event.add("rrule", vRecur.from_ical(rrule))
```

with:

```python
    # RECURRENCE-ID marks this VEVENT as ONE instance of the series identified
    # by UID. Emitted in DISPLAY_TIMEZONE local time; icalendar adds the TZID.
    if recurrence_id is not None:
        event.add("recurrence-id", recurrence_id.astimezone(tz))

    # RRULE for series invites — deliberately suppressed for single-instance
    # exceptions. RRULE alongside RECURRENCE-ID makes Outlook apply the action
    # to the entire series, i.e. wipe every attendee's calendar.
    if rrule is not None and recurrence_id is None:
        event.add("rrule", vRecur.from_ical(rrule))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_ical.py -q
```
Expected: PASS, and the file's pre-existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add booking-api/app/services/ical.py booking-api/tests/test_ical.py
git commit -m "feat(booking): ical RECURRENCE-ID for single-occurrence exceptions

RECURRENCE-ID identifies one instance of a series. RRULE is suppressed
whenever it is set — the two together tell Outlook to act on the whole
series, which would wipe every attendee's calendar."
```

---

### Task 2: iCal — `exdates` (EXDATE, which *does* coexist with RRULE)

The mirror image of Task 1, and easy to invert by mistake: `RECURRENCE-ID` excludes `RRULE`; `EXDATE` requires it.

**Files:**
- Modify: `booking-api/app/services/ical.py`
- Test: `booking-api/tests/test_ical.py`

**Interfaces:**
- Consumes: `build_event_ics` from Task 1.
- Produces: `exdates: list[datetime] | None` parameter, consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

Append to `booking-api/tests/test_ical.py`:

```python
def test_series_invite_with_exdates_keeps_rrule():
    """EXDATE removes individually-cancelled occurrences from the RRULE
    expansion. Unlike RECURRENCE-ID it MUST coexist with RRULE — without the
    RRULE there is nothing to subtract from.
    """
    booking = _make_booking(rrule="FREQ=WEEKLY;INTERVAL=1;COUNT=4")
    # 2026-07-15 18:00 UTC == 14:00 EDT — the second occurrence.
    excluded = datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc)

    raw = build_event_ics(
        booking=booking,
        room=_make_room(),
        organizer_email="organizer@example.com",
        attendee_emails=["a@example.com"],
        method="REQUEST",
        rrule=booking.rrule,
        exdates=[excluded],
    ).decode()

    assert "RRULE:FREQ=WEEKLY" in raw
    assert "EXDATE;TZID=America/Toronto:20260715T140000" in raw


def test_multiple_exdates_share_one_property():
    """Several exclusions collapse into one comma-separated EXDATE line."""
    booking = _make_booking(rrule="FREQ=WEEKLY;INTERVAL=1;COUNT=4")
    ex1 = datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc)
    ex2 = datetime(2026, 7, 22, 18, 0, 0, tzinfo=timezone.utc)

    raw = build_event_ics(
        booking=booking,
        room=_make_room(),
        organizer_email="organizer@example.com",
        attendee_emails=["a@example.com"],
        method="REQUEST",
        rrule=booking.rrule,
        exdates=[ex1, ex2],
    ).decode()

    assert "EXDATE;TZID=America/Toronto:20260715T140000,20260722T140000" in raw
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_ical.py -q -k "exdate"
```
Expected: FAIL — `TypeError: build_event_ics() got an unexpected keyword argument 'exdates'`.

- [ ] **Step 3: Add the parameter and emit EXDATE**

Extend the signature after `recurrence_id`:

```python
    recurrence_id: datetime | None = None,
    exdates: list[datetime] | None = None,
) -> bytes:
```

Add to the docstring Args block:

```
        exdates:         Occurrence start times to exclude from the RRULE
                         expansion (occurrences cancelled individually).  Unlike
                         recurrence_id this COEXISTS with rrule.  Ignored when
                         rrule is None — there is nothing to subtract from.
```

Directly after the RRULE block added in Task 1:

```python
    # EXDATE subtracts individually-cancelled occurrences from the RRULE
    # expansion. Passing a list yields one comma-separated EXDATE property;
    # icalendar adds the TZID.
    if exdates:
        event.add("exdate", [ex.astimezone(tz) for ex in exdates])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_ical.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add booking-api/app/services/ical.py booking-api/tests/test_ical.py
git commit -m "feat(booking): ical EXDATE to exclude cancelled occurrences

EXDATE coexists with RRULE (the opposite of RECURRENCE-ID) and stops
Outlook regenerating an individually-cancelled occurrence."
```

---

### Task 3: Notifications — `cancelled_occ` intent + EXDATE derivation

**Files:**
- Modify: `booking-api/app/services/notifications.py:111-116`, `:295-334`
- Test: `booking-api/tests/test_notifications.py`

**Interfaces:**
- Consumes: `build_event_ics(..., recurrence_id=..., exdates=...)` from Tasks 1-2.
- Produces: `notif_type="cancelled_occ"` — the intent value Task 4 passes to `enqueue`.

This file already has everything needed: `_SMTPRecorder` (`:43`), `_make_room` (`:95`), `_make_series_bookings` (`:685`, builds 3 future weekly rows sharing `series_id` + `rrule="FREQ=WEEKLY;COUNT=3"`), `_configure_smtp` (`:133`) and `make_user` from conftest. **Without `_configure_smtp` the service runs in log-only mode and builds no ICS at all** — every ICS assertion would vacuously pass on an email that was never constructed.

- [ ] **Step 1: Add a shared ICS extraction helper**

The existing cancel test inlines the MIME walk at `:468-478`. Both new tests need it, and Task 5 imports it. Add at module scope in `booking-api/tests/test_notifications.py`, after `_configure_smtp`:

```python
def _extract_ics(msg) -> str:
    """Return the decoded text/calendar part of a recorded email."""
    for part in msg.walk():
        if part.get_content_type() == "text/calendar":
            payload = part.get_payload(decode=True)
            return (
                payload.decode("utf-8", errors="replace")
                if isinstance(payload, bytes)
                else str(payload)
            )
    raise AssertionError("No text/calendar part in message")
```

- [ ] **Step 2: Write the failing tests**

Append to `booking-api/tests/test_notifications.py`:

```python
class TestSingleOccurrenceNotification:
    async def test_cancelled_occ_emits_recurrence_id_without_rrule(
        self, test_engine, db_session, monkeypatch
    ):
        """enqueue('cancelled_occ') on a series row must produce a CANCEL that
        targets ONE instance: RECURRENCE-ID present, RRULE absent.

        The intent has to survive on NotificationLog.notif_type rather than in a
        call argument — scheduler.py retries by re-rendering from the stored log.
        """
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="occ_cancel@test.com", full_name="Occ Org")
        room = await _make_room(db_session)
        await _configure_smtp(db_session)
        bookings = await _make_series_bookings(db_session, room.id, organizer.id, count=3)
        second = bookings[1]

        from app.services.notifications import enqueue
        log = await enqueue(db_session, [second], "cancelled_occ")

        assert log is not None
        assert log.notif_type == "cancelled_occ"
        ics = _extract_ics(_SMTPRecorder.instances[0].sent_messages[0])
        assert "METHOD:CANCEL" in ics
        assert "RECURRENCE-ID" in ics
        assert "RRULE" not in ics, f"RRULE would cancel the whole series: {ics[:400]}"

    async def test_series_request_exdates_cancelled_occurrence(
        self, test_engine, db_session, monkeypatch
    ):
        """A series REQUEST must EXDATE every future occurrence cancelled on its
        own, or Outlook regenerates it from the RRULE."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="occ_exdate@test.com", full_name="Exdate Org")
        room = await _make_room(db_session)
        await _configure_smtp(db_session)
        bookings = await _make_series_bookings(db_session, room.id, organizer.id, count=3)
        second = bookings[1]
        second.status = "cancelled"
        await db_session.flush()

        from app.services.notifications import enqueue
        await enqueue(db_session, [bookings[0]], "updated")

        ics = _extract_ics(_SMTPRecorder.instances[0].sent_messages[0])
        assert "RRULE:FREQ=WEEKLY" in ics
        expected = second.starts_at.astimezone(TZ).strftime("%Y%m%dT%H%M%S")
        assert f"EXDATE;TZID=America/Toronto:{expected}" in ics
```

`TZ` is already defined at `tests/test_notifications.py:31`-ish alongside `_dt`; `_make_series_bookings` places all occurrences in the future (`days_ahead=1 + i*7`), which the `starts_at > now` filter in the EXDATE query requires.

- [ ] **Step 3: Run the tests to verify they fail**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_notifications.py -q -k "recurrence_id or exdates_cancelled"
```
Expected: FAIL — the first test finds `RRULE` present and no `RECURRENCE-ID`; the second finds no `EXDATE`.

- [ ] **Step 4: Teach the plain-text and subject builders the new type**

In `notifications.py:111-115`, add to `action_map`:

```python
    action_map = {
        "created": "A meeting room has been booked for you.",
        "updated": "Your meeting room booking has been updated.",
        "cancelled": "Your meeting room booking has been cancelled.",
        "cancelled_occ": "One occurrence of your recurring meeting room booking has been cancelled.",
    }
```

In `notifications.py:295-297`, add to the subject label map:

```python
        type_label = {
            "created": "Created",
            "updated": "Updated",
            "cancelled": "Cancelled",
            "cancelled_occ": "Cancelled",
        }.get(log_entry.notif_type, log_entry.notif_type.title())
```

- [ ] **Step 5: Route the iCal method and build the ICS context**

Replace `notifications.py:304`:

```python
        ical_method = "CANCEL" if log_entry.notif_type == "cancelled" else "REQUEST"
```

with:

```python
        ical_method = (
            "CANCEL"
            if log_entry.notif_type in ("cancelled", "cancelled_occ")
            else "REQUEST"
        )
```

Then replace the ICS build at `:324-334`:

```python
        # Build ICS
        # rrule: use log's booking rrule field (series bookings carry it)
        ics_bytes = build_event_ics(
            booking=booking,
            room=room,
            organizer_email=organizer_email,
            attendee_emails=attendee_emails,
            method=ical_method,
            rrule=booking.rrule,
            organizer_cn=organizer_cn,
        )
```

with:

```python
        # ── ICS context: series envelope vs. single-instance exception ────────
        # Every row of a series carries the same rrule, so "which VEVENT is this"
        # cannot be read off the booking — it comes from the persisted
        # notif_type. It must be persisted rather than passed as an argument
        # because scheduler.py retries by re-rendering from the stored log.
        recurrence_id: datetime | None = None
        exdates: list[datetime] | None = None
        ics_rrule: str | None = booking.rrule

        if log_entry.notif_type == "cancelled_occ":
            # Cancel exactly this instance. build_event_ics suppresses the RRULE
            # once recurrence_id is set; ics_rrule is cleared here too so the
            # intent is obvious at the call site.
            recurrence_id = booking.starts_at
            ics_rrule = None
        elif ical_method == "REQUEST" and booking.series_id is not None:
            # A series invite must exclude occurrences cancelled individually,
            # or Outlook regenerates them from the RRULE. Derived from the DB
            # (not passed in) so retries pick up the current state.
            exdate_rows = await db.execute(
                select(Booking.starts_at)
                .where(
                    Booking.series_id == booking.series_id,
                    Booking.status == "cancelled",
                    Booking.starts_at > datetime.now(timezone.utc),
                )
                .order_by(Booking.starts_at)
            )
            exdates = [row[0] for row in exdate_rows.all()] or None

        ics_bytes = build_event_ics(
            booking=booking,
            room=room,
            organizer_email=organizer_email,
            attendee_emails=attendee_emails,
            method=ical_method,
            rrule=ics_rrule,
            organizer_cn=organizer_cn,
            recurrence_id=recurrence_id,
            exdates=exdates,
        )
```

`select`, `Booking`, `datetime` and `timezone` are already imported in this module (used at `:268` and `:379-381`) — verify rather than assume; add imports only if a `NameError` says so.

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_notifications.py -q
```
Expected: PASS, existing tests in the file unaffected.

- [ ] **Step 7: Commit**

```bash
git add booking-api/app/services/notifications.py booking-api/tests/test_notifications.py
git commit -m "feat(booking): cancelled_occ notification renders a targeted CANCEL

Intent lives on NotificationLog.notif_type because scheduler retries
re-render the ICS from the stored log. Series REQUESTs now derive EXDATE
from cancelled future rows so Outlook stops regenerating them."
```

---

### Task 4: Cancel endpoint — allow single-occurrence cancel of a series member

**Files:**
- Modify: `booking-api/app/api/v1/bookings.py:1328-1364`
- Test: `booking-api/tests/test_booking_lifecycle.py`

**Interfaces:**
- Consumes: `enqueue(db, [booking], "cancelled_occ")` from Task 3.
- Produces: `POST /bookings/{id}/cancel?series=false` on a series member → `200 {"cancelled": 1}`. New error detail: `occurrence_already_started`.

- [ ] **Step 1: Write the failing tests**

Append to `booking-api/tests/test_booking_lifecycle.py`. The file already provides `_dt(h, m, days_ahead)` (`:38`), `_create_room(client, ...)` (`:48`), `_create_series(client, room_id, starts, ends, count=4)` (`:89`, returns the create response dict — occurrences are under `["bookings"]`) and `_insert_booking_raw(db_session, room_id, starts_at, ends_at, *, organizer_id, status, series_id, rrule)` (`:103`). Add these to the existing `TestCancelBooking` class (`:388`), matching its authed-client style:

```python
    async def test_cancel_single_occurrence_leaves_siblings_confirmed(
        self, db_session, client
    ):
        """series=false on a series member cancels exactly that occurrence.

        The sibling assertion is the point of the test — a regression here
        silently cancels the whole series in the database.
        """
        room = await _create_room(client)
        series = await _create_series(
            client, room["id"], _dt(10), _dt(11), count=3,
        )
        occurrences = series["bookings"]
        target_id = occurrences[1]["id"]

        resp = await client.post(f"/api/v1/bookings/{target_id}/cancel?series=false")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cancelled": 1}

        detail = await client.get(f"/api/v1/bookings/{target_id}")
        assert detail.json()["status"] == "cancelled"
        for sibling in (occurrences[0], occurrences[2]):
            sib = await client.get(f"/api/v1/bookings/{sibling['id']}")
            assert sib.json()["status"] == "confirmed"

    async def test_cancel_single_occurrence_rejects_past_occurrence(
        self, db_session, client, requester
    ):
        """A started/finished occurrence must not be cancellable — otherwise an
        admin could 'cancel' yesterday's meeting and mail every attendee."""
        room = await _create_room(client)
        series_id = uuid.uuid4()
        past = await _insert_booking_raw(
            db_session, room["id"],
            _dt(10, days_ahead=-7), _dt(11, days_ahead=-7),
            organizer_id=requester.id,
            series_id=series_id,
            rrule="FREQ=WEEKLY;COUNT=2",
        )

        resp = await client.post(f"/api/v1/bookings/{past.id}/cancel?series=false")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "occurrence_already_started"
        await db_session.refresh(past)
        assert past.status == "confirmed"

    async def test_cancel_single_occurrence_frees_the_room_slot(
        self, db_session, client
    ):
        """The cancelled slot becomes bookable again — availability counts
        confirmed rows only, so a new booking at that exact time must succeed."""
        room = await _create_room(client)
        series = await _create_series(
            client, room["id"], _dt(10), _dt(11), count=3,
        )
        target = series["bookings"][1]

        await client.post(f"/api/v1/bookings/{target['id']}/cancel?series=false")

        resp = await client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Reclaiming the freed slot",
            "starts_at": target["starts_at"],
            "ends_at": target["ends_at"],
        })
        assert resp.status_code == 201, resp.text

    async def test_cancel_series_still_cancels_all_future_occurrences(
        self, client
    ):
        """Regression: series=true keeps its existing meaning."""
        room = await _create_room(client)
        series = await _create_series(
            client, room["id"], _dt(10), _dt(11), count=3,
        )
        occurrences = series["bookings"]

        resp = await client.post(
            f"/api/v1/bookings/{occurrences[0]['id']}/cancel?series=true"
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cancelled": 3}
        for occ in occurrences:
            detail = await client.get(f"/api/v1/bookings/{occ['id']}")
            assert detail.json()["status"] == "cancelled"
```

**Note for the implementer:** `TestCancelBooking` may use an authed client fixture rather than the bare `client` — read `:388-420` and match whatever the neighbouring cancel tests use for auth, keeping the organizer consistent so the cancel is authorized. `_insert_booking_raw` accepts a negative `days_ahead` through `_dt` for the past occurrence.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_booking_lifecycle.py -q -k "single_occurrence"
```
Expected: FAIL — `400 series_member_use_series_cancel` instead of `200`.

- [ ] **Step 3: Replace the guard in the single-cancel branch**

In `bookings.py`, replace `:1328-1341`:

```python
    else:
        # series=false: single booking cancel
        # Block single-cancel of a series member
        if booking.series_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="series_member_use_series_cancel",
            )

        if booking.status != "confirmed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel booking with status '{booking.status}'",
            )
```

with:

```python
    else:
        # series=false: cancel exactly one booking.
        # For a series member this is a single-occurrence exception — the row is
        # cancelled alone and the invite carries RECURRENCE-ID without an RRULE
        # (see notifications.send_notification), so Outlook drops only this
        # instance.
        if booking.status != "confirmed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel booking with status '{booking.status}'",
            )

        # The series branch only ever cancels occurrences with starts_at > now.
        # The single branch must match for series members, otherwise an admin
        # could cancel an occurrence that already happened and mail every
        # attendee about it.
        if booking.series_id is not None and booking.starts_at <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="occurrence_already_started",
            )
```

`now` is already defined at `:1273`, before the `if series:` split.

- [ ] **Step 4: Bump SEQUENCE and enqueue with the right intent**

Replace `:1343-1344`:

```python
        booking.status = "cancelled"
        booking.sync_status = "pending"
```

with:

```python
        booking.status = "cancelled"
        booking.sync_status = "pending"
        if booking.series_id is not None:
            # A RECURRENCE-ID instance carries its own SEQUENCE, independent of
            # the series master, so the row's own value is the right basis.
            booking.ical_sequence += 1
```

Replace the enqueue at `:1357-1362`:

```python
        try:
            await enqueue(db, [booking], "cancelled")
        except Exception:
            logger.exception(
                "enqueue failed for cancel of booking %s — suppressed", booking.id
            )
```

with:

```python
        # A series member cancels one instance ('cancelled_occ' → RECURRENCE-ID);
        # a standalone booking cancels the whole event.
        notif_type = "cancelled_occ" if booking.series_id is not None else "cancelled"
        try:
            await enqueue(db, [booking], notif_type)
        except Exception:
            logger.exception(
                "enqueue failed for cancel of booking %s — suppressed", booking.id
            )
```

- [ ] **Step 5: Update the endpoint docstring**

Replace the `series:` line in the docstring at `:1251-1256`:

```python
    Query params:
        series: if True, cancel all future confirmed occurrences of the series.
                Requires the target booking to be a series member.
                If False (default), cancel only the target booking.  For a series
                member this is a single-occurrence exception: siblings are kept
                and the invite carries RECURRENCE-ID (no RRULE) so Outlook drops
                only this instance.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_booking_lifecycle.py -q
```
Expected: PASS.

Then confirm nothing else depended on the removed error:
```bash
docker exec uniops_booking_api grep -rn "series_member_use_series_cancel" /app || echo "no references left"
```
Expected: `no references left`.

- [ ] **Step 7: Commit**

```bash
git add booking-api/app/api/v1/bookings.py booking-api/tests/test_booking_lifecycle.py
git commit -m "feat(booking): cancel a single occurrence of a recurring series

series=false on a series member now cancels that occurrence alone instead
of 400ing. Adds a not-yet-started guard matching the series branch."
```

---

### Task 5: Series edit — keep cancelled occurrences aligned, supply EXDATE

`bookings.py:764-772` fetches series rows filtered to `status == "confirmed"`, and four consumers reuse that one list with different meanings. Widening the filter naively breaks two of them: a fully-cancelled series would stop 404-ing (`:774`), and `:795` could resolve the room from a cancelled row. Split the list instead.

**Files:**
- Modify: `booking-api/app/api/v1/bookings.py:763-901`
- Test: `booking-api/tests/test_series_edit.py`

**Interfaces:**
- Consumes: EXDATE derivation from Task 3; single-occurrence cancel from Task 4.
- Produces: no signature change. `PATCH /bookings/series/{id}` still returns `{"updated": <confirmed future count>, "series_id": str}`.

- [ ] **Step 1: Write the failing tests**

`test_series_edit.py` already provides `_dt` (`:38`), `_create_room` (`:48`), `_create_series` (`:75`), `_insert_booking_raw` (`:89`) and `TZ = ZoneInfo("America/Toronto")` (`:31`). It has **no SMTP setup**, which means its existing tests run in log-only mode and never build an ICS — so the EXDATE test below must import the recorder. `tests/` is a package (`tests/__init__.py` exists), so a cross-module import is clean.

Add near the file's other imports:

```python
from tests.test_notifications import _SMTPRecorder, _configure_smtp, _extract_ics
```

Append a new class:

```python
class TestSeriesEditWithCancelledOccurrence:
    async def test_cancelled_occurrence_follows_new_times_and_stays_cancelled(
        self, db_session, client
    ):
        """A cancelled occurrence tracks the series' new times but stays cancelled.

        The alignment is what makes EXDATE correct: EXDATE has to match the
        instant Outlook computes from the new RRULE, so a cancelled row's
        starts_at must move with the series.
        """
        room = await _create_room(client)
        series = await _create_series(client, room["id"], _dt(8, 30), _dt(9, 0), count=3)
        occurrences = series["bookings"]
        cancelled_id = occurrences[1]["id"]
        await client.post(f"/api/v1/bookings/{cancelled_id}/cancel?series=false")

        resp = await client.patch(
            f"/api/v1/bookings/series/{occurrences[0]['series_id']}",
            json={"start_time": "09:00", "end_time": "09:30"},
        )

        assert resp.status_code == 200, resp.text
        detail = (await client.get(f"/api/v1/bookings/{cancelled_id}")).json()
        assert detail["status"] == "cancelled"
        local = datetime.fromisoformat(detail["starts_at"]).astimezone(TZ)
        assert (local.hour, local.minute) == (9, 0)

    async def test_series_edit_invite_exdates_the_updated_time(
        self, db_session, client, monkeypatch
    ):
        """The series invite excludes the cancelled occurrence at its UPDATED
        time. The original time would not match any instance of the new RRULE,
        so the occurrence would reappear on every attendee's calendar."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)
        await _configure_smtp(db_session)

        room = await _create_room(client)
        series = await _create_series(client, room["id"], _dt(8, 30), _dt(9, 0), count=3)
        occurrences = series["bookings"]
        cancelled_id = occurrences[1]["id"]
        await client.post(f"/api/v1/bookings/{cancelled_id}/cancel?series=false")

        await client.patch(
            f"/api/v1/bookings/series/{occurrences[0]['series_id']}",
            json={"start_time": "09:00", "end_time": "09:30"},
        )

        detail = (await client.get(f"/api/v1/bookings/{cancelled_id}")).json()
        expected = (
            datetime.fromisoformat(detail["starts_at"])
            .astimezone(TZ)
            .strftime("%Y%m%dT%H%M%S")
        )
        assert expected.endswith("T090000"), "cancelled row did not follow the edit"
        ics = _extract_ics(_SMTPRecorder.instances[-1].sent_messages[-1])
        assert f"EXDATE;TZID=America/Toronto:{expected}" in ics

    async def test_series_edit_404s_for_unknown_series(self, client):
        """Guard: widening the fetch must not turn 404 into 200."""
        resp = await client.patch(
            f"/api/v1/bookings/series/{uuid.uuid4()}",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404

    async def test_series_edit_400s_when_all_future_occurrences_cancelled(
        self, client
    ):
        """Guard: a series whose future occurrences were every one cancelled
        individually still reports series_fully_started."""
        room = await _create_room(client)
        series = await _create_series(client, room["id"], _dt(8, 30), _dt(9, 0), count=2)
        occurrences = series["bookings"]
        for occ in occurrences:
            await client.post(f"/api/v1/bookings/{occ['id']}/cancel?series=false")

        resp = await client.patch(
            f"/api/v1/bookings/series/{occurrences[0]['series_id']}",
            json={"title": "Nope"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "series_fully_started"
```

**Note for the implementer:** these tests reach the API through whatever authed client the file's other series-edit tests use — read `TestSeriesEditTimeChange` (`:259`) and match its fixtures and its `series_id` accessor (if the create response omits `series_id`, read it back from a booking detail). The last test relies on Task 4: cancelling a series member with `series=false` must already work.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_series_edit.py -q -k "cancelled or unknown_series"
```
Expected: FAIL — the cancelled row's `starts_at` is unchanged (still 08:30) and no `EXDATE` in the invite.

- [ ] **Step 3: Fetch all rows, split by status**

Replace `bookings.py:763-772`:

```python
    # ── Fetch all confirmed occurrences of the series ─────────────────────────
    series_result = await db.execute(
        select(Booking)
        .where(
            Booking.series_id == series_id,
            Booking.status == "confirmed",
        )
        .order_by(Booking.starts_at)
    )
    all_series_bookings = list(series_result.scalars().all())
```

with:

```python
    # ── Fetch ALL occurrences of the series, any status ───────────────────────
    # Individually-cancelled occurrences are fetched for two reasons:
    #   1. their times must follow series edits, so EXDATE keeps matching the
    #      instants Outlook computes from the RRULE;
    #   2. they are where the EXDATE list comes from (see notifications.py).
    # They must NOT take part in authorization, 404 / room resolution, conflict
    # checks or the returned count — confirmed_series_bookings owns all of that,
    # unchanged.
    series_result = await db.execute(
        select(Booking)
        .where(Booking.series_id == series_id)
        .order_by(Booking.starts_at)
    )
    all_series_bookings = list(series_result.scalars().all())
    confirmed_series_bookings = [
        b for b in all_series_bookings if b.status == "confirmed"
    ]
```

- [ ] **Step 4: Point the confirmed-only consumers at the new list**

Replace `:774-776`:

```python
    if not all_series_bookings:
        # No confirmed bookings with this series_id → treat as not found
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")
```

with:

```python
    if not confirmed_series_bookings:
        # No confirmed bookings with this series_id → treat as not found.
        # Keyed on confirmed rows, not all rows: a series whose occurrences were
        # every one cancelled must still read as "not found", exactly as before.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")
```

Replace `:780`:

```python
    organizer_id = all_series_bookings[0].organizer_id
```

with:

```python
    organizer_id = confirmed_series_bookings[0].organizer_id
```

Replace `:784-786`:

```python
    # ── Target rows: future confirmed only ────────────────────────────────────
    now = datetime.now(tz)
    future_bookings = [b for b in all_series_bookings if b.starts_at > now]
```

with:

```python
    # ── Target rows ───────────────────────────────────────────────────────────
    # future_bookings keeps its old meaning (future + confirmed) and its old
    # responsibilities: series_fully_started, room resolution, conflict checks,
    # the invite anchor and the returned count.
    now = datetime.now(tz)
    future_bookings = [b for b in confirmed_series_bookings if b.starts_at > now]
    # Cancelled future occurrences only follow the new times and feed EXDATE.
    future_cancelled = [
        b for b in all_series_bookings
        if b.status == "cancelled" and b.starts_at > now
    ]
```

Everything downstream of `:788` (`series_fully_started`, `:795` room resolution, `:1028` invite anchor, `:1038` count) keeps working untouched, because `future_bookings` still means what it always meant.

- [ ] **Step 5: Compute windows for both groups without duplicating the maths**

Replace `:886-901`:

```python
    # ── Compute new windows for each future occurrence ────────────────────────
    series_ids_set = {b.id for b in all_series_bookings}
    occurrence_windows: list[tuple[Booking, datetime, datetime]] = []

    for b in future_bookings:
        if body.start_time is not None:
            local_date = b.starts_at.astimezone(tz).date()
            new_starts = datetime(local_date.year, local_date.month, local_date.day,
                                  new_start_h, new_start_m, tzinfo=tz)
            new_ends = datetime(local_date.year, local_date.month, local_date.day,
                                new_end_h, new_end_m, tzinfo=tz)
        else:
            new_starts = b.starts_at
            new_ends = b.ends_at

        occurrence_windows.append((b, new_starts, new_ends))
```

with:

```python
    # ── Compute new windows for each future occurrence ────────────────────────
    series_ids_set = {b.id for b in all_series_bookings}

    def _new_window(b: Booking) -> tuple[Booking, datetime, datetime]:
        """Return (booking, new_starts, new_ends) for one occurrence.

        The occurrence keeps its own date and takes the series' new time of day.
        """
        if body.start_time is None:
            return (b, b.starts_at, b.ends_at)
        local_date = b.starts_at.astimezone(tz).date()
        return (
            b,
            datetime(local_date.year, local_date.month, local_date.day,
                     new_start_h, new_start_m, tzinfo=tz),
            datetime(local_date.year, local_date.month, local_date.day,
                     new_end_h, new_end_m, tzinfo=tz),
        )

    occurrence_windows = [_new_window(b) for b in future_bookings]
    # Cancelled occurrences are deliberately absent from the conflict check
    # below — a cancelled occurrence does not occupy the room, so testing it
    # against real bookings would reject valid edits.
    cancelled_windows = [_new_window(b) for b in future_cancelled]
```

- [ ] **Step 6: Apply the updates to both groups**

Replace the apply loop header at `:953`:

```python
        for b, new_starts, new_ends in occurrence_windows:
```

with:

```python
        # Cancelled occurrences are updated alongside confirmed ones. The loop
        # never touches b.status, so they stay cancelled while their times track
        # the series — which is what keeps EXDATE aligned.
        for b, new_starts, new_ends in occurrence_windows + cancelled_windows:
```

`new_sequence` at `:950` already reads `all_series_bookings`, which now includes cancelled rows — correct, and it keeps SEQUENCE monotonic across the series.

- [ ] **Step 7: Run the tests to verify they pass**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/test_series_edit.py -q
```
Expected: PASS.

- [ ] **Step 8: Run the whole suite against the baseline**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/ -q 2>&1 | tail -3
```
Expected: `310 + <number of tests you added>` passed, 0 failed. A bare "no failures" is not evidence — compare the count.

- [ ] **Step 9: Commit**

```bash
git add booking-api/app/api/v1/bookings.py booking-api/tests/test_series_edit.py
git commit -m "feat(booking): series edits keep cancelled occurrences aligned

Cancelled future occurrences now follow the series' new times (status
untouched) so EXDATE matches the instants Outlook derives from the RRULE.
Splits the series fetch so 404, room resolution, conflict checks and the
returned count keep their confirmed-only semantics."
```

---

### Task 6: Frontend — offer the choice

**Files:**
- Modify: `booking/src/pages/MyBookingsPage.tsx:106-139`
- Modify: `booking/src/pages/admin/AllBookingsPage.tsx:88-98`
- Modify: `booking/src/pages/admin/NotificationsPage.tsx:72`

**Interfaces:**
- Consumes: `POST /bookings/{id}/cancel?series=false` from Task 4. `useCancelBooking` and `useAdminForceCancel` already take `{ id, series }` — no service changes.
- Produces: nothing downstream.

All copy is English only.

- [ ] **Step 1: Add "This occurrence" to the organizer dialog**

In `MyBookingsPage.tsx`, replace the dialog body at `:111-135`:

```tsx
        <h2 className="text-base font-semibold text-neutral-900">Cancel booking</h2>
        <p className="text-sm text-neutral-600">
          {isSeries
            ? 'This is part of a recurring series. Cancel the entire series? Past and in-progress occurrences are kept.'
            : `Are you sure you want to cancel "${booking.title}"? This cannot be undone.`}
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >
            Keep
          </button>
          <button
            type="button"
            onClick={() => onConfirm(isSeries)}
            disabled={isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isSeries ? 'Cancel series' : 'Cancel booking'}
          </button>
        </div>
```

with:

```tsx
        <h2 className="text-base font-semibold text-neutral-900">Cancel booking</h2>
        <p className="text-sm text-neutral-600">
          {isSeries
            ? `"${booking.title}" is part of a recurring series. Cancel only this occurrence, or the whole series? Cancelling the series keeps past and in-progress occurrences.`
            : `Are you sure you want to cancel "${booking.title}"? This cannot be undone.`}
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >
            Keep
          </button>
          {isSeries && (
            <button
              type="button"
              onClick={() => onConfirm(false)}
              disabled={isPending}
              className="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              This occurrence
            </button>
          )}
          <button
            type="button"
            onClick={() => onConfirm(isSeries)}
            disabled={isPending}
            className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isSeries ? 'Cancel series' : 'Cancel booking'}
          </button>
        </div>
```

- [ ] **Step 2: Fix the admin dialog copy**

The "This occurrence" button already exists in `AllBookingsPage.tsx:108-117` and already calls `onConfirm(false)` — it only ever failed because the backend rejected it. Task 4 fixed that. Replace only the copy at `:94-98`:

```tsx
        <p className="text-sm text-neutral-600">
          {isSeries
            ? `"${booking.title}" is part of a recurring series. Cancel the entire series?`
            : `Cancel "${booking.title}" booked by ${booking.organizer_name}? This cannot be undone.`}
        </p>
```

with:

```tsx
        <p className="text-sm text-neutral-600">
          {isSeries
            ? `"${booking.title}" booked by ${booking.organizer_name} is part of a recurring series. Cancel only this occurrence, or the whole series?`
            : `Cancel "${booking.title}" booked by ${booking.organizer_name}? This cannot be undone.`}
        </p>
```

- [ ] **Step 3: Label the new notification type**

In `NotificationsPage.tsx`, replace `:72`:

```tsx
  const typeFmt = log.notif_type.replace(/_/g, ' ')
```

with:

```tsx
  const typeFmt = TYPE_LABELS[log.notif_type] ?? log.notif_type.replace(/_/g, ' ')
```

and declare the map at **module scope**, alongside the file's other constant maps (not inside the component — it must not be rebuilt on every render):

```tsx
const TYPE_LABELS: Record<string, string> = {
  cancelled_occ: 'cancelled occurrence',
}
```

- [ ] **Step 4: Typecheck**

Run:
```bash
cd booking && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
Expected: no errors. (`tsc -b` / `npm run build` fail on this repo's TS 6 over a deprecated `baseUrl` — use the command above.)

- [ ] **Step 5: Commit**

```bash
git add booking/src/pages/MyBookingsPage.tsx booking/src/pages/admin/AllBookingsPage.tsx booking/src/pages/admin/NotificationsPage.tsx
git commit -m "feat(booking): offer 'This occurrence' when cancelling a series

My Bookings gains the choice; All Bookings already had the button and only
needed the copy to match now that the backend accepts it."
```

---

### Task 7: Remove the dead `rrule` parameter from `enqueue`

`enqueue(..., rrule=...)` is never read (`notifications.py:414-490`); the ICS takes `booking.rrule` instead. Four call sites pass it and believe it does something. Now that the send path genuinely decides RRULE/RECURRENCE-ID/EXDATE, leaving a fake knob there is a trap.

**Files:**
- Modify: `booking-api/app/services/notifications.py:393-403`
- Modify: `booking-api/app/api/v1/bookings.py:563`, `:1031`, `:1319`
- Modify: `booking-api/tests/test_notifications.py:732`, `:766`

**Interfaces:**
- Consumes: nothing.
- Produces: `enqueue(db, bookings, notif_type) -> NotificationLog | None` — no keyword arguments.

- [ ] **Step 1: Drop the parameter**

In `notifications.py`, replace the signature at `:393-399`:

```python
async def enqueue(
    db: AsyncSession,
    bookings: list[Booking],
    notif_type: str,
    *,
    rrule: str | None = None,
) -> NotificationLog | None:
```

with:

```python
async def enqueue(
    db: AsyncSession,
    bookings: list[Booking],
    notif_type: str,
) -> NotificationLog | None:
```

Replace the docstring paragraph at `:402-403`:

```python
    For series bookings, pass the first occurrence + rrule; a single notification
    email covers the whole series (one RRULE VEVENT).
```

with:

```python
    For series bookings, pass the first occurrence; one notification email covers
    the whole series (one RRULE VEVENT).  The RRULE is read from booking.rrule and
    the VEVENT shape (series envelope vs. RECURRENCE-ID exception) is decided by
    notif_type in send_notification.
```

- [ ] **Step 2: Update the call sites**

- `bookings.py:563`: `await enqueue(db, bookings, "created", rrule=rrule)` → `await enqueue(db, bookings, "created")`
- `bookings.py:1031`: `await enqueue(db, [first_future], "updated", rrule=series_rrule)` → `await enqueue(db, [first_future], "updated")`
- `bookings.py:1319`: `await enqueue(db, [first_future], "cancelled", rrule=series_rrule)` → `await enqueue(db, [first_future], "cancelled")`
- `tests/test_notifications.py:732` and `:766`: drop `, rrule=anchor.rrule`

If `series_rrule` becomes unused at a call site, delete its assignment too; if it is still used (e.g. for logging), leave it.

- [ ] **Step 3: Verify no caller still passes it**

Run:
```bash
docker exec uniops_booking_api grep -rn "rrule=" /app/app /app/tests | grep -i enqueue || echo "no enqueue rrule= callers left"
```
Expected: `no enqueue rrule= callers left`.

- [ ] **Step 4: Run the full suite**

Run:
```bash
docker exec uniops_booking_api python -m pytest tests/ -q 2>&1 | tail -3
```
Expected: same count as Task 5 Step 8, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add booking-api/app/services/notifications.py booking-api/app/api/v1/bookings.py booking-api/tests/test_notifications.py
git commit -m "refactor(booking): drop dead rrule kwarg from enqueue

enqueue never read it — the ICS takes booking.rrule. Now that the send path
decides RRULE vs RECURRENCE-ID vs EXDATE, a fake knob is a trap."
```

---

### Task 8: Manual verification against a real Outlook client

The automated tests prove the bytes are right. They cannot prove Outlook agrees, and this module's history is a list of things Outlook did differently than the RFC suggested (see the RDATE/VTIMEZONE note at `ical.py:12-24`). Do not skip this.

**Files:** none.

- [ ] **Step 1: Restart the dev stack**

```bash
docker compose -f docker-compose.dev.yml restart booking-api booking-frontend
```

- [ ] **Step 2: Verify the DB and list behaviour**

In Booking (`http://localhost:5178`): create a weekly recurring meeting with yourself as organizer and one attendee, at least 3 occurrences, all in the future. From **My Bookings**, cancel the middle occurrence via **This occurrence**.

Expected: exactly one row greys out with a Cancelled badge; the siblings stay Confirmed; the room's slot is free again for that date.

- [ ] **Step 3: Verify the invite**

Check the attendee's Outlook mailbox.

Expected: a "Cancelled: <title>" mail; accepting it removes **only** that occurrence; the other occurrences remain on the calendar. If the whole series disappears, stop — the RRULE suppression from Task 1 is not reaching the wire.

- [ ] **Step 4: Verify the EXDATE linkage**

Now edit the whole series (change the start time). Expected: the updated invite moves the remaining occurrences, and the cancelled occurrence **does not come back** at the new time.

- [ ] **Step 5: Verify the admin path**

Repeat Step 2 from **All Bookings** as an admin against another user's series, and confirm the audit log records `force_cancel` rather than `cancel`.

---

## Definition of Done

- Full suite green in-container at `310 + <added tests>` passed, 0 failed
- `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` clean in `booking/`
- Task 8 manual verification passed, Outlook included
- No alembic migration added
- All commits on `feature/booking-single-occurrence-cancel`
