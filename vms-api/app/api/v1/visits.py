"""Visit CRUD endpoints (PRD §6.5.2).

Visibility scope (PRD §3.2) is enforced in `crud.visit.apply_visibility_scope`.
Audit logging fires on every mutation (PRD VMS-AU-001..002).

S1 scope: status defaults to `confirmed` on create (no approval flow yet —
approval-api `vms_visit` doc_type is added in S2-B).
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_roles
from app.core.request_meta import load_request_meta
from app.crud import audit as audit_crud
from app.crud import visit as visit_crud
from app.crud import visitor as visitor_crud
from app.models.user_mirror import User
from app.models.visit import Visit, VisitStatus
from app.models.visitor import Visitor
from app.models.vms_config import VmsConfig
from app.schemas.visit import (
    VisitCheckOut,
    VisitCreate,
    VisitListResponse,
    VisitResponse,
    VisitUpdate,
)
from app.schemas.visitor import VisitorResponse
from app.services import approval as approval_svc
from app.services import attachments as attachments_svc
from app.services import notifications as notifications_svc

router = APIRouter(prefix="/visits", tags=["visits"])


async def _attach_visitors(db, rows: list[Visit]) -> list[VisitResponse]:
    """Batch-fetch visitors for a list of visits and assemble VisitResponse
    items with primary `visitor` + `additional_visitors` populated.

    Avoids per-row N+1 and avoids a SQLAlchemy relationship (async lazy
    loads are footguns when the response serializer touches the attribute
    outside the request greenlet).
    """
    if not rows:
        return []
    needed: set[uuid.UUID] = set()
    for r in rows:
        needed.add(r.visitor_id)
        for vid in r.additional_visitor_ids or []:
            try:
                needed.add(uuid.UUID(str(vid)))
            except (ValueError, TypeError):
                continue
    vmap = {
        v.id: v
        for v in (
            await db.execute(select(Visitor).where(Visitor.id.in_(needed)))
        ).scalars().all()
    }
    out: list[VisitResponse] = []
    for r in rows:
        resp = VisitResponse.model_validate(r)
        v = vmap.get(r.visitor_id)
        extras: list[VisitorResponse] = []
        for vid in r.additional_visitor_ids or []:
            try:
                vobj = vmap.get(uuid.UUID(str(vid)))
            except (ValueError, TypeError):
                vobj = None
            if vobj is not None:
                extras.append(VisitorResponse.model_validate(vobj))
        resp = resp.model_copy(update={
            "visitor": VisitorResponse.model_validate(v) if v is not None else None,
            "additional_visitors": extras,
        })
        out.append(resp)
    return out


# ── List / search ───────────────────────────────────────────────────────────

@router.get("", response_model=VisitListResponse)
async def list_visits(
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
    status_filter: VisitStatus | None = Query(default=None, alias="status"),
    host_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    meta = await load_request_meta(db, user, request)
    rows, total = await visit_crud.list_visits(
        db,
        user_id=meta.user_id,
        role=meta.role,
        department_id=meta.department_id,
        status=status_filter,
        host_id=host_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return VisitListResponse(items=await _attach_visitors(db, rows), total=total)


# `/active` MUST come before `/{visit_id}` to avoid being shadowed.
@router.get("/active", response_model=list[VisitResponse])
async def list_active_visits(
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Visitors currently on-site (status=checked_in)."""
    meta = await load_request_meta(db, user, request)
    rows = await visit_crud.list_active_visits(
        db,
        user_id=meta.user_id,
        role=meta.role,
        department_id=meta.department_id,
    )
    return await _attach_visitors(db, rows)


@router.get("/{visit_id}", response_model=VisitResponse)
async def get_visit(
    visit_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    row = await visit_crud.get_visit(db, visit_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )
    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, row.host_id)
    if not visit_crud.is_visible(
        row,
        user_id=meta.user_id,
        role=meta.role,
        department_id=meta.department_id,
        host_department_id=host_dept,
    ):
        # 404 (not 403) — don't leak the existence of visits outside the scope.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )
    items = await _attach_visitors(db, [row])
    return items[0]


# ── Create / update ─────────────────────────────────────────────────────────

