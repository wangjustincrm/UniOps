"""One-shot repair: close the Confirm-goods-receipt tasks that nobody can ever
satisfy.

A ``confirm_receipt`` task asks someone to record a receipt. Two defects left
eleven of them open in production against POs whose goods were already in the
building — one of them (PO-089-2607-14) pointing at a PO that was fully
received, invoiced, and paid the day before the task was created:

1. receipt evidence was read as "a ``goods_receipts`` row exists". A
   PMS-migrated PO carries its receipt in ``po_line_items.received_qty`` and
   has no GR document at all (97 of production's 247 fully_received POs), so it
   was judged "not received" forever — and "go create a GR" was never going to
   happen for a PO received years ago.
2. nothing ever closed the task except creating a GR
   (``crud.gr._on_three_way_reached``). When the 3-way was reached any other
   way — an AP re-match linking the GR, which is exactly what happened to
   PO-089-2607-14 — the nudge beside it was orphaned.

The code fixes are in ``crud/po.py`` (``po_has_receipt_evidence``),
``api/v1/invoices.py`` (close on the create_pa path, never nudge a PO that
already shows receipt) and ``crud/invoice.py`` (``review_match`` re-runs GR
discovery). This pass applies the SAME predicate to the tasks that predate them,
importing the helper itself so the two cannot drift apart.

A PO with no receipt evidence at all keeps its task — the nudge is correct
there. Production has three such POs, and one of them (PO-700-2605-03: nothing
received, a PA already paid) wants a human, not a script.

Idempotent: re-running finds nothing left to do.

Usage (inside the epms-api container):
    python -m scripts.close_stale_confirm_receipt_tasks                        # dry-run
    python -m scripts.close_stale_confirm_receipt_tasks --apply --allow-production
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings
from app.crud.po import po_has_receipt_evidence
from app.models.task import Task

PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def close_stale(dry_run: bool, db_url: str | None = None,
                      allow_production: bool = False) -> dict:
    url = str(db_url or settings.DATABASE_URL)
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    stats: dict[str, int] = defaultdict(int)
    closed: list[tuple[str, str, str]] = []
    kept: list[tuple[str, str]] = []
    now = datetime.now(timezone.utc)

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            candidates = (await db.execute(
                select(Task).where(
                    Task.type == "confirm_receipt",
                    Task.document_type == "po",
                    Task.is_completed.is_(False),
                ).order_by(Task.created_at)
            )).scalars().all()
            stats["candidates"] = len(candidates)

            for t in candidates:
                if not await po_has_receipt_evidence(db, t.document_id):
                    stats["kept_no_receipt"] += 1
                    kept.append((t.document_number, t.assigned_role))
                    continue
                t.is_completed = True
                t.completed_at = now
                stats["closed"] += 1
                closed.append((t.document_number, t.assigned_role,
                               t.created_at.date().isoformat()))

            await db.flush()
            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(
        f"Close stale confirm_receipt tasks ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  open confirm_receipt tasks scanned: {stats['candidates']}\n"
        f"  ✓ closed (PO already shows receipt): {stats['closed']}\n"
        f"  · kept (nothing received yet):       {stats['kept_no_receipt']}"
    )
    if closed:
        print("\n  CLOSED:")
        print(f"  {'PO':<16} {'assignee role':<18} raised")
        for number, role, raised in closed:
            print(f"  {number:<16} {role:<18} {raised}")
    if kept:
        print("\n  KEPT (the nudge is still valid — nothing received):")
        for number, role in kept:
            print(f"  {number:<16} {role}")
    return dict(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default is dry-run)")
    ap.add_argument("--allow-production", action="store_true")
    ap.add_argument("--db-url", default=None)
    args = ap.parse_args()
    asyncio.run(close_stale(
        dry_run=not args.apply, db_url=args.db_url,
        allow_production=args.allow_production,
    ))


if __name__ == "__main__":
    main()
