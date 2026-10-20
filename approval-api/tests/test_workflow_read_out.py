"""GET /workflows/{doc_type} — the read-out the Assistant explains processes from.

It used crud.workflow.get_workflow, whose fallbacks had drifted from what the
engine executes (PORTAL.md §14 PO-37), and served five document types, so the
Assistant could not explain an agreement or an OA claim at all (ISS-0001). It
also said nothing about over-budget PRs, so "现在超预算的审批模式是什么？" had
nothing to be answered from (ISS-0005).
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.core.config import settings
from app.crud.engine import _WORKFLOW_DEFAULTS
from app.db.base import get_db
from app.main import app
from app.models.config import CompanyConfig

pytestmark = pytest.mark.asyncio
BASE = "/approval/v1"


@pytest.fixture(autouse=True)
def _override_get_db(engine_db_session):
    async def _get_db_override():
        yield engine_db_session
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


def _client():
    token = jose_jwt.encode({"sub": str(uuid.uuid4()), "role": "requester"},
                            settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _config(db, *, mode="fm_gm_opm", workflow_defs=None):
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs=workflow_defs or {},
                         budget_admin_config={"over_budget_mode": mode}))
    await db.flush()


@pytest.mark.parametrize("doc_type", ["agr", "exp", "mil", "trv", "tra", "budget_plan",
                                      "posign", "pa_dir"])
async def test_chains_the_assistant_could_not_explain_are_now_served(engine_db_session, doc_type):
    await _config(engine_db_session)
    async with _client() as c:
        r = await c.get(f"{BASE}/workflows/{doc_type}")
    assert r.status_code == 200, r.text
    # With no configured definition the read-out is the engine's own default —
    # the chain that actually runs.
    assert [s["id"] for s in r.json()["steps"]] == [s["id"] for s in _WORKFLOW_DEFAULTS[doc_type]]


async def test_configured_chain_wins_over_the_default(engine_db_session):
    agr = [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
           {"id": "fm", "role": "finance_manager", "label": "Finance Manager"}]
    await _config(engine_db_session, workflow_defs={"agr": agr})
    async with _client() as c:
        r = await c.get(f"{BASE}/workflows/agr")
    assert [s["label"] for s in r.json()["steps"]] == ["Department Manager", "Finance Manager"]


async def test_pr_read_out_uses_the_engine_default_not_the_stale_one(engine_db_session):
    """The old fallback said a PR is approved by the Department Manager alone."""
    await _config(engine_db_session)
    async with _client() as c:
        steps = (await c.get(f"{BASE}/workflows/pr")).json()["steps"]
    assert [s["id"] for s in steps] == [s["id"] for s in _WORKFLOW_DEFAULTS["pr"]]
    assert len(steps) > 1


@pytest.mark.parametrize("mode,added,blocks", [
    ("fm_gm_opm", ["Over-Budget — Finance Manager", "Over-Budget — GM / OPM"], False),
    ("fm_only", ["Over-Budget — Finance Manager"], False),
    ("allow", [], False),
    ("hard_block", [], True),
])
async def test_pr_read_out_states_the_over_budget_rule(engine_db_session, mode, added, blocks):
    await _config(engine_db_session, mode=mode)
    async with _client() as c:
        body = (await c.get(f"{BASE}/workflows/pr")).json()
    ob = body["over_budget"]
    assert ob["mode"] == mode and ob["blocks_submission"] is blocks
    assert [s["label"] for s in ob["added_steps"]] == added
    assert ob["mode_label"] and "submitted" in ob["note"]
    # ISS-0025: the PR form demands a justification in every mode that lets an
    # over-budget PR submit, including allow (no extra steps).
    assert ob["justification_required"] is (mode != "hard_block")
    # The rule is reported beside the chain, never folded into it: it applies
    # only to over-budget PRs.
    assert not any(s["id"].startswith("ob_") for s in body["steps"])


async def test_other_documents_carry_no_over_budget_rule(engine_db_session):
    await _config(engine_db_session)
    async with _client() as c:
        assert (await c.get(f"{BASE}/workflows/po")).json()["over_budget"] is None


async def test_unknown_type_is_still_refused(engine_db_session):
    await _config(engine_db_session)
    async with _client() as c:
        assert (await c.get(f"{BASE}/workflows/nonsense")).status_code == 400
