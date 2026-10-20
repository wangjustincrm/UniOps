"""The risk assessment HSE described in round 8.

"Existing Hazard" from the hazard list; "Judged on" a list HSE keeps, with
Equipment; the hierarchy of controls and the hazard controls, any number of
each; how likely and how bad before and after — the final assessment.
"""
import uuid

from app.models.incident import Incident
from app.models.vocabulary import Vocabulary, VocabularyItem
from tests.test_worker_reach import risk_matrix, _an_incident_by_somebody_else  # noqa: F401


async def _item(db, vocabulary, label, *, code=None, attrs=None):
    if await db.get(Vocabulary, vocabulary) is None:
        db.add(Vocabulary(code=vocabulary, name=vocabulary, is_hierarchical=False,
                          is_system_locked=False, attr_schema={}))
        await db.flush()
    row = VocabularyItem(id=uuid.uuid4(), vocabulary_code=vocabulary,
                         code=code or f"c{uuid.uuid4().hex[:8]}", label=label,
                         sort_order=1, is_active=True, attrs=attrs or {}, path=label)
    db.add(row)
    await db.commit()
    return row


async def test_the_assessment_records_what_was_picked_as_it_was_called(
        hse_manager, db_session, risk_matrix):
    _, hse = hse_manager
    incident = await _an_incident_by_somebody_else(hse, db_session)
    hazard = await _item(db_session, "hazard", "Slips, trips and falls")
    eng = await _item(db_session, "hierarchy_of_control", "Engineering Control")
    ctl = await _item(db_session, "hazard_control", "housekeeping")
    await _item(db_session, "consequence_type", "Equipment", code="equipment",
                attrs={"severity": {"2": "Repair under a day", "3": "Repair over a day"}})

    matrix = (await hse.get("/api/v1/risk/matrix")).json()
    equipment = next(c for c in matrix["consequences"] if c["code"] == "equipment")
    assert equipment["severity"]["3"] == "Repair over a day"

    made = await hse.post("/api/v1/risk/assessments", json={
        "source_type": "incident", "source_id": str(incident.id),
        "hazard_item_id": str(hazard.id), "consequence_type": "equipment",
        "likelihood_code": "C", "severity_level": 3,
        "hierarchy_control_ids": [str(eng.id)], "hazard_control_ids": [str(ctl.id)],
        "controls": "A drain is fitted; the floor is still wet at changeover.",
        "residual_likelihood_code": "B", "residual_severity_level": 2})
    assert made.status_code == 201, made.text
    a = made.json()
    assert a["hazard_label"] == "Slips, trips and falls"
    assert a["severity_label"] == "Repair over a day"
    assert [c["label"] for c in a["hierarchy_controls"]] == ["Engineering Control"]
    assert [c["label"] for c in a["hazard_controls"]] == ["housekeeping"]
    assert a["rating"] == "moderate" and a["residual_rating"] == "acceptable"

    # Renaming the list entry later does not rewrite the assessment.
    ctl.label = "Housekeeping (daily)"
    await db_session.commit()
    listed = (await hse.get(f"/api/v1/risk/assessments?source_type=incident&source_id={incident.id}")).json()
    assert [c["label"] for c in listed[0]["hazard_controls"]] == ["housekeeping"]


async def test_something_not_on_the_list_is_refused(hse_manager, db_session, risk_matrix):
    _, hse = hse_manager
    incident = await _an_incident_by_somebody_else(hse, db_session)
    got = await hse.post("/api/v1/risk/assessments", json={
        "source_type": "incident", "source_id": str(incident.id),
        "consequence_type": "people", "likelihood_code": "C", "severity_level": 3,
        "hazard_control_ids": [str(uuid.uuid4())]})
    assert got.status_code == 422 and "hazard control" in got.text
