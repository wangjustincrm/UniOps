"""The minutes, and the meeting they belong to.

HSE asked for the format of the minutes to be configurable, so they are a form
template rather than a second layout engine. What was missing is the link: the
screen told people to fill the form in under Forms and attach it to the
meeting, and there was nothing on the meeting to attach it with — the endpoint
existed and nothing called it.
"""
import uuid
from datetime import date

import pytest

from app.models.config import EhsConfig


@pytest.fixture
async def minutes_template(hse_manager, db_session):
    """A published form, chosen in Settings as the minutes form.

    Built through the API rather than inserted, so it is a template in the
    state the product actually produces — a draft with no published version
    cannot be started, and seeding one directly would hide that.
    """
    _, client = hse_manager
    created = await client.post("/api/v1/forms/templates", json={
        "code": f"jhsc-min-{uuid.uuid4().hex[:5]}",
        "name": "Committee minutes",
        "category": "jhsc",
    })
    assert created.status_code in (200, 201), created.text
    template = created.json()

    # The questions are their own step, and publishing without any is refused —
    # which is why this goes through the real path rather than seeding a row.
    saved = await client.put(f"/api/v1/forms/templates/{template['id']}/schema", json={
        "schema": [{"key": "present", "type": "text", "label": "Who was there",
                    "required": False}],
    })
    assert saved.status_code in (200, 201), saved.text
    published = await client.post(
        f"/api/v1/forms/templates/{template['id']}/publish",
        json={"change_note": "First version"})
    assert published.status_code in (200, 201), published.text

    config = await db_session.get(EhsConfig, 1)
    if config is None:
        config = EhsConfig(id=1)
        db_session.add(config)
    config.jhsc_minutes_template_code = template["code"]
    await db_session.commit()
    return template


async def _a_meeting(client) -> dict:
    r = await client.post("/api/v1/jhsc/meetings", json={
        "held_on": date.today().isoformat(), "title": "Monthly meeting"})
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_the_minutes_are_started_from_the_meeting_and_stay_attached(
        hse_manager, minutes_template):
    """★ The link is made when the document comes into existence, which is the
    one moment nobody can forget it."""
    _, client = hse_manager
    meeting = await _a_meeting(client)

    started = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
    assert started.status_code in (200, 201), started.text
    submission_id = started.json()["submission_id"]
    assert started.json()["submission_no"]

    back = (await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}")).json()
    assert back["minutes_submission_id"] == submission_id


async def test_starting_the_minutes_twice_opens_the_same_ones(
        hse_manager, minutes_template):
    """★ A second press must not cut the first set loose.

    Pressed twice — by one person on a slow connection, or by two members of
    the committee — pointing the meeting at a fresh empty form would leave
    half-written statutory minutes orphaned in Forms with nothing saying where
    they went.
    """
    _, client = hse_manager
    meeting = await _a_meeting(client)

    first = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
    second = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
    assert first.json()["submission_id"] == second.json()["submission_id"]

    back = (await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}")).json()
    assert back["minutes_submission_id"] == first.json()["submission_id"]


async def test_it_says_so_when_no_minutes_form_has_been_chosen(hse_manager, db_session):
    """Rather than starting something arbitrary, or failing silently."""
    _, client = hse_manager
    config = await db_session.get(EhsConfig, 1)
    if config is None:
        config = EhsConfig(id=1)
        db_session.add(config)
    config.jhsc_minutes_template_code = None
    await db_session.commit()

    meeting = await _a_meeting(client)
    refused = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
    assert refused.status_code == 409, refused.text
    assert "Settings" in refused.json()["detail"]


async def test_the_minutes_written_on_the_form_are_read_back_onto_the_meeting(
        hse_manager, minutes_template):
    """★ Round 6, TC-04: "a saved meeting minutes form does not get saved on the
    meeting." It was attached; the meeting showed only its own empty text box
    and said "No minutes recorded" over a filled-in form."""
    _, client = hse_manager
    meeting = await _a_meeting(client)
    started = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
    sid = started.json()["submission_id"]
    saved = await client.put(f"/api/v1/forms/submissions/{sid}/answers",
                             json={"values": {"present": "Kirk, Marie and the night crew"}})
    assert saved.status_code == 200, saved.text

    rows = await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-answers")
    assert rows.status_code == 200, rows.text
    assert rows.json() == [{"label": "Who was there",
                            "text": "Kirk, Marie and the night crew", "heading": False,
                            "emphasis": False}]


