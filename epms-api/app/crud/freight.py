"""Courier freight payment: shipments, carrier-invoice matching, Freight PA.

Design: docs/superpowers/specs/2026-10-19-freight-payment-design.md (§5, §6).
Errors are raised as FreightError(status, detail) and turned into HTTP
responses by the router, so this module stays HTTP-agnostic.
"""
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._list_order import lower_text, sort_clauses
from app.crud._numbering import next_number
from app.crud import invoice_dedup
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.freight import (
    FREIGHT_BUDGET_CODE, FreightCarrier, FreightInvoiceLine, FreightShipment, PaCostAllocation,
)
from app.models.invoice import Invoice
from app.models.pa import PaLineItem, PaymentApplication
from app.models.user import User
from app.models.vendor import Vendor

_CENT = Decimal("0.01")
_ZERO = Decimal("0")

# Lines a human must look at before the invoice can be finalised.
_NEEDS_DECISION = ("unmatched", "duplicate_billing")


class FreightError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def normalize_tracking(raw: str | None) -> str | None:
    """Spaces, hyphens and dots out, upper case. '' → None."""
    if raw is None:
        return None
    s = re.sub(r"[\s\-.]", "", raw).upper()
    return s or None


def _q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(_CENT, rounding=ROUND_HALF_UP)


# ── Lookups ───────────────────────────────────────────────────────────────────

async def get_carrier(db: AsyncSession, carrier_id: uuid.UUID) -> FreightCarrier:
    c = await db.get(FreightCarrier, carrier_id)
    if c is None:
        raise FreightError(404, "Carrier not found")
    return c


async def _cost_center(db: AsyncSession, cc_id: uuid.UUID) -> CostCenter:
    cc = await db.get(CostCenter, cc_id)
    if cc is None or not cc.is_active:
        raise FreightError(422, "Cost center not found or inactive")
    return cc


