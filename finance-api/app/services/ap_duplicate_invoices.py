"""Vendor + invoice number must be unique on NC payables — NC does not check.

NC65 accepts any invoice number on a payable, however many payables of the
same vendor already carry it, and the same invoice has been paid twice because
of it. The rule finance set (2026-09-25): **vendor name + invoice number is
unique**. The same number from two different vendors is not a duplicate —
invoice numbers are the vendor's, and "1" or "000001" from two freelancers are
two invoices.

NC is the book of record and cannot be changed from here, so this is DETECTION
on the mirror: it runs after every AP sync (hourly, see
app/tasks/nc_ap_sync_scheduler.py) and raises one standing task for AP
(ap_duplicate_invoice_tasks.py). The earlier it fires the cheaper it is: a draft
can simply not be approved, an approved copy can be held back from the next
payment run, and only a copy that was already paid has to be recovered.

What still does NOT break the rule: a bill and the credit that reverses it.
Quoting the original invoice number on the credit is how NC corrects a payable,
and afterwards only one copy stands (KLN 0006814591-01: +2,746.71 twice,
-2,746.71 once = one live copy). 64 of the vendor/number pairs on production
are exactly that. Netting is per currency and exact amount.

Three kinds of finding, all counted in the AP task:

  exact           the same amount on 2+ live copies — the plain double entry
                  (production 2026-09-25: 36 groups, 94,999.54 CAD and
                  50,054.07 USD billed twice)
  amount_differs  2+ live copies, different amounts. Often the supply chain
                  splitting one invoice into goods and freight bills; finance
                  decided it is still a breach and is cleared by a review
                  ("split") rather than left off the list (113 groups)
  pending         an UNAPPROVED payable (last PENDING_DAYS days) repeating a
                  vendor/number already entered — the one moment it can be
                  stopped before it is even owed

The vendor is matched by NAME (trimmed, case- and whitespace-folded), not by
supplier code — that is the rule as finance stated it. On production every
name maps to exactly one code today, so the two agree; keying on the name keeps
the rule true if a vendor is ever keyed twice under one name.

Matching is on `invoice_no_norm` (upper-cased, whitespace removed — see
nc_ap_sync.normalise_invoice_no). An entry is one (bill, invoice number): a
bill can carry several invoices (1,873 of them do), and the amount that matters
is the invoice's share of it, not the bill total.

Findings are recomputed on every read and never stored. The only thing stored
is finance's verdict on one (0039_ap_inv_dup_reviews), and a verdict covers a
finding only while no bill has been added to it since.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

EXACT = "exact"
AMOUNT_DIFFERS = "amount_differs"
PENDING = "pending"

KINDS = (EXACT, AMOUNT_DIFFERS, PENDING)

KIND_LABELS = {
    EXACT: "Same vendor and invoice number, same amount",
    AMOUNT_DIFFERS: "Same vendor and invoice number, different amounts",
    PENDING: "Unapproved payable repeats a vendor's invoice number already entered",
}

#: Every kind breaks the rule; all of them count toward the AP task.
ACTIONABLE = KINDS

#: How far back an unapproved payable still counts as work in progress. Older
#: drafts are abandoned documents; AP Subledger Health's "Never approved" view
#: already lists those and marks the ones an approved bill superseded.
PENDING_DAYS = 90

#: Shown for a group whose live copies are in more than one currency: its
#: money cannot be added up, so none is reported for it.
MIXED = "MIXED"

_ZERO = Decimal("0")


def vendor_key(name: str | None) -> str:
    """The vendor half of the uniqueness key."""
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


@dataclass(frozen=True)
class Entry:
    """One invoice number on one NC payable."""
    bill_no: str
    nc_pk: str
    effective: bool
    bill_status: int | None
    approve_status: int | None
    trade_type: str | None
    supplier_code: str | None
    supplier_name: str | None
    currency: str | None
    invoice_no: str | None
    invoice_no_norm: str
    bill_date: date | None
    amount: Decimal
    open: Decimal
    dismissed: bool = False

    @property
    def vendor(self) -> str:
        return vendor_key(self.supplier_name)


@dataclass
class Finding:
    kind: str
    invoice_no_norm: str
    invoice_no: str | None
    vendor: str
    currency: str | None
    supplier_code: str | None
    supplier_name: str | None
    amount: Decimal | None
    entries: list[Entry]
    #: Live copies beyond the first.
    extra_copies: int = 0
    #: For EXACT: what was billed on those extra copies.
    extra_amount: Decimal = _ZERO
    #: The part of extra_amount NC still shows as payable — what can still be
    #: stopped rather than recovered.
    open_exposure: Decimal = _ZERO
    review: dict | None = None
    #: A verdict existed but a bill was added after it.
    reopened: bool = False
    key: str = field(init=False)

    def __post_init__(self):
        # vendor + invoice number IS the rule, so it is the identity; the kind
        # rides along because a pending draft becoming an approved copy is a
        # new situation the earlier verdict never covered.
        self.key = ":".join((self.kind, self.vendor, self.invoice_no_norm))

    @property
    def bill_nos(self) -> list[str]:
        return sorted({e.bill_no for e in self.entries})

    @property
    def status(self) -> str:
        """What finance has to do about it."""
        if self.kind == PENDING:
            return "stop_approval"
        if self.kind == AMOUNT_DIFFERS:
            return "review"
        return "stop_payment" if self.open_exposure > 0 else "recover"


# ── detection (pure) ─────────────────────────────────────────────────────────

def _live_counts(entries: list[Entry]) -> Counter:
    """(currency, positive amount) -> copies still standing once equal credits
    are netted. A bill and its full reversal leave nothing; two bills and one
    reversal leave one. Zero-amount entries are placeholders and count for
    nothing."""
    pos = Counter((e.currency, e.amount) for e in entries if e.amount > 0)
    neg = Counter((e.currency, -e.amount) for e in entries if e.amount < 0)
    return Counter({k: n - neg[k] for k, n in pos.items() if n - neg[k] > 0})


def _exposure(entries: list[Entry], extra_amount: Decimal) -> Decimal:
    """How much of the duplicate billing is still unpaid in NC. Capped at the
    duplicate amount: the first copy's balance is legitimately owed."""
    open_total = sum((e.open for e in entries), _ZERO)
    return max(_ZERO, min(open_total, extra_amount))


