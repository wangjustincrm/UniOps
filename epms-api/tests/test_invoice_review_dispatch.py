"""review_match 任务必须落到 ap_clerk 角色池,而不是钉给某一个人。

钉给个人会让通知只发给派单者本人,并且绕过角色共享邮箱
(services/notification.py 只在 assigned_user_id 为空时才查共享邮箱)。
"""
import uuid

import pytest
from sqlalchemy import select

# 夹具分散在两个模块里(2026-08-13 实测):
#   test_invoice_allocations —— INV_URL / _make_vendor / _make_issued_po
#     (_make_issued_po 直建已下达 PO,不走审批服务,所以本地能跑)
#   test_invoice_assign     —— _make_invoice / _client_for_user / _make_user
#     (该模块的 _make_po 会走审批提交,本地 401→502,别用它)
from tests.test_invoice_allocations import INV_URL, _make_issued_po, _make_vendor
from tests.test_invoice_assign import _client_for_user, _make_invoice, _make_user


async def _delegate_matches_with_variance(admin_client, inv, po):
    """非 AP、非上传人的被指派人持有开放 match_invoice 任务,走 /match 接口完成匹配
    (发票金额与 PO 有偏差 → 触发 require_review → match_review)。"""
    assignee = await _make_user()
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                 json={"user_id": str(assignee)})
    assert r.status_code == 200, r.text
    line = inv["line_items"][0]
    async with await _client_for_user(assignee) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": line["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": line["line_total"], "allocated_tax": "0.00"}]})
        assert r.status_code == 200, r.text
        return r.json()


async def _review_task(invoice_id):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        return (await db.execute(select(Task).where(
            Task.type == "review_match",
            Task.document_id == uuid.UUID(invoice_id),
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_review_task_goes_to_role_pool_not_one_person(admin_client):
    v = await _make_vendor(admin_client, "RVW1")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="RVW-0001", amount="900.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "900.00", "line_total": "900.00"}])

    delegate = await _delegate_matches_with_variance(admin_client, inv, po)
    assert delegate["status"] == "match_review", delegate

    task = await _review_task(inv["id"])
    assert task is not None, "match_review 状态必须伴随一个 review_match 任务"
    assert task.assigned_role == "ap_clerk"
    assert task.assigned_user_id is None, (
        "钉住个人会绕过 AP 共享邮箱、且只通知派单者一人")
