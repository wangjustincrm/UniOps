"""A purchase from requisition to payment, as one read-only projection (ISS-0030).

"PR-20260928-0001 从请购到付款的完整进度是怎样的？" was answered with the PR's
approval history and "it has now entered the payment stage" — invented. The
chain was there: PR approved → PO-535-2609-02 partially received →
GR-20261009-0024 collected → INV-2026-0631 matched → PA-20261013-0003
approved, payment pending. Nothing on the server put it together: the PO
page's Document Chain is assembled in the browser by DocumentChainTree.tsx
from five list calls, and the assistant's planner runs one query per question.

This builds the same chain on the server, with the PO as the pivot exactly as
DocumentChainTree does (an agreement for agreement-route invoices and PAs):

  PR ─ PO ─┬─ goods receipts (cancelled ones left out, as the tree does)
           ├─ invoices (on the PO's header or by line allocation)
           └─ payment applications (any PA paying the PO, primary or not —
              crud.pa_links), each with the invoices it pays

Every document is shown only if the caller could query it: each passes its own
ontology entity's permission gate and row scope. A layer whose permission the
caller lacks is reported as not visible rather than as empty. The facts about
each PO — received, invoiced, PA raised, three-way matched — are the
ontology's hybrid properties (core/po_facts, core/receipt_evidence), so they
answer exactly as a query would.
"""
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.core import doc_numbers
from app.core.delegation import PLANT_TIMEZONE
from app.core.ontology import REGISTRY, may_view
from app.core.receipt_evidence import receipt_evidence
from app.crud.current_step import enrich_current_step
from app.crud.pa_links import pa_ids_for_po, po_ids_of_pa
from app.models.agreement import PurchaseAgreement
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest

SUPPORTED = ("pr", "po", "gr", "invoice", "pa", "agreement")

_ENTITY = {
    "pr": "purchase_request", "po": "purchase_order", "gr": "goods_receipt",
    "invoice": "invoice", "pa": "payment_application", "agreement": "purchase_agreement",
}
_MODEL = {
    "pr": (PurchaseRequest, "number"), "po": (PurchaseOrder, "number"),
    "gr": (GoodsReceipt, "number"), "invoice": (Invoice, "internal_ref"),
    "pa": (PaymentApplication, "pa_number"), "agreement": (PurchaseAgreement, "number"),
}
# doc_numbers prefixes for normalising a typed number.
_PREFIX = {"pr": "PR", "po": "PO", "gr": "GR", "invoice": "INV", "pa": "PA",
           "agreement": "AGR"}

# Status names as the Document Chain shows them (epms/src/components/shared/
# DocumentChainTree.tsx — tests/test_assistant_iss_batch4.py fails when the two
# drift), so the answer uses the words on the screen it is compared with.
STATUS_LABELS: dict[str, dict[str, str]] = {
    "gr": {
        "pending_ack": "Pending Ack.",
        "collection_pending": "Collection Pending",
        "collected": "Collected",
        "confirmed": "Confirmed",
        "discrepancy": "Discrepancy",
        "rejected": "Rejected",
        "cancelled": "Cancelled",
    },
    "invoice": {
        "unmatched": "Unmatched",
        "matched": "Matched",
        "exception": "Exception",
        "match_review": "Pending Review",
        "approved": "Approved",
        "paid": "Paid",
    },
    "pa": {
        "draft": "Draft",
        "submitted": "Submitted",
        "in_review": "In Review",
        "approved": "Approved",
        "processed": "Processed",
        "returned": "Returned",
        "rejected": "Rejected",
        "cancelled": "Cancelled",
    },
    "po": {
        "draft": "Draft",
        "submitted": "Submitted",
        "pending_approval": "Pending Approval",
        "approved": "Approved",
        "issued": "Issued",
        "partially_received": "Partially Received",
        "fully_received": "Fully Received",
        "cancelled": "Cancelled",
        "closed": "Closed",
        "nc_milk": "Milk / NC",
        "nc_pending": "NC Pending Approval",
    },
}

