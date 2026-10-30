"""PO Fee Invoices — freight / customs invoices linked to POs (NC orders and,
since 2026-10-30, EPMS-raised ones too).

Design: docs/superpowers/specs/2026-10-23-po-fee-invoices-design.md.

Three things live here:

* Incoterms terms — the Admin-configured list, and what a PO's code says it
  expects: a freight invoice per arrival, a customs invoice per arrival, both
  or neither. Read live from config on every call, never snapshotted onto the
  PO, so changing a term re-rates every PO at once.
* The links — which invoice (or invoice line) belongs to which PO / arrival.
  Orthogonal to matching and payment: nothing here touches invoice.po_id,
  status, match_route or any PA.
* Fee status — per arrival, is there a freight / customs invoice; rolled up
  per PO for the list column.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import CompanyConfig
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_fee_link import FEE_TYPES, InvoiceFeeLink
from app.models.po import PurchaseOrder

# Mirrored by alembic pf01_po_fee_invoices._SEED (test_po_fee pins the two).
# Buyer's view: who pays the main carriage, who clears the goods in.
DEFAULT_INCOTERMS: list[dict] = [
    {"code": c, "label": l, "needs_freight": f, "needs_customs": cu, "active": True}
    for c, l, f, cu in [
        ("EXW", "Ex Works", True, True),
        ("FCA", "Free Carrier", True, True),
        ("FAS", "Free Alongside Ship", True, True),
        ("FOB", "Free On Board", True, True),
        ("CPT", "Carriage Paid To", False, True),
        ("CIP", "Carriage and Insurance Paid To", False, True),
        ("CFR", "Cost and Freight", False, True),
        ("CIF", "Cost, Insurance and Freight", False, True),
        ("DAP", "Delivered at Place", False, True),
        ("DPU", "Delivered at Place Unloaded", False, True),
        ("DDP", "Delivered Duty Paid", False, False),
    ]
]

# Invoice routes a fee link may be put on. "po" / "agreement" already have
# their own PO or agreement relation; "freight" is the courier module.
LINKABLE_ROUTES = (None, "direct")

# NC tags a non raw-material order "[Milk / <trade type>]" in its notes from
# the first sync on — also while it is still nc_pending, before the status
# itself turns nc_milk (nc_purchase_sync.transform).
_MILK_TAG = "[Milk /"


class FeeLinkInvalid(ValueError):
    """The requested link set cannot be saved (message is user-facing)."""


# ── Incoterms ────────────────────────────────────────────────────────────────

def effective_terms(cfg: CompanyConfig | None) -> list[dict]:
    stored = (cfg.incoterms_terms if cfg is not None else None) or []
    return stored or DEFAULT_INCOTERMS


def term_for(code: str | None, terms: list[dict]) -> dict | None:
    """The configured term for a code — inactive ones included: deactivating
    a code hides it from the picker, it does not un-rate POs that carry it."""
    if not code:
        return None
    code = code.strip().upper()
    return next((t for t in terms if str(t.get("code", "")).upper() == code), None)


def normalise_terms(raw: list[dict]) -> list[dict]:
    """Validate an Incoterms list from the settings page. Raises FeeLinkInvalid."""
    out, seen = [], set()
    for t in raw:
        code = str(t.get("code") or "").strip().upper()
        if not code or len(code) > 10 or not code.isalnum():
            raise FeeLinkInvalid(f"Invalid Incoterms code '{t.get('code')}' (1–10 letters/digits)")
        if code in seen:
            raise FeeLinkInvalid(f"Incoterms code {code} is listed twice")
        seen.add(code)
        out.append({
            "code": code,
            "label": str(t.get("label") or "").strip()[:100],
            "needs_freight": bool(t.get("needs_freight")),
            "needs_customs": bool(t.get("needs_customs")),
            "active": bool(t.get("active", True)),
        })
    return out


def removed_codes(old: list[dict], new: list[dict]) -> list[str]:
    """Codes present before and missing now. Terms are deactivated, never
    deleted, so a PO's stored code always resolves."""
    keep = {t["code"] for t in new}
    return sorted({str(t.get("code", "")).upper() for t in old} - keep - {""})


