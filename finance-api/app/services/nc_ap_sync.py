"""NC65 accounts-payable sync (UI-triggered / scheduled, full or incremental).

Same shape as services/nc_sync.py (the voucher sync): oracledb reads NC,
psycopg2 writes our Postgres, the whole run is synchronous on a worker thread.

Three things about this domain that drove the design, all verified against the
NC production database on 2026-09-21 before a line of this was written:

* **The watermark can be NC's `TS`.** It is filled on 100% of AP rows and it
  *advances on settlement* — a July bill paid in September carries a September
  TS. That is the whole reason an incremental run can see a bill become paid,
  which is the point of mirroring payables at all.
* **A line can move without its header.** 18 of 7,227 lines carried a TS newer
  than their bill's, so the changed-set is the UNION of both sides. Selecting
  only on the header would silently miss those.
* **Incremental cannot reap deletions.** A bill deleted in NC simply stops
  existing; nothing advances a timestamp. Only a full reload removes it — which
  is exactly why every run tie-outs its counts against NC (§6 of the spec) and
  says so out loud instead of looking green.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import Json, execute_values, register_uuid
from sqlalchemy.engine.url import make_url

from app.core.config import settings

register_uuid()
logger = logging.getLogger(__name__)

FULL_CONFIRM = "FULL RELOAD"
_start_lock = threading.Lock()
STALE_AFTER = timedelta(minutes=30)
_CHUNK = 5000
# Oracle's hard limit on a literal IN-list.
_IN_CHUNK = 900

# NC stores bill dates as CHAR in the NC server's own wall clock, and the bill
# NUMBER is generated from the same clock (D1|2026 0724|…  ↔  '2026-07-24 …'),
# so the date part is internally consistent. Pinning that wall clock to the
# company's zone keeps the date part intact; reading it as UTC would drag
# early-morning bills back a day and quietly move them between periods.
NC_TZ = ZoneInfo("America/Toronto")


class NcApSyncError(ValueError):
    """An NC value we refuse to guess about. Aborts the run."""


class SyncAlreadyRunning(Exception):
    pass


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


# ── extract ──────────────────────────────────────────────────────────────────

@dataclass
class NcApExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    bills: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    suppliers: dict = field(default_factory=dict)   # pk -> (code, name)
    depts: dict = field(default_factory=dict)       # pk -> code
    currencies: dict = field(default_factory=dict)  # pk -> code (CAD/USD/CNY)
    max_ts: str | None = None
    # Whole-population totals read straight from NC in the same connection,
    # for the tie-out. Not a per-run subset — the mirror is compared as a whole.
    nc_totals: dict = field(default_factory=dict)


_BILL_COLS = """
    b.pk_payablebill, b.billno, b.pk_tradetype, b.pk_billtype, b.src_syscode,
    b.billclass, b.billyear, b.billperiod, b.billdate, b.approvedate, b.effectdate,
    b.billstatus, b.approvestatus, b.pk_currtype, b.money, b.local_money,
    b.billmaker, b.ts
"""

_LINE_COLS = """
    i.pk_payableitem, i.pk_payablebill, i.rowno,
    i.money_cr, i.money_bal, i.notax_cr, i.local_money_cr,
    i.invoiceno, i.purchaseorder, i.settleno, i.contractno,
    i.subjcode, i.costcenter, i.pk_deptid, i.project, i.material, i.supplier,
    i.pk_payterm, i.taxrate, i.scomment, i.src_billtype, i.top_billtype, i.ts
