"""posting_events / posting_lines schema + constraint tests."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.posting import PostingEvent, PostingLine


def _event(**over) -> PostingEvent:
    kw = dict(
        source_service="epms",
        source_doc_type="pa",
        source_doc_id=uuid.uuid4(),
        source_doc_number="PA-2026-0001",
        event_type="payment",
        occurred_at=datetime.now(timezone.utc),
    )
    kw.update(over)
    return PostingEvent(**kw)


async def test_event_and_lines_roundtrip(db_session):
    ev = _event()
    db_session.add(ev)
    await db_session.flush()
    db_session.add_all([
        PostingLine(event_id=ev.id, line_no=1, line_role="accounts_payable",
                    debit=Decimal("100.00"), partner_name="ACME Inc"),
        PostingLine(event_id=ev.id, line_no=2, line_role="bank",
                    credit=Decimal("100.00")),
    ])
    await db_session.flush()

    rows = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].currency == "CAD"          # server default
    assert rows[0].fx_rate == Decimal("1")    # server default
    assert ev.status == "pending"


async def test_unique_source_constraint(db_session):
    doc_id = uuid.uuid4()
    db_session.add(_event(source_doc_id=doc_id))
    await db_session.flush()
    db_session.add(_event(source_doc_id=doc_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_line_dimension_columns_roundtrip(db_session):
    """B1.7 — FIN-GL-003 dimensions persist and default to NULL."""
    ev = _event()
    db_session.add(ev)
    await db_session.flush()
    dims = dict(item_id=uuid.uuid4(), lot_id=uuid.uuid4(), warehouse_id=uuid.uuid4(),
                project_id=uuid.uuid4(), channel="b2b_export", entity_id=uuid.uuid4())
    db_session.add(PostingLine(event_id=ev.id, line_no=1, line_role="bank",
                               credit=Decimal("1.00"), **dims))
    await db_session.flush()
    row = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id)
    )).scalar_one()
    for k, v in dims.items():
        assert getattr(row, k) == v


async def test_line_cannot_have_both_sides(db_session):
    ev = _event()
    db_session.add(ev)
    await db_session.flush()
    db_session.add(PostingLine(event_id=ev.id, line_no=1, line_role="bank",
                               debit=Decimal("1.00"), credit=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        await db_session.flush()
