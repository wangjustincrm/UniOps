"""CLI for the PMS → EPMS migration.

Examples (run from the epms-api directory, using the project venv):

  # 1) Pull SharePoint lists into local JSON staging (read-only on EPMS):
  python -m scripts.import_pms --extract

  # 2) Dry-run the load (read-only on EPMS — builds & validates, writes nothing):
  python -m scripts.import_pms --load

  # 3) Commit for real (atomic, idempotent). Requires --commit and --yes:
  python -m scripts.import_pms --load --commit --yes

  # Scope to certain entities / target a specific DB:
  python -m scripts.import_pms --load --only pr,po
  python -m scripts.import_pms --load --commit --yes --env production
  python -m scripts.import_pms --load --db-url postgresql+asyncpg://u:p@host/db

SharePoint credentials are read from the environment:
  SP_USER, SP_PASSWORD  (SP_TENANT / SP_SITE optional — sensible defaults).
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def _csv(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {x.strip() for x in value.split(",") if x.strip()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.import_pms", description="PMS → EPMS migration")
    p.add_argument("--extract", action="store_true", help="pull SharePoint lists into data/")
    p.add_argument("--load", action="store_true", help="transform staging and load into EPMS")
    p.add_argument("--commit", action="store_true", help="actually write (default is dry-run)")
    p.add_argument("--yes", action="store_true", help="skip the commit confirmation prompt")
    p.add_argument("--only", help="comma list to scope (extract: file stems; load: pr,po,invoice,pa)")
    p.add_argument("--since", help="extract: only rows Modified at/after this ISO UTC (incremental)")
    p.add_argument("--mode", choices=["insert", "upsert"], default="insert",
                   help="load: insert=new only; upsert=incremental header update + new")
    p.add_argument("--no-attachments", action="store_true",
                   help="extract: skip downloading invoice attachment files")
    p.add_argument("--no-reconstruct-events", action="store_true",
                   help="load: skip rebuilding approval_events for imported docs")
    p.add_argument("--no-dedup-invoices", action="store_true",
                   help="load: skip the invoice + attachment dedup pass")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--env", help="load DB config from .env.<env> instead of .env")
    p.add_argument("--db-url", help="explicit async DB URL (overrides --env/.env)")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    args = p.parse_args(argv)

    if not args.extract and not args.load:
        p.error("nothing to do — pass --extract and/or --load")

    if args.extract:
        from .extract import extract
        extract(only=_csv(args.only), since=args.since, skip_attachments=args.no_attachments)

    if args.load:
        db_url = args.db_url
        if not db_url and args.env:
            from app.core.config import Settings
            db_url = Settings(_env_file=f".env.{args.env}").DATABASE_URL  # type: ignore[call-arg]

        # Guard: never touch the shared production DB unless explicitly allowed.
        effective_url = db_url or _default_url()
        if "10.10.50.20" in effective_url and not args.allow_production:
            p.error(
                "refusing to target production DB (10.10.50.20). Pass "
                "--allow-production if that is truly intended, or run inside the "
                "epms-api container / pass --db-url for the local DB."
            )

        dry_run = not args.commit
        if not dry_run and not args.yes:
            host = (db_url or _default_host())
            reply = input(f"About to COMMIT migration to {host!r}. Type 'yes' to proceed: ")
            if reply.strip().lower() != "yes":
                print("Aborted.")
                return 1

        from . import state
        ts = state.now_iso()
        from .load import run_load
        asyncio.run(run_load(
            only=_csv(args.only),
            dry_run=dry_run,
            batch_size=args.batch_size,
            db_url=db_url,
            mode=args.mode,
            reconstruct=not args.no_reconstruct_events,
            dedup_invoices=not args.no_dedup_invoices,
        ))
        # Invoice attachments → file server + invoice_attachments table.
        if not args.only or "invoice" in (_csv(args.only) or set()):
            from .attachments import sync_invoice_attachments
            att = asyncio.run(sync_invoice_attachments(dry_run=dry_run, db_url=db_url))
            print(f"\nInvoice attachments: {att.to_dict()}")

        # Advance the incremental watermark on a real commit (so the admin page's
        # "Incremental sync" works after a CLI-driven import too).
        if not dry_run and not args.only:
            state.set_last_sync(ts)
            print(f"\nWatermark advanced to {ts}")

    return 0


def _default_url() -> str:
    from app.core.config import settings
    return settings.DATABASE_URL


def _default_host() -> str:
    return _default_url().split("@")[-1].split("/")[0]


if __name__ == "__main__":
    sys.exit(main())
