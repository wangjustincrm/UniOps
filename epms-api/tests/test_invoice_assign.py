"""Match-PO 指派:端点、改派、通知任务。"""
import uuid

import pytest
from sqlalchemy import select

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Assign Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_po(client, vendor_id, lines):
    po = await client.post(PO_URL, json={
        "title": "Assign PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": lines,
    })
    po.raise_for_status()
    return po.json()


async def _make_invoice(client, vendor_id, *, number, amount="1000.00", lines=None):
    r = await client.post(INV_URL, json={
        "vendor_id": vendor_id, "vendor_invoice_number": number,
        "amount": amount, "tax_amount": "0.00", "currency": "CAD",
        "invoice_date": "2026-07-09", "due_date": "2026-08-08",
        "line_items": lines or [{"description": "L1", "quantity": "1",
                                 "unit_price": amount, "line_total": amount}],
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _make_user(role="requester"):
    """直接建一个激活用户,返回其 id(指派对象)。"""
    import app.db.session as session_module
    from app.models.user import User
    uid = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=uid,
                    email=f"{uid.hex[:8]}@test.local",
                    full_name="Assignee Test",
                    role=role, is_active=True, hashed_password="x"))
        await db.commit()
    return uid


async def _open_match_task(invoice_id):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        return (await db.execute(select(Task).where(
            Task.type == "match_invoice",
            Task.document_id == uuid.UUID(invoice_id),
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_assign_match_creates_task(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-01")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-001")
    assignee = await _make_user()

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                json={"user_id": str(assignee)})
    assert r.status_code == 200, r.text

    task = await _open_match_task(inv["id"])
    assert task is not None
    assert task.assigned_user_id == assignee
    assert task.created_by is not None          # assigner 记录在 created_by
    assert task.document_number == inv["internal_ref"]


@pytest.mark.asyncio
async def test_assign_match_reassigns_existing_task(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-02")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-002")
    first, second = await _make_user(), await _make_user()

    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(first)})
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(second)})
    assert r.status_code == 200

    task = await _open_match_task(inv["id"])
    assert task.assigned_user_id == second      # 改派,不是第二条任务
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        n = len((await db.execute(select(Task).where(
            Task.type == "match_invoice",
            Task.document_id == uuid.UUID(inv["id"]),
        ))).scalars().all())
    assert n == 1


@pytest.mark.asyncio
async def test_assign_match_requires_ap_role(requester_client, admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-03")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-003")
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                    json={"user_id": str(uuid.uuid4())})
    assert r.status_code == 403


async def _ensure_company_config():
    """Seed the singleton CompanyConfig so GET endpoints' view_invoice perm is on.
    Idempotent — safe to call multiple times."""
    import app.db.session as session_module
    from app.crud import config as config_crud
    async with session_module.AsyncSessionLocal() as db:
        await config_crud.get_or_create(db)
        await db.commit()


async def _client_for_user(user_id, role="requester"):
    """Give specified user_id a logged-in client (uses create_access_token directly)."""
    from httpx import ASGITransport, AsyncClient
    from app.core.security import create_access_token
    from app.main import create_app
    token = create_access_token(str(user_id), role)
    app = create_app()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_assignee_can_match_and_see_invoice(admin_client):
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ASSIGN-05")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-005")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})

    async with await _client_for_user(assignee) as c:
        # visibility: detail no longer 404s
        assert (await c.get(f"{INV_URL}/{inv['id']}")).status_code == 200
        # list includes this invoice
        listed = (await c.get(INV_URL)).json()
        assert any(i["id"] == inv["id"] for i in listed["items"])
        # authorisation: zero-variance match succeeds
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "matched"

    # match task auto-completed (Task 6 implements this; assertion may still fail
    # until Task 6 is merged — intentional per task-5 scope)
    task = await _open_match_task(inv["id"])
    assert task is None


@pytest.mark.asyncio
async def test_user_without_task_still_403(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-06")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-006")
    stranger = await _make_user()
    async with await _client_for_user(stranger) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_assign_match_rejects_matched_invoice(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-04")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-004")
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                json={"user_id": str(await _make_user())})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_response_surfaces_assignee(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-07")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-007")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})

    detail = (await admin_client.get(f"{INV_URL}/{inv['id']}")).json()
    assert detail["match_assignee_id"] == str(assignee)
    assert detail["match_assignee_name"] == "Assignee Test"

    listed = (await admin_client.get(INV_URL, params={"search": "ASSIGN-007"})).json()
    row = next(i for i in listed["items"] if i["id"] == inv["id"])
    assert row["match_assignee_id"] == str(assignee)


