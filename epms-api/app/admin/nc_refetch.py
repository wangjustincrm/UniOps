"""Standing re-fetch requests for the NC purchase mirror.

Deleting a mirrored purchase order in Data Maintenance removes the UniOps row and
nothing else — NC's own copy is not touched, so its change time does not move.
The incremental sync filters on ``changed_at >= watermark``, and the watermark is
already past that order, so the sync never looks at it again: the document is
gone from UniOps while the ERP still lists it as live, with no error, no task and
no way back short of an operator running ``rewind_nc_purchase_watermark`` by
hand. That is how PO-058-2607-02 vanished.

A request row is what closes the loop: the reader unions these pks into the next
incremental fetch regardless of the watermark, and the service settles them once
the order is either mirrored again or confirmed gone from NC.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nc_purchase_sync import NcPurchaseRefetchRequest


async def request_nc_refetch(db: AsyncSession, po, *, reason: str) -> dict[str, int]:
    """Ask the next incremental sync to re-read the NC order behind `po`.

    A no-op for anything the mirror did not write: a PMS-imported or
    UniOps-native PO has no NC order to go back to, and requesting one would put
    a pk in the queue that NC has never heard of.

    Re-opens an existing request rather than queueing a second one — an order can
    be deleted, re-mirrored and deleted again, and the second delete needs the
    same row live again, not a duplicate the unique constraint would reject.

    Returns a cascade-summary fragment so the admin's confirm dialog can say the
    order is coming back, instead of leaving them to believe a delete they have
    to undo by hand.
    """
    if getattr(po, "source", None) != "nc" or not getattr(po, "nc_source_pk", None):
        return {}
    stmt = insert(NcPurchaseRefetchRequest).values(
        nc_source_pk=po.nc_source_pk, po_number=po.number, reason=reason,
    ).on_conflict_do_update(
        index_elements=[NcPurchaseRefetchRequest.nc_source_pk],
        # updated_at is set by hand: the mixin's ``onupdate`` fires on a Core
        # UPDATE, not on the DO UPDATE half of an upsert.
        set_={"po_number": po.number, "reason": reason,
              "fulfilled_at": None, "outcome": None, "updated_at": func.now()},
    )
    await db.execute(stmt)
    return {"nc_orders_queued_for_resync": 1}


def previews_nc_refetch(po) -> dict[str, int]:
    """The preview twin of ``request_nc_refetch`` — same condition, no write.

    The confirm dialog is built from the preview, so a request the delete WILL
    raise has to be visible before the admin presses the button; otherwise the
    only place it ever appears is the audit log afterwards.
    """
    if getattr(po, "source", None) != "nc" or not getattr(po, "nc_source_pk", None):
        return {}
    return {"nc_orders_queued_for_resync": 1}
