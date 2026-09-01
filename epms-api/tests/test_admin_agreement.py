"""Data Maintenance coverage for Purchase Agreements and Agreement Receipts.

Two entities plus the approval-state rebuild. The delete口径 differs between
them on purpose (user decision, 2026-08-14):

- agreement       — REFUSES to delete while invoices / payment applications
                    still reference it. Those FKs are RESTRICT and the
                    documents are real financial records; the admin deletes
                    them explicitly first.
- agreement_receipt — RELEASES its invoice claim and then deletes. Releasing
                    a claim is an ordinary, well-tested operation in this
                    codebase (crud.invoice._release_agreement_evidence does
                    exactly this from the other side), unlike cascade-deleting
                    an invoice.

★ Agreement numbers here deliberately carry a non-digit in the tail segment
("AGR-DMT-a1" not "AGR-202608-0001"): crud/_numbering.py::next_number treats
ANY same-prefix all-digit tail as an allocated sequence number, so realistic
literals silently poison the production counter (see the epms test-invocation
memory).
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


# ── fixtures ────────────────────────────────────────────────────────────────

def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_actor_and_vendor(factory):
    """A user + a vendor, both required by the agreement FKs."""
    from app.models.user import User
    from app.models.vendor import Vendor

    uid, vid = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(User(id=uid, email=f"dm-{uid.hex[:8]}@x.com", hashed_password="x",
                    full_name="DM Actor", role="requester"))
        db.add(Vendor(id=vid, code=f"V-{vid.hex[:6]}", name="Liftow Limited",
                      category="supplier", contact_name="N", contact_email="n@x.com"))
        await db.commit()
    return uid, vid


async def _make_agreement(factory, uid, vid, *, status="active", suffix=None):
    from app.models.agreement import PurchaseAgreement

    aid = uuid.uuid4()
    tail = suffix or aid.hex[:6]
    async with factory() as db:
        db.add(PurchaseAgreement(
            id=aid, number=f"AGR-DMT-{tail}", title="Maintenance contract",
            agreement_type="recurring", vendor_id=vid, vendor_name="Liftow Limited",
            valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1),
            status=status, created_by=uid,
        ))
        await db.commit()
    return aid


async def _make_invoice(factory, uid, vid, *, agreement_id=None, total="100.00", ref=None):
    from app.models.invoice import Invoice

    iid = uuid.uuid4()
    async with factory() as db:
        db.add(Invoice(
            id=iid, internal_ref=ref or f"INV-DMT-{iid.hex[:6]}",
            vendor_invoice_number=f"V{iid.hex[:6]}", vendor_id=vid,
            vendor_name="Liftow Limited", amount=Decimal(total),
            tax_amount=Decimal("0"), total_amount=Decimal(total),
            invoice_date=date(2026, 8, 1), due_date=date(2026, 9, 1),
            line_items=[], uploaded_by=uid, agreement_id=agreement_id,
        ))
        await db.commit()
    return iid


# ── registry wiring ─────────────────────────────────────────────────────────

def test_registry_exposes_agreement_entities():
    from app.admin.registry import REGISTRY

    assert "agreement" in REGISTRY
    assert "agreement_receipt" in REGISTRY
    assert REGISTRY["agreement"].schema.label == "Purchase Agreement"
    assert REGISTRY["agreement_receipt"].schema.label == "Agreement Receipt"


def test_agreement_derived_and_identity_fields_are_readonly():
    """consumed_amount is derived from the invoice set (crud.invoice._recompute_consumed);
    letting an admin type a number into it produces a value the next invoice write silently
    overwrites. approval_step_idx belongs to the approval-state panel, not the edit form."""
    from app.admin.registry import REGISTRY

    schema = REGISTRY["agreement"].schema
    editable = schema.editable_field_names()
    for name in ("number", "consumed_amount", "approval_step_idx", "created_at"):
        assert name not in editable, f"{name} must be read-only"
    for name in ("title", "status", "valid_to", "not_to_exceed", "vendor_id", "department_id"):
        assert name in editable, f"{name} should be editable"


def test_agreement_receipt_claim_fields_are_readonly():
    """agreement_id / invoice_id define which agreement the receipt belongs to and which
    invoice claims it. Both are maintained by the match flow; editing them by hand desyncs
    invoices.receipt_ids from agreement_receipts.invoice_id — the exact drift
    _release_agreement_evidence exists to prevent."""
    from app.admin.registry import REGISTRY

    editable = REGISTRY["agreement_receipt"].schema.editable_field_names()
    for name in ("agreement_id", "invoice_id", "created_at"):
        assert name not in editable, f"{name} must be read-only"
    for name in ("receipt_type", "receipt_date", "status", "total_amount", "notes"):
        assert name in editable, f"{name} should be editable"


def test_agreement_status_enum_covers_engine_statuses():
    """The agr workflow terminal state is 'active', not 'approved' — and 'returned' is
    produced by the engine's return action. A missing option makes a legitimate state
    unreachable from the edit form."""
    from app.admin.registry import REGISTRY

    opts = REGISTRY["agreement"].schema.field_spec("status").options
    assert set(opts) == {"draft", "in_review", "returned", "active",
                         "expired", "closed", "cancelled"}


def test_departments_resolver_registered():
    """agreement.department_id drives approval routing (engine._routing_department_id);
    without a resolver the admin can only paste a raw UUID."""
    from app.admin.resolvers import resolver_sources

    assert "departments" in resolver_sources()


@pytest.mark.asyncio
async def test_departments_resolver_fetches_and_searches(test_engine):
    from app.models.department import Department
    from app.admin.resolvers import get_resolver

    factory = _factory(test_engine)
    did = uuid.uuid4()
    async with factory() as db:
        db.add(Department(id=did, code=f"D{did.hex[:4]}", name="Engineering"))
        await db.commit()

    r = get_resolver("departments")
    async with factory() as db:
        hit = await r.fetch_by_id(db, did)
        assert hit is not None and "Engineering" in hit.label
        hits = await r.search(db, "Engineer", 10)
        assert any(h.id == did for h in hits)


# ── agreement delete: refuses while downstream documents exist ──────────────

@pytest.mark.asyncio
async def test_agreement_delete_blocked_by_invoice(test_engine):
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid)
    await _make_invoice(factory, uid, vid, agreement_id=aid)

    async with factory() as db:
        with pytest.raises(ValueError, match="invoice"):
            await service.delete_record(db, "agreement", aid,
                                        actor_id=uid, actor_email="a@x.com")


@pytest.mark.asyncio
async def test_agreement_delete_blocked_by_payment_application(test_engine):
    from app.models.pa import PaymentApplication
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid)
    async with factory() as db:
        db.add(PaymentApplication(
            id=uuid.uuid4(), pa_number=f"PA-DMT-{uuid.uuid4().hex[:6]}", title="t",
            vendor_id=vid, vendor_name="Liftow Limited", invoice_ids=[], gr_ids=[],
            subtotal=Decimal("10"), payment_amount=Decimal("10"),
            created_by=uid, agreement_id=aid,
        ))
        await db.commit()

    async with factory() as db:
        with pytest.raises(ValueError, match="payment application"):
            await service.delete_record(db, "agreement", aid,
                                        actor_id=uid, actor_email="a@x.com")


@pytest.mark.asyncio
async def test_agreement_delete_preview_names_the_blockers(test_engine):
    """The preview must show WHY a delete will be refused, before the admin clicks it —
    otherwise the only feedback is a 400 after the fact."""
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid)
    await _make_invoice(factory, uid, vid, agreement_id=aid)

    async with factory() as db:
        summary = await service.delete_preview(db, "agreement", aid)
        assert summary.get("blocked_by_invoices") == 1


@pytest.mark.asyncio
async def test_agreement_delete_removes_children_and_workflow_refs(test_engine):
    from app.models.agreement import PurchaseAgreement
    from app.models.agreement_schedule import AgreementPaymentSchedule
    from app.models.agreement_attachment import AgreementAttachment
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid)

    async with factory() as db:
        for i in range(3):
            db.add(AgreementPaymentSchedule(
                id=uuid.uuid4(), agreement_id=aid, schedule_type="recurring",
                sequence=i, expected_amount=Decimal("50"), status="pending"))
        db.add(AgreementAttachment(id=uuid.uuid4(), agreement_id=aid, filename="c.pdf",
                                   content_type="application/pdf", file_size=1,
                                   storage_key=uuid.uuid4()))
        db.add(Task(id=uuid.uuid4(), type="approve_agr", document_type="agr",
                    document_id=aid, document_number="AGR-DMT", title="Approve",
                    assigned_role="dept_manager", is_completed=True))
        db.add(ApprovalEvent(id=uuid.uuid4(), document_type="agr", document_id=aid,
                             document_number="AGR-DMT", step_idx=0, action="submit",
                             actor_id=uid, actor_role="procurement_officer"))
        await db.commit()

    async with factory() as db:
        summary = await service.delete_record(db, "agreement", aid,
                                              actor_id=uid, actor_email="a@x.com")
        await db.commit()

    assert summary["purchase_agreements"] == 1
    assert summary["agreement_payment_schedule"] == 3
    assert summary["agreement_attachments"] == 1
    assert summary["tasks"] == 1
    assert summary["approval_events"] == 1

    async with factory() as db:
        assert (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == aid))).scalar_one_or_none() is None
        assert (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == aid))).scalars().all() == []
        assert (await db.execute(select(Task).where(
            Task.document_id == aid))).scalars().all() == []


# ── receipt delete: releases the invoice claim first ────────────────────────

async def _make_receipt(factory, aid, uid, *, invoice_id=None, status="open", ref=None):
    from app.models.agreement_receipt import AgreementReceipt

    rid = uuid.uuid4()
    async with factory() as db:
        db.add(AgreementReceipt(
            id=rid, agreement_id=aid, receipt_type="counter_slip",
            receipt_date=date(2026, 8, 1), receipt_ref=ref or rid.hex[:8],
            amount=Decimal("50"), tax_amount=Decimal("0"), total_amount=Decimal("50"),
            received_by=uid, created_by=uid, status=status, invoice_id=invoice_id,
        ))
        await db.commit()
    return rid


@pytest.mark.asyncio
async def test_receipt_delete_releases_invoice_claim(test_engine):
    """Deleting a claimed receipt must drop it out of invoices.receipt_ids. Leaving the id
    behind points the invoice at a row that no longer exists, and every reader of that array
    (reconciliation totals, the receipts panel) then works from a dangling id."""
    from app.models.invoice import Invoice
    from app.models.agreement_receipt import AgreementReceipt
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid, status="active")
    iid = await _make_invoice(factory, uid, vid, agreement_id=aid)
    keep = await _make_receipt(factory, aid, uid, invoice_id=iid, status="reconciled")
    drop = await _make_receipt(factory, aid, uid, invoice_id=iid, status="reconciled")

    async with factory() as db:
        inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one()
        inv.receipt_ids = [str(keep), str(drop)]
        await db.commit()

    async with factory() as db:
        summary = await service.delete_record(db, "agreement_receipt", drop,
                                              actor_id=uid, actor_email="a@x.com")
        await db.commit()

    assert summary["agreement_receipts"] == 1
    assert summary.get("invoice_claims_released") == 1

    async with factory() as db:
        inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one()
        assert [str(x) for x in (inv.receipt_ids or [])] == [str(keep)]
        assert (await db.execute(select(AgreementReceipt).where(
            AgreementReceipt.id == drop))).scalar_one_or_none() is None
        # the sibling receipt keeps its claim untouched
        other = (await db.execute(select(AgreementReceipt).where(
            AgreementReceipt.id == keep))).scalar_one()
        assert other.invoice_id == iid


@pytest.mark.asyncio
async def test_receipt_delete_cascades_its_attachments(test_engine):
    from app.models.agreement_receipt_attachment import AgreementReceiptAttachment
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid)
    rid = await _make_receipt(factory, aid, uid)
    async with factory() as db:
        db.add(AgreementReceiptAttachment(
            id=uuid.uuid4(), receipt_id=rid, filename="slip.jpg",
            content_type="image/jpeg", file_size=10, storage_key=uuid.uuid4()))
        await db.commit()

    async with factory() as db:
        summary = await service.delete_record(db, "agreement_receipt", rid,
                                              actor_id=uid, actor_email="a@x.com")
        await db.commit()

    assert summary["agreement_receipt_attachments"] == 1
    async with factory() as db:
        assert (await db.execute(select(AgreementReceiptAttachment).where(
            AgreementReceiptAttachment.receipt_id == rid))).scalars().all() == []


@pytest.mark.asyncio
async def test_agreement_delete_routes_receipts_through_the_release_path(test_engine):
    """The DB FK would CASCADE receipts away, but that bypasses the claim release. The
    agreement handler must reuse the receipt handler so the口径 is defined once."""
    from app.models.invoice import Invoice
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid)
    # invoice NOT linked to the agreement (so the delete is not blocked) but holding
    # a receipt claim — the shape a legacy/settled match leaves behind.
    iid = await _make_invoice(factory, uid, vid, agreement_id=None)
    rid = await _make_receipt(factory, aid, uid, invoice_id=iid, status="reconciled")
    async with factory() as db:
        inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one()
        inv.receipt_ids = [str(rid)]
        await db.commit()

    async with factory() as db:
        summary = await service.delete_record(db, "agreement", aid,
                                              actor_id=uid, actor_email="a@x.com")
        await db.commit()

    assert summary["agreement_receipts"] == 1
    async with factory() as db:
        inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one()
        assert (inv.receipt_ids or []) == []


# ── pre-existing gap folded in: DM invoice delete left consumed_amount inflated ──

@pytest.mark.asyncio
async def test_admin_invoice_delete_recomputes_agreement_consumed(test_engine):
    """crud.invoice.delete recomputes the agreement's consumed_amount on delete (and says
    why in a comment); the Data Maintenance cascade handler never did. Deleting an
    agreement-matched invoice here used to leave consumed_amount permanently inflated,
    which is what the NTE warning banner reads."""
    from app.models.agreement import PurchaseAgreement
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    aid = await _make_agreement(factory, uid, vid)
    keep = await _make_invoice(factory, uid, vid, agreement_id=aid, total="300.00")
    drop = await _make_invoice(factory, uid, vid, agreement_id=aid, total="200.00")

    async with factory() as db:
        agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == aid))).scalar_one()
        agr.consumed_amount = Decimal("500.00")
        await db.commit()

    async with factory() as db:
        await service.delete_record(db, "invoice", drop, actor_id=uid, actor_email="a@x.com")
        await db.commit()

    async with factory() as db:
        agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == aid))).scalar_one()
        assert agr.consumed_amount == Decimal("300.00"), \
            "consumed_amount must be re-derived from the surviving invoices"
        assert keep  # the other invoice is untouched


# ── owner change reassigns the open confirm tasks ───────────────────────────
# create_confirm_task resolves the assignee once, at the moment a period is
# claimed, and never revisits it. Data Maintenance is the only write path that
# reaches a live agreement's owner (the EPMS PATCH endpoint is fenced to
# draft/returned), so without this the documented repair — "set an owner and
# the right person will see it" — silently does nothing: the new owner still
# has no Confirm button (the schedule table renders it from "I hold this
# task"), and the previous assignee keeps a to-do that is no longer theirs.

async def _make_confirm_task(factory, agreement_id, *, assignee=None,
                             role="dept_manager", label="2026-09", completed=False):
    from app.models.task import Task

    tid = uuid.uuid4()
    async with factory() as db:
        db.add(Task(
            id=tid, type="confirm_period", priority="normal", document_type="agr",
            document_id=agreement_id, document_number=f"AGR-DMT · {label}",
            assigned_role=role, assigned_user_id=assignee,
            title=f"Confirm service for {label}", is_completed=completed,
        ))
        await db.commit()
    return tid


async def _seed_person(factory, *, role="procurement_officer", department_id=None):
    from app.models.user import User

    uid = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=uid, email=f"p-{uid.hex[:8]}@x.com", hashed_password="x",
                    full_name="Person", role=role, department_id=department_id))
        await db.commit()
    return uid


@pytest.mark.asyncio
async def test_dm_owner_change_reassigns_open_confirm_tasks(test_engine):
    from app.models.task import Task
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    agr_id = await _make_agreement(factory, uid, vid)
    old_owner = await _seed_person(factory, role="dept_manager")
    new_owner = await _seed_person(factory, role="procurement_officer")
    open_task = await _make_confirm_task(factory, agr_id, assignee=old_owner)
    done_task = await _make_confirm_task(
        factory, agr_id, assignee=old_owner, label="2026-08", completed=True)

    async with factory() as db:
        after = await service.edit_record(db, "agreement", agr_id,
                                          {"owner_id": str(new_owner)},
                                          actor_id=uid, actor_email="admin@x.com")
        await db.commit()
    assert after["confirm_tasks_reassigned"] == 1

    async with factory() as db:
        t = (await db.execute(select(Task).where(Task.id == open_task))).scalar_one()
        assert t.assigned_user_id == new_owner
        # assigned_role describes who the assignee actually is, matching what
        # _confirm_assignee stores when owner_id resolves on the create path.
        assert t.assigned_role == "procurement_officer"
        # A completed task is history, not a to-do — it must not be rewritten.
        d = (await db.execute(select(Task).where(Task.id == done_task))).scalar_one()
        assert d.assigned_user_id == old_owner


# The two fallback rungs below are exercised against the crud helper directly,
# not through Data Maintenance: _apply_reference refuses to clear ANY reference
# field ("is a required reference and cannot be cleared"), so an owner can be
# replaced there but never emptied. The helper still has to handle an empty
# owner, because the EPMS PATCH path can send owner_id: null — and it must
# degrade the same way create_confirm_task does.

@pytest.mark.asyncio
async def test_reassign_falls_back_to_the_department_manager_with_no_owner(test_engine):
    from app.models.agreement import PurchaseAgreement
    from app.models.department import Department
    from app.models.task import Task
    from app.crud.agreement_schedule import reassign_open_confirm_tasks

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    dept_id = uuid.uuid4()
    async with factory() as db:
        db.add(Department(id=dept_id, code=f"D{dept_id.hex[:4]}", name="Supply Chain DMT"))
        await db.commit()
    manager = await _seed_person(factory, role="dept_manager", department_id=dept_id)
    previous = await _seed_person(factory)
    agr_id = await _make_agreement(factory, uid, vid)
    task_id = await _make_confirm_task(factory, agr_id, assignee=previous)

    async with factory() as db:
        agr = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr_id))).scalar_one()
        agr.owner_id, agr.department_id = None, dept_id
        assert await reassign_open_confirm_tasks(db, agr) == 1
        await db.commit()

    async with factory() as db:
        t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.assigned_user_id == manager
        assert t.assigned_role == "dept_manager"


@pytest.mark.asyncio
async def test_reassign_with_neither_owner_nor_manager_lands_on_system_admin(test_engine):
    """Not a dept_manager role broadcast — a NULL-assignee dept_manager task
    reaches every department manager in the company (task.py::get_for_role does
    not narrow dept_manager by department). Same reasoning, and the same stored
    role, as create_confirm_task's no-assignee branch."""
    from app.models.agreement import PurchaseAgreement
    from app.models.task import Task
    from app.crud.agreement_schedule import reassign_open_confirm_tasks

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    previous = await _seed_person(factory)
    agr_id = await _make_agreement(factory, uid, vid)   # no department_id
    task_id = await _make_confirm_task(factory, agr_id, assignee=previous)

    async with factory() as db:
        agr = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr_id))).scalar_one()
        agr.owner_id = None
        assert await reassign_open_confirm_tasks(db, agr) == 1
        await db.commit()

    async with factory() as db:
        t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.assigned_user_id is None
        assert t.assigned_role == "system_admin"


@pytest.mark.asyncio
async def test_dm_edit_that_leaves_owner_alone_does_not_touch_the_tasks(test_engine):
    from app.models.task import Task
    from app.admin import service

    factory = _factory(test_engine)
    uid, vid = await _seed_actor_and_vendor(factory)
    owner = await _seed_person(factory, role="dept_manager")
    agr_id = await _make_agreement(factory, uid, vid)
    task_id = await _make_confirm_task(factory, agr_id, assignee=owner, role="dept_manager")

    async with factory() as db:
        after = await service.edit_record(db, "agreement", agr_id,
                                          {"title": "Renamed, owner untouched"},
                                          actor_id=uid, actor_email="admin@x.com")
        await db.commit()
    assert "confirm_tasks_reassigned" not in after

    async with factory() as db:
        t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.assigned_user_id == owner