# ── Which POs ────────────────────────────────────────────────────────────────

def is_raw_material_po(po: PurchaseOrder) -> bool:
    """An NC raw-material / packaging order — the only POs fees link to and
    the only ones whose Edit Details requires Incoterms. Milk is excluded
    whether or not NC has approved it yet."""
    return (po.type == 1 and po.source == "nc" and po.status != "nc_milk"
            and _MILK_TAG not in (po.notes or ""))


def raw_material_po_clause():
    return and_(PurchaseOrder.type == 1, PurchaseOrder.source == "nc",
                PurchaseOrder.status != "nc_milk",
                ~func.coalesce(PurchaseOrder.notes, "").contains(_MILK_TAG))


# Fee types whose presence is judged against Incoterms. A milk order with no
# Incoterms cannot take them (user 2026-10-26): it is linked as "other" until
# someone sets its Incoterms in Edit Details.
JUDGED_FEE_TYPES = ("freight", "customs")


def takes_fee_type(po: PurchaseOrder, fee_type: str) -> bool:
    # The milk rule is an NC one; an EPMS-raised PO has no Incoterms picker
    # and takes every fee type.
    return (fee_type not in JUDGED_FEE_TYPES or po.source != "nc"
            or is_raw_material_po(po) or po.incoterms_code is not None)


# An EPMS-raised PO is linkable once it is a real order — approved or past it.
# Draft, in-approval, returned, rejected and cancelled ones are not.
EPMS_LINKABLE_STATUSES = ("approved", "issued", "partially_received", "fully_received", "closed")


def is_nc_po(po: PurchaseOrder) -> bool:
    return po.source == "nc"


def is_fee_linkable_po(po: PurchaseOrder) -> bool:
    """Any order mirrored from NC — milk included (user 2026-10-26) — and,
    since 2026-10-30, any approved EPMS-raised PO: those carry freight and
    customs invoices too. An NC order is linked to an ARRIVAL, so in practice
    only NC orders that have one; that half is checked where the arrivals are
    loaded. The Incoterms requirement stays on raw-material orders only
    (is_raw_material_po)."""
    if is_nc_po(po):
        return po.status != "cancelled"
    return po.status in EPMS_LINKABLE_STATUSES


def fee_linkable_po_clause():
    return or_(and_(PurchaseOrder.source == "nc", PurchaseOrder.status != "cancelled"),
               and_(func.coalesce(PurchaseOrder.source, "") != "nc",
                    PurchaseOrder.status.in_(EPMS_LINKABLE_STATUSES)))



# ── Links on an invoice ──────────────────────────────────────────────────────

@dataclass
class LinkIn:
    fee_type: str
    po_id: uuid.UUID
    gr_id: uuid.UUID | None = None
    invoice_line_id: uuid.UUID | None = None
    note: str | None = None


async def list_for_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> list[dict]:
    rows = (await db.execute(
        select(InvoiceFeeLink, PurchaseOrder.number, PurchaseOrder.vendor_name,
               GoodsReceipt.number, GoodsReceipt.received_at)
        .join(PurchaseOrder, PurchaseOrder.id == InvoiceFeeLink.po_id)
        .outerjoin(GoodsReceipt, GoodsReceipt.id == InvoiceFeeLink.gr_id)
        .where(InvoiceFeeLink.invoice_id == invoice_id)
        .order_by(PurchaseOrder.number, GoodsReceipt.received_at)
    )).all()
    return [{
        "id": l.id, "invoice_line_id": l.invoice_line_id, "fee_type": l.fee_type, "note": l.note,
        "po_id": l.po_id, "po_number": po_no, "po_vendor_name": po_vendor,
        "gr_id": l.gr_id, "gr_number": gr_no, "gr_received_at": gr_at,
    } for l, po_no, po_vendor, gr_no, gr_at in rows]


