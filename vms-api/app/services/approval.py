"""Bridge between vms-api business logic and approval-api engine.

vms-api delegates the actual approval workflow to approval-api. This module
decides whether a visit needs approval at all (based on access_area), resolves
the Quality Manager candidate for GMP / Lab visits, and triggers the engine's
submit/cancel actions over HTTP.

Per S2_ARCHITECTURE_REVIEW.md F1 + locked decision #1, vms-api sets:
  - `Visit.status = pending_approval`  (user-facing VisitStatus enum)
  - `Visit.approval_status = "draft"`  (engine vocabulary, lives on same row)
  - `Visit.visit_title = "VMS Visit — {first} {last} ({company})"`  (Portal label)
  - `Visit.quality_approver_id = <UUID>` for GMP/Lab (picked from roster)

…BEFORE the HTTP POST to approval-api, so the engine's session reads
consistent state from the shared DB.

Quality Manager strategy: **first-active** per locked decision #7. We pick
`cfg.quality_manager_user_ids[0]` and verify they're still in `users` and
`is_active=True`. If the roster is empty or no candidate is active, we
return None — caller must decide whether to block the visit.
"""
from __future__ import annotations

import uuid

import httpx

from app.core.config import settings
from app.models.visit import AccessArea, Visit, VisitStatus
from app.services import area_rules

# Areas that trigger approval-api involvement at all.
_NEEDS_APPROVAL = area_rules.APPROVAL_AREAS

# Areas that additionally require a Quality Manager step.
_NEEDS_QUALITY_MANAGER = area_rules.QUALITY_MANAGER_AREAS


def access_requires_approval(area: AccessArea) -> bool:
    return area in _NEEDS_APPROVAL


def access_requires_quality_manager(area: AccessArea) -> bool:
    return area in _NEEDS_QUALITY_MANAGER


def approval_tier(area: AccessArea) -> int:
    """How much approval an area needs: 0 none, 1 Department Manager,
    2 Department Manager + Quality Manager. An approved visit may move to an
    area of the same or a lower tier without new approval, never higher."""
    if area in _NEEDS_QUALITY_MANAGER:
        return 2
    if area in _NEEDS_APPROVAL:
        return 1
    return 0


# Engine states in which the approval has not finished. "draft" = never
# reached the engine (first submit failed); "returned" = sent back for edit.
IN_FLIGHT_STATES = frozenset({"draft", "submitted", "in_review", "returned"})
# States from which the creator can (re)submit.
RESUBMITTABLE_STATES = frozenset({"draft", "returned"})


def pick_quality_manager(
    *, roster: list[str] | list[uuid.UUID], active_user_ids: set[uuid.UUID]
) -> uuid.UUID | None:
    """First-active strategy. Returns the first UUID in `roster` that is
    also in `active_user_ids`. Returns None if no candidate qualifies.

    The active set is supplied by the caller (one extra query) to keep this
    function pure + unit-testable without DB.
    """
    for raw in roster:
        try:
            uid = uuid.UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            continue
        if uid in active_user_ids:
            return uid
    return None


def make_visit_title(*, first_name: str, last_name: str, company: str) -> str:
    """Format the title field that ends up in the Portal task inbox."""
    suffix = f" ({company})" if company.strip() else ""
    label = f"VMS Visit — {first_name} {last_name}{suffix}".strip()
    return label[:255]