"""


def fetch_from_nc(watermark: str | None) -> NcApExtract:
    """Live NC read (oracledb, read-only).

    `watermark` is an NC TS string; None means full reload. The changed-set is
    the union of bills whose own TS moved and bills whose *lines* moved.
    """
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        ex = NcApExtract()

        cur.execute("select pk_supplier, code, name from NCSC.BD_SUPPLIER")
        ex.suppliers = {pk: (code, name) for pk, code, name in cur.fetchall()}
        cur.execute("select pk_dept, code from NCSC.ORG_DEPT")
        ex.depts = dict(cur.fetchall())
        # pk_currtype on the bill is a 20-char pk, not a currency code — store
        # the code, which is what every reader of this mirror actually wants.
        cur.execute("select pk_currtype, code from NCSC.BD_CURRTYPE")
        ex.currencies = dict(cur.fetchall())

        if watermark:
            cur.execute(
                """select pk_payablebill from NCSC.AP_PAYABLEBILL where ts >= :wm
                   union
                   select pk_payablebill from NCSC.AP_PAYABLEITEM where ts >= :wm""",
                wm=watermark,
            )
            pks = [r[0] for r in cur.fetchall()]
            for chunk in _chunks(pks, _IN_CHUNK):
                binds = {f"p{i}": v for i, v in enumerate(chunk)}
                names = ",".join(f":p{i}" for i in range(len(chunk)))
                cur.execute(f"select {_BILL_COLS} from NCSC.AP_PAYABLEBILL b "
                            f"where b.pk_payablebill in ({names})", binds)
                ex.bills.extend(cur.fetchall())
                cur.execute(f"select {_LINE_COLS} from NCSC.AP_PAYABLEITEM i "
                            f"where i.pk_payablebill in ({names})", binds)
                ex.lines.extend(cur.fetchall())
        else:
            cur.execute(f"select {_BILL_COLS} from NCSC.AP_PAYABLEBILL b")
            ex.bills = cur.fetchall()
            cur.execute(f"select {_LINE_COLS} from NCSC.AP_PAYABLEITEM i")
            ex.lines = cur.fetchall()

        ex.max_ts = _max_ts(ex.bills, ex.lines)
        ex.nc_totals = _read_nc_totals(cur)
        return ex
    finally:
        con.close()


def _read_nc_totals(cur) -> dict:
    """Whole-population counts and money totals, for the tie-out."""
    cur.execute("select count(*) from NCSC.AP_PAYABLEBILL")
    bills = cur.fetchone()[0]
    cur.execute("select count(*), nvl(sum(money_cr),0), nvl(sum(money_bal),0) "
                "from NCSC.AP_PAYABLEITEM")
    lines, money_cr, money_bal = cur.fetchone()
    return {"bills": int(bills), "lines": int(lines),
            "money_cr": str(_d(money_cr)), "money_bal": str(_d(money_bal))}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _max_ts(bills, lines) -> str | None:
    """Highest TS seen on either side — the next run's floor."""
    vals = [b[-1] for b in bills if b[-1]] + [ln[-1] for ln in lines if ln[-1]]
    vals = [v for v in vals if isinstance(v, str) and v != "~"]
    return max(vals) if vals else None


# ── transform ────────────────────────────────────────────────────────────────

def _s(v) -> str | None:
    """NC writes '~' for 'no value' in CHAR columns, and pads the rest."""
    if v is None:
        return None
    v = str(v).strip()
    return None if v in ("", "~") else v


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _dec(v) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def _dt(v) -> datetime | None:
    s = _s(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=NC_TZ)
        except ValueError:
            continue
    return None


def normalise_invoice_no(raw: str | None) -> str | None:
    """The search form of an invoice number.

    Kept ALONGSIDE the raw value, never instead of it. NC's voucher narrations
    run the number straight into the preceding word
    ("Received  Invoice0000396CA01C100712219"), so every downstream match is a
    contains-match on this column, never an equality test on the raw one.
    """
    s = _s(raw)
    if not s:
        return None
    return "".join(ch for ch in s.upper() if not ch.isspace())


