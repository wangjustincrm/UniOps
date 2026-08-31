"""Tests for expanded Data Maintenance edit (references, line items, approval state)."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def test_fieldspec_reference_serialization():
    from app.admin.fields import FieldSpec

    f = FieldSpec("vendor_id", "reference", True, label="Vendor",
                  ref_source="vendors", ref_name_field="vendor_name")
    d = f.to_dict()
    assert d["type"] == "reference"
    assert d["ref_source"] == "vendors"
    assert d["ref_name_field"] == "vendor_name"


def test_entityschema_child_serialization():
    from app.admin.fields import EntitySchema, FieldSpec, ChildSchema
    from app.models.pr import PrLineItem

    child = ChildSchema(
        table_label="Line Items", model=PrLineItem, fk_field="pr_id",
        fields=[FieldSpec("description", "string", True), FieldSpec("qty", "decimal", True)],
    )
    schema = EntitySchema(
        key="pr", label="PR", number_field="number",
        list_columns=["number"], search_fields=["number"], order_by="created_at desc",
        fields=[FieldSpec("number", "string", False)], child=child,
    )
    d = schema.to_dict()
    assert d["child"]["table_label"] == "Line Items"
    assert d["child"]["fk_field"] == "pr_id"
    assert [f["name"] for f in d["child"]["fields"]] == ["description", "qty"]


@pytest.mark.asyncio
async def test_resolver_fetch_and_search_vendors(test_engine):
    from app.models.vendor import Vendor
    from app.admin.resolvers import get_resolver

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vid = uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vid, code="V-ABC", name="Acme Supplies",
                      category="supplier", contact_name="A", contact_email="a@x.com"))
        await db.commit()

    resolver = get_resolver("vendors")
    async with factory() as db:
        got = await resolver.fetch_by_id(db, vid)
        assert got is not None and got.label == "Acme Supplies"
        hits = await resolver.search(db, "acme", limit=10)
        assert any(h.id == vid for h in hits)


def test_registry_pr_has_reference_and_expanded_fields():
    from app.admin.registry import REGISTRY

    schema = REGISTRY["pr"].schema
    names = {f.name for f in schema.fields}
    assert {"created_by", "vendor_id", "cost_center_id", "project_code",
            "delivery_address", "is_prepaid"} <= names
    created_by = schema.field_spec("created_by")
    assert created_by.type == "reference" and created_by.ref_source == "users"
    assert created_by.editable is True
    vendor = schema.field_spec("vendor_id")
    assert vendor.ref_source == "vendors" and vendor.ref_name_field == "vendor_name"


def test_reference_name_fields_serialize_readonly():
    from app.admin.registry import REGISTRY

    for key in ("pr", "po", "pa"):
        schema = REGISTRY[key].schema
        vendor_name = schema.field_spec("vendor_name")
        assert vendor_name is not None, f"{key} missing vendor_name field"
        assert vendor_name.editable is False, f"{key} vendor_name must be read-only"

    pr_schema = REGISTRY["pr"].schema
    cost_center_name = pr_schema.field_spec("cost_center_name")
    assert cost_center_name is not None
    assert cost_center_name.editable is False


@pytest.mark.asyncio
async def test_edit_reference_sets_id_and_syncs_name(test_engine):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); new_vendor = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"c-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="Creator", role="requester"))
        db.add(Vendor(id=new_vendor, code="V-NEW", name="New Vendor Inc",
                      category="supplier", contact_name="N", contact_email="n@x.com"))
        await db.commit()
    pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-REF1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), vendor_name="Old Vendor",
                               created_by=creator))
        await db.commit()

    async with factory() as db:
        await service.edit_record(db, "pr", pr_id, {"vendor_id": str(new_vendor)},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert str(pr.vendor_id) == str(new_vendor)
        assert pr.vendor_name == "New Vendor Inc"   # denormalized name synced


@pytest.mark.asyncio
async def test_edit_reference_unknown_id_rejected(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"c2-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C2", role="requester"))
        await db.commit()
    pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-REF2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError, match="not found"):
            await service.edit_record(db, "pr", pr_id, {"vendor_id": str(uuid.uuid4())},
                                      actor_id=creator, actor_email="admin@x.com")


def test_recompute_po_header():
    from decimal import Decimal
    from app.admin.recompute import recompute_header

    class _Row:
        tax_rate = Decimal("0.05")
    row = _Row()
    lines = [{"line_total": Decimal("100.00")}, {"line_total": Decimal("50.00")}]
    result = recompute_header("po", row, lines)
    assert result["subtotal"] == Decimal("150.00")
    assert result["tax_amount"] == Decimal("7.50")
    assert result["total"] == Decimal("157.50")


def test_recompute_pa_header_with_charges():
    from decimal import Decimal
    from app.admin.recompute import recompute_header

    class _Row:
        tax_rate = Decimal("0.10")
        shipping_amount = Decimal("20.00")
        other_charges = Decimal("5.00")
        prepayment_applied = None
    row = _Row()
    lines = [{"line_total": Decimal("200.00")}]
    result = recompute_header("pa", row, lines)
    assert result["subtotal"] == Decimal("200.00")
    assert result["tax_amount"] == Decimal("20.00")
    assert result["payment_amount"] == Decimal("245.00")  # 200 + 20 + 20 + 5 - 0


def test_registry_entities_have_child_line_items():
    from app.admin.registry import REGISTRY

    for key, fk in [("pr", "pr_id"), ("po", "po_id"), ("pa", "pa_id")]:
        child = REGISTRY[key].schema.child
        assert child is not None, f"{key} missing child schema"
        assert child.fk_field == fk
        names = {f.name for f in child.fields}
        assert {"description", "qty", "unit", "unit_price"} <= names
        # line_total is server-computed → read-only in the child schema
        lt = child.field_type("line_total")
        assert lt == "decimal"
        assert not any(f.name == "line_total" and f.editable for f in child.fields)


@pytest.mark.asyncio
async def test_edit_line_items_add_update_delete_and_recompute(test_engine):
    from app.models.user import User
    from app.models.po import PurchaseOrder, PoLineItem
    from app.models.vendor import Vendor
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); vid = uuid.uuid4(); po_id = uuid.uuid4(); keep_line = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"po-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        db.add(Vendor(id=vid, code="V-PO", name="V", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-LI1", title="t", type=1, status="draft",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0.05"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=vid, vendor_name="V", created_by=creator))
        await db.flush()
        db.add(PoLineItem(id=keep_line, po_id=po_id, description="old", qty=Decimal("1"),
                          unit="ea", unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        await db.commit()

    # keep+update the existing line (qty 1->2), add a new line, (implicitly delete none here)
    line_items = [
        {"id": str(keep_line), "description": "updated", "qty": "2", "unit": "ea", "unit_price": "10"},
        {"description": "brand new", "qty": "3", "unit": "ea", "unit_price": "100"},
    ]
    async with factory() as db:
        await service.edit_record(db, "po", po_id, {"line_items": line_items},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()

    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        lines = (await db.execute(select(PoLineItem).where(PoLineItem.po_id == po_id))).scalars().all()
        assert len(lines) == 2
        # 2*10 + 3*100 = 320 subtotal; tax 5% = 16.00; total 336.00
        assert po.subtotal == Decimal("320.00")
        assert po.tax_amount == Decimal("16.00")
        assert po.total == Decimal("336.00")
        assert {l.line_total for l in lines} == {Decimal("20.00"), Decimal("300.00")}


@pytest.mark.asyncio
async def test_edit_line_items_deletes_dropped_rows(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest, PrLineItem
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4(); l1 = uuid.uuid4(); l2 = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"pr-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-LI2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.flush()
        db.add(PrLineItem(id=l1, pr_id=pr_id, description="a", qty=Decimal("1"), unit="ea",
                          unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        db.add(PrLineItem(id=l2, pr_id=pr_id, description="b", qty=Decimal("1"), unit="ea",
                          unit_price=Decimal("20"), line_total=Decimal("20"), sort_order=1))
        await db.commit()
    async with factory() as db:
        await service.edit_record(db, "pr", pr_id,
                                  {"line_items": [{"id": str(l1), "description": "a", "qty": "1",
                                                   "unit": "ea", "unit_price": "10"}]},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        remaining = (await db.execute(select(PrLineItem).where(PrLineItem.pr_id == pr_id))).scalars().all()
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert {l.id for l in remaining} == {l1}
        assert pr.amount == Decimal("10.00")


@pytest.mark.asyncio
async def test_get_record_includes_line_items(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest, PrLineItem
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"g-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-G1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("10"), created_by=creator))
        await db.flush()
        db.add(PrLineItem(id=uuid.uuid4(), pr_id=pr_id, description="a", qty=Decimal("1"),
                          unit="ea", unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        await db.commit()
    async with factory() as db:
        rec = await service.get_record(db, "pr", pr_id)
        assert "line_items" in rec and len(rec["line_items"]) == 1
        assert rec["line_items"][0]["description"] == "a"
        assert rec["line_items"][0]["line_total"] == "10.00"
        assert "id" in rec["line_items"][0]


@pytest.mark.asyncio
async def test_edit_approval_state_reassigns_open_tasks(test_engine, monkeypatch):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.models.task import Task
    from app.admin import service
    from app.services import approval_client

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4(); done_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"as-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-AS1", title="t", type=1, status="in_review",
                               currency="CAD", amount=Decimal("0"), created_by=creator,
                               approval_step_idx=1))
        await db.flush()
        db.add(Task(document_type="pr", document_id=pr_id, document_number="PR-AS1",
                    type="approve_pr", assigned_role="dept_manager", title="Approve", is_completed=False))
        db.add(Task(id=done_id, document_type="pr", document_id=pr_id, document_number="PR-AS1",
                    type="approve_pr", assigned_role="finance_bp", title="Done", is_completed=True))
        await db.commit()

    # Contract change (2026-08-14): setting a step no longer edits the open task in
    # place — it CLOSES the stale one and hands off to the approval engine to issue
    # the task for the new step, because the engine derives the true step from any
    # open approve task and would otherwise snap the new index straight back. The
    # role override is applied by the route AFTER that resync (see
    # test_admin_approval_state.py); at this layer it only comes back as intent.
    async def _steps(doc_type, doc_id, bearer_token):
        return [{"id": f"s{i}", "role": r, "label": r} for i, r in enumerate(
            ["dept_manager", "finance_bp", "finance_manager"])]
    monkeypatch.setattr(approval_client, "get_workflow_steps", _steps)

    async with factory() as db:
        result = await service.edit_approval_state(
            db, "pr", pr_id, {"approval_step_idx": 2, "assigned_role": "finance_manager"},
            actor_id=creator, actor_email="admin@x.com", bearer_token="t")
        await db.commit()
    assert result["closed_stale_tasks"] == 1
    assert result["resync_doc_type"] == "pr"
    assert result["assigned_role"] == "finance_manager"    # handed to the route

    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        tasks = (await db.execute(select(Task).where(Task.document_id == pr_id))).scalars().all()
        assert pr.approval_step_idx == 2
        assert [t for t in tasks if not t.is_completed] == []   # stale one closed
        done_t = [t for t in tasks if t.is_completed]
        assert {t.assigned_role for t in done_t} == {"dept_manager", "finance_bp"}
        original_done = next(t for t in done_t if t.id == done_id)
        assert original_done.assigned_role == "finance_bp"      # already-completed untouched


@pytest.mark.asyncio
async def test_po_vendor_change_regenerates_number_and_cascades(test_engine):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); v_old = uuid.uuid4(); v_new = uuid.uuid4()
    pr_id = uuid.uuid4(); po_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"pn-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        db.add(Vendor(id=v_old, code="OLD", name="Old Co", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        db.add(Vendor(id=v_new, code="NEW", name="New Co", category="supplier",
                      contact_name="B", contact_email="b@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-PN1", title="t", type=1, status="approved",
                               currency="CAD", amount=Decimal("0"), created_by=creator,
                               po_id=po_id, po_number="PO-OLD-2501-01"))
        await db.flush()
        db.add(PurchaseOrder(id=po_id, number="PO-OLD-2501-01", title="t", type=1, status="approved",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=v_old, vendor_name="Old Co", pr_id=pr_id, created_by=creator))
        db.add(Task(document_type="po", document_id=po_id, document_number="PO-OLD-2501-01",
                    type="approve_po", assigned_role="finance_bp", title="Approve"))
        db.add(ApprovalEvent(document_type="po", document_id=po_id, document_number="PO-OLD-2501-01",
                             step_idx=0, action="submitted", actor_id=creator, actor_role="requester"))
        await db.commit()

    async with factory() as db:
        await service.edit_record(db, "po", po_id,
                                  {"vendor_id": str(v_new)},
                                  actor_id=creator, actor_email="admin@x.com",
                                  regenerate_po_number=True)
        await db.commit()

    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        task = (await db.execute(select(Task).where(Task.document_id == po_id))).scalars().first()
        ev = (await db.execute(select(ApprovalEvent).where(ApprovalEvent.document_id == po_id))).scalars().first()
        assert po.number.startswith("PO-NEW-")          # regenerated with new vendor code
        assert po.number != "PO-OLD-2501-01"
        assert pr.po_number == po.number                # cascade → PR
        assert task.document_number == po.number        # cascade → tasks
        assert ev.document_number == po.number          # cascade → approval_events


@pytest.mark.asyncio
async def test_po_vendor_change_without_flag_keeps_number(test_engine):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.po import PurchaseOrder
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); v_old = uuid.uuid4(); v_new = uuid.uuid4(); po_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"pk-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        db.add(Vendor(id=v_old, code="OLD2", name="Old2", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        db.add(Vendor(id=v_new, code="NEW2", name="New2", category="supplier",
                      contact_name="B", contact_email="b@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-OLD2-2501-01", title="t", type=1, status="draft",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=v_old, vendor_name="Old2", created_by=creator))
        await db.commit()
    async with factory() as db:
        await service.edit_record(db, "po", po_id, {"vendor_id": str(v_new)},
                                  actor_id=creator, actor_email="admin@x.com",
                                  regenerate_po_number=False)
        await db.commit()
    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        assert po.number == "PO-OLD2-2501-01"           # unchanged
        assert po.vendor_name == "New2"                 # vendor still changed


# ── Task 12: routing resync on Requester change ─────────────────────────────

@pytest.mark.asyncio
async def test_requester_change_flags_resync(test_engine):
    """Service layer: changing a PR's created_by returns a flag telling the
    endpoint layer to fire a post-commit routing resync. The service itself
    makes no HTTP call — approval-api reads the shared DB, so the resync must
    run only after the transaction actually lands."""
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    old_creator = uuid.uuid4(); new_creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        for u, name in [(old_creator, "Old"), (new_creator, "New")]:
            db.add(User(id=u, email=f"{u.hex[:6]}@x.com", hashed_password="x",
                        full_name=name, role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-RS1", title="t", type=1, status="in_review",
                               currency="CAD", amount=Decimal("0"), created_by=old_creator))
        await db.commit()

    async with factory() as db:
        result = await service.edit_record(db, "pr", pr_id, {"created_by": str(new_creator)},
                                           actor_id=old_creator, actor_email="admin@x.com",
                                           bearer_token="tok")
        await db.commit()
    assert result["_routing_requester_changed"] is True


@pytest.mark.asyncio
async def test_requester_change_on_other_field_does_not_flag_resync(test_engine):
    """Sanity check on the flag's precision: editing a non-created_by field on a
    PR must NOT flag a resync."""
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"nf-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="Creator", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-RS2", title="t", type=1, status="in_review",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.commit()

    async with factory() as db:
        result = await service.edit_record(db, "pr", pr_id, {"title": "Updated title"},
                                           actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    assert result["_routing_requester_changed"] is False


@pytest.mark.asyncio
async def test_edit_endpoint_fires_resync(admin_client, test_engine, monkeypatch):
    """Endpoint layer: PATCHing a PR's created_by via the Data Maintenance HTTP
    endpoint fires the resync post-commit, and the internal flag never leaks
    into the HTTP response."""
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.services import approval_client

    calls = []

    async def _fake_resync(doc_type, doc_id, bearer_token=None):
        calls.append((doc_type, doc_id))
        return {"resynced": {"actions": ["reissue"]}}

    monkeypatch.setattr(approval_client, "resync_document", _fake_resync)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    old_creator = uuid.uuid4(); new_creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        for u, name in [(old_creator, "Old"), (new_creator, "New")]:
            db.add(User(id=u, email=f"ep-{u.hex[:6]}@x.com", hashed_password="x",
                        full_name=name, role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-EP1", title="t", type=1, status="in_review",
                               currency="CAD", amount=Decimal("0"), created_by=old_creator))
        await db.commit()

    r = await admin_client.patch(f"/api/v1/admin/pr/{pr_id}", json={"created_by": str(new_creator)})
    assert r.status_code == 200
    body = r.json()
    assert calls == [("pr", str(pr_id))]
    assert "_routing_requester_changed" not in body
    assert body["routing_resync"] == "ok"


def test_coerce_bool_parses_string_false():
    from app.admin.service import _coerce

    assert _coerce("bool", "false") is False
    assert _coerce("bool", "true") is True
    assert _coerce("bool", True) is True


@pytest.mark.asyncio
async def test_edit_bool_field_via_string_false_stays_false(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"bf-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-BF1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator,
                               is_prepaid=True))
        await db.commit()

    async with factory() as db:
        await service.edit_record(db, "pr", pr_id, {"is_prepaid": "false"},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert pr.is_prepaid is False


@pytest.mark.asyncio
async def test_edit_line_items_blank_qty_rejected_cleanly(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"bq-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-BQ1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.commit()

    async with factory() as db:
        with pytest.raises(ValueError, match="quantity and unit price"):
            await service.edit_record(db, "pr", pr_id,
                                      {"line_items": [{"description": "x", "qty": "",
                                                       "unit": "ea", "unit_price": ""}]},
                                      actor_id=creator, actor_email="admin@x.com")

    # blank sort_order but valid qty/unit_price succeeds
    async with factory() as db:
        await service.edit_record(db, "pr", pr_id,
                                  {"line_items": [{"description": "y", "qty": "1",
                                                   "unit": "ea", "unit_price": "5",
                                                   "sort_order": ""}]},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert pr.amount == Decimal("5.00")


@pytest.mark.asyncio
async def test_pa_source_requester_edits_source_pr_creator(test_engine, monkeypatch):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.models.po import PurchaseOrder
    from app.models.pa import PaymentApplication
    from app.models.vendor import Vendor
    from app.admin import service
    from app.services import approval_client

    calls = []
    async def _fake(doc_type, doc_id, bearer_token):
        calls.append((doc_type, str(doc_id))); return {}
    monkeypatch.setattr(approval_client, "resync_document", _fake)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    old_c = uuid.uuid4(); new_c = uuid.uuid4(); vid = uuid.uuid4()
    pr_id = uuid.uuid4(); po_id = uuid.uuid4(); pa_id = uuid.uuid4()
    async with factory() as db:
        for u in (old_c, new_c):
            db.add(User(id=u, email=f"{u.hex[:6]}@x.com", hashed_password="x",
                        full_name="U", role="requester"))
        db.add(Vendor(id=vid, code="V", name="V", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-PA1", title="t", type=1, status="approved",
                               currency="CAD", amount=Decimal("0"), created_by=old_c))
        await db.flush()
        db.add(PurchaseOrder(id=po_id, number="PO-PA1", title="t", type=1, status="approved",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=vid, vendor_name="V", pr_id=pr_id, created_by=old_c))
        await db.flush()
        db.add(PaymentApplication(id=pa_id, pa_number="PA-1", title="t", po_id=po_id, po_number="PO-PA1",
                                  vendor_id=vid, vendor_name="V", pa_type="regular",
                                  subtotal=Decimal("0"), tax_amount=Decimal("0"),
                                  payment_amount=Decimal("0"), currency="CAD",
                                  status="in_review", created_by=old_c))
        await db.commit()

    async with factory() as db:
        result = await service.edit_record(db, "pa", pa_id, {"source_requester_id": str(new_c)},
                                           actor_id=old_c, actor_email="admin@x.com", bearer_token="t")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert str(pr.created_by) == str(new_c)         # source PR creator updated
    # resync fires for the PA and/or PR chain
    assert result.get("_routing_requester_changed") is True


# ── reference_list: the repair path for a PA that links no invoice ────────────
# An approved/paid PA cannot be edited anywhere else (pa.py::update_pa accepts
# draft/returned only), and its invoice_ids array is what finance-api's payment
# executor, the remittance advice and the create-PA "already claimed" lock all
# read. Data Maintenance is therefore the only place a wrong or empty link can
# be repaired — these cover that it works, and that it refuses the two repairs
# that would be worse than the damage they replace.


async def _rl_setup(db, *, creator, vid, po_id, tag):
    """Invoices and PAs sit on a real FK chain — users, business_partners and
    purchase_orders must all exist first, or the insert fails long before any
    assertion runs."""
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.po import PurchaseOrder
    db.add(User(id=creator, email=f"{tag}-{creator.hex[:6]}@x.com", hashed_password="x",
                full_name="C", role="requester"))
    db.add(Vendor(id=vid, code=f"V-{tag}-{vid.hex[:5]}", name="RL Vendor",
                  category="supplier", contact_name="R", contact_email="r@x.com"))
    await db.flush()
    db.add(PurchaseOrder(id=po_id, number=f"PO-RL-{po_id.hex[:6]}", title="t", type=1,
                         status="approved", currency="CAD", subtotal=Decimal("0"),
                         tax_rate=Decimal("0"), tax_amount=Decimal("0"), total=Decimal("0"),
                         vendor_id=vid, vendor_name="RL Vendor", created_by=creator))
    await db.flush()


def _rl_invoice(inv_id, po_id, vid, creator, ref):
    from datetime import date as _date
    from app.models.invoice import Invoice
    return Invoice(
        id=inv_id, internal_ref=ref, vendor_invoice_number=f"V{ref}",
        vendor_id=vid, vendor_name="RL Vendor", amount=Decimal("100"),
        tax_amount=Decimal("0"), total_amount=Decimal("100"), currency="CAD",
        invoice_date=_date(2026, 1, 1), due_date=_date(2026, 2, 1),
        status="matched", po_id=po_id, po_number="PO-RL", uploaded_by=creator,
    )


def _rl_pa(pa_id, po_id, vid, creator, number, *, status="approved", invoice_ids=None):
    from app.models.pa import PaymentApplication
    return PaymentApplication(
        id=pa_id, pa_number=number, title="t", pa_type="regular", status=status,
        po_id=po_id, po_number="PO-RL", vendor_id=vid, vendor_name="RL Vendor",
        invoice_ids=invoice_ids or [], gr_ids=[],
        subtotal=Decimal("100"), tax_amount=Decimal("0"), payment_amount=Decimal("100"),
        currency="CAD", created_by=creator,
    )


@pytest.mark.asyncio
async def test_edit_reference_list_links_invoices_to_a_paid_pa(test_engine):
    from app.models.pa import PaymentApplication
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator, vid, po_id, pa_id, inv_id = (uuid.uuid4() for _ in range(5))
    async with factory() as db:
        await _rl_setup(db, creator=creator, vid=vid, po_id=po_id, tag="rl1")
        db.add(_rl_invoice(inv_id, po_id, vid, creator, "I-RL-1"))
        db.add(_rl_pa(pa_id, po_id, vid, creator, "PA-RL1", status="processed"))
        await db.commit()

    async with factory() as db:
        out = await service.edit_record(db, "pa", pa_id, {"invoice_ids": [str(inv_id)]},
                                        actor_id=creator, actor_email="admin@x.com")
        await db.commit()

    # Stored as STRING uuids — every consumer does uuid.UUID(str(i)) over them.
    async with factory() as db:
        pa = (await db.execute(select(PaymentApplication).where(PaymentApplication.id == pa_id))).scalar_one()
        assert pa.invoice_ids == [str(inv_id)]
    # A PAID PA must signal that finance's writeback needs replaying; the caller
    # fires it AFTER commit, since finance-api reads on its own connection.
    assert out["_invoice_links_changed"] is True


@pytest.mark.asyncio
async def test_edit_reference_list_on_unpaid_pa_does_not_ask_for_replay(test_engine):
    """An approved-but-unpaid PA needs no replay — its own payment will do the
    writeback later, over the corrected array."""
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator, vid, po_id, pa_id, inv_id = (uuid.uuid4() for _ in range(5))
    async with factory() as db:
        await _rl_setup(db, creator=creator, vid=vid, po_id=po_id, tag="rl2")
        db.add(_rl_invoice(inv_id, po_id, vid, creator, "I-RL-2"))
        db.add(_rl_pa(pa_id, po_id, vid, creator, "PA-RL2"))
        await db.commit()
    async with factory() as db:
        out = await service.edit_record(db, "pa", pa_id, {"invoice_ids": [str(inv_id)]},
                                        actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    assert out["_invoice_links_changed"] is False


@pytest.mark.asyncio
async def test_edit_reference_list_rejects_invoice_from_another_po(test_engine):
    from app.models.po import PurchaseOrder
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator, vid, po_id, other_po, pa_id, inv_id = (uuid.uuid4() for _ in range(6))
    async with factory() as db:
        await _rl_setup(db, creator=creator, vid=vid, po_id=po_id, tag="rl3")
        db.add(PurchaseOrder(id=other_po, number=f"PO-OTHER-{other_po.hex[:6]}", title="t",
                             type=1, status="approved", currency="CAD", subtotal=Decimal("0"),
                             tax_rate=Decimal("0"), tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=vid, vendor_name="RL Vendor", created_by=creator))
        await db.flush()
        db.add(_rl_invoice(inv_id, other_po, vid, creator, "I-RL-3"))
        db.add(_rl_pa(pa_id, po_id, vid, creator, "PA-RL3"))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError, match="different PO"):
            await service.edit_record(db, "pa", pa_id, {"invoice_ids": [str(inv_id)]},
                                      actor_id=creator, actor_email="admin@x.com")


@pytest.mark.asyncio
async def test_edit_reference_list_rejects_invoice_claimed_by_another_pa(test_engine):
    """Two live PAs pointing at one invoice is how it gets paid twice."""
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator, vid, po_id, pa_id, other_pa, inv_id = (uuid.uuid4() for _ in range(6))
    async with factory() as db:
        await _rl_setup(db, creator=creator, vid=vid, po_id=po_id, tag="rl4")
        db.add(_rl_invoice(inv_id, po_id, vid, creator, "I-RL-4"))
        db.add(_rl_pa(other_pa, po_id, vid, creator, "PA-RL4-OWNER", invoice_ids=[str(inv_id)]))
        db.add(_rl_pa(pa_id, po_id, vid, creator, "PA-RL4"))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError, match="already linked"):
            await service.edit_record(db, "pa", pa_id, {"invoice_ids": [str(inv_id)]},
                                      actor_id=creator, actor_email="admin@x.com")


@pytest.mark.asyncio
async def test_edit_reference_list_rejects_unknown_id(test_engine):
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator, vid, po_id, pa_id = (uuid.uuid4() for _ in range(4))
    async with factory() as db:
        await _rl_setup(db, creator=creator, vid=vid, po_id=po_id, tag="rl5")
        db.add(_rl_pa(pa_id, po_id, vid, creator, "PA-RL5"))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError, match="not found"):
            await service.edit_record(db, "pa", pa_id, {"invoice_ids": [str(uuid.uuid4())]},
                                      actor_id=creator, actor_email="admin@x.com")


@pytest.mark.asyncio
async def test_get_record_labels_reference_list_including_unresolvable_ids(test_engine):
    """An id the resolver cannot find is labelled, never dropped: an OA Direct
    PA's invoice_ids hold expense_invoices ids, which `invoices` will never match
    by design. Dropping them would let a Save quietly delete a link other
    services still read."""
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator, vid, po_id, pa_id, inv_id, stranger = (uuid.uuid4() for _ in range(6))
    async with factory() as db:
        await _rl_setup(db, creator=creator, vid=vid, po_id=po_id, tag="rl6")
        db.add(_rl_invoice(inv_id, po_id, vid, creator, "I-RL-6"))
        db.add(_rl_pa(pa_id, po_id, vid, creator, "PA-RL6",
                      invoice_ids=[str(inv_id), str(stranger)]))
        await db.commit()
    async with factory() as db:
        rec = await service.get_record(db, "pa", pa_id)
    labels = rec["_ref_list_labels"]["invoice_ids"]
    assert [l["id"] for l in labels] == [str(inv_id), str(stranger)]
    assert "I-RL-6" in labels[0]["label"]
    assert "not found" in labels[1]["label"]