def _name(entries: list[Entry]) -> str | None:
    return next((e.supplier_name for e in entries if e.supplier_name), None)


def _code(entries: list[Entry]) -> str | None:
    codes = {e.supplier_code for e in entries if e.supplier_code}
    return next(iter(codes)) if len(codes) == 1 else None


def _raw(entries: list[Entry]) -> str | None:
    return next((e.invoice_no for e in entries if e.invoice_no), None)


def _order(entries):
    return sorted(entries, key=lambda e: (e.bill_date or date.min, e.bill_no))


def detect(entries: list[Entry], *, today: date | None = None,
           pending_days: int = PENDING_DAYS) -> list[Finding]:
    """Every finding in `entries`. Pure: no I/O, no clock unless `today` is omitted."""
    today = today or date.today()
    pending_since = today - timedelta(days=pending_days)

    groups: dict[tuple[str, str], list[Entry]] = defaultdict(list)
    for e in entries:
        if e.invoice_no_norm:
            groups[(e.vendor, e.invoice_no_norm)].append(e)

    findings: list[Finding] = []
    for (vendor, norm), group in groups.items():
        live = [e for e in group if e.effective]
        counts = _live_counts(live)
        standing = sum(counts.values())

        # Approved copies: one finding per vendor + number.
        if standing >= 2:
            ccys = {c for c, _ in counts}
            ccy = next(iter(ccys)) if len(ccys) == 1 else MIXED
            # Amounts entered more than once. Money is reported for these only,
            # and only when every copy is in one currency.
            doubled = {k: n for k, n in counts.items() if n >= 2}
            extra = (sum((a * (n - 1) for (_, a), n in doubled.items()), _ZERO)
                     if ccy != MIXED else _ZERO)
            repeated = [e for e in live if (e.currency, abs(e.amount)) in doubled]
            # One amount, and every live copy is it: the plain double entry.
            single = len(doubled) == 1 and next(iter(doubled.values())) == standing
            findings.append(Finding(
                kind=EXACT if doubled else AMOUNT_DIFFERS,
                invoice_no_norm=norm, invoice_no=_raw(live), vendor=vendor,
                currency=ccy, supplier_code=_code(live), supplier_name=_name(live),
                amount=next(iter(doubled))[1] if single else None,
                entries=_order(live), extra_copies=standing - 1, extra_amount=extra,
                open_exposure=_exposure(repeated, extra) if extra else _ZERO))

        # Unapproved payables repeating a vendor/number already entered. Only a
        # positive draft can be a second copy: a draft credit note quoting the
        # invoice it reverses is the correct way to undo one. And an approved
        # bill that was fully reversed no longer counts as "already entered" —
        # re-entering it corrected is the other half of that workflow.
        drafts = [e for e in group
                  if not e.effective and not e.dismissed and e.amount > 0
                  and e.bill_date is not None and e.bill_date >= pending_since]
        draft_bills = {d.bill_no for d in drafts}
        if drafts and (standing or len(draft_bills) >= 2):
            involved = live + drafts
            findings.append(Finding(
                kind=PENDING, invoice_no_norm=norm, invoice_no=_raw(involved),
                vendor=vendor, currency=drafts[0].currency,
                supplier_code=_code(involved), supplier_name=_name(involved),
                amount=None, entries=_order(involved),
                extra_copies=len(draft_bills)))

    rank = {k: i for i, k in enumerate(KINDS)}
    findings.sort(key=lambda f: (rank[f.kind], -f.open_exposure, -f.extra_amount,
                                 f.invoice_no_norm))
    return findings


