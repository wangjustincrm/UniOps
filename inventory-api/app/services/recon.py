"""Four-way reconciliation as of D: WMS × NC stock (IC) × NC cost (IA) × NC GL,
with every difference attributed to a named cause where one can be computed
(design §7.3, §8).

Three comparisons, each between the two ledgers that share a dimension:

  IC(WMS warehouses) − WMS      quantity   (WMS only knows its own warehouses, D12)
  IA − IC(all warehouses)       quantity   (IA has no warehouse dimension)
  GL − IA                       quantity and amount

Attribution is deterministic: each rule computes the quantity/amount it
explains from the mirrors, it is subtracted, and what remains is `unexplained`
— highlighted when above tolerance. Rules never overlap (each looks at a
disjoint set of documents), so an explained amount is never counted twice.

Everything is computed for all materials at once (≈1.2k) and filtered or
paged in Python; at this size that is simpler and faster than pushing
pagination into five aggregate queries.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import asof

WMS_GO_LIVE = date(2025, 2, 3)
ZERO = Decimal(0)

# Category codes → (side, English label). The frontend shows the label; the
# xlsx export writes it as text (colours do not survive filtering/forwarding).
CATEGORIES: dict[str, tuple[str, str]] = {
    "nc_stopped": ("ic_wms", "NC stopped recording this material"),
    "wms_only_material": ("ic_wms", "Material not tracked by batch in NC / not in NC"),
    "not_costed_warehouse": ("ia_ic", "Stock in a warehouse NC does not cost (e.g. SC16)"),
    "shipped_not_invoiced": ("ia_ic", "Shipped (IC) but not yet invoiced (IA)"),
    "unsigned_documents": ("ia_ic", "Unsigned NC stock documents (not in IA)"),
    "back_dated": ("ia_ic", "IA rows booked in a different period than their date"),
    "prior_years": ("gl_ia", "Difference carried forward from previous years (GL opening vs IA)"),
    "gl_non_ia": ("gl_ia", "GL entries this year from AP / manual vouchers, not from IA or cost management"),
    "batch_label": ("ic_wms", "Same total, different batches (batch labels differ)"),
}
SIDES = ("ic_wms", "ia_ic", "gl_ia")
# GL voucher sources that ARE the normal costing flow into inventory accounts.
GL_COSTING_SOURCES = ("IA", "CM")


@dataclass
class MaterialRow:
    pk: str | None
    code: str
    name: str | None = None
    uom: str | None = None
    class_code: str | None = None
    in_nc: bool = True
    no_batch_since: date | None = None
    wms: Decimal | None = None          # None = WMS not live on D
    ic_wms: Decimal = ZERO
    ic_external: Decimal = ZERO
    ic_production: Decimal = ZERO
    ic_transient: Decimal = ZERO        # transient + other + unclassified
    ia_qty: Decimal = ZERO
    ia_amt: Decimal = ZERO
    gl_qty: Decimal = ZERO
    gl_amt: Decimal = ZERO
    # inputs for attribution
    not_costed_qty: Decimal = ZERO
    shipped_out: Decimal = ZERO         # IC 4C outbound, net
    invoiced_out: Decimal = ZERO        # IA I5 outbound, net
    unsigned_qty: Decimal = ZERO
    back_dated_qty: Decimal = ZERO
    gl_open_qty: Decimal = ZERO         # GL period 00 of year(D)
    gl_open_amt: Decimal = ZERO
    ia_prior_qty: Decimal = ZERO        # IA cumulative to the end of the previous year
    ia_prior_amt: Decimal = ZERO
    gl_non_ia_qty: Decimal = ZERO       # GL periods 01..P not sourced from IA
    gl_non_ia_amt: Decimal = ZERO
    gl_by_source: dict[str, Decimal] = field(default_factory=dict)
    nc_has_batches: bool = False
    wms_uom: str | None = None
    # outputs
    categories: list[dict] = field(default_factory=list)
    residual: dict[str, Decimal] = field(default_factory=dict)

    @property
    def ic_total(self) -> Decimal:
        return self.ic_wms + self.ic_external + self.ic_production + self.ic_transient


def attribute(row: MaterialRow, d: date, qty_tol: Decimal, amt_tol: Decimal) -> MaterialRow:
    """Pure: fill row.categories and row.residual. Signs follow the
    comparison direction (e.g. ia_ic = IA − IC): a category's qty is the part
    of that difference it accounts for."""
    cats: list[dict] = []

    def add(code: str, qty: Decimal = ZERO, amount: Decimal = ZERO) -> None:
        if abs(qty) > qty_tol or abs(amount) > amt_tol:
            cats.append({"code": code, "side": CATEGORIES[code][0], "qty": qty, "amount": amount})

    # ── IC(WMS warehouses) − WMS ──────────────────────────────────────────
    rem_ic_wms = None
    if row.wms is not None:
        diff = row.ic_wms - row.wms
        rem_ic_wms = diff
        if row.no_batch_since is not None and row.no_batch_since <= d:
            add("nc_stopped", diff)
            rem_ic_wms = ZERO
        elif not row.in_nc or (not row.nc_has_batches and row.ic_wms == ZERO):
            add("wms_only_material", diff)
            rem_ic_wms = ZERO

    # ── IA − IC(all) ──────────────────────────────────────────────────────
    # These rules work from aggregates that cannot be tied line by line
    # (purchases reach IA through settlements, sales through invoices), so a
    # rule may explain AT MOST what is still unexplained, and only in the same
    # direction. Without the cap a cumulative figure (e.g. shipped − invoiced
    # since go-live) "explains" a difference that is not there and turns a
    # matching material into an unexplained one.
    rem_ia_ic = row.ia_qty - row.ic_total
    for code, candidate in (
        ("not_costed_warehouse", -row.not_costed_qty),               # in IC, never in IA
        ("unsigned_documents", -row.unsigned_qty),                   # in IC, not (yet) in IA
        ("shipped_not_invoiced", row.shipped_out - row.invoiced_out),  # IC down, IA not yet
        ("back_dated", row.back_dated_qty),                          # in IA by period, not by date
    ):
        explained = _capped(candidate, rem_ia_ic)
        if abs(explained) > qty_tol:
            add(code, explained)
            rem_ia_ic -= explained

    # ── GL − IA ───────────────────────────────────────────────────────────
    # Two candidate causes:
    #   prior_years — GL's opening (period 00) vs IA cumulated to the previous
    #                 year end: a difference brought forward, not made this year.
    #   gl_non_ia   — this year's GL entries on inventory accounts from AP or
    #                 manual vouchers. NOT cost management (CM): CM posting
    #                 production cost into inventory is NC's normal flow and
    #                 is offset by IA, so counting it would "explain" millions
    #                 on materials whose GL and IA agree exactly.
    # Capped like the IA−IC rules, per measure.
    rem_gl_qty = row.gl_qty - row.ia_qty
    rem_gl_amt = row.gl_amt - row.ia_amt
    for code, q, m in (
        ("prior_years", row.gl_open_qty - row.ia_prior_qty, row.gl_open_amt - row.ia_prior_amt),
        ("gl_non_ia", row.gl_non_ia_qty, row.gl_non_ia_amt),
    ):
        q, m = _capped(q, rem_gl_qty), _capped(m, rem_gl_amt)
        if abs(q) > qty_tol or abs(m) > amt_tol:
            add(code, q, m)
            rem_gl_qty -= q
            rem_gl_amt -= m

    row.categories = cats
    row.residual = {"ic_wms": rem_ic_wms, "ia_ic": rem_ia_ic,
                    "gl_ia_qty": rem_gl_qty, "gl_ia_amount": rem_gl_amt}
    return row


# NC unit code → the WMS base-unit word it corresponds to. Only clear
# equivalences are listed; anything else is "unknown", not "mismatch".
_NC_TO_WMS_UOM = {"KGM": "KG", "KG": "KG", "EA": "PIECES", "PIECES": "PIECES", "PCS": "PIECES"}
_WMS_UOM = {"KG": "KG", "PIECES": "PIECES", "EA": "PIECES"}


def unit_check(nc_uom: str | None, wms_uom: str | None) -> str:
    """'match' | 'mismatch' | 'unknown' — INFORMATION ONLY, never an
    attribution. WMS's unit is the EA level of its packaging ladder, and that
    ladder defaults to KG: 2,365 of 2,498 SKUs say KG (2026-10-14), including
    packaging counted in pieces whose quantities agree with NC exactly (CP0016:
    22,000 = 22,000). Treating 'mismatch' as "not comparable" would have
    silenced 756 materials, real differences among them."""
    a = _NC_TO_WMS_UOM.get((nc_uom or "").upper())
    b = _WMS_UOM.get((wms_uom or "").upper())
    if a is None or b is None:
        return "unknown"
    return "match" if a == b else "mismatch"


def _capped(candidate: Decimal, remaining: Decimal) -> Decimal:
    """The part of `candidate` that explains `remaining`: same sign only,
    never more than what is left."""
    if candidate == ZERO or remaining == ZERO or (candidate > 0) != (remaining > 0):
        return ZERO
    return candidate if abs(candidate) <= abs(remaining) else remaining


def status_of(row: MaterialRow, qty_tol: Decimal, amt_tol: Decimal) -> str:
    """'unexplained' if any residual exceeds tolerance; else 'explained' if a
    category fired; else 'ok'."""
    r = row.residual
    if ((r["ic_wms"] is not None and abs(r["ic_wms"]) > qty_tol) or abs(r["ia_ic"]) > qty_tol
            or abs(r["gl_ia_qty"]) > qty_tol or abs(r["gl_ia_amount"]) > amt_tol):
        return "unexplained"
    return "explained" if row.categories else "ok"


# ── Data loading ────────────────────────────────────────────────────────────

async def load(db: AsyncSession, a: asof.AsOf, include_unposted: bool) -> dict[str, MaterialRow]:
    """Every material that has a non-zero figure anywhere on D, keyed by code."""
    p = {**a.sql_params, "include_unposted": include_unposted,
         "wms_cutoff": datetime.combine(a.d, time(23, 59, 59))}
    rows: dict[str, MaterialRow] = {}

    mats = {r.pk_material: r for r in (await db.execute(text(
        "select pk_material, code, name, uom_code, class_code from inv_nc_material"))).all()}
    by_code = {m.code: m for m in mats.values()}

    def get(pk: str | None, code: str | None = None) -> MaterialRow | None:
        if pk is not None:
            m = mats.get(pk)
            if m is None:
                code = code or pk
            else:
                code = m.code
        if code is None:
            return None
        if code not in rows:
            m = by_code.get(code)
            rows[code] = MaterialRow(pk=m.pk_material if m else None, code=code,
                                     name=m.name if m else None, uom=m.uom_code if m else None,
                                     class_code=m.class_code if m else None, in_nc=m is not None)
        return rows[code]

    # IC by warehouse class, plus the not-costed and unsigned inputs.
    for r in (await db.execute(text(f"""
        select f.cmaterialoid mat, coalesce(c.warehouse_class, 'unclassified') cls,
               bool_or(not w.iscalculatedinvcost) not_costed_any,
               sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0)) q,
               sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0))
                   filter (where not w.iscalculatedinvcost) q_not_costed,
               sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0))
                   filter (where f.fbillflag = 2 and f.cbilltypecode <> '4460'
                           and w.iscalculatedinvcost
                           and not exists (select 1 from inv_nc_ia_ledger x
                                           where x.csrcbid = f.cgeneralbid)) q_unsigned,
               sum(coalesce(f.noutnum,0) - coalesce(f.ninnum,0))
                   filter (where f.cbilltypecode = '4C') q_shipped
        from inv_nc_ic_flow f
        left join inv_nc_warehouse w on w.pk_stordoc = f.cwarehouseid
        left join inv_warehouse_class c on c.nc_code = w.code
        where {asof.IC_FLOW_ASOF}
        group by 1, 2"""), p)).all():
        row = get(r.mat)
        if row is None:
            continue
        q = r.q or ZERO
        if r.cls == "wms":
            row.ic_wms += q
        elif r.cls == "external":
            row.ic_external += q
        elif r.cls == "production":
            row.ic_production += q
        else:
            row.ic_transient += q
        row.not_costed_qty += r.q_not_costed or ZERO
        row.unsigned_qty += r.q_unsigned or ZERO
        row.shipped_out += r.q_shipped or ZERO

    # IA qty/amount, invoiced outbound (I5), and back-dated rows.
    for r in (await db.execute(text(f"""
        select l.cinventoryid mat,
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0))
                   filter (where {asof.IA_LEDGER_ASOF}) q,
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nmny,0))
                   filter (where {asof.IA_LEDGER_ASOF}) amt,
               sum(case when l.fdispatchflag = 1 then 1 else -1 end * coalesce(l.nnum,0))
                   filter (where {asof.IA_LEDGER_ASOF} and l.cbilltypecode = 'I5') invoiced,
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0))
                   filter (where l.caccountperiod < :year || '-01') prior_q,
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nmny,0))
                   filter (where l.caccountperiod < :year || '-01') prior_amt,
               -- In IA by period but dated after D  (+)  minus  dated ≤ D but in a later period (−)
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0))
                   filter (where {asof.IA_LEDGER_ASOF} and l.biz_time is not null
                           and not {asof.nc_time_le('l.biz_time')}) q_ahead,
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0))
                   filter (where not {asof.IA_LEDGER_ASOF} and {asof.nc_time_le('l.biz_time')}) q_behind
        from inv_nc_ia_ledger l
        group by 1"""), p)).all():
        row = get(r.mat)
        if row is None:
            continue
        row.ia_qty += r.q or ZERO
        row.ia_amt += r.amt or ZERO
        row.invoiced_out += r.invoiced or ZERO
        row.ia_prior_qty += r.prior_q or ZERO
        row.ia_prior_amt += r.prior_amt or ZERO
        row.back_dated_qty += (r.q_ahead or ZERO) - (r.q_behind or ZERO)

    # GL per material (1471 and material-less lines are reported elsewhere).
    for r in (await db.execute(text(f"""
        select g.material_pk mat, coalesce(g.pk_system, '?') src, g.period = '00' opening,
               sum(coalesce(g.debit_qty,0) - coalesce(g.credit_qty,0)) q,
               sum(coalesce(g.debit_amount,0) - coalesce(g.credit_amount,0)) amt
        from inv_nc_gl_line g
        where {asof.GL_LINE_ASOF}
          and (cast(:include_unposted as boolean) or g.tallydate is not null)
          and g.material_pk is not null and g.account_code not like '1471%'
        group by 1, 2, 3"""), p)).all():
        row = get(r.mat)
        if row is None:
            continue
        row.gl_qty += r.q or ZERO
        row.gl_amt += r.amt or ZERO
        if r.opening:
            row.gl_open_qty += r.q or ZERO
            row.gl_open_amt += r.amt or ZERO
        elif r.src not in GL_COSTING_SOURCES:
            row.gl_non_ia_qty += r.q or ZERO
            row.gl_non_ia_amt += r.amt or ZERO
            row.gl_by_source[r.src] = row.gl_by_source.get(r.src, ZERO) + (r.amt or ZERO)

    # WMS on D (from the transaction history), keyed by SKU = NC code.
    if a.d >= WMS_GO_LIVE:
        for r in (await db.execute(text("""
            select sku, sum(qty) q from inv_wms_move
            where txn_time <= :wms_cutoff group by 1"""), p)).all():
            row = get(None, r.sku)
            if row is not None:
                row.wms = (row.wms or ZERO) + (r.q or ZERO)
        for row in rows.values():
            if row.wms is None:
                row.wms = ZERO

    # Configuration and batch presence.
    for r in (await db.execute(text("select material_code, since_date from inv_no_batch_material"))).all():
        if r.material_code in rows:
            rows[r.material_code].no_batch_since = r.since_date
    for r in (await db.execute(text("select sku, uom from inv_wms_sku"))).all():
        if r.sku in rows:
            rows[r.sku].wms_uom = r.uom
    with_batches = set((await db.execute(text(
        "select distinct cmaterialoid from inv_nc_batch"))).scalars().all())
    for row in rows.values():
        row.nc_has_batches = row.pk in with_batches if row.pk else False
    return rows


async def unattributable(db: AsyncSession, a: asof.AsOf, include_unposted: bool) -> dict:
    """GL figures that cannot sit on a material row: impairment (1471) and
    lines without a material auxiliary — shown below the table, never dropped."""
    p = {**a.sql_params, "include_unposted": include_unposted}
    rows = (await db.execute(text(f"""
        select g.account_code, max(g.account_name) name, g.material_pk is null no_mat,
               sum(coalesce(g.debit_qty,0) - coalesce(g.credit_qty,0)) q,
               sum(coalesce(g.debit_amount,0) - coalesce(g.credit_amount,0)) amt
        from inv_nc_gl_line g
        where {asof.GL_LINE_ASOF} and (cast(:include_unposted as boolean) or g.tallydate is not null)
          and (g.account_code like '1471%' or g.material_pk is null)
        group by 1, 3 having sum(coalesce(g.debit_amount,0) - coalesce(g.credit_amount,0)) <> 0
            or sum(coalesce(g.debit_qty,0) - coalesce(g.credit_qty,0)) <> 0
        order by 1"""), p)).all()
    return {
        "impairment": [r for r in rows if r.account_code.startswith("1471")],
        "no_material": [r for r in rows if not r.account_code.startswith("1471")],
    }


# ── Batch groups ────────────────────────────────────────────────────────────

def norm_batch(v: str | None) -> str | None:
    if v is None:
        return None
    s = "".join(v.split()).upper()
    return s or None


def group_batches(wms_lots: list[dict], nc_batches: list[dict]) -> list[dict]:
    """Pure. Merge WMS lots and NC batches of ONE material into batch groups.

    A WMS lot carries {qc_batch, supplier_batch}; an NC batch carries
    {batch (QC), supplier_batch}. After normalising (no whitespace, upper
    case), any shared value links them — user decision D10/D16: "if either of
    the two batch numbers matches, it is the same batch". Linking is
    transitive (union-find), so one WMS lot matching two NC batches, or two
    WMS lots one NC batch, becomes ONE group and nothing is counted twice.

    wms_lots:   [{"lotnum", "qc_batch", "supplier_batch", "qty"}]
    nc_batches: [{"batch", "supplier_batch", "ic_wms", "ic_other", "ia"}]
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    nodes: list[tuple[str, dict, str]] = []
    for i, w in enumerate(wms_lots):
        n = f"W{i}"
        find(n)
        nodes.append((n, w, "wms"))
        for k in {norm_batch(w.get("qc_batch")), norm_batch(w.get("supplier_batch"))} - {None}:
            union(n, "K:" + k)
    for i, b in enumerate(nc_batches):
        n = f"N{i}"
        find(n)
        nodes.append((n, b, "nc"))
        for k in {norm_batch(b.get("batch")), norm_batch(b.get("supplier_batch"))} - {None}:
            union(n, "K:" + k)

    groups: dict[str, dict] = defaultdict(lambda: {
        "nc_batches": [], "wms_lots": [], "supplier_batches": set(),
        "wms": ZERO, "ic_wms": ZERO, "ic_other": ZERO, "ia": ZERO})
    for n, item, side in nodes:
        g = groups[find(n)]
        if side == "wms":
            g["wms_lots"].append(item["lotnum"])
            g["wms"] += item.get("qty") or ZERO
            if item.get("supplier_batch"):
                g["supplier_batches"].add(item["supplier_batch"])
            if item.get("qc_batch") and item["qc_batch"] not in g["nc_batches"]:
                g.setdefault("wms_qc_batches", set()).add(item["qc_batch"])
        else:
            g["has_nc"] = True
            if item.get("batch"):
                g["nc_batches"].append(item["batch"])
            if item.get("supplier_batch"):
                g["supplier_batches"].add(item["supplier_batch"])
            g["ic_wms"] += item.get("ic_wms") or ZERO
            g["ic_other"] += item.get("ic_other") or ZERO
            g["ia"] += item.get("ia") or ZERO

    out = []
    for g in groups.values():
        has_w, has_n = bool(g["wms_lots"]), bool(g.get("has_nc"))
        label = (sorted(g["nc_batches"]) or sorted(g.get("wms_qc_batches", set()))
                 or sorted(g["supplier_batches"]) or ["(no batch)"])
        out.append({
            "label": " / ".join(label),
            "nc_batches": sorted(g["nc_batches"]),
            "wms_lots": sorted(g["wms_lots"]),
            "supplier_batches": sorted(g["supplier_batches"]),
            "wms": g["wms"], "ic_wms": g["ic_wms"], "ic_other": g["ic_other"], "ia": g["ia"],
            "match": "both" if has_w and has_n else ("wms_only" if has_w else "nc_only"),
        })
    out.sort(key=lambda g: (g["match"] != "both", g["label"]))
    return out


