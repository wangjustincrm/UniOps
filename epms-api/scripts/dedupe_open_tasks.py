"""One-time cleanup: collapse duplicate OPEN inbox tasks to a single row.

Prod bug (Task Inbox): clicking "Mark Done" on a Place Order task made it come
back as TWO copies. Root cause was a read-side backfill in get_for_role that
re-raised a task the user had completed, executed concurrently by the several
GET /tasks the inbox fires at once (header badge + open list + completed list) —
a race with no unique guard, so two backfills each INSERTed one.

The code fix (crud/task.py) stops new duplicates two ways: a user completion is
now permanent (completed_by IS NOT NULL suppresses the re-raise), and a
transaction-scoped advisory lock serializes the backfills. This script cleans up
the duplicate OPEN rows that accumulated BEFORE the fix shipped.

For each (type, document_type, document_id) group of OPEN tasks with more than
one row, the earliest-created row is kept and the rest are deleted. Scoped to the
three types the backfills create (place_order / create_po / create_pa) so no
approval/other task is ever touched. Deleting the surplus rows (rather than
completing them) is correct: they were never real work — one genuine open task
per document remains.

Safe by default (dry-run). Pass --commit to write. Idempotent: a second run finds
no groups >1 and deletes nothing.

Usage (from the epms-api dir / inside the epms-api container):
    python -m scripts.dedupe_open_tasks              # dry-run
    python -m scripts.dedupe_open_tasks --commit     # write to the configured DB
    python -m scripts.dedupe_open_tasks --db-url postgresql+asyncpg://u:p@host/db
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings

# The task types created by get_for_role's read-side backfills — the only ones
# the duplication race could produce. Never touch approval/GR/other tasks.
_DUP_TYPES = ("place_order", "create_po", "create_pa")

# Hosts that must never be touched without an explicit --allow-production.
PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def run(dry_run: bool, db_url: str | None = None, allow_production: bool = False) -> int:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}). This targets the "
            f"shared prod database. Re-run with --allow-production if that is "
            f"truly intended. (Tip: run inside the epms-api container or pass "
            f"--db-url for the local DB to test safely.)"
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            # Pre-scan: report the duplicate groups before deleting anything.
            groups = (await db.execute(text(
                "SELECT type, document_type, document_number, count(*) AS n "
                "FROM tasks "
                "WHERE is_completed = false AND type = ANY(:types) "
                "GROUP BY type, document_type, document_id, document_number "
                "HAVING count(*) > 1 "
                "ORDER BY n DESC, type"
            ).bindparams(types=list(_DUP_TYPES)))).all()

            surplus = sum(int(g.n) - 1 for g in groups)
            print(f"Duplicate OPEN-task groups: {len(groups)}  (surplus rows to delete: {surplus})")
            for g in groups[:50]:
                print(f"  {g.type:<14} {g.document_type:<4} {g.document_number:<18} x{g.n}")
            if len(groups) > 50:
                print(f"  … and {len(groups) - 50} more groups")

            # Keep the earliest-created open row per (type, document_type,
            # document_id); delete the rest.
            res = await db.execute(text(
                "DELETE FROM tasks a USING ("
                "  SELECT id, row_number() OVER ("
                "    PARTITION BY type, document_type, document_id"
                "    ORDER BY created_at, id) AS rn"
                "  FROM tasks"
                "  WHERE is_completed = false AND type = ANY(:types)"
                ") d "
                "WHERE a.id = d.id AND d.rn > 1"
            ).bindparams(types=list(_DUP_TYPES)))
            deleted = res.rowcount or 0

            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(f"\nSurplus OPEN tasks {'that would be' if dry_run else ''} deleted: {deleted}"
          f"  ({'DRY-RUN — nothing written' if dry_run else 'COMMITTED'})")
    return deleted


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.dedupe_open_tasks")
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
