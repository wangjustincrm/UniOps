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
    # The uploader must pass the gate. Anything after it (200, or a 404/410/502
    # relayed from file-api depending on what that service has and whether it is
    # reachable from the test network) is not this test's subject; 403 is.
    assert r.status_code != 403, r.text


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


from app.models.invoice import ExpenseInvoice


async def _seed_invoice(test_engine, created_by: uuid.UUID) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    iid = uuid.uuid4()
    async with factory() as s:
        s.add(ExpenseInvoice(id=iid, file_name="i.pdf", file_mime_type="application/pdf",
                             file_size_bytes=10, created_by=created_by))
        await s.commit()
    return iid


@pytest.mark.asyncio
async def test_get_invoice_forbidden_for_unrelated_user(test_engine):
    iid = await _seed_invoice(test_engine, created_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/invoices/{iid}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_invoice_ok_for_creator(test_engine):
    creator = uuid.uuid4()
    iid = await _seed_invoice(test_engine, created_by=creator)
    async with _client(_make_token("requester", str(creator))) as c:
        r = await c.get(f"/api/v1/invoices/{iid}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_invoice_ok_for_finance(test_engine):
    iid = await _seed_invoice(test_engine, created_by=uuid.uuid4())
    async with _client(_make_token("finance_manager", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/invoices/{iid}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_vendor_suggestions_forbidden_for_unrelated_user(test_engine):
    iid = await _seed_invoice(test_engine, created_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/invoices/{iid}/vendor-suggestions?q=abc")
    assert r.status_code == 403


# ── Task C1: my_actions surfaces customised approval chains ───────────────────
# Original audit-② remediation: /expenses/my-actions used to consult a hardcoded
# step-index -> role map (_INBOX_STEP_ROLES) that could drift from the configured
# company_config.workflow_defs, so a customised chain went missing from inboxes.
#
# 2026-08-14: the endpoint now reads the shared `tasks` table instead of matching
# workflow steps itself (it had no department predicate, so every dept_manager saw
# every company claim — see test_my_actions_task_scope.py). The property under test
# is unchanged, and still holds: approval-api builds those tasks FROM workflow_defs,
# so a customised chain reaches the right person. These tests now seed the task the
# engine would have written, which is also closer to production than a claim with no
# task at all could ever be.
#
# company_config is a single shared row read by other test modules too
# (test_pa_permissions.py, test_travel_application_list.py) under the same
# session-scoped test_engine — so _set_workflow_defs upserts (merging onto
# the existing dict, like test_travel_application_list.py's helper) and the
# tests restore the previous value afterward rather than deleting the row.

from app.models.company_config_mirror import EpmsCompanyConfig
from sqlalchemy import select as _cc_select


async def _set_workflow_defs(test_engine, defs: dict) -> dict:
    """Upsert workflow_defs onto the shared company_config row (merging with
    whatever other test modules left there), returning the previous value so
    the caller can restore it."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        existing = (await s.execute(_cc_select(EpmsCompanyConfig))).scalars().all()
        if existing:
            cfg = existing[0]
            previous = dict(cfg.workflow_defs or {})
            cfg.workflow_defs = defs
        else:
            previous = {}
            s.add(EpmsCompanyConfig(
                id=uuid.uuid4(), dept_gm_opm_mapping={}, role_management={},
                workflow_defs=defs,
            ))
        await s.commit()
    return previous


async def _restore_workflow_defs(test_engine, previous: dict) -> None:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        existing = (await s.execute(_cc_select(EpmsCompanyConfig))).scalars().all()
        if existing:
            existing[0].workflow_defs = previous
            await s.commit()


async def _seed_claim_at_step(test_engine, employee_id: uuid.UUID, claim_type: str,
                               step_idx: int, status: str = "in_review") -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-T-{cid.hex[:8]}", claim_type=claim_type,
            employee_id=employee_id, employee_name="Test Emp",
            submission_date=date(2026, 8, 3), status=status,
            approval_step_idx=step_idx, created_by=employee_id,
        ))
        await s.commit()
    return cid


async def _seed_open_task(claim_id: uuid.UUID, *, role: str,
                          doc_type: str = "exp",
                          user_id: uuid.UUID | None = None) -> None:
    """The approve_* task approval-api writes when a document reaches that step.

    `user_id` set = the pinned form (dept_manager, director, supervisor);
    left None = the broadcast form, matched against the caller's role union.
    """
    from datetime import datetime, timezone
    from app.models.task_mirror import TaskMirror
    import app.db.base as _dbm
    async with _dbm.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(), document_id=claim_id, document_type=doc_type,
            type=f"approve_{doc_type}", assigned_user_id=user_id,
            assigned_role=None if user_id else role,
            is_completed=False, created_at=datetime.now(timezone.utc),
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_my_actions_shows_the_step_this_user_owns(test_engine):
    # dept_manager is step 0 in the exp chain; finance_bp is step 1.
    previous = await _set_workflow_defs(test_engine, {"exp": [
        {"id": "s0", "role": "dept_manager", "label": "Dept Manager"},
        {"id": "s1", "role": "finance_bp", "label": "Finance BP"},
    ]})
    try:
        c0 = await _seed_claim_at_step(test_engine, uuid.uuid4(), "EXP", 0)  # at step 0
        c1 = await _seed_claim_at_step(test_engine, uuid.uuid4(), "EXP", 1)  # at step 1
        await _seed_open_task(c0, role="dept_manager")
        await _seed_open_task(c1, role="finance_bp")
        async with _client(_make_token("dept_manager", str(uuid.uuid4()))) as c:
            r = await c.get("/api/v1/expenses/my-actions")
        assert r.status_code == 200
        ids = {i["id"] for i in r.json()["items"]}
        assert str(c0) in ids and str(c1) not in ids   # dept_manager only sees step 0
    finally:
        await _restore_workflow_defs(test_engine, previous)


@pytest.mark.asyncio
async def test_my_actions_custom_role_not_in_default_map(test_engine):
    # A customised chain assigns step 0 to a role absent from the old hardcoded
    # map ("gm") -> that role must still see the claim in its inbox.
    previous = await _set_workflow_defs(test_engine, {"exp": [
        {"id": "s0", "role": "gm", "label": "GM"},
    ]})
    try:
        c0 = await _seed_claim_at_step(test_engine, uuid.uuid4(), "EXP", 0)
        await _seed_open_task(c0, role="gm")
        async with _client(_make_token("gm", str(uuid.uuid4()))) as c:
            r = await c.get("/api/v1/expenses/my-actions")
        assert r.status_code == 200
        assert str(c0) in {i["id"] for i in r.json()["items"]}
    finally:
        await _restore_workflow_defs(test_engine, previous)


# ── GET /api/v1/tasks (OA Task List) — approver rows come from the shared
# `tasks` table, the same source my_actions and _can_act_on_claim read.
#
# This started as hardcoded step→role maps, then became workflow_defs lookups
# (C2), and both had the same flaw: they answered "does my ROLE appear at this
# step" with no department predicate and no look at who the document is
# actually assigned to, so every dept_manager saw every company document at
# that step. approval-api pins a dept_manager task to the one manager who
# routes for that department; reading tasks is what carries that scope across.
# These tests therefore seed the task approval-api would have written. ───────

@pytest.mark.asyncio
async def test_tasks_expense_approver_from_workflow_defs(test_engine):
    # Custom: step 0 approver = gm (the old hardcoded table's step 0 was dept_manager)
    previous = await _set_workflow_defs(test_engine, {"exp": [
        {"id": "s0", "role": "gm", "label": "GM"},
        {"id": "s1", "role": "finance_bp", "label": "Finance BP"},
    ]})
    try:
        c0 = await _seed_claim_at_step(test_engine, uuid.uuid4(), "EXP", 0)
        await _seed_open_task(c0, role="gm")
        async with _client(_make_token("gm", str(uuid.uuid4()))) as c:
            r = await c.get("/api/v1/tasks")
        items = r.json()["items"]
        assert any(i["doc_id"] == str(c0) and i["task_type"] == "approve_expense" for i in items)
        # dept_manager holds no task on it, so it must not appear for them
        async with _client(_make_token("dept_manager", str(uuid.uuid4()))) as c2:
            r2 = await c2.get("/api/v1/tasks")
        assert not any(i["doc_id"] == str(c0) for i in r2.json()["items"])
    finally:
        await _restore_workflow_defs(test_engine, previous)


@pytest.mark.asyncio
async def test_the_oa_task_list_no_longer_carries_payment_applications(test_engine):
    """OA's Direct PA is retired, so its task list has no PA section at all.

    This used to assert that a PA approver saw the PA here. EPMS's PAs surfaced
    through the same section and deep-linked into OA's /pa/:id, which no longer
    exists — and they are EPMS documents anyway: epms-api's task list covers
    approve_pa (its liveness map names both `pa` and `pa_dir`) and the Portal
    home inbox merges that feed. See DIRECT_PA_RETIRED in api/v1/pa.py.
    """
    previous = await _set_workflow_defs(test_engine, {"pa_dir": [
        {"id": "s0", "role": "gm", "label": "GM"},
    ]})
    try:
        pa_id = await _seed_pa(test_engine, created_by=uuid.uuid4(), status="in_review")
        await _seed_open_task(pa_id, role="gm", doc_type="pa_dir")
        async with _client(_make_token("gm", str(uuid.uuid4()))) as c:
            r = await c.get("/api/v1/tasks")
        assert r.status_code == 200
        assert not any(i["doc_id"] == str(pa_id) for i in r.json()["items"])
    finally:
        await _restore_workflow_defs(test_engine, previous)
