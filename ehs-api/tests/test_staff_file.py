"""★ Round 10 (open box): the staff file — everything in Safety with one
person's name on it, grouped with counts, listed with links. Who may open
whose, and which groups, per the review agreed with uniops-8b."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.mirrors import User
from tests.conftest import authed_client, make_token, make_user

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="


async def _supervise(test_engine, boss, person):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(update(User).where(User.id == person.id).values(supervisor_id=boss.id))
        await db.commit()


async def _injury(db, reporter, injured, *, anonymous=False):
    async with authed_client(make_token(reporter.id, reporter.role), db) as c:
        made = await c.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": f"Cut {uuid.uuid4().hex[:4]}",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "incident_type": "first_aid", "signature": SIGNATURE, "is_anonymous": anonymous,
            "persons": [{"role": "injured", "person_name": injured.full_name,
                         "user_id": str(injured.id)}]})
        assert made.status_code in (200, 201), made.text
        iid = made.json()["id"]
        assert (await c.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200
        return made.json()["incident_no"]


def _groups(body):
    return {g["key"]: g["count"] for g in body["groups"]}


async def test_who_may_open_whose(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    boss = await make_user(test_engine, role="area_supervisor")
    mine = await make_user(test_engine, role="requester")
    other = await make_user(test_engine, role="requester")
    await _supervise(test_engine, boss, mine)
    async with authed_client(make_token(boss.id, boss.role), db_session) as b:
        assert (await b.get(f"/api/v1/staff-file/{mine.id}")).status_code == 200
        assert (await b.get(f"/api/v1/staff-file/{other.id}")).status_code == 403
        # Somebody who does not exist answers the same as somebody not theirs.
        assert (await b.get(f"/api/v1/staff-file/{uuid.uuid4()}")).status_code == 403
        listed = {p["id"] for p in (await b.get("/api/v1/staff-file/people")).json()}
        assert listed == {str(boss.id), str(mine.id)}
    async with authed_client(make_token(mine.id, mine.role), db_session) as m:
        assert (await m.get(f"/api/v1/staff-file/{mine.id}")).status_code == 200
        assert (await m.get(f"/api/v1/staff-file/{boss.id}")).status_code == 403
    assert (await hse.get(f"/api/v1/staff-file/{other.id}")).status_code == 200


async def test_injuries_need_the_medical_key_and_anonymous_is_never_counted(
        test_engine, db_session, hse_manager):
    _, hse = hse_manager
    boss = await make_user(test_engine, role="area_supervisor")
    person = await make_user(test_engine, role="requester")
    await _supervise(test_engine, boss, person)
    named = await _injury(db_session, boss, person)
    anon = await _injury(db_session, person, person, anonymous=True)

    body = (await hse.get(f"/api/v1/staff-file/{person.id}")).json()
    assert _groups(body)["injury"] == 1, "the anonymous one is not summed up, even for HSE"
    numbers = {r["number"] for r in body["rows"]}
    assert named in numbers and anon not in numbers

    async with authed_client(make_token(boss.id, boss.role), db_session) as b:
        theirs = (await b.get(f"/api/v1/staff-file/{person.id}")).json()
    assert "injury" not in _groups(theirs), "a supervisor has no medical key: not shown, not 0"
    assert "incident" in _groups(theirs) and "training" in _groups(theirs)
    assert all(r["category"] != "injury" for r in theirs["rows"])


async def test_on_their_own_file_a_person_sees_what_is_theirs(test_engine, db_session, hse_manager):
    """An employee holds no register keys; their own records still show."""
    _, hse = hse_manager
    person = await make_user(test_engine, role="requester")
    someone = await make_user(test_engine, role="area_supervisor")
    mine = await _injury(db_session, person, person)          # they reported it: theirs
    others = await _injury(db_session, someone, person)       # reported by someone else
    async with authed_client(make_token(person.id, person.role), db_session) as m:
        body = (await m.get(f"/api/v1/staff-file/{person.id}")).json()
    g = _groups(body)
    assert g["injury"] == 1 and g["incident"] == 1
    numbers = {r["number"] for r in body["rows"]}
    assert mine in numbers and others not in numbers
    # Registers they may not open, and nothing of theirs to open as theirs, are absent.
    assert "position" not in g and "jhsc" not in g
    assert "training" in g and "action" in g


async def test_the_export_is_text_only(test_engine, db_session, hse_manager):
    import io
    from openpyxl import load_workbook
    _, hse = hse_manager
    person = await make_user(test_engine, role="requester", full_name="=HYPERLINK(\"x\")")
    await _injury(db_session, person, person)
    got = await hse.get(f"/api/v1/staff-file/{person.id}/export")
    assert got.status_code == 200, got.text
    ws = load_workbook(io.BytesIO(got.content)).active
    assert [c.value for c in ws[1]] == ["Employee name", "Date", "Register type", "Number", "Title"]
    cell = ws.cell(row=2, column=1)
    assert cell.value == "=HYPERLINK(\"x\")" and cell.data_type == "s" and cell.quotePrefix
