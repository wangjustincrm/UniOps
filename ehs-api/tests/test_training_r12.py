"""Round 12, training: a worker's own records searched, printed, exported and
their certificates (TRN-02); certificates printed from a register search
(TRN-03); sessions searched by number, title and date (TRN-04); a course
edited after it is made (TRN-08 / Q14)."""
import base64
import re
import uuid
import zlib
from datetime import date, timedelta

import pytest

from app.services import certificate_pdf
from app.services.day_range import plant_today
from tests.conftest import make_user


def _pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


def _text(pdf: bytes) -> bytes:
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = m.group(1).strip()
        try:
            if raw.endswith(b"~>"):
                raw = base64.a85decode(raw[:-2])
            out += zlib.decompress(raw)
        except (ValueError, zlib.error):
            out += raw
    return out


async def _course(client, name: str, months: int | None = 12) -> dict:
    made = await client.post("/api/v1/training/courses",
                             json={"name": name, "validity_months": months})
    assert made.status_code == 201, made.text
    return made.json()


async def _record(client, user_id, course_id, completed_on: date, **extra) -> dict:
    r = await client.post("/api/v1/training/records", json={
        "user_id": str(user_id), "course_id": course_id,
        "completed_on": completed_on.isoformat(), **extra})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
async def trained(hse_manager, worker):
    """The worker: first aid this year, forklift long expired, WHMIS taken
    elsewhere; somebody else has first aid too."""
    _, hse = hse_manager
    me, _ = worker
    today = plant_today()
    first_aid = await _course(hse, f"First aid {uuid.uuid4().hex[:4]}", 36)
    forklift = await _course(hse, f"Forklift {uuid.uuid4().hex[:4]}", 12)
    whmis = await _course(hse, f"WHMIS {uuid.uuid4().hex[:4]}", None)
    await _record(hse, me.id, first_aid["id"], today - timedelta(days=30))
    await _record(hse, me.id, forklift["id"], today - timedelta(days=800))
    await _record(hse, me.id, whmis["id"], today - timedelta(days=10),
                  delivery="external", provider="St John")
    return {"first_aid": first_aid, "forklift": forklift, "whmis": whmis}


async def test_my_records_are_searched_by_topic_date_and_type(worker, trained):
    _, client = worker
    mine = (await client.get("/api/v1/training/records/mine")).json()
    assert len(mine) == 3
    by_topic = (await client.get(
        f"/api/v1/training/records/mine?search=forklift")).json()
    assert [r["course_label"] for r in by_topic] == [trained["forklift"]["name"]]
    recent = (await client.get(
        f"/api/v1/training/records/mine?completed_from={(plant_today() - timedelta(days=60)).isoformat()}"
    )).json()
    assert len(recent) == 2
    external = (await client.get("/api/v1/training/records/mine?delivery=external")).json()
    assert [r["provider"] for r in external] == ["St John"]


async def test_my_records_print_and_export_are_mine_only(worker, hse_manager, trained,
                                                         test_engine, db_session):
    _, hse = hse_manager
    other = await make_user(test_engine)
    await _record(hse, other.id, trained["first_aid"]["id"], plant_today())

    _, client = worker
    printed = await client.get("/api/v1/training/records/mine/print")
    assert printed.status_code == 200, printed.text
    assert printed.content.startswith(b"%PDF")
    exported = await client.get("/api/v1/training/records/mine/export")
    assert exported.status_code == 200, exported.text
    assert exported.content[:2] == b"PK"  # an xlsx is a zip
    # A worker holds no register permission; the register's own print is not theirs.
    assert (await client.get("/api/v1/training/records/print")).status_code == 403

    # Somebody else's records never appear, whatever is asked.
    other_name = other.full_name.encode()
    assert other_name not in _text(printed.content)


async def test_all_my_certificates_print_ours_and_list_theirs(worker, trained):
    _, client = worker
    got = await client.get("/api/v1/training/records/mine/certificates")
    assert got.status_code == 200, got.text
    # Two run here (one page each) and one taken elsewhere, named on a closing page.
    assert got.headers["x-certificates"] == "2"
    assert got.headers["x-taken-elsewhere"] == "1"
    assert _pages(got.content) == 3
    assert b"Training taken elsewhere" in _text(got.content)

    one = await client.get("/api/v1/training/records/mine/certificates?search=first aid")
    assert one.headers["x-certificates"] == "1" and _pages(one.content) == 1


async def test_register_certificates_for_a_search_not_expired(hse_manager, worker, trained,
                                                             test_engine):
    """TRN-03: Kirk's own example — everyone with first aid not expired."""
    _, hse = hse_manager
    other = await make_user(test_engine)
    lapsed = await make_user(test_engine)
    today = plant_today()
    await _record(hse, other.id, trained["first_aid"]["id"], today - timedelta(days=5))
    await _record(hse, lapsed.id, trained["first_aid"]["id"], today - timedelta(days=2000))

    name = trained["first_aid"]["name"]
    listed = (await hse.get(
        f"/api/v1/training/records?search={name}&expiry=valid")).json()
    assert len(listed) == 2  # the lapsed one is not "not expired"
    got = await hse.get(f"/api/v1/training/records/certificates?search={name}&expiry=valid")
    assert got.status_code == 200, got.text
    assert got.headers["x-certificates"] == "2"
    assert lapsed.full_name.encode() not in _text(got.content)

    # The register's figures still work, and the worker may not batch-print others'.
    _, client = worker
    assert (await client.get(
        "/api/v1/training/records/certificates")).status_code == 403
    assert (await hse.get(
        "/api/v1/training/records/certificates?search=nothing-matches-this")).status_code == 404