@router.post("", response_model=VisitResponse, status_code=status.HTTP_201_CREATED)
async def create_visit(
    payload: VisitCreate,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    """Open to any authenticated UniOps user.

    Any UniOps role can host. created_by comes from the JWT, host_id from
    the payload (Hosts can register visits on behalf of someone else).

    When the visit's `access_area` triggers approval (warehouse / non-GMP
    production / GMP / lab / all-zones), vms-api:
      1. populates `visit_title`, `approval_status="draft"`, and sets
         user-facing `status=pending_approval`;
      2. for GMP/Lab additionally picks a Quality Manager from
         `vms_config.quality_manager_user_ids` (first-active strategy) and
         writes the chosen UUID to `quality_approver_id`;
      3. POSTs to the approval engine to start the workflow.
    """
    meta = await load_request_meta(db, user, request)

    # Validate companion visitor IDs up front so the FK error doesn't fire
    # for the primary row only. Empty list is the common (single-visitor) case.
    if payload.additional_visitor_ids:
        extra_ids = list({vid for vid in payload.additional_visitor_ids if vid != payload.visitor_id})
        if extra_ids:
            found = (
                await db.execute(select(Visitor.id).where(Visitor.id.in_(extra_ids)))
            ).scalars().all()
            missing = set(extra_ids) - set(found)
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown additional_visitor_ids: {sorted(str(m) for m in missing)}",
                )

    row = await visit_crud.create_visit(db, payload, created_by=meta.user_id)

    # Approval branching ---------------------------------------------------
    if approval_svc.access_requires_approval(row.access_area):
        visitor = await visitor_crud.get_visitor(db, row.visitor_id)
        if visitor is None:
            # Should not happen — we just created the visit pointing at this id.
            raise HTTPException(500, detail="Visitor missing post-create")

        row.visit_title = approval_svc.make_visit_title(
            first_name=visitor.first_name,
            last_name=visitor.last_name,
            company=visitor.company_name,
        )
        row.approval_status = "draft"
        row.status = VisitStatus.pending_approval

        # Quality Manager — required for GMP / Lab; the engine workflow may
        # or may not include the QM step depending on `workflow_defs`. Even
        # so we pre-resolve and write the candidate so the engine's special-
        # case lookup (engine.py `_create_approve_task` quality_manager branch)
        # has data to read.
        if approval_svc.access_requires_quality_manager(row.access_area):
            cfg = (await db.execute(select(VmsConfig).limit(1))).scalar_one_or_none()
            roster = (cfg.quality_manager_user_ids if cfg else []) or []
            # Resolve roster → active set so we don't pick a deactivated user.
            try:
                roster_uuids = [uuid.UUID(str(r)) for r in roster]
            except (ValueError, TypeError):
                roster_uuids = []
            if roster_uuids:
                active_rows = (await db.execute(
                    select(User.id).where(User.id.in_(roster_uuids), User.is_active.is_(True))
                )).scalars().all()
                row.quality_approver_id = approval_svc.pick_quality_manager(
                    roster=roster, active_user_ids=set(active_rows),
                )

        # COMMIT before the HTTP call so approval-api's separate session can
        # see the row under READ COMMITTED isolation. The trailing commit in
        # `get_session()` becomes a no-op for this request's main tx.
        await db.commit()

        # HTTP call to approval-api. In tests we monkey-patch
        # `approval_svc.submit_for_approval` so this doesn't actually fire.
        try:
            await approval_svc.submit_for_approval(row.id, token)
        except Exception as e:
            # The visit is already committed; mark it as unsubmitted via a
            # new transaction so the caller sees a coherent state.
            row.approval_status = None
            row.status = VisitStatus.confirmed
            await db.commit()
            raise HTTPException(
                status_code=502,
                detail=f"Could not submit visit for approval: {e}",
            ) from e

        # Re-read so the response reflects whatever approval-api wrote.
        await db.refresh(row)

    # Compliance gates (training/PPE confirmation tasks) and the HR training
    # heads-up email are NOT sent here. They fire only once the visit is
    # APPROVED — see crud.visit._maybe_notify_host_of_approval_result. Every
    # compliance-requiring area (GMP / Lab / all-zones) goes through approval,
    # so nothing is lost by deferring; it avoids pinging HR for a visit that
    # may still be rejected.
    visitor = await visitor_crud.get_visitor(db, row.visitor_id)
    host_user = (await db.execute(
        select(User).where(User.id == row.host_id)
    )).scalar_one_or_none()
    dispatched: list[str] = []
    if visitor is not None:
        # PPE prep email — host opt-in. Fires immediately ONLY for visits that
        # auto-confirm (office / warehouse — no approval gate). Approval-bound
        # visits defer this to the read-time post-approval hook so the Janitor
        # only stages gear after the visit is greenlit.
        if (
            row.ppe_requested
            and row.status == VisitStatus.confirmed
            and row.ppe_notified_at is None
        ):
            sent = await notifications_svc.notify_janitor_ppe_request(
                db, visit=row, visitor=visitor, host=host_user,
            )
            if sent:
                row.ppe_notified_at = datetime.now(timezone.utc)
                dispatched.append("ppe_request")
            else:
                dispatched.append("ppe_request[skipped]")

    # Audit log captures the final state (post-approval-submit if applicable).
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visit.create",
        entity_type="visit",
        entity_id=row.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        new_value=audit_crud.snapshot(row),
        notes=f"notifications: {','.join(dispatched)}" if dispatched else None,
    )
    return VisitResponse.model_validate(row)


