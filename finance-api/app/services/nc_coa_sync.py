"""NC65 → UniOps COA + aux sync.

Ported from scripts/nc_migration/coa_import.py + aux_items_import.py, with the
inference replaced by facts (see the 2026-07-15 spec §2-§3): NC states the
account direction, the leaf flag and the account type outright, and the old
importers guessed all three from the Chinese code prefix. 18 accounts were
stored with the wrong normal_balance as a result.

Every unmapped value raises. There is deliberately no fallback default — the
old `return "asset"` catch-all is how the wrong data got in.
"""
from dataclasses import dataclass, field

from app.core.config import settings
from app.services.nc_sync import nc_configured  # same NC_* env gate

__all__ = ["nc_configured"]

CHART = "1001A1100000003CGN3F"          # CRM0001 加拿大皇家妙克 — 凭证所在的科目表(spec §2.0)
TILDE = "~"                             # NC's empty sentinel — NOT null


class NcMappingError(ValueError):
    """An NC value we refuse to guess about. Aborts the whole sync."""


# ── NC BD_ACCTYPE.code → our account_type ────────────────────────────────────
# 6 (损益) covers both revenue and expense; NC does not split it, balanorient does.
ACCOUNT_TYPE_BY_NC = {"1": "asset", "2": "liability", "4": "equity", "5": "expense"}

# ── NC BD_ACCASSITEM.code → our dim_code (spec §3.4) ─────────────────────────
# Keyed on CODE, not name: the old NAME_MAP matched Chinese substrings and put
# both 项目类型 and 政府拨款项目 onto `project` because both contain 项目.
AUX_ITEM_MAP = {
    "ra01": "cost_center", "0001": "department", "0008": "income_expense_item",
    "0019": "supplier", "0017": "customer",
    "0004": "partner",                  # 客商 = vendors ∪ customers (see spec §3.4.1)
    "0006": "item", "0012": "item_category", "0010": "project",
    "D45": "project_type", "CRM02": "government_grant_project",
    "fa01": "asset_category", "D47": "tax_code", "0022": "bank_category",
    "0023": "bank", "0011": "bank_account", "0044": "country_region",
    "0002": "employee", "D09": "sales_type", "CRM01": "credit_card",
}

# The columns the sync owns. Everything else on chart_of_accounts is UniOps'
# and must never appear in an UPDATE (spec §3.1/§3.2).
NC_OWNED_FIELDS = ("name", "account_type", "normal_balance", "is_postable",
                   "parent_code", "quantity_accounting", "default_uom",
                   "default_currency", "is_off_balance")


