"""Tests for the statutory deadline rules.

Each case cites the authority it is checking. These dates are hardcoded on
purpose: the subject under test is calendar arithmetic against named
regulations, so a fixture computed relative to "now" would test nothing and
would drift into passing for the wrong reason.

Ontario statutory holidays used below are the real ones — the point of several
cases is that a deadline lands differently because of them.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import uuid
import pytest

from app.services.statutory import (
    JHSC_REPLY_AUTHORITY,
    MOL_WRITTEN_REPORT_AUTHORITY,
    WSIB_FORM7_AUTHORITY,
    UnknownDeadlineKind,
    add_business_days,
    compute_due,
    internal_target,
    is_business_day,
)

ET = ZoneInfo("America/Toronto")
UTC = ZoneInfo("UTC")

# Ontario statutory holidays, 2026.
HOLIDAYS_2026 = frozenset({
    date(2026, 1, 1),    # New Year's Day
    date(2026, 2, 16),   # Family Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 18),   # Victoria Day
    date(2026, 7, 1),    # Canada Day
    date(2026, 9, 7),    # Labour Day
    date(2026, 10, 12),  # Thanksgiving
    date(2026, 12, 25),  # Christmas Day
    date(2026, 12, 26),  # Boxing Day
})
HOLIDAYS_2027 = frozenset({date(2027, 1, 1)})
HOLIDAYS = HOLIDAYS_2026 | HOLIDAYS_2027


# ── OHSA s.51(1): 48 hours after the occurrence ─────────────────────────────

def test_mol_48h_is_wall_clock_from_the_occurrence():
    """OHSA s.51(1): 'within forty-eight hours after the occurrence'."""
    occurred = datetime(2026, 8, 28, 22, 10, tzinfo=ET)
    assert compute_due("mol_48h", occurred, HOLIDAYS) == datetime(2026, 8, 30, 22, 10, tzinfo=ET)


def test_mol_48h_runs_straight_through_a_weekend():
    """The 48 hours are wall-clock, not business hours — a Friday evening
    critical injury is due Sunday evening, weekend or not."""
    occurred = datetime(2026, 8, 28, 18, 0, tzinfo=ET)  # Friday
    due = compute_due("mol_48h", occurred, HOLIDAYS)
    assert due == datetime(2026, 8, 30, 18, 0, tzinfo=ET)  # Sunday
    assert due.weekday() == 6


def test_mol_48h_runs_through_a_statutory_holiday():
    occurred = datetime(2026, 12, 24, 9, 0, tzinfo=ET)  # Christmas Eve
    assert compute_due("mol_48h", occurred, HOLIDAYS) == datetime(2026, 12, 26, 9, 0, tzinfo=ET)


def test_mol_48h_crosses_a_month_boundary():
    occurred = datetime(2026, 6, 29, 23, 30, tzinfo=ET)
    assert compute_due("mol_48h", occurred, HOLIDAYS) == datetime(2026, 7, 1, 23, 30, tzinfo=ET)


def test_mol_48h_ignores_the_holiday_list_entirely():
    """Passing holidays must not change a wall-clock result."""
    occurred = datetime(2026, 12, 24, 9, 0, tzinfo=ET)
    assert compute_due("mol_48h", occurred, HOLIDAYS) == compute_due("mol_48h", occurred, frozenset())


# ── WSIB: three business days from employer awareness, to receipt ───────────

def test_wsib_three_business_days_midweek():
    """Monday awareness gives Tuesday, Wednesday, Thursday."""
    aware = datetime(2026, 8, 24, 10, 0, tzinfo=ET)  # Monday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 8, 27)  # Thursday
    assert due.astimezone(ET).hour == 23


def test_wsib_skips_the_weekend():
    """Friday awareness: Monday, Tuesday, Wednesday are the three days."""
    aware = datetime(2026, 8, 28, 14, 0, tzinfo=ET)  # Friday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 2)  # Wednesday


def test_wsib_skips_a_statutory_holiday():
    """Labour Day (7 Sep 2026) is not a business day, pushing the deadline out
    by one. Without the holiday list this would land on Wednesday 9 Sep."""
    aware = datetime(2026, 9, 4, 9, 0, tzinfo=ET)  # Friday before Labour Day
    with_holiday = compute_due("wsib_form7", aware, HOLIDAYS)
    without_holiday = compute_due("wsib_form7", aware, frozenset())
    assert with_holiday.astimezone(ET).date() == date(2026, 9, 10)  # Thursday
    assert without_holiday.astimezone(ET).date() == date(2026, 9, 9)
    assert with_holiday > without_holiday


def test_wsib_crosses_the_new_year_and_two_holidays():
    """Christmas Day and Boxing Day 2026 fall Friday/Saturday, and New Year's
    Day 2027 is a Friday. Awareness on Christmas Eve lands well into January."""
    aware = datetime(2026, 12, 24, 16, 0, tzinfo=ET)  # Thursday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    # 25th holiday, 26th–27th weekend, 28th–30th are the three business days.
    assert due.astimezone(ET).date() == date(2026, 12, 30)


def test_wsib_starting_day_is_never_counted():
    """'Within three business days after' gives three whole working days, so a
    Tuesday-morning and a Tuesday-late-evening awareness share a deadline."""
    early = datetime(2026, 8, 25, 6, 0, tzinfo=ET)
    late = datetime(2026, 8, 25, 23, 0, tzinfo=ET)
    assert compute_due("wsib_form7", early, HOLIDAYS) == compute_due("wsib_form7", late, HOLIDAYS)


def test_wsib_awareness_on_a_weekend_still_gets_three_working_days():
    aware = datetime(2026, 8, 29, 11, 0, tzinfo=ET)  # Saturday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 2)  # Mon, Tue, Wed


def test_wsib_deadline_is_end_of_the_third_day():
    """The obligation is discharged by filing at any point during that day."""
    aware = datetime(2026, 8, 24, 10, 0, tzinfo=ET)
    due = compute_due("wsib_form7", aware, HOLIDAYS).astimezone(ET)
    assert (due.hour, due.minute, due.second) == (23, 59, 59)


# ── The timezone trap ───────────────────────────────────────────────────────

def test_business_days_count_from_the_ontario_date_not_the_utc_date():
    """21:00 Friday in Ontario is 01:00 Saturday UTC. Counting from the UTC
    date would start a day late and hand back a deadline a day too generous."""
    aware_utc = datetime(2026, 8, 29, 1, 0, tzinfo=UTC)  # = Fri 28th 21:00 ET
    assert aware_utc.astimezone(ET).date() == date(2026, 8, 28)
    due = compute_due("wsib_form7", aware_utc, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 2)


def test_naive_datetimes_are_refused():
    """A naive timestamp cannot say which local day it falls on, and guessing
    would silently move a legal deadline."""
    with pytest.raises(ValueError):
        compute_due("wsib_form7", datetime(2026, 8, 24, 10, 0), HOLIDAYS)


# ── OHSA s.9(20): 21 calendar days ──────────────────────────────────────────

def test_jhsc_reply_is_twenty_one_calendar_days():
    recommended = datetime(2026, 8, 21, 9, 0, tzinfo=ET)
    assert compute_due("jhsc_reply_21d", recommended, HOLIDAYS) == datetime(2026, 9, 11, 9, 0, tzinfo=ET)


def test_jhsc_reply_does_not_skip_weekends_or_holidays():
    recommended = datetime(2026, 12, 10, 9, 0, tzinfo=ET)
    due = compute_due("jhsc_reply_21d", recommended, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 12, 31)


# ── Annual and multi-year reviews ───────────────────────────────────────────

def test_policy_review_is_one_year_later():
    reviewed = datetime(2026, 3, 15, 12, 0, tzinfo=ET)
    assert compute_due("policy_annual", reviewed, HOLIDAYS).astimezone(ET).date() == date(2027, 3, 15)


def test_sds_review_is_three_years_later():
    issued = datetime(2026, 5, 4, 12, 0, tzinfo=ET)
    assert compute_due("sds_3y", issued, HOLIDAYS).astimezone(ET).date() == date(2029, 5, 4)


def test_leap_day_review_lands_on_the_28th():
    """29 February has no counterpart in a non-leap year; falling back to the
    28th keeps the review inside the compliant year rather than slipping to
    1 March."""
    reviewed = datetime(2028, 2, 29, 12, 0, tzinfo=ET)
    assert compute_due("policy_annual", reviewed, HOLIDAYS).astimezone(ET).date() == date(2029, 2, 28)


# ── Internal target: WSIB is a receipt deadline ─────────────────────────────

def test_internal_target_for_wsib_is_one_business_day_earlier():
    aware = datetime(2026, 8, 24, 10, 0, tzinfo=ET)  # Monday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    target = internal_target("wsib_form7", due, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 8, 27)
    assert target.astimezone(ET).date() == date(2026, 8, 26)


def test_internal_target_skips_back_over_a_weekend():
    aware = datetime(2026, 8, 27, 10, 0, tzinfo=ET)  # Thursday -> due Tuesday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 1)
    assert internal_target("wsib_form7", due, HOLIDAYS).astimezone(ET).date() == date(2026, 8, 31)


def test_internal_target_leaves_other_clocks_alone():
    occurred = datetime(2026, 8, 28, 22, 10, tzinfo=ET)
    due = compute_due("mol_48h", occurred, HOLIDAYS)
    assert internal_target("mol_48h", due, HOLIDAYS) == due


# ── Guardrails ──────────────────────────────────────────────────────────────

def test_unknown_kind_raises_rather_than_guessing():
    with pytest.raises(UnknownDeadlineKind):
        compute_due("mol_24h", datetime(2026, 8, 24, 10, 0, tzinfo=ET), HOLIDAYS)


def test_is_business_day():
    assert is_business_day(date(2026, 8, 27), HOLIDAYS)          # Thursday
    assert not is_business_day(date(2026, 8, 29), HOLIDAYS)      # Saturday
    assert not is_business_day(date(2026, 9, 7), HOLIDAYS)       # Labour Day
    assert is_business_day(date(2026, 9, 7), frozenset())        # without the list


def test_add_business_days_requires_at_least_one():
    with pytest.raises(ValueError):
        add_business_days(datetime(2026, 8, 24, 10, 0, tzinfo=ET), 0, HOLIDAYS)


def test_authorities_are_stated_on_the_rules():
    """The citation travels with the rule so a screen can show it and a
    reviewer can check it."""
    assert "51(1)" in MOL_WRITTEN_REPORT_AUTHORITY
    assert "WSIB" in WSIB_FORM7_AUTHORITY
    assert "9(20)" in JHSC_REPLY_AUTHORITY


def test_a_full_year_of_wsib_deadlines_never_lands_on_a_non_business_day():
    """Property check across every day of 2026: the deadline is always a
    working day, never a weekend or a statutory holiday."""
    day = datetime(2026, 1, 1, 10, 0, tzinfo=ET)
    while day.year == 2026:
        due = compute_due("wsib_form7", day, HOLIDAYS).astimezone(ET).date()
        assert is_business_day(due, HOLIDAYS), f"{day.date()} -> {due} is not a business day"
        assert due > day.date()
        day += timedelta(days=1)


async def test_days_since_counts_injuries_not_every_record(hse_manager, worker, test_engine):
    """Reporting a good catch must not reset the tile to zero.

    It counted every incident of any kind, so a near miss — the thing the
    module most wants people to report — wiped the number that the plant reads
    as "how we are doing". HSE asked for it to mean medical incidents, and to
    say so on the tile.
    """
    from datetime import datetime, timedelta, timezone

    _, client = hse_manager
    injured, _ = worker
    long_ago = datetime.now(timezone.utc) - timedelta(days=40)

    cut = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Cut finger",
        "incident_type": "first_aid",
        "occurred_at": long_ago.isoformat(),
        "description": "Caught on a burr.",
        "injured_user_id": str(injured.id),
    })
    # Sent, as the form does: a saved draft is not counted.
    sent = await client.post(f"/api/v1/incidents/{cut.json()['id']}/submit")
    assert sent.status_code == 200, sent.text
    before = (await client.get("/api/v1/dashboard")).json()["headline"]
    assert before["days_since_last_incident"] >= 39

    # A near miss today. Good news, and it used to read as a reset.
    await client.post("/api/v1/incidents", json={
        "form_kind": "near_miss", "title": "Pallet nearly fell",
        "incident_type": "near_miss",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Nobody underneath.",
        "talked_to_operator": "yes",
    })
    after = (await client.get("/api/v1/dashboard")).json()["headline"]
    assert after["days_since_last_incident"] == before["days_since_last_incident"], (
        "a near miss is not an injury, and reporting one must not cost the plant "
        "its count")


async def test_changing_the_check_cycle_moves_the_next_check(hse_manager):
    """A shorter cycle that does not bring the date forward is cosmetic.

    An extinguisher moved from quarterly to monthly kept its date three months
    out and quietly missed two checks — the register said monthly, the schedule
    kept the old promise, and nothing on screen disagreed.
    """
    from datetime import date

    _, client = hse_manager
    created = await client.post("/api/v1/safety-assets", json={
        "name": "Extinguisher — ABC 10lb",
        "inspection_frequency": "quarterly",
    })
    assert created.status_code in (200, 201), created.text
    quarterly_due = date.fromisoformat(created.json()["next_due_on"])

    tightened = await client.patch(f"/api/v1/safety-assets/{created.json()['id']}", json={
        "inspection_frequency": "monthly",
    })
    assert tightened.status_code == 200, tightened.text
    monthly_due = date.fromisoformat(tightened.json()["next_due_on"])
    assert monthly_due < quarterly_due, (
        "monthly must fall due sooner than the quarterly date it replaced")

    # And taken off a cycle, nothing is owed at all.
    off = await client.patch(f"/api/v1/safety-assets/{created.json()['id']}", json={
        "inspection_frequency": None,
    })
    assert off.json()["next_due_on"] is None, (
        "an item nobody schedules must stop raising checks")


async def test_checking_the_same_item_twice_in_a_day_is_refused_not_a_crash(hse_manager):
    """One row per asset per day is a database constraint.

    A second check on the same item on the same day cannot be a second row,
    and inserting one answered 500 — which is what the third acceptance round
    hit: "internal server error on pass or fail entries". It is also what
    happens on the ordinary path, because the sweep raises a row for anything
    due today and the ad-hoc check then tried to make another.
    """
    _, client = hse_manager
    asset = (await client.post("/api/v1/safety-assets", json={
        "name": "Eyewash — packaging line", "inspection_frequency": "monthly",
    })).json()

    first = await client.post(f"/api/v1/safety-assets/{asset['id']}/inspections",
                              json={"result": "passed", "notes": "Flushed, clear."})
    assert first.status_code in (200, 201), first.text
    assert first.json()["result"] == "passed"
    assert first.json()["performed_by_name"], "a check has to say who did it"

    again = await client.post(f"/api/v1/safety-assets/{asset['id']}/inspections",
                              json={"result": "failed", "notes": "Second look."})
    assert again.status_code == 409, (
        f"expected a plain refusal, got {again.status_code}: {again.text}")
    assert "already checked" in again.json()["detail"]
    assert asset["asset_no"] in again.json()["detail"], (
        "the message has to name the item — somebody checking a row of "
        "extinguishers needs to know which one it means")

    # And the first result stands: a recorded result is the register's
    # evidence and is not replaced by a later attempt.
    checks = (await client.get(
        f"/api/v1/safety-assets/inspections?asset_id={asset['id']}")).json()
    done = [c for c in checks if c["performed_on"]]
    assert len(done) == 1 and done[0]["result"] == "passed"


# ── The prompt and the obligation ───────────────────────────────────────────

async def test_filing_the_report_closes_the_task_that_asked_for_it(hse_manager, db_session):
    """★ Satisfying an obligation left its inbox task open for good.

    HSE could file with the Ministry, upload the receipt, and still be told to
    do it. Nothing closed a statutory task — the sweep only raised them — and
    nobody saw the pile because tasks assigned to a role rather than to a
    person reached no screen at all.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from app.models.statutory import StatutoryDeadline
    from app.services import deadlines as deadline_service
    from app.services import tasks as task_service

    manager, client = hse_manager
    source_id = _uuid.uuid4()
    now = datetime.now(timezone.utc)
    deadline = StatutoryDeadline(
        id=_uuid.uuid4(), kind="wsib_form7", source_type="incident",
        source_id=source_id, source_ref="INC-2026-9001",
        due_at=now + timedelta(hours=4), regulation_ref="WSIA s.21",
        clock_type="business", starts_at=now,
    )
    db_session.add(deadline)
    await task_service.open_task(
        db_session,
        task_type=task_service.TASK_STATUTORY,
        doc_type=task_service.DOC_INCIDENT,
        doc_id=source_id,
        doc_number="INC-2026-9001",
        title=f"{deadline_service.task_lead('wsib_form7')}due in 4h",
        assigned_role="ehs_manager",
    )
    # A second obligation on the same record. Filing one does not discharge the
    # other, and the assertion below is what keeps them apart.
    await task_service.open_task(
        db_session,
        task_type=task_service.TASK_STATUTORY,
        doc_type=task_service.DOC_INCIDENT,
        doc_id=source_id,
        doc_number="INC-2026-9001",
        title=f"{deadline_service.task_lead('mol_48h')}overdue",
        assigned_role="ehs_manager",
    )
    await db_session.commit()

    before = {t["title"] for t in (await client.get("/api/v1/actions/mine/tasks")).json()}
    assert any(t.startswith("wsib form7") for t in before)

    await deadline_service.satisfy(
        db_session, deadline, user_id=manager.id, user_name=manager.full_name,
        evidence_note="Filed and acknowledged.", when=now)
    await db_session.commit()

    after = {t["title"] for t in (await client.get("/api/v1/actions/mine/tasks")).json()}
    assert not any(t.startswith("wsib form7") for t in after), (
        "the prompt to file it has to go when it has been filed")
    assert any(t.startswith("mol 48h") for t in after), (
        "the Ministry report is a different obligation and is still owed")


