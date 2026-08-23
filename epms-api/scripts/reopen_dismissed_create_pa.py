"""One-time cleanup: reopen create_pa tasks a user dismissed without raising a PA.

"Mark Done" on a create_pa task only flips `is_completed` — it does not create the
Payment Application. `crud/task.py::_already_surfaced` then treats a user
completion (`completed_by IS NOT NULL`) as a PERMANENT dismissal, so the read-side
backfill never re-raises it. That rule exists for a good reason (it fixed a
duplicate-task race — see scripts/dedupe_open_tasks.py), but it means a PO whose
goods were received AND invoiced can end up with nobody being asked to pay: the
vendor's money is silently dropped.

Production had two such POs (PO-199-2607-04, PO-665-2607-01). The Mark Done button
has since been hidden in the UI, so reopening these cannot be undone by the same
mistake.

Scope — only rows where ALL of the following hold, so nothing else is touched:
  * type='create_pa', document_type='po', completed by a USER (not the system),
  * the PO has NO Payment Application at all,
  * the PO HAS a matched invoice, and
  * the PO HAS a goods receipt.
That is exactly "the work is real and still undone".

Reopened rows survive the read-side sweeps by construction:
  * `_complete_stale_create_pa_tasks` only closes when a PA exists — there is none;
  * `_complete_orphan_create_pa_tasks` only closes when there is no matched invoice
    AND no PA — there IS a matched invoice;
  * `_already_surfaced` counts an OPEN task as surfaced, so the backfill will not
    add a duplicate alongside it.

Safe by default (dry-run). Pass --commit to write. Idempotent: after a run the
rows no longer match `completed_by IS NOT NULL`, so a second run finds nothing.

Usage (from the epms-api dir / inside the epms-api container):
    python -m scripts.reopen_dismissed_create_pa              # dry-run
    python -m scripts.reopen_dismissed_create_pa --commit
    python -m scripts.reopen_dismissed_create_pa --db-url postgresql+asyncpg://u:p@host/db
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings

# Hosts that must never be touched without an explicit --allow-production.
PRODUCTION_HOSTS = ("10.10.50.20",)

# The single predicate used by BOTH the pre-scan and the UPDATE, so the preview
# can never describe a different row set than the write (they drifted apart in an
# earlier one-off and the report was quietly wrong).
_SCOPE = """
    k.document_type = 'po'
AND k.type          = 'create_pa'
AND k.is_completed  = true
AND k.completed_by IS NOT NULL
AND NOT EXISTS (SELECT 1 FROM payment_applications pa WHERE pa.po_id = p.id)
AND EXISTS (SELECT 1 FROM invoices i WHERE i.po_id = p.id AND i.status = 'matched')
AND EXISTS (SELECT 1 FROM goods_receipts g WHERE g.po_id = p.id)
"""


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def run(dry_run: bool, db_url: str | None = None, allow_production: bool = False) -> int:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}). Re-run with "
            f"--allow-production if that is truly intended."
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            rows = (await db.execute(text(
                "SELECT p.number, p.status, k.assigned_role, k.completed_at "
                "FROM tasks k JOIN purchase_orders p ON p.id = k.document_id "
                f"WHERE {_SCOPE} "
                "ORDER BY p.number"
            ))).all()
            print(f"Dismissed create_pa tasks with no PA: {len(rows)}")
            for r in rows:
                print(f"  {r.number:<18} po={r.status:<16} role={r.assigned_role:<12} "
                      f"dismissed={r.completed_at:%Y-%m-%d}")

            res = await db.execute(text(
                "UPDATE tasks k "
                "   SET is_completed = false, completed_at = NULL, completed_by = NULL "
                "  FROM purchase_orders p "
                f" WHERE p.id = k.document_id AND {_SCOPE}"
            ))
            changed = res.rowcount or 0

            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(f"\nTasks {'that would be' if dry_run else ''} reopened: {changed}"
          f"  ({'DRY-RUN — nothing written' if dry_run else 'COMMITTED'})")
    return changed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.reopen_dismissed_create_pa")
    p.add_argument("--commit", action="store_true", help="actually write (default dry-run)")
    p.add_argument("--db-url", help="explicit async DB URL (overrides settings)")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    args = p.parse_args(argv)
    asyncio.run(run(dry_run=not args.commit, db_url=args.db_url,
                    allow_production=args.allow_production))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
