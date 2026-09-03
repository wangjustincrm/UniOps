"""Wind the NC purchase sync's incremental watermark back, so one scheduled run
re-reads a window it has already passed.

**Why this exists.** The mirror's scope and its change-detection expression both
changed (free-state orders are now mirrored, and `_CHANGED_AT` moved from NVL to
GREATEST over ts/modifiedtime/taudittime/creationtime). The watermark stored in
`nc_purchase_sync_runs` was computed by the OLD expression, and every order whose
change time is behind it is invisible to every future incremental run — including
the free-state drafts that are the whole point of the change. At the time of
writing, three of the five live free-state orders (PO-063-2608-01, PO-081-2609-01,
PO-055-2609-01) sit behind the stored watermark and would never arrive.

**Why not a full reload.** `full` mode does `delete from purchase_orders where
source='nc'` and rebuilds. It preserves POs an invoice points at, but an
unapproved mirrored PO has no invoice by construction — so a full reload would
delete and recreate exactly the `nc_pending` orders this change is about, taking
their ids, their `signoff_status`, their collected signatures and their open
sign-off tasks with them. Winding the watermark back touches one string.

This script writes nothing but `nc_purchase_sync_runs.watermark_to` on the row
`service.latest_watermark()` reads, and prints the previous value so the change
is reversible by hand.

Usage (from epms-api, with NC_* and POSTGRES_* in the environment):

    python -m scripts.rewind_nc_purchase_watermark --to "2026-08-01 00:00:00" --dry-run
    python -m scripts.rewind_nc_purchase_watermark --to "2026-08-01 00:00:00"

`--dry-run` lists the orders the next incremental run would then pull, straight
from NC, and writes nothing. Run it first: the list is the evidence that the
window you picked actually covers the orders you are missing.
"""
from __future__ import annotations

import argparse
import re
import sys

import psycopg2

from app.services.nc_purchase_sync import reader
from app.services.nc_purchase_sync.service import _cutover, _pg_dsn

_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _preview(cutover: str, watermark: str) -> list[tuple]:
    """The orders an incremental run at `watermark` would fetch, read live from
    NC. Orders only — an order pulled in solely by a new arrival is not listed,
    because that branch is unaffected by the watermark this script moves."""
    con = reader._connect()
    try:
        cur = con.cursor()
        cur.execute(
            f"select o.vbillcode, o.forderstatus, {reader._CHANGED_AT} ca "
            f"from NCSC.PO_ORDER o where {reader._IN_SCOPE} and o.{reader._LATEST_VERSION} "
            f"and o.dbilldate >= :cut and {reader._CHANGED_AT} >= :wm order by ca",
            {"cut": cutover, "wm": watermark})
        return cur.fetchall()
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True,
                    help="new watermark, 'YYYY-MM-DD HH:MM:SS' (NC local time)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args()

    if not _STAMP.match(args.to):
        print(f"--to must be 'YYYY-MM-DD HH:MM:SS', got {args.to!r}", file=sys.stderr)
        return 2

    con = psycopg2.connect(_pg_dsn())
    con.autocommit = False
    try:
        cur = con.cursor()
        cur.execute("select id, started_at, watermark_to from nc_purchase_sync_runs "
                    "where status='success' and watermark_to is not null "
                    "order by started_at desc limit 1")
        row = cur.fetchone()
        if not row:
            print("No successful run carries a watermark — the next incremental "
                  "run is already a full read. Nothing to rewind.")
            return 0
        run_id, started_at, current = row
        cutover = _cutover(cur)

        print(f"current watermark : {current}   (run {run_id}, started {started_at})")
        print(f"new watermark     : {args.to}")
        print(f"cutover           : {cutover}")
        if args.to >= current:
            print("\nThe new watermark is not EARLIER than the current one — this "
                  "would widen nothing. Refusing.", file=sys.stderr)
            return 2

        before = _preview(cutover, current)
        after = _preview(cutover, args.to)
        gained = [r for r in after if r not in before]
        print(f"\norders an incremental fetches now ....... {len(before)}")
        print(f"orders it would fetch after the rewind .. {len(after)}")
        print(f"newly reachable ......................... {len(gained)}")
        for code, status, ca in gained:
            label = {0: "free", 2: "in-approval", 3: "approved"}.get(int(status), str(status))
            print(f"    {code:24s} {label:12s} changed_at={ca}")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        cur.execute("update nc_purchase_sync_runs set watermark_to=%s, updated_at=now() "
                    "where id=%s", (args.to, run_id))
        con.commit()
        print(f"\nwatermark_to on run {run_id}: {current} -> {args.to}")
        print("The next scheduled run (or a press of Sync Now) picks the window up.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
