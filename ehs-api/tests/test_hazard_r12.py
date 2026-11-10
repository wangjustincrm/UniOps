"""★ Round 12, batch 4: the hazard register.

HAZ-11 several kinds and controls; HAZ-07 newest first, ranked on request,
ordered before the limit; HAZ-02 scored on the incident's form; HAZ-12
eliminated at assessment; Q11 workers see a hazard once it is assessed; Kirk's
"last revised on"; HAZ-03 photographs.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.v1 import record_attachments
from app.models.hazard import Hazard
from app.models.risk import RiskLikelihood, RiskSeverity
from app.models.vocabulary import Vocabulary, VocabularyItem


@pytest.fixture
async def lists(db_session):
    """Two kinds and a control on HSE's lists, and enough of the matrix."""
    for code in ("hazard", "hazard_control"):
        if await db_session.get(Vocabulary, code) is None:
            db_session.add(Vocabulary(code=code, name=code))
    await db_session.flush()
    tag = uuid.uuid4().hex[:6]
    items = {}
    for key, vocab, label in (("noise", "hazard", f"Noise {tag}"),
                              ("pinch", "hazard", f"Pinch point {tag}"),
                              ("guard", "hazard_control", f"Fixed guard {tag}")):
        items[key] = VocabularyItem(id=uuid.uuid4(), vocabulary_code=vocab,
                                    code=f"{key}_{tag}", label=label, path=label)
        db_session.add(items[key])
    for row in (RiskLikelihood(code="C", label="Possible", sort_order=3,
                               ratings={"2": "acceptable", "3": "moderate"}),
                RiskSeverity(level=2, people="Medical aid"),
                RiskSeverity(level=3, people="Lost time")):
        await db_session.merge(row)
    await db_session.commit()
    return items


async def _score(hse, hid, **extra):
    got = await hse.post(f"/api/v1/hazards/{hid}/assess", json={
        "consequence_type": "people", "likelihood_code": "C", "severity_level": 3,
        "residual_likelihood_code": "C", "residual_severity_level": 2, **extra})
    assert got.status_code == 200, got.text
    return got.json()


async def test_several_kinds_and_controls_and_last_revised(hse_manager, lists):
    _, hse = hse_manager
    made = await hse.post("/api/v1/hazards", json={
        "title": "Case sealer infeed",
        "hazard_type_ids": [str(lists["pinch"].id), str(lists["noise"].id)],
        "hazard_control_ids": [str(lists["guard"].id)]})
    assert made.status_code == 201, made.text
    h = made.json()
    assert [k["label"] for k in h["hazard_types"]] == [lists["pinch"].label, lists["noise"].label]
    assert h["hazard_type_id"] == str(lists["pinch"].id), "the first kind is the hazard's kind"
    assert [c["label"] for c in h["hazard_controls"]] == [lists["guard"].label]
    assert h["revised_at"] is None, "recording it is not revising it"
    assert h["created_by_name"]

    await _score(hse, h["id"])
    assert (await hse.get(f"/api/v1/hazards/{h['id']}")).json()["revised_at"] is None, \
        "an assessment is not a revision"

    same = await hse.patch(f"/api/v1/hazards/{h['id']}", json={"title": "Case sealer infeed"})
    assert same.json()["revised_at"] is None, "saving it unchanged is not a revision"
    edited = (await hse.patch(f"/api/v1/hazards/{h['id']}", json={
        "hazard_type_ids": [str(lists["noise"].id)]})).json()
    assert edited["revised_at"] and edited["revised_by_name"]
    assert [k["label"] for k in edited["hazard_types"]] == [lists["noise"].label]
    assert edited["hazard_type_id"] == str(lists["noise"].id)

    wrong = await hse.post("/api/v1/hazards", json={
        "title": "x", "hazard_type_ids": [str(lists["guard"].id)]})
    assert wrong.status_code == 422, "a control is not a kind of hazard"


