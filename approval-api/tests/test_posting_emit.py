"""emit_event helper tests (same contract as expense-api's copy)."""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.crud.posting import emit_event
from app.models.posting import PostingEvent, PostingLine


async def test_pa_payment_event_shape(db_session):
    doc_id = uuid.uuid4()
    ev_id = await emit_event(
        db_session,
        source_service="epms", source_doc_type="pa", source_doc_id=doc_id,
        source_doc_number="PA-2026-0007", event_type="payment",
        lines=[
            {"line_role": "accounts_payable", "debit": Decimal("1200.50"),
             "partner_id": uuid.uuid4(), "partner_name": "ACME Inc", "currency": "CAD"},
            {"line_role": "bank", "credit": Decimal("1200.50"), "currency": "CAD"},
        ],
    )
    assert ev_id is not None
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev_id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert lines[0].line_role == "accounts_payable"
    assert lines[0].debit == Decimal("1200.50")
    assert lines[1].credit == Decimal("1200.50")


async def test_double_process_emits_once(db_session):
    doc_id = uuid.uuid4()
    kw = dict(
        source_service="epms", source_doc_type="pa", source_doc_id=doc_id,
        source_doc_number="PA-2026-0008", event_type="payment",
        lines=[{"line_role": "bank", "credit": Decimal("5.00")}],
    )
    assert await emit_event(db_session, **kw) is not None
    assert await emit_event(db_session, **kw) is None
    events = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == doc_id)
    )).scalars().all()
    assert len(events) == 1
