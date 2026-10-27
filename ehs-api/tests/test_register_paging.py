"""★ Review (uniops-8b): the register is read 200 at a time by OFFSET, ordered
only by when each happened. Incidents at the same instant had no fixed order,
so paging could repeat one and skip another."""
import uuid
from datetime import date, datetime, timezone

from app.crud import incident as crud
from app.models.incident import Incident


async def test_same_instant_pages_neither_repeat_nor_skip(db_session):
    at = datetime(2001, 1, 1, 12, 0, tzinfo=timezone.utc)
    made = []
    for i in range(7):
        row = Incident(id=uuid.uuid4(), incident_no=f"PG-{uuid.uuid4().hex[:8]}", form_kind="medical",
                       title=f"Same instant {i}", occurred_at=at, status="submitted")
        db_session.add(row)
        made.append(row.id)
    await db_session.flush()
    for order in ("newest", "oldest", "status"):
        seen = []
        for offset in range(0, 7, 3):
            rows = await crud.list_(db_session, limit=3, offset=offset, order=order,
                                    occurred_from=date(2001, 1, 1), occurred_to=date(2001, 1, 1))
            seen += [r.id for r in rows]
        assert sorted(seen) == sorted(made), order
        # A fixed order, not whatever the database returned this time: ties
        # by id. (Ids are random, so the order of insertion is not this one.)
        assert seen == sorted(made), order
