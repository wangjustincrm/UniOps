"""被指派人 match 的偏差复核流。"""
import uuid

import pytest
from sqlalchemy import select

from tests.test_invoice_assign import (
    INV_URL, _client_for_user, _make_invoice, _make_po, _make_user, _make_vendor,
    _open_match_task,
)


async def _open_review_task(invoice_id):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        return (await db.execute(select(Task).where(
            Task.type == "review_match",
            Task.document_id == uuid.UUID(invoice_id),
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()


async def _assigned_variance_match(admin_client, *, code, number, amount="900.00"):
    """搭台:PO 1000,发票 900(有偏差),指派并由被指派人 match。返回 (inv, assignee)。"""
    v = await _make_vendor(admin_client, code)
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number=number, amount=amount,
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": amount, "line_total": amount}])
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})
    async with await _client_for_user(assignee) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": amount, "allocated_tax": "0.00"}]})
        assert r.status_code == 200, r.text
        inv = r.json()
    return inv, assignee


@pytest.mark.asyncio
async def test_assignee_variance_goes_to_review(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-01", number="REV-001")
    assert inv["status"] == "match_review"
    assert await _open_match_task(inv["id"]) is None          # match 任务完成
    review = await _open_review_task(inv["id"])
    assert review is not None
    assert review.assigned_user_id is not None                 # 给 assigner


@pytest.mark.asyncio
async def test_review_approve_lands_by_tolerance(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-02", number="REV-002")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={"action": "approve"})
    assert r.status_code == 200, r.text
    # 少开(-10%)不再是 exception:部分开票放行(2026-07-10 规则),approve → matched
    assert r.json()["status"] == "matched"
    assert await _open_review_task(inv["id"]) is None


@pytest.mark.asyncio
async def test_review_approve_overbilled_lands_exception(admin_client):
    """超开(+20%)approve 后仍按容差拦截 → exception。"""
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-08",
                                            number="REV-008", amount="1200.00")
    assert inv["status"] == "match_review"
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={"action": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exception"


@pytest.mark.asyncio
async def test_review_reject_returns_unmatched_and_recreates_task(admin_client):
    inv, assignee = await _assigned_variance_match(admin_client, code="VND-REV-03", number="REV-003")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review",
                                json={"action": "reject", "note": "Wrong PO line"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unmatched"
    task = await _open_match_task(inv["id"])
    assert task is not None and task.assigned_user_id == assignee
    assert "Wrong PO line" in (task.description or "")
    assert await _open_review_task(inv["id"]) is None


@pytest.mark.asyncio
async def test_review_reject_requires_note(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-04", number="REV-004")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={"action": "reject"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_match_locked_while_in_review(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-05", number="REV-005")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": str(uuid.uuid4())})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_review_approve_within_tolerance_matched(admin_client):
    """容差放宽到 15% 时,-10% 偏差的 approve → matched(而非 exception)。"""
    import app.db.session as session_module
    from decimal import Decimal
    from app.crud import config as config_crud

    from sqlalchemy import update as sa_update
    from app.models.config import CompanyConfig as CompanyConfigModel

    async with session_module.AsyncSessionLocal() as db:
        cfg = await config_crud.get_or_create(db)
        original = cfg.invoice_match_tolerance_pct
        await db.commit()

    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            sa_update(CompanyConfigModel).values(invoice_match_tolerance_pct=Decimal("15"))
        )
        await db.commit()
    try:
        inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-07", number="REV-007")
        assert inv["status"] == "match_review"
        r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={"action": "approve"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "matched"
        assert await _open_review_task(inv["id"]) is None
    finally:
        async with session_module.AsyncSessionLocal() as db:
            await db.execute(
                sa_update(CompanyConfigModel).values(invoice_match_tolerance_pct=original)
            )
            await db.commit()


@pytest.mark.asyncio
async def test_ap_direct_match_unaffected(admin_client):
    """AP 自己少开 match → 部分开票放行落 matched,且不进 review。"""
    v = await _make_vendor(admin_client, "VND-REV-06")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="REV-006", amount="900.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "900.00", "line_total": "900.00"}])
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "900.00", "allocated_tax": "0.00"}]})
    assert r.json()["status"] == "matched"   # 少开放行(部分开票);超开场景见 allocations 测试