@router.patch("/{visit_id}", response_model=VisitResponse)
async def patch_visit(
    visit_id: uuid.UUID,
    payload: VisitUpdate,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    row = await visit_crud.get_visit(db, visit_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )

    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, row.host_id)
    if not visit_crud.is_visible(
        row,
        user_id=meta.user_id,
        role=meta.role,
        department_id=meta.department_id,
        host_department_id=host_dept,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )

    # Only the creator (or system_admin) can edit; dept_manager can edit
    # their dept visits too.
    can_edit = (
        meta.role in ("system_admin", "auditor")  # auditor is read-only — see below
        or row.created_by == meta.user_id
        or (meta.role == "dept_manager" and host_dept == meta.department_id)
    )
    # auditor must remain read-only; strip them out explicitly.
    if meta.role == "auditor":
        can_edit = False
    if not can_edit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot edit a visit you did not create",
        )

    if not visit_crud.is_editable(row):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot edit a visit in status '{row.status.value}'",
        )

    before = audit_crud.snapshot(row)
    row = await visit_crud.update_visit(db, row, payload)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visit.update",
        entity_type="visit",
        entity_id=row.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value=before,
        new_value=audit_crud.snapshot(row),
    )
    return VisitResponse.model_validate(row)


# ── Cancel ──────────────────────────────────────────────────────────────────

@router.post("/{visit_id}/cancel", response_model=VisitResponse)
async def cancel_visit(
    visit_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    row = await visit_crud.get_visit(db, visit_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )

    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, row.host_id)

    # Visibility check first: if the caller can't even see the visit, return
    # 404 to avoid leaking its existence (matches GET /visits/{id} behavior).
    if not visit_crud.is_visible(
        row,
        user_id=meta.user_id,
        role=meta.role,
        department_id=meta.department_id,
        host_department_id=host_dept,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )

    # Caller can see it but may not have authority to cancel.
    # auditor is read-only even though they have full visibility.
    can_cancel = (
        meta.role == "system_admin"
        or row.created_by == meta.user_id
        or (meta.role == "dept_manager" and host_dept == meta.department_id)
    )
    if not can_cancel:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot cancel a visit you did not create",
        )

    if row.status not in (VisitStatus.confirmed, VisitStatus.pending_approval):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a visit in status '{row.status.value}'",
        )

    before = audit_crud.snapshot(row)

    # If approval is in flight, cascade cancel to approval-api first so the
    # task disappears from the approver's inbox.
    if row.status == VisitStatus.pending_approval and row.approval_status not in (
        None, "cancelled", "rejected", "approved",
    ):
        try:
            await approval_svc.cancel_approval(row.id, token)
        except Exception:
            # Tolerate engine failure — local cancel proceeds. The approval
            # task may be stale until next admin cleanup but VMS state is
            # consistent (Visit.status = cancelled).
            pass

    row = await visit_crud.cancel_visit(db, row)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visit.cancel",
        entity_type="visit",
        entity_id=row.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value=before,
        new_value=audit_crud.snapshot(row),
    )
    return VisitResponse.model_validate(row)


# ── Approval action (approve / reject / return) ─────────────────────────────-