def transform(ex: NcApExtract) -> tuple[list[dict], list[dict]]:
    """NC tuples -> row dicts for our two mirror tables. Pure; no I/O."""
    bills: list[dict] = []
    by_pk: dict[str, dict] = {}
    for r in ex.bills:
        (pk, billno, tradetype, billtype, src_syscode, billclass, billyear, billperiod,
         billdate, approvedate, effectdate, billstatus, approvestatus, currtype,
         money, local_money, billmaker, ts) = r
        sup = None  # header carries no supplier; it lives on the lines
        row = {
            "nc_pk": _s(pk), "bill_no": _s(billno) or "", "trade_type": _s(tradetype),
            "bill_type": _s(billtype),
            "src_syscode": int(src_syscode) if src_syscode is not None else None,
            "bill_class": _s(billclass), "bill_year": _s(billyear),
            "bill_period": _s(billperiod), "bill_date": _dt(billdate),
            "approve_date": _dt(approvedate), "effect_date": _dt(effectdate),
            "bill_status": int(billstatus) if billstatus is not None else None,
            "approve_status": int(approvestatus) if approvestatus is not None else None,
            "supplier_pk": sup, "supplier_code": None, "supplier_name": None,
            "currency": ex.currencies.get(_s(currtype)) or None,
            "money": _dec(money), "local_money": _dec(local_money),
            "bill_maker": _s(billmaker), "nc_ts": _s(ts) or "",
        }
        if not row["nc_pk"] or not row["nc_ts"]:
            raise NcApSyncError(f"AP bill {billno!r} has no pk or no TS — refusing to guess")
        bills.append(row)
        by_pk[row["nc_pk"]] = row

    lines: list[dict] = []
    for r in ex.lines:
        (pk, bill_pk, rowno, money_cr, money_bal, notax_cr, local_money_cr,
         invoiceno, po, settleno, contractno, subjcode, costcenter, deptid,
         project, material, supplier, payterm, taxrate, scomment,
         src_billtype, top_billtype, ts) = r
        bill_pk = _s(bill_pk)
        parent = by_pk.get(bill_pk)
        if parent is None:
            # Its header was not in this batch — the union query guarantees it
            # is, so this means the two reads disagreed mid-run. Drop it rather
            # than orphan it, and let the tie-out report the gap (never silent).
            continue
        sup_pk = _s(supplier)
        code, name = ex.suppliers.get(sup_pk, (None, None)) if sup_pk else (None, None)
        raw_inv = _s(invoiceno)
        lines.append({
            "nc_pk": _s(pk), "bill_nc_pk": bill_pk,
            "row_no": int(rowno) if rowno is not None else None,
            "bill_no": parent["bill_no"], "bill_date": parent["bill_date"],
            "bill_year": parent["bill_year"], "bill_period": parent["bill_period"],
            "money_cr": _dec(money_cr), "money_bal": _dec(money_bal),
            "notax_cr": _dec(notax_cr), "local_money_cr": _dec(local_money_cr),
            "invoice_no": raw_inv, "invoice_no_norm": normalise_invoice_no(raw_inv),
            "purchase_order": _s(po), "settle_no": _s(settleno),
            "contract_no": _s(contractno), "subject_code": _s(subjcode),
            "cost_center": _s(costcenter), "dept_pk": _s(deptid),
            "dept_code": ex.depts.get(_s(deptid)) if _s(deptid) else None,
            "project": _s(project), "material_pk": _s(material),
            "supplier_pk": sup_pk, "supplier_code": _s(code), "supplier_name": _s(name),
            "pay_term_pk": _s(payterm), "tax_rate": _dec(taxrate),
            "scomment": (_s(scomment) or None), "src_bill_type": _s(src_billtype),
            "top_bill_type": _s(top_billtype), "nc_ts": _s(ts) or parent["nc_ts"],
        })

    # A bill's supplier is whatever its lines agree on; NC keeps it per line.
    sup_of: dict[str, tuple] = {}
    for ln in lines:
        if ln["supplier_pk"]:
            sup_of.setdefault(ln["bill_nc_pk"],
                              (ln["supplier_pk"], ln["supplier_code"], ln["supplier_name"]))
    for b in bills:
        if b["nc_pk"] in sup_of:
            b["supplier_pk"], b["supplier_code"], b["supplier_name"] = sup_of[b["nc_pk"]]

    return bills, lines


# ── write ────────────────────────────────────────────────────────────────────

def _pg_dsn() -> str:
    u = make_url(settings.database_url)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