async def test_escalating_rewords_the_prompt_rather_than_adding_one(hse_manager, db_session):
    """★ One obligation, one prompt.

    Each escalation used to raise another task, so a deadline that crossed all
    three thresholds left three saying the same thing in three tones. On the
    test database thirty-six open tasks stood for twelve incidents, one of them
    holding six.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from app.models.statutory import StatutoryDeadline
    from app.tasks.scheduler import sweep_statutory

    _, client = hse_manager
    source_id = _uuid.uuid4()
    now = datetime.now(timezone.utc)
    db_session.add(StatutoryDeadline(
        id=_uuid.uuid4(), kind="wsib_form7", source_type="incident",
        source_id=source_id, source_ref="INC-2026-9002",
        due_at=now + timedelta(hours=20), regulation_ref="WSIA s.21",
        clock_type="business", starts_at=now,
    ))
    await db_session.commit()

    def theirs(rows):
        return [t for t in rows if t["document_number"] == "INC-2026-9002"]

    # Three sweeps, at widening lateness: due tomorrow, due shortly, overdue.
    for at in (now, now + timedelta(hours=18), now + timedelta(hours=30)):
        await sweep_statutory(db_session, at)
        await db_session.commit()

    rows = theirs((await client.get("/api/v1/actions/mine/tasks")).json())
    assert len(rows) == 1, f"one obligation, one prompt — got {[r['title'] for r in rows]}"
    assert "overdue" in rows[0]["title"], "and the one prompt says where it now stands"


class TestEquipmentCheckedOnItsOwnForm:
    """★ Round 4, TC-07: "allow for an equipment qr code to be linked to a
    specific form. for example when a user scans the eye wash station qr code the
    eye wash inspection form shall open with the name of the station on the form
    automatic … there should be a pass of fail criteria set to ensure the item is
    inspected properly."

    `inspection_template_id` has been on the asset since the first migration, the
    API accepted it and the crud stored it — and nothing set it, nothing read it,
    and eleven items on the dev database had none. This is the wiring.

    The pass/fail half is the point of it: the form's own fail conditions decide
    whether the item passed, so a failed answer takes it out of service through
    the same path as a check recorded by hand.
    """

    @pytest.fixture
    async def ladder_form(self, hse_manager):
        """A published template with a question that can fail."""
        _, client = hse_manager
        made = await client.post("/api/v1/forms/templates", json={
            "code": f"ladder-{uuid.uuid4().hex[:5]}", "name": "Ladder check",
            "category": "inspection"})
        assert made.status_code in (200, 201), made.text
        template = made.json()
        saved = await client.put(f"/api/v1/forms/templates/{template['id']}/schema", json={
            "schema": [{"key": "rungs", "type": "yes_no_na",
                        "label": "Rungs undamaged", "required": True,
                        "fail_on": {"equals": "no"}}]})
        assert saved.status_code in (200, 201), saved.text
        published = await client.post(
            f"/api/v1/forms/templates/{template['id']}/publish", json={})
        assert published.status_code in (200, 201), published.text
        return template

    @pytest.fixture
    async def ladder(self, hse_manager, ladder_form):
        _, client = hse_manager
        made = await client.post("/api/v1/safety-assets", json={
            "name": "Ladder by the dryer",
            "inspection_frequency": "monthly",
            "inspection_template_id": ladder_form["id"],
        })
        assert made.status_code in (200, 201), made.text
        return made.json()

    async def test_the_template_reports_its_pass_fail_questions(
            self, hse_manager, ladder_form):
        """★ So that whoever links a form can see whether it can fail at all.

        A template with no fail conditions records a pass whatever was observed,
        and two of the templates on the dev database are exactly that.
        """
        _, client = hse_manager
        rows = {t["code"]: t for t in (await client.get("/api/v1/forms/templates")).json()}
        mine = rows[ladder_form["code"]]
        assert mine["published"] is True
        assert mine["fail_criteria"] == 1

    async def test_starting_it_names_the_item_and_reopens_the_same_one(
            self, hse_manager, ladder):
        _, client = hse_manager
        first = await client.post(
            f"/api/v1/safety-assets/{ladder['id']}/inspection-form/start")
        assert first.status_code == 200, first.text
        submission_id = first.json()["submission_id"]

        sub = (await client.get(f"/api/v1/forms/submissions/{submission_id}")).json()
        assert sub["source_type"] == "asset"
        assert sub["source_id"] == ladder["id"]
        assert "Ladder by the dryer" in (sub["source_ref"] or ""), sub["source_ref"]

        # ★ A second scan reopens the unfinished one.
        second = await client.post(
            f"/api/v1/safety-assets/{ladder['id']}/inspection-form/start")
        assert second.json()["submission_id"] == submission_id

    async def test_a_failed_answer_records_a_failed_check(self, hse_manager, ladder):
        """★ The criteria doing their job. A "no" on the one question that can
        fail is a failed inspection, recorded against the item."""
        _, client = hse_manager
        started = await client.post(
            f"/api/v1/safety-assets/{ladder['id']}/inspection-form/start")
        sid = started.json()["submission_id"]

        assert (await client.put(f"/api/v1/forms/submissions/{sid}/answers",
                                 json={"values": {"rungs": "no"}})).status_code == 200
        assert (await client.post(f"/api/v1/forms/submissions/{sid}/submit")).status_code == 200

        checks = (await client.get(
            f"/api/v1/safety-assets/inspections?asset_id={ladder['id']}")).json()
        assert checks, "submitting the form recorded no check against the item"
        # ★ "failed", as every other path writes it: this test used to pin
        # "fail", which the out-of-service rule never matched (round 10).
        assert checks[0]["result"] == "failed", checks[0]
        assert checks[0]["submission_id"] == sid
        item = (await client.get(f"/api/v1/safety-assets/{ladder['id']}")).json()
        assert item["status"] == "out_of_service"

    async def test_a_clean_answer_records_a_pass(self, hse_manager, ladder):
        """★ The admission. Recording everything as a failure would satisfy the
        test above."""
        _, client = hse_manager
        started = await client.post(
            f"/api/v1/safety-assets/{ladder['id']}/inspection-form/start")
        sid = started.json()["submission_id"]

        assert (await client.put(f"/api/v1/forms/submissions/{sid}/answers",
                                 json={"values": {"rungs": "yes"}})).status_code == 200
        assert (await client.post(f"/api/v1/forms/submissions/{sid}/submit")).status_code == 200

        checks = (await client.get(
            f"/api/v1/safety-assets/inspections?asset_id={ladder['id']}")).json()
        assert checks[0]["result"] == "passed", checks[0]
        item = (await client.get(f"/api/v1/safety-assets/{ladder['id']}")).json()
        assert item["status"] == "in_service"

    async def test_an_item_with_no_form_says_so(self, hse_manager):
        """Rather than opening something else. Recording a check against the
        wrong thing is worse than being told there is no form."""
        _, client = hse_manager
        made = await client.post("/api/v1/safety-assets", json={
            "name": "Extinguisher with no form", "inspection_frequency": "monthly"})
        assert made.status_code in (200, 201), made.text
        refused = await client.post(
            f"/api/v1/safety-assets/{made.json()['id']}/inspection-form/start")
        assert refused.status_code == 409, refused.text
        assert "not linked to an inspection form" in refused.json()["detail"]