async def replace_for_invoice(
    db: AsyncSession, invoice: Invoice, links: list[LinkIn], *, actor_id: uuid.UUID,
) -> tuple[list[dict], list[dict]]:
    """Make `links` the invoice's complete link set. Returns (before, after) as
    plain dicts for the audit row. Raises FeeLinkInvalid."""
    # Clearing is always allowed: an invoice later re-matched onto another
    # route must not be left holding links nobody can remove.
    if links and invoice.match_route not in LINKABLE_ROUTES:
        raise FeeLinkInvalid(
            f"This invoice is matched on the '{invoice.match_route}' route. Only an invoice "
            "paid by a Direct PA (or not yet matched) can be linked to a PO as a fee.")

    line_ids = {str(li.get("id")) for li in (invoice.line_items or []) if li.get("id")}
    po_ids = {l.po_id for l in links}
    pos = {p.id: p for p in (await db.execute(
        select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids)))).scalars()} if po_ids else {}
    grs_by_po: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if po_ids:
        for gid, pid in (await db.execute(
                select(GoodsReceipt.id, GoodsReceipt.po_id)
                .where(GoodsReceipt.po_id.in_(po_ids), GoodsReceipt.status != "cancelled"))).all():
            grs_by_po[pid].add(gid)

    seen, clean = set(), []
    for l in links:
        if l.fee_type not in FEE_TYPES:
            raise FeeLinkInvalid(f"Unknown fee type '{l.fee_type}'")
        # "Other" says nothing by itself, so it must say what it is; the two
        # named types carry no note (their type is the description).
        l.note = ((l.note or "").strip() or None) if l.fee_type == "other" else None
        if l.fee_type == "other" and not l.note:
            raise FeeLinkInvalid("Add a note saying what this Other fee is (e.g. inspection, storage).")
        po = pos.get(l.po_id)
        if po is None:
            raise FeeLinkInvalid("Purchase order not found")
        if not is_fee_linkable_po(po):
            raise FeeLinkInvalid(
                f"{po.number} is cancelled or not approved yet, so fees cannot be linked to it.")
        if not takes_fee_type(po, l.fee_type):
            raise FeeLinkInvalid(
                f"{po.number} is a milk order with no Incoterms, so it can only be linked as "
                "Other. Set its Incoterms in Edit Details to link freight or customs.")
        if l.invoice_line_id is not None and str(l.invoice_line_id) not in line_ids:
            raise FeeLinkInvalid("That invoice line does not belong to this invoice")
        # A link names an arrival: a fee follows a delivery, and an
        # arrival-less link would count toward none. The one exception is an
        # EPMS-raised PO with no arrival yet — its fee is linked to the whole
        # order (user 2026-10-30). (Links can still LOSE their arrival when
        # Data Maintenance deletes it — gr_id is SET NULL — and are shown on
        # the PO as such; re-saving them asks for one.)
        if l.gr_id is None:
            if is_nc_po(po) or grs_by_po[l.po_id]:
                raise FeeLinkInvalid(f"Choose which arrival(s) of {po.number} this fee is for")
        elif l.gr_id not in grs_by_po[l.po_id]:
            raise FeeLinkInvalid(f"That arrival does not belong to {po.number}")
        key = (l.invoice_line_id, l.fee_type, l.po_id, l.gr_id)
        if key in seen:
            continue
        seen.add(key)
        clean.append(l)

    before = await list_for_invoice(db, invoice.id)
    await db.execute(delete(InvoiceFeeLink).where(InvoiceFeeLink.invoice_id == invoice.id))
    for l in clean:
        db.add(InvoiceFeeLink(invoice_id=invoice.id, invoice_line_id=l.invoice_line_id,
                              fee_type=l.fee_type, note=l.note, po_id=l.po_id, gr_id=l.gr_id,
                              created_by=actor_id))
    await db.flush()
    after = await list_for_invoice(db, invoice.id)
    return before, after


