"""Searching on the Reports tab: by incident type, by toolbox talk, and the
records behind a bar.

Round 5 feedback (Ken Boucher, 1 Oct): "Can you please modify the reporting
ability to add additional fuctionality, searching by type of incident, toolbox
talks etc."

Every test plants its records in an area of its own and narrows to it, because
the test database is shared and other tests file incidents too.
"""
import uuid
from datetime import date, datetime, timezone

from app.models.incident import Incident
from app.models.toolbox import TbtDelivery, ToolboxTalk
from app.services import reporting
from tests.conftest import authed_client, make_token, make_user


def _area() -> str:
    return f"Test area {uuid.uuid4().hex[:8]}"


async def _incident(db, *, area: str, kind: str | None, form: str = "medical",
                    status: str = "submitted", at: datetime | None = None) -> Incident:
    i = Incident(id=uuid.uuid4(), incident_no=f"INC-T-{uuid.uuid4().hex[:6]}",
                 form_kind=form, title=f"A {kind or 'untyped'} case", status=status,
                 location_path=area,
                 occurred_at=at or datetime(2026, 9, 10, 14, tzinfo=timezone.utc),
                 extra={"incident_type": kind} if kind else {})
    db.add(i)
    await db.flush()
    return i


def _counts(rows) -> dict:
    return {r["label"]: r["count"] for r in rows}


class TestByIncidentType:
    async def test_grouped_by_the_twelve_types_with_their_names(self, hse_manager, db_session):
        _, client = hse_manager
        area = _area()
        for kind in ("first_aid", "first_aid", "unconscious"):
            await _incident(db_session, area=area, kind=kind)
        await _incident(db_session, area=area, kind=None)

        got = await client.get("/api/v1/reports/group", params={
            "entity": "incident", "dimension": "incident_type", "filter": f"location:{area}"})
        assert got.status_code == 200, got.text
        # The short name, not the whole line the form shows.
        assert _counts(got.json()) == {
            "First Aid Injury": 2, "Unconscious": 1, "Not recorded": 1}

    async def test_narrowed_to_a_type_then_grouped_by_something_else(
            self, hse_manager, db_session):
        _, client = hse_manager
        area = _area()
        await _incident(db_session, area=area, kind="first_aid", status="submitted")
        await _incident(db_session, area=area, kind="first_aid", status="closed")
        await _incident(db_session, area=area, kind="lost_time", status="closed")

        got = await client.get("/api/v1/reports/group", params=[
            ("entity", "incident"), ("dimension", "status"),
            ("filter", f"location:{area}"), ("filter", "incident_type:first_aid")])
        assert got.status_code == 200, got.text
        assert _counts(got.json()) == {"submitted": 1, "closed": 1}

    async def test_the_not_recorded_bar_can_be_opened_too(self, hse_manager, db_session):
        _, client = hse_manager
        area = _area()
        await _incident(db_session, area=area, kind=None)
        await _incident(db_session, area=area, kind="first_aid")

        got = await client.get("/api/v1/reports/group", params=[
            ("entity", "incident"), ("dimension", "form_kind"),
            ("filter", f"location:{area}"), ("filter", "incident_type:")])
        assert _counts(got.json()) == {"Injury or illness": 1}

    async def test_a_period_still_applies(self, hse_manager, db_session):
        _, client = hse_manager
        area = _area()
        await _incident(db_session, area=area, kind="first_aid",
                        at=datetime(2025, 6, 1, 12, tzinfo=timezone.utc))
        await _incident(db_session, area=area, kind="first_aid",
                        at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))
        got = await client.get("/api/v1/reports/group", params={
            "entity": "incident", "dimension": "incident_type",
            "filter": f"location:{area}", "date_from": "2026-01-01", "date_to": "2027-01-01"})
        assert _counts(got.json()) == {"First Aid Injury": 1}

    async def test_this_year_against_last_narrows_the_same_way(self, db_session):
        area = _area()
        await _incident(db_session, area=area, kind="first_aid",
                        at=datetime(2025, 6, 1, 12, tzinfo=timezone.utc))
        await _incident(db_session, area=area, kind="first_aid",
                        at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))
        await _incident(db_session, area=area, kind="lost_time",
                        at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))
        rows = await reporting.compare_periods(
            db_session, entity="incident", dimension="incident_type",
            period_a=(date(2025, 1, 1), date(2026, 1, 1)),
            period_b=(date(2026, 1, 1), date(2027, 1, 1)),
            filters={"location": area, "incident_type": "first_aid"})
        assert [(r["label"], r["period_a"], r["period_b"]) for r in rows] == [
            ("First Aid Injury", 1, 1)]


