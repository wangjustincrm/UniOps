"""Shared sequential document-number allocation (expense-api copy).

Claim numbers are ``<prefix><zero-padded sequence>`` — e.g. ``EXP-20260727-0001``,
``MIL-20260727-0001``. The sequence keys off the MAX existing tail, NOT ``count(*)``.

Why not count(*): when an earlier claim in the prefix window is deleted/renumbered,
``count()`` lags the real max tail, so ``count()+1`` regenerates an already-existing
number → UniqueViolation on claim_number's unique index. (Same defect fixed in
epms-api crud; kept as a per-service copy since services don't share a crud package.)

Concurrency: a transaction-scoped Postgres advisory lock on the prefix serializes
concurrent allocations for the SAME prefix, so two in-flight requests can't read the
same max and mint the same number. Different prefixes never contend; the lock
releases automatically on commit/rollback.
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def next_number(db: AsyncSession, column, prefix: str, width: int) -> str:
    # Serialize same-prefix allocators (transaction-scoped; auto-released on commit).
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(prefix))))
    rows = (await db.execute(
        select(column).where(column.like(f"{prefix}%"))
    )).scalars().all()
    max_seq = 0
    plen = len(prefix)
    for n in rows:
        tail = n[plen:]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:0{width}d}"
