"""Build + push a normalized AP invoice to finance ap_invoices (Plan 2).

EPMS owns the rich invoice (PO 3-way match, allocations, tax lines); finance
owns the normalized AP header. On every lifecycle change we upsert a JSON-ready
snapshot into finance (fail-open via finance_client).

Deleting an invoice is the exception: it is NOT fail-open. A deleted EPMS
invoice whose Finance row stays open is an orphan — a payable (and, once
matched, a GL accrual) for a document that no longer exists, which also
blocks the vendor + number for any re-upload, so the real invoice never gets
a Finance row of its own. Data Maintenance deleted invoices without telling
Finance at all; by 2026-10-21 that had left five orphans (INV-2026-0235,
0321, 0618, 0651, 0653), one of them Sprim's 8,400.02 carried twice.

So every invoice row the ORM deletes is caught here (before_flush), and the
request may not commit until void_deleted_invoices() has had Finance void each
one — a delete path that forgets is refused at commit (before_commit), not
left to drift. Only request sessions (get_session) are held to this; scripts
and test fixtures with their own sessions are not.
"""
import uuid

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.invoice import Invoice
from app.models.invoice_tax_line import InvoiceTaxLine
from app.services import finance_client


def _ap_status(invoice_status: str, void: bool) -> str:
    if void:
        return "void"
    if invoice_status in ("matched", "approved"):
        return "posted"
    if invoice_status == "paid":
        return "paid"
    return "draft"   # unmatched / exception / anything else


def _header(invoice: Invoice, void: bool) -> dict:
    return {
        "source": "epms",
        "source_invoice_id": str(invoice.id),
        "source_ref": invoice.internal_ref,
        "vendor_id": str(invoice.vendor_id) if invoice.vendor_id else None,
        "vendor_name": invoice.vendor_name,
        "vendor_invoice_number": invoice.vendor_invoice_number,
        "amount": str(invoice.amount),
        "tax_amount": str(invoice.tax_amount),
        "total_amount": str(invoice.total_amount),
        "currency": invoice.currency,
        "invoice_date": invoice.invoice_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "status": _ap_status(invoice.status, void),
        "source_status": invoice.status,
        "po_id": str(invoice.po_id) if invoice.po_id else None,
        "po_number": invoice.po_number,
        "tax_lines": [],
    }


async def sync_ap_invoice(db: AsyncSession, invoice: Invoice, bearer_token: str,
                          *, void: bool = False) -> None:
    """Upsert this EPMS invoice's normalized header+tax into finance. Fail-open."""
    tax_rows = (await db.execute(
        select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == invoice.id)
        .order_by(InvoiceTaxLine.line_no)
    )).scalars().all()

    payload = _header(invoice, void)
    payload["tax_lines"] = [
        {"line_no": t.line_no, "tax_code": t.tax_code,
         "taxable_base": str(t.taxable_amount) if t.taxable_amount is not None else "0",
         "tax_amount": str(t.tax_amount), "recoverable": t.recoverable}
        for t in tax_rows
    ]
    await finance_client.upsert_ap_invoice(payload=payload, bearer_token=bearer_token)


# ── Deletes: Finance must void, or the delete does not happen ────────────────

PENDING_VOIDS = "finance_sync.pending_ap_voids"
STRICT = "finance_sync.strict"


class FinanceVoidFailed(Exception):
    """→ HTTP 502. Finance did not void a deleted invoice; nothing was deleted."""

    def __init__(self, refs: list[str], cause: str, already_voided: list[str]):
        self.refs, self.already_voided = refs, already_voided
        msg = (f"Finance could not void the AP record of {', '.join(refs)} ({cause}), "
               "so nothing was deleted. Try again in a moment.")
        if already_voided:
            # A batch that failed part-way: Finance has voided these and EPMS is
            # rolling them back. Rare (Finance down mid-batch); say so loudly.
            msg += (f" Finance had already voided {', '.join(already_voided)} — tell the "
                    "finance admin to re-sync them (scripts.reconcile_epms_ap).")
        super().__init__(msg)


@event.listens_for(Session, "before_flush")
def _collect_deleted_invoices(session: Session, flush_context, instances) -> None:
    for obj in session.deleted:
        if isinstance(obj, Invoice):
            session.info.setdefault(PENDING_VOIDS, []).append(_header(obj, void=True))


@event.listens_for(Session, "before_commit")
def _refuse_unvoided_deletes(session: Session) -> None:
    if not session.info.get(STRICT):
        return
    # before_commit runs BEFORE commit's own flush, so a delete still pending
    # in the session (not flushed yet) has not reached before_flush: count it.
    refs = [p["source_ref"] for p in session.info.get(PENDING_VOIDS, [])]
    refs += [o.internal_ref for o in session.deleted if isinstance(o, Invoice)]
    if refs:
        raise RuntimeError(
            f"Invoice(s) {', '.join(refs)} deleted without voiding them in Finance — the "
            "delete path must await finance_sync.void_deleted_invoices() before commit")


async def void_deleted_invoices(db: AsyncSession, bearer_token: str) -> list[str]:
    """Have Finance void every invoice this session deleted. Call after the
    delete has flushed and before commit; raises FinanceVoidFailed (the caller
    turns it into a 502 and the request rolls back). Returns the refs voided."""
    await db.flush()
    pending = db.info.get(PENDING_VOIDS, [])
    done: list[str] = []
    for payload in pending:
        try:
            await finance_client.upsert_ap_invoice_strict(payload=payload, bearer_token=bearer_token)
        except finance_client.FinanceSyncError as e:
            raise FinanceVoidFailed([payload["source_ref"]], str(e), done) from e
        done.append(payload["source_ref"])
    db.info[PENDING_VOIDS] = []
    return done
