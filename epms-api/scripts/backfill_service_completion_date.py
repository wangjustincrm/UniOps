"""Backfill `purchase_requests.service_completion_date` for existing service POs.

`service_completion_date` is new (migration ai01_pr_service_completion), so
every PR predating it is NULL — and `app/tasks/service_gr_due.py` deliberately
skips NULLs rather than guessing. That is the right default for the long tail,
but it also means the service and project POs that are stuck *right now* would
never be chased. This script gives them a date so they enter the sweep.

Source of the date, in order:

  1. `purchase_orders.expected_delivery` — what the buyer actually entered on
     the PO. On a service PO this is the completion date under a different
     label; `PoCreatePage` even prefills it from `pr.required_by`.
  2. `purchase_requests.required_by` — the fallback when the PO header is
     blank, which happens on imported orders.

A PR with neither is left alone and reported: there is nothing to infer from,
and inventing a date would mail somebody about a deadline nobody set.

Scope is deliberately narrow — only PRs that (a) are types 4/6, (b) still have
no completion date, and (c) hang off a PO that the sweep could actually act on
(open status, no live GR). Closed and already-received work is not touched, so
this cannot resurrect finished business.

Writes nothing on `--dry-run` (the default posture for the first look). Re-runs
are safe: rows that already have a date are excluded by the WHERE clause, so a
second run reports zero changes.

⚠️ Turning the sweep live immediately after this may chase a large batch at
once. Leave `service_gr_due_dry_run` ON (its default) until the admin preview
email shows a list you are happy with.

Usage (from epms-api, with POSTGRES_* pointing at the target database):

    python -m scripts.backfill_service_completion_date --dry-run
    python -m scripts.backfill_service_completion_date
"""
from __future__ import annotations

import argparse
import sys

import psycopg2
import psycopg2.extras

from app.core.config import settings

# Keep in lockstep with app/schemas/gr.py (SERVICE_TYPES, GR_VOID_STATUSES) and
# app/tasks/service_gr_due.py (OPEN_PO_STATUSES). Plain SQL here — this script
# runs standalone against a database, not inside the app.
SERVICE_TYPES = (4, 6)
OPEN_PO_STATUSES = ("approved", "issued", "partially_received")
GR_VOID_STATUSES = ("rejected", "cancelled")

CANDIDATES_SQL = """
select pr.id            as pr_id,
       pr.number        as pr_number,
       po.number        as po_number,
       po.expected_delivery,
       pr.required_by,
       coalesce(po.expected_delivery, pr.required_by) as inferred
  from purchase_requests pr
  join purchase_orders  po on po.pr_id = pr.id
 where pr.type = any(%(types)s)
   and pr.service_completion_date is null
   and po.type = any(%(types)s)
   and po.status = any(%(open_statuses)s)
   and not exists (
         select 1 from goods_receipts gr
          where gr.po_id = po.id
            and gr.status <> all(%(void_statuses)s)
       )
 order by coalesce(po.expected_delivery, pr.required_by) nulls last
"""

UPDATE_SQL = """
update purchase_requests
   set service_completion_date = %(value)s,
       updated_at = now()
 where id = %(pr_id)s
   and service_completion_date is null
"""


def _dsn() -> str:
    return (
        f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
        f"dbname={settings.POSTGRES_DB} user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    conn = psycopg2.connect(_dsn())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(CANDIDATES_SQL, {
                "types": list(SERVICE_TYPES),
                "open_statuses": list(OPEN_PO_STATUSES),
                "void_statuses": list(GR_VOID_STATUSES),
            })
            rows = cur.fetchall()

        fillable = [r for r in rows if r["inferred"] is not None]
        skipped = [r for r in rows if r["inferred"] is None]

        print(f"Open service/project POs with no completion date: {len(rows)}")
        print(f"  can infer a date : {len(fillable)}")
        print(f"  no date to infer : {len(skipped)}")
        print()

        for r in fillable:
            src = "PO.expected_delivery" if r["expected_delivery"] else "PR.required_by"
            print(f"  {r['po_number']}  (PR {r['pr_number']})  -> {r['inferred']}   [{src}]")
        if skipped:
            print()
            print("  Left alone — neither PO.expected_delivery nor PR.required_by is set:")
            for r in skipped:
                print(f"    {r['po_number']}  (PR {r['pr_number']})")

        if args.dry_run:
            print()
            print("--dry-run: nothing written.")
            return 0

        with conn.cursor() as cur:
            for r in fillable:
                cur.execute(UPDATE_SQL, {"value": r["inferred"], "pr_id": r["pr_id"]})
        conn.commit()
        print()
        print(f"Updated {len(fillable)} purchase request(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
