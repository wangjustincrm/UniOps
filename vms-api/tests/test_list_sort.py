"""Click-to-sort headers on the VMS paged lists — /audit-logs,
/health-declarations, /visits: ?sort=<key>&dir=asc|desc sorted in SQL before
paging, NULLs last both ways, unknown key → 422, no sort → the old default.

The test DB is shared by the whole session, so every test seeds rows under a
tag of its own (entity_type / host / visit date) and filters on it.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.health_declaration import HealthDeclaration
from app.models.visit import AccessArea, HealthDeclStatus, Visit, VisitPurpose, VisitStatus
from app.models.visitor import Visitor
from tests.conftest import make_user

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


async def _add(test_engine, rows):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add_all(rows)
        await db.commit()


def _ids(resp, key="id"):
    assert resp.status_code == 200, resp.text
    return [r[key] for r in resp.json()["items"]]


# ── /audit-logs ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_logs_sort(test_engine, admin):
    _, client = admin
    tag = f"sorttest_{uuid.uuid4().hex[:6]}"
    uid = uuid.uuid4()
    rows = [  # (user_name, notes, minutes)
        ("bob", "beta", 1), ("Alice", None, 2), ("carol", "Alpha", 3),
    ]
    await _add(test_engine, [
        AuditLog(timestamp=T0 + timedelta(minutes=m), user_id=uid, user_name=n,
                 action_type="x.sort", entity_type=tag, entity_id=uuid.uuid4(),
                 ip_address="10.0.0.1", notes=notes)
        for n, notes, m in rows
    ])
    get = lambda qs: client.get(f"/api/v1/audit-logs?entity_type={tag}&{qs}")  # noqa: E731
    names = lambda r: _ids(r, "user_name")  # noqa: E731
    # default: newest id first
    assert names(await get("")) == ["carol", "Alice", "bob"]
    assert names(await get("sort=user_name")) == ["Alice", "bob", "carol"]
    assert names(await get("sort=user_name&dir=desc")) == ["carol", "bob", "Alice"]
    assert names(await get("sort=timestamp&dir=asc")) == ["bob", "Alice", "carol"]
    # NULL notes last both ways; case-insensitive
    assert names(await get("sort=notes&dir=asc")) == ["carol", "bob", "Alice"]
    assert names(await get("sort=notes&dir=desc")) == ["bob", "carol", "Alice"]
    # page 2 continues page 1, total unchanged
    r1 = await get("sort=notes&dir=desc&page=1&page_size=2")
    r2 = await get("sort=notes&dir=desc&page=2&page_size=2")
    assert names(r1) + names(r2) == ["bob", "carol", "Alice"]
    assert r1.json()["total"] == 3
    assert (await get("sort=old_value")).status_code == 422
    assert (await get("sort=user_name&dir=sideways")).status_code == 422


# ── /visits + /health-declarations ──────────────────────────────────────────

async def _seed_visits(test_engine, *, day: date, with_decls: bool):
    host = await make_user(test_engine, role="requester", full_name="Host Zed")
    host2 = await make_user(test_engine, role="requester", full_name="host amy")
    # (first, last, company, area, status, hours, host, decl result)
    spec = [
        ("Zoe", "Ng", "acme", AccessArea.all, VisitStatus.checked_in, 3, host, HealthDeclStatus.passed),
        ("adam", "Li", "Beta", AccessArea.office, VisitStatus.confirmed, 1, host2, HealthDeclStatus.failed),
        ("Mia", "Ko", "Acme", AccessArea.production_gmp, VisitStatus.no_show, 2, host, HealthDeclStatus.restricted),
    ]
    visitors, visits, decls = [], [], []
    for first, last, co, area, st, h, hst, res in spec:
        vis = Visitor(id=uuid.uuid4(), first_name=first, last_name=last, company_name=co)
        v = Visit(id=uuid.uuid4(), visitor_id=vis.id, host_id=hst.id, created_by=hst.id,
                  visit_date=day, planned_arrival=datetime.combine(day, datetime.min.time(),
                                                                   tzinfo=timezone.utc) + timedelta(hours=h),
                  visit_purpose=VisitPurpose.meeting, access_area=area, status=st)
        visitors.append(vis); visits.append(v)
        if with_decls:
            decls.append(HealthDeclaration(id=uuid.uuid4(), visit_id=v.id, visitor_id=vis.id,
                                           questionnaire_data={"answers": []}, result=res))
    await _add(test_engine, visitors)
    await _add(test_engine, visits)
    if decls:
        await _add(test_engine, decls)
    return host, host2


@pytest.mark.asyncio
async def test_visits_sort(test_engine, admin):
    _, client = admin
    day = date(2091, 1, 1) + timedelta(days=uuid.uuid4().int % 3000)
    await _seed_visits(test_engine, day=day, with_decls=False)
    get = lambda qs: client.get(f"/api/v1/visits?date_from={day}&date_to={day}&{qs}")  # noqa: E731
    first = lambda r: [i["visitor"]["first_name"] for i in _ids_items(r)]  # noqa: E731
    # default: visit_date desc, planned_arrival desc
    assert first(await get("")) == ["Zoe", "Mia", "adam"]
    assert first(await get("sort=visitor")) == ["adam", "Mia", "Zoe"]
    assert first(await get("sort=visitor&dir=desc")) == ["Zoe", "Mia", "adam"]
    assert first(await get("sort=planned_arrival")) == ["adam", "Mia", "Zoe"]
    # company: case-insensitive, ties broken stably by id (only order of the tie varies)
    assert first(await get("sort=company&dir=desc"))[0] == "adam"
    # area sorts by label: Entire Plant < Office < Production (GMP)
    assert first(await get("sort=access_area")) == ["Zoe", "adam", "Mia"]
    # status by label: Confirmed < No Show < On-Site (checked_in)
    assert first(await get("sort=status")) == ["adam", "Mia", "Zoe"]
    assert first(await get("sort=status&dir=desc")) == ["Zoe", "Mia", "adam"]
    p1 = first(await get("sort=status&page=1&page_size=2"))
    p2 = first(await get("sort=status&page=2&page_size=2"))
    assert p1 + p2 == ["adam", "Mia", "Zoe"]
    assert (await get("sort=notes")).status_code == 422


def _ids_items(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


@pytest.mark.asyncio
async def test_health_declarations_sort(test_engine, admin):
    _, client = admin
    day = date(2091, 1, 1) + timedelta(days=3001 + uuid.uuid4().int % 3000)
    await _seed_visits(test_engine, day=day, with_decls=True)
    get = lambda qs: client.get(f"/api/v1/health-declarations?from={day}&to={day}&{qs}")  # noqa: E731
    names = lambda r: [i["visitor_name"] for i in _ids_items(r)]  # noqa: E731
    assert len(names(await get(""))) == 3
    assert names(await get("sort=visitor_name")) == ["adam Li", "Mia Ko", "Zoe Ng"]
    assert names(await get("sort=visitor_name&dir=desc")) == ["Zoe Ng", "Mia Ko", "adam Li"]
    # host by NAME, case-insensitive: "host amy" before the two "Host Zed"
    assert names(await get("sort=host_name"))[0] == "adam Li"
    assert names(await get("sort=host_name&dir=desc"))[-1] == "adam Li"
    # area as shown ("production gmp" / "office" / "all")
    assert names(await get("sort=access_area")) == ["Zoe Ng", "adam Li", "Mia Ko"]
    assert names(await get("sort=result")) == ["adam Li", "Zoe Ng", "Mia Ko"]
    p1 = names(await get("sort=result&dir=desc&page=1&page_size=2"))
    p2 = names(await get("sort=result&dir=desc&page=2&page_size=2"))
    assert p1 + p2 == ["Mia Ko", "Zoe Ng", "adam Li"]
    assert (await get("sort=signature")).status_code == 422
