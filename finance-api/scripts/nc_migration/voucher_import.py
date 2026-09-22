"""NC65 → UniOps voucher import (Phase 3, account-level).

Reads NC65 GL_VOUCHER + GL_DETAIL for the Canada Royal Milk book and loads them
as POSTED journal_vouchers + journal_voucher_lines (account_code + dual-currency
amounts). Auxiliary dimensions (cost center etc.) are DEFERRED to Phase 2 — this
account-level pass validates the pipeline against NC balances (科目余额表 vs NC).

Read-only on NC; writes to the finance Postgres in --database (default LOCAL dev;
hard-refuses 10.10.50.* production).

Usage:
    python scripts/nc_migration/voucher_import.py --dry-run
    python scripts/nc_migration/voucher_import.py --load --confirm-clear
"""
import argparse
import re
import sys
import uuid
from decimal import Decimal

import oracledb
import psycopg2
from psycopg2.extras import execute_values, register_uuid

register_uuid()                             # let psycopg2 adapt uuid.UUID
oracledb.defaults.fetch_decimals = True     # money precision, not float

NC_ENV = r"C:\Project\nc65_conn.env"
PK_BOOK = "1001A1100000003CGCBX"            # Canada Royal Milk book
DEV_DSN = "host=localhost port=5432 dbname=epms user=epms " \
          "password=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"

# NC 辅助核算 global type pks (first 20 chars of a GL_FREEVALUE.typevalueN):
AUX_DEPT = "0001Z0100000000005CS"           # 部门 -> ORG_DEPT (1:1 by code -> departments)
AUX_COSTCENTER = "1003Z31000000000SP6J"     # 成本中心 -> RESA_COSTCENTER (curated map)
AUX_IOITEM = "0001Z0100000000005CZ"         # 收支项目 -> BD_INOUTBUSICLASS (1:1 by code -> budget_accounts)

# Curated NC -> EPMS cost-center map (user, 2026-07-12). Rule: if the line carries
# a cost-center code, the CODE decides (department-independent); otherwise classify
# by DEPARTMENT. This covers every row of the user's table incl. rollup codes
# (ENG/PD/QA/SC/HR) and cross-department bookings.
CC_BY_CODE = {
    "E01": "MOH-0106-E01", "E02": "MOH-0106-E01", "E03": "MOH-0106-E01",
    "E04": "MOH-0106-E01", "E05": "MOH-0106-E01", "E06": "MOH-0106-E01",
    "E07": "MOH-0106-E01", "ENG": "MOH-0106-E01",
    "P01": "MOH-0104-P01", "P02": "MOH-0104-P02", "P03": "MOH-0104-P03",
    "PD": "MOH-0104-P01",
    "Q01": "MOH-0105-LAB", "Q02": "MOH-0105-LAB", "QA": "GA-0105",
    "S02": "MOH-0107-S02", "S03": "SELL-0107-S03", "SC": "GA-0107",
    "H01": "MOH-0101", "HR": "GA-0101",
    # F01 -> None (6603, no EPMS cost center)
}
CC_BY_DEPT = {
    "0100": "GA-0100", "0101": "GA-0101", "0103": "GA-0103",
    "0105": "GA-0105", "0107": "GA-0107", "0109": "RD-0109",
    "0110": "SELL-0110", "0111": "SELL-0111", "0112": "SELL-0112", "0113": "SELL-0113",
}


def load_aux(cur) -> dict:
    """freevalueid -> (dept_code, cc_code, ioitem_code, sup_code, cust_code) by parsing
    GL_FREEVALUE typevalues. Dynamically resolves all five aux type pks via BD_ACCASSITEM
    (supplier/customer) and the three curated constants (dept/cc/ioitem)."""
    # Resolved BY CODE since 2026-09-22 — the name-matching version this used to
    # call is gone (it could not see NC's combined 客商 archive at all). The sets
    # are kept because the rest of this legacy script indexes them that way.
    from app.services.nc_sync import resolve_aux_types_by_code
    cur.execute("select pk_accassitem, code, name from NCSC.BD_ACCASSITEM")
    type_pks = resolve_aux_types_by_code(list(cur.fetchall()))
    aux_sup_pks = {type_pks["supplier"]} if type_pks.get("supplier") else set()
    aux_cust_pks = {type_pks["customer"]} if type_pks.get("customer") else set()

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
    out = {}
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
        out[fid] = (dcode, ccode, iocode, supcode, custcode)
    return out


def load_uniops_map(dsn: str, table: str) -> dict:
    """code -> id from a UniOps master (cost_centers / departments / budget_accounts)."""
    con = psycopg2.connect(dsn); cur = con.cursor()
    cur.execute(f"select code, id from {table}")
    m = {code: cid for code, cid in cur.fetchall()}
    con.close()
    return m


