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

CHART = "1001A1100000003CG6GD"          # Canada Royal Milk 根科目表
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
    "0004": "__party__",                # derived per account — see derive_party_dim
    "0006": "item", "0012": "item_category", "0010": "project",
    "D45": "project_type", "CRM02": "government_grant_project",
    "fa01": "asset_category", "D47": "tax_code", "0022": "bank_category",
    "0023": "bank", "0011": "bank_account", "0044": "country_region",
    "0002": "employee", "D09": "sales_type", "CRM01": "credit_card",
}

# Neither a supplier nor a customer: 4001 实收资本 is a shareholder,
# 1511/1512 长期股权投资 an investee. Resolve to the unexpandable `partner`
# rather than forcing them into customer (user decision 2026-07-15).
PARTY_EXCEPTIONS = {"4001", "1511", "1512"}

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


def derive_party_dim(account_code: str, account_type: str) -> str:
    """客商 (0004) covers vendors and customers; we keep them apart. Decide from
    the account's nature. `partner` is not in account_balance._dimensions(), so
    it surfaces as supported:false — i.e. not expandable, which beats expanding
    it wrongly."""
    if account_code in PARTY_EXCEPTIONS:
        return "partner"
    if account_type == "asset":
        return "customer"               # receivables — they owe us
    if account_type == "liability":
        return "supplier"               # payables — we owe them
    if account_type == "expense":
        return "supplier"               # cost (5) and P&L debit
    if account_type == "revenue":
        return "customer"               # P&L credit
    return "partner"                    # equity and anything unforeseen


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
