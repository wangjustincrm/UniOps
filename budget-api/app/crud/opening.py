"""期初 (opening balance) import for Budget Actuals.

Opening balances are manual actuals for a (cost_center, account, month) that did
not flow through the normal cross-service ledger writes — e.g. spend that already
occurred before the system went live mid-year. They are stored as `budget_ledger`
rows with operation='opening' and count toward actual_spent (consuming budget).

Import is scoped to one (cost_center_id, fiscal_year): the CSV holds an Account
Code column plus Jan..Dec month columns. Re-importing updates existing values
(idempotent upsert keyed by cost_center/account/year/month), so corrections are
safe to re-upload.
"""
import csv
import io
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.plan import _CSV_MONTH_HEADERS, _parse_decimal_cell
from app.models.catalog import BudgetAccount
from app.models.ledger import BudgetLedger
from app.schemas.opening import OpeningBalanceRow, OpeningImportResult, OpeningListResponse

_OPENING_SOURCE = "manual"
_OPENING_DOC_TYPE = "opening_balance"


async def import_opening_csv(
    db: AsyncSession,
    *, cost_center_id: uuid.UUID, fiscal_year: int, csv_text: str,
) -> OpeningImportResult:
    """Upsert opening-balance ledger rows from a wide CSV (Account Code + Jan..Dec).

    Blank month cells leave the existing value unchanged; an explicit number
    (including 0) sets it. One ledger row per (cc, account, year, month).
    """
    result = OpeningImportResult()

    accts_q = await db.execute(select(BudgetAccount))
    acct_by_code: dict[str, BudgetAccount] = {a.code: a for a in accts_q.scalars().all()}

    # Existing opening rows for this scope, keyed by (account_id, month).
    existing_q = await db.execute(
        select(BudgetLedger).where(
            BudgetLedger.source_service == _OPENING_SOURCE,
            BudgetLedger.source_doc_type == _OPENING_DOC_TYPE,
            BudgetLedger.operation == "opening",
            BudgetLedger.cost_center_id == cost_center_id,
            BudgetLedger.fiscal_year == fiscal_year,
        )
    )
    row_by_key: dict[tuple[uuid.UUID, int], BudgetLedger] = {
        (r.account_id, r.month): r for r in existing_q.scalars().all()
    }

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None or "Account Code" not in reader.fieldnames:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "CSV missing required 'Account Code' column — did you upload the right file?",
        )

    for row_idx, row in enumerate(reader, start=2):  # row 1 is header
        code = (row.get("Account Code") or "").strip()
        if not code:
            continue
        acct = acct_by_code.get(code)
        if acct is None:
            result.errors.append(f"Row {row_idx}: unknown account code '{code}' — skipped")
            continue

        for m_idx, col in enumerate(_CSV_MONTH_HEADERS, start=1):
            try:
                amount = _parse_decimal_cell(row.get(col) or "")
            except ValueError as e:
                result.errors.append(f"Row {row_idx} {col}: {e}")
                continue
            if amount is None:
                continue  # blank → no change

            key = (acct.id, m_idx)
            existing = row_by_key.get(key)
            if existing is None:
                ledger = BudgetLedger(
                    source_service=_OPENING_SOURCE,
                    source_doc_type=_OPENING_DOC_TYPE,
                    source_doc_id=uuid.uuid4(),
                    operation="opening",
                    cost_center_id=cost_center_id,
                    account_id=acct.id,
                    fiscal_year=fiscal_year,
                    month=m_idx,
                    amount=amount,
                    notes="opening balance import",
                )
                db.add(ledger)
                row_by_key[key] = ledger
                result.rows_created += 1
            elif existing.amount != amount:
                existing.amount = amount
                result.rows_updated += 1

    await db.flush()
    return result


async def list_opening_balances(
    db: AsyncSession,
    *, cost_center_id: uuid.UUID | None = None, fiscal_year: int,
    cc_ids: list[uuid.UUID] | None = None,
) -> OpeningListResponse:
    """List imported opening-balance rows for回显 in the config UI."""
    if cc_ids is not None and len(cc_ids) == 0:
        return OpeningListResponse(
            cost_center_id=cost_center_id, fiscal_year=fiscal_year, items=[])
    q = (
        select(BudgetLedger, BudgetAccount)
        .join(BudgetAccount, BudgetLedger.account_id == BudgetAccount.id)
        .where(
            BudgetLedger.source_service == _OPENING_SOURCE,
            BudgetLedger.source_doc_type == _OPENING_DOC_TYPE,
            BudgetLedger.operation == "opening",
            BudgetLedger.fiscal_year == fiscal_year,
        )
        .order_by(BudgetAccount.code, BudgetLedger.month)
    )
    if cost_center_id is not None:
        q = q.where(BudgetLedger.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(BudgetLedger.cost_center_id.in_(cc_ids))

    items = [
        OpeningBalanceRow(
            cost_center_id=led.cost_center_id,
            account_id=led.account_id,
            account_code=acct.code,
            account_name=acct.name,
            fiscal_year=led.fiscal_year,
            month=led.month,
            amount=led.amount,
        )
        for led, acct in (await db.execute(q)).all()
    ]
    return OpeningListResponse(
        cost_center_id=cost_center_id, fiscal_year=fiscal_year, items=items,
    )
