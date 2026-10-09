"""NC65 inventory-accounting + cost sync for AI Financial Analysis.

Same contract as services/nc_ap_sync.py (start_run / _run_worker, a run row,
a tie-out recorded on every run), one deliberate difference: there is no
watermark. Every run re-reads the whole scope (2026 onwards, decision D4) and
REPLACES it in one transaction.

Why, measured on NC production 2026-10-09:
  * The scope is ~20k rows (ledger 14,762; everything else together ~5k). A
    full read is seconds.
  * An incremental mirror cannot see a row deleted outright in NC — nothing
    advances a timestamp — and the AP mirror needs a manual FULL RELOAD to
    reap those. Here there is nothing to remember to do.
  * Cost figures are rewritten wholesale by NC's month-end cost calculation
    (I3 prices back-filled, IA tail adjustments generated); reasoning about
    which subset moved buys nothing.

The tie-out compares NC's own aggregates — count and amount, per table, per
period (and per bill type for the ledger) — with the same aggregates over what
we stored, so a truncated read or a dropped row shows up as a named cell, not
as a slightly-off number three pages later.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg2
from psycopg2.extras import Json, execute_values, register_uuid
from sqlalchemy.engine.url import make_url

from app.core.config import settings

register_uuid()
logger = logging.getLogger(__name__)

# Scope floor (decision D4: 2026 only, to be widened once the module proves
# itself). The month-end balance table reaches one period further back so
# January has an opening balance.
PERIOD_FLOOR = "2026-01"
BALANCE_FLOOR = "2025-12"
DATE_FLOOR = "2026-01-01"

_start_lock = threading.Lock()
_CHUNK = 5000


class NcCostSyncError(ValueError):
    pass


class SyncAlreadyRunning(Exception):
    pass


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


# ── table specs ──────────────────────────────────────────────────────────────
# Each NC select aliases its columns to OUR column names, in the order of
# `cols`. `dates` are CHAR(19) in NC ('YYYY-MM-DD HH:MM:SS'); only the date part
# is kept. `group` is the tie-out grain.

@dataclass(frozen=True)
class TableSpec:
    name: str
    cols: tuple[str, ...]
    select: str
    tie_out: str              # NC aggregate: (group cols…, count, sum(amount))
    group: tuple[str, ...]
    dates: tuple[str, ...] = ()
    texts: dict = field(default_factory=dict)   # col -> max length


LEDGER = TableSpec(
    name="nc_ia_ledger",
    cols=("nc_pk", "period", "bill_type", "bill_code", "bill_id", "bill_bid", "row_no",
          "biz_date", "direction", "material_pk", "material_code", "material_name",
          "qty", "price", "amount", "batch_code", "mo_code", "src_type", "src_code",
          "first_code", "upstream_type", "upstream_code", "upstream_bid", "trantype_code", "trantype_name", "estimate_flag", "audit_seq",
          "price_mode", "note", "dr", "nc_ts"),
    select="""
      select d.cdetailledgerid, d.caccountperiod, d.cbilltypecode, d.vbillcode, d.cbillid,
             d.cbill_bid, d.crowno, substr(d.dbizdate, 1, 10), d.fdispatchflag,
             d.cinventoryid, m.code, m.name, d.nnum, d.nprice, d.nmny, d.vbatchcode,
             d.vproduceordercode, d.vsrctype, d.vsrccode, d.vfirstcode,
             coalesce(gi.csourcetype, go.csourcetype),
             coalesce(gi.vsourcebillcode, go.vsourcebillcode),
             coalesce(gi.csourcebillbid, go.csourcebillbid),
             t.pk_billtypecode, t.billtypename, d.bestimateflag, d.iauditsequence,
             d.fpricemodeflag, d.vnote, d.dr, d.ts
        from NCSC.IA_DETAILLEDGER d
        left join NCSC.BD_MATERIAL m on m.pk_material = d.cinventoryid
        left join NCSC.BD_BILLTYPE t on t.pk_billtypeid = d.ctrantypeid
        left join NCSC.IC_GENERALIN_B gi on gi.cgeneralbid = d.csrcbid and d.vsrctype = '4A'
        left join NCSC.IC_GENERALOUT_B go on go.cgeneralbid = d.csrcbid and d.vsrctype = '4I'
       where d.caccountperiod >= :p""",
    tie_out="""
      select caccountperiod, cbilltypecode, count(*), nvl(sum(nmny), 0)
        from NCSC.IA_DETAILLEDGER where caccountperiod >= :p
       group by caccountperiod, cbilltypecode""",
    group=("period", "bill_type"),
    dates=("biz_date",),
    texts={"note": 500, "material_name": 300, "trantype_name": 200},
)

MONTH_BALANCES = TableSpec(
    name="nc_ia_month_balances",
    cols=("nc_pk", "period", "material_pk", "material_code", "qty", "amount", "price",
          "dr", "nc_ts"),
    select="""
      select n.cmonthnabid, n.caccountperiod, n.cinventoryid, m.code, n.nabnum, n.nabmny,
             n.nabprice, n.dr, n.ts
        from NCSC.IA_MONTHNAB n
        left join NCSC.BD_MATERIAL m on m.pk_material = n.cinventoryid
       where n.caccountperiod >= :b""",
    tie_out="""
      select caccountperiod, count(*), nvl(sum(nabmny), 0)
        from NCSC.IA_MONTHNAB where caccountperiod >= :b group by caccountperiod""",
    group=("period",),
)

COST_OBJECTS = TableSpec(
    name="nc_cm_cost_objects",
    cols=("nc_pk", "code", "name", "material_pk", "material_code", "dr", "nc_ts"),
    select="""
      select o.ccostobjectid, o.vcostobjcode, o.vcostobjname, o.cmaterialid, m.code,
             o.dr, o.ts
        from NCSC.CM_COSTOBJECT o
        left join NCSC.BD_MATERIAL m on m.pk_material = o.cmaterialid""",
    tie_out="select count(*), 0 from NCSC.CM_COSTOBJECT",
    group=(),
    texts={"name": 300},
)

PROD_COSTS = TableSpec(
    name="nc_cm_prod_costs",
    cols=("nc_pk", "period", "cost_object_pk", "material_pk", "material_code",
          "material_name", "cost_center_pk", "fee_type", "qty", "amount", "dr", "nc_ts"),
    select="""
      select p.cprodcostid, p.cperiod, p.ccostobjectid, p.cmaterialid, m.code, m.name,
             p.ccostcenterid, p.ifeetype, p.nnum, p.nmoney, p.dr, p.ts
        from NCSC.CM_PRODCOST p
        left join NCSC.BD_MATERIAL m on m.pk_material = p.cmaterialid
       where p.cperiod >= :p""",
    tie_out="""
      select cperiod, count(*), nvl(sum(nmoney), 0)
        from NCSC.CM_PRODCOST where cperiod >= :p group by cperiod""",
    group=("period",),
    texts={"material_name": 300},
)

# A line under a deleted header is deleted too, whatever its own dr says.
PROD_COST_LINES = TableSpec(
    name="nc_cm_prod_cost_lines",
    cols=("nc_pk", "prod_cost_pk", "period", "element_pk", "element_code", "element_name",
          "material_pk", "material_code", "material_name", "child_type", "qty", "price",
          "amount", "dr", "nc_ts"),
    select="""
      select b.cprodcost_bid, b.cprodcostid, p.cperiod, b.celementid, f.factorcode,
             f.factorname, b.cmaterialid, m.code, m.name, b.ichildtype, b.nnum, b.nprice,
             b.nmoney, case when p.dr = 1 then 1 else b.dr end, b.ts
        from NCSC.CM_PRODCOST_B b
        join NCSC.CM_PRODCOST p on p.cprodcostid = b.cprodcostid
        left join NCSC.RESA_FACTORASOA f on f.pk_factorasoa = b.celementid
        left join NCSC.BD_MATERIAL m on m.pk_material = b.cmaterialid
       where p.cperiod >= :p""",
    tie_out="""
      select p.cperiod, count(*), nvl(sum(b.nmoney), 0)
        from NCSC.CM_PRODCOST_B b join NCSC.CM_PRODCOST p on p.cprodcostid = b.cprodcostid
       where p.cperiod >= :p group by p.cperiod""",
    group=("period",),
    texts={"material_name": 300, "element_name": 200},
)

STUFF_LINES = TableSpec(
    name="nc_cm_stuff_lines",
    cols=("nc_pk", "stuff_pk", "bill_code", "period", "cost_object_pk", "cost_object_code",
          "cost_object_name", "source_type", "business_date", "element_code",
          "element_name", "material_pk", "material_code", "material_name", "qty",
          "price", "amount", "dr", "nc_ts"),
    select="""
      select b.cstuff_bid, b.cstuffid, s.vbillcode, s.cperiod, s.ccostobjectid,
             o.vcostobjcode, o.vcostobjname, s.isourcetype, substr(s.dbusinessdate, 1, 10),
             f.factorcode, f.factorname, b.cmaterialid, m.code, m.name, b.nnum, b.nprice,
             b.nmoney, case when s.dr = 1 then 1 else b.dr end, b.ts
        from NCSC.CM_STUFF_B b
        join NCSC.CM_STUFF s on s.cstuffid = b.cstuffid
        left join NCSC.CM_COSTOBJECT o on o.ccostobjectid = s.ccostobjectid
        left join NCSC.RESA_FACTORASOA f on f.pk_factorasoa = b.celementid
        left join NCSC.BD_MATERIAL m on m.pk_material = b.cmaterialid
       where s.cperiod >= :p""",
    tie_out="""
      select s.cperiod, count(*), nvl(sum(b.nmoney), 0)
        from NCSC.CM_STUFF_B b join NCSC.CM_STUFF s on s.cstuffid = b.cstuffid
       where s.cperiod >= :p group by s.cperiod""",
    group=("period",),
    dates=("business_date",),
    texts={"material_name": 300, "element_name": 200, "cost_object_name": 300},
)

SO_INVOICE_LINES = TableSpec(
    name="nc_so_invoice_lines",
    cols=("nc_pk", "invoice_pk", "invoice_code", "bill_date", "status_flag",
          "customer_code", "customer_name", "orig_currency", "exchange_rate", "row_no",
          "material_pk", "material_code", "material_name", "qty", "amount", "orig_amount",
          "tax", "dr", "nc_ts"),
    select="""
      select b.csaleinvoicebid, h.csaleinvoiceid, h.vbillcode, substr(h.dbilldate, 1, 10),
             h.fstatusflag, c.code, c.name, cu.code, h.nexchangerate, b.crowno,
             b.cmaterialid, m.code, m.name, b.nnum, b.nmny, b.norigmny, b.ntax,
             case when h.dr = 1 then 1 else b.dr end, b.ts
        from NCSC.SO_SALEINVOICE_B b
        join NCSC.SO_SALEINVOICE h on h.csaleinvoiceid = b.csaleinvoiceid
        left join NCSC.BD_CUSTOMER c on c.pk_customer = h.cinvoicecustid
        left join NCSC.BD_CURRTYPE cu on cu.pk_currtype = h.corigcurrencyid
        left join NCSC.BD_MATERIAL m on m.pk_material = b.cmaterialid
       where h.dbilldate >= :d""",
    tie_out="""
      select substr(h.dbilldate, 1, 7), count(*), nvl(sum(b.nmny), 0)
        from NCSC.SO_SALEINVOICE_B b
        join NCSC.SO_SALEINVOICE h on h.csaleinvoiceid = b.csaleinvoiceid
       where h.dbilldate >= :d group by substr(h.dbilldate, 1, 7)""",
    group=("month",),
    dates=("bill_date",),
    texts={"material_name": 300, "customer_name": 300},
)

SPECS: tuple[TableSpec, ...] = (LEDGER, MONTH_BALANCES, COST_OBJECTS, PROD_COSTS,
                                PROD_COST_LINES, STUFF_LINES, SO_INVOICE_LINES)

# Local aggregate matching each spec's NC tie-out, same grain, same order.
_LOCAL_TIE = {
    "nc_ia_ledger": "select period, bill_type, count(*), coalesce(sum(amount),0) "
                    "from nc_ia_ledger group by period, bill_type",
    "nc_ia_month_balances": "select period, count(*), coalesce(sum(amount),0) "
                            "from nc_ia_month_balances group by period",
    "nc_cm_cost_objects": "select count(*), 0 from nc_cm_cost_objects",
    "nc_cm_prod_costs": "select period, count(*), coalesce(sum(amount),0) "
                        "from nc_cm_prod_costs group by period",
    "nc_cm_prod_cost_lines": "select period, count(*), coalesce(sum(amount),0) "
                             "from nc_cm_prod_cost_lines group by period",
    "nc_cm_stuff_lines": "select period, count(*), coalesce(sum(amount),0) "
                         "from nc_cm_stuff_lines group by period",
    "nc_so_invoice_lines": "select to_char(bill_date, 'YYYY-MM'), count(*), "
                           "coalesce(sum(amount),0) from nc_so_invoice_lines group by 1",
}

_BINDS = {"p": PERIOD_FLOOR, "b": BALANCE_FLOOR, "d": DATE_FLOOR}


# ── extract ──────────────────────────────────────────────────────────────────

@dataclass
class CostExtract:
    """Raw NC rows per table (tuples in spec column order) + NC's own
    aggregates for the tie-out. Tests inject a fake one."""
    rows: dict = field(default_factory=dict)       # table -> list[tuple]
    nc_totals: dict = field(default_factory=dict)  # table -> {group_key: [count, amount]}


def _binds_for(sql: str) -> dict:
    return {k: v for k, v in _BINDS.items() if f":{k}" in sql}


def fetch_from_nc() -> CostExtract:
    """Live NC read (oracledb, select-only)."""
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        ex = CostExtract()
        for spec in SPECS:
            cur.execute(spec.select, _binds_for(spec.select))
            ex.rows[spec.name] = cur.fetchall()
            cur.execute(spec.tie_out, _binds_for(spec.tie_out))
            ex.nc_totals[spec.name] = _totals(cur.fetchall(), len(spec.group))
        return ex
    finally:
        con.close()


def _totals(rows, ngroup: int) -> dict:
    out = {}
    for r in rows:
        key = "|".join(str(_s(v) or "") for v in r[:ngroup]) or "*"
        out[key] = [int(r[ngroup]), str(_d(r[ngroup + 1]))]
    return out


# ── transform ────────────────────────────────────────────────────────────────

def _s(v) -> str | None:
    """NC writes '~' for 'no value' and pads CHAR columns."""
    if v is None:
        return None
    v = str(v).strip()
    return None if v in ("", "~") else v


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _date(v) -> date | None:
    s = _s(v)
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        raise NcCostSyncError(f"unparseable NC date {v!r}")


def transform(spec: TableSpec, raw_rows) -> list[tuple]:
    """Normalise one table's rows. Refuses (raises) rather than guesses: a
    row without its NC id, or with a date we cannot read, aborts the run."""
    out = []
    pk_seen = set()
    for raw in raw_rows:
        if len(raw) != len(spec.cols):
            raise NcCostSyncError(f"{spec.name}: expected {len(spec.cols)} columns, got {len(raw)}")
        row = dict(zip(spec.cols, raw))
        for col, val in row.items():
            if isinstance(val, str) or val is None:
                row[col] = _s(val)
        for col in spec.dates:
            row[col] = _date(row[col])
        for col, n in spec.texts.items():
            if row.get(col) and len(row[col]) > n:
                row[col] = row[col][:n]
        if not row["nc_pk"]:
            raise NcCostSyncError(f"{spec.name}: row without an NC id")
        if row["nc_pk"] in pk_seen:
            raise NcCostSyncError(f"{spec.name}: NC id {row['nc_pk']} read twice")
        pk_seen.add(row["nc_pk"])
        row["dr"] = int(row["dr"] or 0)
        out.append(tuple(row[c] for c in spec.cols))
    return out


# ── write ────────────────────────────────────────────────────────────────────

def _pg_dsn() -> str:
    u = make_url(settings.database_url)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


def write_snapshot(cur, ex: CostExtract) -> dict:
    """Replace every table's contents with this extract. Caller owns the
    transaction — a failure anywhere leaves the previous snapshot intact."""
    now = datetime.now(timezone.utc)
    counts = {}
    for spec in SPECS:
        rows = transform(spec, ex.rows.get(spec.name, []))
        cur.execute(f"delete from {spec.name}")
        cols = ", ".join((*spec.cols, "id", "synced_at"))
        for i in range(0, len(rows), _CHUNK):
            execute_values(
                cur, f"insert into {spec.name} ({cols}) values %s",
                [(*r, uuid.uuid4(), now) for r in rows[i:i + _CHUNK]])
        counts[spec.name] = len(rows)
    return counts


def compute_tie_out(cur, nc_totals: dict) -> tuple[bool, dict]:
    """Every table, every group: NC's count and amount against ours."""
    diffs = {}
    local_all = {}
    for spec in SPECS:
        cur.execute(_LOCAL_TIE[spec.name])
        local = _totals(cur.fetchall(), len(spec.group))
        local_all[spec.name] = local
        nc = nc_totals.get(spec.name, {})
        for key in sorted(set(nc) | set(local)):
            a, b = nc.get(key), local.get(key)
            if a is None or b is None or a[0] != b[0] or Decimal(a[1]) != Decimal(b[1]):
                diffs.setdefault(spec.name, {})[key] = {"nc": a, "local": b}
    return (not diffs), {"nc": nc_totals, "local": local_all, "diffs": diffs}


