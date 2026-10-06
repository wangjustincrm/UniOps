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


class TestACallerCannotAddressEveryCauseAtOnce:
    """★★ Round 5 review (uniops-41). `crud/cause.py` was generalised so that more
    callers would use it. Passing neither owner used to produce `hazard_id IS
    NULL` — every incident's causes in the plant — and `replace` would then delete
    every one of those with no action on it. One forgotten argument away from
    wiping the root causes of every investigation on file.
    """

    async def test_neither_owner_is_refused_before_anything_is_touched(
            self, hse_manager, db_session):
        from sqlalchemy import func, select

        from app.crud import cause as cause_crud

        _, client = hse_manager
        incident_id = await _an_incident(client)
        await client.post(f"/api/v1/incidents/{incident_id}/submit")
        await client.put(f"/api/v1/incidents/{incident_id}/causes", json={
            "causes": [{"cause_type": "root", "label": "Must survive"}]})
        before = (await db_session.execute(
            select(func.count()).select_from(Cause))).scalar()
        assert before >= 1, "there must be something to lose, or this proves nothing"

        with pytest.raises(ValueError, match="exactly one record"):
            await cause_crud.replace(
                db_session, causes=[], source_type="manual",
                source_id=uuid.uuid4(), source_ref=None)

        after = (await db_session.execute(
            select(func.count()).select_from(Cause))).scalar()
        assert after == before, "a refused call must not have deleted anything"

    async def test_both_owners_are_refused(self, db_session):
        from app.crud import cause as cause_crud

        with pytest.raises(ValueError, match="exactly one record"):
            await cause_crud.list_for(
                db_session, incident_id=uuid.uuid4(), hazard_id=uuid.uuid4())

    async def test_reading_needs_an_owner_too(self, db_session):
        """Reading every cause by accident is less destructive than deleting them,
        and still wrong: a screen would show another record's causes as its own."""
        from app.crud import cause as cause_crud

        with pytest.raises(ValueError, match="exactly one record"):
            await cause_crud.list_for(db_session)
