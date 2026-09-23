"""Check every NC column this service names against Oracle's data dictionary.

Why this exists: `nc_sync` reads NC over raw SQL, so a wrong column name is
invisible to the whole test suite — the fixtures hand over tuples and never go
near Oracle. It surfaces only when a sync runs, which in practice means during a
production release. On 2026-09-23 `select pk_project, code from BD_PROJECT`
failed with ORA-00904 after deploy: BD_MATERIAL uses CODE, BD_PROJECT uses
PROJECT_CODE, and the two had been assumed to match because the material table
had been checked and the project table had only had its row count measured.

Run it before a release that touches nc_sync:

    docker run --rm --network host -v "$PWD:/app" -w /app \
      -v /path/to/.env:/env/.env:ro <finance-api image> \
      python -m scripts.verify_nc_columns

Exit code 1 if any column is missing, so it can gate a release script.

★ Run it with `python -m scripts.verify_nc_columns`, not
`python scripts/verify_nc_columns.py` — the latter puts /app/scripts on sys.path
instead of /app and the `app.` imports blow up.
"""
import re
import sys

# table -> the columns nc_sync.fetch_from_nc actually selects or filters on.
# Keep in step with that function; a name added there and not here is exactly the
# gap this script exists to close.
EXPECTED = {
    "GL_VOUCHER": "pk_voucher year period num explanation prepareddate creationtime "
                  "tallydate pk_system voucherkind discardflag tempsaveflag errmessage "
                  "attachment pk_prepared pk_checked pk_manager pk_vouchertype "
                  "pk_accountingbook",
    "GL_DETAIL": "pk_voucher detailindex accountcode debitamount creditamount "
                 "localdebitamount localcreditamount pk_currtype excrate1 explanation "
                 "assid debitquantity creditquantity price unitname oppositesubj "
                 "pk_accountingbook",
    "GL_FREEVALUE": "freevalueid " + " ".join(f"typevalue{i}" for i in range(1, 10)),
    "SM_USER": "cuserid user_name",
    "BD_VOUCHERTYPE": "pk_vouchertype name",
    "BD_ACCASSITEM": "pk_accassitem code name",
    "BD_CURRTYPE": "pk_currtype code",
    "ORG_DEPT": "pk_dept code",
    "RESA_COSTCENTER": "pk_costcenter cccode",
    "BD_INOUTBUSICLASS": "pk_inoutbusiclass code",
    "BD_SUPPLIER": "pk_supplier code name enablestate pk_financeorg",
    "BD_CUSTOMER": "pk_customer code name enablestate pk_financeorg",
    "BD_MATERIAL": "pk_material code",
    "BD_PROJECT": "pk_project project_code",          # ★ NOT `code`
    "BD_BANKACCSUB": "pk_bankaccsub code accnum name accname pk_currtype pk_bankaccbas",
    "BD_BANKACCBAS": "pk_bankaccbas pk_bankdoc",
    "BD_BANKDOC": "pk_bankdoc name",
}


def _load_env(path: str) -> dict:
    cfg: dict = {}
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            cfg[key.strip()] = re.sub(r"\s+#.*$", "", val).strip().strip('"').strip("'")
    return cfg


def main(env_path: str = "/env/.env") -> int:
    import oracledb
    cfg = _load_env(env_path)
    con = oracledb.connect(
        user=cfg["NC65_USER"], password=cfg["NC65_PASSWORD"],
        dsn=oracledb.makedsn(cfg["NC65_HOST"], int(cfg["NC65_PORT"]),
                             service_name=cfg["NC65_SERVICE"]))
    bad = 0
    try:
        cur = con.cursor()
        for table, cols in EXPECTED.items():
            cur.execute("select lower(column_name) from all_tab_columns "
                        "where owner = 'NCSC' and table_name = :t", t=table)
            have = {row[0] for row in cur}
            if not have:
                print(f"MISSING TABLE  {table}")
                bad += 1
                continue
            missing = [c for c in cols.split() if c not in have]
            if missing:
                print(f"MISSING COLUMN {table}: {missing}")
                bad += 1
            else:
                print(f"ok             {table} ({len(cols.split())} columns)")
    finally:
        con.close()
    print("\nFAILED" if bad else "\nall columns present")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/env/.env"))
