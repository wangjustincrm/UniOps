"""CRUD operations for `vms_visits` + visibility-scope helper.

Visibility rules (PRD §3.2):
  - `system_admin` / `auditor`: see ALL visits.
  - `dept_manager`: see all visits whose Host is in their own department.
  - Anyone else (requester / gm / opm / finance_bp / etc.): see only visits
    they created OR are the Host of.

The dept-manager scope joins via `users.department_id` of `host_id`; we keep
that join in CRUD so route handlers don't need to know the schema details.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_mirror import User
from app.models.visit import Visit, VisitStatus
from app.schemas.visit import VisitCheckOut, VisitCreate, VisitUpdate


# ── Visibility scope ─────────────────────────────────────────────────────────

# Roles that bypass scoping and see everything.
_FULL_SCOPE_ROLES = frozenset({"system_admin", "auditor"})


def apply_visibility_scope(
    stmt: Select,
    *,
    user_id: uuid.UUID,
    role: str,
    department_id: uuid.UUID | None,
) -> Select:
    """Return `stmt` narrowed to the visits the caller is allowed to see."""
    if role in _FULL_SCOPE_ROLES:
        return stmt

    # Anyone assigned as the visit's Quality Manager (per-visit role, not
    # tied to UniOps `role` column) gets visibility on that visit. Without
    # this, a QM clicking the task in their inbox hits 404 on the deep-link.
    qm_clause = Visit.quality_approver_id == user_id

    if role == "dept_manager" and department_id is not None:
        # Manager sees their own visits + every visit whose host is in their dept.
        host_in_dept = (
            select(User.id).where(User.department_id == department_id)
        ).subquery()
        return stmt.where(
            or_(
                Visit.created_by == user_id,
                Visit.host_id == user_id,
                Visit.host_id.in_(select(host_in_dept)),
                qm_clause,
            )
        )

    # Default: own visits only (whether you're the creator or the host)
    # plus visits where this user is the assigned QM.
    return stmt.where(
        or_(Visit.created_by == user_id, Visit.host_id == user_id, qm_clause)
    )


# ── List ─────────────────────────────────────────────────────────────────────

async def list_visits(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    role: str,
    department_id: uuid.UUID | None,
    status: VisitStatus | None = None,
    host_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Visit], int]:
    q = select(Visit).order_by(Visit.visit_date.desc(), Visit.planned_arrival.desc())
    q = apply_visibility_scope(
        q, user_id=user_id, role=role, department_id=department_id
    )

    if status is not None:
        q = q.where(Visit.status == status)
    if host_id is not None:
        q = q.where(Visit.host_id == host_id)
    if date_from is not None:
        q = q.where(Visit.visit_date >= date_from)
    if date_to is not None:
        q = q.where(Visit.visit_date <= date_to)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = list((await db.execute(q)).scalars().all())
    return rows, total


async def list_active_visits(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    role: str,
    department_id: uuid.UUID | None,
) -> list[Visit]:
    """Visits currently on-site (status=checked_in), visibility-scoped."""
    q = (
        select(Visit)
        .where(Visit.status == VisitStatus.checked_in)
        .order_by(Visit.actual_arrival)
    )
    q = apply_visibility_scope(
        q, user_id=user_id, role=role, department_id=department_id
    )
    return list((await db.execute(q)).scalars().all())


async def get_visit(db: AsyncSession, visit_id: uuid.UUID) -> Visit | None:
    row = (
        await db.execute(select(Visit).where(Visit.id == visit_id))
    ).scalar_one_or_none()
    if row is not None:
        # Mirror engine's terminal `approval_status` onto user-facing `status`
        # for visits where the engine acted but no post-action hook ran
        # (reject, cancel paths). See S2_ARCHITECTURE_REVIEW.md F9.
        # Local import avoids circular dependency with services/approval.py.
        from app.services.approval import sync_status_from_approval
        sync_status_from_approval(row)
        await _maybe_notify_host_of_approval_result(db, row)
    return row


async def _maybe_notify_host_of_approval_result(
    db: AsyncSession, visit: Visit,
) -> None:
    """One-shot Host approval-result email (W12 / S2-D).

    Trigger: visit went through approval (`approval_status` reached a terminal
    value) AND we haven't notified yet (`host_notified_at` is NULL).
    Persists the timestamp atomically inside the same session so subsequent
    reads skip the send.

    We can't rely on detecting the mid-read transition because approval-api
    writes the terminal `Visit.status` directly via raw SQL (the
    `_post_approve_vms_visit` callback). By the time vms-api SELECTs, the
    row already reflects the terminal state. So `host_notified_at` is the
    only reliable idempotency signal.

    SMTP failures don't propagate — they log a warning but the visit read
    still returns successfully (see services/notifications._send_email).

    Caveat: this is read-driven, not webhook-driven. If no one reads the
    visit for an hour after approval, the email doesn't fire. The background
    scanner (S3-A) will provide a deterministic delivery SLA.
    """
    if visit.host_notified_at is not None:
        return
    if visit.approval_status not in ("approved", "rejected", "cancelled"):
        return
    if visit.status not in (VisitStatus.confirmed, VisitStatus.cancelled):
        return

    from app.crud.visitor import get_visitor
    from app.services.notifications import notify_host_approval_resolved

    visitor = await get_visitor(db, visit.visitor_id)
    host = (
        await db.execute(select(User).where(User.id == visit.host_id))
    ).scalar_one_or_none()
    if visitor is None or host is None:
        return

    await notify_host_approval_resolved(
        db, visit=visit, visitor=visitor, host=host,
        approved=(visit.status == VisitStatus.confirmed),
    )

    # Approved-only post-approval side effects. Deferred from create time so
    # HR / Janitor aren't pinged for a visit that may still be rejected
    # (PRD VMS-PR-013 / VMS-PR-021..022). Every compliance-requiring area
    # goes through approval, so this is the right single firing point.
    # Guarded by the outer `host_notified_at is None` check → fires once.
    if visit.status == VisitStatus.confirmed:
        from app.services.notifications import notify_janitor_ppe_request

        # NOTE: training / PPE confirm tasks + the HR training heads-up email
        # are NOT created here. They fire at check-in (first badge print) —
        # training/PPE are post-entry steps, not pre-entry gates. See
        # api/v1/badge.py print_visit_badge.

        # Janitor PPE prep email — only when the host opted in. Separate
        # `ppe_notified_at` flag so SMTP flakiness on one send doesn't mask
        # the other.
        if visit.ppe_requested and visit.ppe_notified_at is None:
            sent = await notify_janitor_ppe_request(
                db, visit=visit, visitor=visitor, host=host,
            )
            if sent:
                visit.ppe_notified_at = datetime.now(timezone.utc)

    visit.host_notified_at = datetime.now(timezone.utc)
    await db.flush()


def is_visible(
    visit: Visit,
    *,
    user_id: uuid.UUID,
    role: str,
    department_id: uuid.UUID | None,
    host_department_id: uuid.UUID | None = None,
) -> bool:
    """Single-row visibility check (mirrors `apply_visibility_scope`)."""
    if role in _FULL_SCOPE_ROLES:
        return True
    if visit.created_by == user_id or visit.host_id == user_id:
        return True
    # Assigned Quality Manager — needed so they can open the visit from
    # their task-inbox deep-link even though their UniOps role (e.g. opm)
    # has no dept-scope claim on this visit.
    if visit.quality_approver_id is not None and visit.quality_approver_id == user_id:
        return True
    if (
        role == "dept_manager"
        and department_id is not None
        and host_department_id == department_id
    ):
        return True
    return False


# ── Create / update / cancel ─────────────────────────────────────────────────

async def create_visit(
    db: AsyncSession,
    payload: VisitCreate,
    *,
    created_by: uuid.UUID,
) -> Visit:
    # JSONB columns can't serialize uuid.UUID objects via the default JSON
    # encoder asyncpg uses — coerce to strings before insert. Reads coerce
    # back via uuid.UUID() when we need the typed value.
    data = payload.model_dump()
    data["additional_visitor_ids"] = [str(v) for v in data.get("additional_visitor_ids", [])]
    ppe = data.get("ppe_requested")
    if ppe and isinstance(ppe, dict):
        for item in ppe.get("items") or []:
            if "visitor_id" in item:
                item["visitor_id"] = str(item["visitor_id"])
    row = Visit(**data, created_by=created_by)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


# Visits can only be edited before the visitor actually arrives.
_EDITABLE_STATUSES = frozenset({VisitStatus.confirmed, VisitStatus.pending_approval})


def is_editable(visit: Visit) -> bool:
    return visit.status in _EDITABLE_STATUSES


async def update_visit(
    db: AsyncSession, visit: Visit, payload: VisitUpdate
) -> Visit:
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(visit, k, v)
    await db.flush()
    await db.refresh(visit)
    return visit


async def cancel_visit(db: AsyncSession, visit: Visit) -> Visit:
    visit.status = VisitStatus.cancelled
    await db.flush()
    await db.refresh(visit)
    return visit


# ── Check-out ────────────────────────────────────────────────────────────────

async def check_out_visit(
    db: AsyncSession, visit: Visit, payload: VisitCheckOut
) -> Visit:
    """Transition a checked_in visit to checked_out.

    Records `actual_departure`, `badge_returned`, optionally appends a
    `system.checkout_note` entry to `ppe_issued.notes` so the trail stays
    on the visit row (PRD VMS-CO-005..007).
    """
    if visit.status != VisitStatus.checked_in:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot check out a visit in status '{visit.status.value}'",
        )
    visit.actual_departure = datetime.now(timezone.utc)
    visit.status = VisitStatus.checked_out
    visit.badge_returned = bool(payload.badge_returned)

    # ppe_returned + notes are best-effort tagged onto ppe_issued JSON so we
    # don't grow the visit table schema in S1. A future field can replace it.
    if payload.ppe_returned is not None or payload.notes:
        existing = dict(visit.ppe_issued or {})
        existing["returned"] = bool(payload.ppe_returned)
        if payload.notes:
            existing["checkout_notes"] = payload.notes
        visit.ppe_issued = existing

    await db.flush()
    await db.refresh(visit)
    return visit


async def list_checked_in(db: AsyncSession) -> list[Visit]:
    """Unscoped — used by batch-checkout and dashboard counters."""
    rows = (
        await db.execute(select(Visit).where(Visit.status == VisitStatus.checked_in))
    ).scalars().all()
    return list(rows)


async def count_active(db: AsyncSession) -> int:
    return (
        await db.execute(
            select(func.count(Visit.id)).where(Visit.status == VisitStatus.checked_in)
        )
    ).scalar_one()


async def count_today(db: AsyncSession, today: date) -> int:
    """Count of visits scheduled for `today`, regardless of status."""
    return (
        await db.execute(
            select(func.count(Visit.id)).where(Visit.visit_date == today)
        )
    ).scalar_one()


async def count_this_week(db: AsyncSession, today: date) -> int:
    """Rolling 7-day window ending at `today` (inclusive).

    Picked over Mon-Sun calendar weeks because the rolling window keeps the
    dashboard meaningful on a Monday morning when calendar-week counts would
    otherwise read 0.
    """
    start = today - timedelta(days=6)
    return (
        await db.execute(
            select(func.count(Visit.id)).where(
                Visit.visit_date >= start,
                Visit.visit_date <= today,
            )
        )
    ).scalar_one()


async def count_overdue(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Visits still on-site past their planned departure (PRD VMS-CO-009)."""
    now = now or datetime.now(timezone.utc)
    return (
        await db.execute(
            select(func.count(Visit.id)).where(
                Visit.status == VisitStatus.checked_in,
                Visit.planned_departure.is_not(None),
                Visit.planned_departure < now,
            )
        )
    ).scalar_one()


# ── Helpers used by audit logging ───────────────────────────────────────────-

async def fetch_host_department(
    db: AsyncSession, host_id: uuid.UUID
) -> uuid.UUID | None:
    return (
        await db.execute(
            select(User.department_id).where(User.id == host_id)
        )
    ).scalar_one_or_none()
