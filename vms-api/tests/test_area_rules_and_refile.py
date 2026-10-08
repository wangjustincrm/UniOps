"""Entire Plant is GMP-grade (D-08) and re-filing keeps history (D-04)."""
import uuid

from sqlalchemy import select

import app.db.session as session_module
from app.models.audit_log import AuditLog
from app.services import area_rules
from app.models.visit import AccessArea
from tests.test_health_decl import (
    _FAIL_ANSWERS, _PASS_ANSWERS, _force_approve_visit, _make_visitor, _mark_compliance_fresh, _payload,
)


async def test_area_rules_endpoint_matches_server_rules(requester):
    _u, client = requester
    rules = {r["area"]: r for r in (await client.get("/api/v1/area-rules")).json()}
    assert set(rules) == {a.value for a in AccessArea}
    for a in AccessArea:
        assert rules[a.value]["requires_health_declaration"] == (a in area_rules.HEALTH_DECLARATION_AREAS)
        assert rules[a.value]["requires_quality_manager"] == (a in area_rules.QUALITY_MANAGER_AREAS)
    assert rules["all"]["requires_health_declaration"] is True
    assert rules["office"]["requires_approval"] is False


async def test_entire_plant_badge_needs_a_passing_declaration(requester):
    user, client = requester
    vid = await _make_visitor(client)
    await _mark_compliance_fresh(vid)
    visit = (await client.post("/api/v1/visits", json=_payload(vid, str(user.id), area="all"))).json()
    await _force_approve_visit(visit["id"])

    blocked = await client.post(f"/api/v1/visits/{visit['id']}/print-badge", json={})
    assert blocked.status_code == 422, blocked.text

    ok = await client.post(f"/api/v1/visits/{visit['id']}/health-declaration",
                           json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True})
    assert ok.status_code == 201
    printed = await client.post(f"/api/v1/visits/{visit['id']}/print-badge", json={})
    assert printed.status_code in (200, 201), printed.text


async def test_refile_keeps_the_replaced_declaration_in_the_audit_log(requester):
    user, client = requester
    vid = await _make_visitor(client)
    visit = (await client.post("/api/v1/visits", json=_payload(vid, str(user.id), area="production_gmp"))).json()
    await _force_approve_visit(visit["id"])

    first = await client.post(f"/api/v1/visits/{visit['id']}/health-declaration",
                              json={"answers": _FAIL_ANSWERS, "signature": "data:image/png;base64,FIRST"})
    assert first.json()["result"] == "failed"
    second = await client.post(f"/api/v1/visits/{visit['id']}/health-declaration",
                               json={"answers": _PASS_ANSWERS, "signature": "data:image/png;base64,SECOND"})
    assert second.json()["result"] == "passed"

    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.entity_id == uuid.UUID(visit["id"]),
                                   AuditLog.action_type == "health_decl.submit")
            .order_by(AuditLog.id)
        )).scalars().all()
    refile = rows[-1]
    old = refile.old_value["declaration"]
    assert old["result"] == "failed"
    assert old["signature"] == "data:image/png;base64,FIRST"
    assert any(a["id"] == "fever_cough" and a["answer"] == "yes" for a in old["questionnaire"]["answers"])
    assert refile.new_value["declaration"]["result"] == "passed"
    assert "re-filed over previous result=failed" in refile.notes
    assert "declaration" not in rows[0].old_value  # first filing had nothing to replace
