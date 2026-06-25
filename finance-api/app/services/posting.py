"""emit_event() — write one posting event + lines inside the caller's transaction.

Same contract as the copies in approval-api / expense-api (Phase 0-B1).
Idempotent on (source_doc_type, source_doc_id, event_type) via ON CONFLICT
DO NOTHING. Lines with debit == credit == 0 are silently dropped.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posting import PostingEvent, PostingLine, PostingLineDimension

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

    occurred = occurred_at or datetime.now(timezone.utc)
    stmt = (
        pg_insert(PostingEvent)
        .values(
            id=uuid.uuid4(),
            source_service=source_service,
            source_doc_type=source_doc_type,
            source_doc_id=source_doc_id,
            source_doc_number=source_doc_number,
            event_type=event_type,
            occurred_at=occurred,
            fiscal_period=occurred.astimezone(timezone.utc).strftime("%Y-%m"),
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

    line_objs: list[tuple[PostingLine, dict]] = []
    for i, ln in enumerate(effective):
        obj = PostingLine(
            event_id=event_id,
            line_no=i + 1,
            line_role=ln["line_role"],
            account_code=ln.get("account_code"),
            cost_center_id=ln.get("cost_center_id"),
            department_id=ln.get("department_id"),
            partner_id=ln.get("partner_id"),
            partner_name=ln.get("partner_name"),
            debit=Decimal(str(ln.get("debit", 0))),
            credit=Decimal(str(ln.get("credit", 0))),
            tax_code=ln.get("tax_code"),
            currency=ln.get("currency", "CAD"),
            fx_rate=Decimal(str(ln.get("fx_rate", 1))),
            memo=ln.get("memo"),
            item_id=ln.get("item_id"),
            lot_id=ln.get("lot_id"),
            warehouse_id=ln.get("warehouse_id"),
            project_id=ln.get("project_id"),
            channel=ln.get("channel"),
            entity_id=ln.get("entity_id"),
        )
        db.add(obj)
        line_objs.append((obj, ln))
    await db.flush()  # populate line ids

    # long-tail aux dimensions (A1.7): ln["aux"] = {dim_code: value_id|str|{...}}
    for obj, ln in line_objs:
        for dim_code, val in (ln.get("aux") or {}).items():
            vid = vtext = None
            if isinstance(val, dict):
                vid, vtext = val.get("value_id"), val.get("value_text")
            elif isinstance(val, uuid.UUID):
                vid = val
            else:
                vtext = str(val)
            db.add(PostingLineDimension(
                posting_line_id=obj.id, dim_code=dim_code,
                value_id=vid, value_text=vtext,
            ))
    await db.flush()
    return event_id
