"""UAT 13: a supervisor runs a past toolbox talk again (R13-C), and review R6 /
R10: it is saved with its signatures when the sheet is finished — never on
opening it — and always names the delivery it repeats."""
import uuid

from sqlalchemy import func, select

from app.crud import toolbox as crud
from app.models.toolbox import TbtDelivery
from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user

SIGN = {"person_name": "Priya", "signature": "data:image/png;base64,AA"}


def _sheet(*names, held="2026-11-16T08:00:00Z"):
    return {"signatures": [{**SIGN, "person_name": n} for n in names], "held_at": held}


async def _published_talk(hse, sup):
    talk = (await hse.post("/api/v1/toolbox-talks", json={
        "title": f"Ladders {uuid.uuid4().hex[:4]}",
        "period_month": plant_today().replace(day=1).isoformat()})).json()
    sent = await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish",
                          json={"supervisor_ids": [str(sup.id)]})
    assert sent.status_code == 200, sent.text
    return talk, sent.json()[0]


async def _count(db, talk_id) -> int:
    return (await db.execute(select(func.count()).select_from(TbtDelivery).where(
        TbtDelivery.talk_id == uuid.UUID(talk_id)))).scalar()


async def test_a_supervisor_holds_a_past_talk_again_with_a_new_sheet(
        hse_manager, worker, test_engine, db_session):
    _, hse = hse_manager
    sup = await make_user(test_engine, role="area_supervisor", full_name="Ken Again")
    talk, first = await _published_talk(hse, sup)
    async with authed_client(make_token(sup.id, sup.role), db_session) as k:
        signed = await k.post(f"/api/v1/toolbox-talks/deliveries/{first['id']}/sign",
                              json=_sheet("Dario", held="2026-11-02T08:00:00Z"))
        assert signed.status_code == 200, signed.text

        again = await k.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again", json=_sheet("Priya", "Sam"))
        assert again.status_code == 201, again.text
        rerun = again.json()
        assert rerun["rerun_of"] == first["id"] and rerun["status"] == "delivered"
        assert rerun["signed_count"] == 2   # its own sheet
        # And again — as often as it is needed.
        assert (await k.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again",
                             json=_sheet("Lee"))).status_code == 201

    page = (await hse.get("/api/v1/toolbox-talks/tracker",
                          params={"month": plant_today().isoformat(), "q": "Ken Again"})).json()
    assert page["items"][0]["delivered"] == 3 and page["items"][0]["outstanding"] == 0
    await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish", json={"supervisor_ids": [str(sup.id)]})
    firsts = [d for d in (await hse.get("/api/v1/toolbox-talks/deliveries",
                                        params={"talk_id": talk["id"]})).json() if not d["rerun_of"]]
    assert len(firsts) == 1


async def test_a_sheet_closed_unsigned_leaves_nothing_to_chase(hse_manager, test_engine, db_session):
    """Review R6: opening the sheet and walking away left a pending delivery
    nobody could remove, chased every fortnight."""
    _, hse = hse_manager
    sup = await make_user(test_engine, role="area_supervisor")
    talk, first = await _published_talk(hse, sup)
    before = await _count(db_session, talk["id"])
    async with authed_client(make_token(sup.id, sup.role), db_session) as k:
        empty = await k.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again", json={"signatures": []})
        assert empty.status_code == 422 and "signature" in empty.json()["detail"]
    assert await _count(db_session, talk["id"]) == before
    chased = await crud.chase_outstanding(db_session)
    assert all(d.talk_id != uuid.UUID(talk["id"]) or d.id == uuid.UUID(first["id"]) for d in chased)


async def test_held_again_by_somebody_it_was_never_handed_to_still_names_the_first(
        hse_manager, test_engine, db_session):
    """Review R10: HSE (or a supervisor not on the talk) holding it again left
    rerun_of empty — and so took the one-first-delivery slot: two tabs, a 500."""
    _, hse = hse_manager
    sup = await make_user(test_engine, role="area_supervisor")
    talk, first = await _published_talk(hse, sup)
    one = await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again", json=_sheet("A"))
    two = await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again", json=_sheet("B"))
    assert (one.status_code, two.status_code) == (201, 201), (one.text, two.text)
    assert one.json()["rerun_of"] == first["id"] == two.json()["rerun_of"]


async def test_only_those_who_hold_talks_may_hold_one_again(hse_manager, worker, test_engine, db_session):
    _, hse = hse_manager
    _, emp = worker
    sup = await make_user(test_engine, role="area_supervisor")
    talk, _ = await _published_talk(hse, sup)
    url = f"/api/v1/toolbox-talks/{talk['id']}/hold-again"
    assert (await emp.post(url, json=_sheet("X"))).status_code == 403
    assert (await hse.post(url, json=_sheet("X"))).status_code == 201
    lead = await make_user(test_engine, full_name="Lead Hand")
    await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish", json={"supervisor_ids": [str(lead.id)]})
    async with authed_client(make_token(lead.id, lead.role), db_session) as c:
        assert (await c.post(url, json=_sheet("X"))).status_code == 201
    unpublished = (await hse.post("/api/v1/toolbox-talks", json={
        "title": "Draft", "period_month": plant_today().replace(day=1).isoformat()})).json()
    assert (await hse.post(f"/api/v1/toolbox-talks/{unpublished['id']}/hold-again",
                           json=_sheet("X"))).status_code == 409
