"""Badge printing CRUD.

`print_badge` is the atomic write that the entire S1-E (Week 5) flow hangs on:

  * First print on a visit → INSERT vms_badge_prints row AND flip the visit
    to `checked_in` (records `actual_arrival`).
  * Subsequent prints → INSERT only. A `reprint_reason` is required by the
    schema; the visit's status is left alone.

Pre-conditions enforced here (in addition to the API-layer scope check):
  * Visit must not be `cancelled`, `checked_out`, or `no_show`.
  * Visit must not be `pending_approval` (S2 approval gate — once the
    approval-api integration lands, that state means the badge can't print
    until dept_manager / quality_manager have signed off).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.badge_print import BadgePrint
from app.models.visit import AccessArea, HealthDeclStatus, Visit, VisitStatus


# Statuses from which a badge CANNOT be printed at all.
_TERMINAL = frozenset({
    VisitStatus.cancelled, VisitStatus.checked_out, VisitStatus.no_show,
})

# Access areas that require a passing health declaration before a badge prints.
_HEALTH_DECL_REQUIRED = frozenset({
    AccessArea.production_gmp, AccessArea.laboratory,
})


def is_printable(visit: Visit) -> tuple[bool, str | None]:
    """Returns (allowed, reason). `reason` is set when not allowed."""
    if visit.status in _TERMINAL:
        return False, f"Cannot print badge for a visit in status '{visit.status.value}'"
    if visit.status == VisitStatus.pending_approval:
        return False, "Visit is pending approval — badge cannot print until approved"
    # PRD §2.2.2 VMS-CI-010: GMP/lab visits require a passing health declaration.
    # `not_required` / null / `failed` all block the print path.
    if visit.access_area in _HEALTH_DECL_REQUIRED:
        if visit.health_decl_status != HealthDeclStatus.passed:
            return False, (
                "Health declaration required before printing a "
                f"{visit.access_area.value.replace('_', ' ')} badge"
            )
    return True, None


async def print_badge(
    db: AsyncSession,
    *,
    visit: Visit,
    printed_by: uuid.UUID,
    template_used: str = "standard",
    reprint_reason: str | None = None,
) -> tuple[BadgePrint, bool]:
    """Insert a badge-print row.

    Returns (row, was_first_print). When `was_first_print` is True, the visit
    has been atomically transitioned to `checked_in` and its `actual_arrival`
    set to the same timestamp as `printed_at`.

    Raises 422 if:
      - the visit is in a terminal / pre-approval state, or
      - this is a reprint and `reprint_reason` is missing.
    """
    ok, why = is_printable(visit)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=why,
        )

    # Detect first-print by checking whether check-in has already happened.
    # `actual_arrival is None` is the canonical "never printed before" signal.
    is_first = visit.actual_arrival is None

    if not is_first and not reprint_reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Reprint requires a reason (PRD VMS-LB-008)",
        )

    now = datetime.now(timezone.utc)

    row = BadgePrint(
        visit_id=visit.id,
        printed_by=printed_by,
        # Let server_default handle printed_at on first INSERT, but we want
        # it identical to the visit's actual_arrival for first prints.
        printed_at=now,
        reprint_reason=reprint_reason if not is_first else None,
        template_used=template_used,
    )
    db.add(row)

    if is_first:
        visit.actual_arrival = now
        visit.status = VisitStatus.checked_in

    await db.flush()
    await db.refresh(row)
    return row, is_first


async def list_badge_prints(
    db: AsyncSession, visit_id: uuid.UUID
) -> list[BadgePrint]:
    """Chronological list of badge prints for a visit."""
    rows = (
        await db.execute(
            select(BadgePrint)
            .where(BadgePrint.visit_id == visit_id)
            .order_by(BadgePrint.printed_at)
        )
    ).scalars().all()
    return list(rows)


async def count_badge_prints(db: AsyncSession, visit_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count(BadgePrint.id)).where(BadgePrint.visit_id == visit_id)
        )
    ).scalar_one()
