"""★ Round 12, PMT-01: a permit's hazards and controls picked from HSE's lists,
saved on create as well as on edit (findings was once dropped on create)."""
import uuid

import pytest

from app.models.permit import PermitType
from app.models.vocabulary import Vocabulary, VocabularyItem


@pytest.fixture
async def setup(db_session):
    for code in ("hazard", "hazard_control"):
        if await db_session.get(Vocabulary, code) is None:
            db_session.add(Vocabulary(code=code, name=code))
    await db_session.flush()
    tag = uuid.uuid4().hex[:6]
    hot = VocabularyItem(id=uuid.uuid4(), vocabulary_code="hazard", code=f"hot_{tag}",
                         label=f"Hot surface {tag}", path=f"Hot surface {tag}")
    watch = VocabularyItem(id=uuid.uuid4(), vocabulary_code="hazard_control", code=f"fw_{tag}",
                           label=f"Fire watch {tag}", path=f"Fire watch {tag}")
    ptype = PermitType(id=uuid.uuid4(), code=f"hw-{tag}", name="Hot work", precautions=[],
                       requires_gas_test=False, required_signatures=["issuer"],
                       max_duration_hours=8)
    db_session.add_all([hot, watch, ptype])
    await db_session.commit()
    return hot, watch, ptype


async def test_hazards_and_controls_on_a_permit(hse_manager, setup):
    _, hse = hse_manager
    hot, watch, ptype = setup
    made = await hse.post("/api/v1/permits", json={
        "type_id": str(ptype.id), "work_description": "Weld the rail",
        "hazard_ids": [str(hot.id)], "control_ids": [str(watch.id)]})
    assert made.status_code == 201, made.text
    p = made.json()
    assert [h["label"] for h in p["hazard_items"]] == [hot.label], "kept on create"
    assert [c["label"] for c in p["control_items"]] == [watch.label]

    got = (await hse.get(f"/api/v1/permits/{p['id']}")).json()
    assert got["hazard_items"] == p["hazard_items"]
    cleared = await hse.patch(f"/api/v1/permits/{p['id']}", json={"control_ids": []})
    assert cleared.status_code == 200 and cleared.json()["control_items"] == []
    assert cleared.json()["hazard_items"] == p["hazard_items"], "what was not sent is kept"
    wrong = await hse.patch(f"/api/v1/permits/{p['id']}", json={"hazard_ids": [str(watch.id)]})
    assert wrong.status_code == 422, "a control is not a hazard"