# A long chain (a blanket PO received fifty times) is listed in part; the
# counts are always whole.
MAX_PER_LAYER = 15


def _label(doc_type: str, status: str | None) -> str | None:
    if status is None:
        return None
    return STATUS_LABELS.get(doc_type, {}).get(status) or status.replace("_", " ").title()


_PLANT_TZ = ZoneInfo(PLANT_TIMEZONE)


def _date(value):
    """A day as the plant reads it: a timestamp in plant time (a GR received at
    22:00 in Toronto is that day, not the next UTC one), a date as it is."""
    if value is None:
        return None
    if hasattr(value, "astimezone"):
        value = (value.astimezone(_PLANT_TZ) if value.tzinfo else value).date()
    return value.isoformat()


def _money(value):
    return str(value) if value is not None else None


async def _visible(db, scope: dict, doc_type: str, *conditions, order_by=None) -> list | None:
    """Rows of one kind the caller may see, or None when they may not see that
    kind at all (the Access Control Matrix keeps them out of it)."""
    entity = REGISTRY[_ENTITY[doc_type]]
    if not may_view(entity, scope.get("perms") or {}):
        return None
    model = _MODEL[doc_type][0]
    stmt = await entity.apply_scope(select(model).where(*conditions), scope, db)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    return list((await db.execute(stmt)).scalars().all())


async def _waiting_on(db, doc_type: str, docs: list) -> None:
    """Who a document still in approval is with — the same open-task read the
    lists' Current Step column uses."""
    if docs:
        await enrich_current_step(db, doc_type, docs)


def _step(doc) -> dict | None:
    current = getattr(doc, "current_step", None)
    if not current:
        return None
    return {"step": current.get("label"), "approver": current.get("approver_name")}


def _layer(rows: list | None, render) -> dict:
    if rows is None:
        return {"not_visible": True}
    out = {"count": len(rows), "items": [render(r) for r in rows[:MAX_PER_LAYER]]}
    if len(rows) > MAX_PER_LAYER:
        out["listed"] = MAX_PER_LAYER
    return out


def _pr(pr) -> dict:
    return {"number": pr.number, "status": pr.status, "title": pr.title,
            "amount": _money(pr.amount), "currency": pr.currency,
            "submitted_at": _date(pr.submitted_at), "waiting_on": _step(pr)}


def _po(po, facts) -> dict:
    return {"number": po.number, "status": po.status, "status_label": _label("po", po.status),
            "vendor": po.vendor_name, "total": _money(po.total), "currency": po.currency,
            "placed_at": _date(po.placed_at), "waiting_on": _step(po),
            **({"facts": facts} if facts else {})}


def _gr(gr) -> dict:
    return {"number": gr.number, "status": gr.status, "status_label": _label("gr", gr.status),
            "type": gr.gr_type, "received_on": _date(gr.received_at)}


def _invoice(inv, pa_numbers: list[str]) -> dict:
    return {"number": inv.internal_ref, "vendor_invoice_number": inv.vendor_invoice_number,
            "status": inv.status, "status_label": _label("invoice", inv.status),
            "total": _money(inv.total_amount), "currency": inv.currency,
            "invoice_date": _date(inv.invoice_date), "due_date": _date(inv.due_date),
            "on_payment_applications": pa_numbers}


def _pa(pa, invoice_refs: list[str]) -> dict:
    paid = pa.paid_at is not None or pa.status == "processed"
    return {"number": pa.pa_number, "status": pa.status, "status_label": _label("pa", pa.status),
            "type": pa.pa_type, "amount": _money(pa.payment_amount), "currency": pa.currency,
            "approved_at": _date(pa.approved_at),
            # The payment itself: the date Finance recorded (paid_at is that
            # date at midnight UTC — PaymentApplication.paid_on).
            "payment": ("paid" if paid else
                        "awaiting payment" if pa.status == "approved" else
                        "not yet — still in approval" if pa.status in ("submitted", "in_review")
                        else "none"),
            "paid_on": (pa.paid_at.astimezone(timezone.utc).date().isoformat()
                        if pa.paid_at else None),
            "pays_invoices": invoice_refs, "waiting_on": _step(pa)}


