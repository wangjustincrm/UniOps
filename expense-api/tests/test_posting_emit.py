"""emit_event helper + process_pay emission tests."""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.posting_mirror import PostingEvent, PostingLine
from app.services.posting import emit_event


async def test_emit_event_writes_event_and_lines(db_session):
    doc_id = uuid.uuid4()
    ev_id = await emit_event(
        db_session,
        source_service="oa", source_doc_type="exp", source_doc_id=doc_id,
        source_doc_number="EXP-2026-0001", event_type="expense_paid",
        lines=[
            {"line_role": "employee_expense", "debit": Decimal("90.00"),
             "partner_name": "Jane Doe"},
            {"line_role": "sales_tax", "debit": Decimal("0")},      # dropped
            {"line_role": "bank", "credit": Decimal("90.00")},
        ],
    )
    assert ev_id is not None
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev_id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert [ln.line_role for ln in lines] == ["employee_expense", "bank"]


async def test_emit_event_is_idempotent(db_session):
    doc_id = uuid.uuid4()
    kw = dict(
        source_service="oa", source_doc_type="exp", source_doc_id=doc_id,
        source_doc_number="EXP-2026-0002", event_type="expense_paid",
        lines=[{"line_role": "bank", "credit": Decimal("10.00")}],
    )
    first = await emit_event(db_session, **kw)
    second = await emit_event(db_session, **kw)
    assert first is not None
    assert second is None
    count = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == doc_id)
    )).scalars().all()
    assert len(count) == 1


async def test_emit_event_all_zero_lines_skips(db_session):
    ev_id = await emit_event(
        db_session,
        source_service="oa", source_doc_type="exp", source_doc_id=uuid.uuid4(),
        source_doc_number="EXP-2026-0003", event_type="expense_paid",
        lines=[{"line_role": "sales_tax", "debit": Decimal("0")}],
    )
    assert ev_id is None
