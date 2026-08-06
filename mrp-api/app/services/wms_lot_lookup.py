"""Read-only WMS lot-attribute lookup (Phase 1A Task 3).

Looks up a lot's production/expiry dates from Flux WMS's `INV_LOT_ATT` table
— deliberately NOT `INV_LOT` (see design doc 6.3 / appendix A): `INV_LOT`
only carries currently-in-stock lots (~3532 rows) while `INV_LOT_ATT` retains
every lot ever received (~23973 rows), including ones that have since fully
shipped out. The consignment warehouse's weekly count is entered by lot
number alone (no expiry) and many of the lots being counted were received
long ago, so the attribute table is the only source that reliably still has
them.

The "lot number" is the **supplier batch** = `LOTATT05` (what the business
uses and the planner types), NOT WMS's internal `lotnum` ("WMS Lot Ref" like
HGC1993989, WMS-internal only). One supplier batch fans out to many internal
lot refs sharing the same dates, so both entry points here key on `lotatt05`:
`lookup_lot` matches a single supplier batch, `list_lots` groups by it. Scope
`(ORGANIZATIONID, CUSTOMERID, SKU)` — org 'FEIHE', customer '10024' — the
same single-tenant scope as app/services/wms_sync/reader.py's
`fetch_inventory`, filtered explicitly even though the surveyed instance has
no other values.

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

`lookup_lot` itself is a blocking, synchronous call (thick-mode oracledb has
no async API) — callers MUST invoke it via `anyio.to_thread.run_sync`, never
directly from an `async def` endpoint, or a slow/hung WMS host stalls the
entire event loop (every other request this process is handling, including
`/health`), not just this one. See app/api/v1/consignment.py's two call
sites. The connection itself is bounded on both axes so a genuinely
blackholed host still can't hang the worker thread forever: `connect()`
gets `tcp_connect_timeout` (TCP handshake) and, once connected,
`Connection.call_timeout` (thick-mode-only per-round-trip timeout) bounds
the SELECT itself — both configurable via `settings.wms_lookup_*_timeout_*`
(app/core/config.py).
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
        con = oracledb.connect(
            user=settings.wms_user,
            password=settings.wms_password,
            dsn=dsn,
            tcp_connect_timeout=settings.wms_lookup_connect_timeout_seconds,
        )
        # Thick-mode-only: bounds each round trip (the SELECT below), so a
        # connection that succeeds but then hangs mid-query (e.g. the host
        # is up but the listener/DB itself is wedged) still can't hang this
        # request forever the way tcp_connect_timeout alone would miss.
        con.call_timeout = settings.wms_lookup_query_timeout_ms
        try:
            cur = con.cursor()
            # Match on LOTATT05 (the SUPPLIER batch — what the business actually
            # calls the lot number), NOT lotnum (WMS's internal "WMS Lot Ref"
            # like HGC1993989, which the planner never sees or types). One
            # supplier batch spans many internal lot refs that all share the
            # same production/expiry, so order by expiry and take one row.
            cur.execute(
                """
                select lotatt01, lotatt02 from (
                    select lotatt01, lotatt02
                    from INV_LOT_ATT
                    where organizationid = :org
                      and customerid = :cust
                      and sku = :sku
                      and lotatt05 = :lot
                    order by lotatt02 desc nulls last
                ) where rownum = 1
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


# Bound the batch list a single product can return: INV_LOT_ATT keeps every
# lot ever received, and a high-volume SKU (e.g. CF00AF has ~500 in-stock lots
# alone, more across full history) would otherwise stream thousands of rows
# into a picker that only needs the recent ones. Newest-expiry-first + this
# cap keeps the payload and the combo box usable.
_LOT_HISTORY_CAP = 500


def list_lots(material_code: str) -> list[dict]:
    """All historical SUPPLIER batches of one SKU, newest expiry first, for
    the Consignment page's lot-number combo box.

    The "lot number" the business uses is the **supplier batch** (`LOTATT05`),
    NOT WMS's internal `lotnum` ("WMS Lot Ref", e.g. HGC1993989) — one supplier
    batch fans out to many internal lot refs that all share its production/
    expiry (e.g. S0093's 41 lot refs collapse to 4 supplier batches). So we
    group by `lotatt05` and return one entry per supplier batch, keeping this
    consistent with `lookup_lot`, which also matches on `lotatt05`.

    Returns `[{"lot_no": str, "production_date": date|None,
    "expiry_date": date|None}, ...]` from Flux WMS's `INV_LOT_ATT` (full
    history — see this module's docstring for why the attribute table and not
    `INV_LOT`). Rows with no supplier batch are skipped (there's nothing to
    offer). Dedup by supplier batch happens IN the query (group by), before
    the `_LOT_HISTORY_CAP` cut, so a high-volume SKU can't have real batches
    truncated away by the cap landing mid-duplicate-run.

    Mirrors `lookup_lot`'s never-raise contract: WMS not configured /
    unreachable / no lots for this SKU all come back as an empty list plus a
    logged warning, so the combo simply offers no suggestions and the planner
    can still type a batch by hand (design doc 6.3: never block entry).
    Blocking, synchronous (thick oracledb) — call via `anyio.to_thread`.
    """
    if not wms_configured():
        logger.warning("wms_lot_lookup.list_lots: WMS not configured, no suggestions for material=%s", material_code)
        return []

    import oracledb

    try:
        _ensure_thick()
        dsn = oracledb.makedsn(settings.wms_host, settings.wms_port, service_name=settings.wms_service)
        con = oracledb.connect(
            user=settings.wms_user,
            password=settings.wms_password,
            dsn=dsn,
            tcp_connect_timeout=settings.wms_lookup_connect_timeout_seconds,
        )
        con.call_timeout = settings.wms_lookup_query_timeout_ms
        try:
            cur = con.cursor()
            # Group by supplier batch (lotatt05) so duplicates collapse BEFORE
            # the ROWNUM cap; max(lotatt01/02) picks that batch's dates (the
            # 'YYYY-MM-DD' strings sort chronologically, and every lot ref in a
            # batch carries the same ones anyway). ROWNUM-in-outer-query (not
            # FETCH FIRST) so the cap holds on older Oracle too.
            cur.execute(
                """
                select lot_no, prod_date, exp_date from (
                    select lotatt05 as lot_no,
                           max(lotatt01) as prod_date,
                           max(lotatt02) as exp_date
                    from INV_LOT_ATT
                    where organizationid = :org
                      and customerid = :cust
                      and sku = :sku
                      and lotatt05 is not null
                    group by lotatt05
                    order by max(lotatt02) desc nulls last, lotatt05 desc
                ) where rownum <= :cap
                """,
                {"org": _ORG, "cust": _CUSTOMER, "sku": material_code, "cap": _LOT_HISTORY_CAP},
            )
            rows = cur.fetchall()
        finally:
            con.close()
    except Exception:
        logger.warning(
            "wms_lot_lookup.list_lots: WMS query failed for material=%s — returning no suggestions",
            material_code, exc_info=True,
        )
        return []

    out: list[dict] = []
    for lot_no, production_date, expiry_date in rows:
        if lot_no is None:
            continue
        out.append({
            "lot_no": lot_no,
            "production_date": _parse_wms_date(production_date),
            "expiry_date": _parse_wms_date(expiry_date),
        })
    return out