class TestTheFilterIsNotSql:
    async def test_an_unknown_dimension_is_refused(self, hse_manager):
        _, client = hse_manager
        got = await client.get("/api/v1/reports/group", params={
            "entity": "incident", "dimension": "status", "filter": "title:x"})
        assert got.status_code == 422 and "cannot be narrowed by 'title'" in got.text

    async def test_a_filter_without_a_colon_is_refused(self, hse_manager):
        _, client = hse_manager
        got = await client.get("/api/v1/reports/group", params={
            "entity": "incident", "dimension": "status", "filter": "status"})
        assert got.status_code == 422

    async def test_a_value_is_a_value(self, hse_manager, db_session):
        """★ A quote in the value is matched as text, not run."""
        _, client = hse_manager
        area = _area()
        await _incident(db_session, area=area, kind="first_aid")
        got = await client.get("/api/v1/reports/group", params=[
            ("entity", "incident"), ("dimension", "status"),
            ("filter", f"location:{area}"), ("filter", "incident_type:x' or '1'='1")])
        assert got.status_code == 200 and got.json() == []


class TestTheRecordsBehindABar:
    async def test_newest_first_with_where_each_opens(self, hse_manager, db_session):
        _, client = hse_manager
        area = _area()
        old = await _incident(db_session, area=area, kind="first_aid",
                              at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
        new = await _incident(db_session, area=area, kind="first_aid",
                              at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
        await _incident(db_session, area=area, kind="lost_time")

        got = await client.get("/api/v1/reports/records", params=[
            ("entity", "incident"), ("filter", f"location:{area}"),
            ("filter", "incident_type:first_aid")])
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["total"] == 2
        assert [r["ref"] for r in body["rows"]] == [new.incident_no, old.incident_no]
        assert body["rows"][0]["path"] == f"/incidents/{new.id}"

    async def test_a_capped_list_says_how_many_there_are(
            self, hse_manager, db_session, monkeypatch):
        _, client = hse_manager
        monkeypatch.setattr(reporting, "RECORDS_LIMIT", 2)
        area = _area()
        for _ in range(3):
            await _incident(db_session, area=area, kind="first_aid")
        body = (await client.get("/api/v1/reports/records", params={
            "entity": "incident", "filter": f"location:{area}"})).json()
        assert body["total"] == 3 and len(body["rows"]) == 2

    async def test_an_entity_with_nothing_to_list_says_so(self, hse_manager):
        _, client = hse_manager
        got = await client.get("/api/v1/reports/records", params={"entity": "risk"})
        assert got.status_code == 422 and "not listed" in got.text

    async def test_training_records_are_held_to_the_training_key(
            self, test_engine, db_session):
        """★ The counts are harmless; names against courses are somebody's
        training file. A JHSC member reads incidents but not training."""
        jhsc = await make_user(test_engine, role="jhsc_member")
        sup = await make_user(test_engine, role="area_supervisor")
        async with authed_client(make_token(jhsc.id, jhsc.role), db_session) as c:
            assert (await c.get("/api/v1/reports/records",
                                params={"entity": "training"})).status_code == 403
            # …and the same person is not simply shut out of the list.
            assert (await c.get("/api/v1/reports/records",
                                params={"entity": "incident"})).status_code == 200
        async with authed_client(make_token(sup.id, sup.role), db_session) as c:
            assert (await c.get("/api/v1/reports/records",
                                params={"entity": "training"})).status_code == 200

    async def test_a_worker_cannot_list_anything(self, worker):
        _, client = worker
        got = await client.get("/api/v1/reports/records", params={"entity": "incident"})
        assert got.status_code == 403


class TestByToolboxTalk:
    async def test_by_talk_and_by_supervisor(self, hse_manager, db_session):
        _, client = hse_manager
        tag = uuid.uuid4().hex[:6]
        talk = ToolboxTalk(id=uuid.uuid4(), talk_no=f"TBT-T-{tag}", title=f"Ladders {tag}",
                           period_month=date(2026, 10, 1), is_published=True)
        db_session.add(talk)
        await db_session.flush()
        for name, status in ((f"Sup A {tag}", "delivered"), (f"Sup B {tag}", "pending"),
                             (f"Sup B {tag}", "delivered")):
            db_session.add(TbtDelivery(id=uuid.uuid4(), talk_id=talk.id,
                                       supervisor_name=name, status=status))
        await db_session.flush()

        by_talk = await client.get("/api/v1/reports/group", params={
            "entity": "toolbox_talk", "dimension": "status", "filter": f"talk:{talk.id}"})
        assert by_talk.status_code == 200, by_talk.text
        assert _counts(by_talk.json()) == {"delivered": 2, "pending": 1}

        named = await client.get("/api/v1/reports/group", params={
            "entity": "toolbox_talk", "dimension": "talk",
            "filter": f"supervisor:Sup B {tag}"})
        assert _counts(named.json()) == {f"Ladders {tag}": 2}

        listed = (await client.get("/api/v1/reports/records", params={
            "entity": "toolbox_talk", "filter": f"talk:{talk.id}"})).json()
        assert listed["total"] == 3
        assert all(r["ref"] == talk.talk_no for r in listed["rows"])
        assert any(r["title"] == f"Ladders {tag} — Sup A {tag}" for r in listed["rows"])
        assert listed["rows"][0]["path"].startswith("/toolbox-talks?delivery=")


async def test_every_entity_lists_and_groups_on_the_real_tables(hse_manager):
    """Each SQL fragment in the entity table runs, rather than the four the
    tests above happen to touch."""
    _, client = hse_manager
    for ent in reporting.ENTITIES:
        for dim in ent.dimensions:
            got = await client.get("/api/v1/reports/group",
                                   params={"entity": ent.key, "dimension": dim})
            assert got.status_code == 200, (ent.key, dim, got.text)
        if ent.ref is not None:
            got = await client.get("/api/v1/reports/records", params={"entity": ent.key})
            assert got.status_code == 200, (ent.key, got.text)
        # Narrowing by every grouping too: the filter writes the same fragment
        # into a WHERE, where the table alias matters.
        for dim in ent.dimensions:
            got = await client.get("/api/v1/reports/group", params={
                "entity": ent.key, "dimension": ent.dimensions[0],
                "filter": f"{dim}:2026-09"})
            assert got.status_code == 200, (ent.key, dim, got.text)


class TestADraftIsNotAnIncidentYet:
    """★ Every form saves a draft (HSE's P0, for the WiFi dead spots). Counted,
    one unsent report moves every bar and TRIR; listed, it shows the title of
    somebody's unsent report to anyone who reads the register. Raised in review
    by uniops-41 — dev had no draft incidents, so nothing else would have shown it.
    """

    async def test_not_counted_and_not_listed(self, hse_manager, db_session):
        _, client = hse_manager
        area = _area()
        await _incident(db_session, area=area, kind="first_aid", status="draft")
        sent = await _incident(db_session, area=area, kind="first_aid", status="submitted")

        got = await client.get("/api/v1/reports/group", params={
            "entity": "incident", "dimension": "incident_type", "filter": f"location:{area}"})
        assert _counts(got.json()) == {"First Aid Injury": 1}
        listed = (await client.get("/api/v1/reports/records", params={
            "entity": "incident", "filter": f"location:{area}"})).json()
        assert [r["ref"] for r in listed["rows"]] == [sent.incident_no]

    async def test_not_in_the_first_aid_slice_either(self, db_session):
        """The slice already had a condition of its own; both must hold."""
        area = _area()
        i = await _incident(db_session, area=area, kind="first_aid", status="draft")
        i.injury_class = "first_aid"
        await db_session.flush()
        rows = await reporting.group_by(db_session, entity="first_aid", dimension="status",
                                        filters={"location": area})
        assert rows == []

    async def test_not_in_the_injury_rates(self, db_session):
        far = (date(2031, 1, 1), date(2032, 1, 1))
        before = await reporting.injury_rates(db_session, hours_worked=200_000,
                                              date_from=far[0], date_to=far[1])
        i = await _incident(db_session, area=_area(), kind="lost_time", status="draft",
                            at=datetime(2031, 3, 1, 12, tzinfo=timezone.utc))
        i.injury_class = "lost_time"
        await db_session.flush()
        after = await reporting.injury_rates(db_session, hours_worked=200_000,
                                             date_from=far[0], date_to=far[1])
        assert after["lost_time"] == before["lost_time"]
        assert after["recordable"] == before["recordable"]

        i.status = "submitted"
        await db_session.flush()
        sent = await reporting.injury_rates(db_session, hours_worked=200_000,
                                            date_from=far[0], date_to=far[1])
        assert sent["lost_time"] == before["lost_time"] + 1

    async def test_not_on_the_dashboard(self, db_session):
        from app.services import dashboard
        year = 2031
        before = await dashboard.headline(db_session, year=year, hours_worked=None)
        months_before = await dashboard.medical_events_by_month(db_session, year=year)
        i = await _incident(db_session, area=_area(), kind="first_aid", status="draft",
                            at=datetime(year, 5, 2, 12, tzinfo=timezone.utc))
        i.injury_class = "first_aid"
        await db_session.flush()

        assert (await dashboard.headline(db_session, year=year, hours_worked=None))[
            "first_aid"] == before["first_aid"]
        assert await dashboard.medical_events_by_month(db_session, year=year) == months_before
        assert all(r["label"] != i.location_path for r in
                   await dashboard.injured_by_location(db_session, year=year))

        i.status = "submitted"
        await db_session.flush()
        assert (await dashboard.headline(db_session, year=year, hours_worked=None))[
            "first_aid"] == before["first_aid"] + 1

    async def test_an_unsent_inspection_is_not_counted(self, hse_manager, db_session):
        from sqlalchemy import text as sql
        _, client = hse_manager
        made = await client.post("/api/v1/forms/templates", json={
            "code": f"r-{uuid.uuid4().hex[:5]}", "name": "Scratch", "category": "inspection"})
        t = made.json()
        await client.put(f"/api/v1/forms/templates/{t['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Sound?"}]})
        await client.post(f"/api/v1/forms/templates/{t['id']}/publish", json={"change_note": "v1"})
        sub = (await client.post(f"/api/v1/forms/templates/{t['id']}/submissions",
                                 json={})).json()
        assert sub["status"] == "draft"

        params = {"entity": "inspection", "dimension": "status", "filter": f"form:{t['id']}"}
        assert (await client.get("/api/v1/reports/group", params=params)).json() == []

        await db_session.execute(sql(
            "update ehs_form_submissions set status = 'submitted' where id = :i"),
            {"i": sub["id"]})
        assert _counts((await client.get("/api/v1/reports/group", params=params)).json()) == {
            "submitted": 1}
