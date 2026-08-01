"""Read-only NC65 Oracle reader for purchase orders + arrivals."""
from app.core.config import settings


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


def _connect():
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    return oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)


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

        wm = " and modifiedtime >= :wm" if watermark else ""
        binds = {"cut": cutover, **({"wm": watermark} if watermark else {})}

        cur.execute(
            "select pk_order, vbillcode, dbilldate, pk_supplier, corigcurrencyid, "
            "ntotalorigmny, forderstatus, modifiedtime, vmemo "
            "from NCSC.PO_ORDER where forderstatus=3 and dbilldate >= :cut" + wm, binds)
        ocols = [c[0].lower() for c in cur.description]
        orders = [dict(zip(ocols, r)) for r in cur.fetchall()]
        order_pks = [o["pk_order"] for o in orders]

        order_lines, arrivals, arrival_lines = [], [], []
        maxmt = max([o["modifiedtime"] for o in orders], default=None)
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
