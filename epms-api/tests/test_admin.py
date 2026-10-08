"""Tests for the cross-system data-maintenance admin (Phase 1: EPMS)."""
import uuid

import pytest

from app.crud.config import PERMISSION_KEYS, _DEFAULT_ROLE_PERMISSIONS


def test_data_maintenance_permission_registered():
    assert "data_maintenance" in PERMISSION_KEYS


def test_system_admin_has_data_maintenance_by_default():
    assert _DEFAULT_ROLE_PERMISSIONS["system_admin"]["data_maintenance"] is True


def test_non_admin_lacks_data_maintenance_by_default():
    assert _DEFAULT_ROLE_PERMISSIONS["ap_clerk"].get("data_maintenance", False) is False


@pytest.mark.asyncio
async def test_admin_audit_log_model_persists(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.admin_audit_log import AdminAuditLog

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = AdminAuditLog(
            actor_id=uuid.uuid4(),
            actor_email="admin@example.com",
            action="delete",
            system="epms",
            entity="pr",
            record_id=uuid.uuid4(),
            record_number="PR-0001",
            before={"status": "draft"},
            after=None,
            cascade_summary={"purchase_orders": 1, "tasks": 2},
        )
        db.add(row)
        await db.commit()
        assert row.id is not None
        assert row.created_at is not None


def test_field_spec_serialization():
    from app.admin.fields import FieldSpec, EntitySchema

    schema = EntitySchema(
        key="pr",
        label="Purchase Request",
        number_field="number",
        list_columns=["number", "title", "status", "amount"],
        search_fields=["number", "title"],
        order_by="created_at desc",
        fields=[
            FieldSpec(name="number", type="string", editable=False),
            FieldSpec(name="amount", type="decimal", editable=True),
            FieldSpec(name="status", type="enum", editable=True, options=["draft", "approved"]),
        ],
    )
    data = schema.to_dict()
    assert data["key"] == "pr"
    assert data["fields"][0] == {"name": "number", "type": "string", "editable": False,
                                 "label": "number", "options": None,
                                 "ref_source": None, "ref_name_field": None}
    assert data["fields"][2]["options"] == ["draft", "approved"]


@pytest.mark.asyncio
async def test_purge_polymorphic_counts_and_deletes(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from app.models.task import Task
    from app.admin.cascade import count_polymorphic, delete_polymorphic

    doc_id = uuid.uuid4()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(Task(document_type="pr", document_id=doc_id, document_number="PR-9",
                    type="approve_pr", assigned_role="finance_bp", title="Test PR 1"))
        db.add(Task(document_type="pr", document_id=doc_id, document_number="PR-9",
                    type="approve_pr", assigned_role="finance_bp", title="Test PR 2"))
        db.add(Task(document_type="po", document_id=uuid.uuid4(), document_number="PO-1",
                    type="approve_po", assigned_role="finance_bp", title="Test PO 1"))
        await db.commit()

        n = await count_polymorphic(db, Task, doc_id)
        assert n == 2
        await delete_polymorphic(db, Task, doc_id)
        await db.commit()
        remaining = (await db.execute(
            select(Task).where(Task.document_type == "pr", Task.document_id == doc_id)
        )).scalars().all()
        assert remaining == []


def test_registry_has_the_epms_entities():
    from app.admin.registry import REGISTRY
    assert set(REGISTRY.keys()) == {"pr", "po", "gr", "invoice", "pa",
                                    "agreement", "agreement_receipt", "task"}
    for spec in REGISTRY.values():
        assert spec.schema.number_field
        assert spec.schema.fields            # non-empty
        assert callable(spec.cascade_delete)
        assert callable(spec.cascade_preview)


@pytest.mark.asyncio
async def test_pr_cascade_deletes_subtree_and_workflow_refs(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select, func
    from datetime import date
    from decimal import Decimal
    from app.models.vendor import Vendor
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.models.po import PurchaseOrder
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.models.task import Task
    from app.admin.registry import REGISTRY

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Create prerequisite vendor + user rows (FK constraints)
    vendor_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vendor_id, code=f"V-{vendor_id.hex[:8]}", name="Test Vendor",
                      category="supplier", contact_name="Alice", contact_email="alice@example.com"))
        db.add(User(id=user_id, email=f"u-{user_id.hex[:8]}@example.com",
                    hashed_password="x", full_name="Test User", role="requester"))
        await db.commit()

    pr_id = uuid.uuid4(); po_id = uuid.uuid4(); gr_id = uuid.uuid4()
    creator = user_id
    # PurchaseRequest and PurchaseOrder reference each other, so commit them in
    # dependency order across separate transactions (PR first, then PO).
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-T1", title="t", type=1, status="approved",
                               currency="CAD", amount=Decimal("10"), created_by=creator))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-T1", title="PO T1", type=1, status="approved",
                             currency="CAD", subtotal=Decimal("10"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("10"),
                             vendor_id=vendor_id, vendor_name="Test Vendor",
                             pr_id=pr_id, created_by=creator))
        await db.commit()
    async with factory() as db:
        db.add(GoodsReceipt(id=gr_id, number="GR-T1", title="GR T1", status="confirmed",
                            po_id=po_id, po_number="PO-T1",
                            pr_id=pr_id, pr_number="PR-T1",
                            vendor_id=vendor_id, vendor_name="Test Vendor",
                            gr_type="physical", procurement_type=1, currency="CAD",
                            created_by=creator))
        await db.flush()  # ensure GR exists before inserting the invoice that FKs it
        db.add(Invoice(id=uuid.uuid4(), internal_ref="INV-T1",
                       vendor_invoice_number="VI-001",
                       vendor_id=vendor_id, vendor_name="Test Vendor",
                       status="unmatched",
                       po_id=po_id, gr_id=gr_id,
                       amount=Decimal("10"), tax_amount=Decimal("0"), total_amount=Decimal("10"),
                       currency="CAD",
                       invoice_date=date(2025, 1, 1), due_date=date(2025, 2, 1),
                       line_items=[],
                       uploaded_by=user_id))
        db.add(Task(document_type="pr", document_id=pr_id, document_number="PR-T1",
                    type="approve_pr", assigned_role="finance_bp", title="Approve PR-T1"))
        await db.commit()

    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        summary = await REGISTRY["pr"].cascade_delete(db, pr)
        await db.commit()
        assert summary["purchase_orders"] >= 1
        assert summary["goods_receipts"] >= 1
        assert summary["invoices"] >= 1
        assert summary["tasks"] >= 1

    async with factory() as db:
        for model in (PurchaseRequest, PurchaseOrder, GoodsReceipt, Invoice):
            cnt = (await db.execute(select(func.count()).select_from(model))).scalar_one()
            assert cnt == 0, f"{model.__name__} not fully deleted"


