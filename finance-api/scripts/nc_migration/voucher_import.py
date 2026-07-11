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
        num_s = str(int(num)) if num is not None else "0"
        vouchers.append({
            "id": jid, "jv_number": f"记-{year}{period}-{num_s}",
            "period": f"{year}-{period}", "vdate": vdate,
            "summary": (expl or "")[:255], "nc_pk": pk,
        })
    return pk2id, vouchers


def read_details(cur, pk2id: dict, ccy: dict) -> list:
    cur.execute(
        "select pk_voucher, detailindex, accountcode, debitamount, creditamount, "
        "localdebitamount, localcreditamount, pk_currtype, excrate1, explanation "
        "from NCSC.GL_DETAIL where pk_accountingbook = :b", b=PK_BOOK)
    lines = []
    for pk, idx, acct, dr, cr, ldr, lcr, curr, rate, expl in cur:
        jid = pk2id.get(pk)
        if jid is None:
            continue
        # NC allows both debit+credit on one line; UniOps JV requires one side.
        # Net onto a single side — preserves the line's net effect on its account.
        odr, ocr = _net_side(_d(dr), _d(cr))
        ldr_, lcr_ = _net_side(_d(ldr), _d(lcr))
        lines.append((
            uuid.uuid4(), jid, int(idx or 0), (acct or "").strip() or None,
            (expl or "")[:255], odr, ocr, ldr_, lcr_,
            ccy.get(curr, "CAD"), _d(rate) if rate else Decimal("1")))
    return lines


def _net_side(dr: Decimal, cr: Decimal) -> tuple[Decimal, Decimal]:
    n = dr - cr
    return (n, Decimal("0")) if n >= 0 else (Decimal("0"), -n)


def load(vouchers, lines, dsn, clear):
    con = psycopg2.connect(dsn); con.autocommit = False; cur = con.cursor()
    if clear:
        cur.execute("delete from journal_vouchers where nc_source_pk is not null")
    # totals per voucher (from lines)
    tot: dict = {}
    for _, jid, _, _, _, dr, crr, ldr, lcr, _, _ in lines:
        t = tot.setdefault(jid, [Decimal("0")] * 4)
        t[0] += dr; t[1] += crr; t[2] += ldr; t[3] += lcr
    execute_values(cur,
        "insert into journal_vouchers "
        "(id, jv_number, voucher_word, voucher_date, fiscal_period, summary, status, "
        " source_service, source_doc_type, source_doc_number, nc_source_pk, "
        " total_debit, total_credit, total_local_debit, total_local_credit, "
        " created_at, updated_at) values %s",
        [(v["id"], v["jv_number"], "记", v["vdate"], v["period"], v["summary"], "posted",
          "nc", "nc_voucher", v["jv_number"], v["nc_pk"],
          *(tot.get(v["id"], [Decimal("0")] * 4)))
         for v in vouchers],
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())",
        page_size=2000)
    execute_values(cur,
        "insert into journal_voucher_lines "
        "(id, jv_id, line_no, account_code, summary, orig_debit, orig_credit, "
        " local_debit, local_credit, currency, fx_rate, created_at, updated_at) values %s",
        lines,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())",
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
    pk2id, vouchers = read_vouchers(cur)
    lines = read_details(cur, pk2id, ccy)
    con.close()
    print(f"NC read: {len(vouchers)} vouchers, {len(lines)} detail lines")

    if args.load:
        load(vouchers, lines, args.database, clear=args.confirm_clear)
        print("loaded. reconciling...")
        con = _nc_connect()
        reconcile(con.cursor(), args.database)
        con.close()
    else:
        print("[dry-run] no writes. sample voucher:", vouchers[0] if vouchers else None)


if __name__ == "__main__":
    main()
