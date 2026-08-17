"""Read-only NC65 Oracle reader for purchase orders + arrivals."""
from app.core.config import settings


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


def _connect():
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    return oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)


def select_incremental_order_pks(order_rows, arrival_rows, watermark) -> set:
    """Pure decision helper: given the candidate order rows and arrival rows for
    an incremental run, return the set of order pks that must be (re)fetched.

    An order qualifies if EITHER its own ``modifiedtime`` advanced to/past the
    watermark, OR any of its arrivals did (``modifiedtime`` OR ``creationtime`` >=
    watermark). The arrival branch exists because NC does NOT reliably bump
    ``po_order.modifiedtime`` when an arrival is posted, so an order-only
    watermark filter would miss a new arrival against an already-synced order
    (its GR would never be mirrored → the 3-way gate never opens → payment
    stalls). NC timestamps are 'YYYY-MM-DD HH24:MI:SS' strings, which compare
    lexicographically in chronological order.

    order_rows: iterable of dicts with 'pk_order' and 'modifiedtime'.
    arrival_rows: iterable of dicts with 'pk_order', 'modifiedtime', 'creationtime'.
    """
    pks: set = set()
    for o in order_rows:
        mt = o.get("modifiedtime")
        if mt is not None and (watermark is None or mt >= watermark):
            pks.add(o["pk_order"])
    for a in arrival_rows:
        mt, ct = a.get("modifiedtime"), a.get("creationtime")
        if watermark is None or (mt is not None and mt >= watermark) \
                or (ct is not None and ct >= watermark):
            pks.add(a["pk_order"])
    return pks


