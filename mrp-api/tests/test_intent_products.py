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
async def test_bind_rejects_when_target_already_has_forecast(client, auth_headers):
    """D11: business says this cannot happen — so it must be loud, not silently merged."""
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


@pytest.mark.asyncio
async def test_bind_is_rejected_twice(client, auth_headers):
    intent = (await client.post("/api/v1/intent-products", json={"name": "Once"},
                                headers=auth_headers)).json()
    await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                      json={"material_code": "S0074"}, headers=auth_headers)
    r = await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0075"}, headers=auth_headers)
    assert r.status_code == 409


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
    sources = (await db_session.execute(text(
        "select source from mrp_forecast_change_log where material_code = 'S0064'"
    ))).scalars().all()
    assert "intent_bind" in sources
