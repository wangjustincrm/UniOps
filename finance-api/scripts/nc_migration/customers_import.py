"""NC65 bd_customer → nc_customers import (partner dimension master).

The ERP integration API has no customer endpoint, so customers load straight
from NC (read-only). Idempotent: --load clears nc_customers then reloads.
Refuses 10.10.50.* targets like the sibling importers.

Usage:
    python scripts/nc_migration/customers_import.py --dry-run
    python scripts/nc_migration/customers_import.py --load
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
DEV_DSN = "host=localhost port=5432 dbname=epms user=epms " \
          "password=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"


def pick_name(ename, name, code) -> str:
    """English name first, then Chinese, then the code (COA import convention)."""
    for v in (ename, name):
        if v and str(v).strip():
            return str(v).strip()[:255]
    return str(code)


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


def fetch(cur) -> list:
    """-> [(code, name)] for enabled customers (enablestate=2), code-deduped."""
    cur.execute("select code, name, ename from NCSC.BD_CUSTOMER where enablestate = 2")
    out, seen = [], set()
    for code, name, ename in cur.fetchall():
        c = (code or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append((c, pick_name(ename, name, c)))
    return out


def load(rows, dsn):
    con = psycopg2.connect(dsn)
    cur = con.cursor()
    cur.execute("delete from nc_customers")
    execute_values(cur,
        "insert into nc_customers (id, code, name, is_active, created_at, updated_at) values %s",
        [(uuid.uuid4(), c, n, True) for c, n in rows],
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
    rows = fetch(con.cursor())
    con.close()
    print(f"NC customers (enabled): {len(rows)}; sample: {rows[:3]}")
    if args.load:
        load(rows, args.database)
        print("loaded into nc_customers.")
    else:
        print("[dry-run] no writes.")


if __name__ == "__main__":
    main()