async def cost_center_labels(db: AsyncSession, ids) -> dict[uuid.UUID, tuple[str, str, str | None]]:
    """cc id → (code, name, department name)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await db.execute(
        select(CostCenter.id, CostCenter.code, CostCenter.name, Department.name)
        .join(Department, Department.id == CostCenter.department_id, isouter=True)
        .where(CostCenter.id.in_(ids))
    )).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


# ── Carriers ──────────────────────────────────────────────────────────────────

async def list_carriers(db: AsyncSession, include_inactive: bool = False) -> list[tuple[FreightCarrier, str]]:
    q = (select(FreightCarrier, Vendor.name)
         .join(Vendor, Vendor.id == FreightCarrier.vendor_id)
         .order_by(FreightCarrier.code))
    if not include_inactive:
        q = q.where(FreightCarrier.is_active.is_(True))
    return [(c, n) for c, n in (await db.execute(q)).all()]


async def create_carrier(db: AsyncSession, payload) -> FreightCarrier:
    if await db.get(Vendor, payload.vendor_id) is None:
        raise FreightError(404, "Vendor not found")
    code = payload.code.strip().upper()
    clash = (await db.execute(select(FreightCarrier).where(or_(
        FreightCarrier.vendor_id == payload.vendor_id, FreightCarrier.code == code)))).scalars().first()
    if clash is not None:
        raise FreightError(409, f"Carrier {clash.code} already uses this vendor or code")
    _check_pattern(payload.tracking_pattern)
    if payload.default_cost_center_id:
        await _cost_center(db, payload.default_cost_center_id)
    c = FreightCarrier(vendor_id=payload.vendor_id, code=code, name=payload.name.strip(),
                       tracking_pattern=payload.tracking_pattern or None,
                       default_cost_center_id=payload.default_cost_center_id)
    db.add(c)
    await db.flush()
    return c


async def update_carrier(db: AsyncSession, carrier: FreightCarrier, payload) -> FreightCarrier:
    data = payload.model_dump(exclude_unset=True)
    if "tracking_pattern" in data:
        _check_pattern(data["tracking_pattern"])
        carrier.tracking_pattern = data["tracking_pattern"] or None
    if "default_cost_center_id" in data:
        if data["default_cost_center_id"]:
            await _cost_center(db, data["default_cost_center_id"])
        carrier.default_cost_center_id = data["default_cost_center_id"]
    if data.get("name"):
        carrier.name = data["name"].strip()
    if data.get("is_active") is not None:
        carrier.is_active = data["is_active"]
    await db.flush()
    return carrier


def _check_pattern(pattern: str | None) -> None:
    if not pattern:
        return
    try:
        re.compile(pattern)
    except re.error as e:
        raise FreightError(422, f"Tracking pattern is not a valid regular expression: {e}")


# ── Shipments ─────────────────────────────────────────────────────────────────

def _validate_tracking(carrier: FreightCarrier, tracking: str) -> None:
    if carrier.tracking_pattern and not re.fullmatch(carrier.tracking_pattern, tracking):
        raise FreightError(
            422, f"'{tracking}' does not look like a {carrier.name} tracking number. "
                 f"Check it against the waybill.")


async def _ensure_tracking_free(db: AsyncSession, carrier_id: uuid.UUID, tracking: str,
                                exclude_id: uuid.UUID | None = None) -> None:
    q = select(FreightShipment).where(FreightShipment.carrier_id == carrier_id,
                                      FreightShipment.tracking_no == tracking)
    if exclude_id:
        q = q.where(FreightShipment.id != exclude_id)
    other = (await db.execute(q)).scalars().first()
    if other is not None:
        who = other.created_by_name or "another user"
        raise FreightError(409, f"Tracking number {tracking} is already registered by {who}"
                                f" ({other.status}).")


async def create_shipment(db: AsyncSession, payload, user_id: uuid.UUID,
                          source: str = "registered") -> FreightShipment:
    carrier = await get_carrier(db, payload.carrier_id)
    if not carrier.is_active:
        raise FreightError(422, "Carrier is inactive")
    tracking = normalize_tracking(payload.tracking_no)
    if not tracking:
        raise FreightError(422, "Tracking number is required")
    _validate_tracking(carrier, tracking)
    await _ensure_tracking_free(db, carrier.id, tracking)
    cc = await _cost_center(db, payload.cost_center_id)
    name = (await db.execute(select(User.full_name).where(User.id == user_id))).scalar_one_or_none()
    s = FreightShipment(
        carrier_id=carrier.id, tracking_no=tracking, direction=payload.direction,
        cost_center_id=cc.id, department_id=cc.department_id, note=payload.note,
        attachment_key=payload.attachment_key, source=source, status="pending",
        created_by=user_id, created_by_name=name)
    db.add(s)
    await db.flush()
    return s


async def update_shipment(db: AsyncSession, s: FreightShipment, payload) -> FreightShipment:
    if s.status != "pending":
        raise FreightError(409, "Only a shipment that has not been billed can be edited")
    data = payload.model_dump(exclude_unset=True)
    if data.get("tracking_no"):
        carrier = await get_carrier(db, s.carrier_id)
        tracking = normalize_tracking(data["tracking_no"])
        _validate_tracking(carrier, tracking)
        await _ensure_tracking_free(db, s.carrier_id, tracking, exclude_id=s.id)
        s.tracking_no = tracking
    if data.get("cost_center_id"):
        cc = await _cost_center(db, data["cost_center_id"])
        s.cost_center_id, s.department_id = cc.id, cc.department_id
    if data.get("direction"):
        s.direction = data["direction"]
    if "note" in data:
        s.note = data["note"]
    await db.flush()
    return s


async def void_shipment(db: AsyncSession, s: FreightShipment) -> FreightShipment:
    if s.status != "pending":
        raise FreightError(409, "Only a shipment that has not been billed can be voided")
    s.status = "void"
    await db.flush()
    return s


def _shipment_sortable() -> dict:
    """Sortable columns of the Shipments list (sort=<key>&dir=asc|desc), each
    sorting what its cell shows: "Carrier" shows the carrier CODE; "Department /
    Cost Center" shows the cost centre's department name over its code (the
    same labels shipment_dicts resolves); "Invoice" shows the vendor invoice
    number of the latest finalised invoice that billed the shipment."""
    S = FreightShipment
    carrier_code = (select(FreightCarrier.code)
                    .where(FreightCarrier.id == S.carrier_id).scalar_subquery())
    dept_name = (select(Department.name)
                 .join(CostCenter, CostCenter.department_id == Department.id)
                 .where(CostCenter.id == S.cost_center_id).scalar_subquery())
    cc_code = (select(CostCenter.code)
               .where(CostCenter.id == S.cost_center_id).scalar_subquery())
    invoice_no = (select(Invoice.vendor_invoice_number)
                  .join(FreightInvoiceLine, FreightInvoiceLine.invoice_id == Invoice.id)
                  .where(FreightInvoiceLine.shipment_id == S.id, Invoice.status != "unmatched")
                  .order_by(Invoice.invoice_date.desc()).limit(1).scalar_subquery())
    return {
        "carrier": lower_text(carrier_code),
        "tracking_no": lower_text(S.tracking_no),
        "direction": lower_text(S.direction),
        "department": (lower_text(dept_name), lower_text(cc_code)),
        "created_by_name": lower_text(S.created_by_name),
        "created_at": S.created_at,
        # Labels Pending / Billed / Void sort the same as their codes.
        "status": lower_text(S.status),
        "invoice_number": lower_text(invoice_no),
    }


async def list_shipments(db: AsyncSession, *, created_by: uuid.UUID | None, status: str | None,
                         carrier_id: uuid.UUID | None, search: str | None,
                         page: int, page_size: int,
                         sort: str | None = None, dir: str = "asc") -> tuple[list[dict], int]:
    q = select(FreightShipment)
    if created_by is not None:
        q = q.where(FreightShipment.created_by == created_by)
    if status:
        q = q.where(FreightShipment.status == status)
    if carrier_id:
        q = q.where(FreightShipment.carrier_id == carrier_id)
    if search:
        q = q.where(FreightShipment.tracking_no.ilike(f"%{normalize_tracking(search) or search}%"))
    ordering = sort_clauses(sort, dir, _shipment_sortable(),
                            tiebreak=[FreightShipment.created_at.desc(), FreightShipment.id])
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = list((await db.execute(
        q.order_by(*(ordering or [FreightShipment.created_at.desc()]))
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return await shipment_dicts(db, rows), total


async def shipment_dicts(db: AsyncSession, rows: list[FreightShipment]) -> list[dict]:
    if not rows:
        return []
    labels = await cost_center_labels(db, [r.cost_center_id for r in rows])
    carriers = {c.id: c.code for c in (await db.execute(select(FreightCarrier))).scalars()}
    # The invoice line that billed each shipment (latest finalised one wins).
    billed = {}
    for sid, inv_id, ref, amt in (await db.execute(
        select(FreightInvoiceLine.shipment_id, Invoice.id, Invoice.vendor_invoice_number,
               FreightInvoiceLine.amount)
        .join(Invoice, Invoice.id == FreightInvoiceLine.invoice_id)
        .where(FreightInvoiceLine.shipment_id.in_([r.id for r in rows]),
               Invoice.status != "unmatched")
        .order_by(Invoice.invoice_date)
    )).all():
        billed[sid] = (inv_id, ref, amt)
    out = []
    for r in rows:
        code, name, dept = labels.get(r.cost_center_id, (None, None, None))
        inv = billed.get(r.id)
        out.append({
            **{c.key: getattr(r, c.key) for c in FreightShipment.__table__.columns},
            "carrier_code": carriers.get(r.carrier_id),
            "cost_center_code": code, "cost_center_name": name, "department_name": dept,
            "invoice_id": inv[0] if inv else None,
            "invoice_number": inv[1] if inv else None,
            "billed_amount": inv[2] if inv else None,
        })
    return out


# ── Default cost centre (D11) ─────────────────────────────────────────────────

async def default_cost_center(db: AsyncSession, department_id: uuid.UUID, balance_fn) -> dict:
    """The department's cost centre to pre-select when registering a shipment.

    One active cost centre → that one. Several → the one whose current-year
    plan carries the freight account (largest annual budget wins). None of
    them budgeted, or budget-api unreachable → no default; the user picks.
    `balance_fn(cost_center_id, fiscal_year)` returns budget-api's balance
    dict or None (injected so tests need no budget-api).
    """
    ccs = list((await db.execute(
        select(CostCenter).where(CostCenter.department_id == department_id,
                                 CostCenter.is_active.is_(True))
        .order_by(CostCenter.code))).scalars().all())
    if not ccs:
        return {"cost_center_id": None, "reason": "none", "budgeted_cost_center_ids": []}
    year = datetime.now(timezone.utc).year
    budgeted: list[tuple[Decimal, uuid.UUID]] = []
    for cc in ccs:
        bal = await balance_fn(cc.id, year)
        annual = Decimal(str((bal or {}).get("annual_budget") or 0))
        if annual > 0:
            budgeted.append((annual, cc.id))
    budgeted.sort(key=lambda t: t[0], reverse=True)
    ids = [cc_id for _, cc_id in budgeted]
    if len(ccs) == 1:
        return {"cost_center_id": ccs[0].id, "reason": "single", "budgeted_cost_center_ids": ids}
    if budgeted:
        return {"cost_center_id": budgeted[0][1], "reason": "freight_budget",
                "budgeted_cost_center_ids": ids}
    return {"cost_center_id": None, "reason": "none", "budgeted_cost_center_ids": ids}


# ── Invoices ──────────────────────────────────────────────────────────────────

async def find_duplicate_invoice(db: AsyncSession, vendor_id: uuid.UUID, number: str) -> str | None:
    """Same vendor + invoice # already in EPMS or OA (PRD INV-004) — the one
    rule in crud.invoice_dedup, which every invoice path uses. Returns a
    reference. A number with no letter or digit raises FreightError(422)."""
    vendor = await db.get(Vendor, vendor_id)
    try:
        hit = await invoice_dedup.find_duplicate(db, vendor_id, vendor.name if vendor else "", number)
    except invoice_dedup.UncheckableInvoiceNumber as e:
        raise FreightError(422, str(e))
    return hit.describe() if hit else None


def _spread(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` over `weights` proportionally, to the cent; the rounding
    remainder goes to the largest weight. All-zero weights → equal split."""
    n = len(weights)
    if n == 0:
        return []
    base = sum(weights)
    if base == 0:
        weights, base = [Decimal(1)] * n, Decimal(n)
    parts = [_q2(total * w / base) for w in weights]
    diff = total - sum(parts)
    if diff:
        i = max(range(n), key=lambda k: weights[k])
        parts[i] += diff
    return parts