def apply_reviews(findings: list[Finding], reviews: dict[str, dict]) -> None:
    """Attach live verdicts. A verdict covers a finding only if every bill in
    the finding was in front of the reviewer; a later copy re-opens it."""
    for f in findings:
        r = reviews.get(f.key)
        if r is None:
            continue
        if set(f.bill_nos) <= set(r["bill_nos"]):
            f.review = r
        else:
            f.reopened = True


# ── loading ──────────────────────────────────────────────────────────────────

# One row per (bill, invoice number). Approved payables of any age, plus
# unapproved ones recent enough to still be in someone's hands (-99 is NC's
# deleted state, never work). The dismissal flag rides along so a draft finance
# already set aside on AP Subledger Health is not raised again here.
_ENTRIES_SQL = """
select b.bill_no, b.nc_pk, b.bill_status, b.approve_status, b.trade_type,
       b.currency, b.bill_date::date as bill_date,
       l.supplier_code, max(l.supplier_name) as supplier_name,
       l.invoice_no_norm, max(l.invoice_no) as invoice_no,
       coalesce(sum(l.money_cr), 0) as amount,
       coalesce(sum(l.money_bal), 0) as open,
       exists (select 1 from nc_ap_bill_dismissals d
                where d.bill_no = b.bill_no and d.restored_at is null) as dismissed
  from nc_ap_bill_lines l
  join nc_ap_bills b on b.id = l.bill_id
 where l.invoice_no_norm is not null
   and ((b.bill_status = 1 and b.approve_status = 1)
        or (b.bill_status = -1 and b.bill_date >= %(since)s))
 group by b.bill_no, b.nc_pk, b.bill_status, b.approve_status, b.trade_type,
          b.currency, b.bill_date, l.supplier_code, l.invoice_no_norm
"""

_REVIEWS_SQL = """
select finding_key, kind, bill_nos, reason, note, reviewed_by_name, reviewed_at
  from nc_ap_invoice_dup_reviews where retired_at is null
"""


def _entry(r) -> Entry:
    return Entry(
        bill_no=r[0], nc_pk=r[1], bill_status=r[2], approve_status=r[3],
        effective=(r[2] == 1 and r[3] == 1), trade_type=r[4], currency=r[5],
        bill_date=r[6], supplier_code=r[7], supplier_name=r[8], invoice_no_norm=r[9],
        invoice_no=r[10], amount=Decimal(r[11]), open=Decimal(r[12]), dismissed=bool(r[13]))


def _review(r) -> tuple[str, dict]:
    return r[0], {"kind": r[1], "bill_nos": list(r[2]), "reason": r[3], "note": r[4],
                  "reviewed_by_name": r[5],
                  "reviewed_at": r[6].isoformat() if r[6] else None}


