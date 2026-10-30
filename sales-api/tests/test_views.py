"""Saved views (design §13.19): a user's own named list states — CRUD, the
ownership rule (another user's view is not found), one default per list, a
name once per list whatever its case, the state checked against the current
version and the list's fields, the list's read permission, and the system
views each list declares in /meta."""
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.deps import get_uow_factory
from app.main import app
from app.models.records import AuditRecord
from app.platform.audit import AuditTrail
from app.platform.uow import UnitOfWorkFactory
from app.platform.views import VIEW_STATE_VERSION, check_state
from app.services.views import LISTS
from tests.conftest import make_token

GRANTS = {"reader": {"sales.read"}}


async def matrix(session, actor, key):  # noqa: ARG001
    return key in GRANTS.get(actor.role or "", set())


ANN, BOB = uuid.uuid4(), uuid.uuid4()
AS_ANN = {"Authorization": f"Bearer {make_token('reader', ANN)}"}
AS_BOB = {"Authorization": f"Bearer {make_token('reader', BOB)}"}
NOBODY = {"Authorization": f"Bearer {make_token('requester')}"}
STATE = {"version": VIEW_STATE_VERSION, "sort": [{"field": "order_number", "desc": True}],
         "group": [{"field": "partner_code"}], "filters": [{"field": "fulfillment_status", "op": "eq", "value": "Review"}],
         "search": "93", "pageSize": 25, "columns": ["order_number", "partner_code"], "hidden": ["total"]}


@pytest_asyncio.fixture
async def http(db_engine):
    async with db_engine.begin() as c:
        await c.execute(sa.text("TRUNCATE sales_user_views, sales_audit RESTART IDENTITY CASCADE"))
    factory = UnitOfWorkFactory(async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False),
                                permissions=matrix, audit=AuditTrail(AuditRecord))
    app.dependency_overrides[get_uow_factory] = lambda: factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.db = db_engine
        yield c
    app.dependency_overrides.clear()


async def new(http, name="Mine", headers=AS_ANN, **over):
    return await http.post("/api/v1/views", json={"resource": "orders", "name": name, "state": STATE, **over}, headers=headers)


async def test_crud_of_my_views_and_the_audit_trail(http):
    r = await new(http)
    assert r.status_code == 201, r.text
    v = r.json()
    assert (v["name"], v["state"], v["is_default"], v["stale"], v["version"]) == ("Mine", STATE, False, False, 1)
    r = await http.put(f"/api/v1/views/{v['id']}?version=1", json={"name": "Review by partner",
                                                                    "state": {**STATE, "search": None}}, headers=AS_ANN)
    assert r.status_code == 200 and r.json()["name"] == "Review by partner" and r.json()["version"] == 2
    assert r.json()["state"]["search"] is None
    stale = await http.put(f"/api/v1/views/{v['id']}?version=1", json={"name": "x"}, headers=AS_ANN)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "entity.stale"
    assert [x["name"] for x in (await http.get("/api/v1/views?resource=orders", headers=AS_ANN)).json()] == ["Review by partner"]
    assert (await http.delete(f"/api/v1/views/{v['id']}", headers=AS_ANN)).status_code == 204
    assert (await http.get("/api/v1/views?resource=orders", headers=AS_ANN)).json() == []
    async with http.db.connect() as c:
        actions = (await c.execute(sa.text("SELECT action FROM sales_audit WHERE entity = 'sales_user_views' ORDER BY id"))).scalars().all()
    assert actions == ["create", "update", "delete"]


async def test_another_users_view_is_not_found(http):
    v = (await new(http)).json()
    for call in (http.put(f"/api/v1/views/{v['id']}", json={"name": "hijack"}, headers=AS_BOB),
                 http.delete(f"/api/v1/views/{v['id']}", headers=AS_BOB),
                 http.post(f"/api/v1/views/{v['id']}/default", headers=AS_BOB)):
        r = await call
        assert r.status_code == 404, r.text
    assert (await http.get("/api/v1/views?resource=orders", headers=AS_BOB)).json() == []
    assert (await new(http, headers=AS_BOB)).status_code == 201  # Bob's own "Mine" is fine
    assert [x["name"] for x in (await http.get("/api/v1/views?resource=orders", headers=AS_ANN)).json()] == ["Mine"]


