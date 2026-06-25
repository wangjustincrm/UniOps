"""emit_event() — write one posting event + lines inside the caller's transaction.

Idempotent on (source_doc_type, source_doc_id, event_type) via ON CONFLICT
DO NOTHING. Lines with debit == credit == 0 are silently dropped.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posting import PostingEvent, PostingLine

_ZERO = Decimal("0")


async def emit_event(
    db: AsyncSession,
    *,
    source_service: str,
    source_doc_type: str,
    source_doc_id: uuid.UUID,
    source_doc_number: str,
    event_type: str,
    lines: list[dict],
    occurred_at: datetime | None = None,
) -> uuid.UUID | None:
    effective = [
        ln for ln in lines
        if Decimal(str(ln.get("debit", 0))) != _ZERO or Decimal(str(ln.get("credit", 0))) != _ZERO
    ]
    if not effective:
        return None

    stmt = (
        pg_insert(PostingEvent)
        .values(
            id=uuid.uuid4(),
            source_service=source_service,
            source_doc_type=source_doc_type,
            source_doc_id=source_doc_id,
            source_doc_number=source_doc_number,
            event_type=event_type,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            status="pending",
        )
        .on_conflict_do_nothing(
            index_elements=["source_doc_type", "source_doc_id", "event_type"]
        )
        .returning(PostingEvent.id)
    )
    event_id = (await db.execute(stmt)).scalar_one_or_none()
    if event_id is None:
        return None  # already emitted — idempotent skip

    db.add_all([
        PostingLine(
            event_id=event_id,
            line_no=i + 1,
            line_role=ln["line_role"],
            account_code=ln.get("account_code"),
            cost_center_id=ln.get("cost_center_id"),
            partner_id=ln.get("partner_id"),
            partner_name=ln.get("partner_name"),
            debit=Decimal(str(ln.get("debit", 0))),
            credit=Decimal(str(ln.get("credit", 0))),
            tax_code=ln.get("tax_code"),
            currency=ln.get("currency", "CAD"),
            fx_rate=Decimal(str(ln.get("fx_rate", 1))),
            memo=ln.get("memo"),
        )
        for i, ln in enumerate(effective)
    ])
    await db.flush()
    return event_id
