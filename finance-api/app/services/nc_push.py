"""AP push to NC via XChange (spec 2026-10-27-ap-push-to-nc-xchange-design.md).

Replaces the Excel "Export to NC". One AP invoice = one NC F1 payable bill
(应付单) in SAVED state; the AP clerk reviews and approves it in NC.

Pipeline, each stage separately testable:
  plan_bill()       UniOps data -> BillPlan (dimensions, tax rows, summary, rate)
  NcLookup          NC read-only: supplier country, cost-centre PKs, bill maker
  render_xml()      BillPlan + NC facts -> XChange <ufinterface> XML (pure)
  XChangeClient     POST to NC, parse <sendresult>
  push_invoices()   orchestration + audit (nc_push_log) + ap_invoices stamp

Rules proven on the NC test server 2026-10-27 (see spec §3):
* NC never looks up a rate — rate and every local_* amount are sent.
* def5 (the real cost centre) is not translated -> its NC PK is sent.
* body pk_fiorg / header pk_busitype are omitted (translation traps).
* scomment is the voucher explanation's tail; NC's pfxx billdefine must allow
  250 chars (production rollout step) — def16 keeps a <=101 copy.
* replace="Y" + the same bill id replaces a SAVED bill; an approved bill is
  refused by NC itself (-32000), which is what protects the clerk's work.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape, quoteattr
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ap_invoice import DRAFT, VOID, ApInvoice, ApInvoiceTaxLine
from app.models.mirrors import (CostCenter, Department, ExpenseInvoice, FreightInvoiceLine,
                                Invoice, InvoicePoAllocation, PurchaseAgreement, PurchaseOrder,
                                PurchaseRequest, User)
from app.models.nc_push import NcPushLog
from app.models.pa import PaymentApplication
from app.models.posting import PostingEvent, PostingLine
from app.services import fx_rates
from app.services.nc_ap_sync import normalise_invoice_no

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_CENT = Decimal("0.01")
NC_TZ = ZoneInfo("America/Toronto")

# ── fixed NC header values (944 of 944 clerk bills carry exactly these) ─────
ORG = "01010104"
GROUP = "0"
BILL_TYPE = "F1"
TRADE_TYPE = "F1-Cxx-017"
BILL_CLASS = "yf"
PAY_TERM = "FH01"
COUNTRY = "CA"
SRC_SYSCODE = 17              # what NC stamps on every XChange bill
EXTERNAL_ID_PREFIX = "UNIOPS-"
SCOMMENT_MAX = 250            # pfxx billdefine, raised from 20 (rollout step)
DEF16_MAX = 101               # DB column

# Every freight cost lands on this budget account (freight design D6).
FREIGHT_REVEXP = "CRM0150102"

# UniOps tax code -> NC tax code. The rate on the row is the invoice's actual
# rate: NC keeps explicit amounts as sent (proven: 001 at 15% stays 15%).
NC_TAX_CODE = {"HST_ON": "001", "HST_NS": "001", "GST": "005", "QST": "004"}
NC_TAX_ZERO = "006"

# UniOps cost centre -> (NC COSTCENTER parent code, NC DEF5 actual cost centre).
# 2026-10-21 mapping study; GA-*/SELL-* outside this list carry no NC cost centre.
NC_CC = {
    "MOH-0106-E01": ("ENG", "E04"),
    "MOH-0101": (None, "H01"),
    "MOH-0105-LAB": ("QA", "Q01"),
    "GA-0105": ("QA", "Q01"),
    "MOH-0104-P01": (None, "P01"),
    "MOH-0104-P02": (None, "P02"),
    "MOH-0104-P03": (None, "P03"),
    "MOH-0107-S02": ("SC", "S02"),
    "GA-0107": ("SC", "S02"),
}
QA_LAB_CC = {"MOH-0105-LAB", "GA-0105"}
# Manufacturing overhead: 510102 (variable) unless one of these -> 510101 (fixed).
MOH_FIXED_REVEXP = {"CRM00503", "CRM0111302", "CRM0111303", "CRM0111305",
                    "CRM01114", "CRM01105", "CRM00301"}
QA_LAB_VARIABLE_REVEXP = {"CRM0111301", "CRM0111304", "CRM00204"}


class PushRefused(Exception):
    """A reason this AP cannot be pushed, worded for the AP clerk."""


# ── rules (pure) ─────────────────────────────────────────────────────────────

def account_for(cc_code: str | None, dept_code: str | None, revexp: str | None) -> str:
    """NC expense account (subjcode). 88% on the 2026-10-21 study (old code 21%)."""
    u = (cc_code or "").upper()
    if u:
        if u.startswith("MOH") or u in ("GA-0105", "GA-0107"):
            mfg = True
        elif u.startswith("SELL"):
            return "660101"
        elif u.startswith("RD"):
            return "5301"
        else:
            return "6602"
    else:
        d = dept_code or ""
        if d in ("0110", "0111", "0112"):
            return "660101"
        if d == "0109":
            return "5301"
        mfg = d in ("0104", "0105", "0106")
        if not mfg:
            return "6602"
    rv = revexp or ""
    if rv in MOH_FIXED_REVEXP:
        return "510101"
    if u in QA_LAB_CC and rv not in QA_LAB_VARIABLE_REVEXP:
        return "510101"
    return "510102"


# Statutory rates a row's rate is snapped to when the invoice's rounding puts it
# a hair off (3.90 on 29.98 = 13.01%). NC keeps the explicit amounts either way.
NOMINAL_RATES = (Decimal("13"), Decimal("5"), Decimal("15"), Decimal("14"),
                 Decimal("9.975"), Decimal("14.975"))


def nc_tax(uniops_code: str | None, base: Decimal, tax: Decimal) -> tuple[str, Decimal]:
    """-> (NC tax code, rate %). Raises PushRefused on a code NC has no match for."""
    if tax == _ZERO:
        return NC_TAX_ZERO, _ZERO
    rate = (tax / base * 100).quantize(_CENT, ROUND_HALF_UP) if base else _ZERO
    rate = next((n for n in NOMINAL_RATES if abs(rate - n) <= Decimal("0.1")), rate)
    if uniops_code:
        code = NC_TAX_CODE.get(uniops_code.upper())
        if code is None:
            raise PushRefused(f"Tax code {uniops_code} has no NC tax code")
        return code, rate
    # No tax line recorded (older invoices): infer from the rate.
    for target, code in ((Decimal("13"), "001"), (Decimal("15"), "001"),
                         (Decimal("14"), "001"), (Decimal("5"), "005")):
        if abs(rate - target) <= Decimal("0.15"):
            return code, rate
    raise PushRefused(f"Tax {tax} on {base} ({rate}%) has no tax line and no NC tax code fits")


# Rates tried, in order, when the taxed part of an invoice must be inferred.
# HST 13 first: almost every vendor is in Ontario. 5% last — any tax in cents
# reproduces at 5%, so it would win every time if tried earlier.
_INFER_RATES = (Decimal("13"), Decimal("15"), Decimal("14"), Decimal("5"))


def _fits_nominal(base: Decimal, tax: Decimal) -> bool:
    if not base:
        return False
    rate = tax / base * 100
    return any(abs(rate - t) <= Decimal("0.15") for t in _INFER_RATES)


def _taxed_part(amount: Decimal, tax: Decimal) -> Decimal | None:
    """The part of `amount` that carries `tax` at a statutory rate, or None."""
    for r in _INFER_RATES:
        part = (tax * 100 / r).quantize(_CENT, ROUND_HALF_UP)
        if _ZERO < part < amount and (part * r / 100).quantize(_CENT, ROUND_HALF_UP) == tax:
            return part
    return None


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n].rstrip()


def external_id(ap: ApInvoice) -> str:
    return f"{EXTERNAL_ID_PREFIX}{ap.ap_invoice_number}"


def money(v: Decimal) -> str:
    return str(Decimal(v).quantize(_CENT, ROUND_HALF_UP))


def rate_str(v: Decimal) -> str:
    s = format(Decimal(v).normalize(), "f")
    return s


# ── plan ─────────────────────────────────────────────────────────────────────

@dataclass
class Dims:
    cc_code: str | None = None          # UniOps cost-centre code
    dept_code: str | None = None        # NC department code (= UniOps code)
    revexp: str | None = None           # 收支项目 / budget code
    cc_nc: str | None = None            # NC COSTCENTER code
    def5_code: str | None = None        # NC DEF5 cost-centre code


@dataclass
class BodyRow:
    notax: Decimal
    tax: Decimal
    nc_tax_code: str
    tax_rate: Decimal
    dims: Dims
    account: str

    @property
    def total(self) -> Decimal:
        return self.notax + self.tax


@dataclass
class BillPlan:
    ap_id: uuid.UUID
    ap_number: str
    external_id: str
    supplier_code: str
    currency: str
    rate: Decimal
    rate_date: date
    invoice_no: str
    summary: str
    rows: list[BodyRow] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((r.total for r in self.rows), _ZERO)


def _leaf_budget(code: str | None) -> str | None:
    """budget_code is sometimes stored as "CODE-Name" (known composite values);
    NC wants the code alone."""
    if not code:
        return None
    return code.strip().split(" ")[0].split("-")[0] or None


async def _has_accrual(db: AsyncSession, ap_id: uuid.UUID) -> bool:
    row = (await db.execute(
        select(PostingLine.account_code)
        .join(PostingEvent, PostingLine.event_id == PostingEvent.id)
        .where(PostingEvent.source_doc_type == "ap_invoice",
               PostingEvent.source_doc_id == ap_id,
               PostingEvent.event_type == "accrual",
               PostingLine.line_role == "purchase_expense")
        .limit(1))).scalar_one_or_none()
    return row is not None


class _Ref:
    """Cost centres / departments, loaded once per push."""

    def __init__(self, ccs: dict, depts: dict):
        self.ccs = ccs
        self.depts = depts
        self.dept_by_name = {d.name.lower(): d for d in depts.values() if d.name}

    @classmethod
    async def load(cls, db: AsyncSession) -> "_Ref":
        ccs = {c.id: c for c in (await db.execute(select(CostCenter))).scalars()}
        depts = {d.id: d for d in (await db.execute(select(Department))).scalars()}
        return cls(ccs, depts)

    def dims(self, *, cc_id=None, dept_id=None, dept_name=None, revexp=None) -> Dims:
        cc = self.ccs.get(cc_id) if cc_id else None
        dept = self.depts.get(dept_id) if dept_id else None
        if dept is None and cc is not None and cc.department_id:
            dept = self.depts.get(cc.department_id)
        if dept is None and dept_name:
            needle = dept_name.strip().lower()
            for tier in (lambda dn: dn == needle,
                         lambda dn: dn.startswith(needle) or needle.startswith(dn),
                         lambda dn: needle in dn):
                hits = sorted((dn for dn in self.dept_by_name if tier(dn)), key=len)
                if hits:
                    dept = self.dept_by_name[hits[0]]
                    break
        cc_code = cc.code if cc else None
        cc_nc, def5 = NC_CC.get(cc_code or "", (None, None))
        return Dims(cc_code=cc_code, dept_code=dept.code if dept else None,
                    revexp=_leaf_budget(revexp), cc_nc=cc_nc, def5_code=def5)


async def _summary_and_dims(db: AsyncSession, ap: ApInvoice, ref: _Ref):
    """-> (route, summary, dims, freight_split | None). The summary rule (user,
    2026-10-27): one PO = number + title; several POs = numbers only;
    agreement = number + title; Direct = PA number + title; courier = Freight + no."""
    if ap.source == "oa":
        ei = (await db.execute(select(ExpenseInvoice).where(
            ExpenseInvoice.id == ap.source_invoice_id))).scalar_one_or_none()
        pa = None
        if ei is not None and ei.pa_id is not None:
            pa = (await db.execute(select(PaymentApplication).where(
                PaymentApplication.id == ei.pa_id))).scalar_one_or_none()
        if pa is None:
            raise PushRefused("OA invoice is not on a Direct PA yet")
        return ("direct", f"{pa.pa_number} {pa.title}",
                ref.dims(cc_id=pa.cost_center_id, dept_id=pa.department_id,
                         revexp=pa.budget_account_code), None)

    inv = (await db.execute(select(Invoice).where(
        Invoice.id == ap.source_invoice_id))).scalar_one_or_none()
    if inv is None:
        raise PushRefused("Source EPMS invoice not found")
    route = inv.match_route or "po"

    if route == "freight":
        split: dict = {}
        for cc_id, amt, tx in (await db.execute(
                select(FreightInvoiceLine.cost_center_id, FreightInvoiceLine.amount,
                       FreightInvoiceLine.tax_amount)
                .where(FreightInvoiceLine.invoice_id == inv.id))).all():
            t = split.setdefault(cc_id, [_ZERO, _ZERO])
            t[0] += amt
            t[1] += tx
        if not split or None in split:
            raise PushRefused("Courier invoice is not matched to cost centres yet")
        return ("freight", f"Freight {ap.vendor_invoice_number or ''}".strip(), None,
                {cc_id: (ref.dims(cc_id=cc_id, revexp=FREIGHT_REVEXP), a, t)
                 for cc_id, (a, t) in split.items()})

    if route == "direct":
        pa = (await db.execute(select(PaymentApplication).where(
            PaymentApplication.invoice_ids.contains([str(inv.id)]),
            PaymentApplication.status != "cancelled").limit(1))).scalars().first()
        if pa is None:
            raise PushRefused("Direct invoice is not on a Direct PA yet")
        return ("direct", f"{pa.pa_number} {pa.title}",
                ref.dims(cc_id=pa.cost_center_id, dept_id=pa.department_id,
                         revexp=pa.budget_account_code), None)

    if route == "agreement" and inv.agreement_id:
        agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == inv.agreement_id))).scalar_one_or_none()
        if agr is None:
            raise PushRefused("Agreement of this invoice not found")
        return ("agreement", f"{agr.number} {agr.title}",
                ref.dims(cc_id=agr.cost_center_id, dept_id=agr.department_id,
                         revexp=agr.budget_code), None)

    # PO route: allocations first (multi-PO), else the invoice's own PO.
    allocs = (await db.execute(select(InvoicePoAllocation).where(
        InvoicePoAllocation.invoice_id == inv.id))).scalars().all()
    weights: dict = {}
    for a in allocs:
        weights[a.po_id] = weights.get(a.po_id, _ZERO) + (a.allocated_amount or _ZERO)
    if not weights and inv.po_id:
        weights[inv.po_id] = _ZERO
    if not weights:
        raise PushRefused("Invoice has no PO, agreement or Direct PA")
    pos = {p.id: p for p in (await db.execute(select(PurchaseOrder).where(
        PurchaseOrder.id.in_(list(weights))))).scalars()}
    from_nc = sorted(p.number for p in pos.values() if (p.source or "").lower() == "nc")
    if from_nc:
        raise PushRefused(f"{', '.join(from_nc)} came from NC — NC makes its payable from the "
                          f"purchase invoice (D1); not pushed")
    if len(pos) == 1:
        po = next(iter(pos.values()))
        summary = f"{po.number} {po.title}"
    else:
        summary = " / ".join(sorted(p.number for p in pos.values()))
    # Dimensions from the dominant PO's PR (largest allocation), as before.
    top_po = max(weights, key=lambda k: weights[k])
    pr = (await db.execute(select(PurchaseRequest).where(
        PurchaseRequest.po_id == top_po).limit(1))).scalars().first()
    dims = (ref.dims(cc_id=pr.cost_center_id, dept_name=pr.department_name,
                     revexp=pr.budget_code) if pr else Dims())
    return ("po", summary, dims, None)


async def plan_bill(db: AsyncSession, ap: ApInvoice, ref: _Ref | None = None,
                    today: date | None = None) -> BillPlan:
    """Everything UniOps knows, checked. Raises PushRefused with the reason."""
    ref = ref or await _Ref.load(db)
    today = today or datetime.now(NC_TZ).date()
    if ap.status in (DRAFT, VOID):
        raise PushRefused(f"AP is {ap.status}")
    if not await _has_accrual(db, ap.id):
        raise PushRefused("AP has no accrual posting")
    created = ap.created_at.astimezone(NC_TZ).date() if ap.created_at else today
    if ap.invoice_date > max(created, today) or (ap.due_date and ap.invoice_date > ap.due_date):
        raise PushRefused(f"Check the invoice date ({ap.invoice_date.isoformat()} is after "
                          f"the entry or due date — day and month may be swapped)")
    vendor = None
    if ap.vendor_id:
        vendor = (await db.execute(text("select erp_id from vendors where id = :i"),
                                   {"i": ap.vendor_id})).one_or_none()
    if vendor is None:
        # Not the same fix as a blank ERP ID: the vendor row itself is gone
        # (or the AP never had one), so there is nothing to fill in.
        raise PushRefused(f"Vendor {ap.vendor_name or '(none)'} is not in the vendor master — "
                          "the AP points at a vendor record that does not exist")
    erp_id = vendor.erp_id
    if not erp_id:
        raise PushRefused("Vendor has no ERP ID (NC supplier code)")

    currency = fx_rates.iso_currency(ap.currency)
    hit = await fx_rates.rate_for(db, currency, ap.invoice_date)
    if hit is None:
        raise PushRefused(f"No {currency} exchange rate for {ap.invoice_date.isoformat()} "
                          f"(Finance → Exchange Rates)")

    route, summary, dims, freight = await _summary_and_dims(db, ap, ref)

    tax_lines = (await db.execute(select(ApInvoiceTaxLine).where(
        ApInvoiceTaxLine.invoice_id == ap.id).order_by(ApInvoiceTaxLine.line_no))).scalars().all()
    rows: list[BodyRow] = []
    if freight is not None:
        line_code = next((t.tax_code for t in tax_lines if t.tax_code), None)
        for _, (d, amt, tx) in sorted(freight.items(), key=lambda kv: -kv[1][1]):
            code, rate = nc_tax(line_code if tx else None, amt, tx)
            rows.append(BodyRow(amt, tx, code, rate, d,
                                account_for(d.cc_code, d.dept_code, d.revexp)))
    else:
        acct = account_for(dims.cc_code, dims.dept_code, dims.revexp)
        merged: dict[str | None, list[Decimal]] = {}
        for t in tax_lines:
            key = (t.tax_code or "").upper() or None
            m = merged.setdefault(key, [_ZERO, _ZERO])
            m[0] += t.taxable_base or _ZERO
            m[1] += t.tax_amount or _ZERO
        if not merged:
            merged[None] = [ap.amount, ap.tax_amount]
            # Mixed-rate invoice keyed without tax lines (Culligan 64069TP: 0.98 on
            # 197.50 = 0.5%): the tax belongs to part of the amount only. Infer
            # that part from the first statutory rate that gives the tax back to
            # the cent; the rest becomes the zero-rated row below (user, 2026-10-30).
            if ap.tax_amount > _ZERO and not _fits_nominal(ap.amount, ap.tax_amount):
                part = _taxed_part(ap.amount, ap.tax_amount)
                if part is not None:
                    merged[None] = [part, ap.tax_amount]
        # A tax line with tax but no taxable base (11 of 642 in production,
        # 2026-10-27) was keyed with the tax only: its base is the invoice's
        # pre-tax amount less what the other lines cover.
        unbased = [k for k, (b, t) in merged.items() if b == _ZERO and t != _ZERO]
        if len(unbased) > 1:
            raise PushRefused("Several tax lines have no taxable base — fix the tax lines")
        if unbased:
            covered = sum((b for k, (b, _) in merged.items() if k != unbased[0]), _ZERO)
            merged[unbased[0]][0] = ap.amount - covered
        for code_u, (base, tx) in merged.items():
            if base == _ZERO and tx == _ZERO:
                continue
            code, rate = nc_tax(code_u, base, tx)
            rows.append(BodyRow(base, tx, code, rate, dims, acct))
        # An untaxed portion the tax lines do not cover (mixed-rate invoices):
        # its own zero-rated row, so every row has one rate (user, 2026-10-27).
        remainder = ap.total_amount - sum((r.total for r in rows), _ZERO)
        if remainder > _ZERO:
            rows.append(BodyRow(remainder, _ZERO, NC_TAX_ZERO, _ZERO, dims, acct))
        elif remainder < _ZERO:
            raise PushRefused(f"Tax lines add up to more than the AP total "
                              f"({money(ap.total_amount - remainder)} vs {money(ap.total_amount)})")
    if not rows:
        raise PushRefused("AP has no amount")
    total = sum((r.total for r in rows), _ZERO)
    if total != ap.total_amount:
        raise PushRefused(f"Rows add up to {money(total)}, AP total is {money(ap.total_amount)}")

    return BillPlan(ap_id=ap.id, ap_number=ap.ap_invoice_number, external_id=external_id(ap),
                    supplier_code=erp_id, currency=currency, rate=hit.rate,
                    rate_date=hit.effective_date,
                    invoice_no=(ap.vendor_invoice_number or ap.ap_invoice_number).strip(),
                    summary=summary, rows=rows)


# ── NC facts (read-only) ─────────────────────────────────────────────────────

@dataclass
class NcFacts:
    supplier_country: str | None          # None = supplier code not in NC
    costcenter_pk: dict[str, str]         # NC cccode -> pk_costcenter
    maker_user_codes: list[str]           # enabled NC users of the pusher's person code
    # NC bd_currinfo.convmode for currency -> CAD: False = local = amount x rate
    # (USD, EUR, GBP); True = NC quotes "1 CAD = rate CNY", local = amount / rate.
    rate_divides: bool = False


class NcLookup:
    """Live NC read through the finance NC connection (settings.nc_*). The
    XChange target and this connection must be the same NC."""

    def facts(self, supplier_code: str, cc_codes: set[str], person_code: str | None,
              currency: str = "CAD") -> NcFacts:
        import oracledb
        dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
        con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
        try:
            cur = con.cursor()
            cur.execute("select c.code from NCSC.BD_SUPPLIER s "
                        "left join NCSC.BD_COUNTRYZONE c on c.pk_country = s.pk_country "
                        "where s.code = :1 and nvl(s.dr,0) = 0", [supplier_code])
            r = cur.fetchone()
            country = (r[0] or "") if r else None
            pks: dict[str, str] = {}
            if cc_codes:
                binds = ",".join(f":c{i}" for i in range(len(cc_codes)))
                cur.execute("select cc.cccode, cc.pk_costcenter from NCSC.RESA_COSTCENTER cc "
                            "join NCSC.ORG_ORGS o on o.pk_org = cc.pk_org "
                            f"where o.code = :org and cc.cccode in ({binds}) "
                            "and nvl(cc.dr,0) = 0",
                            {"org": ORG, **{f"c{i}": c for i, c in enumerate(sorted(cc_codes))}})
                pks = {c: pk for c, pk in cur.fetchall()}
            users: list[str] = []
            if person_code:
                cur.execute("select u.user_code from NCSC.BD_PSNDOC p "
                            "join NCSC.SM_USER u on u.pk_psndoc = p.pk_psndoc "
                            "where p.code = :1 and nvl(p.dr,0) = 0 and nvl(u.dr,0) = 0 "
                            "and u.enablestate = 2 order by u.user_code", [person_code])
                users = [u for (u,) in cur.fetchall()]
            divides = False
            if currency != fx_rates.BASE:
                cur.execute("select max(i.convmode) from NCSC.BD_CURRINFO i "
                            "join NCSC.BD_CURRTYPE c on c.pk_currtype = i.pk_currtype "
                            "join NCSC.BD_CURRTYPE o on o.pk_currtype = i.oppcurrtype "
                            "where c.code = :1 and o.code = 'CAD' and nvl(i.dr,0) = 0", [currency])
                r = cur.fetchone()
                divides = bool(r and r[0] == 1)
            return NcFacts(country, pks, users, divides)
        finally:
            con.close()

    def bill_no(self, pk: str) -> str | None:
        import oracledb
        dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
        con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
        try:
            cur = con.cursor()
            cur.execute("select billno from NCSC.AP_PAYABLEBILL where pk_payablebill = :1", [pk])
            r = cur.fetchone()
            return r[0] if r else None
        finally:
            con.close()


def maker_code(facts: NcFacts, person_code: str | None) -> str:
    if not person_code:
        raise PushRefused("Your UniOps user has no ERP ID — ask an admin to set it")
    if not facts.maker_user_codes:
        raise PushRefused(f"Your ERP ID {person_code} does not resolve to an enabled NC user")
    if len(facts.maker_user_codes) > 1:
        raise PushRefused(f"Your ERP ID {person_code} has several NC users "
                          f"({', '.join(facts.maker_user_codes)}) — keep one enabled")
    return facts.maker_user_codes[0]


# ── XML (pure) ───────────────────────────────────────────────────────────────

def _tags(d: dict, ind: str) -> str:
    return "".join(f"{ind}<{k}>{escape(str(v))}</{k}>\n" for k, v in d.items()
                   if v is not None and v != "")


def render_xml(plan: BillPlan, *, facts: NcFacts, maker: str, now: datetime) -> str:
    if facts.supplier_country is None:
        raise PushRefused(f"Supplier {plan.supplier_code} is not in NC")
    # 2 = domestic purchase, 4 = import. Import bills may carry no tax in
    # XChange ("组织本币无税金额不等于价税合计" — proven on the NC test server
    # 2026-10-27), so a foreign supplier that charges Canadian tax is booked
    # as domestic: the tax is a Canadian input tax either way (user confirmed
    # 2026-10-28; the check is ArapExpPfxxValidater.checkTaxByOut, XChange only).
    taxed = any(r.tax != _ZERO for r in plan.rows)
    buysell = "2" if (facts.supplier_country == COUNTRY or taxed) else "4"
    billdate = now.astimezone(NC_TZ).strftime("%Y-%m-%d %H:%M:%S")
    # NC's own quotation: a dividing currency (CNY) is quoted as 1 CAD = x, the
    # inverse of Bank of Canada's CAD per unit; NC re-derives local from it with
    # zero tolerance ("本币变动超过最大折算误差"), so local uses the same figure.
    if facts.rate_divides and plan.rate != 1:
        R = (Decimal(1) / plan.rate).quantize(Decimal("0.00000001"), ROUND_HALF_UP)
    else:
        R = plan.rate

    def local(v: Decimal) -> str:
        return money(v / R if facts.rate_divides and plan.rate != 1 else v * R)

    summary_s = clip(plan.summary, SCOMMENT_MAX)
    summary_d = clip(plan.summary, DEF16_MAX)
    head = {
        "pk_group": GROUP, "pk_org": ORG, "pk_fiorg": ORG, "sett_org": ORG,
        "pk_billtype": BILL_TYPE, "pk_tradetype": TRADE_TYPE, "billclass": BILL_CLASS,
        "billdate": billdate, "billyear": billdate[:4], "billperiod": billdate[5:7],
        "billmaker": maker, "creator": maker, "pk_currtype": plan.currency,
        "money": money(plan.total), "local_money": local(plan.total),
        "billstatus": "-1", "isinit": "N", "isreded": "N", "isflowbill": "N",
        "syscode": "1", "src_syscode": "1", "rececountryid": COUNTRY, "taxcountryid": COUNTRY,
        "accessorynum": "0",
    }
    items = []
    tax_total = sum((r.tax for r in plan.rows), _ZERO)
    for i, r in enumerate(plan.rows):
        def5_pk = facts.costcenter_pk.get(r.dims.def5_code) if r.dims.def5_code else None
        if r.dims.def5_code and not def5_pk:
            raise PushRefused(f"NC cost centre {r.dims.def5_code} not found in NC")
        if r.dims.cc_nc and r.dims.cc_nc not in facts.costcenter_pk:
            raise PushRefused(f"NC cost centre {r.dims.cc_nc} not found in NC")
        if not r.dims.revexp:
            raise PushRefused("No budget item (收支项目) on the PR / agreement / PA")
        if not r.dims.dept_code:
            raise PushRefused("No department on the PR / agreement / PA")
        items.append({
            "pk_group": GROUP, "pk_org": ORG, "sett_org": ORG,
            "pk_billtype": BILL_TYPE, "pk_tradetype": TRADE_TYPE, "billclass": BILL_CLASS,
            "billdate": billdate, "busidate": billdate, "rowno": str(i),
            "direction": "-1", "objtype": "1",
            "supplier": plan.supplier_code, "ordercubasdoc": plan.supplier_code,
            "pk_deptid": r.dims.dept_code, "pk_subjcode": r.dims.revexp, "subjcode": r.account,
            "costcenter": r.dims.cc_nc, "def5": def5_pk,
            "pk_currtype": plan.currency, "rate": rate_str(R),
            "taxcodeid": r.nc_tax_code, "taxrate": rate_str(r.tax_rate), "taxtype": "1",
            "buysellflag": buysell, "sendcountryid": facts.supplier_country or None,
            "money_cr": money(r.total), "local_money_cr": local(r.total),
            "money_bal": money(r.total), "local_money_bal": local(r.total),
            "occupationmny": money(r.total),
            "notax_cr": money(r.notax), "local_notax_cr": local(r.notax),
            "local_tax_cr": local(r.tax), "caltaxmny": local(r.notax),
            "pk_payterm": PAY_TERM, "invoiceno": plan.invoice_no,
            "scomment": summary_s, "def16": summary_d,
            # the clerks' convention: first row carries the whole bill's tax
            "def1": money(tax_total) if i == 0 else "0.00",
            "pausetransact": "N",
        })
    body = "".join(f"        <item>\n{_tags(it, '          ')}        </item>\n" for it in items)
    eid = quoteattr(plan.external_id)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<ufinterface account={quoteattr(settings.nc_xchange_account)} billtype="F1" '
        f'filename={quoteattr(plan.external_id + ".xml")} groupcode={quoteattr(settings.nc_xchange_group)} '
        f'isexchange="Y" orgcode="{ORG}" receiver="" replace="Y" roottag="" '
        f'sender={quoteattr(settings.nc_xchange_sender)}>\n'
        f'  <bill id={eid}>\n    <billhead>\n{_tags(head, "      ")}'
        f'      <bodys>\n{body}      </bodys>\n    </billhead>\n  </bill>\n</ufinterface>\n')


# ── XChange ──────────────────────────────────────────────────────────────────

@dataclass
class XResult:
    ok: bool
    code: str | None
    message: str
    bill_pk: str | None
    raw: str


def parse_response(raw: str) -> XResult:
    try:
        root = ET.fromstring(raw.encode("utf-8") if isinstance(raw, str) else raw)
    except ET.ParseError:
        return XResult(False, None, f"NC answered something that is not XML: {raw[:300]}", None, raw)
    ok = root.get("successful") == "Y"
    sr = root.find("sendresult")
    code = (sr.findtext("resultcode") or "").strip() if sr is not None else None
    desc = (sr.findtext("resultdescription") or "").strip() if sr is not None else ""
    pk = (sr.findtext("content") or "").strip() if sr is not None else ""
    # NC's description starts "单据 X 开始处理..." — keep only the useful tail.
    lines = [ln.strip() for ln in desc.splitlines() if ln.strip()]
    useful = [ln for ln in lines if "开始处理" not in ln] or lines
    return XResult(ok and bool(pk), code, " ".join(useful)[:2000], pk or None, raw)


class XChangeClient:
    async def post(self, xml: str) -> XResult:
        url = settings.nc_xchange_url
        params = {"account": settings.nc_xchange_account, "groupcode": settings.nc_xchange_group}
        async with httpx.AsyncClient(timeout=settings.nc_xchange_timeout_seconds) as c:
            r = await c.post(url, params=params, content=xml.encode("utf-8"),
                             headers={"Content-Type": "text/xml; charset=UTF-8"})
        raw = r.content.decode("utf-8", errors="replace")
        if r.status_code != 200:
            return XResult(False, str(r.status_code), f"NC XChange HTTP {r.status_code}", None, raw)
        return parse_response(raw)


def xchange_configured() -> bool:
    return bool(settings.nc_xchange_url) and all(
        [settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


# ── NC docs already holding this AP (list column + duplicate guard) ─────────

def match_invoice_no(raw: str | None) -> str | None:
    """The AP <-> NC match form: the mirror's normalised number with hyphens
    dropped too, so "IN-22-1014797" finds NC's "IN-221014797" (user, 2026-10-28).
    Only this match uses it; invoice_no_norm itself is unchanged (duplicate
    reviews are keyed on it)."""
    norm = normalise_invoice_no(raw)
    return (norm.replace("-", "") or None) if norm else None


# SQL twin of match_invoice_no on the mirror column (see crud/ap_invoice._IN_NC_SQL).
LINE_MATCH_SQL = "replace(l.invoice_no_norm, '-', '')"


async def nc_docs_for(db: AsyncSession, aps: list[ApInvoice]) -> dict[uuid.UUID, list[dict]]:
    """AP id -> NC payable bills that carry its invoice number for its supplier,
    from the NC AP mirror (nc_ap_bills / nc_ap_bill_lines), any approval state.
    Numbers match ignoring case, spaces and hyphens (match_invoice_no).
    A bill UniOps pushed and later replaced (src_syscode 17, not the stored
    number) is dropped: the mirror cannot see NC's soft delete until a full
    reload."""
    if not aps:
        return {}
    vendor_ids = list({a.vendor_id for a in aps if a.vendor_id})
    erp = {}
    # vendors is epms-owned (no mirror model); a finance-only schema (tests)
    # may not have it, and then nothing can match.
    has_vendors = (await db.execute(text("select to_regclass('vendors') is not null"))).scalar()
    if vendor_ids and has_vendors:
        erp = {vid: e for vid, e in (await db.execute(
            text("select id, erp_id from vendors where id = any(:ids)"),
            {"ids": vendor_ids})).all()}
    keys = {}
    for a in aps:
        norm = match_invoice_no(a.vendor_invoice_number)
        sup = erp.get(a.vendor_id)
        if norm and sup:
            keys[a.id] = (norm, sup)
    out: dict[uuid.UUID, list[dict]] = {a.id: [] for a in aps}
    if not keys:
        return out
    rows = (await db.execute(text(
        f"select distinct {LINE_MATCH_SQL} as inv_key, l.invoice_no_norm, l.invoice_no, "
        "       l.supplier_code, b.bill_no, b.bill_status, "
        "       b.approve_status, b.src_syscode, b.bill_date, b.money, b.currency "
        "  from nc_ap_bill_lines l join nc_ap_bills b on b.id = l.bill_id "
        f" where {LINE_MATCH_SQL} = any(:n)"),
        {"n": list({k[0] for k in keys.values()})})).all()
    by_key: dict = {}
    for r in rows:
        by_key.setdefault((r.inv_key, r.supplier_code), []).append(r)
    for a in aps:
        k = keys.get(a.id)
        exact = normalise_invoice_no(a.vendor_invoice_number)
        docs: dict[str, dict] = {}
        for r in by_key.get(k, []) if k else []:
            if r.src_syscode == SRC_SYSCODE and r.bill_no != a.nc_bill_no:
                continue
            state = "approved" if (r.bill_status == 1 and r.approve_status == 1) else "saved"
            d = docs.setdefault(r.bill_no, {
                "bill_no": r.bill_no, "state": state, "pushed": r.src_syscode == SRC_SYSCODE,
                "bill_date": r.bill_date.isoformat() if r.bill_date else None,
                "money": str(r.money) if r.money is not None else None,
                "currency": r.currency, "hyphen_match": True, "nc_invoice_no": r.invoice_no})
            # Flag a bill only when no line on it matches with the hyphens kept
            # (user, 2026-10-28: say when the match needed the hyphens dropped).
            if r.invoice_no_norm == exact:
                d["hyphen_match"] = False
                d["nc_invoice_no"] = r.invoice_no
        out[a.id] = sorted(docs.values(), key=lambda d: d["bill_no"])
    return out


# ── orchestration ────────────────────────────────────────────────────────────

def _record_failure(ap: ApInvoice, msg: str) -> None:
    """A failed attempt never hides a bill that is in NC: an AP already pushed
    keeps status 'pushed' and its bill number, with the error alongside."""
    if ap.nc_push_status != "pushed":
        ap.nc_push_status = "failed"
    ap.nc_push_error = msg


@dataclass
class PushOutcome:
    ap_id: uuid.UUID
    ap_number: str
    ok: bool
    message: str
    nc_bill_no: str | None = None


async def push_invoices(db: AsyncSession, ap_ids: list[uuid.UUID], user_id: uuid.UUID, *,
                        lookup: NcLookup | None = None,
                        client: XChangeClient | None = None) -> list[PushOutcome]:
    """Push each AP, one XChange request each, in AP-number order. Every
    attempt that reaches NC is logged; a refusal before NC is reported only
    (nothing was sent). The caller commits."""
    lookup = lookup or NcLookup()
    client = client or XChangeClient()
    aps = (await db.execute(select(ApInvoice).where(ApInvoice.id.in_(ap_ids))
                            .order_by(ApInvoice.ap_invoice_number))).scalars().all()
    person_code = (await db.execute(select(User.erp_person_code).where(
        User.id == user_id))).scalar_one_or_none()
    person_code = (person_code or "").strip() or None
    ref = await _Ref.load(db)
    existing = await nc_docs_for(db, aps)
    outcomes: list[PushOutcome] = []
    for ap in aps:
        try:
            others = [d for d in existing.get(ap.id, []) if not d["pushed"]]
            if others:
                raise PushRefused("Already in NC as " + ", ".join(
                    d["bill_no"] + (f" (NC invoice no. {d['nc_invoice_no']}, matched ignoring hyphens)"
                                    if d["hyphen_match"] else "") for d in others)
                                  + " (entered by hand) — not pushed again")
            plan = await plan_bill(db, ap, ref)
            cc_codes = {c for r in plan.rows for c in (r.dims.cc_nc, r.dims.def5_code) if c}
            facts = await asyncio.to_thread(lookup.facts, plan.supplier_code, cc_codes, person_code,
                                            plan.currency)
            maker = maker_code(facts, person_code)
            now = datetime.now(timezone.utc)
            xml = render_xml(plan, facts=facts, maker=maker, now=now)
        except PushRefused as exc:
            _record_failure(ap, str(exc))
            outcomes.append(PushOutcome(ap.id, ap.ap_invoice_number, False, str(exc)))
            continue

        try:
            res = await client.post(xml)
        except httpx.HTTPError as exc:
            res = XResult(False, None, f"NC XChange not reachable: {exc.__class__.__name__}", None, "")
        bill_no = None
        if res.ok and res.bill_pk:
            try:
                bill_no = await asyncio.to_thread(lookup.bill_no, res.bill_pk)
            except Exception:  # noqa: BLE001 — the bill exists; the number shows up via the mirror
                logger.exception("NC bill number lookup failed for %s", res.bill_pk)
        msg = res.message
        if not res.ok and res.code == "-32000" and "审核" in msg:
            msg = "Already approved in NC — change it in NC. " + msg
        db.add(NcPushLog(ap_invoice_id=ap.id, pushed_by=user_id, pushed_at=now,
                         external_id=plan.external_id, ok=res.ok, result_code=res.code,
                         nc_bill_pk=res.bill_pk, nc_bill_no=bill_no, message=msg,
                         request_xml=xml, response_xml=res.raw))
        if res.ok:
            ap.nc_bill_pk = res.bill_pk
            ap.nc_bill_no = bill_no
            ap.nc_push_status = "pushed"
            ap.nc_push_error = None
            ap.nc_pushed_at = now
            ap.nc_pushed_by = user_id
        else:
            _record_failure(ap, msg)
        outcomes.append(PushOutcome(ap.id, ap.ap_invoice_number, res.ok,
                                    (f"NC bill {bill_no}" if bill_no else "Pushed") if res.ok else msg,
                                    bill_no))
    await db.flush()
    return outcomes