@pytest.mark.asyncio
async def test_service_list_get_and_edit_writes_audit(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from decimal import Decimal
    from app.models.pr import PurchaseRequest
    from app.models.admin_audit_log import AdminAuditLog
    from app.models.user import User
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pr_id = uuid.uuid4(); actor = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=actor, email=f"svc-{actor.hex[:8]}@example.com",
                    hashed_password="x", full_name="Svc User", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-S1", title="orig", type=1, status="draft",
                               currency="CAD", amount=Decimal("5"), created_by=actor))
        await db.commit()

    async with factory() as db:
        rows, total = await service.list_records(db, "pr", page=1, page_size=20, search=None)
        assert total >= 1
        rec = await service.get_record(db, "pr", pr_id)
        assert rec["number"] == "PR-S1"

        # amount is a derived/read-only header field (recomputed from line items);
        # edit a plain editable field instead to exercise the write path.
        updated = await service.edit_record(
            db, "pr", pr_id, {"title": "fixed", "budget_code": "BC-9"},
            actor_id=actor, actor_email="a@x.com",
        )
        await db.commit()
        assert updated["title"] == "fixed"

    async with factory() as db:
        fresh = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert fresh.title == "fixed"
        assert fresh.budget_code == "BC-9"
        # Scoped to THIS record, not the whole table: the audit log is shared
        # by every test in the run, so a table-wide count only held as long as
        # no other test in the session happened to edit anything.
        audits = (await db.execute(select(AdminAuditLog).where(
            AdminAuditLog.action == "edit",
            AdminAuditLog.record_id == pr_id))).scalars().all()
        assert len(audits) == 1
        assert audits[0].before["title"] == "orig"
        assert audits[0].after["title"] == "fixed"


