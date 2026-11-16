"""UAT 14: staff who helped with a hazard assessment, and hazard corrective
actions closed on the dashboard."""
import uuid
from datetime import date, datetime, timezone

from app.models.action import Action
from tests.conftest import make_user


async def test_staff_who_helped_are_kept_with_their_names(hse_manager, test_engine, db_session):
    _, hse = hse_manager
    ann = await make_user(test_engine, full_name="Ann Helper")
    bob = await make_user(test_engine, full_name="Bob Helper")
    made = await hse.post("/api/v1/hazards", json={
        "title": "Pinch point", "helper_ids": [str(ann.id), str(bob.id), str(ann.id)]})
    assert made.status_code in (200, 201), made.text
    h = made.json()
    assert [p["name"] for p in h["helpers"]] == ["Ann Helper", "Bob Helper"]

    # Renamed later: the hazard keeps the name it recorded; dropping one works.
    from app.models.mirrors import User
    person = await db_session.get(User, ann.id)
    person.full_name = "Ann Renamed"
    await db_session.commit()
    again = await hse.patch(f"/api/v1/hazards/{h['id']}", json={"helper_ids": [str(ann.id)]})
    assert again.status_code == 200, again.text
    assert again.json()["helpers"] == [{"id": str(ann.id), "name": "Ann Helper"}]

    # Somebody not on the staff list is refused, not stored.
    gone = await make_user(test_engine, full_name="Gone")
    p = await db_session.get(User, gone.id)
    p.is_active = False
    await db_session.commit()
    refused = await hse.patch(f"/api/v1/hazards/{h['id']}", json={"helper_ids": [str(gone.id)]})
    assert refused.status_code == 422
    unknown = await hse.patch(f"/api/v1/hazards/{h['id']}", json={"helper_ids": [str(uuid.uuid4())]})
    assert unknown.status_code == 422
    # A save that does not mention them leaves them as they were.
    kept = await hse.patch(f"/api/v1/hazards/{h['id']}", json={"description": "x"})
    assert kept.json()["helpers"] == [{"id": str(ann.id), "name": "Ann Helper"}]


async def test_hazard_corrective_actions_closed_by_month(hse_manager, db_session):
    _, hse = hse_manager
    y = 2045
    when = datetime(y, 6, 10, 15, tzinfo=timezone.utc)
    for source, status in (("hazard", "closed"), ("hazard", "closed"), ("hazard", "open"),
                           ("incident", "closed")):
        db_session.add(Action(id=uuid.uuid4(), action_no=f"ACT-T-{uuid.uuid4().hex[:8]}",
                              source_type=source, title="x", status=status, due_date=date(y, 6, 30),
                              closed_at=when if status == "closed" else None))
    await db_session.flush()
    rows = (await hse.get("/api/v1/dashboard", params={"year": y})).json()["assessments"]
    assert rows[5]["capa_resolved"] == 2
    assert sum(r["capa_resolved"] for r in rows) == 2
