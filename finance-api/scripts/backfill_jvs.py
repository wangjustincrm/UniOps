"""Cut-over backfill: ensure every posting_event has a POSTED JV (idempotent).

Run INSIDE the finance-api container (its DATABASE_URL points at the right DB):
    docker exec uniops_finance_api python scripts/backfill_jvs.py
NEVER run from the host — the host .env points at production.
"""
import asyncio
import os
import sys

# Runnable as `python scripts/backfill_jvs.py` — put the service root on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crud.journal_voucher import backfill_posted_jvs  # noqa: E402
from app.db.base import AsyncSessionLocal  # noqa: E402


async def main():
    async with AsyncSessionLocal() as session:
        res = await backfill_posted_jvs(session)
        await session.commit()
        print(f"backfill: generated={res['generated']} posted={res['posted']}")


if __name__ == "__main__":
    asyncio.run(main())
