"""Sync NC 的门禁必须按**角色并集**判定,而不是只看 JWT 里的主角色。

生产上 `erp_pa_officer` 是 3 个人的**附加角色**(user_roles),他们的主角色都是
`requester`。把角色码加进 `_SYNC_ROLES` 而门禁仍读 `payload["role"]`,一个人也放
不进来 —— 按钮出来了、点下去 403,正是「建对了但用户走不到」那一类。

`/status` 的 `can_sync` 必须与真门禁同源,否则 EPMS PO List 上的按钮会和后端各说各话。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.schemas.auth import RegisterRequest


async def _client_with_roles(test_engine, base_role: str, *additional: str):
    """一个 base_role 用户,外加若干 user_roles 附加角色,返回已认证的 client。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{base_role}-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name=f"T {base_role}", role=base_role))
        for code in additional:
            await db.execute(text(
                "INSERT INTO user_roles(user_id, role_code) VALUES (:u, :c)"),
                {"u": str(user.id), "c": code})
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_erp_pa_officer_held_as_additional_role_may_trigger_sync(test_engine, monkeypatch):
    """主角色 requester + 附加角色 erp_pa_officer —— 生产上那 3 个人的真实形状。
    503(NC 未配置)证明已经过了授权;403 才是没过。"""
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: False)
    async with await _client_with_roles(test_engine, "requester", "erp_pa_officer") as c:
        r = await c.post("/api/v1/admin/nc-purchase-sync", json={"mode": "incremental"})
    assert r.status_code == 503, f"被授权挡住了:{r.status_code}"


@pytest.mark.asyncio
async def test_status_can_sync_true_for_erp_pa_officer_additional_role(test_engine):
    """按钮的可见性读的就是这个字段,它必须和上面的门禁给出同一个答案。"""
    async with await _client_with_roles(test_engine, "requester", "erp_pa_officer") as c:
        r = await c.get("/api/v1/admin/nc-purchase-sync/status")
    assert r.status_code == 200
    assert r.json()["can_sync"] is True


@pytest.mark.asyncio
async def test_erp_pa_officer_as_base_role_also_counts(test_engine, monkeypatch):
    """并集里也包含基础角色本身 —— 将来有人把它设成主角色不能反而失效。"""
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: False)
    async with await _client_with_roles(test_engine, "erp_pa_officer") as c:
        r = await c.post("/api/v1/admin/nc-purchase-sync", json={"mode": "incremental"})
        s = await c.get("/api/v1/admin/nc-purchase-sync/status")
    assert r.status_code == 503
    assert s.json()["can_sync"] is True


@pytest.mark.asyncio
async def test_procurement_officer_unchanged(test_engine, monkeypatch):
    """反向对照 1:原有的采购员权限不能被这次改动弄丢。"""
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: False)
    async with await _client_with_roles(test_engine, "procurement_officer") as c:
        r = await c.post("/api/v1/admin/nc-purchase-sync", json={"mode": "incremental"})
        s = await c.get("/api/v1/admin/nc-purchase-sync/status")
    assert r.status_code == 503
    assert s.json()["can_sync"] is True


@pytest.mark.asyncio
async def test_plain_requester_still_refused(test_engine, monkeypatch):
    """反向对照 2:没有那个附加角色的普通 requester 仍然进不来,按钮也不该出现。"""
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: False)
    async with await _client_with_roles(test_engine, "requester") as c:
        r = await c.post("/api/v1/admin/nc-purchase-sync", json={"mode": "incremental"})
        s = await c.get("/api/v1/admin/nc-purchase-sync/status")
    assert r.status_code == 403
    assert s.json()["can_sync"] is False


@pytest.mark.asyncio
async def test_full_reload_stays_system_admin_only(test_engine, monkeypatch):
    """erp_pa_officer 只拿到增量同步。全量重载会重建镜像,仍然只归 system_admin。"""
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: True)
    async with await _client_with_roles(test_engine, "requester", "erp_pa_officer") as c:
        r = await c.post("/api/v1/admin/nc-purchase-sync",
                         json={"mode": "full", "confirm": "RELOAD"})
        s = await c.get("/api/v1/admin/nc-purchase-sync/status")
    assert r.status_code == 403
    assert s.json()["can_set_cutover"] is False
