"""Paying a PA flips the linked ap_invoices to paid (Plan 4)."""
import uuid
from datetime import date

import pytest


def _ap_payload(**over):
    p = dict(source_ref="INV-1", vendor_id=uuid.uuid4(), vendor_name="V",
             vendor_invoice_number="X", amount="1000.00", tax_amount="0.00",
             total_amount="1000.00", currency="CAD",
             invoice_date=date(2026, 6, 17), due_date=date(2026, 7, 17),
             status="posted", source_status="matched", po_id=None, po_number=None)
    p.update(over)
    return p


def _req(pa, **over):
    """Mirror PaymentExecuteRequest shape used by app.crud.payment_execute.execute."""
    class _Req:
        doc_kind = "pa"
        doc_id = pa.id
        payment_method = "bank_transfer"
        amount_paid = None
        reference = None
        notes = None
        payment_date = None
    r = _Req()
    for k, v in over.items():
        setattr(r, k, v)
    return r


@pytest.mark.anyio
async def test_pa_payment_flips_ap_invoice_paid(db_session):
    from app.crud import ap_invoice as ap_crud
    from app.crud.payment_execute import execute
    from app.models.pa import PaymentApplication

    src_id = uuid.uuid4()
    ap = await ap_crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                              payload=_ap_payload(), tax_lines=[])
    pa = PaymentApplication(
        pa_number="PA-WB-1", title="t", pa_type="regular", status="approved",
        vendor_id=uuid.uuid4(), vendor_name="V", po_id=uuid.uuid4(),
        payment_amount=1000, currency="CAD", invoice_ids=[str(src_id)],
        created_by=uuid.uuid4(),
    )
    db_session.add(pa)
    await db_session.commit()

    user = {"sub": str(uuid.uuid4()), "role": "finance_manager"}
    await execute(db_session, _req(pa), user)
    await db_session.commit()

    refreshed = await ap_crud.get(db_session, ap.id)
    assert refreshed.status == "paid"
    assert str(refreshed.paid_amount) == "1000.00"


@pytest.mark.anyio
async def test_pa_dir_payment_flips_ap_invoice_paid(db_session):
    """OA Direct PA (po_id is None) must also write back to ap_invoices."""
    from app.crud import ap_invoice as ap_crud
    from app.crud.payment_execute import execute
    from app.models.pa import PaymentApplication

    src_id = uuid.uuid4()
    ap = await ap_crud.upsert(db_session, source="oa", source_invoice_id=src_id,
                              payload=_ap_payload(), tax_lines=[])
    pa = PaymentApplication(
        pa_number="PA-WB-DIR-1", title="t", pa_type="regular", status="approved",
        vendor_id=uuid.uuid4(), vendor_name="V", po_id=None,
        payment_amount=1000, currency="CAD", invoice_ids=[str(src_id)],
        created_by=uuid.uuid4(),
    )
    db_session.add(pa)
    await db_session.commit()

    user = {"sub": str(uuid.uuid4()), "role": "finance_manager"}
    await execute(db_session, _req(pa, doc_kind="pa_dir"), user)
    await db_session.commit()

    refreshed = await ap_crud.get(db_session, ap.id)
    assert refreshed.status == "paid"
    assert str(refreshed.paid_amount) == "1000.00"