@pytest.mark.asyncio
async def test_edit_rejects_non_editable_field(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from decimal import Decimal
    from app.models.pr import PurchaseRequest
    from app.models.user import User
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pr_id = uuid.uuid4(); actor = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=actor, email=f"svc2-{actor.hex[:8]}@example.com",
                    hashed_password="x", full_name="Svc User 2", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-S2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("5"), created_by=actor))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError):
            await service.edit_record(db, "pr", pr_id, {"number": "HACK"},
                                      actor_id=actor, actor_email="a@x.com")


@pytest.mark.asyncio
async def test_delete_preview_then_delete_with_audit(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select, func
    from decimal import Decimal
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.models.admin_audit_log import AdminAuditLog
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pr_id = uuid.uuid4(); actor = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=actor, email=f"u-{actor.hex[:8]}@example.com",
                    hashed_password="x", full_name="Actor", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-D1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("5"), created_by=actor))
        await db.commit()

    async with factory() as db:
        preview = await service.delete_preview(db, "pr", pr_id)
        assert preview["purchase_requests"] == 1

    async with factory() as db:
        summary = await service.delete_record(db, "pr", pr_id,
                                               actor_id=actor, actor_email="a@x.com")
        await db.commit()
        assert summary["purchase_requests"] == 1

    async with factory() as db:
        cnt = (await db.execute(select(func.count()).select_from(PurchaseRequest).where(
            PurchaseRequest.id == pr_id))).scalar_one()
        assert cnt == 0
        adel = (await db.execute(select(AdminAuditLog).where(AdminAuditLog.action == "delete"))).scalars().all()
        assert len(adel) >= 1
        assert any(a.record_id == pr_id for a in adel)
        rec = next(a for a in adel if a.record_id == pr_id)
        assert rec.cascade_summary["purchase_requests"] == 1
        assert rec.before["number"] == "PR-D1"


@pytest.mark.asyncio
async def test_entities_endpoint_requires_permission(client):
    r = await client.get("/api/v1/admin/entities")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_can_list_entities(admin_client):
    r = await admin_client.get("/api/v1/admin/entities")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()}
    assert {"pr", "po", "gr", "invoice", "pa"} <= keys


