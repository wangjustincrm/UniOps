"""Data Maintenance approval-state panel: setting a step must actually issue the task.

The panel used to only REASSIGN tasks that already existed. For a document with no
open approve task (PMS imports, anything manually put back to in_review, a cancelled
agreement) that meant: the stored step index moved, the detail page rendered the new
step — and no approval button ever appeared, because the approval UI is gated on the
tasks table, not on approval_step_idx. Every such case ended in a hand-written script.

Three failure modes were invisible because the engine's resync silently returns None
and the panel reported "Reassigned 0 open task(s)" as if it had worked:

  1. step >= len(workflow)          — engine: "no task and step out of range"
  2. status not submitted/in_review — engine refuses to build tasks for terminal docs
  3. user-specific role unresolved  — engine emits a WARN in `actions` and no task

They are now rejected up front (1, 2) or surfaced verbatim (3).

The fourth problem was worse than silence: _resync_document derives the true step from
any OPEN approve task in preference to the stored index, so resyncing with a stale task
still open snaps the admin's new step straight back. The stale tasks are closed first.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import approval_client


PA_CHAIN = [
    {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    {"id": "director", "role": "director", "label": "Director"},
    {"id": "gm_opm", "role": "gm_or_opm", "label": "GM / OPM"},
    {"id": "finance_bp", "role": "finance_bp", "label": "Finance BP"},
    {"id": "ap_clerk", "role": "ap_clerk", "label": "AP Review"},
    {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"},
]


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_pa(factory, *, status="in_review", step=0):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.pa import PaymentApplication

    uid, vid, pid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(User(id=uid, email=f"ap-{uid.hex[:8]}@x.com", hashed_password="x",
                    full_name="AP", role="ap_clerk"))
        db.add(Vendor(id=vid, code=f"V-{vid.hex[:6]}", name="Vend", category="supplier",
                      contact_name="n", contact_email="n@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PaymentApplication(
            id=pid, pa_number=f"PA-AST-{pid.hex[:6]}", title="t", vendor_id=vid,
            vendor_name="Vend", invoice_ids=[], gr_ids=[], subtotal=Decimal("10"),
            payment_amount=Decimal("10"), created_by=uid, status=status,
            approval_step_idx=step))
        await db.commit()
    return uid, pid


def _stub_steps(monkeypatch, steps=PA_CHAIN):
    async def fake(doc_type, doc_id, bearer_token):
        return steps
    monkeypatch.setattr(approval_client, "get_workflow_steps", fake)


# ── entity coverage ─────────────────────────────────────────────────────────

def test_approval_state_covers_agreement():
    from app.admin.service import APPROVAL_STATE_ENTITIES

    assert set(APPROVAL_STATE_ENTITIES) == {"pr", "po", "pa", "agreement"}


@pytest.mark.asyncio
async def test_approval_state_rejects_unsupported_entity(test_engine):
    from app.admin import service

    factory = _factory(test_engine)
    async with factory() as db:
        with pytest.raises(ValueError, match="no approval state"):
            await service.edit_approval_state(
                db, "invoice", uuid.uuid4(), {"approval_step_idx": 1},
                actor_id=uuid.uuid4(), actor_email="a@x.com", bearer_token="t")


# ── the three silent failures become explicit errors ────────────────────────

@pytest.mark.asyncio
async def test_out_of_range_step_is_rejected_with_the_real_chain(test_engine, monkeypatch):
    """The memory case: step 8 typed into a 6-step chain. The engine returned None and the
    panel claimed success."""
    from app.admin import service

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory)
    _stub_steps(monkeypatch)

    async with factory() as db:
        with pytest.raises(ValueError) as exc:
            await service.edit_approval_state(
                db, "pa", pid, {"approval_step_idx": 8},
                actor_id=uid, actor_email="a@x.com", bearer_token="t")
    msg = str(exc.value)
    assert "6" in msg                    # names the real chain length
    assert "AP Review" in msg            # and lists the steps so the admin can pick


@pytest.mark.asyncio
async def test_negative_step_is_rejected(test_engine, monkeypatch):
    from app.admin import service

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory)
    _stub_steps(monkeypatch)
    async with factory() as db:
        with pytest.raises(ValueError, match="out of range"):
            await service.edit_approval_state(
                db, "pa", pid, {"approval_step_idx": -1},
                actor_id=uid, actor_email="a@x.com", bearer_token="t")


@pytest.mark.asyncio
async def test_terminal_status_is_rejected(test_engine, monkeypatch):
    """The engine only builds approve tasks for submitted/in_review documents. Setting a
    step on an approved one moved the index and issued nothing."""
    from app.admin import service

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory, status="approved")
    _stub_steps(monkeypatch)

    async with factory() as db:
        with pytest.raises(ValueError) as exc:
            await service.edit_approval_state(
                db, "pa", pid, {"approval_step_idx": 4},
                actor_id=uid, actor_email="a@x.com", bearer_token="t")
    assert "approved" in str(exc.value)          # says what the status actually is
    assert "in_review" in str(exc.value)         # and what it must become


# ── stale open tasks must be closed or resync snaps the step back ───────────

@pytest.mark.asyncio
async def test_stale_open_approve_tasks_are_closed_so_the_index_wins(test_engine, monkeypatch):
    """_resync_document prefers the open task's role over the stored index. Leaving a task
    open at step 0 while the admin sets step 4 makes the engine 'realign' 4 back to 0 —
    actively undoing the edit."""
    from app.models.task import Task
    from app.models.pa import PaymentApplication
    from app.admin import service

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory, step=0)
    _stub_steps(monkeypatch)
    async with factory() as db:
        db.add(Task(id=uuid.uuid4(), type="approve_pa", document_type="pa", document_id=pid,
                    document_number="PA-AST", title="Approve", assigned_role="dept_manager",
                    is_completed=False))
        # a non-approve task must survive: it is legitimate next-step work
        db.add(Task(id=uuid.uuid4(), type="process_pa", document_type="pa", document_id=pid,
                    document_number="PA-AST", title="Pay", assigned_role="ap_clerk",
                    is_completed=False))
        await db.commit()

    async with factory() as db:
        result = await service.edit_approval_state(
            db, "pa", pid, {"approval_step_idx": 4},
            actor_id=uid, actor_email="a@x.com", bearer_token="t")
        await db.commit()

    assert result["closed_stale_tasks"] == 1
    assert result["resync_doc_type"] == "pa"

    async with factory() as db:
        pa = (await db.execute(select(PaymentApplication).where(
            PaymentApplication.id == pid))).scalar_one()
        assert pa.approval_step_idx == 4
        open_approve = (await db.execute(select(Task).where(
            Task.document_id == pid, Task.type.like("approve%"),
            Task.is_completed.is_(False)))).scalars().all()
        assert open_approve == []
        open_other = (await db.execute(select(Task).where(
            Task.document_id == pid, Task.type == "process_pa",
            Task.is_completed.is_(False)))).scalars().all()
        assert len(open_other) == 1, "non-approve tasks must not be touched"


# ── doc_type resolution ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doc_type_follows_the_recorded_workflow_not_the_entity_key(test_engine, monkeypatch):
    """Direct PAs live in the same table but run the 2-step 'pa_dir' chain. Passing the
    entity key 'pa' would fetch the wrong workflow and stamp the new task with a
    document_type OA never reads."""
    from app.models.approval import ApprovalEvent
    from app.admin import service

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory)
    async with factory() as db:
        db.add(ApprovalEvent(id=uuid.uuid4(), document_type="pa_dir", document_id=pid,
                             document_number="PA-AST", step_idx=0, action="submit",
                             actor_id=uid, actor_role="requester"))
        await db.commit()

    seen = {}

    async def fake(doc_type, doc_id, bearer_token):
        seen["doc_type"] = doc_type
        return PA_CHAIN[:2]
    monkeypatch.setattr(approval_client, "get_workflow_steps", fake)

    async with factory() as db:
        result = await service.edit_approval_state(
            db, "pa", pid, {"approval_step_idx": 1},
            actor_id=uid, actor_email="a@x.com", bearer_token="t")
        await db.commit()

    assert seen["doc_type"] == "pa_dir"
    assert result["resync_doc_type"] == "pa_dir"


@pytest.mark.asyncio
async def test_agreement_doc_type_defaults_to_agr(test_engine, monkeypatch):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.agreement import PurchaseAgreement
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid, aid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(User(id=uid, email=f"po-{uid.hex[:8]}@x.com", hashed_password="x",
                    full_name="PO", role="procurement_officer"))
        db.add(Vendor(id=vid, code=f"V-{vid.hex[:6]}", name="Vend", category="supplier",
                      contact_name="n", contact_email="n@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseAgreement(
            id=aid, number=f"AGR-AST-{aid.hex[:6]}", title="t", agreement_type="recurring",
            vendor_id=vid, vendor_name="Vend", valid_from=date(2026, 1, 1),
            valid_to=date(2027, 1, 1), status="in_review", created_by=uid))
        await db.commit()

    seen = {}

    async def fake(doc_type, doc_id, bearer_token):
        seen["doc_type"] = doc_type
        return PA_CHAIN[:3]
    monkeypatch.setattr(approval_client, "get_workflow_steps", fake)

    async with factory() as db:
        await service.edit_approval_state(
            db, "agreement", aid, {"approval_step_idx": 2},
            actor_id=uid, actor_email="a@x.com", bearer_token="t")
        await db.commit()

    assert seen["doc_type"] == "agr"


# ── role-only path keeps its old behaviour ──────────────────────────────────

@pytest.mark.asyncio
async def test_role_only_patch_still_reassigns_without_touching_the_step(test_engine):
    from app.models.task import Task
    from app.models.pa import PaymentApplication
    from app.admin import service

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory, step=2)
    async with factory() as db:
        db.add(Task(id=uuid.uuid4(), type="approve_pa", document_type="pa", document_id=pid,
                    document_number="PA-AST", title="Approve", assigned_role="dept_manager",
                    is_completed=False))
        await db.commit()

    async with factory() as db:
        result = await service.edit_approval_state(
            db, "pa", pid, {"assigned_role": "finance_bp"},
            actor_id=uid, actor_email="a@x.com", bearer_token="t")
        await db.commit()

    assert result["reassigned_open_tasks"] == 1
    assert result.get("resync_doc_type") is None    # no resync when the step is untouched

    async with factory() as db:
        pa = (await db.execute(select(PaymentApplication).where(
            PaymentApplication.id == pid))).scalar_one()
        assert pa.approval_step_idx == 2             # untouched
        t = (await db.execute(select(Task).where(Task.document_id == pid))).scalars().first()
        assert t.assigned_role == "finance_bp"
        assert t.is_completed is False               # reassigned, not closed


# ── route level: the resync actually fires and its result reaches the caller ──

@pytest.mark.asyncio
async def test_route_calls_resync_and_surfaces_engine_actions(test_engine, admin_client, monkeypatch):
    """The whole point: after the panel applies, the engine must have been asked to issue
    the task, and whatever it reports must reach the admin."""
    from app.api.v1 import admin as admin_route

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory)
    _stub_steps(monkeypatch)

    called = {}

    async def fake_resync(doc_type, doc_id, bearer_token):
        called["args"] = (doc_type, doc_id)
        return {"resynced": {"doc_type": doc_type, "number": "PA-AST",
                             "actions": ["reissue step4 -> ap_clerk/broadcast"],
                             "final_step": 4}}
    monkeypatch.setattr(admin_route.approval_client, "resync_document", fake_resync)

    resp = await admin_client.patch(
        f"/api/v1/admin/pa/{pid}/approval-state", json={"approval_step_idx": 4})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert called["args"] == ("pa", str(pid))
    assert body["routing_resync"] == "ok"
    assert "reissue step4 -> ap_clerk/broadcast" in body["resync_actions"]


@pytest.mark.asyncio
async def test_route_applies_role_override_after_the_engine_reissues(test_engine, admin_client, monkeypatch):
    """Step + role together. The override must land on the task the ENGINE issued, not on
    the stale one being closed — applying it before the resync writes the admin's choice
    onto a row that is about to be completed and never seen."""
    from app.models.task import Task
    from app.api.v1 import admin as admin_route

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory)
    _stub_steps(monkeypatch)

    async def fake_resync(doc_type, doc_id, bearer_token):
        # Stand in for the engine: issue the approve task for the requested step.
        async with factory() as db:
            db.add(Task(id=uuid.uuid4(), type="approve_pa", document_type="pa",
                        document_id=uuid.UUID(doc_id), document_number="PA-AST",
                        title="Approve", assigned_role="ap_clerk", is_completed=False))
            await db.commit()
        return {"resynced": {"actions": ["reissue step4 -> ap_clerk/broadcast"], "final_step": 4}}
    monkeypatch.setattr(admin_route.approval_client, "resync_document", fake_resync)

    resp = await admin_client.patch(
        f"/api/v1/admin/pa/{pid}/approval-state",
        json={"approval_step_idx": 4, "assigned_role": "finance_manager"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reassigned_open_tasks"] == 1
    assert body["open_approve_tasks"] == 1

    async with factory() as db:
        open_t = (await db.execute(select(Task).where(
            Task.document_id == pid, Task.is_completed.is_(False)))).scalars().all()
        assert len(open_t) == 1
        assert open_t[0].assigned_role == "finance_manager"   # override beat the engine's pick


@pytest.mark.asyncio
async def test_route_surfaces_engine_warning_instead_of_dropping_it(test_engine, admin_client, monkeypatch):
    """Silent failure #3: a user-specific role with nobody assigned in config. The engine
    returns a WARN string in `actions` and creates no task; that used to vanish."""
    from app.api.v1 import admin as admin_route

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory)
    _stub_steps(monkeypatch)

    async def fake_resync(doc_type, doc_id, bearer_token):
        return {"resynced": {"doc_type": doc_type, "number": "PA-AST",
                             "actions": ["WARN gm_or_opm unresolved @step2 — assign one in config, then re-sync"],
                             "final_step": 2}}
    monkeypatch.setattr(admin_route.approval_client, "resync_document", fake_resync)

    resp = await admin_client.patch(
        f"/api/v1/admin/pa/{pid}/approval-state", json={"approval_step_idx": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any("WARN" in a for a in body["resync_actions"])
    assert body["resync_warning"] is True


@pytest.mark.asyncio
async def test_route_maps_engine_outage_to_502_not_500(test_engine, admin_client, monkeypatch):
    """Validating the step requires the engine's workflow for this document. When the
    engine is down that must read as 'could not check', with nothing changed — not as an
    opaque 500 that leaves the admin guessing whether the step was written."""
    from app.models.pa import PaymentApplication
    from app.admin import service as admin_service

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory, step=1)

    async def down(doc_type, doc_id, bearer_token):
        raise RuntimeError("Approval Engine unreachable at http://approval-api:8003")
    monkeypatch.setattr(admin_service.approval_client, "get_workflow_steps", down)

    resp = await admin_client.patch(
        f"/api/v1/admin/pa/{pid}/approval-state", json={"approval_step_idx": 4})
    assert resp.status_code == 502, resp.text
    assert "unreachable" in resp.json()["detail"]

    async with factory() as db:
        pa = (await db.execute(select(PaymentApplication).where(
            PaymentApplication.id == pid))).scalar_one()
        assert pa.approval_step_idx == 1     # rolled back, nothing written


@pytest.mark.asyncio
async def test_route_reports_resync_failure_without_losing_the_edit(test_engine, admin_client, monkeypatch):
    """resync-document is system_admin-only and cross-service. If it fails the step change
    is already committed — say so plainly rather than 500 or claim success."""
    from app.models.pa import PaymentApplication
    from app.api.v1 import admin as admin_route

    factory = _factory(test_engine)
    uid, pid = await _seed_pa(factory)
    _stub_steps(monkeypatch)

    async def boom(doc_type, doc_id, bearer_token):
        raise RuntimeError("resync-document 403: system_admin only")
    monkeypatch.setattr(admin_route.approval_client, "resync_document", boom)

    resp = await admin_client.patch(
        f"/api/v1/admin/pa/{pid}/approval-state", json={"approval_step_idx": 3})
    assert resp.status_code == 200, resp.text
    assert "403" in resp.json()["routing_resync"]

    async with factory() as db:
        pa = (await db.execute(select(PaymentApplication).where(
            PaymentApplication.id == pid))).scalar_one()
        assert pa.approval_step_idx == 3      # the committed part stands
