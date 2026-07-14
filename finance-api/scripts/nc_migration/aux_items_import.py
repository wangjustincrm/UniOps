"""NC65 BD_ACCASS → coa_aux_items import (which aux dims each account carries).

Chain: BD_ACCASS.pk_coveraccasoa -> BD_ACCASOA.pk_accasoa (-> pk_account)
       BD_ACCASS.pk_entity      -> BD_ACCASSITEM (name/code)
Account codes come from BD_ACCOUNT within chart 1001A1100000003CG6GD.
Idempotent: --load clears coa_aux_items then reloads (small table).
Read-only on NC; refuses 10.10.50.* targets like the sibling importers.

Usage:
    python scripts/nc_migration/aux_items_import.py --dry-run
    python scripts/nc_migration/aux_items_import.py --load
"""
import argparse
import re
import sys
import uuid

import oracledb
import psycopg2
from psycopg2.extras import execute_values, register_uuid

register_uuid()

NC_ENV = r"C:\Project\nc65_conn.env"
CHART = "1001A1100000003CG6GD"
DEV_DSN = "host=localhost port=5432 dbname=epms user=epms " \
          "password=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"

# NC 辅助项名称 -> our dim_code (curated); unknown names fall back to a slug of
# the NC item code so nothing silently disappears.
NAME_MAP = {
    "部門": "department", "成本中心": "cost_center", "收支項目": "income_expense_item",
    "供應商": "supplier", "客戶": "customer", "人員": "employee", "職員": "employee",
    "項目": "project",
}


def map_assitem_name(name: str, code: str) -> str:
    for key, dim in NAME_MAP.items():
        if key in (name or ""):
            return dim
    return re.sub(r"[^a-z0-9_]+", "_", (code or "unknown").strip().lower()).strip("_")


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


def fetch(cur) -> tuple[list, dict]:
    """-> (rows [(account_code, dim_code, seq)], distinct {assitem name: dim_code})."""
    cur.execute(
        "select acc.code, item.name, item.code "
        "from NCSC.BD_ACCASS a "
        "join NCSC.BD_ACCASOA soa on soa.pk_accasoa = a.pk_coveraccasoa "
        "join NCSC.BD_ACCOUNT acc on acc.pk_account = soa.pk_account "
        "join NCSC.BD_ACCASSITEM item on item.pk_accassitem = a.pk_entity "
        "where acc.pk_accchart = :c", c=CHART)
    seen: dict[tuple, int] = {}
    names: dict[str, str] = {}
    rows = []
    for acct_code, iname, icode in cur.fetchall():
        dim = map_assitem_name(iname, icode)
        names[f"{iname} ({icode})"] = dim
        key = (acct_code, dim)
        if key in seen:
            continue
        seen[key] = 1
        rows.append((acct_code, dim, len([r for r in rows if r[0] == acct_code]) + 1))
    return rows, names


def load(rows, dsn):
    con = psycopg2.connect(dsn); con.autocommit = False
    cur = con.cursor()
    cur.execute("delete from coa_aux_items")
    execute_values(cur,
        "insert into coa_aux_items (id, account_code, dim_code, seq, created_at, updated_at) "
        "values %s",
        [(uuid.uuid4(), a, d, s) for a, d, s in rows],
        template="(%s,%s,%s,%s, now(), now())", page_size=2000)
    con.commit(); con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--database", default=DEV_DSN)
    args = ap.parse_args()
    if args.load and "10.10.50" in args.database:
        sys.exit("REFUSING: target looks like production (10.10.50.*).")

    con = _nc_connect()
    rows, names = fetch(con.cursor())
    con.close()
    print(f"NC aux links: {len(rows)} (account, dim) pairs across "
          f"{len({r[0] for r in rows})} accounts")
    print("assitem name -> dim_code mapping observed:")
    for k, v in sorted(names.items()):
        print(f"  {k} -> {v}")
    if args.load:
        load(rows, args.database)
        print("loaded into coa_aux_items.")
    else:
        print("[dry-run] no writes.")


if __name__ == "__main__":
    main()