def fetch_nc(cutover: str, watermark: str | None) -> dict:
    """cutover: 'YYYY-MM-DD HH24:MI:SS' (only orders with dbilldate >= cutover).
    watermark: last synced NC modifiedtime, or None for full."""
    con = _connect()
    try:
        cur = con.cursor()
        def lookup(sql):
            cur.execute(sql)
            return {r[0]: r[1] for r in cur.fetchall()}
        suppliers = lookup("select pk_supplier, code from NCSC.BD_SUPPLIER")
        uoms = lookup("select pk_measdoc, code from NCSC.BD_MEASDOC")
        currencies = lookup("select pk_currtype, code from NCSC.BD_CURRTYPE")
        # Prefer the English material name (ename); fall back to name when a
        # material has no English name.
        cur.execute("select pk_material, code, name, ename from NCSC.BD_MATERIAL")
        materials = {
            r[0]: (r[1], (r[3].strip() if r[3] and r[3].strip() else r[2]))
            for r in cur.fetchall()
        }

        _order_cols = ("pk_order, vbillcode, dbilldate, pk_supplier, corigcurrencyid, "
                       "ntotalorigmny, forderstatus, modifiedtime, vmemo, "
                       "bfinalclose, dclosedate, creationtime, vtrantypecode")
        if watermark:
            # INCREMENTAL: union of (a) orders whose OWN modifiedtime advanced and
            # (b) orders that have a new arrival (NC may not bump order.modifiedtime
            # on arrival posting). Then fetch FULL headers for the unioned set so an
            # order pulled in only by a new arrival still gets its header/lines
            # mirrored (needed to resolve po_line_id and ensure the PO exists).
            cur.execute(
                "select pk_order, modifiedtime from NCSC.PO_ORDER "
                "where forderstatus=3 and dbilldate >= :cut and modifiedtime >= :wm",
                {"cut": cutover, "wm": watermark})
            order_rows = [dict(zip([c[0].lower() for c in cur.description], r))
                          for r in cur.fetchall()]
            cur.execute(
                "select ab.pk_order, ah.modifiedtime, ah.creationtime "
                "from NCSC.PO_ARRIVEORDER ah "
                "join NCSC.PO_ARRIVEORDER_B ab on ab.pk_arriveorder = ah.pk_arriveorder "
                "join NCSC.PO_ORDER o on o.pk_order = ab.pk_order "
                "where ah.fbillstatus=3 and o.forderstatus=3 and o.dbilldate >= :cut "
                "and (ah.modifiedtime >= :wm or ah.creationtime >= :wm)",
                {"cut": cutover, "wm": watermark})
            arrival_rows = [dict(zip([c[0].lower() for c in cur.description], r))
                            for r in cur.fetchall()]
            order_pks = list(select_incremental_order_pks(order_rows, arrival_rows, watermark))
            orders = []
            for chunk in _chunks(order_pks, 900):
                ph = ",".join(f":p{i}" for i in range(len(chunk)))
                b = {f"p{i}": v for i, v in enumerate(chunk)}
                cur.execute(f"select {_order_cols} from NCSC.PO_ORDER "
                            f"where pk_order in ({ph})", b)
                ocols = [c[0].lower() for c in cur.description]
                orders += [dict(zip(ocols, r)) for r in cur.fetchall()]
        else:
            # FULL: everything past cutover (unchanged).
            cur.execute(f"select {_order_cols} from NCSC.PO_ORDER "
                        "where forderstatus=3 and dbilldate >= :cut", {"cut": cutover})
            ocols = [c[0].lower() for c in cur.description]
            orders = [dict(zip(ocols, r)) for r in cur.fetchall()]
            order_pks = [o["pk_order"] for o in orders]

        order_lines, arrivals, arrival_lines = [], [], []
        invoiced_arrivals: set = set()
        for chunk in _chunks(order_pks, 900):
            ph = ",".join(f":p{i}" for i in range(len(chunk)))
            b = {f"p{i}": v for i, v in enumerate(chunk)}
            cur.execute(
                "select pk_order_b, pk_order, crowno, pk_material, vvendinventoryname, "
                "castunitid, nastnum, norigtaxprice, ntaxrate, ctaxcodeid, norigtaxmny, norigmny, ntax, "
                # dplanarrvdate = 计划到货日期, the date NC's PO list shows as
                # "Delivery Date". CHAR 'YYYY-MM-DD HH24:MI:SS'; the transform
                # keeps only the date half. Line level, not header -- NC lets
                # each line differ and real orders do.
                "dplanarrvdate, "
                "bpayclose, binvoiceclose "
                f"from NCSC.PO_ORDER_B where pk_order in ({ph})", b)
            lcols = [c[0].lower() for c in cur.description]
            order_lines += [dict(zip(lcols, r)) for r in cur.fetchall()]
            cur.execute(
                "select pk_arriveorder, vbillcode, dbilldate, pk_supplier, fbillstatus, modifiedtime "
                "from NCSC.PO_ARRIVEORDER ah where fbillstatus=3 and exists("
                "select 1 from NCSC.PO_ARRIVEORDER_B ab where ab.pk_arriveorder=ah.pk_arriveorder "
                f"and ab.pk_order in ({ph}))", b)
            acols = [c[0].lower() for c in cur.description]
            arrivals += [dict(zip(acols, r)) for r in cur.fetchall()]
            cur.execute(
                "select pk_arriveorder_b, pk_arriveorder, pk_order, pk_order_b, crowno, "
                "pk_material, nastnum, norigtaxprice, norigtaxmny, norigmny "
                f"from NCSC.PO_ARRIVEORDER_B where pk_order in ({ph})", b)
            albcols = [c[0].lower() for c in cur.description]
            arrival_lines += [dict(zip(albcols, r)) for r in cur.fetchall()]
            # Which arrivals (GRs) are already invoiced downstream in NC. NC pays
            # via 到货->采购入库单->采购发票, so an arrival is "invoiced/paid" (per
            # the user's proxy: has any downstream invoice) when a stock-in line
            # (ic_purchasein_b.csourcebillbid = this arrival line) has an invoice
            # line (po_invoice_b.csourcebid = that stock-in line's cgeneralbid).
            # NC uses '~' as a null placeholder and CHAR-pads pks, hence trim().
            cur.execute(
                "select distinct ab.pk_arriveorder from NCSC.PO_ARRIVEORDER_B ab "
                f"where ab.pk_order in ({ph}) and exists("
                "select 1 from NCSC.IC_PURCHASEIN_B ic "
                "join NCSC.PO_INVOICE_B vb on trim(vb.csourcebid)=trim(ic.cgeneralbid) "
                "where trim(ic.csourcebillbid)=trim(ab.pk_arriveorder_b))", b)
            invoiced_arrivals.update(r[0] for r in cur.fetchall())
        # Advance the watermark to the TRUE max across orders AND arrivals — an
        # arrival can be newer than every order.modifiedtime (the whole reason the
        # order-only watermark missed arrival-only changes), so an orders-only max
        # would re-fetch the same arrival forever.
        _mts = [o["modifiedtime"] for o in orders if o.get("modifiedtime") is not None]
        _mts += [a["modifiedtime"] for a in arrivals if a.get("modifiedtime") is not None]
        maxmt = max(_mts, default=None)
        return {
            "orders": orders, "order_lines": order_lines,
            "arrivals": arrivals, "arrival_lines": arrival_lines,
            "invoiced_arrivals": invoiced_arrivals,
            "suppliers": suppliers, "materials": materials, "uoms": uoms,
            "currencies": currencies, "max_modifiedtime": maxmt,
        }
    finally:
        con.close()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