_BILL_INSERT = """
insert into nc_ap_bills (
  id, nc_pk, bill_no, trade_type, bill_type, src_syscode, bill_class,
  bill_year, bill_period, bill_date, approve_date, effect_date,
  bill_status, approve_status, supplier_pk, supplier_code, supplier_name,
  currency, money, local_money, bill_maker, nc_ts, synced_at, created_at, updated_at)
values %s
on conflict (nc_pk) do update set
  bill_no = excluded.bill_no, trade_type = excluded.trade_type,
  bill_type = excluded.bill_type, src_syscode = excluded.src_syscode,
  bill_class = excluded.bill_class, bill_year = excluded.bill_year,
  bill_period = excluded.bill_period, bill_date = excluded.bill_date,
  approve_date = excluded.approve_date, effect_date = excluded.effect_date,
  bill_status = excluded.bill_status, approve_status = excluded.approve_status,
  supplier_pk = excluded.supplier_pk, supplier_code = excluded.supplier_code,
  supplier_name = excluded.supplier_name, currency = excluded.currency,
  money = excluded.money, local_money = excluded.local_money,
  bill_maker = excluded.bill_maker, nc_ts = excluded.nc_ts,
  synced_at = excluded.synced_at, updated_at = now()
"""

_LINE_INSERT = """
insert into nc_ap_bill_lines (
  id, bill_id, nc_pk, row_no, bill_no, bill_date, bill_year, bill_period,
  money_cr, money_bal, notax_cr, local_money_cr,
  invoice_no, invoice_no_norm, purchase_order, settle_no, contract_no,
  subject_code, cost_center, dept_pk, dept_code, project, material_pk,
  supplier_pk, supplier_code, supplier_name, pay_term_pk, tax_rate,
  scomment, src_bill_type, top_bill_type, nc_ts, synced_at)
values %s
on conflict (nc_pk) do update set
  bill_id = excluded.bill_id, row_no = excluded.row_no,
  bill_no = excluded.bill_no, bill_date = excluded.bill_date,
  bill_year = excluded.bill_year, bill_period = excluded.bill_period,
  money_cr = excluded.money_cr, money_bal = excluded.money_bal,
  notax_cr = excluded.notax_cr, local_money_cr = excluded.local_money_cr,
  invoice_no = excluded.invoice_no, invoice_no_norm = excluded.invoice_no_norm,
  purchase_order = excluded.purchase_order, settle_no = excluded.settle_no,
  contract_no = excluded.contract_no, subject_code = excluded.subject_code,
  cost_center = excluded.cost_center, dept_pk = excluded.dept_pk,
  dept_code = excluded.dept_code, project = excluded.project,
  material_pk = excluded.material_pk, supplier_pk = excluded.supplier_pk,
  supplier_code = excluded.supplier_code, supplier_name = excluded.supplier_name,
  pay_term_pk = excluded.pay_term_pk, tax_rate = excluded.tax_rate,
  scomment = excluded.scomment, src_bill_type = excluded.src_bill_type,
  top_bill_type = excluded.top_bill_type, nc_ts = excluded.nc_ts,
  synced_at = excluded.synced_at
"""


