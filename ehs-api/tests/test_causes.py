"""Causes belong to exactly one record.

Round 5, answer box 11 put immediate and root causes on the hazard register as
well as on an incident investigation. One table takes both, with two nullable
foreign keys and a database CHECK rather than this module's usual polymorphic
pair — causes come from three kinds of record at most, and both keys keep their
`ON DELETE CASCADE`.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy

from app.models.incident import Cause


async def _an_incident(client) -> str:
    made = await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Something to investigate",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    })
    assert made.status_code in (200, 201), made.text
    return made.json()["id"]


class TestExactlyOneOwner:
    """★ The constraint is declared on the model as well as in the migration.

    The test database is built with `create_all`, so a CHECK that lives only in a
    migration is absent for the whole suite and refuses a row for the first time
    in production. Raised in review by uniops-41; this repository has been caught
    by the same gap before, in `conftest` not building the authz foreign keys.
    """

    async def test_a_cause_with_no_owner_is_refused(self, hse_manager, db_session):
        db_session.add(Cause(
            id=uuid.uuid4(), cause_type="root", label="Belongs to nothing"))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_a_cause_with_two_owners_is_refused(
            self, hse_manager, db_session):
        from app.models.hazard import Hazard

        _, client = hse_manager
        incident_id = await _an_incident(client)
        hazard = Hazard(id=uuid.uuid4(), hazard_no="HAZ-TEST-1",
                        title="A pinch point")
        db_session.add(hazard)
        await db_session.flush()

        db_session.add(Cause(
            id=uuid.uuid4(), cause_type="root", label="Belongs to both",
            incident_id=uuid.UUID(incident_id), hazard_id=hazard.id))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_one_owner_each_way_is_accepted(self, hse_manager, db_session):
        """★ The admission. A constraint that refuses everything satisfies both
        assertions above perfectly well."""
        from app.models.hazard import Hazard

        _, client = hse_manager
        incident_id = await _an_incident(client)
        hazard = Hazard(id=uuid.uuid4(), hazard_no="HAZ-TEST-2",
                        title="An unguarded nip")
        db_session.add(hazard)
        await db_session.flush()

        db_session.add(Cause(id=uuid.uuid4(), cause_type="root",
                             label="On the incident",
                             incident_id=uuid.UUID(incident_id)))
        db_session.add(Cause(id=uuid.uuid4(), cause_type="immediate",
                             label="On the hazard", hazard_id=hazard.id))
        await db_session.flush()

    async def test_deleting_a_hazard_takes_its_causes(self, hse_manager, db_session):
        """★ The cascade this shape was chosen to keep. Going polymorphic would
        have left these behind and then needed a guard against the orphans."""
        from sqlalchemy import select

        from app.models.hazard import Hazard

        hazard = Hazard(id=uuid.uuid4(), hazard_no="HAZ-TEST-3",
                        title="Goes away")
        db_session.add(hazard)
        await db_session.flush()
        db_session.add(Cause(id=uuid.uuid4(), cause_type="root",
                             label="Goes with it", hazard_id=hazard.id))
        await db_session.flush()

        await db_session.delete(hazard)
        await db_session.flush()

        left = (await db_session.execute(
            select(Cause).where(Cause.hazard_id == hazard.id))).scalars().all()
        assert left == []