@pytest.mark.asyncio
async def test_admin_list_records_endpoint(admin_client):
    r = await admin_client.get("/api/v1/admin/pr?page=1&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body


@pytest.mark.asyncio
async def test_purge_matches_any_document_type(test_engine):
    """Regression: Direct-PA tasks carry document_type='pa_dir' (not 'pa').

    The cascade must purge tasks by document_id only, so deleting the PA also
    clears its Task Inbox entry regardless of the type tag the producer used.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from app.models.task import Task
    from app.admin.cascade import purge_workflow_refs

    doc_id = uuid.uuid4()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(Task(document_type="pa_dir", document_id=doc_id, document_number="PA-D1",
                    type="approve_pa", assigned_role="finance_bp", title="Approve Direct PA"))
        await db.commit()

        summary = await purge_workflow_refs(db, doc_id)
        await db.commit()
        assert summary["tasks"] == 1
        remaining = (await db.execute(
            select(Task).where(Task.document_id == doc_id)
        )).scalars().all()
        assert remaining == []


def test_task_entity_is_delete_only():
    from app.admin.registry import REGISTRY
    assert "task" in REGISTRY
    assert REGISTRY["task"].schema.allow_edit is False
    # delete-only entities expose no editable fields
    assert REGISTRY["task"].schema.editable_field_names() == set()


@pytest.mark.asyncio
async def test_edit_rejected_on_delete_only_entity(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.task import Task
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    tid = uuid.uuid4()
    async with factory() as db:
        db.add(Task(id=tid, document_type="pr", document_id=uuid.uuid4(), document_number="PR-Z",
                    type="approve_pr", assigned_role="finance_bp", title="Z"))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError):
            await service.edit_record(db, "task", tid, {"document_number": "X"},
                                      actor_id=uuid.uuid4(), actor_email="a@x.com")


@pytest.mark.asyncio
async def test_task_delete_removes_only_that_task(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select, func
    from app.models.task import Task
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc = uuid.uuid4(); keep_id = uuid.uuid4(); del_id = uuid.uuid4()
    async with factory() as db:
        # two tasks for the SAME document — deleting one must NOT remove the other
        db.add(Task(id=del_id, document_type="pr", document_id=doc, document_number="PR-K",
                    type="approve_pr", assigned_role="finance_bp", title="del"))
        db.add(Task(id=keep_id, document_type="pr", document_id=doc, document_number="PR-K",
                    type="approve_pr", assigned_role="gm", title="keep"))
        await db.commit()
    async with factory() as db:
        summary = await service.delete_record(db, "task", del_id,
                                              actor_id=uuid.uuid4(), actor_email="a@x.com")
        await db.commit()
        assert summary["tasks"] == 1
    async with factory() as db:
        remaining = (await db.execute(select(Task.id).where(Task.document_id == doc))).scalars().all()
        assert remaining == [keep_id]


@pytest.mark.asyncio
async def test_pa_delete_clears_prepayment_self_reference(test_engine):
    """A PA used as another PA's prepayment_pa_id (RESTRICT self-FK) must still
    be deletable — the cascade NULLs the referrer first."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from decimal import Decimal
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.pa import PaymentApplication
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    actor = uuid.uuid4(); vendor_id = uuid.uuid4(); prepay_id = uuid.uuid4(); final_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=actor, email=f"u-{actor.hex[:8]}@example.com",
                    hashed_password="x", full_name="A", role="requester"))
        db.add(Vendor(id=vendor_id, code=f"V-{vendor_id.hex[:8]}", name="Test Vendor",
                      category="supplier", contact_name="A", contact_email="a@example.com"))
        await db.commit()
    async with factory() as db:
        db.add(PaymentApplication(id=prepay_id, pa_number="PA-PRE", title="prepay", status="processed",
                                  payment_amount=Decimal("5"), subtotal=Decimal("5"), tax_amount=Decimal("0"),
                                  currency="CAD", created_by=actor,
                                  vendor_id=vendor_id, vendor_name="Test Vendor"))
        await db.commit()
    async with factory() as db:
        db.add(PaymentApplication(id=final_id, pa_number="PA-FIN", title="final", status="processed",
                                  payment_amount=Decimal("5"), subtotal=Decimal("5"), tax_amount=Decimal("0"),
                                  currency="CAD", created_by=actor,
                                  vendor_id=vendor_id, vendor_name="Test Vendor",
                                  prepayment_pa_id=prepay_id))
        await db.commit()
    async with factory() as db:
        summary = await service.delete_record(db, "pa", prepay_id, actor_id=actor, actor_email="a@x.com")
        await db.commit()
        assert summary["payment_applications"] == 1
    async with factory() as db:
        # prepay PA gone; final PA survives with its prepayment link cleared
        rows = (await db.execute(select(PaymentApplication.id, PaymentApplication.prepayment_pa_id))).all()
        ids = {r[0]: r[1] for r in rows}
        assert prepay_id not in ids
        assert ids.get(final_id) is None


def test_pa_override_receipt_permission_registered():
    from app.crud.config import PERMISSION_KEYS, _DEFAULT_ROLE_PERMISSIONS
    assert "pa_override_receipt" in PERMISSION_KEYS
    # 授权角色默认有,requester 默认无
    assert _DEFAULT_ROLE_PERMISSIONS["finance_manager"]["pa_override_receipt"] is True
    assert _DEFAULT_ROLE_PERMISSIONS["procurement_officer"]["pa_override_receipt"] is True
    assert _DEFAULT_ROLE_PERMISSIONS["requester"]["pa_override_receipt"] is False


