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
