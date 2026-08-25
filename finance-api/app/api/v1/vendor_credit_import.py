"""QBO vendor-credit import API — throwaway, delete when QuickBooks is retired.

Both routes are gated on `epms.vendor_credit.manage`, the same key that guards
approving a credit note. That is the correct gate: this writes spendable credit
straight into the ledger `payment_execute` pays out of, bypassing review, so it
must not be reachable by anyone who could not approve the same credit by hand.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.crud import vendor_credit_import as crud
from app.db.base import get_db
from app.schemas.vendor_credit_import import (
    ImportCandidatesResponse, ImportRunRequest, ImportRunResponse,
)

router = APIRouter(prefix="/qbo-credit-import", tags=["qbo-credit-import"])

_MANAGE_KEY = "epms.vendor_credit.manage"


def _actor(user: dict) -> tuple[uuid.UUID, str | None]:
    try:
        uid = uuid.UUID(str(user.get("sub", "")))
    except ValueError:
        raise HTTPException(status_code=401, detail="Token has no usable subject")
    return uid, user.get("full_name") or user.get("email")


@router.get("/candidates", response_model=ImportCandidatesResponse)
async def list_candidates(user: dict = Depends(require_permission(_MANAGE_KEY)),
                          db: AsyncSession = Depends(get_db)):
    return await crud.list_candidates(db)


@router.post("/run", response_model=ImportRunResponse)
async def run_import(body: ImportRunRequest,
                     user: dict = Depends(require_permission(_MANAGE_KEY)),
                     db: AsyncSession = Depends(get_db)):
    uid, name = _actor(user)
    result = await crud.run_import(db, mapping=body.mapping, imported_by=uid,
                                   imported_by_name=name, dry_run=body.dry_run)
    if not body.dry_run:
        await db.commit()
    return result
