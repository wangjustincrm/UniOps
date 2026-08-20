"""The four-step document chain behind one invoice.

Match PO → Link GR → Create PA → Payment, as the Invoice List's Due Date
drawer shows it. This is a READ-ONLY projection of state the matching, receipt
and payment paths already wrote; nothing here decides anything, and no step is
a permission check on the ACTION it names (whether *this* caller may raise a
PA is decided by the PA endpoints, not by a drawer).

Two of the four steps cannot be answered from the invoice row alone:

  - Link GR: the invoice stores gr_ids as a JSONB array of id strings with no
    FK, so the GR numbers have to be looked up.
  - Create PA / Payment: PaymentApplication.invoice_ids is likewise a JSONB
    array with no FK back to invoices — the array IS the only link there is,
    which is why the PA list endpoint cannot filter by invoice and this module
    exists at all.
"""
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice

# Deliberate reuse rather than a second copy of the predicate: this is the one
# place in the codebase that defines "a PA still holds a claim on this invoice"
# (non-cancelled + JSONB containment), and the receipt-evidence gate in
# crud/invoice.py enforces payment decisions with it. A drawer that answered
# "has a PA been raised?" differently from the gate that blocks pulling the
# evidence out from under one would be actively misleading.
from app.crud.invoice import _invoice_referenced_by_active_pa as _active_pa

# Step states. `pending` = this is the outstanding action; `blocked` = an
# earlier step has to land first; `not_applicable` = this route never has this
# step; `restricted` = the caller is not admitted to the module that owns it.
DONE = "done"
PENDING = "pending"
BLOCKED = "blocked"
NOT_APPLICABLE = "not_applicable"
RESTRICTED = "restricted"

# A PA that has reached this status has cleared approval and is waiting on the
# payment run; anything earlier is still working through the approval chain.
_PA_APPROVED = "approved"
# finance-api's payment executor moves a PA here once money has actually moved.
_PA_PAID = "processed"


def _ref(doc_type: str, doc_id: Any, number: str | None) -> dict:
    return {"doc_type": doc_type, "id": str(doc_id), "number": number}


def _step(key: str, state: str, refs: list[dict] | None = None,
          detail: str | None = None) -> dict:
    return {"key": key, "state": state, "detail": detail, "refs": refs or []}


def _linked_gr_ids(invoice: Invoice) -> list[uuid.UUID]:
    """gr_ids is the full set; gr_id/gr_number hold the first one for backward
    compatibility, so an older row may carry only the scalar."""
    raw = invoice.gr_ids or ([str(invoice.gr_id)] if invoice.gr_id else [])
    out: list[uuid.UUID] = []
    for value in raw:
        try:
            out.append(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


async def build_chain(
    db: AsyncSession, invoice: Invoice, *, can_view_pa: bool,
) -> dict:
    steps: list[dict] = []

    # ── 1. Match PO ───────────────────────────────────────────────────────────
    # An agreement-route invoice is matched just as completely as a PO-route
    # one — it is matched to a different kind of document.
    if invoice.po_id is not None:
        matched = True
        steps.append(_step("match_po", DONE,
                           [_ref("po", invoice.po_id, invoice.po_number)]))
    elif invoice.agreement_id is not None:
        matched = True
        steps.append(_step("match_po", DONE,
                           [_ref("agreement", invoice.agreement_id,
                                 invoice.agreement_number)]))
    else:
        matched = False
        steps.append(_step("match_po", PENDING))

    # ── 2. Link GR ────────────────────────────────────────────────────────────
    on_agreement_route = invoice.po_id is None and invoice.agreement_id is not None
    if not matched:
        gr_state = BLOCKED
        steps.append(_step("link_gr", BLOCKED))
    elif on_agreement_route:
        # Agreement invoices are reconciled against agreement receipts; there
        # is no goods receipt to wait for and never will be.
        gr_state = NOT_APPLICABLE
        steps.append(_step("link_gr", NOT_APPLICABLE))
    else:
        gr_ids = _linked_gr_ids(invoice)
        if gr_ids:
            rows = (await db.execute(
                select(GoodsReceipt.id, GoodsReceipt.number)
                .where(GoodsReceipt.id.in_(gr_ids))
                .order_by(GoodsReceipt.number)
            )).all()
            gr_state = DONE
            steps.append(_step("link_gr", DONE,
                               [_ref("gr", r.id, r.number) for r in rows]))
        else:
            gr_state = PENDING
            steps.append(_step("link_gr", PENDING))

    # ── 3. Create PA / 4. Payment ─────────────────────────────────────────────
    # Both live in the PA module; a caller the Access Control Matrix keeps out
    # of it must not read payment state through this drawer instead.
    if not can_view_pa:
        steps.append(_step("create_pa", RESTRICTED))
        steps.append(_step("payment", RESTRICTED))
        return _envelope(invoice, steps)

    pa = await _active_pa(db, invoice.id)

    if pa is not None:
        steps.append(_step("create_pa", DONE,
                           [_ref("pa", pa.id, pa.pa_number)], detail=pa.status))
    elif matched and gr_state in (DONE, NOT_APPLICABLE):
        # Matched and received: raising the PA is the outstanding action. The
        # receipt gate (crud/pa.py) is what actually enforces this, and it
        # accepts an authorised override — so this is "what is outstanding",
        # not "what is forbidden".
        steps.append(_step("create_pa", PENDING))
    else:
        steps.append(_step("create_pa", BLOCKED))

    if invoice.status == "paid":
        # The invoice's own status only flips here once money has moved, so it
        # is authoritative even for the zero-cash settlement path, where no
        # payment run executes against a PA at all.
        steps.append(_step("payment", DONE,
                           detail=pa.paid_at.isoformat() if pa is not None and pa.paid_at else None))
    elif pa is None:
        steps.append(_step("payment", BLOCKED))
    elif pa.paid_at is not None or pa.status == _PA_PAID:
        steps.append(_step("payment", DONE,
                           detail=pa.paid_at.isoformat() if pa.paid_at else None))
    elif pa.status == _PA_APPROVED:
        steps.append(_step("payment", PENDING))
    else:
        steps.append(_step("payment", BLOCKED))

    return _envelope(invoice, steps)


def _envelope(invoice: Invoice, steps: list[dict]) -> dict:
    return {
        "invoice_id": invoice.id,
        "internal_ref": invoice.internal_ref,
        "status": invoice.status,
        "due_date": invoice.due_date,
        "steps": steps,
    }
