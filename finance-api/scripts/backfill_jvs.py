"""Cut-over backfill: ensure every posting_event has a POSTED JV (idempotent).

⚠️ CUT-OVER DAY ONLY. This bulk-posts EVERY draft JV, bypassing the
review→post control — run it after cut-over and you silently post drafts
finance never reviewed. It therefore requires an explicit flag:

    docker exec uniops_finance_api python scripts/backfill_jvs.py --i-am-cutting-over

Run INSIDE the finance-api container (its DATABASE_URL points at the right DB).
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
    if "--i-am-cutting-over" not in sys.argv:
        print("REFUSING: this bulk-posts every draft JV (cut-over day only). "
              "Re-run with --i-am-cutting-over if that is really what you want.")
        raise SystemExit(1)
    async with AsyncSessionLocal() as session:
        res = await backfill_posted_jvs(session)
        await session.commit()
        print(f"backfill: generated={res['generated']} posted={res['posted']}")


if __name__ == "__main__":
    asyncio.run(main())
