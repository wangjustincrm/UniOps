"""Click-to-sort headers, epms-api half of group E:

- GET /users (Portal → Admin → Users): sort by name, role LABEL, department
  NAME, ERP code, status.
- GET /admin/{entity} (Portal → Data Maintenance): sort by any list column.

Each: asc/desc, NULLs last in both directions, unknown key → 422, no sort →
the previous default order, page 2 continues page 1.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.department import Department
from app.models.pr import PurchaseRequest
from app.models.user import User


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_users_list_sort(test_engine, admin_client):
    tag = f"srt{uuid.uuid4().hex[:6]}"
    async with _factory(test_engine)() as db:
        d_b = Department(id=uuid.uuid4(), code=f"B{tag}", name="beta dept")
        d_a = Department(id=uuid.uuid4(), code=f"A{tag}", name="Alpha Dept")
        db.add_all([d_a, d_b])
        await db.flush()
        # (name, role, dept, erp code, active)
        for name, role, dept, erp, active in [
            (f"carol {tag}", "requester", d_b, None, True),
            (f"Alice {tag}", "system_admin", None, "E2", False),
            (f"bob {tag}", "dept_manager", d_a, "e1", True),
        ]:
            db.add(User(id=uuid.uuid4(), email=f"{name.split()[0].lower()}-{tag}@t.com",
                        full_name=name, role=role, department_id=dept.id if dept else None,
                        erp_person_code=erp, is_active=active,
                        hashed_password=hash_password("TestPass1!")))
        await db.commit()

    async def names(qs):
        r = await admin_client.get(f"/api/v1/users?search={tag}&{qs}")
        assert r.status_code == 200, r.text
        return [u["full_name"].split()[0] for u in r.json()["items"]]

    # default stays full_name (raw column order: upper-case first under C collation)
    default = await names("")
    assert sorted(default) == ["Alice", "bob", "carol"] and len(default) == 3
    assert await names("sort=full_name") == ["Alice", "bob", "carol"]
    assert await names("sort=full_name&dir=desc") == ["carol", "bob", "Alice"]
    # role by label: Dept Manager < Requester < System Admin
    assert await names("sort=role") == ["bob", "carol", "Alice"]
    # department by NAME; no department last both ways
    assert await names("sort=department") == ["bob", "carol", "Alice"]
    assert await names("sort=department&dir=desc") == ["carol", "bob", "Alice"]
    # ERP code case-insensitive, NULL last both ways
    assert await names("sort=erp_person_code") == ["bob", "Alice", "carol"]
    assert await names("sort=erp_person_code&dir=desc") == ["Alice", "bob", "carol"]
    # status: Active before Inactive ascending
    assert (await names("sort=status"))[-1] == "Alice"
    assert (await names("sort=status&dir=desc"))[0] == "Alice"
    r1 = await names("sort=department&dir=desc&page=1&page_size=2")
    r2 = await names("sort=department&dir=desc&page=2&page_size=2")
    assert r1 + r2 == ["carol", "bob", "Alice"]
    tot = await admin_client.get(f"/api/v1/users?search={tag}&sort=role&page_size=1")
    assert tot.json()["total"] == 3
    assert (await admin_client.get("/api/v1/users?sort=hashed_password")).status_code == 422
    assert (await admin_client.get("/api/v1/users?sort=role&dir=up")).status_code == 422


@pytest.mark.asyncio
async def test_admin_entity_list_sort(test_engine, admin_client):
    tag = f"Srt{uuid.uuid4().hex[:6]}"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with _factory(test_engine)() as db:
        owner = User(id=uuid.uuid4(), email=f"own-{tag}@t.com", full_name="Owner",
                     role="requester", hashed_password=hash_password("TestPass1!"))
        db.add(owner)
        await db.flush()
        # (title suffix, vendor_name, amount, minutes)
        for title, vendor, amt, m in [("bravo", "zeta Co", "10.00", 1),
                                      ("Alpha", None, "200.00", 2),
                                      ("charlie", "Acme", "9.50", 3)]:
            db.add(PurchaseRequest(id=uuid.uuid4(), number=f"PR-{tag}-{title}",
                                   title=f"{tag} {title}", type=1, created_by=owner.id,
                                   vendor_name=vendor, amount=Decimal(amt),
                                   created_at=t0 + timedelta(minutes=m)))
        await db.commit()

    ents = (await admin_client.get("/api/v1/admin/entities")).json()
    pr = next(e for e in ents if e["key"] == "pr")
    assert pr["sortable_columns"] == pr["list_columns"]

    async def titles(qs):
        r = await admin_client.get(f"/api/v1/admin/pr?search={tag}&{qs}")
        assert r.status_code == 200, r.text
        return [i["title"].split()[1] for i in r.json()["items"]]

    assert await titles("") == ["charlie", "Alpha", "bravo"]          # created_at desc
    assert await titles("sort=title") == ["Alpha", "bravo", "charlie"]
    assert await titles("sort=title&dir=desc") == ["charlie", "bravo", "Alpha"]
    # amount is numeric, not text ("9.50" < "10.00" < "200.00")
    assert await titles("sort=amount") == ["charlie", "bravo", "Alpha"]
    # vendor_name NULL last both ways
    assert await titles("sort=vendor_name") == ["charlie", "bravo", "Alpha"]
    assert await titles("sort=vendor_name&dir=desc") == ["bravo", "charlie", "Alpha"]
    p1 = await titles("sort=amount&dir=desc&page=1&page_size=2")
    p2 = await titles("sort=amount&dir=desc&page=2&page_size=2")
    assert p1 + p2 == ["Alpha", "bravo", "charlie"]
    r = await admin_client.get(f"/api/v1/admin/pr?search={tag}&sort=title&page_size=1")
    assert r.json()["total"] == 3
    assert (await admin_client.get("/api/v1/admin/pr?sort=budget_code")).status_code == 422
    assert (await admin_client.get("/api/v1/admin/pr?sort=title&dir=x")).status_code == 422
    assert (await admin_client.get("/api/v1/admin/nope?sort=title")).status_code == 404


@pytest.fixture(autouse=True)
async def _cleanup(test_engine):
    """Other tests count whole tables (test_admin's cascade test counts every
    PR), so remove what these tests committed."""
    yield
    async with _factory(test_engine)() as db:
        await db.execute(delete(PurchaseRequest).where(PurchaseRequest.number.like("PR-Srt%")))
        await db.execute(delete(User).where(User.email.like("%-srt%@t.com")
                                            | User.email.like("own-Srt%@t.com")))
        await db.execute(delete(Department).where(Department.code.like("_srt%")))
        await db.commit()
