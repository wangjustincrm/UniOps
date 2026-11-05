"""Facts about a purchase order, each written once as a SQL condition.

Used by the code paths that act on them and by the Assistant's ontology
(hybrid properties on the models), so the two cannot answer differently. The
Assistant used to rebuild these from joins on every question and got a
different answer each time (ISS-0036, ISS-0058, ISS-0060).
"""
from sqlalchemy import and_, exists, or_, select

from app.core.receipt_evidence import receipt_evidence
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation


def _matched_on_header(po_id_expr):
    return exists(select(Invoice.id).where(
        Invoice.po_id == po_id_expr, Invoice.status == "matched"))


def _matched_by_allocation(po_id_expr):
    return exists(select(InvoicePoAllocation.invoice_id)
                  .join(Invoice, Invoice.id == InvoicePoAllocation.invoice_id)
                  .where(InvoicePoAllocation.po_id == po_id_expr,
                         Invoice.status == "matched"))


def three_way_matched(po_id_expr):
    """The PO has a matched invoice AND something received against it (PRD
    INV-3WM-006) — the PA receipt gate, create_pa and the confirm-receipt nudge.

    A header invoice that carries a GR counts on its own. Otherwise a matched
    invoice on the header or by line allocation counts once the PO has receipt
    evidence (a live GR or a received quantity): an allocated invoice's gr_id
    may belong to another PO, and PMS-migrated POs have quantities but no GR.
    """
    return or_(
        exists(select(Invoice.id).where(
            Invoice.po_id == po_id_expr, Invoice.status == "matched",
            Invoice.gr_id.is_not(None))),
        and_(or_(_matched_on_header(po_id_expr), _matched_by_allocation(po_id_expr)),
             receipt_evidence(po_id_expr)),
    )


def has_invoice(po_id_expr):
    """The PO has at least one invoice that is not cancelled, on its header or
    by line allocation — the Document Chain's view of "invoiced". Decided at PO
    level by the product owner on 2026-10-21: many invoices name only the PO,
    not the goods receipt, so a receipt-level test lists invoiced receipts as
    "not invoiced" (ISS-0058)."""
    return or_(
        exists(select(Invoice.id).where(
            Invoice.po_id == po_id_expr, Invoice.status != "cancelled")),
        exists(select(InvoicePoAllocation.invoice_id)
               .join(Invoice, Invoice.id == InvoicePoAllocation.invoice_id)
               .where(InvoicePoAllocation.po_id == po_id_expr,
                      Invoice.status != "cancelled")),
    )