def distribute_residual_tax(invoice_tax: Decimal, lines: list) -> None:
    """D13 fallback: tax the invoice prints that no line carries yet is spread
    over the positive-amount lines in proportion to their amount. Mutates
    `lines` (anything with .amount / .tax_amount)."""
    residual = _q2(invoice_tax) - sum((_q2(l.tax_amount or 0) for l in lines), _ZERO)
    if residual == 0:
        return
    targets = [l for l in lines if (l.amount or 0) > 0] or list(lines)
    for l, part in zip(targets, _spread(residual, [Decimal(l.amount) for l in targets])):
        l.tax_amount = _q2((l.tax_amount or 0) + part)


async def _next_invoice_ref(db: AsyncSession) -> str:
    # The same INV-YYYY- sequence as every invoice, and the same never-reuse rule.
    from app.crud.invoice import _next_ref
    return await _next_ref(db)


async def create_invoice(db: AsyncSession, payload, user_id: uuid.UUID) -> Invoice:
    carrier = await get_carrier(db, payload.carrier_id)
    if not carrier.is_active:
        raise FreightError(422, "Carrier is inactive")
    vendor = await db.get(Vendor, carrier.vendor_id)
    dup = await find_duplicate_invoice(db, carrier.vendor_id, payload.vendor_invoice_number)
    if dup:
        raise FreightError(409, f"Invoice {payload.vendor_invoice_number.strip()} from "
                                f"{vendor.name} is already recorded ({dup}).")
    amount, tax = _q2(payload.amount), _q2(payload.tax_amount)
    name = (await db.execute(select(User.full_name).where(User.id == user_id))).scalar_one_or_none()
    inv = Invoice(
        internal_ref=await _next_invoice_ref(db),
        vendor_invoice_number=payload.vendor_invoice_number.strip(),
        vendor_id=carrier.vendor_id, vendor_name=vendor.name,
        amount=amount, tax_amount=tax, total_amount=amount + tax,
        currency=payload.currency, invoice_date=payload.invoice_date, due_date=payload.due_date,
        status="unmatched", line_items=[], file_name=payload.file_name,
        file_size=payload.file_size, notes=payload.notes,
        uploaded_by=user_id, uploaded_by_name=name,
        match_route="freight", match_route_auto=True, freight_carrier_id=carrier.id,
    )
    db.add(inv)
    try:
        async with invoice_dedup.key_violation_as_duplicate(vendor.name, payload.vendor_invoice_number):
            await db.flush()
    except invoice_dedup.DuplicateInvoice as e:
        raise FreightError(409, str(e))
    lines = []
    for i, ln in enumerate(payload.lines):
        lead = normalize_tracking(ln.tracking_no)
        pkgs = [p for p in (normalize_tracking(x) for x in ln.package_tracking_nos) if p]
        if lead and lead not in pkgs:
            pkgs.insert(0, lead)
        lines.append(FreightInvoiceLine(
            invoice_id=inv.id, sort_order=i, charge_type=ln.charge_type,
            tracking_no=lead or (pkgs[0] if pkgs else None), package_tracking_nos=pkgs,
            reference=ln.reference, ship_date=ln.ship_date, description=ln.description,
            amount=_q2(ln.amount), tax_amount=_q2(ln.tax_amount or 0)))
    distribute_residual_tax(tax, lines)
    for ln in lines:
        db.add(ln)
    await db.flush()
    await auto_match(db, inv)
    return inv