def resolve_dims(assid, aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust):
    """-> (cost_center_id, department_id, ioitem_code, budget_account_id,
    partner_id, partner_name, had_cc_hint).
    Cost center: code-first else department (curated). Supplier wins over customer."""
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


def _nc_cfg() -> dict:
    cfg = {}
    with open(NC_ENV, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")
    return cfg


def _nc_connect():
    cfg = _nc_cfg()
    dsn = oracledb.makedsn(cfg["NC65_HOST"], int(cfg.get("NC65_PORT", "1521")),
                           service_name=cfg["NC65_SERVICE"])
    return oracledb.connect(user=cfg["NC65_USER"], password=cfg["NC65_PASSWORD"], dsn=dsn)


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def read_vouchers(cur) -> tuple[dict, list]:
    """Returns (pk_voucher -> jv_id, list of voucher row dicts)."""
    cur.execute(
        "select pk_voucher, year, period, num, explanation, prepareddate "
        "from NCSC.GL_VOUCHER where pk_accountingbook = :b", b=PK_BOOK)
    pk2id, vouchers = {}, []
    for pk, year, period, num, expl, pdate in cur.fetchall():
        jid = uuid.uuid4()
        pk2id[pk] = jid
        vdate = (pdate[:10] if pdate and len(pdate) >= 10 else f"{year}-{period}-01")
        num_i = int(num) if num is not None else 0
        vouchers.append({
            # JV- prefix + 4-padded (user 2026-07-13; keep in sync with services/nc_sync.py)
            "id": jid, "jv_number": f"JV-{year}{period}-{num_i:04d}",
            "period": f"{year}-{period}", "vdate": vdate,
            "summary": (expl or "")[:255], "nc_pk": pk,
        })
    return pk2id, vouchers


def read_details(cur, pk2id, ccy, aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust):
    """Returns (lines, dims). Lines carry 16 fields including partner_id/partner_name;
    dims are jv_line_dimensions rows for the 收支项目 (income/expense item)."""
    cur.execute(
        "select pk_voucher, detailindex, accountcode, debitamount, creditamount, "
        "localdebitamount, localcreditamount, pk_currtype, excrate1, explanation, assid "
        "from NCSC.GL_DETAIL where pk_accountingbook = :b", b=PK_BOOK)
    lines, dims = [], []
    for pk, idx, acct, dr, cr, ldr, lcr, curr, rate, expl, assid in cur:
        jid = pk2id.get(pk)
        if jid is None:
            continue
        # NC allows both debit+credit on one line; UniOps JV requires one side.
        # Net onto a single side — preserves the line's net effect on its account.
        odr, ocr = _net_side(_d(dr), _d(cr))
        ldr_, lcr_ = _net_side(_d(ldr), _d(lcr))
        cc_id, dept_id, io_code, ba_id, partner_id, partner_name, _ = resolve_dims(
            assid, aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust)
        lid = uuid.uuid4()
        lines.append((
            lid, jid, int(idx or 0), (acct or "").strip() or None,
            (expl or "")[:255], odr, ocr, ldr_, lcr_,
            ccy.get(curr, "CAD"), _d(rate) if rate else Decimal("1"),
            cc_id, dept_id, ba_id, partner_id, partner_name))
        if io_code:
            dims.append((uuid.uuid4(), lid, "income_expense_item", ba_id, io_code))
    return lines, dims


def _net_side(dr: Decimal, cr: Decimal) -> tuple[Decimal, Decimal]:
    n = dr - cr
    return (n, Decimal("0")) if n >= 0 else (Decimal("0"), -n)


def load(vouchers, lines, dims, dsn, clear):
    con = psycopg2.connect(dsn); con.autocommit = False; cur = con.cursor()
    if clear:
        # cascades to journal_voucher_lines + jv_line_dimensions (ondelete CASCADE)
        cur.execute("delete from journal_vouchers where nc_source_pk is not null")
    # totals per voucher (from lines)
    tot: dict = {}
    for _, jid, _, _, _, dr, crr, ldr, lcr, _, _, _, _, _, _, _ in lines:
        t = tot.setdefault(jid, [Decimal("0")] * 4)
        t[0] += dr; t[1] += crr; t[2] += ldr; t[3] += lcr
    execute_values(cur,
        "insert into journal_vouchers "
        "(id, jv_number, voucher_word, voucher_date, fiscal_period, summary, status, "
        " source_service, source_doc_type, source_doc_number, nc_source_pk, "
        " total_debit, total_credit, total_local_debit, total_local_credit, "
        " created_at, updated_at) values %s",
        [(v["id"], v["jv_number"], "JV", v["vdate"], v["period"], v["summary"], "posted",
          "nc", "nc_voucher", v["jv_number"], v["nc_pk"],
          *(tot.get(v["id"], [Decimal("0")] * 4)))
         for v in vouchers],
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())",
        page_size=2000)
    execute_values(cur,
        "insert into journal_voucher_lines "
        "(id, jv_id, line_no, account_code, summary, orig_debit, orig_credit, "
        " local_debit, local_credit, currency, fx_rate, cost_center_id, department_id, "
        " income_expense_item_id, partner_id, partner_name, created_at, updated_at) values %s",
        lines,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())",
        page_size=5000)
    if dims:
        execute_values(cur,
            "insert into jv_line_dimensions "
            "(id, jv_line_id, dim_code, value_id, value_text, created_at, updated_at) values %s",
            dims,
            template="(%s,%s,%s,%s,%s, now(), now())",
            page_size=5000)
    con.commit(); con.close()


