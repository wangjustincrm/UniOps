import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_create_intent_product_generates_placeholder_code(client, auth_headers):
    r = await client.post("/api/v1/intent-products",
                          json={"name": "Stage 3 New Formula", "note": "planning"},
                          headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"].startswith("INTENT-")
    assert len(body["code"]) == len("INTENT-") + 8
    assert body["name"] == "Stage 3 New Formula"
    assert body["status"] == "active"
    assert body["bound_material_code"] is None


@pytest.mark.asyncio
async def test_create_intent_product_rejects_blank_name(client, auth_headers):
    r = await client.post("/api/v1/intent-products", json={"name": "   "}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_active_only_by_default(client, auth_headers):
    created = (await client.post("/api/v1/intent-products", json={"name": "Keeper"},
                                 headers=auth_headers)).json()
    dropped = (await client.post("/api/v1/intent-products", json={"name": "Gone"},
                                 headers=auth_headers)).json()
    await client.post(f"/api/v1/intent-products/{dropped['id']}/drop", headers=auth_headers)

    codes = [i["code"] for i in (await client.get("/api/v1/intent-products",
                                                  headers=auth_headers)).json()]
    assert created["code"] in codes
    assert dropped["code"] not in codes


@pytest.mark.asyncio
async def test_list_status_all_query_param_includes_dropped(client, auth_headers):
    """Fix round 1: the wire contract is `?status=all`, not `?status_filter=all`
    (FastAPI silently ignores unrecognized query params, so this must
    actually exercise the aliased name or it proves nothing)."""
    created = (await client.post("/api/v1/intent-products", json={"name": "Keeper"},
                                 headers=auth_headers)).json()
    dropped = (await client.post("/api/v1/intent-products", json={"name": "Gone"},
                                 headers=auth_headers)).json()
    await client.post(f"/api/v1/intent-products/{dropped['id']}/drop", headers=auth_headers)

    default_codes = [i["code"] for i in (await client.get(
        "/api/v1/intent-products", headers=auth_headers)).json()]
    assert dropped["code"] not in default_codes

    all_codes = [i["code"] for i in (await client.get(
        "/api/v1/intent-products", params={"status": "all"}, headers=auth_headers)).json()]
    assert created["code"] in all_codes
    assert dropped["code"] in all_codes


@pytest.mark.asyncio
async def test_intent_tables_exist(db_session):
    """mrp09 建表 + forecast_lines 加列。"""
    cols = (await db_session.execute(text(
        "select column_name from information_schema.columns "
        "where table_name = 'mrp_intent_products'"
    ))).scalars().all()
    assert {"id", "code", "name", "note", "status",
            "bound_material_code", "bound_at", "bound_by", "created_by"} <= set(cols)

    fc = (await db_session.execute(text(
        "select column_name from information_schema.columns "
        "where table_name = 'mrp_forecast_lines'"
    ))).scalars().all()
    assert {"is_intent", "intent_name"} <= set(fc)


@pytest.mark.asyncio
async def test_intent_code_is_unique(db_session):
    from app.models.intent import MrpIntentProduct
    db_session.add(MrpIntentProduct(code="INTENT-aaaaaaaa", name="A", status="active"))
    await db_session.flush()
    db_session.add(MrpIntentProduct(code="INTENT-aaaaaaaa", name="B", status="active"))
    with pytest.raises(Exception):
        await db_session.flush()


@pytest.mark.asyncio
async def test_bind_moves_series_rows_and_marks_bound(client, auth_headers, db_session):
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "New SKU"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-01", "qty": "1000"},
        {"material_code": intent["code"], "month": "2027-02", "qty": "2000"},
    ]})

    r = await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0093"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["moved_months"] == 2

    rows = (await db_session.execute(text(
        "select month, qty from mrp_demand_series where material_code = 'S0093' order by month"
    ))).all()
    assert [(m, str(q)) for m, q in rows] == [("2027-01", "1000.000"), ("2027-02", "2000.000")]
    assert (await db_session.execute(text(
        "select count(*) from mrp_demand_series where material_code = :c"
    ), {"c": intent["code"]})).scalar() == 0

    detail = (await client.get("/api/v1/intent-products?status=all",
                               headers=auth_headers)).json()
    bound = [i for i in detail if i["id"] == intent["id"]][0]
    assert bound["status"] == "bound"
    assert bound["bound_material_code"] == "S0093"


