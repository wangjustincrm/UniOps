"""Read-only NC65 Oracle reader for BOM headers/lines/substitutes (thin mode).

Full-scan every call — no incremental watermark yet. 1016 + 10169 + 664 rows
measured (2026-08-03 survey, docs/superpowers/specs/2026-08-03-nc-bom-survey.md),
small enough to re-read in full on every sync trigger. Incremental via
BD_BOM*.TS (the survey's confirmed reliable watermark column — NOT
MODIFIEDTIME, which stays NULL until a row is edited a *second* time) is a
Phase 1 concern, not implemented here.

Table/column source of truth: the survey doc above, not this module's SQL.
"""
from app.core.config import settings


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


def fetch_nc_bom() -> dict:
    """Live NC read (oracledb, read-only). Raises on any connection/query
    error — service.py reads this whole extract into memory before writing
    anything to Postgres, so a raise here can never leave a half-written sync.

    Returns raw NC rows (lower-cased Oracle column names, unfiltered — the
    caller drops columns the mirror models don't carry, e.g. the VDEF*/VFREE*
    custom-field slots) plus a `material_codes` pk->code lookup so Task 5's
    normalization step can resolve HCMATERIALID/CMATERIALID/CREPLMATERIALOID
    to material codes without a second NC round-trip.
    """
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()

        def rows(sql):
            cur.execute(sql)
            names = [c[0].lower() for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

        headers = rows("select * from NCSC.BD_BOM")
        lines = rows("select * from NCSC.BD_BOM_B")
        repl = rows("select * from NCSC.BD_BOM_REPL")

        cur.execute("select pk_material, code from NCSC.BD_MATERIAL")
        material_codes = {pk: code for pk, code in cur.fetchall()}

        return {"headers": headers, "lines": lines, "repl": repl, "material_codes": material_codes}
    finally:
        con.close()
