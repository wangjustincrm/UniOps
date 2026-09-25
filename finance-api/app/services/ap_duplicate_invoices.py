"""The same invoice on more than one NC payable — NC does not check, so we do.

NC65 accepts any invoice number on a payable, however many payables already
carry it, and it has been paid twice because of it. NC is the book of record
and cannot be changed from here, so this is DETECTION on the mirror: it runs
after every AP sync (hourly, see app/tasks/nc_ap_sync_scheduler.py) and raises
one standing task for AP (ap_duplicate_invoice_tasks.py). The earlier it fires
the cheaper it is: a draft can simply not be approved, an approved copy can be
held back from the next payment run, and only a copy that was already paid has
to be recovered from the supplier.

What production said on 2026-09-25, which is what shaped the rules below:

  * 213 (supplier, invoice number) pairs sit on 2+ approved payables. Most of
    them are NOT duplicates, and a check that fires on all of them would be
    ignored within a week:
      - a bill and its reversal: KLN 0006814591-01 +2,746.71 twice and
        -2,746.71 once is ONE live copy, not three;
      - the supply chain settles one invoice into two bills on the same day
        (goods and freight — Gertex, Doverco, Agropur);
      - the milk bills carry month labels ("Aug", "Jul") in the invoice field.
  * Once an equal credit is netted off, 36 pairs still have the same amount
    on two or more live copies — 145,053.61 of duplicate billing. The biggest,
    Jane Media 1497 (50,722.88 CAD) and FrieslandCampina 9007737328 (45,800.00
    USD), were paid twice. Several still had a copy open.
  * The same number and amount under TWO supplier codes is the vendor-master
    version of the same mistake (Nielsen 9301122753, 3,860.84, under
    "ACNieisen Company" and "Nielsen Consumer LLC", August and September 2026).
  * Drafts collide too — 507848 was on four unapproved payables at once.

Hence four kinds of finding, in descending order of how sure we are:

  exact           same supplier, currency, invoice number and amount, on 2+
                  live copies after equal credits are netted off
  cross_supplier  same invoice number and amount under 2+ supplier codes
  pending         an UNAPPROVED payable (last PENDING_DAYS days) whose supplier
                  and invoice number are already on another payable — the one
                  moment it can be stopped before it is even owed
  amount_differs  same supplier and invoice number, live copies of different
                  amounts. Mostly legitimate splits; shown for review, never
                  counted in the task

Matching is on `invoice_no_norm` (upper-cased, whitespace removed — see
nc_ap_sync.normalise_invoice_no). An entry is one (bill, invoice number):
a bill can carry several invoices (1,873 of them do), and the amount that
matters is the invoice's share of it, not the bill total.

Findings are recomputed on every read and never stored. The only thing stored
is finance's verdict on one (0039_ap_inv_dup_reviews), and a verdict covers a
finding only while no bill has been added to it since.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

EXACT = "exact"
CROSS_SUPPLIER = "cross_supplier"
PENDING = "pending"
AMOUNT_DIFFERS = "amount_differs"

KINDS = (EXACT, CROSS_SUPPLIER, PENDING, AMOUNT_DIFFERS)

KIND_LABELS = {
    EXACT: "Same supplier, same invoice number, same amount",
    CROSS_SUPPLIER: "Same invoice number and amount under different supplier codes",
    PENDING: "Unapproved payable repeats an invoice number already entered",
    AMOUNT_DIFFERS: "Same supplier and invoice number, different amounts",
}

#: The kinds that are a duplicate until someone says otherwise. AMOUNT_DIFFERS
#: is left out: on production it is overwhelmingly one invoice split across
#: bills on purpose, and a task that is mostly noise stops being read.
ACTIONABLE = (EXACT, CROSS_SUPPLIER, PENDING)

#: How far back an unapproved payable still counts as work in progress. Older
#: drafts are abandoned documents; AP Subledger Health's "Never approved" view
#: already lists those and marks the ones an approved bill superseded.
PENDING_DAYS = 90

_ZERO = Decimal("0")


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


@dataclass
class Finding:
    kind: str
    invoice_no_norm: str
    invoice_no: str | None
    currency: str | None
    supplier_code: str | None
    supplier_name: str | None
    amount: Decimal | None
    entries: list[Entry]
    #: Copies beyond the first. For EXACT/CROSS_SUPPLIER, the number of times
    #: this invoice was billed once too often.
    extra_copies: int = 0
    #: What was billed on those extra copies.
    extra_amount: Decimal = _ZERO
    #: The part of extra_amount NC still shows as payable — what can still be
    #: stopped rather than recovered.
    open_exposure: Decimal = _ZERO
    review: dict | None = None
    #: A verdict existed but a bill was added after it.
    reopened: bool = False
    key: str = field(init=False)

    def __post_init__(self):
        amt = f"{self.amount:.2f}" if self.amount is not None else "*"
        self.key = ":".join((self.kind, self.currency or "-", self.supplier_code or "*",
                             self.invoice_no_norm, amt))

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
    """Positive amount -> copies still standing once equal credits are netted.

    A bill and its full reversal leave nothing; two bills and one reversal
    leave one. Zero-amount entries are placeholders and count for nothing.
    """
    pos = Counter(e.amount for e in entries if e.amount > 0)
    neg = Counter(-e.amount for e in entries if e.amount < 0)
    return Counter({a: n - neg[a] for a, n in pos.items() if n - neg[a] > 0})


def _exposure(entries: list[Entry], extra_amount: Decimal) -> Decimal:
    """How much of the duplicate billing is still unpaid in NC. Capped at the
    duplicate amount: the first copy's balance is legitimately owed."""
    open_total = sum((e.open for e in entries), _ZERO)
    return max(_ZERO, min(open_total, extra_amount))