async def test_a_meeting_with_no_form_has_no_form_rows(hse_manager):
    _, client = hse_manager
    meeting = await _a_meeting(client)
    rows = await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-answers")
    assert rows.status_code == 200 and rows.json() == []


async def test_the_minutes_print_as_one_document(hse_manager, minutes_template):
    """Round 6, TC-04: "no means to print the meeting minutes including the
    attendance, agenda, minutes, and recommendations." """
    from tests.test_label_revision import _pdf_text

    _, client = hse_manager
    meeting = await _a_meeting(client)
    await client.patch(f"/api/v1/jhsc/meetings/{meeting['id']}",
                       json={"agenda": "Ladders, then the eyewash"})
    started = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
    await client.put(f"/api/v1/forms/submissions/{started.json()['submission_id']}/answers",
                     json={"values": {"present": "Everyone on days"}})
    await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                      json={"recommendation": "Replace the cracked step ladder in 104"})

    got = await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}/print")
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "application/pdf"
    text = _pdf_text(got.content)
    assert text, "the extractor found nothing — an empty extraction passes every 'not in'"
    for expected in ("Attendance", "Agenda", "Ladders, then the eyewash", "Minutes",
                     "Who was there", "Everyone on days", "Recommendations",
                     "Replace the cracked step ladder in 104"):
        assert expected in text, expected


async def test_the_printed_minutes_number_their_pages_and_say_who_owes_a_reply(
        hse_manager, minutes_template, test_engine):
    """★ Round 7, TC-01: "add the page number to the bottom, page 1 of 2. The
    recommendations section should also include who it is assigned to and the
    due date." """
    from tests.conftest import make_user
    from tests.test_label_revision import _pdf_text

    _, client = hse_manager
    meeting = await _a_meeting(client)
    owner = await make_user(test_engine, role="ehs_manager", full_name="Pat Plantmanager")
    sent = (await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                              json={"recommendation": "Guard the conveyor nip point"})).json()
    await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                      json={"recommendation": "Repaint the forklift lanes"})
    assert (await client.post(f"/api/v1/jhsc/recommendations/{sent['id']}/send",
                              json={"user_ids": [str(owner.id)]})).status_code == 200

    text = _pdf_text((await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}/print")).content)
    assert "Page 1 of 1" in text
    assert "Sent to Pat Plantmanager on" in text and "Reply due" in text
    assert "Not yet sent, so not yet assigned" in text



async def test_a_question_marked_to_stand_out_does_so_read_back_and_printed(hse_manager, db_session):
    """★ Round 9: "change the area to be inspected, photos, and inspection
    results to be Bold and increase the font size by 2" — on the form, read
    back, and printed. Marked per question in the form builder."""
    from tests.test_label_revision import _pdf_text_sizes

    _, client = hse_manager
    created = (await client.post("/api/v1/forms/templates", json={
        "code": f"jhsc-em-{uuid.uuid4().hex[:5]}", "name": "Minutes", "category": "jhsc"})).json()
    bad = await client.put(f"/api/v1/forms/templates/{created['id']}/schema", json={
        "schema": [{"key": "a", "type": "text", "label": "A", "emphasis": "yes"}]})
    assert bad.status_code == 422, bad.text
    saved = await client.put(f"/api/v1/forms/templates/{created['id']}/schema", json={
        "schema": [{"key": "area", "type": "text", "label": "Plant area to be inspected",
                    "emphasis": True},
                   {"key": "notes", "type": "text", "label": "Other notes"}]})
    assert saved.status_code in (200, 201), saved.text
    await client.post(f"/api/v1/forms/templates/{created['id']}/publish", json={"change_note": "v1"})
    config = await db_session.get(EhsConfig, 1)
    if config is None:
        config = EhsConfig(id=1)
        db_session.add(config)
    config.jhsc_minutes_template_code = created["code"]
    await db_session.commit()

    meeting = await _a_meeting(client)
    sid = (await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")).json()["submission_id"]
    await client.put(f"/api/v1/forms/submissions/{sid}/answers",
                     json={"values": {"area": "Dryer floor", "notes": "Quiet month"}})

    rows = (await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-answers")).json()
    assert [(r["label"], r["emphasis"]) for r in rows] == [
        ("Plant area to be inspected", True), ("Other notes", False)]

    pdf = (await client.get(f"/api/v1/jhsc/meetings/{meeting['id']}/print")).content
    sizes = {t: pt for pt, t in _pdf_text_sizes(pdf)}
    assert sizes["Dryer floor"] == 11.5 and sizes["Plant area to be inspected"] == 10.5
    assert sizes["Quiet month"] == 9.5 and sizes["Other notes"] == 8.5
