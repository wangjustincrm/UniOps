"""NC → inv_nc_* mirror sync, with a tie-out against NC's own balances.

Shape: extract everything from NC first (one Oracle session, in a worker
thread), then replace every mirror table inside ONE Postgres transaction and
tie the result out before committing. A reader therefore sees either the old
snapshot or the new one, never a half-filled mix; and a failed extract leaves
the previous snapshot untouched (only the state row records the failure).

The tie-out is what makes a mirror trustworthy rather than merely present
(design §5.4 / Finance positioning red line). Three checks, each comparing
the mirror with a figure NC computed itself, fetched in the same NC session:

* IC — Σ flow (all time) per warehouse × material × batch  vs  current on-hand
* IA — Σ ledger up to the last closed period, per material  vs  IA_MONTHNAB
* GL — Σ POSTED lines per year × account                     vs  GL_BALANCE

A tie-out mismatch does NOT fail the sync: the data is still the best copy of
NC there is, and hiding it would be worse. It is recorded (tieout_ok=False +
inv_tieout_issue rows) and the pages show a red banner. No Task, no
notification (user decision Q4).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import anyio
from sqlalchemy import delete, insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InvSyncState, InvTieoutIssue, NcBatch, NcGlLine, NcIaLedger, NcIaMonthNab, NcIaPeriod,
    NcIcFlow, NcIcOnhand, NcMaterial, NcStoreState, NcTranType, NcWarehouse,
)
from app.services.nc_sync import reader, transform

log = logging.getLogger(__name__)

SOURCE = "nc"

# extract name → (model, row transform, primary key column)
MIRRORS: dict[str, tuple[type, Callable[[dict], dict], str]] = {
    "ic_flow": (NcIcFlow, transform.ic_flow, "pk_flow"),
    "ic_onhand": (NcIcOnhand, transform.ic_onhand, "pk_onhanddim"),
    "batch": (NcBatch, transform.batch, "pk_batchcode"),
    "warehouse": (NcWarehouse, transform.warehouse, "pk_stordoc"),
    "store_state": (NcStoreState, transform.store_state, "pk_storestate"),
    "material": (NcMaterial, transform.material, "pk_material"),
    "trantype": (NcTranType, transform.trantype, "pk_billtypeid"),
    "ia_ledger": (NcIaLedger, transform.ia_ledger, "cdetailledgerid"),
    "ia_monthnab": (NcIaMonthNab, transform.ia_monthnab, "cmonthnabid"),
    "ia_period": (NcIaPeriod, transform.ia_period, "caccountid"),
    "gl_line": (NcGlLine, transform.gl_line, "pk_detail"),
}

# Columns that must be present for a row to be storable at all. A row missing
# one is not silently dropped: it is counted under filtered["<name>_unusable"].
REQUIRED = {
    "warehouse": ("code",),
    "material": ("code",),
    "ia_period": ("caccountperiod",),
    "gl_line": ("pk_voucher", "year", "period", "account_code"),
}

NC_CHECKS = ("ic_flow_vs_onhand", "ia_ledger_vs_monthnab", "gl_lines_vs_balance")
QTY_TOLERANCE = Decimal("0.0001")
AMOUNT_TOLERANCE = Decimal("0.005")
MAX_ISSUES_STORED_PER_CHECK = 500
_PG_MAX_PARAMS = 32767


def nc_configured() -> bool:
    return reader.nc_configured()


def shape_extract(raw: dict) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Transform every extracted table; return (rows by table, filtered counts).

    Pure, so the de-duplication and "unusable row" accounting is testable
    without NC or Postgres. A duplicate primary key from NC would make the
    bulk INSERT fail the whole sync; it is kept once and counted instead."""
    tables = raw["tables"]
    # int(): counts may arrive as Decimal (oracledb fetch_decimals) and are
    # stored in JSONB.
    filtered: dict[str, int] = {k: int(v) for k, v in raw.get("filtered", {}).items()}
    shaped: dict[str, list[dict]] = {}
    for name, (_model, fn, pk) in MIRRORS.items():
        seen: set[str] = set()
        out: list[dict] = []
        unusable = dup = 0
        for row in tables.get(name, []):
            r = fn(row)
            if r.get(pk) is None or any(r.get(c) is None for c in REQUIRED.get(name, ())):
                unusable += 1
                continue
            if r[pk] in seen:
                dup += 1
                continue
            seen.add(r[pk])
            out.append(r)
        shaped[name] = out
        if unusable:
            filtered[f"{name}_unusable"] = unusable
        if dup:
            filtered[f"{name}_duplicate_pk"] = dup
    return shaped, filtered


