"""finance-api entity registry: schema metadata + cascade handlers.

Each entity exposes:
- a render/edit schema (EntitySchema)
- cascade_preview(db, row): counts only, NO mutation
- cascade_delete(db, row): deletes child rows + polymorphic workflow refs. The CALLER commits.

payment_batch is delete-only (allow_edit=False). payment_batch_lines is
ondelete=CASCADE, so db.delete(batch) removes the lines automatically; we count
them first for the summary, then purge any shared tasks by document_id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.cascade import count_shared_refs, purge_shared_refs
from app.admin.fields import EntitySchema, FieldSpec
from app.models.payment_batch import PaymentBatch, PaymentBatchLine

CascadeFn = Callable[[AsyncSession, object], Awaitable[dict[str, int]]]


@dataclass
class EntitySpec:
    schema: EntitySchema
    model: type
    system: str
    cascade_preview: CascadeFn
    cascade_delete: CascadeFn


async def _line_count(db: AsyncSession, batch_id) -> int:
    return int((await db.execute(
        select(func.count()).select_from(PaymentBatchLine)
        .where(PaymentBatchLine.batch_id == batch_id)
    )).scalar_one())


# ── PaymentBatch ────────────────────────────────────────────────────────────────

async def _batch_preview(db: AsyncSession, batch) -> dict[str, int]:
    lines = await _line_count(db, batch.id)
    refs = await count_shared_refs(db, batch.id)
    return {"payment_batches": 1, "payment_batch_lines": lines, **refs}


async def _batch_delete(db: AsyncSession, batch) -> dict[str, int]:
    lines = await _line_count(db, batch.id)
    await db.delete(batch)  # payment_batch_lines cascade via FK ondelete=CASCADE
    refs = await purge_shared_refs(db, batch.id)
    return {"payment_batches": 1, "payment_batch_lines": lines, **refs}


# ── Schemas ───────────────────────────────────────────────────────────────────

_BATCH_SCHEMA = EntitySchema(
    key="payment_batch", label="Payment Batch", number_field="batch_number",
    list_columns=["batch_number", "batch_date", "status", "total", "payment_method", "created_at"],
    search_fields=["batch_number"], order_by="created_at desc",
    allow_edit=False,
    fields=[
        FieldSpec("batch_number", "string", False),
        FieldSpec("batch_date", "date", False),
        FieldSpec("status", "string", False),
        FieldSpec("currency", "string", False),
        FieldSpec("total", "decimal", False),
        FieldSpec("payment_method", "string", False),
        FieldSpec("executed_at", "datetime", False),
        FieldSpec("created_at", "datetime", False),
    ],
)

REGISTRY: dict[str, EntitySpec] = {
    "payment_batch": EntitySpec(_BATCH_SCHEMA, PaymentBatch, "finance", _batch_preview, _batch_delete),
}