@pytest.mark.asyncio
async def test_ap_direct_match_completes_assignee_task(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-08")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-008")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
    assert r.status_code == 200
    assert await _open_match_task(inv["id"]) is None   # assignee's task not orphaned


@pytest.mark.asyncio
async def test_delete_completes_open_match_task(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-09")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-009")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})

    r = await admin_client.delete(f"{INV_URL}/{inv['id']}")
    assert r.status_code == 204
    assert await _open_match_task(inv["id"]) is None


@pytest.mark.asyncio
async def test_assignee_keeps_detail_visibility_after_match(admin_client):
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-ASSIGN-10")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-010")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})
    async with await _client_for_user(assignee) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
        assert r.status_code == 200
        # task completed, but the matcher still sees what they acted on
        assert (await c.get(f"{INV_URL}/{inv['id']}")).status_code == 200


# ── Match candidates + decline(指派人无 PO scope 时的死锁修复)────────────────

async def _set_po_status(po_id, status):
    import app.db.session as session_module
    from sqlalchemy import update as sa_update
    from app.models.po import PurchaseOrder
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(sa_update(PurchaseOrder)
                         .where(PurchaseOrder.id == uuid.UUID(po_id)).values(status=status))
        await db.commit()


@pytest.mark.asyncio
async def test_assignee_sees_match_candidates(admin_client):
    """被指派人即使没有任何 PR/PO scope,也能拿到该发票供应商的开放 PO 候选。"""
    v = await _make_vendor(admin_client, "VND-CAND-01")
    other = await _make_vendor(admin_client, "VND-CAND-02")
    po_ok = await _make_po(admin_client, v["id"],
                           [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    po_draft = await _make_po(admin_client, v["id"],
                              [{"description": "B", "qty": "1", "unit": "EA", "unit_price": "50.00"}])
    po_other = await _make_po(admin_client, other["id"],
                              [{"description": "C", "qty": "1", "unit": "EA", "unit_price": "70.00"}])
    await _set_po_status(po_ok["id"], "issued")
    await _set_po_status(po_other["id"], "issued")
    inv = await _make_invoice(admin_client, v["id"], number="CAND-001", amount="100.00")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})

    async with await _client_for_user(assignee) as c:
        r = await c.get(f"{INV_URL}/{inv['id']}/match-candidates")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        ids = {p["id"] for p in items}
        assert po_ok["id"] in ids          # 同 vendor + issued
        assert po_draft["id"] not in ids   # draft 不可匹配
        assert po_other["id"] not in ids   # 其他 vendor
        assert items[0]["line_items"]      # 带行,供分摊面板用

    stranger = await _make_user()
    async with await _client_for_user(stranger) as c:
        assert (await c.get(f"{INV_URL}/{inv['id']}/match-candidates")).status_code == 403


@pytest.mark.asyncio
async def test_decline_match_bounces_back_to_assigner(admin_client):
    v = await _make_vendor(admin_client, "VND-DECL-01")
    inv = await _make_invoice(admin_client, v["id"], number="DECL-001")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})
    task_before = await _open_match_task(inv["id"])
    assigner_id = task_before.created_by

    async with await _client_for_user(assignee) as c:
        assert (await c.post(f"{INV_URL}/{inv['id']}/decline-match",
                             json={"note": "  "})).status_code == 422
        r = await c.post(f"{INV_URL}/{inv['id']}/decline-match",
                         json={"note": "I do not know this vendor's POs"})
        assert r.status_code == 200, r.text

    task = await _open_match_task(inv["id"])
    assert task is not None
    assert task.assigned_user_id == assigner_id            # 退回给指派人
    assert "do not know this vendor" in (task.description or "")

    stranger = await _make_user()
    async with await _client_for_user(stranger) as c:
        assert (await c.post(f"{INV_URL}/{inv['id']}/decline-match",
                             json={"note": "x"})).status_code == 403