async def test_assessed_on_the_incident_form_and_eliminated_there(hse_manager, lists):
    _, hse = hse_manager
    h = (await hse.post("/api/v1/hazards", json={
        "title": "Open pit", "existing_controls": "Barrier tape"})).json()
    scored = await _score(hse, h["id"], controls="Edge still reachable from the stairs",
                          hazard_control_ids=[str(lists["guard"].id)], eliminated=True)
    assert scored["status"] == "eliminated", "★ HAZ-12: the dashboard's Eliminated was always 0"
    assert scored["existing_controls"] == "Barrier tape", \
        "what remains after controls is not copied in as the controls in place"
    assert [c["label"] for c in scored["hazard_controls"]] == [lists["guard"].label]
    rows = (await hse.get("/api/v1/risk/assessments",
                          params={"source_type": "hazard", "source_id": h["id"]})).json()
    assert rows[0]["controls"] == "Edge still reachable from the stairs"
    assert rows[0]["hazard_controls"][0]["label"] == lists["guard"].label


async def test_a_worker_sees_a_hazard_once_it_is_assessed(hse_manager, worker, lists):
    """Q11: "it would be complete once it has at least one assessment"."""
    _, hse = hse_manager
    _, w = worker
    h = (await hse.post("/api/v1/hazards", json={"title": f"Wet floor {uuid.uuid4().hex[:5]}"})).json()
    assert (await w.get(f"/api/v1/hazards/{h['id']}")).status_code == 404
    assert (await w.get(f"/api/v1/hazards/{h['id']}/causes")).status_code == 404
    assert h["id"] not in [x["id"] for x in (await w.get("/api/v1/hazards")).json()]
    assert h["id"] in [x["id"] for x in (await hse.get("/api/v1/hazards")).json()], \
        "whoever writes hazards still sees it"

    await _score(hse, h["id"])
    assert (await w.get(f"/api/v1/hazards/{h['id']}")).status_code == 200
    assert h["id"] in [x["id"] for x in (await w.get("/api/v1/hazards")).json()]


async def test_newest_first_or_worst_first_ordered_before_the_limit(hse_manager, db_session):
    _, hse = hse_manager
    tag = uuid.uuid4().hex[:6]
    old_critical = Hazard(id=uuid.uuid4(), hazard_no=f"HZ-{tag}-1", title=f"Old {tag}",
                          current_rating="critical",
                          created_at=datetime.now(timezone.utc) - timedelta(days=30))
    db_session.add(old_critical)
    await db_session.commit()
    newer = (await hse.post("/api/v1/hazards", json={"title": f"New {tag}"})).json()

    newest = (await hse.get("/api/v1/hazards")).json()
    assert newest[0]["id"] == newer["id"], "★ HAZ-07: newest first by default"
    worst = (await hse.get("/api/v1/hazards", params={"order": "risk"})).json()
    assert worst[0]["current_rating"] == "critical"
    assert str(old_critical.id) in [x["id"] for x in worst if x["current_rating"] == "critical"]


class FakeFileServer:
    def __init__(self):
        self.deleted = []

    async def upload(self, data, name, ctype, doc_type, doc_id, token):
        return uuid.uuid4()

    async def delete(self, key, token):
        self.deleted.append(key)


@pytest.fixture
def files(monkeypatch):
    fake = FakeFileServer()
    monkeypatch.setattr(record_attachments, "upload_to_file_server", fake.upload)
    monkeypatch.setattr(record_attachments, "delete_from_file_server", fake.delete)
    return fake


async def test_photographs_on_a_hazard_follow_the_hazard(hse_manager, worker, lists, files):
    _, hse = hse_manager
    _, w = worker
    h = (await hse.post("/api/v1/hazards", json={"title": "Frayed sling"})).json()
    url = f"/api/v1/records/hazard/{h['id']}/attachments"
    photo = {"file": ("sling.jpg", b"\xff\xd8jpeg", "image/jpeg")}

    assert (await w.post(url, files=photo)).status_code == 403, "a worker does not write hazards"
    added = await hse.post(url, files=photo)
    assert added.status_code == 201, added.text
    assert (await w.get(url)).status_code == 404, "not before it is assessed"
    await _score(hse, h["id"])
    seen = (await w.get(url)).json()
    assert [a["filename"] for a in seen] == ["sling.jpg"]
    assert (await w.delete(f"{url}/{seen[0]['id']}")).status_code == 403, \
        "someone else's photograph is not theirs to remove"
    assert (await hse.delete(f"{url}/{seen[0]['id']}")).status_code == 204
    assert files.deleted

    assert (await hse.post(url, files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
            ).status_code == 415
    assert (await hse.get(f"/api/v1/records/memo/{h['id']}/attachments")).status_code == 404
    assert (await hse.get(f"/api/v1/records/jsa/{h['id']}/attachments")).status_code == 404, \
        "the id has to be a JSA's"
