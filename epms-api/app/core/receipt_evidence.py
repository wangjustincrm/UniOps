"""Has anything been received against a purchase order — one definition.

Used by crud.po.po_has_receipt_evidence (the PA receipt gate, create_pa and the
confirm-receipt nudge) and by the Invoice.po_receipt_recorded expression the
Assistant queries (ISS-0013). Written once as a SQL condition on a PO id
expression so the two can never disagree about what counts as received:

  1. a live goods receipt on the PO (cancelled / rejected never count), or
  2. a PO line with received_qty > 0 — the only form a PMS-migrated PO carries.
"""
from sqlalchemy import exists, or_, select

from app.models.gr import GoodsReceipt
from app.models.po import PoLineItem
from app.schemas.gr import DEAD_GR_STATUSES


def receipt_evidence(po_id_expr):
    """SQL condition: the PO identified by `po_id_expr` has receipt evidence."""
    return or_(
        exists(select(GoodsReceipt.id).where(
            GoodsReceipt.po_id == po_id_expr,
            GoodsReceipt.status.not_in(DEAD_GR_STATUSES))),
        exists(select(PoLineItem.id).where(
            PoLineItem.po_id == po_id_expr, PoLineItem.received_qty > 0)),
    )
