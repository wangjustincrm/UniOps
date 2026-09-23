"""The audit trail: every field, on every form in this module."""
import uuid

from sqlalchemy import select

from app.models.change_log import ChangeLogEntry


async def _trail(db_session, table: str, row_id) -> list[ChangeLogEntry]:
    await db_session.commit()
    return list((await db_session.execute(
        select(ChangeLogEntry)
        .where(ChangeLogEntry.table_name == table,
               ChangeLogEntry.row_id == str(row_id))
        .order_by(ChangeLogEntry.changed_at, ChangeLogEntry.field)
    )).scalars())


async def test_every_field_change_is_recorded_with_what_it_was(hse_manager, db_session):
    """Not the key actions — those were already kept — but each field.

    Recorded at the flush rather than in each write path: thirty-odd endpoints
    write these tables, and a line added to each is a line that will be missing
    from the next one written.
    """
    user, client = hse_manager
    created = await client.post("/api/v1/hazards", json={
        "title": "Pinch point at the case sealer",
        "existing_controls": "Verbal instruction not to reach in.",
    })
    hazard_id = created.json()["id"]

    born = await _trail(db_session, "ehs_hazards", hazard_id)
    assert [e.action for e in born] == ["insert"], (
        "a creation is one row — the record is the account of what it was "
        f"created as: {[(e.action, e.field) for e in born]}")
    assert born[0].row_ref == created.json()["hazard_no"]
    assert born[0].changed_by == user.id

    await client.patch(f"/api/v1/hazards/{hazard_id}", json={
        "existing_controls": "Interlocked guard fitted and keyed.",
        "control_level": "engineering",
    })

    after = await _trail(db_session, "ehs_hazards", hazard_id)
    updates = {e.field: e for e in after if e.action == "update"}
    assert set(updates) == {"existing_controls", "control_level"}, (
        f"only the fields that changed: {sorted(updates)}")
    assert updates["existing_controls"].old_value == "Verbal instruction not to reach in."
    assert updates["existing_controls"].new_value == "Interlocked guard fitted and keyed."
    assert updates["control_level"].old_value is None
    assert updates["control_level"].new_value == "engineering"


async def test_health_information_is_recorded_as_changed_not_as_what_it_became(
        hse_manager, worker, db_session):
    """A general log carries a general permission.

    Copying an allergy history into it would put that history behind a weaker
    gate than the one the medical table exists to provide. The field name and
    the time still answer "was this altered, by whom, when" — which is what an
    audit trail is for.
    """
    from datetime import datetime, timezone

    from app.models.medical import IncidentMedical

    _, client = hse_manager
    injured, _ = worker
    incident_id = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Cut finger",
        "incident_type": "first_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Caught on a burr.",
        "persons": [{"role": "injured", "person_name": injured.full_name or "W",
                     "user_id": str(injured.id)}],
    })).json()["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/submit")

    await client.put(f"/api/v1/incidents/{incident_id}/medical",
                     json={"allergies": "Penicillin"})
    await client.put(f"/api/v1/incidents/{incident_id}/medical",
                     json={"allergies": "Penicillin and sulfa"})

    row = (await db_session.execute(
        select(IncidentMedical).where(
            IncidentMedical.incident_id == uuid.UUID(incident_id)))).scalar_one()
    trail = await _trail(db_session, "ehs_incident_medical", row.id)

    changed = [e for e in trail if e.field == "allergies"]
    assert changed, "the change itself must be recorded"
    for entry in changed:
        assert entry.old_value is None and entry.new_value is None, (
            "health information must not be copied into the general log")

    # The positive control: an ordinary table still records its values, or the
    # assertion above would pass on a log that records nothing at all.
    hazard = (await client.post("/api/v1/hazards", json={"title": "Unguarded nip"})).json()
    await client.patch(f"/api/v1/hazards/{hazard['id']}", json={"title": "Unguarded conveyor nip"})
    ordinary = await _trail(db_session, "ehs_hazards", hazard["id"])
    assert any(e.new_value == "Unguarded conveyor nip" for e in ordinary)


async def test_the_trail_cannot_be_edited(hse_manager, db_session):
    """A trail somebody can quietly rewrite is not evidence.

    Enforced by the database rather than by everybody remembering. The guard is
    a trigger created by migration 0034, and this test database is built by
    create_all — which makes the tables and none of the triggers. Rather than
    assert nothing and pass, the test says so and skips: it protects a database
    that was actually migrated, and reports honestly against one that was not.
    """
    import pytest
    import sqlalchemy

    present = (await db_session.execute(sqlalchemy.text(
        "SELECT 1 FROM pg_trigger WHERE tgname = 'trg_ehs_change_log_append_only'"
    ))).scalar()
    if not present:
        pytest.skip("append-only trigger comes from migration 0034; this "
                    "database was built by create_all")

    _, client = hse_manager
    hazard = (await client.post("/api/v1/hazards", json={"title": "Steam line unlagged"})).json()
    trail = await _trail(db_session, "ehs_hazards", hazard["id"])
    assert trail

    with pytest.raises(sqlalchemy.exc.DBAPIError):
        await db_session.execute(sqlalchemy.text(
            "UPDATE ehs_change_log SET new_value = 'rewritten' WHERE id = :i"),
            {"i": trail[0].id})
        await db_session.flush()
    await db_session.rollback()
