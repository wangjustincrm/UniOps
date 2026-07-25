# scripts/qbo_import/run_import.py
"""CLI entry point for the QBO AP-core import.

Usage (run INSIDE the finance-api container, or with an explicit DATABASE_URL —
the host .env points at the production DB):
    python scripts/qbo_import/run_import.py --full
    python scripts/qbo_import/run_import.py --incremental
    python scripts/qbo_import/run_import.py --full --entities Account,Vendor
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.base import AsyncSessionLocal  # noqa: E402
from scripts.qbo_import.attachments import load_attachments  # noqa: E402
from scripts.qbo_import.client import QboClient  # noqa: E402
from scripts.qbo_import.extract import extract_entity  # noqa: E402
from scripts.qbo_import.orchestrator import DEFAULT_ENTITIES, run_sync  # noqa: E402


async def _main(mode: str, entities: list[str], with_attachments: bool = True) -> int:
    client = QboClient()
    async with AsyncSessionLocal() as db:
        run = await run_sync(db, client, mode=mode, entities=entities)
    print(f"{mode} sync {run.status}")
    for name, c in (run.counters or {}).items():
        print(f"  {name}: {c}")
    if run.error:
        print(f"  error: {run.error}")
        return 1

    if with_attachments:
        async with AsyncSessionLocal() as db:
            objs = extract_entity(client, "Attachable", since=None)
            n = await load_attachments(db, objs)
            print(f"  Attachable: {n}")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true")
    g.add_argument("--incremental", action="store_true")
    ap.add_argument("--entities", help="comma-separated subset (default: AP core)")
    ap.add_argument("--no-attachments", action="store_true", help="skip Attachable pull (default: pulled)")
    args = ap.parse_args()
    mode = "full" if args.full else "incremental"
    entities = args.entities.split(",") if args.entities else DEFAULT_ENTITIES
    return asyncio.run(_main(mode, entities, with_attachments=not args.no_attachments))


if __name__ == "__main__":
    raise SystemExit(main())
