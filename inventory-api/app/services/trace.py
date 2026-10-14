"""Batch / material trace: NC and WMS movements side by side (design §7.4, D13).

Scope (D12): NC movements in WMS-managed warehouses only, against every WMS
movement of the SKU. Window: WMS go-live (2025-02-03) to the end of D;
NC's balance before the window is the opening, WMS starts from zero (its own
opening came in as manual "QC" receipts, shown as such — D15).

Three pairing tiers (verified rates in design §4.8):
  1. document number — NC carries WMS numbers (vheaduserdef13) and WMS
     carries NC numbers (ASN/SO references): production orders, ZK return
     numbers, PO numbers, DN numbers, CASO/CAADJ …
  2. same category, same quantity, within a time window
  3. none — shown on one side only, highlighted
A tier-1 key can join many lines on each side (one production order, many
call-offs), so pairs are GROUPS compared by their sums.

The first divergence is the point after which the two running balances never
agree again — "the first movement that does not match".
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import asof, params
from app.services.recon import WMS_GO_LIVE, group_batches, norm_batch

ZERO = Decimal(0)
LOCAL = ZoneInfo(params.LOCAL_TIME_ZONE)

CATEGORY_LABELS = {
    "opening": "Opening",
    "manual_opening": "Manual opening import (WMS)",
    "purchase_in": "Purchase receipt",
    "purchase_return": "Purchase return",
    "production_issue": "Issued to production",
    "production_return": "Returned from production",
    "fg_receipt": "Finished goods received",
    "sales": "Sales shipment",
    "ship_out": "Shipped to external / consignment",
    "other_issue": "Department use / sample / scrap / damage",
    "count_adjust": "Stock count / adjustment",
    "batch_change": "Batch or material change",
    "other": "Other",
    "internal": "Internal move (no effect on comparison)",
    "state_change": "Stock state change (no quantity)",
}
HIDDEN_BY_DEFAULT = {"internal", "state_change"}

# NC transaction type → category. Keys are vtrantypecode with the factory code
# ('Cxx') kept literally — the codes are stored that way in NC.
_NC_TRANTYPE = {
    "45-Cxx-CRM02": "purchase_in", "45-Cxx-CRM01": "purchase_return",
    "4I-Cxx-005": "production_issue", "4D-Cxx-001": "production_issue",
    "4A-Cxx-002": "production_return", "4I-Cxx-006": "production_return",
    "4A-Cxx-093": "fg_receipt", "4I-Cxx-093": "fg_receipt",
    "4C-01": "sales",
    "4I-Cxx-103": "ship_out", "4I-Cxx-101": "ship_out", "4I-Cxx-CRM01": "ship_out",
    "4I-Cxx-CRM03": "other_issue", "4I-Cxx-009": "other_issue", "4I-Cxx-22": "other_issue",
    "4I-Cxx-CRM06": "other_issue",
    "4A-03": "count_adjust", "4I-03": "count_adjust", "4I-Cxx-CRM004": "count_adjust",
    "4A-Cxx-055": "count_adjust", "4I-Cxx-055": "count_adjust",
    "4A-Cxx-010": "batch_change", "4I-Cxx-015": "batch_change",
    "4A-Cxx-099": "other", "4I-Cxx-099": "other", "4A-Cxx-011": "other",
    "4460": "state_change",
}
# WMS document type → category, per transaction kind.
_WMS_ASN = {"PR": "purchase_in", "RM": "production_return", "FP": "fg_receipt",
            "ZK_CA": "fg_receipt", "QC": "manual_opening", "QTRK": "other"}
_WMS_SO = {"DM": "production_issue", "DD": "production_issue", "DM_WH": "production_issue",
           "OT": "other_issue", "SA": "other_issue", "BF": "other_issue", "QTCK": "ship_out",
           "XS": "sales", "RT": "purchase_return", "QCTZCK": "count_adjust", "PDXJ": "count_adjust"}

# Tier-2 time windows (days). Finished goods reach NC up to ~4 weeks late
# (measured −27 … +7 days, design §4.8).
_WINDOW = defaultdict(lambda: 7, {"fg_receipt": 30, "count_adjust": 120, "manual_opening": 120})


@dataclass
class Line:
    side: str                       # 'nc' | 'wms'
    id: str
    time: datetime | None           # Toronto, naive
    date_only: bool
    category: str
    qty: Decimal
    doc: str | None                 # the document number people say
    keys: set[str] = field(default_factory=set)
    detail: dict = field(default_factory=dict)
    pair: int | None = None
    tier: int | None = None


def seq_key(ln: "Line") -> tuple:
    """Order of movements on one side: time, then DOCUMENT NUMBER, then line.

    Several movements often share one timestamp (a stock-count batch posted at
    23:01). NC and WMS number documents sequentially as they are created, so
    the number is the order things happened in; the internal id is not —
    sorting by it showed CF0086 going 0 → 25 → 50 → 0 instead of
    625 → 0 → 25 → 50 (user, 2026-10-14)."""
    return (ln.time or datetime.min, ln.doc or "", ln.id)


def nc_category(trantype: str | None, other_wh_is_wms: bool) -> str:
    if other_wh_is_wms and trantype in ("4A-Cxx-099", "4I-Cxx-099", "4A-Cxx-055", "4I-Cxx-055"):
        return "internal"           # SC11 ↔ SC16: one physical warehouse to WMS
    return _NC_TRANTYPE.get(trantype or "", "other")


def wms_category(txn_type: str, doc_type: str | None, same_sku_tr: bool) -> str:
    if txn_type == "IN":
        return _WMS_ASN.get(doc_type or "", "other")
    if txn_type == "SO":
        return _WMS_SO.get(doc_type or "", "other")
    if txn_type == "AD":
        return "count_adjust"
    if txn_type == "TR":
        return "internal" if same_sku_tr else "batch_change"
    return "other"


_KEY_OK = re.compile(r"[A-Z0-9]")


def keys_of(*values: str | None) -> set[str]:
    """Document numbers usable for pairing: normalised, at least 6 characters
    (short values like 'SC11' or '1' would join unrelated documents)."""
    out = set()
    for v in values:
        if not v:
            continue
        k = "".join(v.split()).upper()
        if len(k) >= 6 and _KEY_OK.search(k):
            out.add(k)
    return out


def nc_local_time(t: datetime | None, nc_zone: str) -> tuple[datetime | None, bool]:
    """NC wall clock → Toronto. Date-only (00:00:00) stays a date (see asof)."""
    if t is None:
        return None, False
    if t.time() == time(0, 0):
        return t, True
    return t.replace(tzinfo=params.zone(nc_zone)).astimezone(LOCAL).replace(tzinfo=None), False


# ── Pairing (pure) ──────────────────────────────────────────────────────────

def pair_lines(nc: list[Line], wms: list[Line], qty_tol: Decimal) -> list[dict]:
    """Assign pair groups. Returns the groups:
    {"id", "tier", "key", "nc": [Line], "wms": [Line]}. Lines left with
    pair=None are unmatched."""
    groups: list[dict] = []
    visible = lambda ln: ln.category not in HIDDEN_BY_DEFAULT  # noqa: E731

    # Tier 1: shared document key. Each line joins the group of its first key
    # (sorted) that the other side also has.
    wms_by_key: dict[str, list[Line]] = defaultdict(list)
    for w in wms:
        if visible(w):
            for k in w.keys:
                wms_by_key[k].append(w)
    by_key: dict[str, dict] = {}
    for n in nc:
        if not visible(n):
            continue
        for k in sorted(n.keys):
            if wms_by_key.get(k):
                g = by_key.setdefault(k, {"tier": 1, "key": k, "nc": [], "wms": []})
                g["nc"].append(n)
                break
    for k, g in by_key.items():
        for w in wms_by_key[k]:
            if w.pair is None and w not in g["wms"]:
                g["wms"].append(w)
                w.pair = -1  # provisional, numbered below
    for g in by_key.values():
        g["id"] = len(groups)
        for ln in g["nc"] + g["wms"]:
            ln.pair, ln.tier = g["id"], 1
        groups.append(g)

    # Tier 2: same category, same quantity, nearest time within the window —
    # compared per DOCUMENT, because one NC line is often many WMS lines (a
    # finished-goods receipt of 5,216.4 arrives in WMS as 13 pallets on one
    # ASN). A single-line document is just a document of one line.
    def buckets(lines, side):
        out: dict[tuple, list[Line]] = defaultdict(list)
        for ln in lines:
            if ln.pair is None and visible(ln) and ln.time:
                out[(ln.category, ln.doc or f"{side}:{ln.id}")].append(ln)
        return [{"cat": k[0], "lines": v, "qty": sum((x.qty for x in v), ZERO),
                 "time": min(x.time for x in v)} for k, v in out.items()]

    wb = buckets(wms, "w")
    for nbk in sorted(buckets(nc, "n"), key=lambda b: b["time"]):
        window = timedelta(days=_WINDOW[nbk["cat"]])
        best, best_gap = None, None
        for wbk in wb:
            if wbk.get("taken") or wbk["cat"] != nbk["cat"] or abs(wbk["qty"] - nbk["qty"]) > qty_tol:
                continue
            gap = abs(wbk["time"] - nbk["time"])
            if gap <= window and (best_gap is None or gap < best_gap):
                best, best_gap = wbk, gap
        if best is not None:
            best["taken"] = True
            g = {"id": len(groups), "tier": 2, "key": None, "nc": nbk["lines"], "wms": best["lines"]}
            for ln in g["nc"] + g["wms"]:
                ln.pair, ln.tier = g["id"], 2
            groups.append(g)
    return groups


def first_divergence(nc: list[Line], wms: list[Line], nc_opening: Decimal,
                     qty_tol: Decimal) -> dict | None:
    """Walk both sides in time order; the divergence is the first event after
    the LAST moment the balances agreed. None when they agree at the end."""
    events = sorted(
        [ln for ln in nc + wms if ln.time is not None],
        key=lambda ln: (ln.time, 0 if ln.side == "nc" else 1, ln.doc or "", ln.id))
    nc_bal, wms_bal = nc_opening, ZERO
    # -1 = "agreed before the first event"; None = never agreed.
    last_agree_index = -1 if abs(nc_bal - wms_bal) <= qty_tol else None
    for i, ln in enumerate(events):
        if ln.side == "nc":
            nc_bal += ln.qty
        else:
            wms_bal += ln.qty
        if abs(nc_bal - wms_bal) <= qty_tol:
            last_agree_index = i
    if abs(nc_bal - wms_bal) <= qty_tol:
        return None
    idx = 0 if last_agree_index is None else last_agree_index + 1
    if idx >= len(events):
        return None
    # The first event after agreement that is NOT internal noise — and, since
    # the first one can be a trivial −4.2 ahead of a −5,040, also the largest
    # UNMATCHED movement since agreement: usually the one to look at.
    after = [ln for ln in events[idx:] if ln.category not in HIDDEN_BY_DEFAULT] or [events[idx]]
    docs: dict[tuple, dict] = {}
    for ln in after:
        if ln.pair is None:
            d = docs.setdefault((ln.side, ln.doc or ln.id), {
                "side": ln.side, "doc": ln.doc, "category": ln.category, "time": ln.time,
                "qty": ZERO, "lines": 0})
            d["qty"] += ln.qty
            d["lines"] += 1
    main = max(docs.values(), key=lambda x: abs(x["qty"])) if docs else None
    return {"line": after[0], "main": main, "diverged_from_start": last_agree_index is None}


def describe_doc(m: dict) -> str:
    side = "NC" if m["side"] == "nc" else "WMS"
    other = "WMS" if m["side"] == "nc" else "NC"
    when = m["time"].date().isoformat() if m["time"] else "?"
    lines = f" ({m['lines']} lines)" if m["lines"] > 1 else ""
    what = CATEGORY_LABELS.get(m["category"], m["category"]).lower()
    return f"{side} {when}: {what} {m['doc'] or ''} {_fmt(m['qty'])}{lines} — no matching {other} movement."


def describe(ln: Line, groups: list[dict]) -> str:
    """A rule-built sentence for the divergence row — no AI."""
    when = ln.time.date().isoformat() if ln.time else "?"
    what = CATEGORY_LABELS.get(ln.category, ln.category).lower()
    side = "NC" if ln.side == "nc" else "WMS"
    other = "WMS" if ln.side == "nc" else "NC"
    doc = f" {ln.doc}" if ln.doc else ""
    if ln.pair is None:
        return f"{side} {when}: {what}{doc} {_fmt(ln.qty)} — no matching {other} movement."
    g = groups[ln.pair]
    nq, wq = sum((x.qty for x in g["nc"]), ZERO), sum((x.qty for x in g["wms"]), ZERO)
    if g["tier"] == 1 and abs(nq - wq) > 0:
        return (f"{when}: {what} under {g['key']} — NC {_fmt(nq)} vs WMS {_fmt(wq)} "
                f"(difference {_fmt(nq - wq)}).")
    return f"{side} {when}: {what}{doc} {_fmt(ln.qty)} — matched, timing differs."


def _fmt(q: Decimal) -> str:
    s = format(q.normalize(), "f") if q != 0 else "0"
    return ("+" if q > 0 else "") + s


# ── Loading ────────────────────────────────────────────────────────────────

async def batch_scope(db: AsyncSession, code: str, pk: str | None, batch: str) -> tuple[set[str], set[str], str]:
    """Which NC batch codes and WMS lots make up the batch GROUP containing
    `batch` (either number, normalised; transitive — design D10)."""
    wms_lots = [{"lotnum": r.lotnum, "qc_batch": r.qc_batch, "supplier_batch": r.supplier_batch, "qty": ZERO}
                for r in (await db.execute(text(
                    "select lotnum, qc_batch, supplier_batch from inv_wms_lot where sku = :c"), {"c": code})).all()]
    nc_batches = [{"batch": r.vbatchcode, "supplier_batch": r.vvendbatchcode}
                  for r in (await db.execute(text(
                      "select vbatchcode, vvendbatchcode from inv_nc_batch where cmaterialoid = :m"),
                      {"m": pk})).all()] if pk else []
    # Flow rows may carry a batch code with no master row; include them too.
    if pk:
        known = {b["batch"] for b in nc_batches}
        for (b,) in (await db.execute(text(
                "select distinct vbatchcode from inv_nc_ic_flow where cmaterialoid = :m and vbatchcode is not null"),
                {"m": pk})).all():
            if b not in known:
                nc_batches.append({"batch": b, "supplier_batch": None})
    key = norm_batch(batch)
    for g in group_batches(wms_lots, nc_batches):
        members = {norm_batch(x) for x in g["nc_batches"] + g["supplier_batches"]}
        lots = set(g["wms_lots"])
        lot_batches = {norm_batch(w["qc_batch"]) for w in wms_lots if w["lotnum"] in lots} | \
                      {norm_batch(w["supplier_batch"]) for w in wms_lots if w["lotnum"] in lots}
        if key in members or key in lot_batches:
            return set(g["nc_batches"]), lots, g["label"]
    return set(), set(), batch


async def load(db: AsyncSession, code: str, d: date, batch: str | None) -> dict:
    tz = await params.get_param(db, params.NC_TIME_ZONE_KEY)
    qty_tol = Decimal(str(await params.get_param(db, params.QTY_TOLERANCE_KEY)))
    a = asof.make_asof(d, tz)
    start = WMS_GO_LIVE

    mat = (await db.execute(text(
        "select pk_material, code, name, uom_code from inv_nc_material where code = :c"), {"c": code})).first()
    pk = mat.pk_material if mat else None
    no_batch = (await db.execute(text(
        "select since_date from inv_no_batch_material where material_code = :c"), {"c": code})).scalar()

    nc_batches = wms_lots = None
    group_label = None
    if batch:
        nc_batches, wms_lots, group_label = await batch_scope(db, code, pk, batch)

    wms_wh = set((await db.execute(text("""
        select w.pk_stordoc from inv_nc_warehouse w
        join inv_warehouse_class c on c.nc_code = w.code and c.warehouse_class = 'wms'"""))).scalars().all())

    nc_lines: list[Line] = []
    nc_opening = ZERO
    if pk:
        rows = (await db.execute(text(f"""
            select f.pk_flow, f.biz_time, f.dbizdate, f.vtrantypecode, f.cbilltypecode, f.cwarehouseid,
                   f.cotherwhid, f.vbatchcode, coalesce(f.ninnum,0) - coalesce(f.noutnum,0) q,
                   f.vbillcode, f.vsourcebillcode, f.vfirstbillcode, f.vheaduserdef6, f.vheaduserdef13,
                   f.fbillflag, w.code wh, ow.code other_wh, coalesce(t.name_alt, t.name) tname,
                   {asof.nc_time_le('f.biz_time')} in_window_end
            from inv_nc_ic_flow f
            left join inv_nc_warehouse w on w.pk_stordoc = f.cwarehouseid
            left join inv_nc_warehouse ow on ow.pk_stordoc = f.cotherwhid
            left join inv_nc_trantype t on t.pk_billtypeid = f.ctrantypeid
            where f.cmaterialoid = :m and f.cwarehouseid = any(:wh)"""),
            {**a.sql_params, "m": pk, "wh": list(wms_wh)})).all()
        for r in rows:
            if nc_batches is not None and r.vbatchcode not in nc_batches:
                continue
            if not r.in_window_end:
                continue
            t, date_only = nc_local_time(r.biz_time, tz)
            if t is None or t.date() < start:
                nc_opening += r.q
                continue
            cat = nc_category(r.vtrantypecode, r.cotherwhid in wms_wh)
            if cat == "internal" and nc_batches is not None:
                cat = "other"       # inside one batch a SC11↔SC16 move still nets to 0
            nc_lines.append(Line(
                side="nc", id=r.pk_flow, time=t, date_only=date_only, category=cat, qty=r.q,
                doc=r.vbillcode,
                keys=keys_of(r.vbillcode, r.vsourcebillcode, r.vfirstbillcode, r.vheaduserdef6, r.vheaduserdef13),
                detail={"bill": r.vbillcode, "source": r.vsourcebillcode, "first": r.vfirstbillcode,
                        "wms_ref": r.vheaduserdef13, "trantype": r.vtrantypecode, "trantype_name": r.tname,
                        "warehouse": r.wh, "other_warehouse": r.other_wh, "batch": r.vbatchcode,
                        "unsigned": r.fbillflag == 2, "nc_time": r.dbizdate}))

    wms_lines: list[Line] = []
    if d >= WMS_GO_LIVE:
        rows = (await db.execute(text("""
            select m.move_id, m.transactionid, m.txn_type, m.doctype, m.docno, m.lotnum, m.qty, m.txn_time,
                   m.reasoncode, m.reason, m.addwho,
                   l.qc_batch, l.supplier_batch,
                   dc.doc_type, dc.ref1, dc.ref2, dc.ref3, dc.ref4, dc.ref5, dc.pono,
                   (select count(distinct x.sku) from inv_wms_move x where x.transactionid = m.transactionid) skus,
                   (select bool_and(x.lotnum = any(cast(:lots as text[]))) from inv_wms_move x
                     where x.transactionid = m.transactionid) all_in_group
            from inv_wms_move m
            left join inv_wms_lot l on l.sku = m.sku and l.lotnum = m.lotnum
            left join inv_wms_doc dc on dc.docno = m.docno and dc.doc_kind = case
                 when m.doctype = 'ASN' then 'ASN' when m.doctype = 'SO' then 'SO'
                 when m.doctype = 'AJ' then 'AJ' when m.doctype = 'TR' then 'TR' end
            where m.sku = :c and m.txn_time <= :cut
            order by m.txn_time, m.move_id"""),
            {"c": code, "cut": datetime.combine(d, time(23, 59, 59)),
             "lots": list(wms_lots) if wms_lots is not None else None})).all()
        for r in rows:
            if wms_lots is not None and r.lotnum not in wms_lots:
                continue
            same_sku_tr = r.txn_type == "TR" and r.skus == 1 and (wms_lots is None or bool(r.all_in_group))
            cat = wms_category(r.txn_type, r.doc_type, same_sku_tr)
            wms_lines.append(Line(
                side="wms", id=r.move_id, time=r.txn_time, date_only=False, category=cat, qty=r.qty,
                doc=r.docno,
                keys=keys_of(r.docno, r.ref1, r.ref2, r.ref3, r.ref5, r.pono),
                detail={"doc": r.docno, "doc_type": r.doc_type, "txn_type": r.txn_type,
                        "refs": [x for x in (r.ref1, r.ref2, r.ref3, r.ref4, r.ref5, r.pono) if x],
                        "lot": r.lotnum, "qc_batch": r.qc_batch, "supplier_batch": r.supplier_batch,
                        "reason": r.reason or r.reasoncode, "by": r.addwho}))

    groups = pair_lines(nc_lines, wms_lines, qty_tol)
    div = first_divergence(nc_lines, wms_lines, nc_opening, qty_tol)
    return {
        "material": {"code": code, "name": mat.name if mat else None, "uom": mat.uom_code if mat else None,
                     "in_nc": mat is not None},
        "batch": batch, "batch_group": group_label,
        "batch_scope": {"nc_batches": sorted(nc_batches) if nc_batches is not None else None,
                        "wms_lots": sorted(wms_lots) if wms_lots is not None else None},
        "no_batch_since": no_batch, "date": d, "start": start, "nc_time_zone": tz,
        "nc_opening": nc_opening, "nc": nc_lines, "wms": wms_lines, "groups": groups,
        "divergence": div, "qty_tol": qty_tol,
    }
