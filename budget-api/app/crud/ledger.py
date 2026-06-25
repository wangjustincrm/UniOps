"""Ledger writes (commit / release / actualize / book_expense) with idempotency.

The ledger table doubles as:
  1. Idempotency guarantee (unique on source_service/doc_type/doc_id/operation)
  2. Source of truth for actual_spent and committed (aggregated by SUM)

See DESIGN §4.1 and §4.4.
"""
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import BudgetLedger
from app.schemas.ledger import BookExpenseRequest, CommitRequest, LedgerWriteResponse


async def _write_ledger_row(
    db: AsyncSession,
    source_service: str,
    source_doc_type: str,
    source_doc_id: uuid.UUID,
    operation: str,
    cost_center_id: uuid.UUID,
    account_id: uuid.UUID,
    fiscal_year: int,
    month: int,
    amount: Decimal,
    notes: str | None = None,
) -> tuple[uuid.UUID | None, bool]:
    """Insert one ledger row idempotently. Returns (row_id, was_new)."""
    existing_q = select(BudgetLedger).where(
        BudgetLedger.source_service == source_service,
        BudgetLedger.source_doc_type == source_doc_type,
        BudgetLedger.source_doc_id == source_doc_id,
        BudgetLedger.operation == operation,
    )
    existing = (await db.execute(existing_q)).scalar_one_or_none()
    if existing:
        return existing.id, False

    row = BudgetLedger(
        source_service=source_service,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        operation=operation,
        cost_center_id=cost_center_id,
        account_id=account_id,
        fiscal_year=fiscal_year,
        month=month,
        amount=amount,
        notes=notes,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row.id, True


async def write_commit_or_similar(
    db: AsyncSession, source_service: str, operation: str, payload: CommitRequest,
) -> LedgerWriteResponse:
    """Used for commit / release / actualize — single line per call."""
    row_id, new = await _write_ledger_row(
        db,
        source_service=source_service,
        source_doc_type=payload.source_doc_type,
        source_doc_id=payload.source_doc_id,
        operation=operation,
        cost_center_id=payload.cost_center_id,
        account_id=payload.account_id,
        fiscal_year=payload.fiscal_year,
        month=payload.month,
        amount=payload.amount,
        notes=payload.notes,
    )
    return LedgerWriteResponse(
        idempotent=not new, inserted=1 if new else 0,
        ledger_ids=[row_id] if row_id else [],
    )


async def write_book_expense(
    db: AsyncSession, source_service: str, payload: BookExpenseRequest,
) -> LedgerWriteResponse:
    """Multiple lines per call. Idempotency keys by source_doc_id, but if multiple
    lines come from the same source_doc_id we suffix with a stable index."""
    inserted = 0
    ledger_ids: list[uuid.UUID] = []
    any_new = False

    # We accept multiple lines per book-expense; encode line index into source_doc_id partition
    # via the unique constraint by varying source_doc_type. To keep it simple and idempotent
    # we treat the same source_doc_id as a single atomic call: skip-all-if-any-exists.
    existing_q = select(BudgetLedger).where(
        BudgetLedger.source_service == source_service,
        BudgetLedger.source_doc_type == payload.source_doc_type,
        BudgetLedger.source_doc_id == payload.source_doc_id,
        BudgetLedger.operation == "book_expense",
    )
    existing = list((await db.execute(existing_q)).scalars().all())
    if existing:
        return LedgerWriteResponse(
            idempotent=True, inserted=0, ledger_ids=[r.id for r in existing],
        )

    for idx, line in enumerate(payload.lines):
        # Subtype the doc_type so multiple lines from the same doc don't collide on unique constraint
        sub_type = f"{payload.source_doc_type}:line{idx}"
        row_id, new = await _write_ledger_row(
            db,
            source_service=source_service,
            source_doc_type=sub_type,
            source_doc_id=payload.source_doc_id,
            operation="book_expense",
            cost_center_id=line.cost_center_id,
            account_id=line.account_id,
            fiscal_year=line.fiscal_year,
            month=line.month,
            amount=line.amount,
            notes=payload.notes,
        )
        if new:
            inserted += 1
            any_new = True
        if row_id:
            ledger_ids.append(row_id)

    return LedgerWriteResponse(idempotent=not any_new, inserted=inserted, ledger_ids=ledger_ids)


# ── Aggregation queries ───────────────────────────────────────────────────────

async def get_committed(
    db: AsyncSession,
    cost_center_id: uuid.UUID,
    account_id: uuid.UUID,
    fiscal_year: int,
    month: int | None = None,
) -> Decimal:
    """committed = SUM(commit) − SUM(release) − SUM(actualize)."""
    q = select(BudgetLedger).where(
        BudgetLedger.cost_center_id == cost_center_id,
        BudgetLedger.account_id == account_id,
        BudgetLedger.fiscal_year == fiscal_year,
    )
    if month is not None:
        q = q.where(BudgetLedger.month == month)
    rows = (await db.execute(q)).scalars().all()
    total = Decimal("0")
    for r in rows:
        if r.operation == "commit":
            total += r.amount
        elif r.operation in ("release", "actualize"):
            total -= r.amount
    if total < 0:
        total = Decimal("0")
    return total


async def get_actual_spent(
    db: AsyncSession,
    cost_center_id: uuid.UUID,
    account_id: uuid.UUID,
    fiscal_year: int,
    month: int | None = None,
) -> Decimal:
    """actual_spent = SUM(actualize) + SUM(book_expense) + SUM(opening)."""
    q = select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
        BudgetLedger.cost_center_id == cost_center_id,
        BudgetLedger.account_id == account_id,
        BudgetLedger.fiscal_year == fiscal_year,
        BudgetLedger.operation.in_(["actualize", "book_expense", "opening"]),
    )
    if month is not None:
        q = q.where(BudgetLedger.month == month)
    result = await db.execute(q)
    return Decimal(str(result.scalar_one()))
