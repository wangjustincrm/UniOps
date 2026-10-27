"""The certificate this plant issues for its own training.

A course taken outside arrives as a file somebody uploads. A course run here
produced nothing the person could keep — which is what somebody asks for the
day they apply for a job somewhere else.
"""
import uuid
from datetime import date

import pytest

from app.models.training import Course


@pytest.fixture
async def course(db_session) -> Course:
    row = Course(
        id=uuid.uuid4(), code=f"c{uuid.uuid4().hex[:6]}", name="Working at Heights",
        is_statutory=True, validity_months=36,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _record(client, course, user_id, *, delivery="internal") -> dict:
    r = await client.post("/api/v1/training/records", json={
        "user_id": str(user_id), "course_id": str(course.id),
        "completed_on": date.today().isoformat(), "delivery": delivery,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_the_plant_issues_a_certificate_for_its_own_training(
        hse_manager, worker, course):
    """★ Generated from the record rather than stored, so it cannot drift from
    it: a record corrected next week produces a corrected certificate."""
    _, client = hse_manager
    learner, _ = worker
    record = await _record(client, course, learner.id)

    got = await client.get(f"/api/v1/training/records/{record['id']}/certificate/issued")
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("application/pdf")
    assert got.content.startswith(b"%PDF-")


async def test_the_person_who_earned_it_can_fetch_their_own(worker, hse_manager, course):
    """Both of them, which is what was asked for — the student and whoever
    taught it."""
    learner, learner_client = worker
    _, hse = hse_manager
    record = await _record(hse, course, learner.id)

    theirs = await learner_client.get(
        f"/api/v1/training/records/{record['id']}/certificate/issued")
    assert theirs.status_code == 200, theirs.text


async def test_somebody_elses_certificate_is_not_yours(
        worker, hse_manager, course, test_engine, db_session):
    """The admission above must not have been bought by opening it to all."""
    from tests.conftest import authed_client, make_token, make_user

    learner, _ = worker
    _, hse = hse_manager
    record = await _record(hse, course, learner.id)

    stranger = await make_user(test_engine, role="requester")
    async with authed_client(make_token(stranger.id, stranger.role), db_session) as c:
        refused = await c.get(
            f"/api/v1/training/records/{record['id']}/certificate/issued")
        assert refused.status_code == 403, refused.text


async def test_an_outside_course_keeps_the_certificate_it_came_with(
        hse_manager, worker, course):
    """★ Issuing our own for a ticket somebody else awarded would be making a
    claim that is not ours to make."""
    _, client = hse_manager
    learner, _ = worker
    record = await _record(client, course, learner.id, delivery="external")

    refused = await client.get(
        f"/api/v1/training/records/{record['id']}/certificate/issued")
    assert refused.status_code == 409, refused.text
    assert "taken elsewhere" in refused.json()["detail"]


async def test_a_chinese_name_is_drawn_rather_than_boxed(hse_manager, course, test_engine):
    """★ ReportLab's built-in faces have no CJK glyphs, and drawing one in
    Helvetica produces black boxes with no error raised.

    The evidence is the embedded font: a certificate whose name needs the CJK
    face carries a FontFile object, and one in Latin letters does not. Where
    the image has not been rebuilt with the font this is skipped rather than
    failed — it is a property of the image, and saying "broken" on a machine
    that simply has not rebuilt yet would be the wrong message.
    """
    from app.services.pdf_fonts import cjk_available
    from tests.conftest import make_user

    if not cjk_available():
        pytest.skip("no CJK font in this image — rebuild with fonts-wqy-zenhei")

    _, client = hse_manager
    # Made with the name, through the same helper every other test uses.
    # Assigning it to the `worker` fixture's object did nothing: that row was
    # created on its own session, so the instance is detached from this one.
    learner = await make_user(test_engine, role="requester", full_name="王建国")

    record = await _record(client, course, learner.id)
    # The record keeps a snapshot of the name, so assert on that rather than on
    # what the user row says — if the snapshot were Latin the certificate would
    # be right to draw it in Helvetica, and the test would be wrong, not the code.
    assert record["user_name"] == "王建国", (
        f"the record did not pick up the name: {record['user_name']!r}")

    got = await client.get(f"/api/v1/training/records/{record['id']}/certificate/issued")
    assert got.status_code == 200, got.text
    assert b"FontFile" in got.content, (
        "the face has to be embedded, not merely named — a reference relies on "
        "the reader having the font and does not survive being emailed")


PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
       "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _images(pdf: bytes) -> int:
    return pdf.count(b"/Subtype /Image")


async def test_the_certificate_carries_the_company_logo(hse_manager, worker, course, db_session):
    """★ Round 9: "In-house training certificates should have the CRM logo
    centered on the top of the page, use the CRM logo found on the admin panel,
    company settings." """
    from app.models.mirrors import CompanyConfig

    _, client = hse_manager
    learner, _ = worker
    record = await _record(client, course, learner.id)
    url = f"/api/v1/training/records/{record['id']}/certificate/issued"

    assert _images((await client.get(url)).content) == 0, "no logo set, none drawn"

    db_session.add(CompanyConfig(id=uuid.uuid4(), name="Canada Royal Milk", logo_data_url=PNG))
    await db_session.flush()
    got = await client.get(url)
    assert got.status_code == 200, got.text
    assert _images(got.content) >= 1  # the picture (and its transparency mask)


def test_a_logo_that_cannot_be_drawn_is_left_off():
    """An SVG or a broken upload: the certificate still prints, without it."""
    from app.services import certificate_pdf

    for bad in ("data:image/svg+xml;base64,PHN2Zy8+", "data:image/png;base64,bm90IGFuIGltYWdl",
                "not a data url"):
        pdf = certificate_pdf.render(
            person_name="A", course_name="B", course_code=None, completed_on=date(2026, 1, 1),
            expires_on=None, record_no="X", verify_url=None, logo_data_url=bad)
        assert pdf.startswith(b"%PDF-") and _images(pdf) == 0


async def test_the_register_and_the_minutes_carry_the_logo_too(hse_manager, db_session):
    """★ Round 10, question 7: "the logo should also go on the other printouts —
    the printed training register, the JHSC minutes …" """
    from datetime import date as _date
    from app.models.mirrors import CompanyConfig

    _, client = hse_manager
    meeting = (await client.post("/api/v1/jhsc/meetings", json={
        "held_on": _date.today().isoformat(), "title": "Monthly"})).json()
    urls = ("/api/v1/training/records/print", f"/api/v1/jhsc/meetings/{meeting['id']}/print")
    for url in urls:
        got = await client.get(url)
        assert got.status_code == 200, (url, got.text)
        assert _images(got.content) == 0, url
    db_session.add(CompanyConfig(id=uuid.uuid4(), name="Canada Royal Milk", logo_data_url=PNG))
    await db_session.flush()
    for url in urls:
        assert _images((await client.get(url)).content) >= 1, url
