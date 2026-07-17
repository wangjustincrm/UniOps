"""Purchase Order endpoint tests."""
from decimal import Decimal

import pytest
import sqlalchemy as sa

URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"
PR_URL = "/api/v1/pr"

_LINE = {
    "description": "Hydraulic Filter HF-200",
    "qty": "4",
    "unit": "EA",
    "unit_price": "45.00",
}


async def _make_vendor(client, code="VND-PO-01"):
    resp = await client.post(VENDOR_URL, json={
        "code": code, "name": "PO Vendor Corp", "category": "Services",
        "contact_name": "Bob", "contact_email": "bob@vendor.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _po_payload(vendor_id, **overrides):
    base = {
        "title": "Monthly Filter Order",
        "type": 2,
        "vendor_id": vendor_id,
        "currency": "CAD",
        "tax_rate": "0.13",
        "line_items": [_LINE],
    }
    base.update(overrides)
    return base


async def _create_po(client, vendor_id, **overrides):
    resp = await client.post(URL, json=_po_payload(vendor_id, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Basic CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-CREATE-01")
    po = await _create_po(admin_client, v["id"])
    assert po["number"].startswith("PO-")
    assert po["status"] == "draft"
    assert len(po["line_items"]) == 1
    subtotal = 4 * 45.0
    assert float(po["subtotal"]) == pytest.approx(subtotal)
    assert float(po["tax_amount"]) == pytest.approx(subtotal * 0.13)
    assert float(po["total"]) == pytest.approx(subtotal * 1.13)


@pytest.mark.asyncio
async def test_list_pos(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-LIST-01")
    await _create_po(admin_client, v["id"])
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_get_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-GET-01")
    po = await _create_po(admin_client, v["id"])
    resp = await admin_client.get(f"{URL}/{po['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == po["id"]


@pytest.mark.asyncio
async def test_update_po_lines(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-UPD-01")
    po = await _create_po(admin_client, v["id"])
    new_lines = [
        {**_LINE, "qty": "10", "unit_price": "50.00"},
    ]
    resp = await admin_client.patch(f"{URL}/{po['id']}", json={"line_items": new_lines})
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["subtotal"]) == pytest.approx(500.0)
    assert float(data["total"]) == pytest.approx(500.0 * 1.13)


@pytest.mark.asyncio
async def test_create_po_invalid_vendor(admin_client):
    resp = await admin_client.post(URL, json=_po_payload(
        "00000000-0000-0000-0000-000000000000"
    ))
    assert resp.status_code == 404


# ── Workflow ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-SUB-01")
    po = await _create_po(admin_client, v["id"])
    resp = await admin_client.post(f"{URL}/{po['id']}/action", json={"action": "submit"})
    assert resp.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_full_po_approval(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-APPR-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]

    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})

    # step 0 → in_review
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "in_review"
    assert r.json()["approval_step_idx"] == 1

    # step 1 → approved
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_issue_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-ISSUE-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "issue"})
    assert r.json()["status"] == "issued"


@pytest.mark.asyncio
async def test_return_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-RET-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "return"})
    assert r.json()["status"] == "returned"


@pytest.mark.asyncio
async def test_po_events(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-EVT-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})

    resp = await admin_client.get(f"{URL}/{pid}/events")
    assert resp.status_code == 200
    assert any(e["action"] == "submit" for e in resp.json())


# ── PR → PO link ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_po_from_pr(admin_client):
    # Create a PR first
    pr_resp = await admin_client.post(PR_URL, json={
        "title": "Linked PR for PO test",
        "type": 2,
        "line_items": [_LINE],
    })
    pr = pr_resp.json()

    v = await _make_vendor(admin_client, code="VND-PO-PR-01")
    po = await _create_po(admin_client, v["id"], pr_id=pr["id"])
    assert po["pr_id"] == pr["id"]
    assert po["pr_number"] == pr["number"]

    # PR should now have po_id set
    pr_resp2 = await admin_client.get(f"{PR_URL}/{pr['id']}")
    assert pr_resp2.json()["po_id"] == po["id"]
    assert pr_resp2.json()["po_number"] == po["number"]


# ── Task inbox ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tasks_created_on_pr_submit(admin_client):
    pr_resp = await admin_client.post(PR_URL, json={
        "title": "Task creation test PR",
        "type": 1,
        "line_items": [_LINE],
    })
    pr = pr_resp.json()
    await admin_client.post(f"{PR_URL}/{pr['id']}/action", json={"action": "submit"})

    resp = await admin_client.get("/api/v1/tasks", params={"include_completed": True})
    assert resp.status_code == 200
    tasks = resp.json()
    pr_tasks = [t for t in tasks if t["document_id"] == pr["id"]]
    assert any(t["type"] == "approve_pr" for t in pr_tasks)


