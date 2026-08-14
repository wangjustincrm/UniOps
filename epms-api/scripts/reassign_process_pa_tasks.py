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

Idempotent: a second run finds no more ap_clerk rows and changes nothing —
and, because it changed nothing, leaves an existing moved-ids file (below)
exactly as it is rather than overwriting it with an empty ids list.

Reverse (rollback) path, same script, --revert. IMPORTANT: after release,
approval-api starts assigning brand-new process_pa tasks directly to
payment_officer (that is the entire point of the split) — those are
correctly-assigned rows this script never touched, and a revert run any time
after new PAs start flowing must not sweep them back to ap_clerk. So a
successful forward --apply writes the ids of the rows it actually moved to a
JSON file (default: reassign_process_pa_tasks.moved.json next to this
script, override with --ids-file), and --revert reads that file and
restricts the reverse UPDATE to exactly those ids (still additionally
filtered to assigned_role='payment_officer' AND is_completed=false, so a row
someone has since completed or hand-reassigned is left alone either way).

--revert with no ids file present refuses by default — an unscoped revert
would also catch every legitimately-assigned new task. Pass --revert-all to
explicitly opt into that full, unscoped sweep (e.g. reverting minutes after
the forward run, before any new tasks could plausibly have landed).

Read-only inventory (no --apply): the release checklist needs the backlog
count *before* the migration runs, as the expected-row-count to verify the
real run against afterwards. Dry-run mode builds the real UPDATE inside a
transaction (so the reported "changed" count is exact, not a separate COUNT
query that could drift from the real WHERE clause) and rolls back instead of
committing; it never writes the ids file, since nothing was actually moved.

Usage (inside the epms-api container):
    python -m scripts.reassign_process_pa_tasks                          # dry-run (default)
    python -m scripts.reassign_process_pa_tasks --apply --allow-production
    python -m scripts.reassign_process_pa_tasks --revert --apply --allow-production        # rollback, scoped to moved-ids file
    python -m scripts.reassign_process_pa_tasks --revert --revert-all --apply --allow-production   # rollback, unscoped sweep (explicit opt-in)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings
from app.models.task import Task

TASK_TYPE = "process_pa"

# Hosts that must never be touched without an explicit --allow-production.
PRODUCTION_HOSTS = ("10.10.50.20",)

# Where a successful forward --apply records which task ids it moved, so
# --revert can undo exactly those rows instead of sweeping every open
# payment_officer/process_pa task (which after release also includes tasks
# approval-api assigned directly, never touched by this script's forward run).
DEFAULT_IDS_FILE = Path(__file__).parent / "reassign_process_pa_tasks.moved.json"


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def reassign(dry_run: bool, db_url: str | None = None,
                    allow_production: bool = False, revert: bool = False,
                    revert_all: bool = False,
                    ids_file: str | Path | None = None) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )

    from_role, to_role = ("payment_officer", "ap_clerk") if revert else ("ap_clerk", "payment_officer")
    ids_path = Path(ids_file) if ids_file is not None else DEFAULT_IDS_FILE

    restrict_ids: list[uuid.UUID] | None = None
    if revert and not revert_all:
        if not ids_path.exists():
            raise SystemExit(
                f"No moved-ids record at {ids_path}; refusing to revert every open "
                f"{to_role}/{TASK_TYPE} task blind. Since release, approval-api also "
                f"assigns new {TASK_TYPE} tasks directly to payment_officer (that is "
                f"the point of the split) — an unscoped revert would sweep those back "
                f"to ap_clerk too, not just the rows this script moved. Pass "
                f"--revert-all (revert_all=True) to explicitly opt into that full, "
                f"unscoped sweep instead."
            )
        try:
            record = json.loads(ids_path.read_text())
            restrict_ids = [uuid.UUID(i) for i in record["ids"]]
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise SystemExit(
                f"Moved-ids record at {ids_path} is unreadable ({exc!r}); refusing to "
                f"revert. Fix or remove the file, or pass --revert-all for an explicit "
                f"unscoped sweep instead."
            ) from exc

    scope_note = "FORWARD"
    if revert:
        scope_note = "REVERT (--revert-all, unscoped)" if revert_all else "REVERT (scoped to moved-ids file)"
    print(
        f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})  "
        f"[{scope_note}: {from_role} -> {to_role}]\n"
    )

    stats: dict = {}
    changed_ids: list[uuid.UUID] = []
    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            scanned = (await db.execute(
                select(func.count()).select_from(Task).where(
                    Task.type == TASK_TYPE, Task.is_completed.is_(False),
                )
            )).scalar_one()
            stats["scanned"] = int(scanned)

            already_on_target = (await db.execute(
                select(func.count()).select_from(Task).where(
                    Task.type == TASK_TYPE, Task.is_completed.is_(False),
                    Task.assigned_role == to_role,
                )
            )).scalar_one()
            stats["already_on_target"] = int(already_on_target)

            # Built inside this transaction even on dry-run, then rolled back
            # below — the returned ids/count reflect exactly what the real
            # WHERE clause would touch, not a separate hand-written COUNT
            # that could drift out of sync with it.
            stmt = update(Task).where(
                Task.type == TASK_TYPE, Task.is_completed.is_(False),
                Task.assigned_role == from_role,
            )
            if restrict_ids is not None:
                stmt = stmt.where(Task.id.in_(restrict_ids))
            stmt = stmt.values(assigned_role=to_role).returning(Task.id)

            res = await db.execute(stmt)
            changed_ids = [row[0] for row in res.fetchall()]
            stats["changed"] = len(changed_ids)

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
        f"  {'would change' if dry_run else 'changed'} {from_role} -> {to_role}: {stats['changed']}"
    )

    if not dry_run and not revert:
        if stats["changed"] == 0:
            # Nothing moved this run — most likely a re-run of --apply after
            # the backlog was already migrated (the idempotent no-op case).
            # Leave any existing ids file exactly as it is: overwriting it
            # here would replace a real moved-ids record with an empty one,
            # and a later scoped --revert would then silently revert
            # nothing — the same "quietly does nothing when you need it"
            # failure this file exists to prevent.
            print("  no rows changed; existing ids file left untouched")
        else:
            # Forward apply only: record exactly which rows moved so a later
            # --revert can undo only these, not tasks approval-api has since
            # assigned directly to payment_officer.
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "from_role": from_role,
                "to_role": to_role,
                "count": stats["changed"],
                "ids": [str(i) for i in changed_ids],
            }
            ids_path.write_text(json.dumps(record, indent=2))
            print(f"  moved-ids recorded: {ids_path}")

    stats["changed_ids"] = [str(i) for i in changed_ids]
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.reassign_process_pa_tasks", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="commit (default is dry-run)")
    p.add_argument("--revert", action="store_true",
                   help="rollback path: move process_pa tasks back to ap_clerk")
    p.add_argument("--revert-all", action="store_true",
                   help="with --revert: ignore the moved-ids file and sweep every open "
                        "payment_officer/process_pa task back to ap_clerk. Explicit opt-in "
                        "only — this also reverts tasks approval-api has since assigned "
                        "directly to payment_officer.")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    p.add_argument("--db-url", default=None, help="explicit async DB URL (overrides settings)")
    p.add_argument("--ids-file", default=None,
                   help=f"path to the moved-ids record (default: {DEFAULT_IDS_FILE})")
    args = p.parse_args(argv)
    asyncio.run(reassign(
        dry_run=not args.apply, db_url=args.db_url,
        allow_production=args.allow_production, revert=args.revert,
        revert_all=args.revert_all, ids_file=args.ids_file,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
