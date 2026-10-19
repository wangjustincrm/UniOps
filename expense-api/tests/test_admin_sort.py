"""Data Maintenance list (GET /admin/{entity}) sorts by any list column it shows:
?sort=<column>&dir=asc|desc in SQL before paging, unknown / non-list column →
422, no sort → the registry's default order (created_at desc). Every
expense_claim list column is NOT NULL, so NULL placement is covered by the
identical code in epms-api/vms-api tests (tests/test_admin_sort.py there)."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.expense import ExpenseClaim


@pytest.mark.asyncio
async def test_admin_entity_list_sort(test_engine, admin_client):
    tag = f"SRT{uuid.uuid4().hex[:6]}"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    # (suffix, employee_name, total, minutes)
    spec = [("b", "bob", "10.00", 1), ("a", "Alice", "200.00", 2), ("c", "carol", "9.50", 3)]
    async with factory() as db:
        for sfx, emp, tot, m in spec:
            db.add(ExpenseClaim(
                id=uuid.uuid4(), claim_number=f"{tag}-{sfx}", claim_type="EXP",
                employee_id=uuid.uuid4(), employee_name=emp, department_name="Finance",
                submission_date=date(2025, 6, 1), currency="CAD", total_amount=Decimal(tot),
                tax_amount=Decimal("0"), net_amount=Decimal(tot), status="draft",
                created_by=uuid.uuid4(), created_at=t0 + timedelta(minutes=m)))
        await db.commit()
    try:
        ents = (await admin_client.get("/api/v1/admin/entities")).json()
        ec = next(e for e in ents if e["key"] == "expense_claim")
        assert ec["sortable_columns"] == ec["list_columns"]

        async def names(qs):
            r = await admin_client.get(f"/api/v1/admin/expense_claim?search={tag}&{qs}")
            assert r.status_code == 200, r.text
            return [i["employee_name"] for i in r.json()["items"]]

        assert await names("") == ["carol", "Alice", "bob"]                  # created_at desc
        assert await names("sort=employee_name") == ["Alice", "bob", "carol"]
        assert await names("sort=employee_name&dir=desc") == ["carol", "bob", "Alice"]
        assert await names("sort=total_amount") == ["carol", "bob", "Alice"]  # numeric
        p1 = await names("sort=total_amount&dir=desc&page=1&page_size=2")
        p2 = await names("sort=total_amount&dir=desc&page=2&page_size=2")
        assert p1 + p2 == ["Alice", "bob", "carol"]
        r = await admin_client.get(f"/api/v1/admin/expense_claim?search={tag}&sort=status&page_size=1")
        assert r.json()["total"] == 3
        assert (await admin_client.get("/api/v1/admin/expense_claim?sort=net_amount")).status_code == 422
        assert (await admin_client.get("/api/v1/admin/expense_claim?sort=status&dir=x")).status_code == 422
        assert (await admin_client.get("/api/v1/admin/nope?sort=status")).status_code == 404
    finally:
        async with factory() as db:
            await db.execute(delete(ExpenseClaim).where(ExpenseClaim.claim_number.like(f"{tag}-%")))
            await db.commit()
