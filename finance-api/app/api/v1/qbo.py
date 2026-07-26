"""QuickBooks Online mirror API — sync trigger/status + browse. Auth via CurrentUser."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.qbo import (
    RUNNING, QboAccount, QboAttachmentLink, QboBill, QboBillLine,
    QboBillPayment, QboBillPaymentLine, QboCreditMemo, QboCreditMemoLine,
    QboDeposit, QboDepositLine, QboInvoice, QboInvoiceLine, QboJournalEntry,
    QboJournalEntryLine, QboPayment, QboPaymentLine, QboPurchase,
    QboPurchaseLine, QboSyncRun, QboTransfer, QboVendor, QboVendorCredit,
    QboVendorCreditLine,
)
from app.services import qbo_sync as svc

# Server-side auth only (CurrentUser); view_finance is the client-side nav gate.
router = APIRouter(prefix="/qbo", tags=["qbo"])


class SyncIn(BaseModel):
    mode: Literal["full", "incremental"]
    entities: list[str] | None = None
    with_attachments: bool = True
    confirm: str | None = None


def _run_out(r: QboSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "mode": r.mode, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "counters": r.counters, "watermarks": r.watermarks, "error": r.error,
    }


@router.get("/sync/status")
async def sync_status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await db.execute(text(
        "update qbo_sync_runs set status='failed', error='abandoned', "
        "finished_at=now(), updated_at=now() "
        "where status='running' and updated_at < :cutoff"),
        {"cutoff": datetime.now(timezone.utc) - svc.STALE_AFTER})
    await db.commit()
    current = (await db.execute(select(QboSyncRun).where(QboSyncRun.status == RUNNING)
               .order_by(QboSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(select(QboSyncRun).where(QboSyncRun.status != RUNNING)
            .order_by(QboSyncRun.started_at.desc()).limit(1))).scalars().first()
    return {"can_sync": True, "configured": svc.qbo_configured(),
            "current_run": _run_out(current), "last_run": _run_out(last)}


@router.get("/sync/runs")
async def sync_runs(user: CurrentUser, db: AsyncSession = Depends(get_db), limit: int = 20):
    rows = (await db.execute(select(QboSyncRun)
            .order_by(QboSyncRun.started_at.desc()).limit(min(limit, 100)))).scalars().all()
    return {"items": [_run_out(r) for r in rows]}


@router.post("/sync", status_code=202)
async def trigger_sync(body: SyncIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    if not svc.qbo_configured():
        raise HTTPException(status_code=503, detail="QBO connection is not configured")
    if body.mode == "full" and body.confirm != svc.FULL_CONFIRM:
        raise HTTPException(status_code=422, detail=f'full reload requires confirm="{svc.FULL_CONFIRM}"')
    running = (await db.execute(select(QboSyncRun).where(QboSyncRun.status == RUNNING).limit(1))).scalars().first()
    if running:
        raise HTTPException(status_code=409, detail="a sync is already running")
    svc.launch_sync(mode=body.mode, entities=body.entities, with_attachments=body.with_attachments)
    return {"status": "started"}


# ── entity browse + detail ──────────────────────────────────────────────
# NOTE: FastAPI matches the fixed /sync/* paths above before falling through
# to /{entity} regardless of definition order, and _resolve() 404s unknown
# entities — the routes are simply ordered this way for readability.

_ENTITIES = {
    "vendors": (QboVendor, None), "accounts": (QboAccount, None),
    "bills": (QboBill, QboBillLine), "bill-payments": (QboBillPayment, QboBillPaymentLine),
    "vendor-credits": (QboVendorCredit, QboVendorCreditLine),
    "purchases": (QboPurchase, QboPurchaseLine), "invoices": (QboInvoice, QboInvoiceLine),
    "payments": (QboPayment, QboPaymentLine), "credit-memos": (QboCreditMemo, QboCreditMemoLine),
    "deposits": (QboDeposit, QboDepositLine), "transfers": (QboTransfer, None),
    "journal-entries": (QboJournalEntry, QboJournalEntryLine),
}


def _row_dict(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name)
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, datetime):
            v = v.isoformat()
        out[col.name] = v
    return out


def _resolve(entity: str):
    if entity not in _ENTITIES:
        raise HTTPException(status_code=404, detail=f"unknown entity: {entity}")
    return _ENTITIES[entity]


@router.get("/{entity}")
async def browse(entity: str, user: CurrentUser, db: AsyncSession = Depends(get_db),
                 page: int = 1, page_size: int = 50,
                 date_from: str | None = None, date_to: str | None = None, q: str | None = None):
    model, _ = _resolve(entity)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conds = []
    if hasattr(model, "deleted_at"):
        conds.append(model.deleted_at.is_(None))
    if date_from and hasattr(model, "txn_date"):
        conds.append(model.txn_date >= date_from)
    if date_to and hasattr(model, "txn_date"):
        conds.append(model.txn_date <= date_to)
    if q:
        like = f"%{q}%"
        ors = []
        for attr in ("counterparty_name", "doc_number", "display_name", "name"):
            if hasattr(model, attr):
                ors.append(getattr(model, attr).ilike(like))
        if ors:
            conds.append(or_(*ors))
    total = (await db.execute(select(func.count()).select_from(model).where(*conds))).scalar()
    order = model.txn_date.desc() if hasattr(model, "txn_date") else model.qbo_id
    rows = (await db.execute(select(model).where(*conds).order_by(order)
            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [_row_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/{entity}/{qbo_id}")
async def detail(entity: str, qbo_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    model, line_model = _resolve(entity)
    header = (await db.execute(select(model).where(model.qbo_id == qbo_id))).scalars().first()
    if not header:
        raise HTTPException(status_code=404, detail="not found")
    lines = []
    if line_model is not None:
        lrows = (await db.execute(select(line_model).where(line_model.parent_qbo_id == qbo_id)
                 .order_by(line_model.id))).scalars().all()
        lines = [_row_dict(r) for r in lrows]
    att = (await db.execute(select(QboAttachmentLink).where(QboAttachmentLink.txn_id == qbo_id))).scalars().all()
    return {"header": _row_dict(header), "lines": lines,
            "attachments": [{"attachment_qbo_id": a.attachment_qbo_id, "txn_type": a.txn_type} for a in att]}
