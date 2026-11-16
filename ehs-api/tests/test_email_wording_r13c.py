"""UAT 13: email wording HSE can change, and what a corrective action email
says (R13-C)."""
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.models.communication import Communication
from app.services import email_templates as et
from app.services.day_range import plant_today
from tests.conftest import make_user
from tests.test_incident_anonymity_and_edit import _report
import pytest


@pytest.fixture(autouse=True)
async def _config(db_session):
    from app.models.config import EhsConfig
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.commit()


async def _capa_mail(db, action_id: str, incident_id: str) -> Communication:
    rows = list((await db.execute(select(Communication).where(
        Communication.doc_id == uuid.UUID(incident_id),
        Communication.template_key == "action_owner_told"))).scalars())
    assert len(rows) == 1, rows
    return rows[0]


async def _raise(hse, iid, owner, **kw):
    made = await hse.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": iid, "title": "Fit an interlocked guard",
        "description": "Order it from the OEM <a href='https://evil'>here</a>",
        "hierarchy_of_control": "engineering", "owner_id": str(owner.id), "priority": "high",
        "due_date": (plant_today() + timedelta(days=7)).isoformat(), **kw})
    assert made.status_code == 201, made.text
    return made.json()


async def test_the_corrective_action_email_says_what_kirk_listed(test_engine, db_session, hse_manager):
    """"the priority, the original incident number, CAPA number, the 'what will
    be done', the detail, and the due date. The incident and CAPA numbers are
    clickable." """
    _, hse = hse_manager
    _, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    incident = (await hse.get(f"/api/v1/incidents/{iid}")).json()
    owner = await make_user(test_engine, role="area_supervisor", full_name="Olive Owner")
    action = await _raise(hse, iid, owner)
    m = await _capa_mail(db_session, action["id"], iid)

    for must in ("High", incident["incident_no"], action["action_no"], "Fit an interlocked guard",
                 "Order it from the OEM", (plant_today() + timedelta(days=7)).strftime("%-d %B %Y")):
        assert must in m.body_text, must
    import re
    assert re.search(rf'href="[^"]*/actions/{action["id"]}"[^>]*>{action["action_no"]}</a>', m.body_html)
    assert re.search(rf'href="[^"]*/incidents/{iid}"[^>]*>{incident["incident_no"]}</a>', m.body_html)
    # What somebody typed is still only text.
    assert "<a href='https://evil'>" not in m.body_html and "&lt;a href=" in m.body_html


async def test_hse_change_the_wording_and_the_next_email_uses_it(
        test_engine, db_session, hse_manager, worker):
    _, hse = hse_manager
    _, emp = worker
    got = await hse.get("/api/v1/settings/email-templates")
    assert got.status_code == 200, got.text
    listed = got.json()
    assert {t["key"] for t in listed} == set(et.TEMPLATES)
    assert (await emp.get("/api/v1/settings/email-templates")).status_code == 403

    url = "/api/v1/settings/email-templates/action_owner_told"
    bad = await hse.put(url, json={"subject": "CAPA {action_no}", "body": "Dear {ownr_name}"})
    assert bad.status_code == 422 and "{ownr_name}" in bad.json()["detail"]
    unpaired = await hse.put(url, json={"subject": "CAPA {action_no", "body": "x"})
    assert unpaired.status_code == 422 and "pair" in unpaired.json()["detail"]

    preview = (await hse.post(f"{url}/preview", json={
        "subject": "Action {action_no} for {owner_name}",
        "body": "Please do {title} by {due_date} ({priority})."})).json()
    assert preview["problems"] == [] and preview["subject"] == "Action ACT-2026-0142 for Dario Okafor"
    assert "Fit an interlocked guard to the capping head" in preview["html"]

    saved = await hse.put(url, json={"subject": "Action {action_no} for {owner_name}",
                                     "body": "Please do {title} by {due_date} ({priority})."})
    assert saved.status_code == 200 and saved.json()["customised"] is True

    _, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    owner = await make_user(test_engine, full_name="Wendy Wording")
    action = await _raise(hse, iid, owner)
    m = await _capa_mail(db_session, action["id"], iid)
    assert m.subject == f"Action {action['action_no']} for Wendy Wording"
    assert m.body_text.startswith("Please do Fit an interlocked guard by")

    reset = (await hse.delete(url)).json()
    assert reset["customised"] is False and reset["subject"] == et.TEMPLATES["action_owner_told"].subject


def test_every_default_uses_only_its_own_placeholders():
    for key, t in et.TEMPLATES.items():
        assert et.problems(key, t.subject, t.body) == [], key
        # and its example values fill every one of them
        assert set(et.placeholders_in(t.subject + t.body)) <= set(t.sample), key