async def submit_for_approval(visit_id: uuid.UUID, bearer_token: str) -> dict:
    """POST to approval-api's submit action.

    Caller must have already flushed the Visit row's `approval_status="draft"`
    + `visit_title` + (optional) `quality_approver_id` to the DB so the engine
    reads consistent state.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.APPROVAL_ENGINE_URL}/approvals/vms_visit/{visit_id}/action",
            json={"action": "submit"},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def prepare_and_submit(db, row: Visit, bearer_token: str) -> str | None:
    """Put `row` into Pending Approval and hand it to approval-api.

    Used for the first submit (create) and every resubmit. Returns None on
    success, or the error text when approval-api could not be reached / refused.

    On failure the visit STAYS in Pending Approval with `approval_status`
    "draft" (or "returned"), so it cannot be badged until it is submitted
    successfully — the creator retries with POST /visits/{id}/submit. It used
    to be reset to `confirmed`, which let a visit print without any approval.
    """
    from sqlalchemy import select

    from app.crud import visitor as visitor_crud
    from app.models.user_mirror import User
    from app.models.vms_config import VmsConfig

    visitor = await visitor_crud.get_visitor(db, row.visitor_id)
    if visitor is not None:
        row.visit_title = make_visit_title(
            first_name=visitor.first_name,
            last_name=visitor.last_name,
            company=visitor.company_name,
        )
    if row.approval_status not in RESUBMITTABLE_STATES:
        row.approval_status = "draft"
    row.status = VisitStatus.pending_approval

    # Re-resolved on every submit: the area may have changed since a return.
    row.quality_approver_id = None
    if access_requires_quality_manager(row.access_area):
        cfg = (await db.execute(select(VmsConfig).limit(1))).scalar_one_or_none()
        roster = (cfg.quality_manager_user_ids if cfg else []) or []
        try:
            roster_uuids = [uuid.UUID(str(r)) for r in roster]
        except (ValueError, TypeError):
            roster_uuids = []
        if roster_uuids:
            active = (await db.execute(
                select(User.id).where(User.id.in_(roster_uuids), User.is_active.is_(True))
            )).scalars().all()
            row.quality_approver_id = pick_quality_manager(
                roster=roster, active_user_ids=set(active),
            )

    # COMMIT before the HTTP call so approval-api's own session sees the row.
    await db.commit()
    try:
        await submit_for_approval(row.id, bearer_token)
    except Exception as e:  # noqa: BLE001 — any failure leaves the visit unsubmitted
        await db.refresh(row)
        return str(e) or e.__class__.__name__
    await db.refresh(row)

    # The engine does not close the creator's "Revise" task on resubmit.
    from app.services.visit_tasks import close_open_visit_tasks
    await close_open_visit_tasks(db, row.id, types=("revise_vms_visit",))
    return None


async def run_action(
    visit_id: uuid.UUID, *, action: str, bearer_token: str, comment: str | None = None,
) -> dict:
    """POST any approval-engine action (approve / reject / return) for a visit.

    Used by VMS's approval UI when the assigned approver clicks Approve /
    Reject / Return on VisitDetailPage. Engine validates that the actor is
    the assigned approver — vms-api just forwards the bearer token.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.APPROVAL_ENGINE_URL}/approvals/vms_visit/{visit_id}/action",
            json={"action": action, "comment": comment},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def cancel_approval(visit_id: uuid.UUID, bearer_token: str) -> dict | None:
    """POST cancel to approval-api. Tolerates 422 (visit was never submitted)
    by returning None — caller doesn't need to know whether the engine had a
    record of the visit."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.APPROVAL_ENGINE_URL}/approvals/vms_visit/{visit_id}/action",
            json={"action": "cancel"},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        if resp.status_code in (404, 422):
            return None
        resp.raise_for_status()
        return resp.json()


# ── State sync helpers (engine state ↔ VMS user-facing state) ───────────────-

_TERMINAL_APPROVAL_STATES = frozenset({"approved", "rejected", "cancelled"})


def sync_status_from_approval(visit: Visit) -> Visit:
    """Translate engine-state writes back into the VMS-facing VisitStatus.

    Engine writes `approval_status` (per S2_ARCHITECTURE_REVIEW.md F1). When
    it reaches a terminal state we mirror it onto `Visit.status` so the rest
    of vms-api (badge guards, dashboard counters, etc.) doesn't have to know
    about the parallel field.

    Safe to call on every read — only flips when there's something to flip.
    The `_post_approve_vms_visit` hook in approval-api flips `status` to
    `confirmed` directly on approval, but reject / cancel rely on this
    read-side mirror because the engine has no post-reject / post-cancel hook.
    """
    if visit.status != VisitStatus.pending_approval:
        return visit
    if visit.approval_status in ("rejected", "cancelled"):
        visit.status = VisitStatus.cancelled
    elif visit.approval_status == "approved":
        # approval-api's _post_approve_vms_visit callback should have already
        # done this via raw SQL; this branch is a belt-and-braces fallback.
        visit.status = VisitStatus.confirmed
    return visit