async def _lines(db: AsyncSession, invoice_id: uuid.UUID) -> list[FreightInvoiceLine]:
    return list((await db.execute(
        select(FreightInvoiceLine).where(FreightInvoiceLine.invoice_id == invoice_id)
        .order_by(FreightInvoiceLine.sort_order))).scalars().all())


async def auto_match(db: AsyncSession, inv: Invoice) -> None:
    """Design §6. Idempotent; never touches a line a human has decided (manual)."""
    carrier = await get_carrier(db, inv.freight_carrier_id)
    lines = await _lines(db, inv.id)
    nos = {n for ln in lines for n in (ln.package_tracking_nos or [])}
    shipments = {}
    if nos:
        for s in (await db.execute(select(FreightShipment).where(
                FreightShipment.carrier_id == carrier.id,
                FreightShipment.tracking_no.in_(nos),
                FreightShipment.status != "void"))).scalars():
            shipments[s.tracking_no] = s
    claimed: set[uuid.UUID] = {ln.shipment_id for ln in lines
                               if ln.match_status == "manual" and ln.shipment_id}
    by_date: dict = defaultdict(list)

    for ln in lines:
        if ln.match_status == "manual":
            if ln.charge_type == "shipment" and ln.cost_center_id:
                by_date[ln.ship_date].append(ln)
            continue
        ln.shipment_id, ln.linked_line_id = None, None
        if ln.charge_type in ("shipment", "adjustment") and ln.package_tracking_nos:
            hits = [shipments[n] for n in ln.package_tracking_nos if n in shipments]
            hit = next((s for s in hits if s.id not in claimed), None)
            if hit is None:
                ln.cost_center_id, ln.match_status = None, "unregistered"
                if ln.charge_type == "adjustment":
                    ln.match_status = "unmatched"
            elif hit.status == "pending" or ln.charge_type == "adjustment" or \
                    await _billed_only_by(db, hit.id, inv.id):
                # An adjustment follows the original shipment wherever it was billed.
                ln.shipment_id, ln.cost_center_id, ln.match_status = hit.id, hit.cost_center_id, "auto"
                claimed.add(hit.id)
            else:
                ln.shipment_id, ln.cost_center_id = hit.id, hit.cost_center_id
                ln.match_status = "duplicate_billing"
                claimed.add(hit.id)
            if ln.charge_type == "shipment" and ln.cost_center_id:
                by_date[ln.ship_date].append(ln)
        elif ln.charge_type in ("shipment", "adjustment"):
            ln.cost_center_id, ln.match_status = None, "unmatched"

    for ln in lines:
        if ln.match_status == "manual" or ln.charge_type not in ("pickup", "fee"):
            continue
        anchor = None
        if ln.charge_type == "pickup" and ln.ship_date:
            for delta in (0, 1, -1):
                cands = by_date.get(ln.ship_date + timedelta(days=delta))
                if cands:
                    anchor = cands[0]
                    break
        if anchor is not None:
            ln.linked_line_id, ln.cost_center_id, ln.match_status = anchor.id, anchor.cost_center_id, "auto"
        elif carrier.default_cost_center_id:
            ln.cost_center_id, ln.match_status = carrier.default_cost_center_id, "default"
        else:
            ln.cost_center_id, ln.match_status = None, "unmatched"
    await db.flush()