@pytest.mark.asyncio
async def test_bind_rejects_when_target_already_has_forecast(client, auth_headers, db_session):
    """D11: business says this cannot happen — so it must be loud, not silently merged.

    Fix round 1: a status code alone doesn't prove "reject and change
    nothing" — it would still pass if the clash check moved below the
    first UPDATE. Re-query everything the bind would have touched.
    """
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "Collides"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-01", "qty": "50"},
    ]})
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": "S0060", "month": "2027-01", "qty": "200"},
    ]})

    r = await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0060"}, headers=auth_headers)
    assert r.status_code == 409
    assert "already has forecast" in r.json()["detail"].lower()

    detail = (await client.get("/api/v1/intent-products?status=all",
                               headers=auth_headers)).json()
    row = [i for i in detail if i["id"] == intent["id"]][0]
    assert row["status"] == "active"
    assert row["bound_material_code"] is None

    own_rows = (await db_session.execute(text(
        "select month, qty from mrp_demand_series where material_code = :c order by month"
    ), {"c": intent["code"]})).all()
    assert [(m, str(q)) for m, q in own_rows] == [("2027-01", "50.000")]

    target_rows = (await db_session.execute(text(
        "select month, qty from mrp_demand_series where material_code = 'S0060' order by month"
    ))).all()
    assert [(m, str(q)) for m, q in target_rows] == [("2027-01", "200.000")]


@pytest.mark.asyncio
async def test_bind_is_rejected_twice(client, auth_headers, db_session):
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "Once"},
                                headers=auth_headers)).json()
    await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                      json={"material_code": "S0074"}, headers=auth_headers)
    r = await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0075"}, headers=auth_headers)
    assert r.status_code == 409

    detail = (await client.get("/api/v1/intent-products?status=all",
                               headers=auth_headers)).json()
    row = [i for i in detail if i["id"] == intent["id"]][0]
    assert row["status"] == "bound"
    assert row["bound_material_code"] == "S0074"

    assert (await db_session.execute(text(
        "select count(*) from mrp_demand_series where material_code = 'S0075'"
    ))).scalar() == 0
    assert (await db_session.execute(text(
        "select count(*) from mrp_forecast_change_log where material_code = 'S0075'"
    ))).scalar() == 0


@pytest.mark.asyncio
async def test_bind_rewrites_change_log_and_leaves_an_audit_row(client, auth_headers, db_session):
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "Audited"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-03", "qty": "10"},
    ]})
    await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                      json={"material_code": "S0064"}, headers=auth_headers)

    assert (await db_session.execute(text(
        "select count(*) from mrp_forecast_change_log where material_code = :c"
    ), {"c": intent["code"]})).scalar() == 0
    log_rows = (await db_session.execute(text(
        "select source, changed_by from mrp_forecast_change_log where material_code = 'S0064'"
    ))).all()
    sources = [s for s, _ in log_rows]
    assert "intent_bind" in sources
    intent_bind_row = [row for row in log_rows if row[0] == "intent_bind"][0]
    assert intent_bind_row[1] is not None  # changed_by — audit row must have an author


@pytest.mark.asyncio
async def test_bind_is_atomic_when_the_second_update_fails(
    client, auth_headers, db_session, monkeypatch,
):
    """A half-renamed forecast (series moved, change log not — or vice
    versa) is the single worst outcome this feature can produce. Force a
    failure between the two UPDATEs inside bind_intent_to_material by
    making `update(MrpForecastChangeLog)` raise, and prove nothing landed:
    not the series rows, not the change log, not the intent product's
    status.

    The app has a catch-all `Exception` handler (app/main.py) that sends a
    500 response, but Starlette's ServerErrorMiddleware re-raises the
    original exception after sending it (that's what lets a test client
    see it) — and httpx's ASGITransport defaults to re-raising app
    exceptions to the caller (`raise_app_exceptions=True`), so `client.post`
    itself raises here rather than returning a response.

    The test harness's `client` fixture overrides `get_session` with a
    bare `yield db_session` (no try/except) — unlike the real
    `get_session`, which rolls back on any exception. So the assertions
    below explicitly roll back `db_session` first, to reproduce exactly
    what production's real dependency does on this same failure path
    before checking the database is untouched.
    """
    from sqlalchemy import text

    import app.services.intent_products as intent_products_module

    intent = (await client.post("/api/v1/intent-products", json={"name": "Half-renamed"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-04", "qty": "77"},
    ]})

    original_update = intent_products_module.update

    def _boom(table):
        if table is intent_products_module.MrpForecastChangeLog:
            raise RuntimeError("simulated failure between the two UPDATEs")
        return original_update(table)

    monkeypatch.setattr(intent_products_module, "update", _boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0081"}, headers=auth_headers)

    await db_session.rollback()

    assert (await db_session.execute(text(
        "select count(*) from mrp_demand_series where material_code = 'S0081'"
    ))).scalar() == 0
    own_rows = (await db_session.execute(text(
        "select month, qty from mrp_demand_series where material_code = :c order by month"
    ), {"c": intent["code"]})).all()
    assert [(m, str(q)) for m, q in own_rows] == [("2027-04", "77.000")]

    monkeypatch.setattr(intent_products_module, "update", original_update)
    detail = (await client.get("/api/v1/intent-products?status=all",
                               headers=auth_headers)).json()
    row = [i for i in detail if i["id"] == intent["id"]][0]
    assert row["status"] == "active"
    assert row["bound_material_code"] is None
