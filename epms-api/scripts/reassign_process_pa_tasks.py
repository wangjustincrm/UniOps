"""One-shot backlog move: shift open process_pa tasks from ap_clerk to
payment_officer.

feature/ap-payment-officer-and-match-visibility splits payment authority out
of ap_clerk into a new payment_officer role. approval-api's crud/engine.py
now assigns *newly created* process_pa tasks to payment_officer — but that is
purely forward-looking. Every process_pa task already sitting open on
ap_clerk before this code shipped keeps saying ap_clerk forever, which means
AP's inbox is exactly as full on release day as it was before the split.
This script clears that backlog in one pass.

tasks is a shared table (both approval-api and epms-api read/write it in the
same physical database); this script lives in epms-api/scripts for
consistency with the sibling backfill_exception_tasks.py that just landed
there, and because it only touches rows, not any model unique to one
service's ORM.

Scope, deliberately narrow:
    type = 'process_pa' AND is_completed = false AND assigned_role = 'ap_clerk'
Completed tasks are historical record and are never touched. Other task
types on ap_clerk (e.g. resolve_exception) are untouched — only the payment
workflow moved roles, not AP clerk's other duties.

Idempotent: a second run finds no more ap_clerk rows and changes nothing.

Reverse (rollback) path, same script, --revert:
    type = 'process_pa' AND is_completed = false AND assigned_role = 'payment_officer'
    -> assigned_role = 'ap_clerk'
This is the rollback path documented in the release notes if the role split
needs to be backed out.

Read-only inventory (no --apply): the release checklist needs the backlog
count *before* the migration runs, as the expected-row-count to verify the
real run against afterwards. Dry-run mode computes the real UPDATE inside a
transaction (so the reported "changed" count is exact, not a separate COUNT
query that could drift from the real WHERE clause) and rolls back instead of
committing.

Usage (inside the epms-api container):
    python -m scripts.reassign_process_pa_tasks                          # dry-run (default)
    python -m scripts.reassign_process_pa_tasks --apply --allow-production
    python -m scripts.reassign_process_pa_tasks --revert --apply --allow-production   # rollback
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings

TASK_TYPE = "process_pa"

# Hosts that must never be touched without an explicit --allow-production.
PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def reassign(dry_run: bool, db_url: str | None = None,
                    allow_production: bool = False, revert: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )

    from_role, to_role = ("payment_officer", "ap_clerk") if revert else ("ap_clerk", "payment_officer")

    print(
        f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})  "
        f"[{'REVERT' if revert else 'FORWARD'}: {from_role} -> {to_role}]\n"
    )

    stats: dict[str, int] = {}
    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            scanned = (await db.execute(text(
                "SELECT count(*) FROM tasks WHERE type = :t AND is_completed = false"
            ).bindparams(t=TASK_TYPE))).scalar_one()
            stats["scanned"] = int(scanned)

            already_on_target = (await db.execute(text(
                "SELECT count(*) FROM tasks "
                "WHERE type = :t AND is_completed = false AND assigned_role = :to_role"
            ).bindparams(t=TASK_TYPE, to_role=to_role))).scalar_one()
            stats["already_on_target"] = int(already_on_target)

            # Executed inside this transaction even on dry-run, then rolled
            # back below — rowcount reflects the exact rows the real WHERE
            # clause would touch, not a second hand-written COUNT that could
            # drift out of sync with it.
            res = await db.execute(text(
                "UPDATE tasks SET assigned_role = :to_role "
                "WHERE type = :t AND is_completed = false AND assigned_role = :from_role"
            ).bindparams(t=TASK_TYPE, from_role=from_role, to_role=to_role))
            stats["changed"] = res.rowcount or 0

            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(
        f"Reassign {TASK_TYPE} tasks ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  scanned (open {TASK_TYPE}):         {stats['scanned']}\n"
        f"  · already on {to_role}:  {stats['already_on_target']}\n"
        f"  {'would change' if dry_run else '✓ changed'} {from_role} -> {to_role}: {stats['changed']}"
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.reassign_process_pa_tasks", description=__doc__)
    p.add_argument("--apply", action="store_true", help="commit (default is dry-run)")
    p.add_argument("--revert", action="store_true",
                   help="rollback path: move payment_officer process_pa tasks back to ap_clerk")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    p.add_argument("--db-url", default=None, help="explicit async DB URL (overrides settings)")
    args = p.parse_args(argv)
    asyncio.run(reassign(
        dry_run=not args.apply, db_url=args.db_url,
        allow_production=args.allow_production, revert=args.revert,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
