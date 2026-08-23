"""Read-only Flux WMS Oracle reader (thick mode — server is Oracle <=11g).

python-oracledb's THIN mode refuses to connect to Oracle <=11g (DPY-3010),
so this must run THICK via the Instant Client baked into mrp-api's Dockerfile
(see settings.oracle_client_lib). `oracledb.init_oracle_client` may only be
called ONCE per process, hence the `_client_ready` module-level guard.

Single warehouse (`CANADA`), single org (`FEIHE`), single inventory owner
(`CUSTOMERID='10024'`) per design doc appendix A — no WHERE filters on those
are needed since the surveyed instance has no other values, but `l.qty > 0`
still matters: WMS keeps zero-qty historical lot rows.
"""
from app.core.config import settings

_client_ready = False


def _ensure_thick() -> None:
    global _client_ready
    if not _client_ready:
        import oracledb
        oracledb.init_oracle_client(lib_dir=settings.oracle_client_lib)
        _client_ready = True


def wms_configured() -> bool:
    return all([settings.wms_host, settings.wms_service, settings.wms_user, settings.wms_password])


def fetch_lot_locations() -> list[dict]:
    """Where each lot sits, one row per (location, handling unit).

    Joined to BAS_LOCATION for the zone, which is the only human-meaningful
    thing the location master carries — it has no name or description column,
    so a bare `11040511` is all a planner would otherwise get. LEFT joined: a
    location missing from the master is still holding real stock.

    `l.qty > 0` mirrors fetch_inventory's filter so the two extracts describe
    the same set of stock; without it, depleted location rows would list a
    lot as sitting somewhere it no longer is.

    ★ The join MUST carry `warehouseid` as well as `locationid`. Location codes
    are reused across warehouses -- `01010107` exists in CANADA, HARBINGC,
    LONGJIANGLK and QQHE with a different zone in each -- so joining on the code
    alone both fans the extract out (4,469 rows instead of 3,549: 920 phantom
    ones) and attaches another warehouse's zone to the survivors. Written the
    wrong way first; the mirror's unique key caught it on the first real run.
    `(WAREHOUSEID, LOCATIONID)` is unique in BAS_LOCATION across all 165,261
    rows, so with the warehouse in the join this stays strictly 1:1.

    Verified read-only against the live instance (2026-08-18): 3,549 rows for
    3,429 active lots, quantities reconciling exactly with INV_LOT.QTY, and
    `(WAREHOUSEID, SKU, LOTNUM, LOCATIONID, TRACEID)` unique across all of them.
    """
    import oracledb
    _ensure_thick()
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.wms_host, settings.wms_port, service_name=settings.wms_service)
    con = oracledb.connect(user=settings.wms_user, password=settings.wms_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("""
            select l.warehouseid, l.sku, l.lotnum, l.locationid, l.traceid,
                   l.qty, l.qtyallocated, l.qtyonhold, b.zoneid
            from INV_LOT_LOC_ID l
            left join BAS_LOCATION b
              on b.warehouseid = l.warehouseid and b.locationid = l.locationid
            where l.qty > 0""")
        names = [c[0].lower() for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()


def fetch_inventory() -> list[dict]:
    """Full extract, one round trip. Returns lower-cased-column dicts, one per
    active (qty > 0) lot -- roughly 3.5k rows per the appendix A survey."""
    import oracledb
    _ensure_thick()
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.wms_host, settings.wms_port, service_name=settings.wms_service)
    con = oracledb.connect(user=settings.wms_user, password=settings.wms_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("""
            select l.warehouseid, l.sku, l.lotnum, l.qty, l.qtyallocated, l.qtyonhold,
                   a.lotatt01, a.lotatt02, a.lotatt03, a.lotatt05, a.lotatt08,
                   a.lotatt13, a.lotatt14, l.edittime, p.uomdescr as uom
            from INV_LOT l
            join INV_LOT_ATT a
              on a.organizationid = l.organizationid and a.lotnum = l.lotnum
             and a.customerid = l.customerid and a.sku = l.sku
            -- The unit the warehouse measures this in. NOT BAS_SKU's own UOM
            -- columns: those are 'EA' on all 2,482 SKUs and unmaintained. The
            -- real answer is the BASE level of the packaging ladder
            -- (PACKUOM='EA'), whose UOMDESCR is KG for STANDARD, PIECES for
            -- PMSTANDARD/TINBOTTOM, and so on. Verified 1:1 against the live
            -- instance -- (CUSTOMERID, PACKID) is unique at that level across
            -- all 6,535 groups, and the extract stays at 3,429 rows.
            left join BAS_SKU s
              on s.sku = l.sku and s.customerid = l.customerid
            left join BAS_PACKAGE_DETAILS p
              on p.packid = s.packid and p.customerid = l.customerid
             and p.packuom = 'EA'
            where l.qty > 0""")
        names = [c[0].lower() for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()