@router.post("/{visit_id}/action", response_model=VisitResponse)
async def visit_action(
    visit_id: uuid.UUID,
    payload: dict,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    """Run an approval engine action (approve / reject / return) against a
    visit's pending task. Forwards to approval-api with the caller's bearer
    token — engine validates that the actor is the assigned approver, so
    we don't duplicate that check here.

    Body: {"action": "approve" | "reject" | "return", "comment": "…"}
    """
    action = (payload or {}).get("action")
    comment = (payload or {}).get("comment")
    if action not in ("approve", "reject", "return"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="action must be one of: approve, reject, return",
        )

    row = await visit_crud.get_visit(db, visit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Visit not found")

    meta = await load_request_meta(db, user, request)

    try:
        result = await approval_svc.run_action(
            visit_id, action=action, bearer_token=token, comment=comment,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"approval-api action failed: {e}",
        ) from e

    # approval-api wrote the new approval_status (and possibly user-facing
    # status via _post_approve_vms_visit). Re-read so the response is fresh.
    await db.refresh(row)
    approval_svc.sync_status_from_approval(row)

    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type=f"visit.{action}",
        entity_type="visit",
        entity_id=row.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        new_value={"engine_result": result, "comment": comment},
    )
    return VisitResponse.model_validate(row)


# ── Check-out ───────────────────────────────────────────────────────────────-

@router.post("/{visit_id}/check-out", response_model=VisitResponse)
async def check_out_visit(
    visit_id: uuid.UUID,
    payload: VisitCheckOut,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Mark visitor as departed (PRD §2.4.2 VMS-CO-001..007).

    Visibility scope and 'can_modify' permission match the cancel endpoint:
    only the creator / system_admin / dept_manager-of-dept can check out.
    Status gate (must be `checked_in`) is enforced in the crud layer.
    """
    row = await visit_crud.get_visit(db, visit_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )

    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, row.host_id)
    if not visit_crud.is_visible(
        row,
        user_id=meta.user_id,
        role=meta.role,
        department_id=meta.department_id,
        host_department_id=host_dept,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found"
        )

    can_modify = (
        meta.role == "system_admin"
        or row.created_by == meta.user_id
        or row.host_id == meta.user_id
        or (meta.role == "dept_manager" and host_dept == meta.department_id)
    )
    if not can_modify:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot check out a visit you do not host",
        )

    before = audit_crud.snapshot(row)
    row = await visit_crud.check_out_visit(db, row, payload)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visit.check_out",
        entity_type="visit",
        entity_id=row.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value=before,
        new_value=audit_crud.snapshot(row),
        notes=payload.notes,
    )
    return VisitResponse.model_validate(row)


# ── Batch checkout (Admin sweep) ────────────────────────────────────────────-

AdminDep = Depends(require_roles("system_admin"))


@router.post(
    "/batch-checkout",
    response_model=dict,
    status_code=status.HTTP_200_OK,
)
async def batch_check_out(
    request: Request,
    db: SessionDep,
    user: dict = AdminDep,
):
    """Close every on-site visit in one transaction (PRD VMS-CO-013/014).

    Used for end-of-day sweeps when visitors have left without scanning out.
    Each closed visit gets a `visit.check_out` audit row with notes
    `"System Batch Check-Out"`.
    """
    meta = await load_request_meta(db, user, request)
    rows = await visit_crud.list_checked_in(db)
    closed_ids = []

    payload = VisitCheckOut(badge_returned=False, ppe_returned=False, notes=None)
    for row in rows:
        before = audit_crud.snapshot(row)
        await visit_crud.check_out_visit(db, row, payload)
        await audit_crud.log_event(
            db,
            user_id=meta.user_id,
            user_name=meta.user_name,
            action_type="visit.check_out",
            entity_type="visit",
            entity_id=row.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            old_value=before,
            new_value=audit_crud.snapshot(row),
            notes="System Batch Check-Out",
        )
        closed_ids.append(str(row.id))

    return {"closed": len(closed_ids), "visit_ids": closed_ids}


# ── Attachments (proxy to file-api) ─────────────────────────────────────────-

@router.get("/{visit_id}/attachments", response_model=list[dict])
async def list_visit_attachments(
    visit_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """List attachments uploaded for a visit (ID copy, work permit, etc.).

    Visibility scope matches GET /visits/{id} — auditor sees all, Host
    sees own / own-dept. file-api owns the storage; we read metadata
    directly from the shared `file_metadata` table.
    """
    row = await visit_crud.get_visit(db, visit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Visit not found")

    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, row.host_id)
    if not visit_crud.is_visible(
        row,
        user_id=meta.user_id, role=meta.role,
        department_id=meta.department_id, host_department_id=host_dept,
    ):
        raise HTTPException(status_code=404, detail="Visit not found")

    return await attachments_svc.list_attachments(db, visit_id)


@router.post(
    "/{visit_id}/attachments",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def upload_visit_attachment(
    visit_id: uuid.UUID,
    file: UploadFile,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    """Forward a single file to file-api. Auditor is read-only."""
    row = await visit_crud.get_visit(db, visit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Visit not found")

    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, row.host_id)
    if not visit_crud.is_visible(
        row,
        user_id=meta.user_id, role=meta.role,
        department_id=meta.department_id, host_department_id=host_dept,
    ):
        raise HTTPException(status_code=404, detail="Visit not found")
    if meta.role == "auditor":
        raise HTTPException(status_code=403, detail="Auditors cannot upload attachments")

    data = await file.read()
    try:
        result = await attachments_svc.upload_attachment(
            visit_id=visit_id,
            bearer_token=token,
            filename=file.filename or "attachment",
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"file-api upload failed: {e}",
        ) from e

    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visit.attachment.upload",
        entity_type="visit",
        entity_id=visit_id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        new_value={"file_id": result.get("id"), "filename": result.get("original_filename")},
        notes=f"size={result.get('file_size')} bytes",
    )
    return result
