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
}


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


@dataclass
class NcExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    ccy: dict           # pk_currtype -> currency code
    aux: dict           # freevalueid -> (dept_code, cc_code, io_code)
    vouchers: list      # (pk, year, period, num, explanation, prepareddate, creationtime)
    details: list       # (pk_voucher, detailindex, accountcode, dr, cr, ldr, lcr,
                        #  pk_currtype, excrate1, explanation, assid)
    max_creationtime: str | None


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _net_side(dr: Decimal, cr: Decimal) -> tuple[Decimal, Decimal]:
    n = dr - cr
    return (n, Decimal("0")) if n >= 0 else (Decimal("0"), -n)


def _resolve_dims(assid, aux, uni_cc, uni_dept, uni_ba):
    """-> (cost_center_id, department_id, io_code, budget_account_id, had_cc_hint)."""
    d, c, io = aux.get(assid, ("", "", ""))
    epms = CC_BY_CODE.get(c) if c else CC_BY_DEPT.get(d)
    return (uni_cc.get(epms) if epms else None,
            uni_dept.get(d) if d else None,
            io or None,
            uni_ba.get(io) if io else None,
            bool(c or d))


def transform(extract: NcExtract, uni_cc: dict, uni_dept: dict, uni_ba: dict,
              skip_pks: set) -> tuple[list, list, list, int]:
    """NC rows -> (voucher dicts, line tuples, dim tuples, unmapped_cc count).
    Skips vouchers whose pk is in skip_pks (incremental pk-dedup)."""
    pk2id, vouchers = {}, []
    for pk, year, period, num, expl, pdate, _ctime in extract.vouchers:
        if pk in skip_pks:
            continue
        jid = uuid.uuid4()
        pk2id[pk] = jid
        vdate = (pdate[:10] if pdate and len(pdate) >= 10 else f"{year}-{period}-01")
        num_s = str(int(num)) if num is not None else "0"
        vouchers.append({
            "id": jid, "jv_number": f"记-{year}{period}-{num_s}",
            "period": f"{year}-{period}", "vdate": vdate,
            "summary": (expl or "")[:255], "nc_pk": pk,
        })

    lines, dims, unmapped = [], [], 0
    for pk, idx, acct, dr, cr, ldr, lcr, curr, rate, expl, assid in extract.details:
        jid = pk2id.get(pk)
        if jid is None:
            continue
        odr, ocr = _net_side(_d(dr), _d(cr))
        ldr_, lcr_ = _net_side(_d(ldr), _d(lcr))
        cc_id, dept_id, io_code, ba_id, had_hint = _resolve_dims(
            assid, extract.aux, uni_cc, uni_dept, uni_ba)
        if had_hint and cc_id is None:
            unmapped += 1
        lid = uuid.uuid4()
        lines.append((
            lid, jid, int(idx or 0), (acct or "").strip() or None,
            (expl or "")[:255], odr, ocr, ldr_, lcr_,
            extract.ccy.get(curr, "CAD"), _d(rate) if rate else Decimal("1"),
            cc_id, dept_id))
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
            dcode = ccode = iocode = ""
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
            aux[fid] = (dcode, ccode, iocode)

        vq = ("select pk_voucher, year, period, num, explanation, prepareddate, "
              "creationtime from NCSC.GL_VOUCHER where pk_accountingbook = :b")
        if watermark:
            cur.execute(vq + " and creationtime >= :wm", b=PK_BOOK, wm=watermark)
        else:
            cur.execute(vq, b=PK_BOOK)
        vouchers = list(cur.fetchall())
        max_ct = max((v[6] for v in vouchers if v[6]), default=None)

        # details: fetch the whole book; transform() filters by pk2id membership.
        cur.execute(
            "select pk_voucher, detailindex, accountcode, debitamount, creditamount, "
            "localdebitamount, localcreditamount, pk_currtype, excrate1, explanation, assid "
            "from NCSC.GL_DETAIL where pk_accountingbook = :b", b=PK_BOOK)
        details = list(cur.fetchall())
    finally:
        con.close()
    return NcExtract(ccy=ccy, aux=aux, vouchers=vouchers, details=details,
                     max_creationtime=max_ct)
