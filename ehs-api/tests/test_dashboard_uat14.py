"""UAT 14: this month against last on the dashboard, and the Food Safety
dashboard. Each test works in a year nothing else writes to, so the figures
are exactly what it made."""
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import text

from app.models.action import Action
from app.models.food_safety import FoodSafetyAssessment
from app.models.incident import Incident
from app.services import statutory


def _at(y: int, m: int, d: int = 10) -> datetime:
    return datetime.combine(date(y, m, d), time(12), tzinfo=statutory.ONTARIO)


def _incident(db, when, *, form="medical", kind=None, injury=None, status="submitted",
              category_id=None, closed_after_days=None) -> Incident:
    i = Incident(id=uuid.uuid4(), incident_no=f"INC-T-{uuid.uuid4().hex[:8]}", form_kind=form,
                 title="t", status=status, occurred_at=when, injury_class=injury,
                 category_id=category_id, extra={"incident_type": kind} if kind else {},
                 submitted_at=when)
    if closed_after_days is not None:
        i.closed_at = when + timedelta(days=closed_after_days)
    db.add(i)
    return i


async def _food_category(db) -> uuid.UUID:
    await db.execute(text("""
        insert into ehs_vocabularies (code, name) values ('incident_category', 'Incident Category')
        on conflict (code) do nothing"""))
    await db.execute(text("""
        insert into ehs_vocabulary_items (id, vocabulary_code, code, label, sort_order, is_active, attrs, path)
        values (:id, 'incident_category', 'FOOD', 'Food Safety', 99, true, '{}'::jsonb, 'Food Safety')
        on conflict (vocabulary_code, code) do nothing"""), {"id": uuid.uuid4()})
    return (await db.execute(text(
        "select id from ehs_vocabulary_items where vocabulary_code='incident_category' and code='FOOD'"))).scalar()


async def test_this_month_against_last(hse_manager, db_session):
    """Looking back at a year, it is December against November."""
    _, hse = hse_manager
    y = 2041
    for injury in ("first_aid", "first_aid", "lost_time"):
        _incident(db_session, _at(y, 12), injury=injury)
    _incident(db_session, _at(y, 12), form="near_miss", kind="near_miss")
    _incident(db_session, _at(y, 12), status="draft", injury="first_aid")      # not counted
    _incident(db_session, _at(y, 11), injury="medical_aid")
    await db_session.flush()

    got = (await hse.get("/api/v1/dashboard", params={"year": y})).json()["month_change"]
    assert (got["month"], got["previous"]) == ("2041-12", "2041-11")
    assert {k: got["this"][k] for k in ("injuries", "first_aid", "medical_aid", "lost_time", "near_misses")} \
        == {"injuries": 3, "first_aid": 2, "medical_aid": 0, "lost_time": 1, "near_misses": 1}
    assert (got["last"]["injuries"], got["last"]["medical_aid"]) == (1, 1)


async def test_the_food_safety_dashboard_counts_what_its_lists_hold(hse_manager, db_session):
    _, hse = hse_manager
    y = 2042
    food = await _food_category(db_session)
    a = _incident(db_session, _at(y, 12, 3), form="near_miss", kind="near_miss_food_safety",
                  status="closed", closed_after_days=4)
    b = _incident(db_session, _at(y, 12, 5), form="near_miss", kind="near_miss", category_id=food,
                  status="under_investigation")
    _incident(db_session, _at(y, 11, 5), form="near_miss", kind="near_miss_food_safety",
              status="pending_closure")
    _incident(db_session, _at(y, 10, 5), form="near_miss", kind="near_miss_food_safety",
              status="closed", closed_after_days=2)
    _incident(db_session, _at(y, 12, 6), form="near_miss", kind="near_miss")       # not food safety
    _incident(db_session, _at(y, 12, 7), form="medical", kind="first_aid", category_id=food)  # not a near miss
    _incident(db_session, _at(y, 12, 8), form="near_miss", kind="near_miss_food_safety",
              status="draft")                                                    # a draft
    await db_session.flush()
    db_session.add(FoodSafetyAssessment(
        id=uuid.uuid4(), incident_id=a.id, probability=2, severity=2, detectability=2, rpn=8,
        band="low", assessed_at=_at(y, 12, 4)))
    db_session.add(FoodSafetyAssessment(
        id=uuid.uuid4(), incident_id=a.id, ccp_failure=True, probability=1, severity=1,
        detectability=1, rpn=46, band="critical", assessed_at=_at(y, 12, 5)))   # the latest wins
    # b's investigation is signed: under investigation until it is closed.
    from app.models.incident import IncidentInvestigation
    db_session.add(IncidentInvestigation(id=uuid.uuid4(), incident_id=b.id, completed_at=_at(y, 12, 6)))
    for status in ("closed", "open", "cancelled"):
        db_session.add(Action(id=uuid.uuid4(), action_no=f"ACT-T-{uuid.uuid4().hex[:8]}",
                              source_type="incident", source_id=b.id, title="x", status=status,
                              due_date=date(y, 12, 20)))
    await db_session.flush()

    got = await hse.get("/api/v1/dashboard/food-safety", params={"year": y})
    assert got.status_code == 200, got.text
    d = got.json()
    assert d["total"] == 4
    assert d["month"] == {"month": "2042-12", "count": 2, "previous": "2042-11",
                          "previous_count": 1, "change_percent": 100.0}
    assert d["bands"] == {"critical": 1, "high": 0, "medium": 0, "low": 0}
    assert d["not_assessed"] == 3
    assert d["investigations"] == {"open": 1, "under_investigation": 1, "closed": 2}
    assert d["capa"] == {"created": 2, "completed": 1, "outstanding": 1}
    assert [r["count"] for r in d["by_month"]][9:] == [1, 1, 2]
    assert (d["average_days"], d["resolved"]) == (3.0, 2)

    # The figure opens exactly the near misses it counted.
    listed = (await hse.get("/api/v1/incidents", params={
        "food_safety": "true", "occurred_from": f"{y}-01-01", "occurred_to": f"{y}-12-31"})).json()
    assert len(listed) == d["total"]
    closed = (await hse.get("/api/v1/incidents", params=[
        ("food_safety", "true"), ("status", "closed"),
        ("occurred_from", f"{y}-01-01"), ("occurred_to", f"{y}-12-31")])).json()
    assert len(closed) == d["investigations"]["closed"]
    not_closed = [("food_safety", "true"), ("status", "submitted"), ("status", "under_investigation"),
                  ("status", "pending_closure"), ("occurred_from", f"{y}-01-01"), ("occurred_to", f"{y}-12-31")]
    under = (await hse.get("/api/v1/incidents", params=not_closed + [("investigation_signed", "true")])).json()
    opened = (await hse.get("/api/v1/incidents", params=not_closed + [("investigation_signed", "false")])).json()
    assert (len(opened), len(under)) == (d["investigations"]["open"], d["investigations"]["under_investigation"])
    assert [i["id"] for i in under] == [str(b.id)]


async def test_the_food_safety_dashboard_needs_incident_read(worker):
    _, emp = worker
    assert (await emp.get("/api/v1/dashboard/food-safety")).status_code == 403
