"""part_status 别名 —— 向前兼容的读端修复(spec §6.1)。

'A' 同时匹配 'A' 和 NC 的 ENABLESTATE 原值 '2'。这样将来写端归一成
'A'/'B' 之后这段代码不用再改一次,两种口径并存的窗口期也不会失效。
"""
from datetime import datetime, timezone

import pytest

from app.crud import erp as erp_crud
from app.models.erp_material import ErpMaterial

# ErpMaterial.raw_payload / synced_at are NOT NULL with no default at the
# model or DB level, so every fixture row below must supply them explicitly.
_SYNCED_AT = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_filter_A_matches_legacy_numeric_2(db_session):
    db_session.add(ErpMaterial(
        erp_part_no="CR0297", part_status="2",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == 1
    assert items[0].erp_part_no == "CR0297"


@pytest.mark.asyncio
async def test_filter_A_still_matches_literal_A(db_session):
    db_session.add(ErpMaterial(
        erp_part_no="CR0298", part_status="A",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == 1
    assert items[0].erp_part_no == "CR0298"


@pytest.mark.asyncio
async def test_filter_B_matches_legacy_numeric_3(db_session):
    db_session.add(ErpMaterial(
        erp_part_no="CR0299", part_status="3",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="B")
    assert total == 1


@pytest.mark.asyncio
async def test_filter_A_excludes_inactive(db_session):
    db_session.add(ErpMaterial(
        erp_part_no="CR0300", part_status="3",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == 0
    assert items == []


@pytest.mark.asyncio
async def test_total_and_items_agree(db_session):
    """count 查询和取行查询必须用同一个过滤条件 —— 只改一条会让分页错乱。"""
    db_session.add(ErpMaterial(
        erp_part_no="CR0301", part_status="2",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    db_session.add(ErpMaterial(
        erp_part_no="CR0302", part_status="3",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == len(items) == 1


@pytest.mark.asyncio
async def test_no_filter_returns_everything(db_session):
    db_session.add(ErpMaterial(
        erp_part_no="CR0303", part_status="2",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    db_session.add(ErpMaterial(
        erp_part_no="CR0304", part_status="3",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session)
    assert total == 2


@pytest.mark.asyncio
async def test_unknown_status_still_matches_itself(db_session):
    """别名表里没有的值原样比较,不要吞掉。"""
    db_session.add(ErpMaterial(
        erp_part_no="CR0305", part_status="Z",
        raw_payload={}, synced_at=_SYNCED_AT,
    ))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="Z")
    assert total == 1
