"""NC65 voucher sync service (UI-triggered full / incremental).

Extraction + transform ported verbatim from scripts/nc_migration/voucher_import.py
(reconciled 0-diff against NC per-account nets, 2026-07-11..13). The whole run is
synchronous (oracledb reads NC, psycopg2 writes our Postgres) and is executed on a
worker thread by the API layer. The CLI script stays for cut-over/emergency use.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings

PK_BOOK = "1001A1100000003CGCBX"            # Canada Royal Milk accounting book
FULL_CONFIRM = "FULL RELOAD"

# NC 辅助核算 global type pks (first 20 chars of a GL_FREEVALUE.typevalueN):
AUX_DEPT = "0001Z0100000000005CS"           # 部门 -> ORG_DEPT
AUX_COSTCENTER = "1003Z31000000000SP6J"     # 成本中心 -> RESA_COSTCENTER
AUX_IOITEM = "0001Z0100000000005CZ"         # 收支项目 -> BD_INOUTBUSICLASS

# Curated NC -> EPMS cost-center map (user, 2026-07-12): code decides when present,
# else classify by department.
CC_BY_CODE = {
    "E01": "MOH-0106-E01", "E02": "MOH-0106-E01", "E03": "MOH-0106-E01",
    "E04": "MOH-0106-E01", "E05": "MOH-0106-E01", "E06": "MOH-0106-E01",
    "E07": "MOH-0106-E01", "ENG": "MOH-0106-E01",
    "P01": "MOH-0104-P01", "P02": "MOH-0104-P02", "P03": "MOH-0104-P03",
    "PD": "MOH-0104-P01",
    "Q01": "MOH-0105-LAB", "Q02": "MOH-0105-LAB", "QA": "GA-0105",
    "S02": "MOH-0107-S02", "S03": "SELL-0107-S03", "SC": "GA-0107",
    "H01": "MOH-0101", "HR": "GA-0101",
}
CC_BY_DEPT = {
    "0100": "GA-0100", "0101": "GA-0101", "0103": "GA-0103",
    "0105": "GA-0105", "0107": "GA-0107", "0109": "RD-0109",
    "0110": "SELL-0110", "0111": "SELL-0111", "0112": "SELL-0112", "0113": "SELL-0113",
    # 2026-07-17: these four were missing, costing exactly the 956 lines that
    # unmapped_cc_count had been reporting all along (404+274+163+115).
    # 0106/0104 were an outright oversight — CC_BY_CODE already routes
    # E01-E07/ENG -> MOH-0106-E01 and P01-P03/PD -> MOH-0104-*, so only the
    # dept-only path lost them.
    "0106": "MOH-0106-E01", "0104": "MOH-0104-P01",
    # 0102 (named "Purchasing(NOT USE)") and 0108 have no obvious EPMS
    # counterpart. These two targets are the USER'S call (2026-07-17), not
    # inferred — do not "improve" them from the code's side.
    "0102": "GA-0107", "0108": "RD-0109",
}


class NcSyncError(ValueError):
    """An NC value we refuse to guess about. Aborts the run."""


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


@dataclass
class NcExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    ccy: dict           # pk_currtype -> currency code
    aux: dict           # freevalueid -> (dept_code, cc_code, io_code, sup_code, cust_code)
    vouchers: list      # (pk, year, period, num, explanation, prepareddate, creationtime,
                        #  tallydate)
    details: list       # (pk_voucher, detailindex, accountcode, dr, cr, ldr, lcr,
                        #  pk_currtype, excrate1, explanation, assid)
    max_creationtime: str | None
    tallied: set        # EVERY tallied pk in the book (NOT watermark-limited) —
                        # drives the status backfill, see _sync_statuses


_AUX_NAME_SLOTS = {
    "department": "部门", "cost_center": "成本中心", "income_expense_item": "收支项目",
    "supplier": "供应商", "customer": "客户",
}
_AUX_CONSTANTS = {"department": AUX_DEPT, "cost_center": AUX_COSTCENTER,
                  "income_expense_item": AUX_IOITEM}


def resolve_aux_type_pks(items) -> dict:
    """[(pk_accassitem, name)] -> {slot: set of pks}. Collects EVERY item whose
    name contains the slot needle but not 分类 (classification types never
    appear as GL_FREEVALUE prefixes; live NC carries e.g. both 客户基本分类 and
    客户档案 — only the latter shows up in vouchers). Set-based because
    multi-org NC catalogs can hold several 档案 rows per slot.
    Validates the three frozen constants are AMONG their resolved sets."""
    out: dict = {}
    for slot, needle in _AUX_NAME_SLOTS.items():
        out[slot] = {pk for pk, name in items
                     if needle in (name or "") and "分类" not in (name or "")}
    for slot, const in _AUX_CONSTANTS.items():
        if out.get(slot) and const not in out[slot]:
            raise RuntimeError(
                f"aux type pk mismatch for {slot}: constant {const!r} not among "
                f"resolved {sorted(out[slot])!r} — typevalue-prefix assumption broke")
    return out


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _net_side(dr: Decimal, cr: Decimal) -> tuple[Decimal, Decimal]:
    n = dr - cr
    return (n, Decimal("0")) if n >= 0 else (Decimal("0"), -n)


def _resolve_dims(assid, aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust):
    """-> (cc_id, dept_id, io_code, ba_id, partner_id, partner_name, had_cc_hint).
    Supplier wins over customer when both appear (AP accounts carry suppliers,
    AR customers; a clash is NC data noise). Missing master row -> partner_id
    None with the NC code kept as partner_name text."""
    d, c, io, sup, cust = aux.get(assid, ("", "", "", "", ""))
    epms = CC_BY_CODE.get(c) if c else CC_BY_DEPT.get(d)
    partner_id = partner_name = None
    code = sup or cust
    if code:
        hit = (uni_sup.get(sup) if sup else None) or (uni_cust.get(cust) if cust else None)
        if hit:
            partner_id, partner_name = hit
        else:
            partner_name = code
    return (uni_cc.get(epms) if epms else None,
            uni_dept.get(d) if d else None,
            io or None,
            uni_ba.get(io) if io else None,
            partner_id, partner_name,
            bool(c or d))


def transform(extract: NcExtract, uni_cc: dict, uni_dept: dict, uni_ba: dict,
              uni_sup: dict, uni_cust: dict,
              skip_pks: set) -> tuple[list, list, list, int]:
    """NC rows -> (voucher dicts, line tuples, dim tuples, unmapped_cc count).
    Skips vouchers whose pk is in skip_pks (incremental pk-dedup)."""
    pk2id, vouchers = {}, []
    for pk, year, period, num, expl, pdate, _ctime, tallydate in extract.vouchers:
        if pk in skip_pks:
            continue
        jid = uuid.uuid4()
        pk2id[pk] = jid
        vdate = (pdate[:10] if pdate and len(pdate) >= 10 else f"{year}-{period}-01")
        num_i = int(num) if num is not None else 0
        vouchers.append({
            # JV- prefix + 4-padded, same shape as go-forward numbers (user 2026-07-13;
            # next_jv_number is max-based so the shared namespace can't collide).
            "id": jid, "jv_number": f"JV-{year}{period}-{num_i:04d}",
            "period": f"{year}-{period}", "vdate": vdate,
            "summary": (expl or "")[:255], "nc_pk": pk,
            # NC's TALLYDATE empty = not yet posted to NC's ledger. Mirror that:
            # the GL and Account Balance both read status == POSTED only, so an
            # un-tallied voucher must not colour reports (spec §14.4).
            "status": "posted" if (tallydate and tallydate != "~") else "draft",
        })

    lines, dims, unmapped = [], [], 0
    for pk, idx, acct, dr, cr, ldr, lcr, curr, rate, expl, assid in extract.details:
        jid = pk2id.get(pk)
        if jid is None:
            continue
        odr, ocr = _net_side(_d(dr), _d(cr))
        ldr_, lcr_ = _net_side(_d(ldr), _d(lcr))
        cc_id, dept_id, io_code, ba_id, partner_id, partner_name, had_hint = _resolve_dims(
            assid, extract.aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust)
        if had_hint and cc_id is None:
            unmapped += 1
        ccy_code = extract.ccy.get(curr)
        if ccy_code is None:
            raise NcSyncError(f"voucher line {pk}/{idx}: currency pk {curr!r} not in "
                              f"BD_CURRTYPE — refusing to default it to CAD")
        lid = uuid.uuid4()
        lines.append((
            lid, jid, int(idx or 0), (acct or "").strip() or None,
            (expl or "")[:255], odr, ocr, ldr_, lcr_,
            ccy_code, _d(rate) if rate else Decimal("1"),
            cc_id, dept_id, ba_id, partner_id, partner_name))
        if io_code:
            dims.append((uuid.uuid4(), lid, "income_expense_item", ba_id, io_code))
    return vouchers, lines, dims, unmapped


def fetch_from_nc(watermark: str | None) -> NcExtract:
    """Live NC read (oracledb, read-only). watermark: only vouchers with
    creationtime >= watermark (pk-dedup upstream makes >= safe)."""
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port,
                           service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("select pk_currtype, code from NCSC.BD_CURRTYPE")
        ccy = {pk: code for pk, code in cur.fetchall()}

        cur.execute("select pk_accassitem, name from NCSC.BD_ACCASSITEM")
        type_pks = resolve_aux_type_pks(list(cur.fetchall()))
        aux_sup_pks, aux_cust_pks = type_pks.get("supplier") or set(), type_pks.get("customer") or set()

        cur.execute("select pk_supplier, code from NCSC.BD_SUPPLIER")
        sup_codes = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_customer, code from NCSC.BD_CUSTOMER")
        cust_codes = {pk: code for pk, code in cur.fetchall()}

        cur.execute("select pk_dept, code from NCSC.ORG_DEPT")
        dept = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_costcenter, cccode from NCSC.RESA_COSTCENTER")
        cc = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_inoutbusiclass, code from NCSC.BD_INOUTBUSICLASS")
        io = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select freevalueid, typevalue1, typevalue2, typevalue3, typevalue4, "
                    "typevalue5, typevalue6, typevalue7, typevalue8, typevalue9 "
                    "from NCSC.GL_FREEVALUE")
        aux = {}
        for row in cur:
            fid, tvs = row[0], row[1:]
            dcode = ccode = iocode = supcode = custcode = ""
            for tv in tvs:
                if not tv or len(tv) < 40:
                    continue
                tpk, vpk = tv[:20], tv[20:40]
                if tpk == AUX_DEPT:
                    dcode = dept.get(vpk, "")
                elif tpk == AUX_COSTCENTER:
                    ccode = cc.get(vpk, "")
                elif tpk == AUX_IOITEM:
                    iocode = io.get(vpk, "")
                elif tpk in aux_sup_pks:
                    supcode = sup_codes.get(vpk, "")
                elif tpk in aux_cust_pks:
                    custcode = cust_codes.get(vpk, "")
            aux[fid] = (dcode, ccode, iocode, supcode, custcode)

        # Discarded (作废) vouchers are not ledger entries. Measured 2026-07-17:
        # exactly one on this book ($4,298.52) — it had been importing as posted.
        vq = ("select pk_voucher, year, period, num, explanation, prepareddate, "
              "creationtime, tallydate from NCSC.GL_VOUCHER "
              "where pk_accountingbook = :b "
              "  and (discardflag is null or discardflag <> 'Y')")
        if watermark:
            cur.execute(vq + " and creationtime >= :wm", b=PK_BOOK, wm=watermark)
        else:
            cur.execute(vq, b=PK_BOOK)
        vouchers = list(cur.fetchall())
        max_ct = max((v[6] for v in vouchers if v[6]), default=None)

        # Status backfill feed: the whole book's tally facts, deliberately NOT
        # watermark-limited. A voucher created in June and tallied in July keeps
        # its June creationtime, so the watermark would never bring it back and
        # it would sit at draft forever (spec §14.4.1). Two columns x ~40k rows.
        cur.execute("select pk_voucher, tallydate from NCSC.GL_VOUCHER "
                    "where pk_accountingbook = :b "
                    "  and (discardflag is null or discardflag <> 'Y')", b=PK_BOOK)
        tallied = {pk for pk, td in cur if td and td != "~"}

        # details: fetch the whole book; transform() filters by pk2id membership.
        cur.execute(
            "select pk_voucher, detailindex, accountcode, debitamount, creditamount, "
            "localdebitamount, localcreditamount, pk_currtype, excrate1, explanation, assid "
            "from NCSC.GL_DETAIL where pk_accountingbook = :b", b=PK_BOOK)
        details = list(cur.fetchall())
    finally:
        con.close()
    return NcExtract(ccy=ccy, aux=aux, vouchers=vouchers, details=details,
                     max_creationtime=max_ct, tallied=tallied)


# ── run lifecycle (worker) ─────────────────────────────────────────────────────────
import logging
import threading
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values, register_uuid
from sqlalchemy.engine.url import make_url

register_uuid()

logger = logging.getLogger(__name__)

_start_lock = threading.Lock()
STALE_AFTER = timedelta(minutes=30)
_CHUNK = 5000


class SyncAlreadyRunning(Exception):
    pass


def _pg_dsn() -> str:
    u = make_url(settings.database_url)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


def _mark(dsn, run_id, **fields):
    """Small autocommit update on the run row (progress counters)."""
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"update nc_sync_runs set {sets}, updated_at = now() where id = %s",
                (*fields.values(), run_id))
    con.close()


def _sync_statuses(cur, tallied: set) -> tuple[int, int]:
    """Align every NC-sourced voucher's status with NC's tally fact.

    Incremental skips pks it has already imported and its watermark is on
    creationtime, so a voucher tallied AFTER import never returns through that
    path — without this it would sit at draft forever. Diff first and update only
    what actually changed (usually nothing), same shape as the COA sync.
    """
    cur.execute("select nc_source_pk, status from journal_vouchers "
                "where nc_source_pk is not null")
    current = dict(cur.fetchall())
    to_posted = [pk for pk, st in current.items() if pk in tallied and st != "posted"]
    to_draft = [pk for pk, st in current.items() if pk not in tallied and st != "draft"]
    if to_posted:
        cur.execute("update journal_vouchers set status = 'posted', updated_at = now() "
                    "where nc_source_pk = any(%s)", (to_posted,))
    if to_draft:
        cur.execute("update journal_vouchers set status = 'draft', updated_at = now() "
                    "where nc_source_pk = any(%s)", (to_draft,))
    return len(to_posted), len(to_draft)


def _mark_terminal(dsn, run_id, **fields):
    """Terminal update that refuses to overwrite an already-terminal row
    (e.g. a run swept as abandoned must not flip back to success)."""
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"update nc_sync_runs set {sets}, updated_at = now() "
                f"where id = %s and status = 'running'",
                (*fields.values(), run_id))
    con.close()


def start_run(mode: str, started_by, *, fetch=fetch_from_nc,
              pg_dsn: str | None = None, run_worker: bool = True):
    """Single-flight gate + run-row insert. Synchronous — the API layer threads it.
    Returns the new run id. Raises SyncAlreadyRunning if a live run exists."""
    dsn = pg_dsn or _pg_dsn()
    with _start_lock:
        con = psycopg2.connect(dsn); con.autocommit = True
        cur = con.cursor()
        # auto-fail stale 'running' rows (crashed container), then check liveness
        cur.execute("update nc_sync_runs set status = 'failed', error = 'abandoned', "
                    "finished_at = now(), updated_at = now() "
                    "where status = 'running' and updated_at < %s",
                    (datetime.now(timezone.utc) - STALE_AFTER,))
        cur.execute("select id from nc_sync_runs where status = 'running'")
        if cur.fetchone():
            con.close()
            raise SyncAlreadyRunning("an NC sync is already running")
        run_id = uuid.uuid4()
        cur.execute("insert into nc_sync_runs (id, mode, status, started_by, started_at, "
                    "created_at, updated_at) values (%s, %s, 'running', %s, now(), now(), now())",
                    (run_id, mode, started_by))
        con.close()
    if run_worker:
        _run_worker(run_id, mode, fetch, dsn)
    return run_id


def _run_worker(run_id, mode: str, fetch, dsn: str) -> None:
    try:
        con = psycopg2.connect(dsn); con.autocommit = False
        cur = con.cursor()
        cur.execute("select nc_source_pk from journal_vouchers where nc_source_pk is not null")
        existing = {r[0] for r in cur.fetchall()}
        cur.execute("select watermark_to from nc_sync_runs where status = 'success' "
                    "and watermark_to is not null order by started_at desc limit 1")
        row = cur.fetchone()
        prev_wm = row[0] if row else None

        extract = fetch(prev_wm if mode == "incremental" else None)

        cur.execute("select code, id from cost_centers")
        uni_cc = dict(cur.fetchall())
        cur.execute("select code, id from departments")
        uni_dept = dict(cur.fetchall())
        # budget_accounts is owned by budget-api; may not exist in this DB
        try:
            sp = con.cursor()
            sp.execute("savepoint _ba")
            sp.execute("select code, id from budget_accounts")
            uni_ba = dict(sp.fetchall())
            sp.execute("release savepoint _ba")
            sp.close()
        except Exception:  # noqa: BLE001
            cur.execute("rollback to savepoint _ba")
            cur.execute("release savepoint _ba")
            logger.warning("budget_accounts table not found; income/expense dims will have "
                           "value_id=NULL — check the finance DB schema")
            uni_ba = {}

        # erp_suppliers mirrors NC BD_SUPPLIER; may not exist in very minimal DBs
        try:
            sp = con.cursor()
            sp.execute("savepoint _sup")
            sp.execute("select erp_supplier_code, id, supplier_name from erp_suppliers")
            uni_sup = {c: (i, n) for c, i, n in sp.fetchall()}
            sp.execute("release savepoint _sup")
            sp.close()
        except Exception:  # noqa: BLE001
            cur.execute("rollback to savepoint _sup")
            cur.execute("release savepoint _sup")
            logger.warning("erp_suppliers table not found; partner_id will be NULL for all "
                           "supplier lines — check the finance DB schema")
            uni_sup = {}

        try:
            sp = con.cursor()
            sp.execute("savepoint _cust")
            sp.execute("select code, id, name from nc_customers")
            uni_cust = {c: (i, n) for c, i, n in sp.fetchall()}
            sp.execute("release savepoint _cust")
            sp.close()
        except Exception:  # noqa: BLE001
            cur.execute("rollback to savepoint _cust")
            cur.execute("release savepoint _cust")
            logger.warning("nc_customers table not found; partner_id will be NULL for all "
                           "customer lines — check the finance DB schema")
            uni_cust = {}

        skip = existing if mode == "incremental" else set()
        vouchers, lines, dims, unmapped = transform(
            extract, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust, skip)

        deleted = 0
        if mode == "full":
            cur.execute("delete from journal_vouchers where nc_source_pk is not null")
            deleted = cur.rowcount

        tot: dict = {}
        for _, jid, _, _, _, dr, crr, ldr, lcr, _, _, _, _, _, _, _ in lines:
            t = tot.setdefault(jid, [Decimal("0")] * 4)
            t[0] += dr; t[1] += crr; t[2] += ldr; t[3] += lcr

        v_rows = [(v["id"], v["jv_number"], "JV", v["vdate"], v["period"], v["summary"],
                   v["status"], "nc", "nc_voucher", v["jv_number"], v["nc_pk"],
                   *(tot.get(v["id"], [Decimal("0")] * 4))) for v in vouchers]
        for i in range(0, len(v_rows), _CHUNK):
            execute_values(cur,
                "insert into journal_vouchers "
                "(id, jv_number, voucher_word, voucher_date, fiscal_period, summary, status, "
                " source_service, source_doc_type, source_doc_number, nc_source_pk, "
                " total_debit, total_credit, total_local_debit, total_local_credit, "
                " created_at, updated_at) values %s",
                v_rows[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, vouchers_inserted=min(i + _CHUNK, len(v_rows)))
        for i in range(0, len(lines), _CHUNK):
            execute_values(cur,
                "insert into journal_voucher_lines "
                "(id, jv_id, line_no, account_code, summary, orig_debit, orig_credit, "
                " local_debit, local_credit, currency, fx_rate, cost_center_id, department_id, "
                " income_expense_item_id, partner_id, partner_name, created_at, updated_at) values %s",
                lines[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, lines_inserted=min(i + _CHUNK, len(lines)))
        for i in range(0, len(dims), _CHUNK):
            execute_values(cur,
                "insert into jv_line_dimensions "
                "(id, jv_line_id, dim_code, value_id, value_text, created_at, updated_at) "
                "values %s",
                dims[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, dims_inserted=min(i + _CHUNK, len(dims)))

        _sync_statuses(cur, extract.tallied)

        # superseded-run guard: if sweeper already marked us abandoned, do not commit.
        cur.execute("select status from nc_sync_runs where id = %s for update", (run_id,))
        row = cur.fetchone()
        if not row or row[0] != "running":
            con.rollback(); con.close()
            return
        con.commit(); con.close()
        _mark_terminal(dsn, run_id, status="success", finished_at=datetime.now(timezone.utc),
                       vouchers_deleted=deleted, vouchers_inserted=len(vouchers),
                       lines_inserted=len(lines), dims_inserted=len(dims),
                       unmapped_cc_count=unmapped, watermark_from=prev_wm,
                       watermark_to=extract.max_creationtime or prev_wm)
    except Exception as e:  # noqa: BLE001 — terminal state must always be written
        try:
            con.rollback(); con.close()
        except Exception:
            pass
        _mark_terminal(dsn, run_id, status="failed", error=str(e)[:2000],
                       finished_at=datetime.now(timezone.utc))