async def _find(db, scope: dict, doc_type: str, number: str):
    model, attr = _MODEL[doc_type]
    stored = doc_numbers.normalize(_PREFIX[doc_type], number)
    rows = await _visible(db, scope, doc_type, getattr(model, attr) == stored)
    if rows is None:
        return None, "not_permitted"
    return (rows[0], None) if rows else (None, "not_found")


async def _po_facts(db, po_ids: list) -> dict:
    if not po_ids:
        return {}
    rows = (await db.execute(select(
        PurchaseOrder.id, receipt_evidence(PurchaseOrder.id).label("received"),
        PurchaseOrder.invoiced, PurchaseOrder.has_pa, PurchaseOrder.three_way_matched,
    ).where(PurchaseOrder.id.in_(po_ids)))).all()
    return {r.id: {"anything_received": bool(r.received), "invoiced": bool(r.invoiced),
                   "pa_raised": bool(r.has_pa), "three_way_matched": bool(r.three_way_matched)}
            for r in rows}


async def _pas_with_invoices(db, scope, pa_condition):
    pas = await _visible(db, scope, "pa", pa_condition, order_by=PaymentApplication.created_at)
    if pas is None:
        return None, {}
    by_invoice: dict[str, list[str]] = {}
    for pa in pas:
        for iid in pa.invoice_ids or []:
            by_invoice.setdefault(str(iid), []).append(pa.pa_number)
    await _waiting_on(db, "pa", pas)
    return pas, by_invoice


async def _po_branch(db, scope: dict, po_id: uuid.UUID) -> dict:
    """One PO and everything hanging off it."""
    pos = await _visible(db, scope, "po", PurchaseOrder.id == po_id)
    po = pos[0] if pos else None
    grs = await _visible(db, scope, "gr", GoodsReceipt.po_id == po_id,
                         GoodsReceipt.status != "cancelled",
                         order_by=GoodsReceipt.received_at)
    invoices = await _visible(
        db, scope, "invoice",
        or_(Invoice.po_id == po_id, Invoice.id.in_(
            select(InvoicePoAllocation.invoice_id).where(InvoicePoAllocation.po_id == po_id))),
        order_by=Invoice.invoice_date)
    refs = {str(i.id): i.internal_ref for i in invoices or []}
    pas, pas_by_invoice = await _pas_with_invoices(
        db, scope, PaymentApplication.id.in_(pa_ids_for_po(po_id)))
    facts = (await _po_facts(db, [po_id])).get(po_id) if po is not None else None
    if po is not None:
        await _waiting_on(db, "po", [po])
    return {
        # Out of the caller's scope reads as not visible, never as "no PO": a
        # receipt or invoice they can see already names it.
        "purchase_order": _po(po, facts) if po is not None else {"not_visible": True},
        "goods_receipts": _layer(grs, _gr),
        "invoices": _layer(invoices, lambda i: _invoice(i, pas_by_invoice.get(str(i.id), []))),
        "payment_applications": _layer(
            pas, lambda p: _pa(p, [refs[str(i)] for i in p.invoice_ids or [] if str(i) in refs])),
    }


async def _agreement_branch(db, scope: dict, agreement_id: uuid.UUID) -> dict:
    agreements = await _visible(db, scope, "agreement", PurchaseAgreement.id == agreement_id)
    agr = agreements[0] if agreements else None
    invoices = await _visible(db, scope, "invoice", Invoice.agreement_id == agreement_id,
                              order_by=Invoice.invoice_date)
    refs = {str(i.id): i.internal_ref for i in invoices or []}
    pas, pas_by_invoice = await _pas_with_invoices(
        db, scope, PaymentApplication.agreement_id == agreement_id)
    return {
        "agreement": ({"number": agr.number, "status": agr.status, "kind": agr.agreement_type,
                       "vendor": agr.vendor_name, "valid_to": _date(agr.valid_to),
                       "not_to_exceed": _money(agr.not_to_exceed),
                       "consumed": _money(agr.consumed_amount), "currency": agr.currency}
                      if agr is not None else {"not_visible": True}),
        "invoices": _layer(invoices, lambda i: _invoice(i, pas_by_invoice.get(str(i.id), []))),
        "payment_applications": _layer(
            pas, lambda p: _pa(p, [refs[str(i)] for i in p.invoice_ids or [] if str(i) in refs])),
    }