async def search_candidate_pos(db: AsyncSession, q: str | None, limit: int = 50,
                               fee_type: str | None = None) -> dict:
    """NC orders that have at least one arrival — raw material, packaging and
    milk alike — and approved EPMS-raised POs, with or without one, for the
    link picker, with their arrivals and how many match in all (the picker
    says "showing N of M" rather than cutting off silently).

    Searched by PO number, vendor or arrival number — never filtered by the fee
    invoice's own vendor (a forwarder, not the PO's supplier). Latest arrival
    first, then orders with none yet, newest first: a freight or customs
    invoice follows a delivery. For a freight or customs fee, milk orders
    without Incoterms are left out (takes_fee_type)."""
    latest_arrival = (
        select(GoodsReceipt.po_id, func.max(GoodsReceipt.received_at).label("last_at"))
        .where(GoodsReceipt.status != "cancelled")
        .group_by(GoodsReceipt.po_id).subquery())
    is_nc = func.coalesce(PurchaseOrder.source, "") == "nc"   # source is NULL on EPMS POs
    stmt = (select(PurchaseOrder)
            .outerjoin(latest_arrival, latest_arrival.c.po_id == PurchaseOrder.id)
            .where(fee_linkable_po_clause(),
                   or_(~is_nc, latest_arrival.c.po_id.is_not(None))))
    if fee_type in JUDGED_FEE_TYPES:
        stmt = stmt.where(or_(~is_nc, raw_material_po_clause(),
                              PurchaseOrder.incoterms_code.is_not(None)))
    if q and q.strip():
        like = f"%{q.strip()}%"
        by_arrival = select(GoodsReceipt.po_id).where(GoodsReceipt.number.ilike(like))
        stmt = stmt.where(or_(PurchaseOrder.number.ilike(like),
                              PurchaseOrder.vendor_name.ilike(like),
                              PurchaseOrder.id.in_(by_arrival)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    pos = list((await db.execute(
        stmt.order_by(latest_arrival.c.last_at.desc().nulls_last(),
                      PurchaseOrder.created_at.desc())
        .limit(limit))).scalars())
    grs = await _arrivals(db, [p.id for p in pos])
    return {"total": total, "items": [{
        "id": p.id, "number": p.number, "vendor_name": p.vendor_name, "status": p.status,
        "source": "nc" if is_nc_po(p) else "epms",
        "is_milk": is_nc_po(p) and not is_raw_material_po(p),
        "incoterms_code": p.incoterms_code,
        "arrivals": [{"id": g.id, "number": g.number, "received_at": g.received_at}
                     for g in grs.get(p.id, [])],
    } for p in pos]}


# ── Fee status ───────────────────────────────────────────────────────────────

async def _arrivals(db: AsyncSession, po_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[GoodsReceipt]]:
    out: dict[uuid.UUID, list[GoodsReceipt]] = defaultdict(list)
    if not po_ids:
        return out
    for g in (await db.execute(
            select(GoodsReceipt).where(GoodsReceipt.po_id.in_(po_ids),
                                       GoodsReceipt.status != "cancelled")
            .order_by(GoodsReceipt.received_at.nulls_last(), GoodsReceipt.number))).scalars():
        out[g.po_id].append(g)
    return out


def _rollup(term: dict | None, arrivals: list, covered: dict) -> str:
    """complete | missing | no_arrivals | not_required | not_set"""
    if term is None:
        return "not_set"
    needed = [t for t, flag in (("freight", term.get("needs_freight")),
                                ("customs", term.get("needs_customs"))) if flag]
    if not needed:
        return "not_required"
    if not arrivals:
        return "no_arrivals"
    for g in arrivals:
        for t in needed:
            if not covered.get((g.id, t)):
                return "missing"
    return "complete"


async def fee_status_for_pos(db: AsyncSession, pos: list[PurchaseOrder]) -> dict[uuid.UUID, str]:
    """PO id → roll-up status. Raw-material orders always get one (Incoterms is
    required on them). A milk order gets one only once someone has given it an
    Incoterms code — otherwise there is nothing to judge, and the list would
    say "Incoterms not set" on every milk order. An EPMS-raised PO never gets
    one: it has no Incoterms picker, so its linked fees are only listed. One
    query for arrivals and one for links whatever the page size."""
    raw = [p for p in pos if is_raw_material_po(p)
           or (is_nc_po(p) and is_fee_linkable_po(p) and p.incoterms_code)]
    if not raw:
        return {}
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    terms = effective_terms(cfg)
    ids = [p.id for p in raw]
    arrivals = await _arrivals(db, ids)
    covered: dict = {}
    for gr_id, fee_type in (await db.execute(
            select(InvoiceFeeLink.gr_id, InvoiceFeeLink.fee_type)
            .where(InvoiceFeeLink.po_id.in_(ids), InvoiceFeeLink.gr_id.is_not(None)))).all():
        covered[(gr_id, fee_type)] = True
    return {p.id: _rollup(term_for(p.incoterms_code, terms), arrivals.get(p.id, []), covered)
            for p in raw}


async def fee_detail_for_po(db: AsyncSession, po: PurchaseOrder) -> dict:
    """Everything the PO page's Freight & Customs panel shows."""
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    # Fees on an EPMS-raised PO are listed, never judged (fee_status_for_pos).
    term = term_for(po.incoterms_code, effective_terms(cfg)) if is_nc_po(po) else None
    arrivals = (await _arrivals(db, [po.id])).get(po.id, [])

    rows = (await db.execute(
        select(InvoiceFeeLink, Invoice)
        .join(Invoice, Invoice.id == InvoiceFeeLink.invoice_id)
        .where(InvoiceFeeLink.po_id == po.id)
        .order_by(Invoice.invoice_date, Invoice.internal_ref))).all()

    def _inv(link: InvoiceFeeLink, inv: Invoice) -> dict:
        line = None
        if link.invoice_line_id is not None:
            line = next((li for li in (inv.line_items or [])
                         if str(li.get("id")) == str(link.invoice_line_id)), None)
        return {
            "link_id": link.id, "fee_type": link.fee_type, "note": link.note,
            "invoice_id": inv.id, "internal_ref": inv.internal_ref,
            "vendor_invoice_number": inv.vendor_invoice_number, "vendor_name": inv.vendor_name,
            "invoice_date": inv.invoice_date, "status": inv.status, "currency": inv.currency,
            "total_amount": inv.total_amount,
            "line_description": (line or {}).get("description"),
            "line_total": (line or {}).get("line_total"),
        }

    by_gr: dict = defaultdict(list)
    unassigned: list[dict] = []
    covered: dict = {}
    for link, inv in rows:
        if link.gr_id is None:
            unassigned.append(_inv(link, inv))
        else:
            by_gr[link.gr_id].append(_inv(link, inv))
            covered[(link.gr_id, link.fee_type)] = True

    return {
        "applies": is_fee_linkable_po(po),
        "is_nc": is_nc_po(po),
        # Raw-material orders must carry Incoterms; on a milk order it is
        # optional, so "not set" there is not something to fix.
        "incoterms_required": is_raw_material_po(po),
        "incoterms_code": po.incoterms_code,
        "incoterms_place": po.incoterms,
        "term": term,
        "status": _rollup(term, arrivals, covered),
        "arrivals": [{
            "gr_id": g.id, "gr_number": g.number, "received_at": g.received_at,
            "freight": [i for i in by_gr[g.id] if i["fee_type"] == "freight"],
            "customs": [i for i in by_gr[g.id] if i["fee_type"] == "customs"],
            "other": [i for i in by_gr[g.id] if i["fee_type"] == "other"],
        } for g in arrivals],
        "unassigned": unassigned,
    }

