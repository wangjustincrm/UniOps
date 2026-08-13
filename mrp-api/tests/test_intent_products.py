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
