"""UAT 17 (closed 26 Nov 2026): the equipment form records no injury, and the
first aider is sent on its own."""
import uuid
from datetime import datetime, timezone

from app.models.incident import Incident


def _report(form_kind, incident_type, **extra):
    return {"form_kind": form_kind, "title": "Guard came loose on the filler",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident_type": incident_type, **extra}


async def test_the_equipment_form_records_nobody_as_hurt(hse_manager, db_session):
    """★ "If someone was hurt it would be recorded under the 'someone was hurt'
    incident form." Whatever is sent, as on a near miss."""
    _, hse = hse_manager
    offered = {t["code"]: t for t in (await hse.get(
        "/api/v1/incidents/types", params={"form_kind": "equipment"})).json()}
    assert offered and all(not t["asks_injury_class"] for t in offered.values()), offered

    made = await hse.post("/api/v1/incidents", json=_report(
        "equipment", "property_damage", injury_class="lost_time"))
    assert made.status_code == 201, made.text
    row = await db_session.get(Incident, uuid.UUID(made.json()["id"]))
    assert row.injury_class is None

    # The medical form still asks, and still keeps the answer.
    medical = await hse.post("/api/v1/incidents", json=_report(
        "medical", "first_aid", injury_class="medical_aid"))
    assert medical.status_code == 201, medical.text
    row = await db_session.get(Incident, uuid.UUID(medical.json()["id"]))
    assert row.injury_class == "medical_aid"


async def test_a_custom_type_on_the_equipment_form_does_not_ask(hse_manager):
    _, hse = hse_manager
    made = await hse.post("/api/v1/settings/incident-types", json={
        "label": f"Conveyor jam {uuid.uuid4().hex[:6]}", "form_kind": "equipment",
        "restricted": False})
    assert made.status_code == 201, made.text
    offered = {t["code"]: t for t in (await hse.get(
        "/api/v1/incidents/types", params={"form_kind": "equipment"})).json()}
    assert offered[made.json()["code"]]["asks_injury_class"] is False
    medical = await hse.post("/api/v1/settings/incident-types", json={
        "label": f"Needle stick {uuid.uuid4().hex[:6]}", "form_kind": "medical",
        "restricted": False})
    offered = {t["code"]: t for t in (await hse.get(
        "/api/v1/incidents/types", params={"form_kind": "medical"})).json()}
    assert offered[medical.json()["code"]]["asks_injury_class"] is True


async def test_who_gave_the_treatment_is_kept_without_a_name_for_who_was_hurt(hse_manager):
    """★ Asked above "Who was hurt" now, so it can be answered first."""
    _, hse = hse_manager
    made = await hse.post("/api/v1/incidents", json=_report("medical", "first_aid", persons=[{
        "role": "first_aider", "person_name": "Dana Ruiz"}]))
    assert made.status_code == 201, made.text
    got = (await hse.get(f"/api/v1/incidents/{made.json()['id']}")).json()
    assert [(p["role"], p["person_name"]) for p in got["persons"]] == [("first_aider", "Dana Ruiz")]