async def _bulk_insert(db: AsyncSession, model: type, rows: list[dict]) -> None:
    if not rows:
        return
    ncols = len(rows[0])
    chunk = max(1, _PG_MAX_PARAMS // ncols)
    for i in range(0, len(rows), chunk):
        await db.execute(insert(model), rows[i:i + chunk])


async def _tieout(db: AsyncSession, reference: dict) -> list[dict]:
    """Run the three checks against the freshly written (uncommitted) mirror.
    Returns issue dicts ready for inv_tieout_issue."""
    issues: list[dict] = []

    ic = (await db.execute(text("""
        with f as (
            select cwarehouseid wh, cmaterialoid mat, coalesce(vbatchcode,'') b,
                   sum(coalesce(ninnum,0) - coalesce(noutnum,0)) q
            from inv_nc_ic_flow group by 1,2,3),
        o as (
            select cwarehouseid wh, cmaterialoid mat, coalesce(vbatchcode,'') b,
                   sum(coalesce(nonhandnum,0)) q
            from inv_nc_ic_onhand group by 1,2,3)
        select coalesce(f.wh,o.wh) wh, coalesce(f.mat,o.mat) mat, coalesce(f.b,o.b) b,
               coalesce(f.q,0) mirror_q, coalesce(o.q,0) nc_q,
               w.code wh_code, m.code mat_code
        from f full join o on o.wh = f.wh and o.mat = f.mat and o.b = f.b
        left join inv_nc_warehouse w on w.pk_stordoc = coalesce(f.wh,o.wh)
        left join inv_nc_material m on m.pk_material = coalesce(f.mat,o.mat)
        where abs(coalesce(f.q,0) - coalesce(o.q,0)) > :tol
    """), {"tol": QTY_TOLERANCE})).all()
    for r in ic:
        issues.append({
            "check_name": "ic_flow_vs_onhand", "measure": "qty",
            "key": f"{r.wh}|{r.mat}|{r.b}",
            "label": f"{r.wh_code or r.wh} · {r.mat_code or r.mat} · {r.b or '(no batch)'}",
            "mirror_value": r.mirror_q, "nc_value": r.nc_q,
        })

    last_closed = (await db.execute(text(
        "select max(caccountperiod) from inv_nc_ia_period"))).scalar()
    if last_closed:
        ia = (await db.execute(text("""
            with l as (
                select cinventoryid inv,
                       sum(case when fdispatchflag = 0 then 1 else -1 end * coalesce(nnum,0)) q,
                       sum(case when fdispatchflag = 0 then 1 else -1 end * coalesce(nmny,0)) a
                from inv_nc_ia_ledger where caccountperiod <= :p group by 1),
            n as (
                select cinventoryid inv, sum(coalesce(nabnum,0)) q, sum(coalesce(nabmny,0)) a
                from inv_nc_ia_monthnab where caccountperiod = :p group by 1)
            select coalesce(l.inv,n.inv) inv, coalesce(l.q,0) lq, coalesce(n.q,0) nq,
                   coalesce(l.a,0) la, coalesce(n.a,0) na, m.code mat_code
            from l full join n on n.inv = l.inv
            left join inv_nc_material m on m.pk_material = coalesce(l.inv,n.inv)
            where abs(coalesce(l.q,0) - coalesce(n.q,0)) > :qt
               or abs(coalesce(l.a,0) - coalesce(n.a,0)) > :at
        """), {"p": last_closed, "qt": QTY_TOLERANCE, "at": AMOUNT_TOLERANCE})).all()
        for r in ia:
            label = f"{r.mat_code or r.inv} @ {last_closed}"
            if abs(r.lq - r.nq) > QTY_TOLERANCE:
                issues.append({"check_name": "ia_ledger_vs_monthnab", "measure": "qty",
                               "key": f"{r.inv}|{last_closed}", "label": label,
                               "mirror_value": r.lq, "nc_value": r.nq})
            if abs(r.la - r.na) > AMOUNT_TOLERANCE:
                issues.append({"check_name": "ia_ledger_vs_monthnab", "measure": "amount",
                               "key": f"{r.inv}|{last_closed}", "label": label,
                               "mirror_value": r.la, "nc_value": r.na})

    posted = {
        (r.year, r.account_code): (r.a, r.q)
        for r in (await db.execute(text("""
            select year, account_code,
                   sum(coalesce(debit_amount,0) - coalesce(credit_amount,0)) a,
                   sum(coalesce(debit_qty,0) - coalesce(credit_qty,0)) q
            from inv_nc_gl_line where tallydate is not null group by 1,2"""))).all()
    }
    nc_bal = {}
    for r in reference.get("gl_balance", []):
        key = (transform.clean_str(r["year"]), transform.clean_str(r["account_code"]))
        nc_bal[key] = (transform.num(r["amount"]) or Decimal(0), transform.num(r["qty"]) or Decimal(0))
    for key in sorted(set(posted) | set(nc_bal), key=lambda k: (k[0] or "", k[1] or "")):
        ma, mq = posted.get(key, (Decimal(0), Decimal(0)))
        na, nq = nc_bal.get(key, (Decimal(0), Decimal(0)))
        for measure, m, n, tol in (("amount", ma, na, AMOUNT_TOLERANCE), ("qty", mq, nq, QTY_TOLERANCE)):
            if abs(m - n) > tol:
                issues.append({"check_name": "gl_lines_vs_balance", "measure": measure,
                               "key": f"{key[0]}|{key[1]}", "label": f"{key[1]} · {key[0]}",
                               "mirror_value": m, "nc_value": n})
    return issues


async def _write_state(db: AsyncSession, source: str = SOURCE, **fields) -> None:
    state = await db.get(InvSyncState, source)
    if state is None:
        state = InvSyncState(source=source, tieout_issue_count=0)
        db.add(state)
    for k, v in fields.items():
        setattr(state, k, v)
    state.updated_at = datetime.now(timezone.utc)


async def run_nc_sync(db: AsyncSession, extract: Callable[[], dict] = reader.extract_all) -> dict:
    """Replace the NC mirrors and tie them out. Commits. Raises on failure
    after recording it on the state row (the previous snapshot stays)."""
    started = time.monotonic()
    attempted_at = datetime.now(timezone.utc)
    try:
        raw = await anyio.to_thread.run_sync(extract)
        shaped, filtered = shape_extract(raw)

        for _name, (model, _fn, _pk) in MIRRORS.items():
            await db.execute(delete(model))
        for name, (model, _fn, _pk) in MIRRORS.items():
            await _bulk_insert(db, model, shaped[name])
        await db.flush()

        issues = await _tieout(db, raw.get("reference", {}))
        # Only this sync's own checks — the WMS sync keeps its findings here too.
        await db.execute(delete(InvTieoutIssue).where(InvTieoutIssue.check_name.in_(NC_CHECKS)))
        per_check: dict[str, int] = {}
        stored = []
        for i in issues:
            per_check[i["check_name"]] = per_check.get(i["check_name"], 0) + 1
            if per_check[i["check_name"]] <= MAX_ISSUES_STORED_PER_CHECK:
                stored.append(i)
        await _bulk_insert(db, InvTieoutIssue, stored)

        row_counts = {name: len(rows) for name, rows in shaped.items()}
        duration_ms = int((time.monotonic() - started) * 1000)
        await _write_state(
            db, status="success", last_error=None, row_counts=row_counts,
            filtered_counts=filtered, duration_ms=duration_ms,
            last_synced_at=attempted_at, last_success_at=attempted_at,
            tieout_ok=not issues, tieout_issue_count=len(issues),
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # Whatever the failed attempt left in the session (e.g. a state row
        # with an unserialisable field) must not ride along on the failure
        # record's commit.
        db.expunge_all()
        await _write_state(
            db, status="failed", last_error=f"{type(exc).__name__}: {exc}"[:2000],
            last_synced_at=attempted_at,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        await db.commit()
        log.exception("NC inventory sync failed")
        raise

    result = {"status": "success", "rows": row_counts, "filtered": filtered,
              "tieout_ok": not issues, "tieout_issues": per_check, "duration_ms": duration_ms}
    log.info("NC inventory sync: %s", result)
    return result
