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
    """Build the REAL request model `app.crud.payment_execute.execute` takes.

    This was a hand-rolled `_Req` stub that mirrored the schema's fields. It
    drifted: Phase B added `credit_ids` to PaymentExecuteRequest and to
    execute()'s `pa` branch, the stub never grew the field, and both tests in
    this file started dying on `AttributeError: '_Req' object has no attribute
    'credit_ids'`. A stub standing in for a contract is only as good as the
    person who remembers to update it, so use the contract itself — the next
    required field is then a construction error here, not a mystery
    AttributeError deep inside the executor.
    """
    from app.schemas.payment_execute import PaymentExecuteRequest

    kw = dict(doc_kind="pa", doc_id=pa.id, payment_method="bank_transfer")
    kw.update(over)
    return PaymentExecuteRequest(**kw)


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