async def _billed_only_by(db: AsyncSession, shipment_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    """A billed shipment whose billing came from THIS invoice (re-running
    auto-match after reopen) is not a duplicate."""
    other = (await db.execute(
        select(FreightInvoiceLine.id).join(Invoice, Invoice.id == FreightInvoiceLine.invoice_id)
        .where(FreightInvoiceLine.shipment_id == shipment_id,
               FreightInvoiceLine.invoice_id != invoice_id,
               Invoice.status != "unmatched").limit(1))).scalar_one_or_none()
    return other is None


async def previously_billed(db: AsyncSession, lines: list[FreightInvoiceLine],
                            invoice_id: uuid.UUID) -> dict[uuid.UUID, str]:
    ids = [l.shipment_id for l in lines if l.match_status == "duplicate_billing" and l.shipment_id]
    if not ids:
        return {}
    out = {}
    for sid, num, amt in (await db.execute(
        select(FreightInvoiceLine.shipment_id, Invoice.vendor_invoice_number, FreightInvoiceLine.amount)
        .join(Invoice, Invoice.id == FreightInvoiceLine.invoice_id)
        .where(FreightInvoiceLine.shipment_id.in_(ids), FreightInvoiceLine.invoice_id != invoice_id,
               Invoice.status != "unmatched"))).all():
        out[sid] = f"{num} (${amt})"
    return out


async def get_line(db: AsyncSession, line_id: uuid.UUID) -> FreightInvoiceLine:
    ln = await db.get(FreightInvoiceLine, line_id)
    if ln is None:
        raise FreightError(404, "Invoice line not found")
    return ln


async def update_line(db: AsyncSession, inv: Invoice, ln: FreightInvoiceLine, payload) -> None:
    if inv.status != "unmatched":
        raise FreightError(409, "Reopen the invoice before changing its lines")
    data = payload.model_dump(exclude_unset=True)
    if data.get("clear_shipment"):
        ln.shipment_id = None
    if data.get("clear_link"):
        ln.linked_line_id = None
    if data.get("shipment_id"):
        s = await db.get(FreightShipment, data["shipment_id"])
        if s is None or s.carrier_id != inv.freight_carrier_id or s.status == "void":
            raise FreightError(422, "Shipment not found for this carrier")
        ln.shipment_id, ln.cost_center_id = s.id, s.cost_center_id
    if data.get("linked_line_id"):
        anchor = await db.get(FreightInvoiceLine, data["linked_line_id"])
        if anchor is None or anchor.invoice_id != inv.id or anchor.id == ln.id:
            raise FreightError(422, "Linked line must be another line on this invoice")
        ln.linked_line_id = anchor.id
        if anchor.cost_center_id:
            ln.cost_center_id = anchor.cost_center_id
    if data.get("cost_center_id"):
        await _cost_center(db, data["cost_center_id"])
        ln.cost_center_id = data["cost_center_id"]
    if data.get("tax_amount") is not None:
        ln.tax_amount = _q2(data["tax_amount"])
    if data.get("amount") is not None:
        ln.amount = _q2(data["amount"])
    if data.get("tracking_no") is not None or data.get("package_tracking_nos") is not None:
        lead = normalize_tracking(data.get("tracking_no")) if data.get("tracking_no") is not None \
            else ln.tracking_no
        pkgs = [p for p in (normalize_tracking(x) for x in (data.get("package_tracking_nos")
                                                            or ln.package_tracking_nos or [])) if p]
        if lead and lead not in pkgs:
            pkgs.insert(0, lead)
        ln.tracking_no, ln.package_tracking_nos = lead, pkgs
    ln.match_status = "manual"
    # Lines that follow this one (pickups linked to it) follow its cost centre.
    for follower in (await db.execute(select(FreightInvoiceLine).where(
            FreightInvoiceLine.linked_line_id == ln.id))).scalars():
        follower.cost_center_id = ln.cost_center_id
    await db.flush()


async def finalize_blockers(db: AsyncSession, inv: Invoice, lines: list[FreightInvoiceLine]) -> list[str]:
    out = []
    amt = sum((l.amount for l in lines), _ZERO)
    tax = sum((l.tax_amount for l in lines), _ZERO)
    if amt != inv.amount:
        out.append(f"Lines add up to {amt} before tax; the invoice says {inv.amount}.")
    if tax != inv.tax_amount:
        out.append(f"Line taxes add up to {tax}; the invoice says {inv.tax_amount}.")
    missing = [l for l in lines if not l.cost_center_id]
    if missing:
        out.append(f"{len(missing)} line(s) have no cost center.")
    undecided = [l for l in lines if l.match_status == "duplicate_billing"]
    if undecided:
        out.append(f"{len(undecided)} line(s) were already billed on another invoice — "
                   f"confirm each one (keep or change the cost center).")
    return out


async def finalize(db: AsyncSession, inv: Invoice, user_id: uuid.UUID) -> None:
    if inv.status != "unmatched":
        raise FreightError(409, "Invoice is already finalised")
    lines = await _lines(db, inv.id)
    blockers = await finalize_blockers(db, inv, lines)
    if blockers:
        raise FreightError(422, " ".join(blockers))
    for ln in lines:
        if ln.charge_type != "shipment":
            continue
        if ln.shipment_id is None and ln.tracking_no:
            # Billed but never registered: record it so the shipment list stays complete.
            existing = (await db.execute(select(FreightShipment).where(
                FreightShipment.carrier_id == inv.freight_carrier_id,
                FreightShipment.tracking_no == ln.tracking_no))).scalars().first()
            if existing is None:
                cc = await _cost_center(db, ln.cost_center_id)
                name = (await db.execute(select(User.full_name).where(User.id == user_id))).scalar_one_or_none()
                existing = FreightShipment(
                    carrier_id=inv.freight_carrier_id, tracking_no=ln.tracking_no,
                    direction="outbound", ship_date=ln.ship_date, cost_center_id=cc.id,
                    department_id=cc.department_id, source="from_invoice", status="billed",
                    created_by=user_id, created_by_name=name)
                db.add(existing)
                await db.flush()
            ln.shipment_id = existing.id
        if ln.shipment_id:
            s = await db.get(FreightShipment, ln.shipment_id)
            if s.status == "pending":
                s.status = "billed"
            if s.ship_date is None and ln.ship_date:
                s.ship_date = ln.ship_date
    name = (await db.execute(select(User.full_name).where(User.id == user_id))).scalar_one_or_none()
    inv.status = "matched"
    inv.matched_at = datetime.now(timezone.utc)
    inv.matched_by, inv.matched_by_name = user_id, name
    await db.flush()


async def active_pa_for_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> PaymentApplication | None:
    return (await db.execute(select(PaymentApplication).where(
        PaymentApplication.invoice_ids.contains([str(invoice_id)]),
        PaymentApplication.status != "cancelled").limit(1))).scalars().first()


async def reopen(db: AsyncSession, inv: Invoice) -> None:
    if inv.status != "matched":
        raise FreightError(409, "Only a finalised, unpaid invoice can be reopened")
    pa = await active_pa_for_invoice(db, inv.id)
    if pa is not None:
        raise FreightError(409, f"{pa.pa_number} holds this invoice — cancel or return it first")
    inv.status = "unmatched"
    inv.matched_at = None
    await db.flush()
    for ln in await _lines(db, inv.id):
        if ln.shipment_id and ln.match_status != "duplicate_billing":
            s = await db.get(FreightShipment, ln.shipment_id)
            if s.status == "billed" and await _billed_only_by(db, s.id, inv.id):
                s.status = "pending"
    await db.flush()


async def invoice_detail(db: AsyncSession, inv: Invoice) -> dict:
    lines = await _lines(db, inv.id)
    labels = await cost_center_labels(db, [l.cost_center_id for l in lines])
    prev = await previously_billed(db, lines, inv.id)
    carrier = await db.get(FreightCarrier, inv.freight_carrier_id)
    totals: dict = {}
    for l in lines:
        t = totals.setdefault(l.cost_center_id, [_ZERO, _ZERO])
        t[0] += l.amount
        t[1] += l.tax_amount
    pa = await active_pa_for_invoice(db, inv.id)
    line_out = []
    for l in lines:
        code, name, dept = labels.get(l.cost_center_id, (None, None, None))
        line_out.append({**{c.key: getattr(l, c.key) for c in FreightInvoiceLine.__table__.columns},
                         "cost_center_code": code, "cost_center_name": name, "department_name": dept,
                         "previously_billed_on": prev.get(l.shipment_id)})
    return {
        **{k: getattr(inv, k) for k in (
            "id", "internal_ref", "vendor_id", "vendor_name", "vendor_invoice_number", "invoice_date",
            "due_date", "currency", "amount", "tax_amount", "total_amount", "status", "file_name",
            "notes", "uploaded_by_name", "created_at")},
        "carrier_id": inv.freight_carrier_id,
        "carrier_code": carrier.code if carrier else None,
        "lines": line_out,
        "totals_by_cost_center": [
            {"cost_center_id": cc, "cost_center_code": labels.get(cc, (None,))[0],
             "cost_center_name": labels.get(cc, (None, None))[1],
             "department_name": labels.get(cc, (None, None, None))[2],
             "amount": v[0], "tax_amount": v[1]}
            for cc, v in sorted(totals.items(), key=lambda kv: -kv[1][0])],
        "lines_amount_total": sum((l.amount for l in lines), _ZERO),
        "lines_tax_total": sum((l.tax_amount for l in lines), _ZERO),
        "pa_id": pa.id if pa else None,
        "pa_number": pa.pa_number if pa else None,
        "finalize_blockers": (await finalize_blockers(db, inv, lines)) if inv.status == "unmatched" else [],
    }


async def list_invoices(db: AsyncSession, *, status: str | None, carrier_id: uuid.UUID | None,
                        page: int, page_size: int) -> tuple[list[dict], int]:
    q = select(Invoice).where(Invoice.match_route == "freight")
    if status:
        q = q.where(Invoice.status == status)
    if carrier_id:
        q = q.where(Invoice.freight_carrier_id == carrier_id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = list((await db.execute(
        q.order_by(Invoice.due_date.asc(), Invoice.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all())
    if not rows:
        return [], total
    ids = [r.id for r in rows]
    counts = dict((await db.execute(
        select(FreightInvoiceLine.invoice_id, func.count())
        .where(FreightInvoiceLine.invoice_id.in_(ids)).group_by(FreightInvoiceLine.invoice_id))).all())
    carriers = {c.id: c.code for c in (await db.execute(select(FreightCarrier))).scalars()}
    out = []
    for r in rows:
        pa = await active_pa_for_invoice(db, r.id)
        out.append({
            **{k: getattr(r, k) for k in (
                "id", "internal_ref", "vendor_invoice_number", "invoice_date", "due_date", "currency",
                "amount", "tax_amount", "total_amount", "status")},
            "carrier_id": r.freight_carrier_id, "carrier_code": carriers.get(r.freight_carrier_id),
            "line_count": counts.get(r.id, 0),
            "pa_id": pa.id if pa else None, "pa_number": pa.pa_number if pa else None,
            "pa_status": pa.status if pa else None,
        })
    return out, total


async def delete_invoice(db: AsyncSession, inv: Invoice) -> None:
    """Throw away an invoice recorded by mistake (only before it is finalised)."""
    if inv.status != "unmatched":
        raise FreightError(409, "Reopen the invoice before deleting it")
    await db.delete(inv)
    await db.flush()


# ── Freight PA ────────────────────────────────────────────────────────────────

async def _next_pa_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return await next_number(db, PaymentApplication.pa_number, f"PA-{today}-", width=4)


async def create_pa(db: AsyncSession, payload, user_id: uuid.UUID) -> PaymentApplication:
    ids = list(dict.fromkeys(payload.invoice_ids))
    # Lock the invoices: two people raising a PA on the same invoice at once
    # must not both succeed (one bill, paid twice).
    invs = list((await db.execute(
        select(Invoice).where(Invoice.id.in_(ids)).order_by(Invoice.id).with_for_update()
    )).scalars().all())
    if len(invs) != len(ids):
        raise FreightError(404, "Invoice not found")
    if any(i.match_route != "freight" for i in invs):
        raise FreightError(422, "Only freight invoices can go on a Freight PA")
    carriers = {i.freight_carrier_id for i in invs}
    if len(carriers) != 1:
        raise FreightError(422, "All invoices on one PA must be from the same carrier")
    currencies = {i.currency for i in invs}
    if len(currencies) != 1:
        raise FreightError(422, "All invoices on one PA must be in the same currency")
    not_ready = [i.vendor_invoice_number for i in invs if i.status != "matched"]
    if not_ready:
        raise FreightError(422, f"Finish matching first: {', '.join(not_ready)}")
    for i in invs:
        held = await active_pa_for_invoice(db, i.id)
        if held is not None:
            raise FreightError(409, f"Invoice {i.vendor_invoice_number} is already on {held.pa_number}")
    carrier = await db.get(FreightCarrier, carriers.pop())
    invs.sort(key=lambda i: (i.invoice_date, i.vendor_invoice_number))

    subtotal = sum((i.amount for i in invs), _ZERO)
    tax = sum((i.tax_amount for i in invs), _ZERO)
    title = (payload.title or "").strip() or \
        f"Freight — {carrier.name} " + ", ".join(i.vendor_invoice_number for i in invs)
    pa = PaymentApplication(
        pa_number=await _next_pa_number(db), title=title[:255], source="freight",
        vendor_id=carrier.vendor_id, vendor_name=invs[0].vendor_name,
        invoice_ids=[str(i.id) for i in invs], gr_ids=[], pa_type="regular",
        subtotal=subtotal, tax_amount=tax, shipping_amount=_ZERO, other_charges=_ZERO,
        payment_amount=subtotal + tax, currency=invs[0].currency, notes=payload.notes,
        status="draft", created_by=user_id,
    )
    db.add(pa)
    await db.flush()

    for n, inv in enumerate(invs):
        lines = await _lines(db, inv.id)
        db.add(PaLineItem(
            pa_id=pa.id, sort_order=n, qty=Decimal("1"), unit="invoice",
            description=f"{carrier.name} invoice {inv.vendor_invoice_number} "
                        f"({inv.invoice_date.isoformat()}) · {len(lines)} charge(s)",
            unit_price=inv.amount, line_total=inv.amount))
        split: dict[uuid.UUID, list[Decimal]] = {}
        for ln in lines:
            t = split.setdefault(ln.cost_center_id, [_ZERO, _ZERO])
            t[0] += ln.amount
            t[1] += ln.tax_amount
        depts = dict((await db.execute(select(CostCenter.id, CostCenter.department_id)
                                       .where(CostCenter.id.in_(list(split))))).all())
        for cc_id, (amt, tx) in split.items():
            db.add(PaCostAllocation(pa_id=pa.id, invoice_id=inv.id, cost_center_id=cc_id,
                                    department_id=depts[cc_id], budget_code=FREIGHT_BUDGET_CODE,
                                    amount=amt, tax_amount=tx))
    await db.flush()
    return pa


async def cost_allocations(db: AsyncSession, pa_id: uuid.UUID) -> list[dict]:
    rows = list((await db.execute(select(PaCostAllocation).where(
        PaCostAllocation.pa_id == pa_id))).scalars().all())
    if not rows:
        return []
    labels = await cost_center_labels(db, [r.cost_center_id for r in rows])
    invs = dict((await db.execute(select(Invoice.id, Invoice.vendor_invoice_number)
                                  .where(Invoice.id.in_({r.invoice_id for r in rows})))).all())
    out = []
    for r in rows:
        code, name, dept = labels.get(r.cost_center_id, (None, None, None))
        out.append({"invoice_id": r.invoice_id, "invoice_number": invs.get(r.invoice_id),
                    "cost_center_id": r.cost_center_id, "cost_center_code": code,
                    "cost_center_name": name, "department_id": r.department_id,
                    "department_name": dept, "budget_code": r.budget_code,
                    "amount": r.amount, "tax_amount": r.tax_amount})
    out.sort(key=lambda d: (d["invoice_number"] or "", -(d["amount"] or 0)))
    return out
