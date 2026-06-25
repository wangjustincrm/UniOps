"""Per-visitor training + PPE compliance gates.

Per the user decision tree:
  - Training / PPE are tracked on the **Visitor** row (not Visit) so frequent
    visitors keep a rolling 12-month record.
  - Only the **configured HR / Janitor email** can confirm — looked up by
    matching `users.email` against `vms_config.notification_contacts`.
  - On visit creation, when a visitor's record is missing or stale, we
    create Task rows assigned to those users so the gate shows up in their
    Portal / VMS task inboxes, AND fire the existing notification email
    with a deep-link to the visitor's compliance page.
  - Badge print for GMP / Lab visits is blocked until both gates are fresh.

This module owns the freshness math + task creation; route handlers call
into it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_mirror import Task
from app.models.user_mirror import User
from app.models.visit import AccessArea, Visit
from app.models.visitor import Visitor

# 12-month TTL — matches CFIA / food-safety practice. Lives here, not in
# schema, so we can tune it without a migration.
COMPLIANCE_TTL = timedelta(days=365)

# Access areas that require both gates. Mirrors the email-dispatch set.
_NEEDS_COMPLIANCE = frozenset({
    AccessArea.production_gmp,
    AccessArea.laboratory,
    AccessArea.all,
})

# Task document type codes — must fit `tasks.document_type varchar(10)`.
DOC_TYPE_TRAINING = "vms_train"
DOC_TYPE_PPE = "vms_ppe"

ComplianceKind = Literal["training", "ppe"]


# ── Freshness helpers ───────────────────────────────────────────────────────


def _is_fresh(at: datetime | None, *, now: datetime | None = None) -> bool:
    if at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (now - at) < COMPLIANCE_TTL


def training_fresh(visitor: Visitor, *, now: datetime | None = None) -> bool:
    return _is_fresh(visitor.safety_training_confirmed_at, now=now)


def ppe_fresh(visitor: Visitor, *, now: datetime | None = None) -> bool:
    return _is_fresh(visitor.ppe_issued_at, now=now)


def visit_requires_compliance(visit: Visit) -> bool:
    return visit.access_area in _NEEDS_COMPLIANCE


# ── Contact lookup ──────────────────────────────────────────────────────────-

async def _configured_contacts(db: AsyncSession) -> dict[str, str | None]:
    """Read `vms_config.notification_contacts` (training_email, ppe_email)."""
    raw = (await db.execute(
        text("SELECT notification_contacts FROM vms_config LIMIT 1")
    )).scalar_one_or_none()
    contacts = raw if isinstance(raw, dict) else {}
    return {
        "training_email": contacts.get("training_email") or None,
        "ppe_email": contacts.get("ppe_email") or None,
    }


async def _user_id_for_email(db: AsyncSession, email: str | None) -> uuid.UUID | None:
    if not email:
        return None
    row = (await db.execute(
        select(User.id).where(User.email == email, User.is_active.is_(True))
    )).scalar_one_or_none()
    return row


async def is_authorized_confirmer(
    db: AsyncSession, *, kind: ComplianceKind, user_id: uuid.UUID, user_role: str,
) -> bool:
    """The configured HR / Janitor user (matched by email) — plus system_admin
    override — can confirm. Anyone else gets 403."""
    if user_role == "system_admin":
        return True
    contacts = await _configured_contacts(db)
    key = "training_email" if kind == "training" else "ppe_email"
    contact_email = contacts.get(key)
    if not contact_email:
        return False
    contact_user_id = await _user_id_for_email(db, contact_email)
    return contact_user_id is not None and contact_user_id == user_id


# ── Task creation / completion ──────────────────────────────────────────────-

_TASK_TYPE = {
    "training": "vms_confirm_training",
    "ppe": "vms_confirm_ppe",
}

_TASK_TITLE = {
    "training": "Confirm food-safety training",
    "ppe": "Confirm PPE issuance",
}

_TASK_ROLE = {
    "training": "vms_training_contact",
    "ppe": "vms_ppe_contact",
}


async def _existing_open_task(
    db: AsyncSession, *, kind: ComplianceKind, visitor_id: uuid.UUID,
) -> Task | None:
    doc_type = DOC_TYPE_TRAINING if kind == "training" else DOC_TYPE_PPE
    row = (await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.document_id == visitor_id,
            Task.is_completed.is_(False),
        ).limit(1)
    )).scalar_one_or_none()
    return row


async def open_compliance_task(
    db: AsyncSession,
    *,
    kind: ComplianceKind,
    visitor: Visitor,
    triggering_visit: Visit,
) -> Task | None:
    """Create (or reuse) an open Task row for the HR / Janitor contact.

    Returns None when the contact isn't configured (admin hasn't set the
    email in the VMS Admin panel) — the gate stays blocked but no task is
    created. Idempotent: if an open task already exists for the same
    visitor + kind, returns it unchanged so we don't spam the inbox when
    the same visitor is registered for multiple visits.
    """
    existing = await _existing_open_task(db, kind=kind, visitor_id=visitor.id)
    if existing is not None:
        return existing

    contacts = await _configured_contacts(db)
    contact_email = contacts.get("training_email" if kind == "training" else "ppe_email")
    assigned_user_id = await _user_id_for_email(db, contact_email)
    if assigned_user_id is None:
        # Either the email isn't configured or no active user matches it.
        # Don't create an unassignable task — the caller's email notification
        # path is still useful even without an inbox row.
        return None

    visitor_label = f"{visitor.first_name} {visitor.last_name} ({visitor.company_name})"
    doc_type = DOC_TYPE_TRAINING if kind == "training" else DOC_TYPE_PPE
    task = Task(
        type=_TASK_TYPE[kind],
        priority="normal",
        document_type=doc_type,
        document_id=visitor.id,
        document_number=str(visitor.id)[:8],
        assigned_role=_TASK_ROLE[kind],
        assigned_user_id=assigned_user_id,
        title=f"{_TASK_TITLE[kind]} — {visitor_label}",
        description=(
            f"Triggered by visit on {triggering_visit.visit_date.isoformat()} "
            f"({triggering_visit.access_area.value.replace('_', ' ')}). "
            f"Open the visitor compliance page to confirm."
        ),
    )
    db.add(task)
    await db.flush()
    return task


async def complete_compliance_tasks(
    db: AsyncSession, *, kind: ComplianceKind, visitor_id: uuid.UUID, completed_by: uuid.UUID,
) -> None:
    """Mark all open Task rows for this visitor + kind as completed.

    Called by the confirm endpoint after writing the timestamp on the
    Visitor row.
    """
    doc_type = DOC_TYPE_TRAINING if kind == "training" else DOC_TYPE_PPE
    rows = (await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.document_id == visitor_id,
            Task.is_completed.is_(False),
        )
    )).scalars().all()
    now = datetime.now(timezone.utc)
    for t in rows:
        t.is_completed = True
        t.completed_at = now
        t.completed_by = completed_by


# ── Confirmation writers ────────────────────────────────────────────────────-


async def confirm_training(
    db: AsyncSession, visitor: Visitor, *, confirmed_by: uuid.UUID,
) -> Visitor:
    now = datetime.now(timezone.utc)
    visitor.safety_training_confirmed_at = now
    visitor.safety_training_confirmed_by = confirmed_by
    await complete_compliance_tasks(
        db, kind="training", visitor_id=visitor.id, completed_by=confirmed_by,
    )
    await db.flush()
    return visitor


async def confirm_ppe(
    db: AsyncSession, visitor: Visitor, *, confirmed_by: uuid.UUID,
) -> Visitor:
    now = datetime.now(timezone.utc)
    visitor.ppe_issued_at = now
    visitor.ppe_issued_by = confirmed_by
    await complete_compliance_tasks(
        db, kind="ppe", visitor_id=visitor.id, completed_by=confirmed_by,
    )
    await db.flush()
    return visitor
