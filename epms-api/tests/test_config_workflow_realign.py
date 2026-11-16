"""Saving Admin → Approval Workflows carries in-flight documents over.

The realignment is approval-api's (its own tests pin the rules). These pin
EPMS's half: the save triggers it, a document it fully approves gets EPMS's
post-approval work (PDF, emails, settlement, schedule), the edit is audited,
and a preview changes nothing.
"""
import uuid

import pytest

CONFIG_URL = "/api/v1/config"
PREVIEW_URL = "/api/v1/config/workflow-defs/preview"

FM = {"id": "fm", "role": "finance_manager", "label": "Finance Manager"}
BP = {"id": "bp", "role": "finance_bp", "label": "Finance BP"}


@pytest.fixture
def engine(monkeypatch):
    """Stand-in for approval-api's realign-workflows; records each call."""
    from app.services import approval_client
    state = {"calls": [], "result": {"changed_workflows": [], "realigned": [], "errors": []},
             "fail": None}

    async def fake(bearer_token, *, new_defs=None):
        state["calls"].append({"token": bearer_token, "new_defs": new_defs})
        if state["fail"]:
            raise RuntimeError(state["fail"])
        return state["result"]

    monkeypatch.setattr(approval_client, "realign_workflows", fake)
    return state


@pytest.fixture(autouse=True)
async def _restore_workflow_defs(admin_client, engine):
    """These tests save workflow_defs wholesale; the config row is shared by the
    whole suite, so put the chains back (with the engine stand-in still on)."""
    original = (await admin_client.get(CONFIG_URL)).json()["workflow_defs"]
    yield
    await admin_client.patch(CONFIG_URL, json={"workflow_defs": original})


@pytest.mark.asyncio
async def test_saving_workflows_realigns_and_audits(admin_client, pg_cur, engine):
    await admin_client.patch(CONFIG_URL, json={"workflow_defs": {"pa_dir": [FM, BP]}})
    engine["calls"].clear()
    engine["result"] = {"changed_workflows": ["pa_dir"], "errors": [], "realigned": [
        {"doc_type": "pa", "doc_id": str(uuid.uuid4()), "number": "PA-1",
         "workflow_key": "pa_dir", "from": "Finance Manager", "to": "Finance BP",
         "completed": False, "actions": []}]}

    r = await admin_client.patch(CONFIG_URL, json={"workflow_defs": {"pa_dir": [BP, FM]}})

    assert r.status_code == 200, r.text
    assert len(engine["calls"]) == 1 and engine["calls"][0]["new_defs"] is None
    out = r.json()["workflow_realign"]
    assert out["summary"].startswith("ok: 1 in-flight document(s) moved")
    assert out["realigned"][0]["to"] == "Finance BP"

    pg_cur.execute("SELECT action, before, after, cascade_summary FROM admin_audit_log "
                   "WHERE entity = 'workflow_defs' ORDER BY created_at DESC LIMIT 1")
    action, before, after, summary = pg_cur.fetchone()
    assert action == "edit_workflow"
    assert before == {"pa_dir": [FM, BP]} and after == {"pa_dir": [BP, FM]}
    assert summary["realigned"][0]["number"] == "PA-1"


@pytest.mark.asyncio
async def test_other_config_saves_do_not_realign(admin_client, engine):
    r = await admin_client.patch(CONFIG_URL, json={"tagline": "No workflow change"})
    assert r.status_code == 200
    assert engine["calls"] == []
    assert r.json()["workflow_realign"] is None


@pytest.mark.asyncio
async def test_engine_outage_keeps_the_save_and_says_so(admin_client, engine):
    engine["fail"] = "approval-api unreachable"
    r = await admin_client.patch(CONFIG_URL, json={"workflow_defs": {"pa_dir": [FM]}})
    assert r.status_code == 200
    assert r.json()["workflow_defs"]["pa_dir"] == [FM]
    assert r.json()["workflow_realign"]["summary"].startswith("failed:")


@pytest.mark.asyncio
async def test_documents_the_realignment_approved_get_epms_post_approval_work(
        admin_client, engine, monkeypatch):
    """A PA whose every step of the new chain was already given is approved by
    the engine during the realignment; EPMS's own approval work (PDF, emails,
    settlement) must still run — through the same function the approve action
    uses. A document that only moved gets none."""
    import app.api.v1.pa as pa_api
    from app.crud import pa as pa_crud
    finished = []

    async def fake_on_pa_approved(db, pa, actor_id, token):
        finished.append(pa)

    async def fake_get_by_id(db, pa_id):
        return f"pa:{pa_id}"

    monkeypatch.setattr(pa_api, "on_pa_approved", fake_on_pa_approved)
    monkeypatch.setattr(pa_crud, "get_by_id", fake_get_by_id)
    done_id, moved_id = uuid.uuid4(), uuid.uuid4()
    engine["result"] = {"changed_workflows": ["pa_dir"], "errors": [], "realigned": [
        {"doc_type": "pa", "doc_id": str(done_id), "number": "PA-DONE", "workflow_key": "pa_dir",
         "from": "Finance BP", "to": "Approved", "completed": True, "actions": []},
        {"doc_type": "pa", "doc_id": str(moved_id), "number": "PA-MOVED", "workflow_key": "pa_dir",
         "from": "Finance Manager", "to": "Finance BP", "completed": False, "actions": []}]}

    r = await admin_client.patch(CONFIG_URL, json={"workflow_defs": {"pa_dir": [FM]}})

    assert r.status_code == 200
    assert finished == [f"pa:{done_id}"]
    assert "1 of them fully approved" in r.json()["workflow_realign"]["summary"]


@pytest.mark.asyncio
async def test_preview_sends_the_proposed_chains_and_saves_nothing(admin_client, engine):
    await admin_client.patch(CONFIG_URL, json={"workflow_defs": {"pa_dir": [FM, BP]}})
    engine["calls"].clear()
    engine["result"] = {"changed_workflows": ["pa_dir"], "realigned": [], "errors": [
        {"doc_type": "pa", "doc_id": str(uuid.uuid4()), "error": "ValueError: no manager"}]}

    r = await admin_client.post(PREVIEW_URL, json={"workflow_defs": {"pa_dir": [BP, FM]}})

    assert r.status_code == 200, r.text
    assert engine["calls"] == [{"token": engine["calls"][0]["token"],
                                "new_defs": {"pa_dir": [BP, FM]}}]
    assert r.json()["errors"][0]["error"] == "ValueError: no manager"
    cfg = (await admin_client.get(CONFIG_URL)).json()
    assert cfg["workflow_defs"]["pa_dir"] == [FM, BP]


@pytest.mark.asyncio
async def test_preview_needs_admin_panel(requester_client, engine):
    r = await requester_client.post(PREVIEW_URL, json={"workflow_defs": {"pa_dir": [FM]}})
    assert r.status_code == 403
    assert engine["calls"] == []