# ── run orchestration ────────────────────────────────────────────────────────

def _mark_terminal(dsn, run_id, **fields):
    con = psycopg2.connect(dsn)
    con.autocommit = True
    try:
        sets = ", ".join(f"{k} = %s" for k in fields)
        con.cursor().execute(
            f"update nc_cost_sync_runs set {sets}, finished_at = now(), updated_at = now() "
            f"where id = %s", (*fields.values(), run_id))
    finally:
        con.close()


def start_run(started_by, *, fetch=fetch_from_nc, pg_dsn: str | None = None,
              run_worker: bool = False) -> uuid.UUID:
    """Reserve a run (one at a time) and optionally do it inline.

    Same contract as nc_ap_sync.start_run: the scheduler passes
    run_worker=True from a thread; the API reserves here and dispatches
    `_run_worker` on its executor.
    """
    if not nc_configured():
        raise NcCostSyncError("NC connection is not configured")
    dsn = pg_dsn or _pg_dsn()
    with _start_lock:
        con = psycopg2.connect(dsn)
        con.autocommit = True
        try:
            cur = con.cursor()
            cur.execute("update nc_cost_sync_runs set status = 'failed', "
                        "error = 'abandoned (worker gone)', finished_at = now(), "
                        "updated_at = now() "
                        "where status = 'running' and started_at < now() - interval '30 minutes'")
            cur.execute("select 1 from nc_cost_sync_runs where status = 'running' limit 1")
            if cur.fetchone():
                raise SyncAlreadyRunning("a cost sync is already running")
            run_id = uuid.uuid4()
            cur.execute(
                "insert into nc_cost_sync_runs (id, status, started_by, started_at) "
                "values (%s, 'running', %s, now())", (run_id, started_by))
        finally:
            con.close()
    if run_worker:
        _run_worker(run_id, fetch, dsn)
    return run_id


def _run_worker(run_id, fetch, dsn: str) -> None:
    try:
        ex = fetch()
        con = psycopg2.connect(dsn)
        con.autocommit = False
        try:
            cur = con.cursor()
            counts = write_snapshot(cur, ex)
            ok, tie = compute_tie_out(cur, ex.nc_totals)
            con.commit()
        finally:
            con.close()
        _mark_terminal(dsn, run_id, status="success", counts=Json(counts),
                       tie_out_ok=ok, tie_out=Json(tie))
        if not ok:
            logger.warning("NC cost sync %s completed but does NOT tie out: %s",
                           run_id, tie["diffs"])
    except Exception as exc:                                  # noqa: BLE001
        logger.exception("NC cost sync %s failed", run_id)
        try:
            _mark_terminal(dsn, run_id, status="failed", error=str(exc)[:2000])
        except Exception:                                     # noqa: BLE001
            logger.exception("could not even mark NC cost sync %s failed", run_id)
