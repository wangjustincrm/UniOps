"""超容差发票必须产生一条可见、可通知的 AP 任务,并在解决后关闭。

在此之前 exception 状态不产生任何 Task —— AP 收不到提醒不是通知丢了,
是任务压根不存在。
"""
import uuid

import pytest
from sqlalchemy import select

# 夹具分散在两个模块里(2026-08-13 实测):
#   test_invoice_allocations —— INV_URL / _make_vendor / _make_issued_po
#     (_make_issued_po 直建已下达 PO,不走审批服务,所以本地能跑)
#   test_invoice_assign     —— _make_invoice
#     (该模块的 _make_po 会走审批提交,本地 401→502,别用它)
from tests.test_invoice_allocations import INV_URL, _make_issued_po, _make_vendor
from tests.test_invoice_assign import _make_invoice


async def _exception_task(invoice_id, *, open_only=True):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        stmt = select(Task).where(
            Task.type == "resolve_exception",
            Task.document_id == uuid.UUID(invoice_id),
        )
        if open_only:
            stmt = stmt.where(Task.is_completed.is_(False))
        return (await db.execute(stmt)).scalar_one_or_none()


@pytest.mark.asyncio
async def test_exception_creates_ap_pool_task(admin_client):
    v = await _make_vendor(admin_client, "EXC1")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    # 200 vs PO 100 = +100%,远超默认 5% 容差
    inv = await _make_invoice(admin_client, v["id"], number="EXC-0001", amount="200.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "200.00", "line_total": "200.00"}])
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "200.00", "allocated_tax": "0.00"}]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exception", r.json()

    task = await _exception_task(inv["id"])
    assert task is not None, "exception 状态必须产生一条 AP 任务"
    assert task.assigned_role == "ap_clerk"
    assert task.assigned_user_id is None, "必须走角色池,才能命中 AP 共享邮箱"
    assert task.document_type == "invoice"


@pytest.mark.asyncio
async def test_resolving_exception_closes_the_task(admin_client):
    v = await _make_vendor(admin_client, "EXC2")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="EXC-0002", amount="200.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "200.00", "line_total": "200.00"}])
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "200.00", "allocated_tax": "0.00"}]})
    assert await _exception_task(inv["id"]) is not None

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception",
                                json={"resolution": "accepted", "note": "approved by ops"})
    assert r.status_code == 200, r.text

    assert await _exception_task(inv["id"]) is None, "解决后不得留下开放任务"
    closed = await _exception_task(inv["id"], open_only=False)
    assert closed is not None and closed.is_completed is True