async def test_one_default_per_list_and_a_name_once_whatever_its_case(http):
    a, b = (await new(http, "A")).json(), (await new(http, "B")).json()
    assert (await http.post(f"/api/v1/views/{a['id']}/default", headers=AS_ANN)).json()["is_default"] is True
    assert (await http.post(f"/api/v1/views/{b['id']}/default", headers=AS_ANN)).json()["is_default"] is True
    mine = {x["name"]: x["is_default"] for x in (await http.get("/api/v1/views?resource=orders", headers=AS_ANN)).json()}
    assert mine == {"A": False, "B": True}
    off = await http.post(f"/api/v1/views/{b['id']}/default?on=false", headers=AS_ANN)
    assert off.json()["is_default"] is False
    r = await new(http, "a")
    assert r.status_code == 409 and r.json()["error"]["code"] == "view.name_taken"
    r = await http.put(f"/api/v1/views/{b['id']}", json={"name": " A "}, headers=AS_ANN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "view.name_taken"
    async with http.db.begin() as c:  # the database holds the rule too (a race between two requests)
        with pytest.raises(sa.exc.IntegrityError):
            await c.execute(sa.text("UPDATE sales_user_views SET is_default = true"))


@pytest.mark.parametrize("state, code", [
    ({"version": 99, "sort": []}, "view.bad_version"),
    ({"sort": []}, "view.bad_state"),
    ({"version": VIEW_STATE_VERSION, "colour": "red"}, "view.bad_state"),
    ({"version": VIEW_STATE_VERSION, "pageSize": 1000}, "view.bad_state"),
    ({"version": VIEW_STATE_VERSION, "filters": [{"field": "secret_name", "op": "eq", "value": "x"}]}, "view.unknown_field"),
    ({"version": VIEW_STATE_VERSION, "group": [{"field": "review_reason"}]}, "view.unknown_field"),  # not groupable
])
async def test_a_state_this_version_cannot_read_is_refused(http, state, code):
    r = await new(http, state=state)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


async def test_a_view_that_no_longer_fits_is_flagged_stale_not_dropped(http):
    v = (await new(http)).json()
    async with http.db.begin() as c:  # saved by an older client, or naming a field since removed
        await c.execute(sa.text("UPDATE sales_user_views SET state = jsonb_set(state, '{filters}', "
                                "'[{\"field\": \"gone\", \"op\": \"eq\", \"value\": \"x\"}]')"))
    [row] = (await http.get("/api/v1/views?resource=orders", headers=AS_ANN)).json()
    assert row["id"] == v["id"] and row["stale"] is True


async def test_views_need_the_lists_read_permission_and_a_known_list(http):
    assert (await http.get("/api/v1/views?resource=orders", headers=NOBODY)).status_code == 403
    assert (await new(http, headers=NOBODY)).status_code == 403
    r = await http.get("/api/v1/views?resource=../identity/users", headers=AS_ANN)
    assert r.status_code == 422 and r.json()["error"]["code"] == "view.unknown_resource"


async def test_system_views_are_in_meta_and_fit_their_list(http):
    m = (await http.get("/api/v1/orders/meta", headers=AS_ANN)).json()
    assert [v["key"] for v in m["views"]] == ["all", "needs_review", "payment_hold", "pending", "archive_suppressed",
                                              "historical"]
    review = next(v for v in m["views"] if v["key"] == "needs_review")
    assert review["name"] == "Needs review" and review["state"]["filters"] == [
        {"field": "fulfillment_status", "op": "eq", "value": "Review"}]
    for resource, svc in LISTS.items():  # every system view of every list is a state its list accepts
        repo = svc.repo_cls
        assert repo.presets, resource
        for p in repo.presets:
            check_state(p.as_dict()["state"], repo)
