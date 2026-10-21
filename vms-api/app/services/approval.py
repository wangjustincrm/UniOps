"""Bridge between vms-api business logic and approval-api engine.

vms-api delegates the actual approval workflow to approval-api. This module
decides whether a visit needs approval at all (whether its zone has managers),
snapshots the zone's managers onto the visit for the workflow's Zone Manager
step, and triggers the engine's submit/cancel actions over HTTP.

Per S2_ARCHITECTURE_REVIEW.md F1 + locked decision #1, vms-api sets:
  - `Visit.status = pending_approval`  (user-facing VisitStatus enum)
  - `Visit.approval_status = "draft"`  (engine vocabulary, lives on same row)
  - `Visit.visit_title = "VMS Visit — {first} {last} ({company})"`  (Portal label)
  - `Visit.zone_approver_ids = [<UUID>, …]` the zone's active managers

…BEFORE the HTTP POST to approval-api, so the engine's session reads
consistent state from the shared DB.

Which steps the workflow has (Department Manager, Zone Manager, …) is set in
the approval engine (Portal → Admin → Approval Workflows), not here. The Zone
Manager step creates one task per manager in `zone_approver_ids`; the first to
act decides. (Before 2026-10 that step was "Quality Manager", resolved from a
single VMS-wide roster; the engine still accepts that role name as an alias.)
"""
from __future__ import annotations

import uuid

import httpx

from app.core.config import settings
from app.models.visit import Visit, VisitStatus
from app.services import area_rules


class ZoneNotRoutable(Exception):
    """The zone needs approval but none of its managers is an active user."""


async def access_requires_approval(db, code: str) -> bool:
    rules = await area_rules.rules_for(db, code)
    return rules is not None and rules.requires_approval


async def may_move_confirmed_visit(db, row: Visit, new_code: str) -> bool:
    """May an already-approved (or approval-free) visit move to `new_code`
    without going through approval again?

    Yes when the new zone needs no approval, or when every active manager of
    the new zone was already among those the visit was routed to — i.e. the
    people who now own the zone already had this visit in front of them.
    Moving into a zone with anyone new in charge needs a new booking.
    """
    rules = await area_rules.rules_for(db, new_code)
    if rules is None or not rules.requires_approval:
        return True
    if not rules.active_manager_ids:
        return False
    routed = {str(u) for u in (row.zone_approver_ids or [])}
    return all(str(u) in routed for u in rules.active_manager_ids)


# Engine states in which the approval has not finished. "draft" = never
# reached the engine (first submit failed); "returned" = sent back for edit.
IN_FLIGHT_STATES = frozenset({"draft", "submitted", "in_review", "returned"})
# States from which the creator can (re)submit.
RESUBMITTABLE_STATES = frozenset({"draft", "returned"})


# approval-api copies the title into tasks.document_number and
# approval_events.document_number, both varchar(40) (epms-api owns them). A
# longer title made every submit fail with a 500 (prod 2026-10-15: "VMS Visit
# — Axel Struzina (Malette Electric)", 44 characters).
TITLE_MAX = 40


def make_visit_title(*, first_name: str, last_name: str, company: str) -> str:
    """Format the title field that ends up in the Portal task inbox."""
    suffix = f" ({company})" if company.strip() else ""
    label = f"VMS Visit — {first_name} {last_name}{suffix}".strip()
    if len(label) > TITLE_MAX:
        label = label[:TITLE_MAX - 1].rstrip() + "…"
    return label


async def submit_for_approval(visit_id: uuid.UUID, bearer_token: str) -> dict:
    """POST to approval-api's submit action.

    Caller must have already flushed the Visit row's `approval_status="draft"`
    + `visit_title` + `zone_approver_ids` to the DB so the engine
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
    from app.crud import visitor as visitor_crud

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

    # Re-snapshotted on every submit: the zone, or its managers, may have
    # changed since a return.
    rules = await area_rules.rules_for(db, row.access_area)
    approvers = list(rules.active_manager_ids) if rules else []
    row.zone_approver_ids = [str(u) for u in approvers]
    row.quality_approver_id = None
    if not approvers:
        # Configured managers but none active: nobody to route the Zone Manager
        # step to. Stop here instead of letting the engine auto-skip the step.
        await db.commit()
        await db.refresh(row)
        name = rules.name if rules else row.access_area
        return (
            f"No active manager is set for zone '{name}'. Ask a VMS admin to "
            f"update the zone's managers (VMS Admin → Zones), then submit again."
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
