"""Health declaration CRUD (PRD §2.2.2 VMS-CI-010..013).

A single declaration row per visit. The submission writes:
  - one `vms_health_declarations` row with the full questionnaire snapshot
    (text + answer + computed result) baked in (PRD VMS-AU-013 — tamper-proof
    historical record even after Admin edits the template)
  - `vms_visits.health_decl_status` updated to passed / failed / restricted
  - `vms_visits.safety_training_confirmed` set per payload

The print-badge gate (`badge.is_printable`) reads `health_decl_status`. GMP/Lab
visits without `passed` cannot print.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health_declaration import HealthDeclaration
from app.models.user_mirror import User
from app.models.visit import HealthDeclStatus, Visit, VisitStatus
from app.models.visitor import Visitor


def compute_result(template_questions: list[dict], answers: list[dict]) -> HealthDeclStatus:
    """Walk through the (question, answer) pairs and return passed/failed.

    A question fails if its `answer` matches its `fail_on` value (default "yes").
    Any single failing question fails the whole declaration.
    """
    fail_map: dict[str, str] = {
        q.get("id", ""): str(q.get("fail_on", "yes")).lower()
        for q in template_questions
    }
    for a in answers:
        qid = a.get("id")
        ans = str(a.get("answer", "")).lower()
        if not qid:
            continue
        if qid in fail_map and ans == fail_map[qid]:
            return HealthDeclStatus.failed
    return HealthDeclStatus.passed


# Visits in these statuses are "closed" and can't be retroactively declared.
_DECLARABLE = frozenset({VisitStatus.confirmed, VisitStatus.pending_approval})


def visit_visitor_ids(visit: Visit) -> list[uuid.UUID]:
    """All visitor UUIDs on a visit: primary first, then companions.

    `additional_visitor_ids` is a JSONB list of strings — normalize to UUID
    and drop anything unparseable.
    """
    ids: list[uuid.UUID] = [visit.visitor_id]
    for raw in visit.additional_visitor_ids or []:
        try:
            vid = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            continue
        if vid not in ids:
            ids.append(vid)
    return ids


async def recompute_visit_health_status(db: AsyncSession, visit: Visit) -> None:
    """Aggregate the per-visitor declarations into `visit.health_decl_status`.

    The badge-print gate reads this single field. It is `passed` only when
    EVERY visitor on the appointment has a passing declaration; `failed` when
    any filed declaration failed; otherwise `None` (incomplete → still blocked).
    """
    decls = (
        await db.execute(
            select(HealthDeclaration).where(HealthDeclaration.visit_id == visit.id)
        )
    ).scalars().all()
    by_visitor = {d.visitor_id: d for d in decls}
    per_visitor = [by_visitor.get(vid) for vid in visit_visitor_ids(visit)]

    if any(d is not None and d.result == HealthDeclStatus.failed for d in per_visitor):
        visit.health_decl_status = HealthDeclStatus.failed
    elif all(d is not None and d.result == HealthDeclStatus.passed for d in per_visitor):
        visit.health_decl_status = HealthDeclStatus.passed
    else:
        visit.health_decl_status = None


async def submit_declaration(
    db: AsyncSession,
    *,
    visit: Visit,
    visitor_id: uuid.UUID,
    questionnaire_data: dict,
    result: HealthDeclStatus,
    signature: str | None,
    safety_training_confirmed: bool,
) -> HealthDeclaration:
    """Insert/replace one visitor's declaration and re-aggregate the visit.

    Rejects if the visit is already checked-in / checked-out / cancelled —
    declarations are a pre-arrival artifact. Replaces only THIS visitor's prior
    declaration (re-filing is allowed if their condition changed); other
    visitors on the appointment are untouched.
    """
    if visit.status not in _DECLARABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot file a health declaration for a visit in status '{visit.status.value}'",
        )

    existing = (
        await db.execute(
            select(HealthDeclaration).where(
                HealthDeclaration.visit_id == visit.id,
                HealthDeclaration.visitor_id == visitor_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.questionnaire_data = questionnaire_data
        existing.result = result
        existing.signature = signature
        row = existing
    else:
        row = HealthDeclaration(
            visit_id=visit.id,
            visitor_id=visitor_id,
            questionnaire_data=questionnaire_data,
            result=result,
            signature=signature,
        )
        db.add(row)

    # Food-safety briefing: once confirmed for the visit, stays confirmed.
    if safety_training_confirmed:
        visit.safety_training_confirmed = True

    await db.flush()
    await recompute_visit_health_status(db, visit)
    await db.flush()
    await db.refresh(row)
    await db.refresh(visit)
    return row


async def get_for_visit(
    db: AsyncSession, visit_id: uuid.UUID
) -> HealthDeclaration | None:
    return (
        await db.execute(
            select(HealthDeclaration).where(HealthDeclaration.visit_id == visit_id)
        )
    ).scalars().first()


async def get_all_for_visit(
    db: AsyncSession, visit_id: uuid.UUID
) -> list[HealthDeclaration]:
    """All visitors' declarations for a visit, oldest first."""
    return list(
        (
            await db.execute(
                select(HealthDeclaration)
                .where(HealthDeclaration.visit_id == visit_id)
                .order_by(HealthDeclaration.created_at)
            )
        ).scalars().all()
    )


async def list_declarations(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    role: str,
    department_id: uuid.UUID | None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[tuple], int]:
    """Cross-visit declarations browser, visibility-scoped like /visits.

    Returns (rows, total) where each row is
    (HealthDeclaration, Visitor, Visit, host_full_name).
    """
    # Local import avoids a circular dependency (visit crud imports schemas).
    from app.crud import visit as visit_crud

    base = (
        select(HealthDeclaration, Visitor, Visit, User.full_name)
        .join(Visit, Visit.id == HealthDeclaration.visit_id)
        .join(Visitor, Visitor.id == HealthDeclaration.visitor_id)
        .join(User, User.id == Visit.host_id)
    )
    # Same scope as visits: auditor/admin see all, host sees own, dept-mgr dept.
    base = visit_crud.apply_visibility_scope(
        base, user_id=user_id, role=role, department_id=department_id
    )
    if date_from is not None:
        base = base.where(Visit.visit_date >= date_from)
    if date_to is not None:
        base = base.where(Visit.visit_date <= date_to)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(
            or_(
                func.lower(Visitor.first_name).like(like),
                func.lower(Visitor.last_name).like(like),
                func.lower(Visitor.company_name).like(like),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            base.order_by(
                Visit.visit_date.desc(), HealthDeclaration.created_at.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return list(rows), total


def snapshot_answers(template: dict, raw_answers: Iterable[dict]) -> list[dict]:
    """Bake question text into each answer so historical records survive template edits.

    Input shape:  [{"id": "fever_cough", "answer": "no"}, ...]
    Output shape: [{"id": "fever_cough", "text": "Have you …?", "answer": "no"}, ...]

    Unknown question ids are dropped silently — frontend should send only ids
    that exist in the current template.
    """
    text_map = {q.get("id"): q.get("text", "") for q in template.get("questions", [])}
    out: list[dict] = []
    for a in raw_answers:
        qid = a.get("id")
        if not qid or qid not in text_map:
            continue
        out.append({
            "id": qid,
            "text": text_map[qid],
            "answer": str(a.get("answer", "")).lower(),
        })
    return out
