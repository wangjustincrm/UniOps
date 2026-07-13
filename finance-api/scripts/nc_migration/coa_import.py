"""NC65 → UniOps COA import (Phase 1).

Reads NC65 `BD_ACCOUNT` for the Canada Royal Milk book's account chart
(pk_accchart = 1001A1100000003CG6GD, 360 enabled accounts) and loads them into
finance `chart_of_accounts`, keyed by NC 科目编码 (code). "沿用 NC 中式科目".

Read-only on NC (Oracle). Writes to the finance Postgres given by --database-url
(defaults to the LOCAL dev DB — NEVER point this at production without intent).

Usage (dry-run reconcile only, no writes):
    python -m scripts.nc_migration.coa_import --dry-run
Load into local dev DB (clears chart_of_accounts first):
    python -m scripts.nc_migration.coa_import --load --confirm-clear
"""
import argparse
import os
import re
import sys

import oracledb
import psycopg2

NC_ENV = r"C:\Project\nc65_conn.env"
PK_ACCCHART = "1001A1100000003CG6GD"        # Canada Royal Milk book's account chart
DEV_DSN = "host=localhost port=5432 dbname=epms user=epms " \
          "password=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"


# ── account_type / normal_balance from Chinese 科目 code ─────────────────────────
def account_type(code: str) -> str:
    c0, c2 = code[:1], code[:2]
    if c0 == "1":
        return "asset"
    if c0 == "2":
        return "liability"
    if c0 in ("3", "4"):
        return "equity"
    if c0 == "5":
        return "expense"            # 成本类 (生产成本/制造费用…)
    if c0 == "6":
        # 损益类: 收入 vs 费用
        return "revenue" if c2 in ("60", "61", "62", "63") else "expense"
    return "asset"


def normal_balance(atype: str) -> str:
    return "debit" if atype in ("asset", "expense") else "credit"


# ── NC read ──────────────────────────────────────────────────────────────────────
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


def read_nc_accounts() -> list[dict]:
    cfg = _nc_cfg()
    dsn = oracledb.makedsn(cfg["NC65_HOST"], int(cfg.get("NC65_PORT", "1521")),
                           service_name=cfg["NC65_SERVICE"])
    con = oracledb.connect(user=cfg["NC65_USER"], password=cfg["NC65_PASSWORD"], dsn=dsn)
    cur = con.cursor()
    cur.execute(
        "select pk_account, code, name, name2, pid, quantity "
        "from NCSC.BD_ACCOUNT where pk_accchart = :chart and enablestate = 2",
        chart=PK_ACCCHART)
    rows = [{"pk": r[0], "code": r[1], "name_cn": r[2], "name_en": r[3],
             "pid": r[4], "quantity": r[5]}
            for r in cur.fetchall()]
    con.close()

    pk2code = {r["pk"]: r["code"] for r in rows}
    parents = {r["pid"] for r in rows if r["pid"] and r["pid"] in pk2code}
    for r in rows:
        r["parent_code"] = pk2code.get(r["pid"])          # None for top-level
        r["is_postable"] = r["pk"] not in parents         # leaf = postable
        # Prefer English (NAME2), fall back to Chinese (NAME), then the code.
        def _clean(v):
            return v if v and v != "~" else None
        r["name"] = _clean(r["name_en"]) or _clean(r["name_cn"]) or r["code"]
        atype = account_type(r["code"])
        r["account_type"] = atype
        r["normal_balance"] = normal_balance(atype)
        r["quantity_accounting"] = (r["quantity"] == "Y")
    return rows


# ── finance write ─────────────────────────────────────────────────────────────────
def load(rows: list[dict], dsn: str, clear: bool) -> None:
    con = psycopg2.connect(dsn)
    con.autocommit = False
    cur = con.cursor()
    if clear:
        cur.execute("delete from chart_of_accounts")
    for r in rows:
        cur.execute(
            "insert into chart_of_accounts "
            "(id, code, name, account_type, normal_balance, is_postable, parent_code, "
            " is_active, quantity_accounting, aux_dimensions, created_at, updated_at) "
            "values (gen_random_uuid(), %s,%s,%s,%s,%s,%s, true,%s, '[]'::jsonb, now(), now()) "
            "on conflict (code) do update set name=excluded.name, "
            "account_type=excluded.account_type, normal_balance=excluded.normal_balance, "
            "is_postable=excluded.is_postable, parent_code=excluded.parent_code, "
            "quantity_accounting=excluded.quantity_accounting, updated_at=now()",
            (r["code"], r["name"], r["account_type"], r["normal_balance"],
             r["is_postable"], r["parent_code"], r["quantity_accounting"]))
    con.commit()
    cur.execute("select count(*) from chart_of_accounts")
    total = cur.fetchone()[0]
    con.close()
    print(f"loaded {len(rows)} NC accounts; chart_of_accounts now has {total} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="read+map NC only, no writes")
    ap.add_argument("--load", action="store_true", help="write into the target DB")
    ap.add_argument("--confirm-clear", action="store_true",
                    help="clear chart_of_accounts before load (COA aligns with NC)")
    ap.add_argument("--database", default=DEV_DSN, help="target Postgres DSN (default: local dev)")
    args = ap.parse_args()

    rows = read_nc_accounts()
    print(f"NC accounts read (chart {PK_ACCCHART}): {len(rows)}")
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["account_type"]] = by_type.get(r["account_type"], 0) + 1
    print("by account_type:", by_type)
    print("postable(leaf):", sum(1 for r in rows if r["is_postable"]),
          " headers:", sum(1 for r in rows if not r["is_postable"]))
    print("sample:", [(r["code"], r["name"], r["account_type"], r["normal_balance"],
                       r["parent_code"], r["is_postable"]) for r in rows[:5]])

    if args.load:
        if "10.10.50" in args.database:
            sys.exit("REFUSING: target DSN looks like production (10.10.50.*).")
        load(rows, args.database, clear=args.confirm_clear)
    else:
        print("\n[dry-run] no writes. Re-run with --load --confirm-clear to load into dev.")


if __name__ == "__main__":
    main()