async def build(db, scope: dict, doc_type: str, number: str) -> dict:
    """The whole chain around one document.

    Returns {"found": False, "reason": ...} when the document is not there or
    not visible — the same answer for both, as everywhere else.
    """
    doc_type = (doc_type or "").lower()
    if doc_type not in SUPPORTED:
        return {"found": False, "reason": "not_supported"}
    doc, why = await _find(db, scope, doc_type, number)
    if doc is None:
        return {"found": False, "reason": why, "asked": number}

    po_ids: list[uuid.UUID] = []
    agreement_ids: list[uuid.UUID] = []
    pr = None
    if doc_type == "pr":
        pr = doc
        linked = [PurchaseOrder.pr_id == doc.id]
        if doc.po_id:
            linked.append(PurchaseOrder.id == doc.po_id)
        po_ids = list((await db.execute(select(PurchaseOrder.id).where(or_(*linked))
                                        .order_by(PurchaseOrder.created_at))).scalars().all())
    elif doc_type == "po":
        po_ids = [doc.id]
    elif doc_type == "gr":
        po_ids = [doc.po_id] if doc.po_id else []
    elif doc_type == "invoice":
        po_ids = list(dict.fromkeys(
            ([doc.po_id] if doc.po_id else []) + list((await db.execute(
                select(InvoicePoAllocation.po_id).where(
                    InvoicePoAllocation.invoice_id == doc.id))).scalars().all())))
        if doc.agreement_id:
            agreement_ids = [doc.agreement_id]
    elif doc_type == "pa":
        po_ids = list((await db.execute(po_ids_of_pa(doc.id))).scalars().all())
        if doc.agreement_id:
            agreement_ids = [doc.agreement_id]
    elif doc_type == "agreement":
        agreement_ids = [doc.id]

    branches = [await _po_branch(db, scope, pid) for pid in po_ids[:5]]
    if pr is None and po_ids:
        # The requisition upstream, when the caller may see it.
        pr_ids = list((await db.execute(select(PurchaseOrder.pr_id).where(
            PurchaseOrder.id.in_(po_ids), PurchaseOrder.pr_id.is_not(None)))).scalars().all())
        if pr_ids:
            prs = await _visible(db, scope, "pr", PurchaseRequest.id.in_(pr_ids))
            pr = prs[0] if prs else None
    if pr is not None:
        await _waiting_on(db, "pr", [pr])

    out = {
        "found": True,
        "asked_about": {"doc_type": doc_type, "number": getattr(doc, _MODEL[doc_type][1])},
        "purchase_request": _pr(pr) if pr is not None else None,
        "orders": branches,
        "agreements": [await _agreement_branch(db, scope, a) for a in agreement_ids],
    }
    if len(po_ids) > 5:
        out["more_orders"] = len(po_ids) - 5
    if doc_type == "pr" and not po_ids:
        out["no_order_yet"] = True
    return out


def count_documents(chain: dict) -> int:
    """How many documents the chain names — the evidence line's figure."""
    n = 1 if chain.get("purchase_request") else 0
    for b in chain.get("orders") or []:
        n += 1 if (b.get("purchase_order") or {}).get("number") else 0
        for layer in ("goods_receipts", "invoices", "payment_applications"):
            n += (b.get(layer) or {}).get("count") or 0
    for a in chain.get("agreements") or []:
        n += 1 if (a.get("agreement") or {}).get("number") else 0
        for layer in ("invoices", "payment_applications"):
            n += (a.get(layer) or {}).get("count") or 0
    return n
