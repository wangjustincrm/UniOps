"""Who owns a PR's service receipt — the single definition of that fallback.

`purchase_requests.owner_id` is nullable and NULL means "the requester"
(app/models/pr.py explains why there is no backfill). Every routing point that
used to read `pr.created_by` to decide who confirms a service delivery reads
these helpers instead, so the fallback cannot drift between the three paths
that raise the same confirm_receipt task:

  * app/tasks/service_gr_due.py      — the completion-date sweep
  * app/api/v1/invoices.py           — the invoice-matched nudge
  * app/crud/gr.py                   — the GR acknowledge/confirm chain

Physical procurement types never carry an owner, and that is enforced HERE
rather than left to the form: `owner_id` is honoured only for the service pair
(`schemas.gr.SERVICE_TYPES`). The form only offers the field for those two, but
a PR whose type is edited from 4 to 2 while in draft keeps whatever owner_id it
already had (PATCH treats an absent field as "leave alone"), and without the
type check that stale row would quietly route a warehouse `collect_goods` task
to a service owner. So on every physical path these helpers return exactly what
`created_by` returned before — by construction.

Deliberately NOT merged into `crud.gr.get_pr_requester_id`: that function also
answers "who raised this" for the PO detail response, and conflating the two
would quietly move the requester label onto the owner.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pr import PurchaseRequest
from app.schemas.gr import SERVICE_TYPES


def _resolve(pr_type, owner_id, created_by):
    """owner_id when this type collects one, else the requester."""
    if pr_type in SERVICE_TYPES and owner_id is not None:
        return owner_id
    return created_by


def owner_id_of(pr: PurchaseRequest) -> uuid.UUID:
    """The PR's service owner: its explicit owner_id, else its requester."""
    return _resolve(pr.type, pr.owner_id, pr.created_by)


async def get_pr_owner_id(
    db: AsyncSession, pr_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Same, by id. None only when there is no PR at all (imported PO) — which
    is the same condition under which `crud.gr.get_pr_requester_id` returns
    None, so callers that branch on "no requester → skip the chain" keep
    behaving identically.
    """
    if not pr_id:
        return None
    row = (await db.execute(
        select(PurchaseRequest.type, PurchaseRequest.owner_id,
               PurchaseRequest.created_by)
        .where(PurchaseRequest.id == pr_id)
    )).first()
    if row is None:
        return None
    pr_type, owner_id, created_by = row
    return _resolve(pr_type, owner_id, created_by)
