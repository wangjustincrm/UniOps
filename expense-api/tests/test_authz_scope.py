"""Object-level authz (IDOR fix) tests for PA read endpoints.

Locks in Task A1: GET /pa/{id}, GET /pa/{id}/history, GET /pa/by-po/{po_id}
previously only checked login (`_: CurrentUserDep`), not ownership/participation
— any logged-in employee could read another employee's PA. These tests verify
`_can_view_pa` (pa.py) is now enforced: 403 for unrelated users, 200 for the
owner, and per-item filtering on the by-po list endpoint.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import _client, _make_token
from app.models.pa import PaymentApplication


async def _seed_pa(test_engine, created_by: uuid.UUID, status: str = "submitted",
                    po_id: uuid.UUID | None = None) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pa_id = uuid.uuid4()
    async with factory() as s:
        s.add(PaymentApplication(
            id=pa_id, pa_number=f"PA-T-{pa_id.hex[:8]}", title="Test PA",
            vendor_id=uuid.uuid4(), vendor_name="Test Vendor",
            subtotal=Decimal("100.00"), payment_amount=Decimal("100.00"),
            currency="CAD", status=status, created_by=created_by, po_id=po_id,
        ))
        await s.commit()
    return pa_id


@pytest.mark.asyncio
async def test_get_pa_forbidden_for_unrelated_user(test_engine):
    pa_id = await _seed_pa(test_engine, created_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/pa/{pa_id}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_pa_ok_for_owner(test_engine):
    owner = uuid.uuid4()
    pa_id = await _seed_pa(test_engine, created_by=owner)
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.get(f"/api/v1/pa/{pa_id}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_pa_history_forbidden_for_unrelated_user(test_engine):
    pa_id = await _seed_pa(test_engine, created_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/pa/{pa_id}/history")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_by_po_filters_out_unrelated_pas(test_engine):
    po_id = uuid.uuid4()
    await _seed_pa(test_engine, created_by=uuid.uuid4(), po_id=po_id)  # someone else's
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/pa/by-po/{po_id}")
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_get_pa_ok_for_past_actor(test_engine):
    """Parity fix: _can_view_pa must include the "ever-acted" branch (not just
    open tasks). A non-finance approver whose task is already completed (e.g.
    under a customized pa_dir workflow) still must be able to view the PA."""
    approver = uuid.uuid4()
    pa_id = await _seed_pa(test_engine, created_by=uuid.uuid4(), status="approved")
    await _seed_event(test_engine, pa_id, approver)
    async with _client(_make_token("requester", str(approver))) as c:
        r = await c.get(f"/api/v1/pa/{pa_id}")
    assert r.status_code == 200


from app.models.expense import ExpenseClaim
from datetime import date


async def _seed_claim(test_engine, employee_id: uuid.UUID, claim_type: str = "EXP",
                      status: str = "submitted") -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-T-{cid.hex[:8]}", claim_type=claim_type,
            employee_id=employee_id, employee_name="Test Emp",
            submission_date=date(2026, 8, 3), status=status,
            created_by=employee_id,
        ))
        await s.commit()
    return cid


@pytest.mark.asyncio
async def test_get_expense_forbidden_for_unrelated_user(test_engine):
    cid = await _seed_claim(test_engine, employee_id=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/expenses/{cid}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_expense_ok_for_owner(test_engine):
    owner = uuid.uuid4()
    cid = await _seed_claim(test_engine, employee_id=owner)
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.get(f"/api/v1/expenses/{cid}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_expense_approval_status_forbidden_for_unrelated_user(test_engine):
    cid = await _seed_claim(test_engine, employee_id=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/expenses/{cid}/approval-status")
    assert r.status_code == 403


from app.models.approval_event_mirror import ApprovalEventMirror


async def _seed_event(test_engine, claim_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        s.add(ApprovalEventMirror(
            id=uuid.uuid4(), document_type="expense", document_id=claim_id,
            document_number="EC-EVT", action="approve",
            actor_id=actor_id, actor_role="dept_manager",
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_get_expense_ok_for_past_actor(test_engine):
    approver = uuid.uuid4()
    cid = await _seed_claim(test_engine, employee_id=uuid.uuid4(), status="approved")
    await _seed_event(test_engine, cid, approver)
    async with _client(_make_token("requester", str(approver))) as c:
        r = await c.get(f"/api/v1/expenses/{cid}")
    assert r.status_code == 200


from app.models.invoice_attachment import InvoiceAttachment
from app.models.expense import ExpenseAttachment


async def _seed_claim_attachment(test_engine, claim_id: uuid.UUID) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    aid = uuid.uuid4()
    async with factory() as s:
        s.add(ExpenseAttachment(id=aid, claim_id=claim_id, file_id=str(uuid.uuid4()),
                                file_name="r.pdf", mime_type="application/pdf",
                                file_size_bytes=10))
        await s.commit()
    return aid


async def _seed_invoice_attachment(test_engine, uploaded_by: uuid.UUID) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    aid = uuid.uuid4()
    async with factory() as s:
        s.add(InvoiceAttachment(id=aid, invoice_id=uuid.uuid4(), invoice_source="oa",
                                file_name="inv.pdf", content_type="application/pdf",
                                file_size_bytes=10, storage_key=uuid.uuid4(),
                                uploaded_by=uploaded_by))
        await s.commit()
    return aid


@pytest.mark.asyncio
async def test_list_claim_attachments_forbidden_for_unrelated_user(test_engine):
    cid = await _seed_claim(test_engine, employee_id=uuid.uuid4())
    await _seed_claim_attachment(test_engine, cid)
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/expenses/{cid}/attachments")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_claim_attachment_forbidden_for_non_owner(test_engine):
    cid = await _seed_claim(test_engine, employee_id=uuid.uuid4(), status="draft")
    aid = await _seed_claim_attachment(test_engine, cid)
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.delete(f"/api/v1/expenses/{cid}/attachments/{aid}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_serve_invoice_attachment_forbidden_for_unrelated_user(test_engine):
    aid = await _seed_invoice_attachment(test_engine, uploaded_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/invoice-attachments/{aid}/file")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_invoice_attachment_forbidden_for_unrelated_user(test_engine):
    aid = await _seed_invoice_attachment(test_engine, uploaded_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.delete(f"/api/v1/invoice-attachments/{aid}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_serve_invoice_attachment_ok_for_uploader(test_engine):
    uploader = uuid.uuid4()
    aid = await _seed_invoice_attachment(test_engine, uploaded_by=uploader)
    async with _client(_make_token("requester", str(uploader))) as c:
        r = await c.get(f"/api/v1/invoice-attachments/{aid}/file")
    assert r.status_code in (200, 410, 502)   # passes authz; may 410/502 because file-api is unreachable in tests, but never 403/404


@pytest.mark.asyncio
async def test_non_admin_only_sees_active_custom_forms(test_engine):
    # 用唯一 code 避免与并发/既有数据碰撞;大写因为 CustomFormCreate 会把 code 规范化成大写
    suffix = uuid.uuid4().hex[:6].upper()
    active_code = f"CFM_ACT_{suffix}"
    inactive_code = f"CFM_INACT_{suffix}"
    async with _client(_make_token("system_admin")) as admin:
        r1 = await admin.post("/api/v1/expenses/custom-forms", json={
            "code": active_code, "name": "Active Test Form",
        })
        assert r1.status_code == 201, r1.text
        r2 = await admin.post("/api/v1/expenses/custom-forms", json={
            "code": inactive_code, "name": "Inactive Test Form",
        })
        assert r2.status_code == 201, r2.text
        r3 = await admin.patch(f"/api/v1/expenses/custom-forms/{inactive_code}", json={"is_active": False})
        assert r3.status_code == 200, r3.text

    async with _client(_make_token("requester")) as user:
        resp = await user.get("/api/v1/expenses/custom-forms")
    assert resp.status_code == 200
    codes = {f["code"] for f in resp.json()}
    assert active_code in codes
    assert inactive_code not in codes   # 非管理员看不到未激活表单

    # admin 不加 active_only 能看到两者
    async with _client(_make_token("system_admin")) as admin2:
        resp2 = await admin2.get("/api/v1/expenses/custom-forms")
    codes2 = {f["code"] for f in resp2.json()}
    assert inactive_code in codes2
