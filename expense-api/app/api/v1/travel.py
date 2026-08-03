"""Travel-application read endpoints: eligible TRAs for the TRV picker, user directory."""
import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

# Postgres SQLSTATE for "undefined_table" — the ONLY DB error we tolerate below.
_UNDEFINED_TABLE = "42P01"

from app.core.deps import BearerTokenDep, CurrentUserDep, SessionDep
from app.crud import expense as expense_crud
from app.models.expense import ExpenseAttachment
from app.services.attachment_helper import upload_to_file_server
from app.services.pdf_tra import build_travel_application_pdf

log = logging.getLogger(__name__)

router = APIRouter(tags=["travel"])


class EligibleTravelApp(BaseModel):
    id: uuid.UUID
    claim_number: str
    travel_destination: str | None = None
    travel_from_date: str | None = None
    travel_to_date: str | None = None
    purpose: str | None = None


@router.get("/travel-applications/eligible", response_model=list[EligibleTravelApp])
async def eligible_travel_apps(db: SessionDep, user: CurrentUserDep):
    user_id = uuid.UUID(user["sub"])
    rows = await expense_crud.list_eligible_travel_apps(db, user_id)
    return [EligibleTravelApp(
        id=c.id, claim_number=c.claim_number, travel_destination=c.travel_destination,
        travel_from_date=c.travel_from_date.isoformat() if c.travel_from_date else None,
        travel_to_date=c.travel_to_date.isoformat() if c.travel_to_date else None,
        purpose=c.purpose) for c in rows]


class DirectoryUser(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None


@router.get("/users/directory", response_model=list[DirectoryUser])
async def user_directory(db: SessionDep, _: CurrentUserDep, q: str = ""):
    """Search active users by name/email (shared users table). Limit 20.

    Best-effort like the actor-name lookup in app/api/v1/expenses.py: the
    `users` table is identity-owned and absent from this service's test DB.
    Only that specific, expected condition (SQLSTATE 42P01 / undefined_table)
    is tolerated and degrades to `[]`. Any other DB error (dropped connection,
    permission error, a future SQL typo, etc.) is a genuine incident and must
    surface as a 500 — swallowing it here would silently mask a real outage
    behind "no search results".
    """
    like = f"%{q.strip()}%"
    try:
        rows = (await db.execute(text(
            "SELECT id, full_name, email FROM users "
            "WHERE is_active = true AND (full_name ILIKE :like OR email ILIKE :like) "
            "ORDER BY full_name LIMIT 20"), {"like": like})).all()
    except ProgrammingError as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate != _UNDEFINED_TABLE:
            raise
        await db.rollback()
        log.warning(
            "user_directory: `users` table not found (SQLSTATE 42P01) — "
            "expected in this service's test DB, returning []")
        return []
    return [DirectoryUser(id=r[0], full_name=r[1] or "", email=r[2]) for r in rows]


@router.post("/travel-applications/{claim_id}/pdf")
async def regenerate_tra_pdf(claim_id: uuid.UUID, db: SessionDep,
                             user: CurrentUserDep, token: BearerTokenDep):
    from sqlalchemy import bindparam
    from sqlalchemy import select as sa_select
    from app.models.approval_event_mirror import ApprovalEventMirror as AEM

    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim or claim.claim_type != "TRA":
        raise HTTPException(status_code=404, detail="Travel Application not found")

    # Real approval history lives in the shared approval_events table (mirrored here
    # as ApprovalEventMirror) — `claim.approval_events` (ExpenseApprovalEvent) is never
    # written anywhere in this service, so it would always render blank signatures.
    # Same source `get_approval_status` (~expenses.py:369-381) already uses.
    events = list((await db.execute(
        sa_select(AEM).where(AEM.document_id == claim_id, AEM.action == "approve")
        .order_by(AEM.created_at.asc())
    )).scalars().all())

    # Resolve actor display names from the shared users table — best-effort, same
    # pattern as get_approval_status: that table is identity-owned and absent from
    # this service's test DB, so a failure here must not 500 or poison the session
    # for the ExpenseAttachment write below (hence the rollback).
    names: dict = {}
    actor_ids = list({e.actor_id for e in events})
    if actor_ids:
        try:
            q = text("SELECT id, full_name FROM users WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True))
            names = {r[0]: r[1] for r in (await db.execute(q, {"ids": actor_ids})).all()}
        except Exception:
            await db.rollback()
            names = {}

    approvals = [{
        "step_idx": getattr(e, "step_idx", None),
        "actor_name": names.get(e.actor_id) or getattr(e, "actor_role", "") or "",
        "acted_date": e.created_at.date().isoformat() if getattr(e, "created_at", None) else None,
    } for e in events]

    data = build_travel_application_pdf(claim, approvals=approvals)
    filename = f"{claim.claim_number}.pdf"
    try:
        storage_key = await upload_to_file_server(
            data, filename, "application/pdf", "tra", claim.id, token)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    att = ExpenseAttachment(claim_id=claim.id, file_id=str(storage_key),
                            file_name=filename, file_size_bytes=len(data),
                            mime_type="application/pdf")
    db.add(att)
    await db.commit()
    return {"file_name": filename, "file_id": str(storage_key)}
