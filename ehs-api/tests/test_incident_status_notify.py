"""Who is told when an incident's status changes — and when it is first sent.

Round 5, TC-15: "provide a way to manage the recipients list on incidents when
every status changes on an incident form. add this to the list of 'who is
told'."
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.communication import Communication
from tests.test_settings import config_row  # noqa: F401 — the fixture
from tests.conftest import make_user

# Every test here reads or writes the settings row, which the test database
# does not have until something makes it.
pytestmark = pytest.mark.usefixtures("config_row")


async def _mail(db, incident_id: str, key: str) -> list[Communication]:
    return list((await db.execute(select(Communication).where(
        Communication.doc_id == uuid.UUID(incident_id),
        Communication.template_key == key))).scalars())


async def _incident(client, *, title: str = "Forklift into the racking") -> str:
    made = await client.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": title,
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "incident_type": "property_damage"})
    assert made.status_code in (200, 201), made.text
    return made.json()["id"]


async def _name_for(client, event: str, *people) -> None:
    saved = await client.put("/api/v1/settings/config", json={
        "incident_status_notify": {event: [str(p.id) for p in people]}})
    assert saved.status_code == 200, saved.text


class TestTheFirstNoticeGoesOnSubmit:
    """★ It went on create. A report whose create landed and whose submit did
    not left HSE a notice about a draft, and the submit after it sent nothing."""

    async def test_a_draft_tells_nobody_and_sending_it_tells_once(
            self, hse_manager, db_session):
        _, client = hse_manager
        iid = await _incident(client)
        assert await _mail(db_session, iid, "incident_reported") == []

        assert (await client.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200
        assert len(await _mail(db_session, iid, "incident_reported")) == 1

        # Sending it again is refused, and says nothing a second time.
        assert (await client.post(f"/api/v1/incidents/{iid}/submit")).status_code == 409
        assert len(await _mail(db_session, iid, "incident_reported")) == 1


class TestTheListsAreChecked:
    async def test_an_unknown_change_is_refused(self, hse_manager):
        _, client = hse_manager
        got = await client.put("/api/v1/settings/config", json={
            "incident_status_notify": {"submitted": []}})
        assert got.status_code == 422 and "not a status change" in got.text

    async def test_somebody_who_is_not_an_id_is_refused(self, hse_manager):
        _, client = hse_manager
        got = await client.put("/api/v1/settings/config", json={
            "incident_status_notify": {"closed": ["Marie"]}})
        assert got.status_code == 422

    async def test_what_is_saved_is_what_is_read_back(self, hse_manager, test_engine):
        _, client = hse_manager
        named = await make_user(test_engine, role="requester")
        await _name_for(client, "closed", named, named)
        cfg = (await client.get("/api/v1/settings/config")).json()
        # Named twice, kept once.
        assert cfg["incident_status_notify"] == {"closed": [str(named.id)]}

    async def test_only_hse_may_change_them(self, worker):
        _, client = worker
        got = await client.put("/api/v1/settings/config", json={
            "incident_status_notify": {"closed": []}})
        assert got.status_code == 403


class TestEachChange:
    async def test_investigation_started_once_not_on_every_reclassify(
            self, hse_manager, test_engine, db_session):
        _, client = hse_manager
        named = await make_user(test_engine, role="requester")
        await _name_for(client, "under_investigation", named)
        iid = await _incident(client)
        await client.post(f"/api/v1/incidents/{iid}/submit")

        first = await client.post(f"/api/v1/incidents/{iid}/classify",
                                  json={"injury_class": "first_aid"})
        assert first.status_code == 200, first.text
        sent = await _mail(db_session, iid, "incident_under_investigation")
        assert len(sent) == 1 and sent[0].to_addrs == [named.email]
        assert "investigation started" in sent[0].subject

        # A correction to an investigation already under way is not news.
        again = await client.post(f"/api/v1/incidents/{iid}/classify",
                                  json={"injury_class": "medical_aid"})
        assert again.status_code == 200, again.text
        assert len(await _mail(db_session, iid, "incident_under_investigation")) == 1

    async def test_reviewed_closed_and_reopened(self, hse_manager, test_engine, db_session):
        user, client = hse_manager
        named = await make_user(test_engine, role="requester")
        saved = await client.put("/api/v1/settings/config", json={"incident_status_notify": {
            "hse_reviewed": [str(named.id)], "closed": [str(named.id)],
            "reopened": [str(named.id)]}})
        assert saved.status_code == 200, saved.text
        iid = await _incident(client)
        await client.post(f"/api/v1/incidents/{iid}/submit")

        assert (await client.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200
        reviewed = await _mail(db_session, iid, "incident_hse_reviewed")
        assert len(reviewed) == 1 and user.full_name in reviewed[0].body_text

        assert (await client.post(f"/api/v1/incidents/{iid}/close")).status_code == 200
        assert len(await _mail(db_session, iid, "incident_closed")) == 1
        # Closing what is already closed is a no-op, and says nothing twice.
        assert (await client.post(f"/api/v1/incidents/{iid}/close")).status_code == 200
        assert len(await _mail(db_session, iid, "incident_closed")) == 1

        assert (await client.post(f"/api/v1/incidents/{iid}/reopen")).status_code == 200
        assert len(await _mail(db_session, iid, "incident_reopened")) == 1

    async def test_an_empty_list_sends_nothing(self, hse_manager, db_session):
        """These changes sent nothing before the lists existed; unlike a new
        report, nobody named means nobody is emailed."""
        _, client = hse_manager
        saved = await client.put("/api/v1/settings/config",
                                 json={"incident_status_notify": {}})
        assert saved.status_code == 200, saved.text
        iid = await _incident(client)
        await client.post(f"/api/v1/incidents/{iid}/submit")
        await client.post(f"/api/v1/incidents/{iid}/hse-review")
        await client.post(f"/api/v1/incidents/{iid}/close")
        for key in ("incident_hse_reviewed", "incident_closed"):
            assert await _mail(db_session, iid, key) == []

    async def test_what_somebody_typed_is_text_in_the_mail(
            self, hse_manager, test_engine, db_session):
        """The title is typed by whoever reported it; it goes out as text."""
        _, client = hse_manager
        named = await make_user(test_engine, role="requester")
        await _name_for(client, "hse_reviewed", named)
        iid = await _incident(client, title='<a href="https://x.example">Sign in</a>')
        await client.post(f"/api/v1/incidents/{iid}/submit")
        await client.post(f"/api/v1/incidents/{iid}/hse-review")
        [sent] = await _mail(db_session, iid, "incident_hse_reviewed")
        assert "<a href=\"https://x.example\"" not in sent.body_html
        assert "&lt;a href=" in sent.body_html


class TestTheReporterAndTheirSupervisor:
    """Round 6, TC-07: "a checkbox on all 'who is told' items to include the
    requester and the requester's supervisor"; and from the answers, "notify
    the reporter and their supervisor" on investigation and closing."""

    async def _people(self, test_engine, db):
        from app.models.mirrors import User
        boss = await make_user(test_engine, role="requester")
        reporter = await make_user(test_engine, role="requester")
        row = await db.get(User, reporter.id)
        row.supervisor_id = boss.id
        await db.flush()
        return reporter, boss

    async def _report_as(self, test_engine, db, reporter, *, anonymous=False, told=None):
        from tests.conftest import authed_client, make_token
        async with authed_client(make_token(reporter.id, reporter.role), db) as c:
            body = {"form_kind": "equipment", "title": "Forklift into the racking",
                    "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                    "incident_type": "property_damage", "is_anonymous": anonymous}
            if told:
                body["reported_to_id"] = str(told.id)
            made = await c.post("/api/v1/incidents", json=body)
            assert made.status_code in (200, 201), made.text
            iid = made.json()["id"]
            sent = await c.post(f"/api/v1/incidents/{iid}/submit")
            assert sent.status_code == 200, sent.text
        return iid

    async def test_a_closing_list_with_only_the_boxes_ticked(
            self, hse_manager, test_engine, db_session):
        _, hse = hse_manager
        reporter, boss = await self._people(test_engine, db_session)
        saved = await hse.put("/api/v1/settings/config", json={
            "incident_status_notify": {},
            "incident_notify_include": {"status:closed": {"reporter": True, "supervisor": True}}})
        assert saved.status_code == 200, saved.text
        iid = await self._report_as(test_engine, db_session, reporter)
        await hse.post(f"/api/v1/incidents/{iid}/hse-review")
        assert (await hse.post(f"/api/v1/incidents/{iid}/close")).status_code == 200
        [sent] = await _mail(db_session, iid, "incident_closed")
        assert sorted(sent.to_addrs) == sorted([reporter.email, boss.email])
        assert "the person who reported it and their supervisor" in sent.body_text

    async def test_unticked_tells_neither(self, hse_manager, test_engine, db_session):
        """The admission beside the denial: the same close, boxes off."""
        _, hse = hse_manager
        reporter, _ = await self._people(test_engine, db_session)
        await hse.put("/api/v1/settings/config", json={
            "incident_status_notify": {}, "incident_notify_include": {}})
        iid = await self._report_as(test_engine, db_session, reporter)
        await hse.post(f"/api/v1/incidents/{iid}/hse-review")
        await hse.post(f"/api/v1/incidents/{iid}/close")
        assert await _mail(db_session, iid, "incident_closed") == []

    async def test_a_new_report_copies_them_alongside_the_list(
            self, hse_manager, test_engine, db_session):
        _, hse = hse_manager
        reporter, boss = await self._people(test_engine, db_session)
        named = await make_user(test_engine, role="requester")
        await hse.put("/api/v1/settings/config", json={
            "incident_notify_groups": {"property_damage": [str(named.id)]},
            "incident_notify_include": {"property_damage": {"reporter": False, "supervisor": True}}})
        iid = await self._report_as(test_engine, db_session, reporter)
        [sent] = await _mail(db_session, iid, "incident_reported")
        assert sorted(sent.to_addrs) == sorted([named.email, boss.email])

    async def test_with_no_supervisor_recorded_the_person_they_told(
            self, hse_manager, test_engine, db_session):
        _, hse = hse_manager
        reporter = await make_user(test_engine, role="requester")
        told = await make_user(test_engine, role="requester")
        await hse.put("/api/v1/settings/config", json={
            "incident_status_notify": {},
            "incident_notify_include": {"status:under_investigation": {"supervisor": True}}})
        iid = await self._report_as(test_engine, db_session, reporter, told=told)
        await hse.post(f"/api/v1/incidents/{iid}/classify", json={"injury_class": "none"})
        [sent] = await _mail(db_session, iid, "incident_under_investigation")
        assert sent.to_addrs == [told.email]

    async def test_an_anonymous_report_copies_neither(
            self, hse_manager, test_engine, db_session):
        """★ Copying the reporter would put their address beside everybody
        else's; copying their supervisor would say who reported it."""
        _, hse = hse_manager
        reporter, boss = await self._people(test_engine, db_session)
        named = await make_user(test_engine, role="requester")
        await hse.put("/api/v1/settings/config", json={
            "incident_status_notify": {"closed": [str(named.id)]},
            "incident_notify_include": {"status:closed": {"reporter": True, "supervisor": True}}})
        iid = await self._report_as(test_engine, db_session, reporter, anonymous=True)
        await hse.post(f"/api/v1/incidents/{iid}/hse-review")
        await hse.post(f"/api/v1/incidents/{iid}/close")
        [sent] = await _mail(db_session, iid, "incident_closed")
        assert sent.to_addrs == [named.email]

    async def test_only_known_lists_and_flags(self, hse_manager):
        _, hse = hse_manager
        bad_key = await hse.put("/api/v1/settings/config", json={
            "incident_notify_include": {"status:submitted": {"reporter": True}}})
        assert bad_key.status_code == 422
        bad_flag = await hse.put("/api/v1/settings/config", json={
            "incident_notify_include": {"closed": {"manager": True}}})
        assert bad_flag.status_code == 422