async def test_register_certificates_are_capped_and_say_so(hse_manager, trained, monkeypatch):
    monkeypatch.setattr(certificate_pdf, "MAX_BATCH", 1)
    _, hse = hse_manager
    refused = await hse.get("/api/v1/training/records/certificates")
    assert refused.status_code == 422
    assert "Narrow the search" in refused.json()["detail"]


async def test_sessions_are_searched_by_number_title_and_date(hse_manager, trained):
    _, hse = hse_manager
    today = plant_today()
    made = []
    for title, when in (("Spring refresher", today - timedelta(days=200)),
                        ("Autumn refresher", today - timedelta(days=10))):
        r = await hse.post("/api/v1/course-sessions", json={
            "course_id": trained["first_aid"]["id"], "title": title,
            "session_date": when.isoformat()})
        assert r.status_code == 201, r.text
        made.append(r.json())

    by_title = (await hse.get("/api/v1/course-sessions?search=spring")).json()
    assert [s["title"] for s in by_title] == ["Spring refresher"]
    by_no = (await hse.get(
        f"/api/v1/course-sessions?search={made[1]['delivery_no']}")).json()
    assert [s["id"] for s in by_no] == [made[1]["id"]]
    by_date = (await hse.get(
        f"/api/v1/course-sessions?date_from={(today - timedelta(days=30)).isoformat()}")).json()
    assert made[1]["id"] in {s["id"] for s in by_date}
    assert made[0]["id"] not in {s["id"] for s in by_date}


async def test_a_course_is_corrected_without_touching_what_was_issued(hse_manager, worker,
                                                                     trained):
    """TRN-08 / Q14: "certificates should stay once issued. a new valid for
    period should only affect new course sessions." """
    _, hse = hse_manager
    me, client = worker
    course = trained["first_aid"]
    before = [r for r in (await client.get("/api/v1/training/records/mine")).json()
              if r["course_id"] == course["id"]][0]

    fixed = await hse.patch(f"/api/v1/training/courses/{course['id']}",
                            json={"name": "First Aid (CPR-C)", "validity_months": 24})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["name"] == "First Aid (CPR-C)"

    after = [r for r in (await client.get("/api/v1/training/records/mine")).json()
             if r["course_id"] == course["id"]][0]
    assert after["course_label"] == before["course_label"]
    assert after["expires_on"] == before["expires_on"]

    # A new record takes the new period.
    new = await _record(hse, me.id, course["id"], plant_today())
    from app.services import validity
    assert new["expires_on"] == validity.expires_on(plant_today(), 24).isoformat()

    # Two courses of one name are refused, as at creation.
    clash = await hse.patch(f"/api/v1/training/courses/{course['id']}",
                            json={"name": trained["forklift"]["name"].upper()})
    assert clash.status_code == 409
    # Renaming to itself in another case is not a clash.
    same = await hse.patch(f"/api/v1/training/courses/{course['id']}",
                           json={"name": "first aid (cpr-c)"})
    assert same.status_code == 200


async def test_my_records_sort_by_the_column_asked_for(worker, trained):
    _, client = worker
    asc = (await client.get("/api/v1/training/records/mine?sort=course_label&dir=asc")).json()
    names = [r["course_label"] for r in asc]
    assert names == sorted(names, key=str.lower)
    desc = (await client.get("/api/v1/training/records/mine?sort=expires_on&dir=desc")).json()
    # WHMIS never expires: blank, so last either way.
    assert desc[-1]["expires_on"] is None
    assert desc[0]["expires_on"] > desc[1]["expires_on"]
    assert (await client.get("/api/v1/training/records/mine?sort=user_id")).status_code == 422


async def test_my_external_certificate_opens_for_me_and_not_for_others(
        worker, hse_manager, trained, test_engine, db_session, monkeypatch):
    """Review C1: the provider's certificate is the worker's to open too."""
    from fastapi import Response

    from app.api.v1 import training as training_api
    from app.models.training import TrainingRecord
    from sqlalchemy import select
    from tests.conftest import authed_client, make_token

    async def fake_download(file_id, token):  # noqa: ARG001
        return Response(content=b"%PDF-1.4 ticket", media_type="application/pdf")
    monkeypatch.setattr(training_api, "proxy_download", fake_download)

    me, client = worker
    rec = (await db_session.execute(select(TrainingRecord).where(
        TrainingRecord.user_id == me.id, TrainingRecord.delivery == "external"))).scalar_one()
    rec.certificate_file_id = uuid.uuid4()
    await db_session.commit()

    mine = await client.get(f"/api/v1/training/records/{rec.id}/certificate")
    assert mine.status_code == 200, mine.text
    other = await make_user(test_engine)
    async with authed_client(make_token(other.id, other.role), db_session) as theirs:
        assert (await theirs.get(
            f"/api/v1/training/records/{rec.id}/certificate")).status_code == 403
    _, hse = hse_manager
    assert (await hse.get(f"/api/v1/training/records/{rec.id}/certificate")).status_code == 200
