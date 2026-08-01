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
        cur.execute("select pk_material, code, name from NCSC.BD_MATERIAL")
        materials = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        _order_cols = ("pk_order, vbillcode, dbilldate, pk_supplier, corigcurrencyid, "
                       "ntotalorigmny, forderstatus, modifiedtime, vmemo")
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
        for chunk in _chunks(order_pks, 900):
            ph = ",".join(f":p{i}" for i in range(len(chunk)))
            b = {f"p{i}": v for i, v in enumerate(chunk)}
            cur.execute(
                "select pk_order_b, pk_order, crowno, pk_material, vvendinventoryname, "
                "castunitid, nastnum, norigtaxprice, ntaxrate, ctaxcodeid, norigtaxmny, norigmny, ntax "
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
            "suppliers": suppliers, "materials": materials, "uoms": uoms,
            "currencies": currencies, "max_modifiedtime": maxmt,
        }
    finally:
        con.close()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