def _name(entries: list[Entry]) -> str | None:
    return next((e.supplier_name for e in entries if e.supplier_name), None)


def _raw(entries: list[Entry]) -> str | None:
    return next((e.invoice_no for e in entries if e.invoice_no), None)


def _order(entries):
    return sorted(entries, key=lambda e: (e.bill_date or date.min, e.bill_no))


def detect(entries: list[Entry], *, today: date | None = None,
           pending_days: int = PENDING_DAYS) -> list[Finding]:
    """Every finding in `entries`. Pure: no I/O, no clock unless `today` is omitted."""
    today = today or date.today()
    pending_since = today - timedelta(days=pending_days)

    by_invoice: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        if e.invoice_no_norm:
            by_invoice[e.invoice_no_norm].append(e)

    findings: list[Finding] = []
    for norm, group in by_invoice.items():
        live = [e for e in group if e.effective]

        # Same supplier: EXACT, else AMOUNT_DIFFERS.
        by_supplier: dict[tuple, list[Entry]] = defaultdict(list)
        for e in live:
            by_supplier[(e.supplier_code, e.currency)].append(e)
        for (sc, ccy), bucket in by_supplier.items():
            counts = _live_counts(bucket)
            exact_amounts = [a for a, n in counts.items() if n >= 2]
            for a in exact_amounts:
                involved = [e for e in bucket if e.amount in (a, -a)]
                extra = counts[a] - 1
                findings.append(Finding(
                    kind=EXACT, invoice_no_norm=norm, invoice_no=_raw(involved),
                    currency=ccy, supplier_code=sc, supplier_name=_name(involved),
                    amount=a, entries=_order(involved), extra_copies=extra,
                    extra_amount=a * extra,
                    open_exposure=_exposure(involved, a * extra)))
            if not exact_amounts and sum(counts.values()) >= 2:
                findings.append(Finding(
                    kind=AMOUNT_DIFFERS, invoice_no_norm=norm, invoice_no=_raw(bucket),
                    currency=ccy, supplier_code=sc, supplier_name=_name(bucket),
                    amount=None, entries=_order(bucket),
                    extra_copies=sum(counts.values()) - 1))

        # Different suppliers, same number and amount.
        by_ccy: dict[str | None, list[Entry]] = defaultdict(list)
        for e in live:
            by_ccy[e.currency].append(e)
        for ccy, bucket in by_ccy.items():
            per_supplier = {sc: _live_counts([e for e in bucket if e.supplier_code == sc])
                            for sc in {e.supplier_code for e in bucket}}
            if len(per_supplier) < 2:
                continue
            amounts = {a for c in per_supplier.values() for a in c}
            for a in amounts:
                holders = [sc for sc, c in per_supplier.items() if c.get(a)]
                if len(holders) < 2:
                    continue
                involved = [e for e in bucket
                            if e.supplier_code in holders and e.amount in (a, -a)]
                copies = sum(per_supplier[sc][a] for sc in holders)
                extra = copies - 1
                findings.append(Finding(
                    kind=CROSS_SUPPLIER, invoice_no_norm=norm, invoice_no=_raw(involved),
                    currency=ccy, supplier_code=None, supplier_name=None, amount=a,
                    entries=_order(involved), extra_copies=extra, extra_amount=a * extra,
                    open_exposure=_exposure(involved, a * extra)))

        # Unapproved payables repeating a number the same supplier already has.
        # Only a positive draft can be a second copy: a draft credit note that
        # quotes the invoice it reverses is the correct way to undo one. And an
        # approved bill that was fully reversed no longer counts as "already
        # entered" — re-entering it corrected is the other half of that workflow.
        drafts = [e for e in group
                  if not e.effective and not e.dismissed and e.amount > 0
                  and e.bill_date is not None and e.bill_date >= pending_since]
        by_draft_supplier: dict[str | None, list[Entry]] = defaultdict(list)
        for d in drafts:
            by_draft_supplier[d.supplier_code].append(d)
        for sc, ds in by_draft_supplier.items():
            same = [e for e in group if e.supplier_code == sc]
            others_live = [e for e in same if e.effective]
            standing = sum(_live_counts(others_live).values())
            if not standing and len({d.bill_no for d in ds}) < 2:
                continue
            involved = [e for e in same if e.effective or e in ds]
            findings.append(Finding(
                kind=PENDING, invoice_no_norm=norm, invoice_no=_raw(involved),
                currency=ds[0].currency, supplier_code=sc, supplier_name=_name(involved),
                amount=None, entries=_order(involved),
                extra_copies=len({d.bill_no for d in ds})))

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
        "key": f.key, "kind": f.kind, "kind_label": KIND_LABELS[f.kind],
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