def clean(v: str | None) -> str | None:
    """NC stores empty as the string '~'. Everything else treats it as a value."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s == "" or s == TILDE else s


def map_normal_balance(balanorient: int) -> str:
    if balanorient == 0:
        return "debit"
    if balanorient == 1:
        return "credit"
    raise NcMappingError(f"unknown BALANORIENT {balanorient!r} (expected 0 or 1)")


def map_account_type(acctype_code: str, balanorient: int) -> str:
    code = (acctype_code or "").strip()
    if code == "6":                     # 损益: credit = revenue, debit = expense
        return "revenue" if map_normal_balance(balanorient) == "credit" else "expense"
    try:
        return ACCOUNT_TYPE_BY_NC[code]
    except KeyError:
        raise NcMappingError(
            f"unregistered BD_ACCTYPE.code {code!r}; a human must decide its mapping"
        ) from None


def map_aux_item(nc_item_code: str) -> str:
    code = (nc_item_code or "").strip()
    try:
        return AUX_ITEM_MAP[code]
    except KeyError:
        raise NcMappingError(
            f"unregistered BD_ACCASSITEM.code {code!r}; add it to AUX_ITEM_MAP"
        ) from None


def map_account(row: dict, *, uom: dict, ccy: dict, acctype: dict,
                pk2code: dict) -> dict:
    """One raw NC row (BD_ACCOUNT joined to BD_ACCASOA) -> chart_of_accounts dict."""
    code = row["code"].strip()

    # endflag lives on BD_ACCASOA. Missing row = anomaly (measured 360 == 360),
    # and both is_postable and name depend on it — do not degrade to pid-inference.
    if row.get("endflag") is None:
        raise NcMappingError(f"account {code}: BD_ACCASOA row missing")

    atype_code = acctype.get(row["acctype_pk"])
    if atype_code is None:
        raise NcMappingError(f"account {code}: BD_ACCTYPE pk {row['acctype_pk']!r} not found")
    account_type = map_account_type(atype_code, row["balanorient"])

    unit_pk = clean(row.get("unit_pk"))
    default_uom = None
    if unit_pk is not None:
        default_uom = uom.get(unit_pk)
        if default_uom is None:
            raise NcMappingError(f"account {code}: BD_MEASDOC pk {unit_pk!r} not found")

    ccy_pk = clean(row.get("currency_pk"))
    default_currency = None
    if ccy_pk is not None:
        default_currency = ccy.get(ccy_pk)
        if default_currency is None:
            raise NcMappingError(f"account {code}: BD_CURRTYPE pk {ccy_pk!r} not found")

    pid = clean(row.get("pid"))
    name = (clean(row.get("name2_soa")) or clean(row.get("name_soa"))
            or clean(row.get("name2_acct")) or clean(row.get("name_acct")) or code)

    return {
        "code": code,
        "name": name,
        "account_type": account_type,
        "normal_balance": map_normal_balance(row["balanorient"]),
        "is_postable": row["endflag"] == "Y",
        "parent_code": pk2code.get(pid) if pid else None,
        # 数量核算 is driven by UNIT, not the QUANTITY column — proven by an
        # exhaustive two-table column diff against the NC UI (spec §2.4).
        "quantity_accounting": unit_pk is not None,
        "default_uom": default_uom,
        "default_currency": default_currency,
        "is_off_balance": row.get("outflag") == "Y",
    }


@dataclass
class CoaDiff:
    to_insert: list = field(default_factory=list)
    to_update: list = field(default_factory=list)
    to_deactivate: list = field(default_factory=list)
    unchanged: int = 0


@dataclass
class AuxDiff:
    to_insert: list = field(default_factory=list)
    to_delete: list = field(default_factory=list)
    unchanged: int = 0


def diff(nc_accounts: list[dict], db_accounts: list[dict]) -> CoaDiff:
    """Pure. Three mutually exclusive buckets + a count.

    Reactivation is NOT its own bucket: is_active false->true is a field change
    like any other, so it rides in to_update with reactivated=True. An account
    that was both renamed and re-enabled appears exactly once.
    """
    by_code = {a["code"]: a for a in db_accounts}
    out = CoaDiff()
    for nc in nc_accounts:
        cur = by_code.get(nc["code"])
        if cur is None:
            out.to_insert.append(nc)
            continue
        # Only NC_OWNED_FIELDS are ever compared — UniOps' own columns
        # (subtype/aux_dimensions/effective_*/...) must not enter an UPDATE.
        changes = {f: (cur.get(f), nc[f]) for f in NC_OWNED_FIELDS
                   if cur.get(f) != nc[f]}
        reactivated = cur.get("is_active") is False
        if changes or reactivated:
            out.to_update.append({"code": nc["code"], "changes": changes,
                                  "reactivated": reactivated, "values": nc})
        else:
            out.unchanged += 1
    nc_codes = {a["code"] for a in nc_accounts}
    for cur in db_accounts:
        if cur["code"] not in nc_codes and cur.get("is_active") is not False:
            out.to_deactivate.append({"code": cur["code"], "name": cur.get("name")})
    return out


def _aux_key(r: dict) -> tuple:
    return (r["account_code"], r["dim_code"], r["seq"], r["required"])


def diff_aux(nc_aux: list[dict], db_aux: list[dict]) -> AuxDiff:
    """Pure. coa_aux_items is a plain NC projection with no UniOps-side data and
    nothing referencing it, so any difference is a replace (spec §5)."""
    nc_keys = {_aux_key(r): r for r in nc_aux}
    db_keys = {_aux_key(r): r for r in db_aux}
    out = AuxDiff()
    out.to_insert = [r for k, r in nc_keys.items() if k not in db_keys]
    out.to_delete = [r for k, r in db_keys.items() if k not in nc_keys]
    out.unchanged = len(set(nc_keys) & set(db_keys))
    return out


import uuid as _uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values, register_uuid
from sqlalchemy.engine.url import make_url

register_uuid()


@dataclass
class NcCoaExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    accounts: list = field(default_factory=list)
    aux: list = field(default_factory=list)
    pk2code: dict = field(default_factory=dict)
    uom: dict = field(default_factory=dict)
    ccy: dict = field(default_factory=dict)
    acctype: dict = field(default_factory=dict)


def fetch_coa_from_nc() -> NcCoaExtract:
    """Live NC read (oracledb, read-only). Blocking — call via run_in_executor."""
    import oracledb
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port,
                           service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("select pk_measdoc, code from NCSC.BD_MEASDOC")
        uom = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_currtype, code from NCSC.BD_CURRTYPE")
        ccy = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_acctype, code from NCSC.BD_ACCTYPE")
        acctype = {pk: code for pk, code in cur.fetchall()}

        # Accounts are DEFINED in the root chart and inherited; they are
        # ENABLED and CONFIGURED per chart via BD_ACCASOA (one account can carry
        # up to 19 of them). So join BD_ACCASOA on OUR chart and read enablement
        # from it — a.enablestate is the root's answer and would admit 10
        # accounts CRM0001 has actually retired. endflag/name live only on ACCASOA.
        cur.execute(
            "select a.pk_account, a.code, a.pid, a.pk_acctype, a.balanorient, "
            "       a.unit, a.currency, a.outflag, soa.endflag, "
            "       soa.name, soa.name2, a.name, a.name2 "
            "from NCSC.BD_ACCOUNT a "
            "join NCSC.BD_ACCASOA soa on soa.pk_account = a.pk_account "
            "where soa.pk_accchart = :c and soa.enablestate = 2", c=CHART)
        accounts = [{"pk": r[0], "code": r[1], "pid": r[2], "acctype_pk": r[3],
                     "balanorient": r[4], "unit_pk": r[5], "currency_pk": r[6],
                     "outflag": r[7], "endflag": r[8], "name_soa": r[9],
                     "name2_soa": r[10], "name_acct": r[11], "name2_acct": r[12]}
                    for r in cur.fetchall()]

        # Join pk_accasoa, NOT pk_coveraccasoa: the latter returns nothing for
        # 101201/1402 against the NC UI (spec §2.0.1). Scope to OUR chart's
        # ACCASOA rows and its enablement, mirroring the accounts query.
        cur.execute(
            "select acc.code, item.code, a.id, a.isempty "
            "from NCSC.BD_ACCASS a "
            "join NCSC.BD_ACCASOA soa on soa.pk_accasoa = a.pk_accasoa "
            "join NCSC.BD_ACCOUNT acc on acc.pk_account = soa.pk_account "
            "join NCSC.BD_ACCASSITEM item on item.pk_accassitem = a.pk_entity "
            "where soa.pk_accchart = :c and soa.enablestate = 2", c=CHART)
        aux = [{"account_code": r[0], "nc_item_code": r[1], "seq": int(r[2]),
                "isempty": r[3]} for r in cur.fetchall()]
    finally:
        con.close()
    pk2code = {r["pk"]: r["code"].strip() for r in accounts}
    return NcCoaExtract(accounts=accounts, aux=aux, pk2code=pk2code,
                        uom=uom, ccy=ccy, acctype=acctype)


def build(extract: NcCoaExtract) -> tuple[list[dict], list[dict]]:
    """Map + guard. Raises rather than returning anything we'd have to guess at."""
    # Zero-row guards: with "absent from NC = deactivate", an empty read would
    # deactivate the entire 360-account chart. A wrong chart pk looks exactly
    # like this.
    if not extract.accounts:
        raise NcMappingError("NC returned no accounts; refusing to deactivate the chart")
    if not extract.aux:
        raise NcMappingError("NC returned no aux rows; refusing to wipe coa_aux_items")

    accounts = [map_account(r, uom=extract.uom, ccy=extract.ccy,
                            acctype=extract.acctype, pk2code=extract.pk2code)
                for r in extract.accounts]
    known = {a["code"] for a in accounts}

    aux = []
    for r in extract.aux:
        acct = r["account_code"].strip()
        # An aux row outside our account set means the two queries disagree about
        # the chart — a bug, not data to paper over. coa_aux_items has no FK, so
        # it would otherwise land silently as an orphan.
        if acct not in known:
            raise NcMappingError(f"aux row references unknown account {acct}")
        if r["isempty"] not in ("Y", "N"):
            raise NcMappingError(
                f"account {acct}: unexpected BD_ACCASS.ISEMPTY {r['isempty']!r}")
        aux.append({"account_code": acct,
                    "dim_code": map_aux_item(r["nc_item_code"]),
                    "seq": r["seq"],
                    "required": r["isempty"] == "N"})
    return accounts, aux