def reconcile(nc_cur, dsn):
    """Per-account NET local balance (debit-credit): NC GL_DETAIL vs imported JV.
    Net is invariant under the both-sided line netting, and is what 科目余额表 uses."""
    nc_cur.execute(
        "select accountcode, sum(localdebitamount - localcreditamount) "
        "from NCSC.GL_DETAIL where pk_accountingbook = :b group by accountcode", b=PK_BOOK)
    nc = {(a or "").strip(): _d(n) for a, n in nc_cur.fetchall()}
    con = psycopg2.connect(dsn); cur = con.cursor()
    cur.execute("select account_code, coalesce(sum(local_debit - local_credit),0) "
                "from journal_voucher_lines l join journal_vouchers v on v.id=l.jv_id "
                "where v.nc_source_pk is not null group by account_code")
    ours = {a: _d(n) for a, n in cur.fetchall()}
    con.close()
    diffs = 0
    for acct, ncnet in nc.items():
        if ours.get(acct, Decimal("0")) != ncnet:
            diffs += 1
            if diffs <= 8:
                print(f"  DIFF {acct}: NC net={ncnet} | ours net={ours.get(acct)}")
    print(f"reconcile (net per account): {len(nc)} NC accounts, {diffs} diffs "
          f"({'OK — all match' if diffs == 0 else 'MISMATCH'})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--confirm-clear", action="store_true")
    ap.add_argument("--database", default=DEV_DSN)
    args = ap.parse_args()
    if args.load and "10.10.50" in args.database:
        sys.exit("REFUSING: target looks like production (10.10.50.*).")

    con = _nc_connect(); cur = con.cursor()
    cur.execute("select pk_currtype, code from NCSC.BD_CURRTYPE")
    ccy = {pk: code for pk, code in cur.fetchall()}
    aux = load_aux(cur)
    uni_cc = load_uniops_map(args.database, "cost_centers")
    uni_dept = load_uniops_map(args.database, "departments")
    uni_ba = load_uniops_map(args.database, "budget_accounts")

    # partner master maps: erp_supplier_code / nc_customer code -> (id, name)
    pg_con = psycopg2.connect(args.database); pg_cur = pg_con.cursor()
    pg_cur.execute("select erp_supplier_code, id, supplier_name from erp_suppliers")
    uni_sup = {c: (i, n) for c, i, n in pg_cur.fetchall()}
    pg_cur.execute("select code, id, name from nc_customers")
    uni_cust = {c: (i, n) for c, i, n in pg_cur.fetchall()}
    pg_con.close()

    print(f"aux combos={len(aux)}, UniOps cc={len(uni_cc)} dept={len(uni_dept)} "
          f"budget_accounts={len(uni_ba)} suppliers={len(uni_sup)} customers={len(uni_cust)}")
    pk2id, vouchers = read_vouchers(cur)
    lines, dims = read_details(cur, pk2id, ccy, aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust)
    con.close()
    cc_n = sum(1 for ln in lines if ln[11] is not None)
    dept_n = sum(1 for ln in lines if ln[12] is not None)
    partner_n = sum(1 for ln in lines if ln[14] is not None)
    print(f"NC read: {len(vouchers)} vouchers, {len(lines)} lines "
          f"(cost_center={cc_n}, department={dept_n}, partner={partner_n}, "
          f"income/expense dims={len(dims)})")

    if args.load:
        load(vouchers, lines, dims, args.database, clear=args.confirm_clear)
        print("loaded. reconciling...")
        con = _nc_connect()
        reconcile(con.cursor(), args.database)
        con.close()
    else:
        print("[dry-run] no writes. sample voucher:", vouchers[0] if vouchers else None)


if __name__ == "__main__":
    main()
