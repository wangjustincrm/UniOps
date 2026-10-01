"""Audit log read endpoints (PRD §2.5.2 / §6.5.5 VMS-AU-005..009)."""
import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.deps import SessionDep, require_roles
from app.crud import audit_query as audit_crud
from app.schemas.audit_log import AuditLogListResponse, AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["audit"])

# Only auditors and admins can read the audit trail (PRD §3.2).
AuditDep = Annotated[dict, Depends(require_roles("auditor", "system_admin"))]


# ── JSON list (paginated) ───────────────────────────────────────────────────-

@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    db: SessionDep,
    _: AuditDep,
    user_id: uuid.UUID | None = None,
    action_type: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    rows, total = await audit_crud.list_audit_logs(
        db,
        user_id=user_id, action_type=action_type, entity_type=entity_type,
        entity_id=entity_id, date_from=date_from, date_to=date_to,
        page=page, page_size=page_size,
    )
    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(r) for r in rows],
        total=total,
    )


# ── Filter options ──────────────────────────────────────────────────────────-

@router.get("/facets")
async def audit_log_facets(db: SessionDep, _: AuditDep):
    """The action and entity types actually present in the log, for the Audit
    Log page's filters. The page used to hard-code a list that had fallen far
    behind (no approvals, no-shows, health declarations, admin changes,
    exports), so those events could not be filtered."""
    from sqlalchemy import distinct, select

    from app.models.audit_log import AuditLog

    actions = (await db.execute(select(distinct(AuditLog.action_type)).order_by(AuditLog.action_type))).scalars().all()
    entities = (await db.execute(select(distinct(AuditLog.entity_type)).order_by(AuditLog.entity_type))).scalars().all()
    return {"action_types": list(actions), "entity_types": list(entities)}


# ── CSV export (streaming) ──────────────────────────────────────────────────-

_CSV_COLUMNS = [
    "id", "timestamp", "user_id", "user_name", "action_type",
    "entity_type", "entity_id", "ip_address", "user_agent", "notes",
    "old_value", "new_value",
]


@router.get("/export")
async def export_audit_logs(
    db: SessionDep,
    _: AuditDep,
    user_id: uuid.UUID | None = None,
    action_type: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
):
    """Stream the filtered audit log as CSV.

    Cursor-paginated server-side to keep memory bounded; Pydantic
    serialization is bypassed because we're writing raw text.
    """
    async def _rows():
        # Header row first.
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CSV_COLUMNS)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)

        async for row in audit_crud.stream_audit_logs(
            db,
            user_id=user_id, action_type=action_type, entity_type=entity_type,
            entity_id=entity_id, date_from=date_from, date_to=date_to,
        ):
            writer.writerow([
                row.id,
                row.timestamp.isoformat() if row.timestamp else "",
                str(row.user_id),
                row.user_name,
                row.action_type,
                row.entity_type,
                str(row.entity_id),
                row.ip_address,
                row.user_agent or "",
                row.notes or "",
                json.dumps(row.old_value, ensure_ascii=False) if row.old_value is not None else "",
                json.dumps(row.new_value, ensure_ascii=False) if row.new_value is not None else "",
            ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    filename = f"vms-audit-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        _rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
