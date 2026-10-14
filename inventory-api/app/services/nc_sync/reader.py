"""Read-only NC65 extracts for the Inventory mirrors (thin mode).

Every query here is a plain SELECT scoped to the Canada slice
(settings.NC_STOCK_ORG / NC_COST_REGION / NC_ACCOUNTING_BOOK). Row shaping —
'~' → NULL, CHAR padding, date parsing — is NOT done here; it lives in
transform.py so it can be unit-tested without an Oracle connection.

Each `fetch_*` returns a list of dicts keyed by lower-case column name. The
whole extract runs on one connection inside `extract_all`, which is what the
sync service calls (in a worker thread — oracledb is synchronous).

Verified against the live instance 2026-10-14 (design doc §4); the scripts
live in /srv/uniops/nc65_introspect/inventory_2026_10_14/.
"""
from __future__ import annotations

from app.core.config import settings

# '物料基本信息' auxiliary-accounting type. A GL_FREEVALUE.typevalueN slot whose
# first 20 characters equal this holds the material pk in characters 21-40.
MATERIAL_AUX_TYPE = "0001Z0100000000005CX"


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


def _connect():
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    return oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)


def _rows(cur, sql: str, params: dict | None = None) -> list[dict]:
    cur.execute(sql, params or {})
    names = [c[0].lower() for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _count(cur, sql: str, params: dict | None = None) -> int:
    # fetch_decimals=True makes count(*) a Decimal, which JSONB cannot store.
    cur.execute(sql, params or {})
    return int(cur.fetchone()[0])


def _gl_account_filter() -> str:
    # Bound as literals: the prefixes come from settings, never from a request.
    prefixes = settings.NC_GL_INVENTORY_ACCOUNT_PREFIXES
    for p in prefixes:
        if not p.isdigit():
            raise ValueError(f"GL account prefix {p!r} is not numeric")
    return " or ".join(f"a.code like '{p}%'" for p in prefixes)


def _material_aux_expr() -> str:
    """Pick the material pk out of whichever of the nine typevalue slots holds
    the material auxiliary. NULL when the line has no such auxiliary."""
    slots = ", ".join(
        f"case when substr(f.typevalue{i},1,20)='{MATERIAL_AUX_TYPE}' "
        f"then substr(f.typevalue{i},21,20) end"
        for i in range(1, 10)
    )
    return f"coalesce({slots})"


def extract_all() -> dict:
    """One connection, every extract the NC sync needs. Returns
    {'tables': {name: [rows]}, 'filtered': {name: count}, 'reference': {...}}.

    `filtered` counts rows NC has that we deliberately do not mirror, so the
    sync state can show them (design §3: nothing disappears silently).
    `reference` holds NC's own pre-aggregated figures the tie-out compares the
    mirror against — fetched in the same session so both sides describe the
    same moment."""
    org, region, book = settings.NC_STOCK_ORG, settings.NC_COST_REGION, settings.NC_ACCOUNTING_BOOK
    con = _connect()
    try:
        cur = con.cursor()
        cur.arraysize = 5000
        t: dict[str, list[dict]] = {}
        filtered: dict[str, int] = {}

        t["ic_flow"] = _rows(cur, """
            select pk_flow, cwarehouseid, cotherwhid, cmaterialoid, vbatchcode, pk_batchcode,
                   cstateid, ninnum, noutnum, dbizdate, cbilltypecode, vtrantypecode,
                   ctrantypeid, vbillcode, cgeneralhid, cgeneralbid, vsourcebillcode,
                   vfirstbillcode, vheaduserdef6, vheaduserdef13, fbillflag, taudittime, ts
            from ic_flow where pk_org = :org and nvl(dr,0) = 0""", {"org": org})
        filtered["ic_flow_deleted"] = _count(cur,
            "select count(*) from ic_flow where pk_org = :org and nvl(dr,0) <> 0", {"org": org})

        t["ic_onhand"] = _rows(cur, """
            select d.pk_onhanddim, d.cwarehouseid, d.cmaterialoid, d.vbatchcode, d.pk_batchcode,
                   d.cstateid, d.clocationid, d.cvendorid, n.nonhandnum, n.nlocknum, n.nrsnum
            from ic_onhanddim d join ic_onhandnum n on n.pk_onhanddim = d.pk_onhanddim
            where d.pk_org = :org
              and (nvl(n.nonhandnum,0) <> 0 or nvl(n.nlocknum,0) <> 0 or nvl(n.nrsnum,0) <> 0)""",
            {"org": org})

        # Batches referenced by any IC flow row of this org — the batch master
        # itself is group-wide and mostly other factories'.
        t["batch"] = _rows(cur, """
            select b.pk_batchcode, b.cmaterialoid, b.vbatchcode, b.vvendbatchcode,
                   b.dproducedate, b.dvalidate, b.dinbounddate
            from scm_batchcode b
            where nvl(b.dr,0) = 0 and b.pk_batchcode in (
                select pk_batchcode from ic_flow where pk_org = :org and pk_batchcode is not null
                union select pk_batchcode from ic_onhanddim where pk_org = :org and pk_batchcode is not null)""",
            {"org": org})

        t["warehouse"] = _rows(cur, """
            select pk_stordoc, code, name, iscalculatedinvcost, enablestate
            from bd_stordoc where pk_org = :org and nvl(dr,0) = 0""", {"org": org})

        t["store_state"] = _rows(cur, "select pk_storestate, vcode, vname from ic_storestate")

        t["material"] = _rows(cur, """
            select m.pk_material, m.code, m.name, m.ename, u.code uom_code,
                   coalesce(u.ename, u.name) uom_name, c.code class_code, c.name class_name
            from bd_material m
            left join bd_measdoc u on u.pk_measdoc = m.pk_measdoc
            left join bd_marbasclass c on c.pk_marbasclass = m.pk_marbasclass
            where nvl(m.dr,0) = 0""")

        t["trantype"] = _rows(cur, """
            select pk_billtypeid, pk_billtypecode code, billtypename name, billtypename2 name_alt
            from bd_billtype where pk_billtypeid in (
                select ctrantypeid from ic_flow where pk_org = :org
                union select ctrantypeid from ia_detailledger where pk_org = :region)""",
            {"org": org, "region": region})

        t["ia_ledger"] = _rows(cur, """
            select cdetailledgerid, cinventoryid, caccountperiod, dbizdate, fdispatchflag, nnum, nmny,
                   vbatchcode, cstordocid, cbilltypecode, ctrantypeid, vbillcode, vsrccode, csrcbid,
                   bestimateflag
            from ia_detailledger
            where pk_org = :region and pk_book = :book and nvl(dr,0) = 0""",
            {"region": region, "book": book})
        filtered["ia_ledger_deleted"] = _count(cur, """
            select count(*) from ia_detailledger
            where pk_org = :region and pk_book = :book and nvl(dr,0) <> 0""",
            {"region": region, "book": book})

        t["ia_monthnab"] = _rows(cur, """
            select cmonthnabid, cinventoryid, caccountperiod, nabnum, nabmny
            from ia_monthnab where pk_org = :region and pk_book = :book and nvl(dr,0) = 0""",
            {"region": region, "book": book})

        t["ia_period"] = _rows(cur, """
            select caccountid, caccountperiod from ia_account
            where pk_org = :region and pk_book = :book and nvl(dr,0) = 0""",
            {"region": region, "book": book})

        acct = _gl_account_filter()
        t["gl_line"] = _rows(cur, f"""
            select d.pk_detail, d.pk_voucher, v.year, v.period, v.num voucher_num, v.prepareddate,
                   v.tallydate, v.pk_system, v.voucherkind, a.code account_code, a.name account_name,
                   {_material_aux_expr()} material_pk,
                   d.localdebitamount debit_amount, d.localcreditamount credit_amount,
                   d.debitquantity debit_qty, d.creditquantity credit_qty, d.explanation
            from gl_detail d
            join gl_voucher v on v.pk_voucher = d.pk_voucher
            join bd_account a on a.pk_account = d.pk_account
            left join gl_freevalue f on f.freevalueid = d.assid
            where v.pk_accountingbook = :book and nvl(v.dr,0) = 0 and nvl(d.dr,0) = 0
              and nvl(v.discardflag,'N') <> 'Y' and ({acct})""", {"book": book})
        filtered["gl_line_deleted_or_discarded"] = _count(cur, f"""
            select count(*) from gl_detail d
            join gl_voucher v on v.pk_voucher = d.pk_voucher
            join bd_account a on a.pk_account = d.pk_account
            where v.pk_accountingbook = :book and ({acct})
              and (nvl(v.dr,0) <> 0 or nvl(d.dr,0) <> 0 or nvl(v.discardflag,'N') = 'Y')""",
            {"book": book})

        # NC's own GL balance, per year × account, POSTED vouchers only:
        # GL_BALANCE is maintained on posting (verified 2026-10-14 — it equals
        # the posted detail lines to the cent on every inventory account, and
        # ignores October's 169 unposted vouchers). Joined through
        # bd_accasoa: GL_BALANCE.pk_account does not join bd_account directly.
        reference = {
            "gl_balance": _rows(cur, f"""
                select b.year, a.code account_code,
                       sum(nvl(b.localdebitamount,0) - nvl(b.localcreditamount,0)) amount,
                       sum(nvl(b.debitquantity,0) - nvl(b.creditquantity,0)) qty
                from gl_balance b
                join bd_accasoa s on s.pk_accasoa = b.pk_accasoa
                join bd_account a on a.pk_account = s.pk_account
                where b.pk_accountingbook = :book and nvl(b.dr,0) = 0 and ({acct})
                group by b.year, a.code""", {"book": book}),
        }
        return {"tables": t, "filtered": filtered, "reference": reference}
    finally:
        con.close()
