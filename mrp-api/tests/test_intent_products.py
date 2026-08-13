import pytest
from sqlalchemy import text


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