# ── Cascade ORDER, under the session settings production actually runs with ────
# Every other cascade test above builds its factory WITHOUT autoflush=False, so
# each db.execute() inside a handler silently flushes the pending child deletes
# and the statement order comes out right by accident. The request session
# (app/db/session.py) sets autoflush=False, so nothing flushes until the end and
# SQLAlchemy orders the whole tree itself — with no relationship() between these
# documents it had no idea invoices must precede goods_receipts. These two tests
# therefore MUST keep autoflush=False; drop it and they stop testing anything.

def _prod_like_factory(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    return async_sessionmaker(test_engine, class_=AsyncSession,
                              expire_on_commit=False, autoflush=False)


async def _seed_po_gr_invoice(factory, *, with_invoice_po_link: bool):
    """PO + GR + one invoice matched to both (the shape Data Maintenance chokes on)."""
    from datetime import date
    from decimal import Decimal
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.models.po import PurchaseOrder
    from app.models.user import User
    from app.models.vendor import Vendor

    vendor_id = uuid.uuid4(); user_id = uuid.uuid4()
    po_id = uuid.uuid4(); gr_id = uuid.uuid4(); inv_id = uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vendor_id, code=f"V-{vendor_id.hex[:8]}", name="Test Vendor",
                      category="supplier", contact_name="A", contact_email="a@example.com"))
        db.add(User(id=user_id, email=f"u-{user_id.hex[:8]}@example.com",
                    hashed_password="x", full_name="Test User", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number=f"PO-{po_id.hex[:6]}", title="PO", type=1,
                             status="approved", currency="CAD", subtotal=Decimal("10"),
                             tax_rate=Decimal("0"), tax_amount=Decimal("0"), total=Decimal("10"),
                             vendor_id=vendor_id, vendor_name="Test Vendor", created_by=user_id))
        await db.commit()
    async with factory() as db:
        db.add(GoodsReceipt(id=gr_id, number=f"GR-{gr_id.hex[:6]}", title="GR", status="confirmed",
                            po_id=po_id, po_number=f"PO-{po_id.hex[:6]}",
                            vendor_id=vendor_id, vendor_name="Test Vendor",
                            gr_type="physical", procurement_type=1, currency="CAD",
                            created_by=user_id))
        await db.flush()
        db.add(Invoice(id=inv_id, internal_ref=f"INV-{inv_id.hex[:6]}",
                       vendor_invoice_number="VI-001", vendor_id=vendor_id,
                       vendor_name="Test Vendor", status="unmatched",
                       po_id=po_id if with_invoice_po_link else None, gr_id=gr_id,
                       amount=Decimal("10"), tax_amount=Decimal("0"), total_amount=Decimal("10"),
                       currency="CAD", invoice_date=date(2025, 1, 1), due_date=date(2025, 2, 1),
                       line_items=[], uploaded_by=user_id))
        await db.commit()
    return po_id, gr_id, inv_id, user_id


@pytest.mark.asyncio
async def test_gr_cascade_unlinks_its_invoice_instead_of_deleting_it(test_engine):
    """Deleting a GR withdraws the receipt link and LEAVES the invoice.

    Two regressions in one: the delete used to die on invoices_gr_id_fkey (the
    parent DELETE went out first, and the resulting 500 reached the dialog as a
    bare "Failed to fetch"), and what it was trying to do in the first place —
    take a financial record down with a mis-keyed receipt — was wrong.
    """
    from sqlalchemy import func, select
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.admin import service

    factory = _prod_like_factory(test_engine)
    po_id, gr_id, inv_id, actor = await _seed_po_gr_invoice(factory, with_invoice_po_link=True)

    async with factory() as db:
        summary = await service.delete_record(db, "gr", gr_id, actor_id=actor,
                                              actor_email="a@x.com")
        await db.commit()
    assert summary["goods_receipts"] == 1
    assert summary["invoices_unlinked"] == 1
    assert "invoices" not in summary

    async with factory() as db:
        gone = (await db.execute(select(func.count()).select_from(GoodsReceipt)
                                 .where(GoodsReceipt.id == gr_id))).scalar_one()
        assert gone == 0
        inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        # Receipt evidence withdrawn ...
        assert inv.gr_id is None
        assert not inv.gr_ids
        assert inv.gr_number is None
        # ... everything that describes the PO match left alone.
        assert inv.po_id == po_id
        assert inv.status == "unmatched"