def _write(cur, bills: list[dict], lines: list[dict], *, full: bool) -> dict:
    """Upsert the batch. Returns counters. Caller owns the transaction."""
    now = datetime.now(timezone.utc)
    counts = {"bills_upserted": 0, "lines_upserted": 0, "lines_deleted": 0,
              "bills_deleted": 0}

    for chunk in _chunks(bills, _CHUNK):
        execute_values(cur, _BILL_INSERT, [
            (uuid.uuid4(), b["nc_pk"], b["bill_no"], b["trade_type"], b["bill_type"],
             b["src_syscode"], b["bill_class"], b["bill_year"], b["bill_period"],
             b["bill_date"], b["approve_date"], b["effect_date"], b["bill_status"],
             b["approve_status"], b["supplier_pk"], b["supplier_code"], b["supplier_name"],
             b["currency"], b["money"], b["local_money"], b["bill_maker"], b["nc_ts"],
             now, now, now)
            for b in chunk])
        counts["bills_upserted"] += len(chunk)

    if not bills:
        return counts

    # nc_pk -> our id, for the lines we are about to write.
    cur.execute("select nc_pk, id from nc_ap_bills where nc_pk = any(%s)",
                ([b["nc_pk"] for b in bills],))
    id_of = dict(cur.fetchall())

    # Lines that NC no longer has under these bills. Targeted, so the count is
    # a real "removed in NC" number rather than churn from a delete-and-reinsert.
    cur.execute(
        "delete from nc_ap_bill_lines where bill_id = any(%s) and nc_pk <> all(%s)",
        ([id_of[b["nc_pk"]] for b in bills if b["nc_pk"] in id_of],
         [ln["nc_pk"] for ln in lines] or [""]))
    counts["lines_deleted"] = cur.rowcount

    writable = [ln for ln in lines if ln["bill_nc_pk"] in id_of]
    for chunk in _chunks(writable, _CHUNK):
        execute_values(cur, _LINE_INSERT, [
            (uuid.uuid4(), id_of[ln["bill_nc_pk"]], ln["nc_pk"], ln["row_no"],
             ln["bill_no"], ln["bill_date"], ln["bill_year"], ln["bill_period"],
             ln["money_cr"], ln["money_bal"], ln["notax_cr"], ln["local_money_cr"],
             ln["invoice_no"], ln["invoice_no_norm"], ln["purchase_order"],
             ln["settle_no"], ln["contract_no"], ln["subject_code"], ln["cost_center"],
             ln["dept_pk"], ln["dept_code"], ln["project"], ln["material_pk"],
             ln["supplier_pk"], ln["supplier_code"], ln["supplier_name"],
             ln["pay_term_pk"], ln["tax_rate"], ln["scomment"], ln["src_bill_type"],
             ln["top_bill_type"], ln["nc_ts"], now)
            for ln in chunk])
        counts["lines_upserted"] += len(chunk)

    if full:
        # Only a full reload can reap bills deleted in NC — nothing advances a
        # timestamp when a row disappears, so incremental is blind to it.
        cur.execute("delete from nc_ap_bills where nc_pk <> all(%s)",
                    ([b["nc_pk"] for b in bills],))
        counts["bills_deleted"] = cur.rowcount

    return counts


def compute_tie_out(cur, nc_totals: dict) -> tuple[bool, dict]:
    """Compare the whole mirror against NC's whole population.

    Completeness is the one thing this system cannot be wrong about quietly
    (spec §6), so the answer is recorded on every run — including runs that
    succeeded. `tie_out_ok=False` with `status='success'` is a real and
    meaningful state: the job ran, the data does not agree.
    """
    cur.execute("select count(*) from nc_ap_bills")
    bills = cur.fetchone()[0]
    cur.execute("select count(*), coalesce(sum(money_cr),0), coalesce(sum(money_bal),0) "
                "from nc_ap_bill_lines")
    lines, money_cr, money_bal = cur.fetchone()
    local = {"bills": int(bills), "lines": int(lines),
             "money_cr": str(_d(money_cr)), "money_bal": str(_d(money_bal))}
    diffs = {
        k: {"nc": nc_totals.get(k), "local": local.get(k)}
        for k in ("bills", "lines", "money_cr", "money_bal")
        if _cmp(nc_totals.get(k), local.get(k))
    }
    return (not diffs), {"nc": nc_totals, "local": local, "diffs": diffs}


def _cmp(a, b) -> bool:
    """True when the two totals differ. Money is compared numerically."""
    if a is None or b is None:
        return a is not b
    try:
        return Decimal(str(a)) != Decimal(str(b))
    except Exception:
        return str(a) != str(b)


# ── run orchestration ────────────────────────────────────────────────────────

def _mark(dsn, run_id, **fields):
    """Small autocommit update on the run row (progress counters)."""
    con = psycopg2.connect(dsn)
    con.autocommit = True
    try:
        sets = ", ".join(f"{k} = %s" for k in fields)
        con.cursor().execute(
            f"update nc_ap_sync_runs set {sets}, updated_at = now() where id = %s",
            (*fields.values(), run_id))
    finally:
        con.close()


