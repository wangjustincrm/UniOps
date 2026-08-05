"""Read-only WMS lot-attribute lookup (Phase 1A Task 3).

Looks up a single lot's production/expiry dates from Flux WMS's
`INV_LOT_ATT` table — deliberately NOT `INV_LOT` (see design doc 6.3 /
appendix A): `INV_LOT` only carries currently-in-stock lots (~3532 rows)
while `INV_LOT_ATT` retains every lot ever received (~23973 rows), including
ones that have since fully shipped out. The consignment warehouse's weekly
count is entered by lot number alone (no expiry) and many of the lots being
counted were received long ago, so the attribute table is the only source
that reliably still has them.

Join key is `(ORGANIZATIONID, LOTNUM, CUSTOMERID, SKU)` — org 'FEIHE',
customer '10024' — same single-tenant scope as
app/services/wms_sync/reader.py's `fetch_inventory`; filtered explicitly even
though the surveyed instance has no other values, so a match is scoped, not
"first LOTNUM+SKU pair found across any org/customer".

Reuses reader.py's thick-mode connection idiom (`_ensure_thick`,
`wms_configured`) rather than a second connection style — importing the
"private" `_ensure_thick` directly (instead of duplicating it) matters here
because `oracledb.init_oracle_client` may only be called ONCE per process;
sharing reader.py's module-level guard keeps that true even when both WMS
sync and lot lookup run in the same mrp-api process.

Design doc 6.3 is explicit: a stock-count save must NEVER be blocked by WMS
being unreachable or the lot simply not existing there. So `lookup_lot`
never raises for either case — both come back as `found=False` with a
logged warning; only a genuinely unexpected local misconfiguration (e.g. a
programming error) would still raise.
"""
import logging

from app.core.config import settings
from app.services.wms_sync.reader import _ensure_thick, wms_configured
from app.services.wms_sync.transform import _d as _parse_wms_date

logger = logging.getLogger(__name__)

_ORG = "FEIHE"
_CUSTOMER = "10024"

_NOT_FOUND = {"found": False, "production_date": None, "expiry_date": None}


def lookup_lot(lot_no: str, material_code: str) -> dict:
    """Returns {"found": bool, "production_date": date|None, "expiry_date": date|None}.

    Raw LOTATT01/LOTATT02 'YYYY-MM-DD' strings are parsed via the same `_d`
    helper wms_sync/transform.py uses for the same columns, so both call
    sites treat malformed date strings identically (silently -> None rather
    than raising).

    Never raises for "WMS not configured" / "WMS unreachable" / "lot not
    found" — all three collapse to `found=False` plus a logged warning, per
    the explicit "never block the save" rule in design doc 6.3.
    """
    if not wms_configured():
        logger.warning(
            "wms_lot_lookup: WMS not configured, skipping lookup for lot=%s material=%s",
            lot_no, material_code,
        )
        return dict(_NOT_FOUND)

    import oracledb

    try:
        _ensure_thick()
        dsn = oracledb.makedsn(settings.wms_host, settings.wms_port, service_name=settings.wms_service)
        con = oracledb.connect(user=settings.wms_user, password=settings.wms_password, dsn=dsn)
        try:
            cur = con.cursor()
            cur.execute(
                """
                select lotatt01, lotatt02
                from INV_LOT_ATT
                where organizationid = :org
                  and customerid = :cust
                  and lotnum = :lot
                  and sku = :sku
                """,
                {"org": _ORG, "cust": _CUSTOMER, "lot": lot_no, "sku": material_code},
            )
            row = cur.fetchone()
        finally:
            con.close()
    except Exception:
        logger.warning(
            "wms_lot_lookup: WMS lookup failed for lot=%s material=%s — leaving expiry blank",
            lot_no, material_code, exc_info=True,
        )
        return dict(_NOT_FOUND)

    if row is None:
        logger.warning(
            "wms_lot_lookup: lot not found in INV_LOT_ATT for lot=%s material=%s — leaving expiry blank",
            lot_no, material_code,
        )
        return dict(_NOT_FOUND)

    production_date, expiry_date = row
    return {
        "found": True,
        "production_date": _parse_wms_date(production_date),
        "expiry_date": _parse_wms_date(expiry_date),
    }