@pytest.mark.asyncio
@pytest.mark.parametrize("invoice_status", ["approved", "paid"])
async def test_gr_delete_refused_while_its_invoice_is_in_payment(test_engine, invoice_status):
    """Unlinking would pull the evidence out from under a payment, so the GR
    delete is refused outright rather than quietly going around the same three
    conditions the AP-facing Unmatch button is gated on."""
    from sqlalchemy import select
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.admin import service

    factory = _prod_like_factory(test_engine)
    _, gr_id, inv_id, actor = await _seed_po_gr_invoice(factory, with_invoice_po_link=True)
    async with factory() as db:
        inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        inv.status = invoice_status
        await db.commit()

    async with factory() as db:
        preview = await service.delete_preview(db, "gr", gr_id)
        # The admin is told BEFORE clicking, not after.
        assert preview["blocked_by_in_payment_invoices"] == 1

    async with factory() as db:
        with pytest.raises(ValueError) as e:
            await service.delete_record(db, "gr", gr_id, actor_id=actor, actor_email="a@x.com")
        assert invoice_status in str(e.value)
        await db.rollback()

    async with factory() as db:
        # Nothing moved: neither the receipt nor the link it carries.
        assert (await db.execute(select(GoodsReceipt).where(
            GoodsReceipt.id == gr_id))).scalar_one_or_none() is not None
        inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.gr_id == gr_id


@pytest.mark.asyncio
async def test_gr_delete_refused_while_a_live_pa_claims_its_invoice(test_engine):
    """A PA sitting in draft/submitted/approved holds the invoice without the
    invoice's own status showing it — the check has to read invoice_ids."""
    from decimal import Decimal
    from sqlalchemy import select
    from app.models.invoice import Invoice
    from app.models.pa import PaymentApplication
    from app.admin import service

    factory = _prod_like_factory(test_engine)
    po_id, gr_id, inv_id, actor = await _seed_po_gr_invoice(factory, with_invoice_po_link=True)
    async with factory() as db:
        inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        db.add(PaymentApplication(pa_number="PA-CLAIM", title="claim", status="submitted",
                                  payment_amount=Decimal("10"), subtotal=Decimal("10"),
                                  tax_amount=Decimal("0"), currency="CAD", created_by=actor,
                                  vendor_id=inv.vendor_id, vendor_name="Test Vendor",
                                  po_id=po_id, invoice_ids=[str(inv_id)]))
        await db.commit()

    async with factory() as db:
        assert (await service.delete_preview(db, "gr", gr_id))["blocked_by_claimed_by_pa_invoices"] == 1

    async with factory() as db:
        with pytest.raises(ValueError) as e:
            await service.delete_record(db, "gr", gr_id, actor_id=actor, actor_email="a@x.com")
        assert "PA-CLAIM" in str(e.value)
        await db.rollback()


@pytest.mark.asyncio
async def test_po_preview_reports_a_po_and_gr_matched_invoice_once(test_engine):
    """An invoice reachable through BOTH po_id and gr_id is one row, not two.

    The dialog's numbers are the only warning an admin gets before an
    irreversible delete, so they have to match what the delete actually does —
    and this invoice goes away with the PO branch, so the GR branch must not
    also report it as an unlink.
    """
    from app.admin import service

    factory = _prod_like_factory(test_engine)
    po_id, _, _, actor = await _seed_po_gr_invoice(factory, with_invoice_po_link=True)

    async with factory() as db:
        preview = await service.delete_preview(db, "po", po_id)
        assert preview["invoices"] == 1
        assert "invoices_unlinked" not in preview
        summary = await service.delete_record(db, "po", po_id, actor_id=actor,
                                              actor_email="a@x.com")
        await db.commit()
    assert summary["invoices"] == preview["invoices"]
    assert "invoices_unlinked" not in summary
