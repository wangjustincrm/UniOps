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


# An invoice in one of these has been matched to its PO: matching is the only
# way to `matched`, and paying is the only way on from it (PAY-005).
PASSED_MATCHING = ("matched", "paid", "partially_paid")


def _matched_on_header(po_id_expr, statuses=("matched",)):
    return exists(select(Invoice.id).where(
        Invoice.po_id == po_id_expr, Invoice.status.in_(statuses)))


def _matched_by_allocation(po_id_expr, statuses=("matched",)):
    return exists(select(InvoicePoAllocation.invoice_id)
                  .join(Invoice, Invoice.id == InvoicePoAllocation.invoice_id)
                  .where(InvoicePoAllocation.po_id == po_id_expr,
                         Invoice.status.in_(statuses)))


def three_way_matched(po_id_expr, statuses=("matched",)):
    """The PO has a matched invoice AND something received against it (PRD
    INV-3WM-006) — the PA receipt gate, create_pa and the confirm-receipt nudge.

    A header invoice that carries a GR counts on its own. Otherwise a matched
    invoice on the header or by line allocation counts once the PO has receipt
    evidence (a live GR or a received quantity): an allocated invoice's gr_id
    may belong to another PO, and PMS-migrated POs have quantities but no GR.

    `statuses` are the invoice statuses that count. The gate takes only
    `matched` — an invoice still waiting for its PA; once paid it can go on no
    other PA. "Is this PO three-way matched" asked afterwards is
    three_way_match_passed.
    """
    return or_(
        exists(select(Invoice.id).where(
            Invoice.po_id == po_id_expr, Invoice.status.in_(statuses),
            Invoice.gr_id.is_not(None))),
        and_(or_(_matched_on_header(po_id_expr, statuses),
                 _matched_by_allocation(po_id_expr, statuses)),
             receipt_evidence(po_id_expr)),
    )


def three_way_match_passed(po_id_expr):
    """The same test, counting invoices that were matched and have since been
    paid. A PO whose matched invoice was paid read "not three-way matched", and
    the Assistant reported the match as failed (ISS-0036: INV-2026-0631, Paid,
    with GR-20261009-0024 linked)."""
    return three_way_matched(po_id_expr, PASSED_MATCHING)


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
