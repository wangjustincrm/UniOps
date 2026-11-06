"""WMS transaction history → inv_wms_* mirrors, tied out against INV_LOT.

Same shape as the NC sync: extract in a worker thread, replace every mirror in
one transaction, tie out, then commit. A failure leaves the previous snapshot
and records the error on the 'wms' state row.

Tie-out: Σ legs per SKU × lot  vs  WMS's current INV_LOT.QTY. Known baseline
(2026-10-14): 2 of 25k lots differ for reasons in WMS itself — a receipt line
edited after posting, and the virtual SKU FULLCARTON which holds stock with no
transactions. They show up as issues, by design: a reconciliation tool must not
quietly carry an allow-list.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import anyio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InvSyncState, InvTieoutIssue, WmsCode, WmsDoc, WmsLot, WmsMove, WmsSku
from app.services.nc_sync.service import (
    MAX_ISSUES_STORED_PER_CHECK, QTY_TOLERANCE, _bulk_insert, _write_state,
)
from app.services.nc_sync.transform import clean_str, num, parse_nc_date, to_int
from app.services.wms_sync import reader

log = logging.getLogger(__name__)

SOURCE = "wms"
CHECK = "wms_moves_vs_inv_lot"


def wms_configured() -> bool:
    return reader.wms_configured()


def _dt(v) -> datetime | None:
    if v is None:
        return None
    return v.replace(tzinfo=None) if isinstance(v, datetime) else None


def txn_to_moves(row: dict) -> list[dict]:
    """One WMS transaction → its signed legs (pure; rules in models/wms_mirror)."""
    tid = clean_str(row.get("transactionid"))
    ttype = clean_str(row.get("transactiontype"))
    fs, fl = clean_str(row.get("fmsku")), clean_str(row.get("fmlotnum"))
    ts, tl = clean_str(row.get("tosku")), clean_str(row.get("tolotnum"))
    fq, tq = num(row.get("fmqty_each")) or Decimal(0), num(row.get("toqty_each")) or Decimal(0)
    common = {
        "transactionid": tid, "txn_type": ttype, "doctype": clean_str(row.get("doctype")),
        "docno": clean_str(row.get("docno")), "doclineno": to_int(row.get("doclineno")),
        "txn_time": _dt(row.get("transactiontime")), "reasoncode": clean_str(row.get("reasoncode")),
        "reason": clean_str(row.get("reason")), "addwho": clean_str(row.get("addwho")),
    }
    if ttype == "IN":
        legs = [("T", ts, tl, tq)]
    elif ttype == "SO":
        legs = [("F", fs, fl, -fq)]
    elif ttype == "AD":
        legs = [("F", fs, fl, -fq), ("T", ts, tl, tq)]
    elif ttype == "TR":
        if (fs, fl) == (ts, tl):
            return []  # location move: nets to zero
        legs = [("F", fs, fl, -tq), ("T", ts, tl, tq)]
    else:
        return []
    return [{**common, "move_id": f"{tid}:{leg}", "sku": sku, "lotnum": lot, "qty": q}
            for leg, sku, lot, q in legs if sku is not None]


def shape_extract(raw: dict) -> tuple[dict[str, list[dict]], dict[str, int]]:
    t = raw["tables"]
    filtered = {k: int(v) for k, v in raw.get("filtered", {}).items()}
    moves, same_lot = [], 0
    for row in t.get("txn", []):
        legs = txn_to_moves(row)
        if not legs and clean_str(row.get("transactiontype")) == "TR":
            same_lot += 1
        moves.extend(legs)
    filtered["tr_location_only"] = same_lot

    lots, seen = [], set()
    for r in t.get("lot", []):
        key = (clean_str(r.get("sku")), clean_str(r.get("lotnum")))
        if None in key or key in seen:
            filtered["lot_unusable_or_duplicate"] = filtered.get("lot_unusable_or_duplicate", 0) + 1
            continue
        seen.add(key)
        lots.append({"sku": key[0], "lotnum": key[1],
                     "qc_batch": clean_str(r.get("lotatt04")), "supplier_batch": clean_str(r.get("lotatt05")),
                     "quality_status": clean_str(r.get("lotatt08")),
                     "produced": parse_nc_date(r.get("lotatt01")), "expires": parse_nc_date(r.get("lotatt02")),
                     "received": parse_nc_date(r.get("lotatt03"))})

    docs, seen = [], set()
    for r in t.get("doc", []):
        key = (clean_str(r.get("doc_kind")), clean_str(r.get("docno")))
        if None in key or key in seen:
            continue
        seen.add(key)
        docs.append({"doc_kind": key[0], "docno": key[1], "doc_type": clean_str(r.get("doc_type")),
                     **{f"ref{i}": clean_str(r.get(f"ref{i}")) for i in range(1, 6)},
                     "pono": clean_str(r.get("pono")), "udf01": clean_str(r.get("udf01")),
                     "hedi03": clean_str(r.get("hedi03")), "reasoncode": clean_str(r.get("reasoncode")),
                     "reason": clean_str(r.get("reason")), "added": _dt(r.get("added"))})

    codes, seen = [], set()
    for r in t.get("code", []):
        key = (clean_str(r.get("codetype")), clean_str(r.get("codeid")), clean_str(r.get("languageid")))
        if None in key or key in seen:
            continue
        seen.add(key)
        codes.append({"codetype": key[0], "codeid": key[1], "languageid": key[2],
                      "descr": clean_str(r.get("descr"))})
    skus, seen = [], set()
    for r in t.get("sku", []):
        sku = clean_str(r.get("sku"))
        if sku is None or sku in seen:
            continue
        seen.add(sku)
        skus.append({"sku": sku, "uom": clean_str(r.get("uom"))})
    return {"move": moves, "lot": lots, "doc": docs, "code": codes, "sku": skus}, filtered


async def _tieout(db: AsyncSession, reference: dict) -> list[dict]:
    mirror = {(r.sku, r.lotnum): r.q for r in (await db.execute(text(
        "select sku, lotnum, sum(qty) q from inv_wms_move group by 1,2"))).all()}
    wms: dict[tuple, Decimal] = {}
    for r in reference.get("inv_lot", []):
        key = (clean_str(r["sku"]), clean_str(r["lotnum"]))
        wms[key] = wms.get(key, Decimal(0)) + (num(r["qty"]) or Decimal(0))
    issues = []
    for key in sorted(set(mirror) | set(wms), key=lambda k: (k[0] or "", k[1] or "")):
        m, w = mirror.get(key, Decimal(0)), wms.get(key, Decimal(0))
        if abs(m - w) > QTY_TOLERANCE:
            issues.append({"check_name": CHECK, "measure": "qty", "key": f"{key[0]}|{key[1]}",
                           "label": f"{key[0]} · lot {key[1]}", "mirror_value": m, "nc_value": w})
    return issues


async def run_wms_sync(db: AsyncSession, extract: Callable[[], dict] = reader.extract_all) -> dict:
    started = time.monotonic()
    attempted_at = datetime.now(timezone.utc)
    try:
        raw = await anyio.to_thread.run_sync(extract)
        shaped, filtered = shape_extract(raw)
        for model in (WmsMove, WmsLot, WmsDoc, WmsCode, WmsSku):
            await db.execute(delete(model))
        await _bulk_insert(db, WmsMove, shaped["move"])
        await _bulk_insert(db, WmsLot, shaped["lot"])
        await _bulk_insert(db, WmsDoc, shaped["doc"])
        await _bulk_insert(db, WmsCode, shaped["code"])
        await _bulk_insert(db, WmsSku, shaped["sku"])
        await db.flush()

        issues = await _tieout(db, raw.get("reference", {}))
        await db.execute(delete(InvTieoutIssue).where(InvTieoutIssue.check_name == CHECK))
        await _bulk_insert(db, InvTieoutIssue, issues[:MAX_ISSUES_STORED_PER_CHECK])
        rows = {k: len(v) for k, v in shaped.items()}
        duration_ms = int((time.monotonic() - started) * 1000)
        await _write_state(db, source=SOURCE, status="success", last_error=None, row_counts=rows,
                           filtered_counts=filtered, duration_ms=duration_ms,
                           last_synced_at=attempted_at, last_success_at=attempted_at,
                           tieout_ok=not issues, tieout_issue_count=len(issues))
        await db.commit()
    except Exception as exc:
        await db.rollback()
        db.expunge_all()
        await _write_state(db, source=SOURCE, status="failed",
                           last_error=f"{type(exc).__name__}: {exc}"[:2000],
                           last_synced_at=attempted_at,
                           duration_ms=int((time.monotonic() - started) * 1000))
        await db.commit()
        log.exception("WMS transaction-history sync failed")
        raise
    result = {"status": "success", "rows": rows, "filtered": filtered, "tieout_ok": not issues,
              "tieout_issues": len(issues), "duration_ms": duration_ms}
    log.info("WMS transaction-history sync: %s", result)
    return result
