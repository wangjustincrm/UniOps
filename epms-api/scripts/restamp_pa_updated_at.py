"""Restore PaymentApplication.updated_at to the SharePoint Modified date.

The invoice clear/rebuild passes wrote PA.invoice_ids, which tripped
onupdate=now() and bumped `updated_at` to the run date — inflating time-based
dashboards ("Paid This Month", "Processed This Month", all keyed on PA.updated_at)
and making the incremental sync treat every touched PA as "locally edited".

This re-stamps `updated_at` from the staged pa.json / pa_backup.json `Modified`
value, but ONLY for PAs whose updated_at is on/after the corruption cutoff (so
genuinely-recent edits before that date are left alone).

Safe by default (dry-run). Idempotent.

Usage (inside the epms-api container; pa.json must be staged from the last import):
    python -m scripts.restamp_pa_updated_at                              # dry-run
    python -m scripts.restamp_pa_updated_at --commit --allow-production
    python -m scripts.restamp_pa_updated_at --cutoff 2026-07-07 --commit --allow-production
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

import app.models  # noqa: F401
from app.core.config import settings
from app.models.pa import PaymentApplication
from scripts.import_pms.extract import load_staging
from scripts.import_pms.transform import to_dt

PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


def _pa_modified() -> dict[str, datetime]:
    """pa_number → Modified datetime (a real value wins over blank/None)."""
    out: dict[str, datetime] = {}
    for f in ("pa.json", "pa_backup.json"):
        for r in load_staging(f):
            num = str(r.get("Title") or "").strip()
            dt = to_dt(r.get("Modified")) or to_dt(r.get("Created"))
            if num and dt and num not in out:
                out[num] = dt
    return out


async def run(dry_run: bool, cutoff: date, db_url: str | None = None,
              allow_production: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(f"REFUSING to run against production DB ({host}); pass --allow-production.")
    print(f"Target DB: {host}  cutoff>={cutoff}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    pa_mod = _pa_modified()
    print(f"  staged PA Modified dates: {len(pa_mod)}")

    stats = {"restamped": 0, "no_modified": 0, "skipped_old": 0}
    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            pas = (await db.execute(select(PaymentApplication))).scalars().all()
            for pa in pas:
                if pa.updated_at is None or pa.updated_at.date() < cutoff:
                    stats["skipped_old"] += 1
                    continue
                dt = pa_mod.get(pa.pa_number or "")
                if dt is None:
                    stats["no_modified"] += 1
                    continue
                pa.updated_at = dt
                flag_modified(pa, "updated_at")  # force into UPDATE, overriding onupdate
                stats["restamped"] += 1
            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(
        f"\nRe-stamp PA.updated_at ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  restamped to SP Modified:  {stats['restamped']}\n"
        f"  skipped (updated before cutoff): {stats['skipped_old']}\n"
        f"  no staged Modified:        {stats['no_modified']}"
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.restamp_pa_updated_at")
    p.add_argument("--commit", action="store_true", help="actually write (default dry-run)")
    p.add_argument("--cutoff", default=date.today().isoformat(),
                   help="only restamp PAs updated on/after this YYYY-MM-DD (default: today)")
    p.add_argument("--db-url")
    p.add_argument("--allow-production", action="store_true")
    args = p.parse_args(argv)
    asyncio.run(run(dry_run=not args.commit, cutoff=date.fromisoformat(args.cutoff),
                    db_url=args.db_url, allow_production=args.allow_production))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
