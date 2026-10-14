"""Pure row shaping between NC's Oracle rows and the inv_nc_* mirrors.

NC conventions this undoes:
* CHAR(n) columns come back space-padded ('IA                  ').
* '~' (often padded) is NC's "no value"; so is the empty string — and in
  Oracle '' *is* NULL, so a filter like `x not in ('', '~')` is always NULL
  (see feedback_uniops_nc_aux_line_count_and_oracle_null). Normalising to a
  real NULL here means every query downstream can say `IS NULL` and mean it.
* Dates are 'YYYY-MM-DD HH:MI:SS' strings (sometimes date-only). They are
  parsed to naive datetimes in NC's own wall clock; which zone that clock is
  in is decided at query time (design D14).
* Y/N flags are CHAR(1).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable


def clean_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s == "~":
        return None
    return s


def parse_nc_datetime(v: Any) -> datetime | None:
    """'2026-08-31 23:15:00' → datetime; '2026-08-31' → midnight. Anything
    unparseable becomes None rather than failing the whole sync — the raw
    string is mirrored next to it, so nothing is lost."""
    s = clean_str(v)
    if s is None:
        return None
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def parse_nc_date(v: Any) -> date | None:
    dt = parse_nc_datetime(v)
    return dt.date() if dt else None


def yn(v: Any) -> bool | None:
    s = clean_str(v)
    if s is None:
        return None
    return s.upper() == "Y"


def num(v: Any) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _shape(row: dict, spec: dict[str, Callable[[Any], Any]]) -> dict:
    return {k: fn(row.get(k)) for k, fn in spec.items()}


S, N, I = clean_str, num, to_int

IC_FLOW = {
    "pk_flow": S, "cwarehouseid": S, "cotherwhid": S, "cmaterialoid": S, "vbatchcode": S,
    "pk_batchcode": S, "cstateid": S, "ninnum": N, "noutnum": N, "dbizdate": S,
    "cbilltypecode": S, "vtrantypecode": S, "ctrantypeid": S, "vbillcode": S, "cgeneralhid": S,
    "cgeneralbid": S, "vsourcebillcode": S, "vfirstbillcode": S, "vheaduserdef6": S,
    "vheaduserdef13": S, "fbillflag": I, "taudittime": S, "ts": S,
}
IC_ONHAND = {
    "pk_onhanddim": S, "cwarehouseid": S, "cmaterialoid": S, "vbatchcode": S, "pk_batchcode": S,
    "cstateid": S, "clocationid": S, "cvendorid": S, "nonhandnum": N, "nlocknum": N, "nrsnum": N,
}
BATCH = {"pk_batchcode": S, "cmaterialoid": S, "vbatchcode": S, "vvendbatchcode": S,
         "dproducedate": parse_nc_date, "dvalidate": parse_nc_date, "dinbounddate": parse_nc_date}
WAREHOUSE = {"pk_stordoc": S, "code": S, "name": S, "enablestate": I}
STORE_STATE = {"pk_storestate": S, "vcode": S, "vname": S}
TRANTYPE = {"pk_billtypeid": S, "code": S, "name": S, "name_alt": S}
IA_LEDGER = {
    "cdetailledgerid": S, "cinventoryid": S, "caccountperiod": S, "dbizdate": S,
    "fdispatchflag": I, "nnum": N, "nmny": N, "vbatchcode": S, "cstordocid": S,
    "cbilltypecode": S, "ctrantypeid": S, "vbillcode": S, "vsrccode": S, "csrcbid": S,
}
IA_MONTHNAB = {"cmonthnabid": S, "cinventoryid": S, "caccountperiod": S, "nabnum": N, "nabmny": N}
IA_PERIOD = {"caccountid": S, "caccountperiod": S}
GL_LINE = {
    "pk_detail": S, "pk_voucher": S, "year": S, "period": S, "voucher_num": I, "prepareddate": S,
    "tallydate": S, "pk_system": S, "voucherkind": I, "account_code": S, "account_name": S,
    "material_pk": S, "debit_amount": N, "credit_amount": N, "debit_qty": N, "credit_qty": N,
    "explanation": S,
}


def ic_flow(row: dict) -> dict:
    out = _shape(row, IC_FLOW)
    out["biz_time"] = parse_nc_datetime(out["dbizdate"])
    return out


def ic_onhand(row: dict) -> dict:
    return _shape(row, IC_ONHAND)


def batch(row: dict) -> dict:
    return _shape(row, BATCH)


def warehouse(row: dict) -> dict:
    out = _shape(row, WAREHOUSE)
    # NC's own default for the flag is Y; only an explicit N excludes a
    # warehouse from costing.
    out["iscalculatedinvcost"] = yn(row.get("iscalculatedinvcost")) is not False
    return out


def store_state(row: dict) -> dict:
    return _shape(row, STORE_STATE)


def material(row: dict) -> dict:
    name_local = clean_str(row.get("name"))
    return {
        "pk_material": clean_str(row.get("pk_material")),
        "code": clean_str(row.get("code")),
        "name": clean_str(row.get("ename")) or name_local,
        "name_local": name_local,
        "uom_code": clean_str(row.get("uom_code")),
        "uom_name": clean_str(row.get("uom_name")),
        "class_code": clean_str(row.get("class_code")),
        "class_name": clean_str(row.get("class_name")),
    }


def trantype(row: dict) -> dict:
    return _shape(row, TRANTYPE)


def ia_ledger(row: dict) -> dict:
    out = _shape(row, IA_LEDGER)
    out["biz_time"] = parse_nc_datetime(out["dbizdate"])
    out["bestimateflag"] = yn(row.get("bestimateflag"))
    return out


def ia_monthnab(row: dict) -> dict:
    return _shape(row, IA_MONTHNAB)


def ia_period(row: dict) -> dict:
    return _shape(row, IA_PERIOD)


def gl_line(row: dict) -> dict:
    out = _shape(row, GL_LINE)
    out["prepared_time"] = parse_nc_datetime(out["prepareddate"])
    return out
