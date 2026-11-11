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


async def test_photographs_on_a_permit_follow_the_permit(
        worker, hse_manager, setup, test_engine, db_session, monkeypatch):
    """★ B3/B4 review H1: the photographs asked only for ehs.permit.request,
    which every employee holds, so anyone with the id could list, download,
    add and remove them while the permit itself answered 403. And once issued
    or closed they could still be removed."""
    from app.api.v1 import record_attachments
    from tests.conftest import authed_client, make_token, make_user

    async def up(*a):
        return uuid.uuid4()

    async def down(*a):
        return None

    monkeypatch.setattr(record_attachments, "upload_to_file_server", up)
    monkeypatch.setattr(record_attachments, "delete_from_file_server", down)
    monkeypatch.setattr(record_attachments, "proxy_download", lambda *a: _ok())
    _, mine = worker
    _, hse = hse_manager
    _, _, ptype = setup
    p = (await mine.post("/api/v1/permits", json={
        "type_id": str(ptype.id), "work_description": "Grind the weld"})).json()
    url = f"/api/v1/records/permit/{p['id']}/attachments"
    photo = {"file": ("weld.jpg", b"\xff\xd8jpeg", "image/jpeg")}

    added = await mine.post(url, files=photo)
    assert added.status_code == 201, "★ whoever raised it adds photographs"
    att = added.json()["id"]
    assert [a["id"] for a in (await mine.get(url)).json()] == [att]
    assert [a["id"] for a in (await hse.get(url)).json()] == [att], "the register sees them"

    stranger = await make_user(test_engine, role="requester")
    async with authed_client(make_token(stranger.id, stranger.role), db_session) as theirs:
        assert (await theirs.get(f"/api/v1/permits/{p['id']}")).status_code == 403
        assert (await theirs.get(url)).status_code == 403, "★ not someone else's permit"
        assert (await theirs.get(f"{url}/{att}/download")).status_code == 403
        assert (await theirs.post(url, files=photo)).status_code == 403
        assert (await theirs.delete(f"{url}/{att}")).status_code == 403

    from app.models.permit import Permit
    row = await db_session.get(Permit, uuid.UUID(p["id"]))
    row.status = "closed"
    await db_session.commit()
    assert (await mine.delete(f"{url}/{att}")).status_code == 409, \
        "★ a closed permit's photographs are evidence"
    assert (await hse.delete(f"{url}/{att}")).status_code == 409
    assert (await mine.post(url, files=photo)).status_code == 409
    assert (await mine.get(url)).status_code == 200, "and still seen"

    row.status = "draft"
    await db_session.commit()
    assert (await mine.delete(f"{url}/{att}")).status_code == 204, "while a draft, removable"


def _ok():
    async def _r():
        from fastapi.responses import Response
        return Response(b"x")
    return _r()