@pytest.mark.asyncio
async def test_backfill_create_po_task_for_approved_pr_without_po(test_engine):
    """PMS-imported PRs land 'approved' without going through the engine hook
    that raises the Create PO task, so procurement never saw them (unlike
    place_order, which was already backfilled). get_for_role must synthesize a
    create_po task for any approved PR that still has no PO."""
    import uuid
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import task as task_crud
    from app.crud import user as user_crud
    from app.models.pr import PurchaseRequest
    from app.schemas.auth import RegisterRequest

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer = await user_crud.create(db, RegisterRequest(
            email=f"po-officer-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="Purchasing Officer", role="procurement_officer"))
        pr = PurchaseRequest(
            number=f"PR-{uuid.uuid4().hex[:6]}", title="Imported approved PR", type=2,
            status="approved", amount=Decimal("100.00"), created_by=officer.id, po_id=None)
        db.add(pr)
        await db.commit()

        tasks = await task_crud.get_for_role(db, "procurement_officer", officer.id)
        await db.commit()

        create_po = [t for t in tasks if t.type == "create_po" and t.document_id == pr.id]
        assert len(create_po) == 1, "approved PR without a PO must surface one Create PO task"
        assert create_po[0].assigned_role == "procurement_officer"

        # Idempotent: a second call must not duplicate the task
        tasks2 = await task_crud.get_for_role(db, "procurement_officer", officer.id)
        await db.commit()
        again = [t for t in tasks2 if t.type == "create_po" and t.document_id == pr.id]
        assert len(again) == 1, "backfill must not create duplicate create_po tasks"


# ── Approval auto-skip (crud-level) ─────────────────────────────────────────────
# The HTTP /action endpoint forwards approve/submit/etc to approval-api's engine
# (which owns the live workflow — already migrated off role_management in Task
# 5). epms-api/crud/po.py keeps its own copy of the auto-skip logic; this test
# exercises it directly to prove it now resolves role holders from identity's
# user_roles ∪ users.role, not the retired company_config.role_management
# single *_user_id fields (phase 3).

@pytest.mark.asyncio
async def test_po_approve_auto_skips_step_held_by_same_actor(test_engine):
    import uuid
    from sqlalchemy import select as _select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import po as po_crud
    from app.crud import user as user_crud
    from app.models.approval import ApprovalEvent
    from app.models.config import CompanyConfig
    from app.models.po import PurchaseOrder
    from app.models.vendor import Vendor
    from app.schemas.auth import RegisterRequest
    from app.schemas.po import PoActionRequest

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        # Pin a 2-step "po" workflow on the shared singleton config row
        # (other tests, e.g. test_config.py::test_update_workflow_defs, may
        # have already overwritten it with a different shape — don't rely on
        # the hardcoded PO_WORKFLOW fallback still being in effect).
        cfg = (await db.execute(_select(CompanyConfig).limit(1))).scalar_one_or_none()
        po_workflow_defs = [
            {"id": "po-step-0", "role": "procurement_manager", "label": "Procurement Manager"},
            {"id": "po-step-1", "role": "finance_manager", "label": "Finance Manager"},
        ]
        if cfg is None:
            db.add(CompanyConfig(role_permissions={}, custom_roles=[], workflow_defs={"po": po_workflow_defs}))
        else:
            cfg.workflow_defs = {**(cfg.workflow_defs or {}), "po": po_workflow_defs}
        await db.commit()

        # Actor's PRIMARY role IS "finance_manager" — PO workflow step 1. No
        # user_roles row is written for this (the phase-3 seed deliberately
        # skips it when the primary role already covers the post) — proving
        # role_holder_ids() unions users.role, not just user_roles.
        actor = await user_crud.create(db, RegisterRequest(
            email=f"po-autoskip-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="Auto Skip FM", role="finance_manager"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Auto Skip Vendor",
                        category="Services", contact_name="C", contact_email="c@autoskip.test")
        db.add(vendor)
        await db.commit()
        await db.refresh(vendor)

        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="Auto Skip PO", type=2,
                           vendor_id=vendor.id, vendor_name=vendor.name, created_by=actor.id,
                           status="submitted", approval_step_idx=0)
        db.add(po)
        await db.commit()
        await db.refresh(po)

        result = await po_crud.action(
            db, po, PoActionRequest(action="approve"), actor_id=actor.id, actor_role=actor.role)
        await db.commit()

        # step 0 (procurement_manager) is the explicit approve; step 1
        # (finance_manager) auto-skips because the actor also holds it.
        assert result.status == "approved"

        events = (await db.execute(
            sa.select(ApprovalEvent).where(ApprovalEvent.document_id == po.id).order_by(ApprovalEvent.step_idx)
        )).scalars().all()
        assert [e.action for e in events] == ["approve", "approve"]
        assert events[1].comment == "Auto-approved (same approver holds both roles)"
        assert events[1].actor_role == "finance_manager"
