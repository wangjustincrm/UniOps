"""Read-only Flux WMS extracts for the transaction-history mirror (THICK mode).

`oracledb.init_oracle_client` may run once per process — guarded below, same
as mrp-api's WMS reader. Every query is scoped to the Canada warehouse and
customer and is a plain SELECT.
"""
from __future__ import annotations

from app.core.config import settings

_client_ready = False

# Transaction types that change on-hand quantity (design §4.7). PA/PK/MV are
# location moves, HD/RL holds and releases, 90/99 document close/cancel events.
QTY_TXN_TYPES = ("IN", "SO", "AD", "TR")
CODE_TYPES = ("TRN_TYP", "DOC_TYP", "ASN_TYP", "SO_TYP", "ADJ_TYP")


def wms_configured() -> bool:
    return all([settings.wms_host, settings.wms_service, settings.wms_user, settings.wms_password])


def _ensure_thick() -> None:
    global _client_ready
    if not _client_ready:
        import oracledb
        oracledb.init_oracle_client(lib_dir=settings.oracle_client_lib)
        _client_ready = True


def _rows(cur, sql: str, params: dict | None = None) -> list[dict]:
    cur.execute(sql, params or {})
    names = [c[0].lower() for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _count(cur, sql: str, params: dict | None = None) -> int:
    cur.execute(sql, params or {})
    return int(cur.fetchone()[0])


def extract_all() -> dict:
    import oracledb
    _ensure_thick()
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.wms_host, settings.wms_port, service_name=settings.wms_service)
    con = oracledb.connect(user=settings.wms_user, password=settings.wms_password, dsn=dsn)
    p = {"wh": settings.WMS_WAREHOUSE, "cust": settings.WMS_CUSTOMER}
    types = ",".join(f"'{t}'" for t in QTY_TXN_TYPES)
    try:
        cur = con.cursor()
        cur.arraysize = 5000
        t: dict[str, list[dict]] = {}
        filtered: dict[str, int] = {}

        t["txn"] = _rows(cur, f"""
            select transactionid, transactiontype, doctype, docno, doclineno,
                   fmsku, fmlotnum, fmqty_each, tosku, tolotnum, toqty_each,
                   transactiontime, reasoncode, reason, addwho
            from act_transaction_log
            where warehouseid = :wh and fmcustomerid = :cust
              and status = '99' and transactiontype in ({types})""", p)
        filtered["txn_cancelled"] = _count(cur, """
            select count(*) from act_transaction_log
            where warehouseid = :wh and fmcustomerid = :cust and status <> '99'""", p)
        filtered["txn_no_qty_effect"] = _count(cur, f"""
            select count(*) from act_transaction_log
            where warehouseid = :wh and fmcustomerid = :cust
              and status = '99' and transactiontype not in ({types})""", p)

        t["lot"] = _rows(cur, """
            select sku, lotnum, lotatt01, lotatt02, lotatt03, lotatt04, lotatt05, lotatt08
            from inv_lot_att where customerid = :cust""", {"cust": settings.WMS_CUSTOMER})

        t["doc"] = (
            _rows(cur, """
                select 'ASN' doc_kind, asnno docno, asntype doc_type, asnreference1 ref1,
                       asnreference2 ref2, asnreference3 ref3, asnreference4 ref4, asnreference5 ref5,
                       pono, null udf01, hedi03, null reasoncode, null reason, addtime added
                from doc_asn_header where warehouseid = :wh and customerid = :cust""", p)
            + _rows(cur, """
                select 'SO' doc_kind, orderno docno, ordertype doc_type, soreference1 ref1,
                       soreference2 ref2, soreference3 ref3, soreference4 ref4, soreference5 ref5,
                       null pono, udf01, null hedi03, null reasoncode, null reason, addtime added
                from doc_order_header where warehouseid = :wh and customerid = :cust""", p)
            + _rows(cur, """
                select 'AJ' doc_kind, adjno docno, adjtype doc_type, null ref1, null ref2, null ref3,
                       null ref4, null ref5, null pono, null udf01, null hedi03, reasoncode, reason,
                       addtime added
                from doc_adj_header where warehouseid = :wh and customerid = :cust""", p)
            + _rows(cur, """
                select 'TR' doc_kind, tdocno docno, tdoctype doc_type, null ref1, null ref2, null ref3,
                       null ref4, null ref5, null pono, null udf01, null hedi03, reasoncode, reason,
                       addtime added
                from doc_transfer_header where warehouseid = :wh""", {"wh": settings.WMS_WAREHOUSE})
        )

        codes = ",".join(f"'{c}'" for c in CODE_TYPES)
        t["code"] = _rows(cur, f"""
            select codetype, codeid, languageid, codedescr descr from bsm_code_ml
            where codetype in ({codes})""")

        reference = {"inv_lot": _rows(cur, """
            select sku, lotnum, sum(qty) qty from inv_lot
            where warehouseid = :wh and customerid = :cust group by sku, lotnum""", p)}
        return {"tables": t, "filtered": filtered, "reference": reference}
    finally:
        con.close()