def _mark_terminal(dsn, run_id, **fields):
    con = psycopg2.connect(dsn)
    con.autocommit = True
    try:
        sets = ", ".join(f"{k} = %s" for k in fields)
        con.cursor().execute(
            f"update nc_ap_sync_runs set {sets}, finished_at = now(), updated_at = now() "
            f"where id = %s", (*fields.values(), run_id))
    finally:
        con.close()


def _last_watermark(cur) -> str | None:
    """Floor for the next incremental run: the high-water mark of the last run
    that actually succeeded. A failed run must not advance it."""
    cur.execute("select watermark_to from nc_ap_sync_runs "
                "where status = 'success' and watermark_to is not null "
                "order by started_at desc limit 1")
    row = cur.fetchone()
    return row[0] if row else None


def start_run(mode: str, started_by, *, fetch=fetch_from_nc, pg_dsn: str | None = None,
              confirm: str | None = None, run_worker: bool = False) -> uuid.UUID:
    """Gate the domain, insert the `running` row, return its id.

    Same contract as the voucher sync's start_run, deliberately:
    `run_worker=True` does the NC read inline (the scheduler already calls this
    from `asyncio.to_thread`); `run_worker=False` only reserves the run and the
    CALLER dispatches `_run_worker` — the API does it on the event loop's
    executor. A row left `running` because nobody dispatched is swept by the
    staleness check above on the next attempt.
    """
    if mode not in ("full", "incremental"):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "full" and confirm != FULL_CONFIRM:
        raise ValueError("full reload needs an explicit confirmation")
    if not nc_configured():
        raise NcApSyncError("NC connection is not configured")

    dsn = pg_dsn or _pg_dsn()
    with _start_lock:
        con = psycopg2.connect(dsn)
        con.autocommit = True
        try:
            cur = con.cursor()
            # A crashed worker leaves a 'running' row forever; time it out rather
            # than wedging the domain until someone notices.
            cur.execute("update nc_ap_sync_runs set status = 'failed', "
                        "error = 'abandoned (worker gone)', finished_at = now(), "
                        "updated_at = now() "
                        "where status = 'running' and started_at < now() - interval '30 minutes'")
            cur.execute("select 1 from nc_ap_sync_runs where status = 'running' limit 1")
            if cur.fetchone():
                raise SyncAlreadyRunning("an AP sync is already running")
            run_id = uuid.uuid4()
            cur.execute(
                "insert into nc_ap_sync_runs (id, mode, status, started_by, started_at) "
                "values (%s, %s, 'running', %s, now())", (run_id, mode, started_by))
        finally:
            con.close()

    if run_worker:
        _run_worker(run_id, mode, fetch, dsn)
    return run_id


def _run_worker(run_id, mode: str, fetch, dsn: str) -> None:
    try:
        con = psycopg2.connect(dsn)
        con.autocommit = False
        try:
            cur = con.cursor()
            watermark = None if mode == "full" else _last_watermark(cur)
            con.rollback()

            ex = fetch(watermark)
            bills, lines = transform(ex)
            _mark(dsn, run_id, watermark_from=watermark, bills_seen=len(bills))

            counts = _write(cur, bills, lines, full=(mode == "full"))
            ok, tie = compute_tie_out(cur, ex.nc_totals)
            con.commit()

            # Advance the watermark only to what we actually stored. If NC gave
            # us nothing this round, keep the old floor — never jump forward
            # past rows we never read.
            watermark_to = ex.max_ts or watermark
            _mark_terminal(dsn, run_id, status="success",
                           watermark_to=watermark_to,
                           bills_upserted=counts["bills_upserted"],
                           lines_upserted=counts["lines_upserted"],
                           lines_deleted=counts["lines_deleted"],
                           tie_out_ok=ok, tie_out=Json(tie))
            if not ok:
                logger.warning("NC AP sync %s completed but does NOT tie out: %s",
                               run_id, tie["diffs"])
        finally:
            con.close()
    except Exception as exc:                                  # noqa: BLE001
        logger.exception("NC AP sync %s failed", run_id)
        try:
            _mark_terminal(dsn, run_id, status="failed", error=str(exc)[:2000])
        except Exception:                                     # noqa: BLE001
            logger.exception("could not even mark NC AP sync %s failed", run_id)
