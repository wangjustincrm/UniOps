"""UAT 14: "on the email wording registry, include all email templates. new
incident is missing." """
import re
import uuid

import pytest
from sqlalchemy import select

from app.models.communication import Communication
from app.services import email_templates as et
from tests.conftest import authed_client, make_token, make_user
from tests.test_incident_anonymity_and_edit import _report


@pytest.fixture(autouse=True)
async def _config(db_session):
    from app.models.config import EhsConfig
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.commit()


async def _mails(db, key: str, doc_id=None) -> list[Communication]:
    q = select(Communication).where(Communication.template_key == key)
    if doc_id is not None:
        q = q.where(Communication.doc_id == uuid.UUID(str(doc_id)))
    return list((await db.execute(q)).scalars())


EVERY_EMAIL = {
    "incident_reported", "incident_status", "statutory_escalation",
    "action_owner_told", "action_reassigned", "action_escalation",
    "jhsc_recommendation", "jhsc_recommendation_sent", "jhsc_recommendation_answered",
    "jhsc_minutes", "jhsc_reassigned", "toolbox_chase", "inspection_due",
    "course_reminder", "cert_expiry_notice", "document_published",
    "feedback_left", "feedback_answered", "work_reassigned",
}


async def test_every_email_is_listed_under_a_heading(hse_manager):
    _, hse = hse_manager
    listed = (await hse.get("/api/v1/settings/email-templates")).json()
    assert {t["key"] for t in listed} == EVERY_EMAIL
    assert all(t["group"] for t in listed)
    new = next(t for t in listed if t["key"] == "incident_reported")
    assert new["group"] == "Incidents" and "{incident_no}" in new["subject"]


async def test_a_new_incident_email_in_hse_words(test_engine, db_session, hse_manager):
    """HSE's wording reaches the email: the kind and title they asked for, the
    number a link, and the Ministry line left out on one that is not
    reportable."""
    _, hse = hse_manager
    saved = await hse.put("/api/v1/settings/email-templates/incident_reported", json={
        "subject": "New {kind}: {incident_no}",
        "body": "A {kind} was reported: {incident_no}.\n\nTitle: {title}\n"
                "Ministry: {ministry_note}\n\nPlease review it today."})
    assert saved.status_code == 200, saved.text

    _, iid = await _report(test_engine, db_session, anonymous=False)
    got = await _mails(db_session, "incident_reported", iid)
    assert len(got) == 1, got
    m = got[0]
    no = (await hse.get(f"/api/v1/incidents/{iid}")).json()["incident_no"]
    assert m.subject == f"New First Aid Injury: {no}"
    assert "Title: Slipped by the dryer" in m.body_text
    assert "Ministry:" not in m.body_text          # empty → the line is left out
    assert "Please review it today." in m.body_text  # a line with no placeholder stays
    assert re.search(rf'href="[^"]*/incidents/{iid}"[^>]*>{no}</a>', m.body_html)


async def test_the_default_new_incident_email_is_unchanged(test_engine, db_session, hse_manager):
    _, iid = await _report(test_engine, db_session, anonymous=False)
    m = (await _mails(db_session, "incident_reported", iid))[0]
    no = m.subject.removeprefix("Safety incident ")
    assert m.body_text.startswith(f"{no} was reported at ")
    assert "Wet floor, no sign." in m.body_text
    assert "Recorded as: first aid." in m.body_text


async def test_feedback_email_in_hse_words(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    assert (await hse.put("/api/v1/settings/email-templates/feedback_left", json={
        "subject": "Feedback: {who}", "body": "{comment}\n\nPage: {page}"})).status_code == 200
    me = await make_user(test_engine, role="requester", full_name="Fay Feedback")
    async with authed_client(make_token(me.id, me.role), db_session) as c:
        assert (await c.post("/api/v1/feedback", json={"body": "Ladder is cracked"})).status_code in (200, 201)
    m = [x for x in await _mails(db_session, "feedback_left") if "Ladder is cracked" in x.body_text][0]
    assert m.subject == "Feedback: Fay Feedback"
    assert "Page:" not in m.body_text


def test_a_line_is_left_out_only_when_all_its_placeholders_are_empty():
    text = "A {a}\nB {a}{b}\nC\n\n\nD {b}"
    assert et.fill(text, {"a": "", "b": "x"}) == "B x\nC\n\nD x"
    assert et.fill(text, {"a": "", "b": ""}) == "C\n\n"
    # The subject is one line and never left out.
    assert et.fill("Tomorrow: {course}", {"course": ""}, drop_empty_lines=False) == "Tomorrow: "


async def test_the_preview_links_the_incident_number(hse_manager):
    _, hse = hse_manager
    t = et.TEMPLATES["incident_reported"]
    got = (await hse.post("/api/v1/settings/email-templates/incident_reported/preview",
                          json={"subject": t.subject, "body": t.body})).json()
    assert got["problems"] == []
    assert re.search(r'href="#"[^>]*>INC-2026-0134</a>', got["html"])
