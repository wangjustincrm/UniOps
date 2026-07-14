"""AP Invoice module tests (finance-owned AP invoices)."""
import uuid
from datetime import date

import pytest


def test_ap_invoice_model_mapped():
    from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine
    cols = {c.name for c in ApInvoice.__table__.columns}
    assert {"ap_invoice_number", "source", "source_invoice_id", "source_ref",
            "vendor_id", "vendor_name", "vendor_invoice_number",
            "amount", "tax_amount", "total_amount", "paid_amount",
            "currency", "invoice_date", "due_date", "status", "source_status",
            "po_id", "po_number", "posted_at", "entity_id"} <= cols
    assert ApInvoice.__tablename__ == "ap_invoices"
    tcols = {c.name for c in ApInvoiceTaxLine.__table__.columns}
    assert {"invoice_id", "line_no", "tax_code", "taxable_base",
            "tax_amount", "recoverable"} <= tcols


def _payload(**over):
    p = dict(
        source_ref="INV-2026-0001", vendor_id=uuid.uuid4(), vendor_name="ULINE",
        vendor_invoice_number="V-123", amount="1000.00", tax_amount="130.00",
        total_amount="1130.00", currency="CAD",
        invoice_date=date(2026, 6, 17), due_date=date(2026, 7, 17),
        status="draft", source_status="unmatched", po_id=None, po_number=None,
    )
    p.update(over)
    return p


@pytest.mark.anyio
async def test_upsert_creates_then_updates_idempotent(db_session):
    from app.crud import ap_invoice as crud
    src_id = uuid.uuid4()
    inv1 = await crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                             payload=_payload(), tax_lines=[
                                 {"line_no": 1, "tax_code": "HST_ON", "taxable_base": "1000.00",
                                  "tax_amount": "130.00", "recoverable": True}])
    await db_session.commit()
    assert inv1.ap_invoice_number.startswith("AP-")
    assert inv1.status == "draft"
    assert str(inv1.total_amount) == "1130.00"

    inv2 = await crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                             payload=_payload(status="posted", source_status="matched"),
                             tax_lines=[])
    await db_session.commit()
    assert inv2.id == inv1.id
    assert inv2.ap_invoice_number == inv1.ap_invoice_number
    assert inv2.status == "posted"

    total, rows = await crud.list_invoices(db_session, source="epms")
    assert len([r for r in rows if r.source_invoice_id == src_id]) == 1


@pytest.mark.anyio
async def test_get_and_void(db_session):
    from app.crud import ap_invoice as crud
    inv = await crud.upsert(db_session, source="oa", source_invoice_id=uuid.uuid4(),
                            payload=_payload(), tax_lines=[])
    await db_session.commit()
    got = await crud.get(db_session, inv.id)
    assert got is not None and got.id == inv.id
    voided = await crud.set_void(db_session, inv.id)
    await db_session.commit()
    assert voided.status == "void"


@pytest.mark.anyio
async def test_post_invoice_accrual_reads_ap_invoice(db_session):
    from app.crud import ap_invoice as crud
    from app.crud.ap_accrual import post_invoice_accrual
    src_id = uuid.uuid4()
    inv = await crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                            payload=_payload(status="posted", source_status="matched"),
                            tax_lines=[{"line_no": 1, "tax_code": "HST_ON",
                                        "taxable_base": "1000.00", "tax_amount": "130.00",
                                        "recoverable": True}])
    await db_session.commit()
    res = await post_invoice_accrual(db_session, inv.id)
    await db_session.commit()
    assert res["invoice_id"] == inv.id
    assert res["posting_event_id"] is not None       # first post emits
    res2 = await post_invoice_accrual(db_session, inv.id)
    await db_session.commit()
    assert res2["already_accrued"] is True
