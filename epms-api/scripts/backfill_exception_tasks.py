"""One-shot repair: raise the resolve_exception AP task for invoices that
were already sitting in status='exception' before that task type existed.

feature/ap-payment-officer-and-match-visibility made EPMS raise a
resolve_exception task (assigned_role="ap_clerk" pool) whenever an invoice
lands outside match tolerance, via the shared _sync_exception_task helper in
app/api/v1/invoices.py — but that is purely forward-looking. Invoices that
reached status='exception' before this code existed never got a task, so
AP has no way to discover them: e.g. INV-2026-0150 went exception on
2026-08-05 with zero resolve_exception tasks and only a match_invoice task
pinned to one specific user — AP sees nothing.

This pass finds every invoice with status='exception' and no open
resolve_exception task, and creates one via build_exception_task() —
app/api/v1/invoices.py's own task-construction helper — so the backfilled
task is byte-for-byte the same shape the live code produces (role pool,
title, description, vendor/amount) instead of a second hand-rolled copy
that can drift out of sync.

Deliberately does NOT send notifications. The live path emails the AP pool
the moment a single invoice lands in exception; replaying that per backfilled
invoice would blast the AP pool with an email per invoice in one shot for a
purely historical backlog nobody is triaging in real time. Getting these
onto the Task Inbox is the goal; a mass-email side effect is not something
anyone asked for. AP will see them next time they open the inbox, same as
any other pool task.

Idempotent: an invoice that already has an open resolve_exception task
(including one this script created on a prior run) is left alone.

Usage (inside the epms-api container):
    python -m scripts.backfill_exception_tasks                        # dry-run (default)
    python -m scripts.backfill_exception_tasks --apply --allow-production
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.api.v1.invoices import build_exception_task
from app.core.config import settings
from app.models.invoice import Invoice
from app.models.task import Task

PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def backfill(dry_run: bool, db_url: str | None = None,
                   allow_production: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    stats: dict[str, int] = defaultdict(int)
    created: list[tuple[str, str, str]] = []

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            candidates = (await db.execute(
                select(Invoice).where(
                    Invoice.status == "exception",
                ).order_by(Invoice.internal_ref)
            )).scalars().all()
            stats["scanned"] = len(candidates)

            for inv in candidates:
                existing = (await db.execute(select(Task.id).where(
                    Task.type == "resolve_exception", Task.document_type == "invoice",
                    Task.document_id == inv.id, Task.is_completed.is_(False),
                ))).scalar_one_or_none()
                if existing is not None:
                    stats["already_has_task"] += 1
                    continue

                # created_by=None: no actor initiated this — matches the
                # documented convention for historical tasks (Task.created_by
                # docstring: "历史任务为 NULL").
                exc_task = build_exception_task(inv, inv.exception_reason, None)
                db.add(exc_task)
                stats["created"] += 1
                created.append((inv.internal_ref, inv.vendor_name, str(inv.total_amount)))

            await db.flush()
            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(
        f"Backfill resolve_exception tasks ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  scanned (status=exception):    {stats['scanned']}\n"
        f"  ✓ created:                     {stats['created']}\n"
        f"  · already had an open task:    {stats['already_has_task']}"
    )
    if created:
        print("\n  invoice          vendor                          amount")
        for ref, vendor, amount in created:
            print(f"  {ref:<16} {vendor:<30} {amount}")
    return dict(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default is dry-run)")
    ap.add_argument("--allow-production", action="store_true")
    ap.add_argument("--db-url", default=None)
    args = ap.parse_args()
    asyncio.run(backfill(
        dry_run=not args.apply, db_url=args.db_url,
        allow_production=args.allow_production,
    ))


if __name__ == "__main__":
    main()
