"""Cross-service write endpoints — commit / release / actualize / book-expense.

All writes are idempotent via budget_ledger's unique constraint on
(source_service, source_doc_type, source_doc_id, operation).
"""
from fastapi import APIRouter

from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import ledger as ledger_crud
from app.crud import plan as plan_crud
from app.schemas.ledger import BookExpenseRequest, CommitRequest, LedgerWriteResponse

router = APIRouter(tags=["crossservice"])


@router.get("/plan-lines")
async def plan_lines(fiscal_year: int, month: int, db: SessionDep,
                     user: CurrentUserPayload):  # noqa: ARG001 — auth gate only
    """Current approved plan lines for a (fiscal_year, month), all cost centers.
    Read-only, consumed by finance's predreal grid for the budget column.
    Requires a valid JWT (forwarded by finance-api)."""
    rows = await plan_crud.current_plan_lines(db, fiscal_year, month)
    return [{"cost_center_id": str(cc), "account_id": str(a), "amount": str(amt)}
            for cc, a, amt in rows]


def _source_service_from_role(role: str) -> str:
    """Map caller role to source_service tag for the ledger."""
    if role in ("ap_clerk", "finance_manager", "finance_bp"):
        return "finance"
    if role in ("requester", "dept_manager", "system_admin"):
        return "epms"  # default for hand-rolled testing
    return role or "unknown"


@router.post("/commit", response_model=LedgerWriteResponse)
async def commit(
    payload: CommitRequest, db: SessionDep, user: CurrentUserPayload,
):
    source = _source_service_from_role(user.get("role", ""))
    return await ledger_crud.write_commit_or_similar(db, source, "commit", payload)


@router.post("/release", response_model=LedgerWriteResponse)
async def release(
    payload: CommitRequest, db: SessionDep, user: CurrentUserPayload,
):
    source = _source_service_from_role(user.get("role", ""))
    return await ledger_crud.write_commit_or_similar(db, source, "release", payload)


@router.post("/actualize", response_model=LedgerWriteResponse)
async def actualize(
    payload: CommitRequest, db: SessionDep, user: CurrentUserPayload,
):
    source = _source_service_from_role(user.get("role", ""))
    return await ledger_crud.write_commit_or_similar(db, source, "actualize", payload)


@router.post("/book-expense", response_model=LedgerWriteResponse)
async def book_expense(
    payload: BookExpenseRequest, db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
):
    # book-expense always comes from expense-api regardless of caller role
    return await ledger_crud.write_book_expense(db, "expense", payload)
