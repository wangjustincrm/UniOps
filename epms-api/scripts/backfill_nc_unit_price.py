"""Re-read NC unit prices at full precision, without a full reload.

`po_line_items.unit_price` and `gr_line_items.unit_price` were NUMERIC(15,2)
until migration ak01_nc_price_scale5 widened them to five decimals. Widening a
column does not bring back digits already dropped on write, so every line
mirrored before that migration still holds a price rounded to cents. This reads
the prices back from NC and restores them.

Measured over the 4,944 in-scope NC order lines: 319 of them disagreed with the
ERP's own line amount, by 43,256.09 in total, the worst single line by 2,000
(PO-057-2402-02: 500,000 x 0.944 stored as 500,000 x 0.94).

**Why neither ordinary path repairs this.**

  * An incremental sync only touches orders whose change time advanced. Nothing
    changed in NC, so nothing is re-read.
  * A full reload rebuilds what it deletes — but it deliberately PRESERVES any
    PO an invoice points at or that carries buyer-added lines, and the writer
    skips consumed POs row by row. Those are exactly the orders whose prices
    matter most, and a reload would leave every one of them rounded.

So this touches nothing but `unit_price`, on every mirrored line regardless of
downstream state. That is safe in a way a general re-sync is not: the stored
MONEY is untouched. `line_total` holds NC's own norigtaxmny and the header sums
its norigmny — both were always right. Only the price printed beside them was
rounded, which is why the documents stopped adding up.

Read-only on NC. Idempotent: a second run reports zero changes.

Usage (from epms-api, with NC_* and POSTGRES_* in the environment):

    python -m scripts.backfill_nc_unit_price --dry-run
    python -m scripts.backfill_nc_unit_price

`--dry-run` reports exactly what would change and writes nothing.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

import oracledb
import psycopg2

from app.core.config import settings
from app.services.nc_purchase_sync.reader import _IN_SCOPE, _LATEST_VERSION
from app.services.nc_purchase_sync.service import _cutover, _pg_dsn
from app.services.nc_purchase_sync.transform import _quotation_price

#: What the widened columns hold. A price beyond this cannot be stored, and
#: rounding here rather than at the driver keeps the comparison below honest —
#: otherwise a line NC quotes to eight places would look "changed" on every run.
_SCALE = Decimal("0.00001")


def _connect_nc():
    oracledb.defaults.fetch_decimals = True     # NUMBER -> Decimal, not float
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port,
                           service_name=settings.nc_service)
    return oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)


def fetch_nc_prices(cutover: str) -> tuple[dict, dict]:
    """(order-line prices, arrival-line prices), keyed by the pk the mirror stores.

    Order lines use ``_quotation_price`` — the same choice the transform makes,
    so the backfill cannot disagree with the sync about which of NC's two price
    columns is the one the mirrored quantity is counted in. Same scope filters
    as the sync, so this can never reach a line the sync would not have
    mirrored.
    """
    con = _connect_nc()
    try:
        cur = con.cursor()
        cur.execute(
            "select b.pk_order_b, b.nqtorigtaxprice, b.norigtaxprice "
            "from NCSC.PO_ORDER_B b join NCSC.PO_ORDER o on o.pk_order = b.pk_order "
            f"where {_IN_SCOPE} and o.{_LATEST_VERSION} and o.dbilldate >= :cut",
            {"cut": cutover})
        order_lines = {
            pk: _quotation_price({"nqtorigtaxprice": qt, "norigtaxprice": main}).quantize(_SCALE)
            for pk, qt, main in cur.fetchall()
        }
        cur.execute(
            "select ab.pk_arriveorder_b, ab.norigtaxprice "
            "from NCSC.PO_ARRIVEORDER_B ab "
            "join NCSC.PO_ORDER o on o.pk_order = ab.pk_order "
            f"where {_IN_SCOPE} and o.{_LATEST_VERSION} and o.dbilldate >= :cut",
            {"cut": cutover})
        arrival_lines = {
            pk: (Decimal(str(p)) if p is not None else Decimal("0")).quantize(_SCALE)
            for pk, p in cur.fetchall()
        }
        return order_lines, arrival_lines
    finally:
        con.close()


def _plan(cur, table: str, nc_prices: dict) -> tuple[list, int]:
    """Rows whose stored price differs from NC's, plus the count NC no longer lists."""
    cur.execute(f"select nc_source_pk, unit_price from {table} "
                "where nc_source_pk is not null")
    ours = dict(cur.fetchall())
    changes = [(pk, nc_prices[pk]) for pk, current in ours.items()
               if pk in nc_prices and current != nc_prices[pk]]
    missing = sum(1 for pk in ours if pk not in nc_prices)
    print(f"{table}: {len(ours)} mirrored line(s), {len(changes)} to update")
    if missing:
        # Reported rather than ignored: a mirrored line NC no longer returns
        # means the two have drifted, worth knowing even though repairing that
        # is not this script's job.
        print(f"  note: {missing} line(s) were not in NC's result set "
              f"(older than the cutover, or no longer in scope)")
    for pk, value in changes[:5]:
        print(f"    {pk}: {ours[pk]} -> {value}")
    if len(changes) > 5:
        print(f"    ... {len(changes) - 5} more")
    return changes, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    args = parser.parse_args()

    con = psycopg2.connect(_pg_dsn())
    con.autocommit = False
    cur = con.cursor()

    # The admin-set cutover lives in company_config, so read it through this
    # cursor rather than assume the env — the same source the sync uses.
    cutover = _cutover(cur)
    print(f"cutover: {cutover}")

    order_prices, arrival_prices = fetch_nc_prices(cutover)
    print(f"NC returned {len(order_prices)} order line(s) and "
          f"{len(arrival_prices)} arrival line(s)")

    po_changes, _ = _plan(cur, "po_line_items", order_prices)
    gr_changes, _ = _plan(cur, "gr_line_items", arrival_prices)

    if args.dry_run:
        print("dry run — nothing written")
        con.rollback()
        con.close()
        return 0

    for table, changes in (("po_line_items", po_changes), ("gr_line_items", gr_changes)):
        for pk, value in changes:
            cur.execute(f"update {table} set unit_price = %s where nc_source_pk = %s",
                        (value, pk))
    con.commit()
    print(f"updated {len(po_changes)} order line(s) and {len(gr_changes)} arrival line(s)")

    cur.execute("""
        select count(*)
        from po_line_items l join purchase_orders po on po.id = l.po_id
        where po.source = 'nc' and l.nc_source_pk is not null
          and abs(l.qty * l.unit_price - l.line_total) > 0.005
    """)
    print(f"NC lines whose qty x price still misses line_total: {cur.fetchone()[0]}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