def _pg_dsn() -> str:
    u = make_url(settings.database_url)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


_UPDATE_SQL = (
    "update chart_of_accounts set "
    + ", ".join(f"{f} = %({f})s" for f in NC_OWNED_FIELDS)
    + ", is_active = true, updated_at = now() where code = %(code)s"
)


def apply(coa_diff: CoaDiff, aux_rows: list[dict], aux_diff: AuxDiff,
          dsn: str, started_by) -> dict:
    """Single transaction, all-or-nothing. The audit row is committed SEPARATELY
    afterwards — inside the same transaction a rollback would take the failure
    record with it, exactly when it is most needed."""
    started_at = datetime.now(timezone.utc)
    counts = {"accounts_inserted": len(coa_diff.to_insert),
              "accounts_updated": len(coa_diff.to_update),
              "accounts_deactivated": len(coa_diff.to_deactivate),
              "aux_items_inserted": len(aux_diff.to_insert),
              "aux_items_deleted": len(aux_diff.to_delete)}
    err = None
    con = psycopg2.connect(dsn)
    con.autocommit = False
    try:
        cur = con.cursor()
        for a in coa_diff.to_insert:
            cur.execute(
                "insert into chart_of_accounts (id, code, name, account_type, "
                " normal_balance, is_postable, parent_code, quantity_accounting, "
                " default_uom, default_currency, is_off_balance, is_active, "
                " aux_dimensions, created_at, updated_at) "
                "values (gen_random_uuid(), %(code)s, %(name)s, %(account_type)s, "
                " %(normal_balance)s, %(is_postable)s, %(parent_code)s, "
                " %(quantity_accounting)s, %(default_uom)s, %(default_currency)s, "
                " %(is_off_balance)s, true, '[]'::jsonb, now(), now())", a)
        for u in coa_diff.to_update:
            cur.execute(_UPDATE_SQL, u["values"])
        for d in coa_diff.to_deactivate:
            cur.execute("update chart_of_accounts set is_active = false, "
                        "updated_at = now() where code = %s", (d["code"],))
        # aux is a pure NC projection — replace wholesale (spec §5)
        if aux_diff.to_insert or aux_diff.to_delete:
            cur.execute("delete from coa_aux_items")
            execute_values(cur,
                "insert into coa_aux_items (id, account_code, dim_code, seq, "
                " required, created_at, updated_at) values %s",
                [(_uuid.uuid4(), r["account_code"], r["dim_code"], r["seq"],
                  r["required"]) for r in aux_rows],
                template="(%s,%s,%s,%s,%s, now(), now())", page_size=2000)
        con.commit()
    except Exception as e:
        con.rollback()
        err = str(e)[:2000]
        raise
    finally:
        con.close()
        _write_audit(dsn, started_by, started_at, counts, err)
    return counts


def _write_audit(dsn, started_by, started_at, counts, err) -> None:
    """Separate connection + transaction so it survives a rollback of the main one."""
    con = psycopg2.connect(dsn)
    con.autocommit = True
    try:
        con.cursor().execute(
            "insert into coa_sync_runs (id, started_by, started_at, finished_at, "
            " accounts_inserted, accounts_updated, accounts_deactivated, "
            " aux_items_inserted, aux_items_deleted, error, created_at, updated_at) "
            "values (%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,now(),now())",
            (_uuid.uuid4(), started_by, started_at,
             0 if err else counts["accounts_inserted"],
             0 if err else counts["accounts_updated"],
             0 if err else counts["accounts_deactivated"],
             0 if err else counts["aux_items_inserted"],
             0 if err else counts["aux_items_deleted"], err))
    finally:
        con.close()
