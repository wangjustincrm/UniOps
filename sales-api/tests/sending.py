"""What the sending actions' tests share (stage 3 g–k): an outbox row in a state, and two presses at once. Fakes only."""
import asyncio
from datetime import timedelta

import sqlalchemy as sa

from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.clock import utc_now
from app.platform.errors import BusinessError

# (status, its lease or due time from now, what a press says: a code and a state — or None: it does not block)
STATES = [("PENDING", -1, ("send.queued", "pending")), ("IN_FLIGHT", 10, ("send.queued", "sending")),
          ("IN_FLIGHT", -10, ("send.outcome_unknown", "unknown")), ("FAILED", -1, None), ("SUPPRESSED", -1, None),
          ("CANCELLED", -1, None), ("DONE", -1, None)]
STATE_IDS = ["pending", "in_flight", "in_flight_expired", "failed", "suppressed", "cancelled", "done"]


async def put_row(t, kind: str, payload: dict, entity: tuple[str, int], status: str, minutes: int = -1) -> int:
    """An outbox row as a worker left it."""
    async with t.factory(SYSTEM_ACTOR) as u:
        r = OutboxRecord(kind=kind, payload=payload, status=status, attempts=1, manual=False, entity_type=entity[0], entity_id=str(entity[1]),
                         next_attempt_at=utc_now() + timedelta(minutes=minutes))
        u.session.add(r)
        await u.session.flush()
        id = r.id
        await u.commit()
    return id


async def set_row(t, id: int, **values) -> None:
    async with t.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(OutboxRecord).where(OutboxRecord.id == id).values(**values))
        await u.commit()


async def rows(t, *kinds: str) -> list[dict]:
    return await t.q("SELECT id, kind, status, last_error FROM sales_outbox WHERE kind = ANY(:k) ORDER BY id", k=list(kinds))


async def says(press) -> BusinessError | None:
    """What a press is told, or None: it went through."""
    try:
        await press()
    except BusinessError as e:
        return e
    return None


async def twice(t, actor, work) -> BusinessError | None:
    """The same press by two people at once: the first has done its work and not committed when the second comes, which
    must wait for the record (else both see nothing under way). The first commits; what the second is then told."""
    async def second():
        async with t.factory(actor) as u:
            await work(u)
            await u.commit()
    async with t.factory(actor) as u:
        await work(u)
        task = asyncio.create_task(says(second))
        await asyncio.sleep(0.4)
        assert not task.done(), "the second press did not wait for the first"
        await u.commit()
    return await asyncio.wait_for(task, 10)


def said(e: BusinessError | None) -> tuple[str, str | None] | None:
    return None if e is None else (e.code, e.details.get("state"))


def way_out(e: BusinessError) -> bool:
    """An unknown outcome says that it may have gone out, how it ends, and who may do it."""
    return all(x in e.message for x in ("may have gone out", "Run the system job now", "sales.file.retry"))
