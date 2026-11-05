"""Data Maintenance list (GET /admin/{entity}) sorts by any list column it shows:
?sort=<column>&dir=asc|desc in SQL before paging, NULLs last both ways,
unknown / non-list column → 422, no sort → the registry's default order."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.visitor import Visitor
from tests.conftest import authed_client, make_token, make_user


@pytest.mark.asyncio
async def test_admin_entity_list_sort(test_engine):
    user = await make_user(test_engine, role="system_admin")
    tag = f"Srt{uuid.uuid4().hex[:6]}"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # (first_name, email, minutes after t0)
    spec = [("bob", "b@x.com", 1), ("Alice", None, 2), ("carol", "A@x.com", 3)]
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add_all([Visitor(id=uuid.uuid4(), first_name=f, last_name=tag, company_name="Co",
                            email=e, created_at=t0 + timedelta(minutes=m)) for f, e, m in spec])
        await db.commit()

    async with authed_client(make_token(user.id, "system_admin")) as c:
        ents = (await c.get("/api/v1/admin/entities")).json()
        visitor = next(e for e in ents if e["key"] == "visitor")
        assert visitor["sortable_columns"] == visitor["list_columns"]

        async def names(qs):
            r = await c.get(f"/api/v1/admin/visitor?search={tag}&{qs}")
            assert r.status_code == 200, r.text
            return [i["first_name"] for i in r.json()["items"]]

        assert await names("") == ["carol", "Alice", "bob"]            # created_at desc
        assert await names("sort=first_name") == ["Alice", "bob", "carol"]
        assert await names("sort=first_name&dir=desc") == ["carol", "bob", "Alice"]
        assert await names("sort=email&dir=asc") == ["carol", "bob", "Alice"]   # NULL last
        assert await names("sort=email&dir=desc") == ["bob", "carol", "Alice"]  # still last
        p1 = await names("sort=email&dir=desc&page=1&page_size=2")
        p2 = await names("sort=email&dir=desc&page=2&page_size=2")
        assert p1 + p2 == ["bob", "carol", "Alice"]
        r = await c.get(f"/api/v1/admin/visitor?search={tag}&sort=first_name&page_size=1")
        assert r.json()["total"] == 3
        # not a list column (exists on the model, but the table doesn't show it)
        assert (await c.get("/api/v1/admin/visitor?sort=phone")).status_code == 422
        assert (await c.get("/api/v1/admin/visitor?sort=id;drop")).status_code == 422
        assert (await c.get("/api/v1/admin/visitor?sort=first_name&dir=x")).status_code == 422
        # unknown entity is still a 404, not a 422
        assert (await c.get("/api/v1/admin/nope?sort=first_name")).status_code == 404