async def load_batches(db: AsyncSession, a: asof.AsOf, codes: list[str]) -> dict[str, list[dict]]:
    """Batch groups for the given material codes on D."""
    if not codes:
        return {}
    p = {**a.sql_params, "codes": codes, "wms_cutoff": datetime.combine(a.d, time(23, 59, 59))}
    wms: dict[str, list[dict]] = defaultdict(list)
    if a.d >= WMS_GO_LIVE:
        for r in (await db.execute(text("""
            select m.sku, m.lotnum, l.qc_batch, l.supplier_batch, sum(m.qty) q
            from inv_wms_move m left join inv_wms_lot l on l.sku = m.sku and l.lotnum = m.lotnum
            where m.sku = any(:codes) and m.txn_time <= :wms_cutoff
            group by 1,2,3,4 having sum(m.qty) <> 0"""), p)).all():
            wms[r.sku].append({"lotnum": r.lotnum, "qc_batch": r.qc_batch,
                               "supplier_batch": r.supplier_batch, "qty": r.q})

    nc: dict[str, dict[str, dict]] = defaultdict(dict)

    def nc_row(code: str, batch: str | None) -> dict:
        key = batch or ""
        return nc[code].setdefault(key, {"batch": batch, "supplier_batch": None,
                                         "ic_wms": ZERO, "ic_other": ZERO, "ia": ZERO})

    for r in (await db.execute(text(f"""
        select mt.code, f.vbatchcode batch, max(b.vvendbatchcode) supplier_batch,
               sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0))
                   filter (where c.warehouse_class = 'wms') q_wms,
               sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0))
                   filter (where c.warehouse_class is distinct from 'wms') q_other
        from inv_nc_ic_flow f
        join inv_nc_material mt on mt.pk_material = f.cmaterialoid
        left join inv_nc_warehouse w on w.pk_stordoc = f.cwarehouseid
        left join inv_warehouse_class c on c.nc_code = w.code
        left join inv_nc_batch b on b.pk_batchcode = f.pk_batchcode
        where mt.code = any(:codes) and {asof.IC_FLOW_ASOF}
        group by 1, 2
        having sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0)) <> 0"""), p)).all():
        row = nc_row(r.code, r.batch)
        row["supplier_batch"] = r.supplier_batch
        row["ic_wms"] += r.q_wms or ZERO
        row["ic_other"] += r.q_other or ZERO

    for r in (await db.execute(text(f"""
        select mt.code, l.vbatchcode batch,
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0)) q
        from inv_nc_ia_ledger l join inv_nc_material mt on mt.pk_material = l.cinventoryid
        where mt.code = any(:codes) and {asof.IA_LEDGER_ASOF}
        group by 1, 2 having sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0)) <> 0"""),
            p)).all():
        nc_row(r.code, r.batch)["ia"] += r.q or ZERO

    return {code: group_batches(wms.get(code, []), list(nc.get(code, {}).values())) for code in codes}
