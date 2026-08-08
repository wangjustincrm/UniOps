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
                   a.lotatt13, a.lotatt14, l.edittime
            from INV_LOT l
            join INV_LOT_ATT a
              on a.organizationid = l.organizationid and a.lotnum = l.lotnum
             and a.customerid = l.customerid and a.sku = l.sku
            where l.qty > 0""")
        names = [c[0].lower() for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()
