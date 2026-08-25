"""QuickBooks Online mirror API — sync trigger/status + browse. Auth via CurrentUser."""
import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.mirrors import BusinessPartner
from app.models.qbo import (
    RUNNING, QboAccount, QboAttachment, QboAttachmentLink, QboBill,
    QboBillLine, QboBillPayment, QboBillPaymentLine, QboCreditMemo,
    QboCreditMemoLine, QboDeposit, QboDepositLine, QboInvoice, QboInvoiceLine,
    QboJournalEntry, QboJournalEntryLine, QboPayment, QboPaymentLine,
    QboPurchase, QboPurchaseLine, QboSyncRun, QboTransfer, QboVendor,
    QboVendorCredit, QboVendorCreditLine,
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


# ── vendor email backfill ───────────────────────────────────────────────
# POST is safe next to the GET-only /{entity} catch-alls — method+path routing
# means this never shadows browse/detail.

@router.post("/vendor-emails/backfill")
async def backfill_vendor_emails(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Copy qbo_vendors.email into EMPTY business_partners.remittance_email,
    matched on lower(trim(name)) == lower(trim(display_name)). Fill-only
    (human-entered values are never overwritten) and idempotent; ambiguous
    names on either side are skipped and reported, never guessed."""
    def norm(s: str | None) -> str:
        return (s or "").strip().lower()

    qbo_rows = (await db.execute(
        select(QboVendor).where(QboVendor.deleted_at.is_(None))
        .order_by(QboVendor.qbo_id))).scalars().all()
    emails_by_key: dict[str, set[str]] = {}
    display_by_key: dict[str, str] = {}   # first-seen trimmed display name
    for v in qbo_rows:
        key = norm(v.display_name)
        email = (v.email or "").strip()
        if not key or not email:
            continue
        display_by_key.setdefault(key, (v.display_name or "").strip())
        emails_by_key.setdefault(key, set()).add(email)

    partners = (await db.execute(
        select(BusinessPartner).where(BusinessPartner.is_supplier.is_(True))
        .order_by(BusinessPartner.code))).scalars().all()
    partners_by_key: dict[str, list[BusinessPartner]] = {}
    for p in partners:
        key = norm(p.name)
        if key:
            partners_by_key.setdefault(key, []).append(p)

    updated: list[dict] = []
    ambiguous: list[dict] = []
    unmatched: list[str] = []
    skipped_has_value = 0
    for key in sorted(emails_by_key):
        emails = emails_by_key[key]
        if len(emails) > 1:
            ambiguous.append({"side": "qbo", "name": display_by_key[key]})
            continue
        matches = partners_by_key.get(key)
        if not matches:
            unmatched.append(display_by_key[key])
            continue
        if len(matches) > 1:
            ambiguous.append({"side": "epms", "name": matches[0].name.strip()})
            continue
        partner = matches[0]
        if (partner.remittance_email or "").strip():
            skipped_has_value += 1
            continue
        partner.remittance_email = next(iter(emails))
        updated.append({"code": partner.code, "name": partner.name,
                        "email": partner.remittance_email})
    await db.commit()
    return {"updated": updated, "skipped_has_value": skipped_has_value,
            "ambiguous": ambiguous, "unmatched_qbo": unmatched}


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


def _filters(model, date_from: str | None, date_to: str | None, q: str | None) -> list:
    """The browse filter set, shared with the CSV export.

    Extracted rather than duplicated so the export cannot drift from what the
    operator is looking at: an export that quietly ignored the search box would
    hand an auditor a different population than the screen showed.
    """
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
    return conds


# Declared BEFORE /{entity}/{qbo_id}: both are two-segment paths, so the
# detail route would otherwise swallow "export" as a qbo_id and 404.
@router.get("/{entity}/export")
async def export_entity(entity: str, user: CurrentUser, db: AsyncSession = Depends(get_db),
                        date_from: str | None = None, date_to: str | None = None,
                        q: str | None = None):
    """CSV of everything the browse table would show, for the same filters.

    QuickBooks is being decommissioned; these mirror tables become the company's
    permanent record of what was in it. Browsing was already possible — taking
    the record away was not, which is what an auditor actually needs.

    Built in memory rather than streamed: the session is request-scoped and
    would be closed underneath a streaming generator, and the mirror is small
    (the largest entity is a few thousand rows). `raw` is included as the final
    column because it carries the LinkedTxn evidence of how each document was
    applied inside QBO — the thing that cannot be reconstructed once QBO is off.
    """
    model, _ = _resolve(entity)
    conds = _filters(model, date_from, date_to, q)
    order = model.txn_date.desc() if hasattr(model, "txn_date") else model.qbo_id
    rows = (await db.execute(select(model).where(*conds).order_by(order))).scalars().all()

    cols = [c.name for c in model.__table__.columns if c.name != "raw"] + ["raw"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        d = _row_dict(r)
        w.writerow([json.dumps(d.get(c), default=str) if isinstance(d.get(c), (dict, list))
                    else ("" if d.get(c) is None else d.get(c))
                    for c in cols])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="qbo-{entity}.csv"'},
    )


@router.get("/{entity}")
async def browse(entity: str, user: CurrentUser, db: AsyncSession = Depends(get_db),
                 page: int = 1, page_size: int = 50,
                 date_from: str | None = None, date_to: str | None = None, q: str | None = None):
    model, _ = _resolve(entity)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conds = _filters(model, date_from, date_to, q)
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


# ── attachment file stream ──────────────────────────────────────────────
# NOTE: "attachments" is not in _ENTITIES, so this 3-segment route never
# collides with the /{entity}/{qbo_id} browse-detail route above.

@router.get("/attachments/{qbo_id}/file")
async def attachment_file(qbo_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    a = (await db.execute(select(QboAttachment).where(QboAttachment.qbo_id == qbo_id))).scalars().first()
    if not a or a.content is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return Response(content=a.content, media_type=a.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{a.file_name or qbo_id}"'})
