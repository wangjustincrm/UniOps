"""Round 12, FRM-01 (deleting a draft form) and FRM-03 (a one-person question
filled with whoever opens the form)."""
import uuid

import pytest
from sqlalchemy import select

from app.models.form import FormAnswer, FormSubmission
from app.models.schedule import FormScheduleRun
from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user


@pytest.fixture
async def template(hse_manager):
    _, client = hse_manager
    made = (await client.post("/api/v1/forms/templates", json={
        "code": f"frm-{uuid.uuid4().hex[:5]}", "name": "Walkabout", "category": "inspection",
    })).json()
    saved = await client.put(f"/api/v1/forms/templates/{made['id']}/schema", json={"schema": [
        {"key": "who", "type": "person", "label": "Inspector"},
        {"key": "notes", "type": "text", "label": "Notes"},
    ]})
    assert saved.status_code in (200, 201), saved.text
    assert (await client.post(f"/api/v1/forms/templates/{made['id']}/publish",
                              json={"change_note": "v1"})).status_code in (200, 201)
    return made


async def _start(client, template_id) -> dict:
    r = await client.post(f"/api/v1/forms/templates/{template_id}/submissions", json={})
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_my_own_draft_is_mine_to_delete(worker, template, db_session):
    _, client = worker
    sub = await _start(client, template["id"])
    await client.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                     json={"values": {"notes": "half done"}})
    assert (await client.get(f"/api/v1/forms/submissions/{sub['id']}")).json()["may_delete"]

    gone = await client.delete(f"/api/v1/forms/submissions/{sub['id']}")
    assert gone.status_code == 204, gone.text
    assert (await client.get(f"/api/v1/forms/submissions/{sub['id']}")).status_code == 404
    left = (await db_session.execute(select(FormAnswer).where(
        FormAnswer.submission_id == uuid.UUID(sub["id"])))).scalars().all()
    assert left == []


async def test_somebody_elses_draft_is_theirs_or_hses(worker, hse_manager, template,
                                                     test_engine, db_session):
    _, mine = worker
    other = await make_user(test_engine)
    async with authed_client(make_token(other.id, other.role), db_session) as theirs:
        sub = await _start(theirs, template["id"])
    refused = await mine.delete(f"/api/v1/forms/submissions/{sub['id']}")
    assert refused.status_code == 403
    # Still there.
    assert await db_session.get(FormSubmission, uuid.UUID(sub["id"])) is not None

    _, hse = hse_manager
    assert (await hse.get(f"/api/v1/forms/submissions/{sub['id']}")).json()["may_delete"]
    assert (await hse.delete(f"/api/v1/forms/submissions/{sub['id']}")).status_code == 204


async def test_a_shared_draft_is_only_hses_to_delete(worker, hse_manager, template, db_session):
    """An equipment check, a meeting's minutes or a scheduled form: not any one
    person's, even the one who started it."""
    _, client = worker
    sub = await _start(client, template["id"])
    row = await db_session.get(FormSubmission, uuid.UUID(sub["id"]))
    row.source_type = "jhsc"
    await db_session.commit()

    back = (await client.get(f"/api/v1/forms/submissions/{sub['id']}")).json()
    assert back["may_delete"] is False
    refused = await client.delete(f"/api/v1/forms/submissions/{sub['id']}")
    assert refused.status_code == 403 and "shared" in refused.json()["detail"]

    _, hse = hse_manager
    assert (await hse.delete(f"/api/v1/forms/submissions/{sub['id']}")).status_code == 204


async def test_a_scheduled_forms_draft_goes_back_to_unstarted(worker, hse_manager, template,
                                                              db_session):
    _, hse = hse_manager
    schedule = await hse.post("/api/v1/forms/schedules", json={
        "template_id": template["id"], "name": "Monthly walkabout", "frequency": "monthly",
        "next_due_on": plant_today().isoformat()})
    assert schedule.status_code in (200, 201), schedule.text
    run = FormScheduleRun(id=uuid.uuid4(), schedule_id=uuid.UUID(schedule.json()["id"]),
                          template_id=uuid.UUID(template["id"]), due_on=plant_today())
    db_session.add(run)
    await db_session.commit()

    _, client = worker
    sub = (await client.post(f"/api/v1/forms/runs/{run.id}/start")).json()
    # Started by the worker, but it is the occurrence's — HSE's to remove.
    assert (await client.delete(f"/api/v1/forms/submissions/{sub['id']}")).status_code == 403

    assert (await hse.delete(f"/api/v1/forms/submissions/{sub['id']}")).status_code == 204
    await db_session.refresh(run)
    assert run.submission_id is None and run.status == "pending"
    # And the next tap starts a fresh one.
    again = (await client.post(f"/api/v1/forms/runs/{run.id}/start")).json()
    assert again["id"] != sub["id"]


async def test_a_submitted_form_is_a_record_and_is_not_deleted(hse_manager, template):
    _, hse = hse_manager
    sub = await _start(hse, template["id"])
    await hse.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                  json={"values": {"notes": "all fine"}})
    assert (await hse.post(f"/api/v1/forms/submissions/{sub['id']}/submit")).status_code == 200
    assert (await hse.get(f"/api/v1/forms/submissions/{sub['id']}")).json()["may_delete"] is False
    refused = await hse.delete(f"/api/v1/forms/submissions/{sub['id']}")
    assert refused.status_code == 409


async def test_fill_me_is_a_one_person_switch(hse_manager, template):
    """FRM-03: saved on a person question; refused anywhere else, or as a
    non-boolean, at save rather than at fill-in."""
    _, hse = hse_manager
    url = f"/api/v1/forms/templates/{template['id']}/schema"
    ok = await hse.put(url, json={"schema": [
        {"key": "who", "type": "person", "label": "Inspector", "fill_me": False},
        {"key": "lead", "type": "person", "label": "Lead", "fill_me": True},
    ]})
    assert ok.status_code in (200, 201), ok.text
    saved = ok.json()["schema"]
    assert {q["key"]: q.get("fill_me") for q in saved} == {"who": False, "lead": True}

    wrong_type = await hse.put(url, json={"schema": [
        {"key": "notes", "type": "text", "label": "Notes", "fill_me": True}]})
    assert wrong_type.status_code >= 400 and "one-person" in wrong_type.text
    not_bool = await hse.put(url, json={"schema": [
        {"key": "who", "type": "person", "label": "Inspector", "fill_me": "yes"}]})
    assert not_bool.status_code >= 400 and "true or false" in not_bool.text