def _since(today: date) -> date:
    return today - timedelta(days=PENDING_DAYS)


def findings_sync(cur, *, today: date | None = None) -> list[Finding]:
    """For the AP sync worker, over its own psycopg2 cursor."""
    today = today or date.today()
    cur.execute(_ENTRIES_SQL, {"since": _since(today)})
    found = detect([_entry(r) for r in cur.fetchall()], today=today)
    cur.execute(_REVIEWS_SQL)
    apply_reviews(found, dict(_review(r) for r in cur.fetchall()))
    return found


def _to_sa(sql: str) -> str:
    return sql.replace("%(since)s", ":since")


async def findings(db: AsyncSession, *, today: date | None = None) -> list[Finding]:
    today = today or date.today()
    rows = (await db.execute(text(_to_sa(_ENTRIES_SQL)), {"since": _since(today)})).all()
    found = detect([_entry(r) for r in rows], today=today)
    reviews = (await db.execute(text(_REVIEWS_SQL))).all()
    apply_reviews(found, dict(_review(r) for r in reviews))
    return found


async def _payments(db: AsyncSession, nc_pks: list[str]) -> dict[str, dict]:
    """Approved NC payments against each bill — the evidence for "paid twice".
    Keyed by the bill's NC pk (payment lines point at it via TOP_BILLID, not
    SRC_BILLID — see models/nc_ap.py)."""
    if not nc_pks:
        return {}
    rows = (await db.execute(text("""
        select pl.top_bill_id, coalesce(sum(pl.money_de), 0) as paid,
               string_agg(distinct p.bill_no, ', ') as payment_nos,
               max(p.bill_date)::date as last_paid
          from nc_ap_payment_lines pl
          join nc_ap_payments p on p.id = pl.payment_id
         where p.bill_status = 1 and p.approve_status = 1
           and pl.top_bill_id = any(:pks)
         group by pl.top_bill_id
    """), {"pks": nc_pks})).all()
    return {r[0]: {"paid": str(r[1]), "payment_nos": r[2],
                   "last_paid": r[3].isoformat() if r[3] else None} for r in rows}


# ── shaping ──────────────────────────────────────────────────────────────────

_APPROVE_LABELS = {-1: "Draft", 0: "Rejected", 1: "Approved", 2: "Approving", 3: "Submitted"}


def _entry_json(e: Entry, pays: dict) -> dict:
    p = pays.get(e.nc_pk) or {}
    return {
        "bill_no": e.bill_no, "effective": e.effective,
        "status_label": "Approved" if e.effective
        else _APPROVE_LABELS.get(e.approve_status, f"Status {e.approve_status}"),
        "trade_type": e.trade_type, "supplier_code": e.supplier_code,
        "supplier_name": e.supplier_name, "currency": e.currency,
        "invoice_no": e.invoice_no,
        "bill_date": e.bill_date.isoformat() if e.bill_date else None,
        "amount": str(e.amount), "open": str(e.open), "dismissed": e.dismissed,
        "paid_on_bill": p.get("paid"), "payment_nos": p.get("payment_nos"),
        "last_paid": p.get("last_paid"),
    }


def finding_json(f: Finding, pays: dict | None = None) -> dict:
    pays = pays or {}
    return {
        "key": f.key, "kind": f.kind, "kind_label": KIND_LABELS[f.kind], "vendor": f.vendor,
        "status": f.status, "invoice_no": f.invoice_no, "invoice_no_norm": f.invoice_no_norm,
        "currency": f.currency, "supplier_code": f.supplier_code,
        "supplier_name": f.supplier_name,
        "amount": str(f.amount) if f.amount is not None else None,
        "extra_copies": f.extra_copies, "extra_amount": str(f.extra_amount),
        "open_exposure": str(f.open_exposure), "bill_nos": f.bill_nos,
        "reviewed": f.review is not None, "review": f.review, "reopened": f.reopened,
        "bills": [_entry_json(e, pays) for e in f.entries],
    }


