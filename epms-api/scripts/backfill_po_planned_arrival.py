"""Backfill `po_line_items.planned_arrival_date` from NC, without a full reload.

The column was added after the NC purchase sync had already mirrored every
order, so every existing line is NULL — which is precisely the set the feature
needs: all 115 open raw-material POs are NC-sourced and not one carries a
hand-entered header date, so `DPLANARRVDATE` is the only answer there is.

**Why not just run the sync in `full` mode.** That mode does
`delete from goods_receipts where source='nc'` and
`delete from purchase_orders where source='nc'` and rebuilds them. It is the
right tool for repairing a divergent mirror and the wrong one for adding a
column: in production it would delete and recreate every NC purchase order and
goods receipt, taking their ids with them, to populate one date. This script
touches nothing but that date.

Read-only on NC. On Postgres it issues one UPDATE per changed line, keyed on
`nc_source_pk` (= `PO_ORDER_B.PK_ORDER_B`), and skips lines already holding the
right value so a second run reports zero changes rather than churning rows.

Usage (from epms-api, with NC_* and POSTGRES_* in the environment):

    python -m scripts.backfill_po_planned_arrival --dry-run
    python -m scripts.backfill_po_planned_arrival

`--dry-run` reports exactly what would change and writes nothing.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import oracledb
import psycopg2

from app.core.config import settings
from app.services.nc_purchase_sync.service import _cutover, _pg_dsn
from app.services.nc_purchase_sync.transform import _planned_arrival


def fetch_nc_arrival_dates(cutover: str) -> dict[str, date | None]:
    """`pk_order_b` -> planned arrival date, for approved orders past the cutover.

    Same `forderstatus = 3` and cutover filters the sync itself uses, so this
    cannot reach lines the sync would never have mirrored.
    """
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port,
                           service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute(
            "select b.pk_order_b, b.dplanarrvdate "
            "from NCSC.PO_ORDER_B b "
            "join NCSC.PO_ORDER o on o.pk_order = b.pk_order "
            "where o.forderstatus = 3 and o.dbilldate >= :cut",
            {"cut": cutover},
        )
        return {pk: _planned_arrival(raw) for pk, raw in cur.fetchall()}
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    args = parser.parse_args()

    con = psycopg2.connect(_pg_dsn())
    con.autocommit = False
    cur = con.cursor()

    # The admin-set cutover lives in company_config, so it is read through this
    # cursor rather than assumed from the env -- the same source the sync uses.
    cutover = _cutover(cur)
    print(f"cutover: {cutover}")

    arrivals = fetch_nc_arrival_dates(cutover)
    print(f"NC returned {len(arrivals)} order line(s), "
          f"{sum(1 for v in arrivals.values() if v)} with a planned arrival date")

    cur.execute("select nc_source_pk, planned_arrival_date from po_line_items "
                "where nc_source_pk is not null")
    ours = dict(cur.fetchall())
    print(f"{len(ours)} mirrored NC line(s) in this database, "
          f"{sum(1 for v in ours.values() if v)} already dated")

    changes = [
        (pk, arrivals[pk]) for pk, current in ours.items()
        if pk in arrivals and arrivals[pk] is not None and current != arrivals[pk]
    ]
    missing = [pk for pk in ours if pk not in arrivals]

    print(f"{len(changes)} line(s) to update")
    if missing:
        # Reported rather than silently ignored: a mirrored line NC no longer
        # returns means the two have drifted, which is worth knowing even
        # though this script is not the place to fix it.
        print(f"note: {len(missing)} mirrored line(s) were not in NC's result set "
              f"(older than the cutover, or no longer approved)")

    for pk, value in changes[:5]:
        print(f"  {pk} -> {value}")
    if len(changes) > 5:
        print(f"  ... {len(changes) - 5} more")

    if args.dry_run:
        print("dry run — nothing written")
        con.rollback()
        return 0

    for pk, value in changes:
        cur.execute("update po_line_items set planned_arrival_date = %s "
                    "where nc_source_pk = %s", (value, pk))
    con.commit()
    print(f"updated {len(changes)} line(s)")

    cur.execute("""
        select count(*), count(l.planned_arrival_date)
        from po_line_items l join purchase_orders po on po.id = l.po_id
        where po.type = 1 and po.status in ('issued','partially_received')
          and l.material_id is not null and l.qty > l.received_qty
    """)
    total, dated = cur.fetchone()
    print(f"open raw-material lines: {dated} of {total} now carry an arrival date")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