def summarise(found: list[Finding]) -> dict:
    """Per kind and currency, unreviewed vs reviewed. Money is never summed
    across currencies."""
    out: dict = {k: {"label": KIND_LABELS[k], "actionable": k in ACTIONABLE,
                     "unreviewed": 0, "reviewed": 0, "by_currency": {}} for k in KINDS}
    for f in found:
        k = out[f.kind]
        k["reviewed" if f.review else "unreviewed"] += 1
        if f.review:
            continue
        c = k["by_currency"].setdefault(f.currency or "(unknown)", {
            "findings": 0, "extra_amount": _ZERO, "open_exposure": _ZERO})
        c["findings"] += 1
        c["extra_amount"] += f.extra_amount
        c["open_exposure"] += f.open_exposure
    for k in out.values():
        for c in k["by_currency"].values():
            c["extra_amount"] = str(c["extra_amount"])
            c["open_exposure"] = str(c["open_exposure"])
    return out


async def report(db: AsyncSession, *, kind: str | None = None,
                 reviewed: bool | None = False, currency: str | None = None,
                 q: str | None = None, limit: int = 500) -> dict:
    """Summary over everything, rows filtered. The summary never follows the
    filter — a page whose totals shrink with the filter cannot say how much
    was left out."""
    found = await findings(db)
    rows = [f for f in found
            if (kind is None or f.kind == kind)
            and (reviewed is None or (f.review is not None) == reviewed)
            and (currency is None or f.currency == currency)]
    if q:
        needle = "".join(q.upper().split())
        rows = [f for f in rows
                if needle in f.invoice_no_norm
                or any(needle in (e.supplier_name or "").upper().replace(" ", "")
                       or needle == (e.supplier_code or "") or needle == e.bill_no
                       for e in f.entries)]
    shown = rows[:limit]
    pays = await _payments(db, sorted({e.nc_pk for f in shown for e in f.entries}))
    return {"summary": summarise(found), "total": len(rows),
            "findings": [finding_json(f, pays) for f in shown]}


# ── verdicts ─────────────────────────────────────────────────────────────────

async def review(db: AsyncSession, key: str, seen_bill_nos: list[str], reason: str,
                 note: str | None, user_id: uuid.UUID, user_name: str | None) -> dict:
    """Record a verdict on the finding as it stands NOW.

    `seen_bill_nos` is what the reviewer was shown. If the finding has moved
    since (a sync added a copy, a copy was voided) the verdict would cover a set
    nobody looked at, so it is refused and the current set is returned.
    """
    current = next((f for f in await findings(db) if f.key == key), None)
    if current is None:
        return {"applied": False, "reason": "not_found"}
    if sorted(set(seen_bill_nos)) != current.bill_nos:
        return {"applied": False, "reason": "changed", "bill_nos": current.bill_nos}
    if current.review is not None:
        return {"applied": False, "reason": "already_reviewed", "review": current.review}
    # A re-opened finding carries a stale verdict; retire it so the new one can
    # take the (partial) unique slot, and the old one stays on the record.
    await db.execute(text("""
        update nc_ap_invoice_dup_reviews
           set retired_at = now(), retired_by = :by, retired_by_name = :by_name
         where finding_key = :key and retired_at is null
    """), {"key": key, "by": user_id, "by_name": user_name})
    await db.execute(text("""
        insert into nc_ap_invoice_dup_reviews
            (finding_key, kind, invoice_no_norm, currency, supplier_code, bill_nos,
             reason, note, reviewed_by, reviewed_by_name, reviewed_at)
        values (:key, :kind, :norm, :ccy, :sc, :bills, :reason, :note, :by, :by_name, now())
    """), {"key": key, "kind": current.kind, "norm": current.invoice_no_norm,
           "ccy": current.currency, "sc": current.supplier_code, "bills": current.bill_nos,
           "reason": reason, "note": note, "by": user_id, "by_name": user_name})
    await db.commit()
    return {"applied": True, "key": key}


async def unreview(db: AsyncSession, key: str, user_id: uuid.UUID,
                   user_name: str | None) -> dict:
    res = await db.execute(text("""
        update nc_ap_invoice_dup_reviews
           set retired_at = now(), retired_by = :by, retired_by_name = :by_name
         where finding_key = :key and retired_at is null
    """), {"key": key, "by": user_id, "by_name": user_name})
    await db.commit()
    return {"retired": res.rowcount}
