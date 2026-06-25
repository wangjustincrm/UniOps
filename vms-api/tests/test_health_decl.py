"""Health declaration + GMP-gate (PRD §2.2.2 / §6.5.4)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.models.audit_log import AuditLog
from app.models.health_declaration import HealthDeclaration
from app.models.visit import Visit, VisitStatus
from tests.conftest import authed_client, make_token, make_user


async def _force_approve_visit(visit_id: str) -> None:
    """Simulate that approval-api ran to completion: flip approval_status →
    approved AND user-facing status → confirmed (the engine's
    `_post_approve_vms_visit` callback would do this in prod)."""
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE vms_visits SET status='confirmed', approval_status='approved' WHERE id = :id"),
            {"id": uuid.UUID(visit_id)},
        )
        await db.commit()


async def _mark_compliance_fresh(visitor_id: str) -> None:
    """Stamp the visitor's training + PPE records as freshly confirmed so the
    badge-print compliance gate passes. These health-decl tests are about the
    *health declaration* gate — the training/PPE gate is exercised separately
    in test_compliance.py, so we satisfy it here to isolate the subject."""
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE vms_visitors SET safety_training_confirmed_at = NOW(), "
                "ppe_issued_at = NOW() WHERE id = :id"
            ),
            {"id": uuid.UUID(visitor_id)},
        )
        await db.commit()


# ── Helpers ─────────────────────────────────────────────────────────────────-

async def _make_visitor(client) -> str:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Health",
        "last_name":  f"Test{uuid.uuid4().hex[:4]}",
        "company_name": "HealthCo",
        "phone": "+1-555-0200",
        "visitor_type": "supplier",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _payload(visitor_id: str, host_id: str, *, area: str) -> dict:
    arrival = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "planned_departure": (arrival + timedelta(hours=2)).isoformat(),
        "visit_purpose": "audit",
        "access_area": area,
    }


_PASS_ANSWERS = [
    {"id": "fever_cough",        "answer": "no"},
    {"id": "open_wounds",        "answer": "no"},
    {"id": "contact_infectious", "answer": "no"},
    {"id": "food_allergens",     "answer": "no"},
]

_FAIL_ANSWERS = [
    {"id": "fever_cough",        "answer": "yes"},   # this one fails
    {"id": "open_wounds",        "answer": "no"},
    {"id": "contact_infectious", "answer": "no"},
    {"id": "food_allergens",     "answer": "no"},
]


async def _create_gmp_visit(client, host_id: str, *, auto_approve: bool = True) -> dict:
    """Create a GMP visit. With auto_approve=True (the default), we simulate
    that the approval workflow has cleared so health-decl tests can exercise
    the badge-print path without going through the engine.
    """
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, host_id, area="production_gmp"))
    ).json()
    # Satisfy the badge-print training/PPE gate so these tests isolate the
    # health-declaration gate (their actual subject).
    await _mark_compliance_fresh(created["visitor_id"])
    if auto_approve:
        await _force_approve_visit(created["id"])
        # Re-fetch so callers see status=confirmed.
        created = (await client.get(f"/api/v1/visits/{created['id']}")).json()
    return created


# ── Template endpoint ──────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_health_questions_template_open_to_any_user(requester):
    _, client = requester
    resp = await client.get("/api/v1/health-questions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert len(body["questions"]) >= 4
    assert all("id" in q and "text" in q and "fail_on" in q for q in body["questions"])


@pytest.mark.asyncio
async def test_health_questions_rejects_anonymous(client):
    resp = await client.get("/api/v1/health-questions")
    assert resp.status_code in (401, 403)


# ── Submit happy path ──────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_submit_with_all_no_passes(requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))

    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={
            "answers": _PASS_ANSWERS,
            "safety_training_confirmed": True,
            "signature": "data:image/png;base64,iVBORw0KGgoAAAANS…",  # truncated for test
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["result"] == "passed"
    assert body["visit_id"] == visit["id"]

    # Visit row now carries the result.
    got = (await client.get(f"/api/v1/visits/{visit['id']}")).json()
    assert got["health_decl_status"] == "passed"
    assert got["safety_training_confirmed"] is True


@pytest.mark.asyncio
async def test_submit_with_a_yes_fails(requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))

    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={
            "answers": _FAIL_ANSWERS,
            "safety_training_confirmed": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["result"] == "failed"


@pytest.mark.asyncio
async def test_resubmit_overwrites_prior_declaration(requester):
    """Visitor's condition changed — they had a cough at booking but it's
    cleared by arrival. Latest declaration wins."""
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))

    # First submission fails.
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _FAIL_ANSWERS, "safety_training_confirmed": True},
    )
    # Second submission passes.
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
    )
    assert resp.status_code == 201
    assert resp.json()["result"] == "passed"

    # And only ONE row exists for the visit (no duplicates).
    history = await client.get(f"/api/v1/visits/{visit['id']}/health-declaration")
    assert history.status_code == 200
    body = history.json()
    assert len(body) == 1
    assert body[0]["result"] == "passed"


# ── GMP / Lab badge gate ───────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_gmp_badge_blocked_without_health_declaration(requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 422
    assert "health declaration" in resp.text.lower()


@pytest.mark.asyncio
async def test_gmp_badge_blocked_when_declaration_failed(requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _FAIL_ANSWERS, "safety_training_confirmed": True},
    )
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_gmp_badge_allowed_after_passing_declaration(requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
    )
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "checked_in"


@pytest.mark.asyncio
async def test_office_badge_does_not_require_health_declaration(requester):
    """The gate is GMP/Lab only — office visits skip it."""
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, str(user.id), area="office"))
    ).json()
    resp = await client.post(
        f"/api/v1/visits/{created['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 201


# ── Multi-visitor: each person signs their own declaration ─────────────────-

async def _create_two_visitor_gmp_visit(client, host_id: str):
    """GMP visit with a primary + one companion, approved, both compliance-fresh."""
    primary = await _make_visitor(client)
    companion = await _make_visitor(client)
    payload = _payload(primary, host_id, area="production_gmp")
    payload["additional_visitor_ids"] = [companion]
    created = (await client.post("/api/v1/visits", json=payload)).json()
    await _mark_compliance_fresh(primary)
    await _mark_compliance_fresh(companion)
    await _force_approve_visit(created["id"])
    return created, primary, companion


@pytest.mark.asyncio
async def test_multi_visitor_gate_requires_every_declaration(requester):
    user, client = requester
    visit, primary, companion = await _create_two_visitor_gmp_visit(client, str(user.id))

    # Only the primary declares (passes).
    r = await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True, "visitor_id": primary},
    )
    assert r.status_code == 201, r.text
    assert r.json()["visitor_id"] == primary

    # NOT printable yet — the companion hasn't declared.
    got = (await client.get(f"/api/v1/visits/{visit['id']}")).json()
    assert got["health_decl_status"] != "passed"
    blocked = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge", json={"template_used": "standard"},
    )
    assert blocked.status_code == 422

    # Companion declares (passes) → whole visit cleared and prints.
    r2 = await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True, "visitor_id": companion},
    )
    assert r2.status_code == 201
    got2 = (await client.get(f"/api/v1/visits/{visit['id']}")).json()
    assert got2["health_decl_status"] == "passed"
    printed = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge", json={"template_used": "standard"},
    )
    assert printed.status_code == 201, printed.text

    # Two declarations on file — one per visitor.
    listed = (await client.get(f"/api/v1/visits/{visit['id']}/health-declaration")).json()
    assert {d["visitor_id"] for d in listed} == {primary, companion}


@pytest.mark.asyncio
async def test_multi_visitor_one_failure_blocks_whole_visit(requester):
    user, client = requester
    visit, primary, companion = await _create_two_visitor_gmp_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True, "visitor_id": primary},
    )
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _FAIL_ANSWERS, "safety_training_confirmed": True, "visitor_id": companion},
    )
    got = (await client.get(f"/api/v1/visits/{visit['id']}")).json()
    assert got["health_decl_status"] == "failed"
    blocked = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge", json={"template_used": "standard"},
    )
    assert blocked.status_code == 422


@pytest.mark.asyncio
async def test_declaration_rejects_visitor_not_on_visit(requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    stranger = await _make_visitor(client)
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True, "visitor_id": stranger},
    )
    assert resp.status_code == 400


# ── Standalone declarations browser ─────────────────────────────────────────-

@pytest.mark.asyncio
async def test_browse_declarations_lists_signed(test_engine, requester):
    """The standalone browser returns signed declarations with visitor context
    and the signature, visibility-scoped like /visits."""
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={
            "answers": _PASS_ANSWERS,
            "safety_training_confirmed": True,
            "signature": "data:image/png;base64,iVBORw0KGgo=",
        },
    )

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get("/api/v1/health-declarations", params={"page_size": 100})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ours = [i for i in body["items"] if i["visit_id"] == visit["id"]]
    assert ours, body
    item = ours[0]
    assert item["result"] == "passed"
    assert item["visitor_name"]
    assert item["signature"]
    assert item["visit_date"]


@pytest.mark.asyncio
async def test_browse_declarations_scope_hides_others(test_engine):
    """A requester only sees declarations for visits they can see."""
    owner = await make_user(test_engine, role="requester")
    other = await make_user(test_engine, role="requester")
    async with authed_client(make_token(owner.id, "requester")) as oc:
        visit = await _create_gmp_visit(oc, str(owner.id))
        await oc.post(
            f"/api/v1/visits/{visit['id']}/health-declaration",
            json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
        )
    async with authed_client(make_token(other.id, "requester")) as xc:
        resp = await xc.get("/api/v1/health-declarations", params={"page_size": 100})
    assert resp.status_code == 200
    assert [i for i in resp.json()["items"] if i["visit_id"] == visit["id"]] == []


# ── RBAC ───────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_auditor_can_read_declaration(test_engine, requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
    )

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as ac:
        resp = await ac.get(f"/api/v1/visits/{visit['id']}/health-declaration")
    assert resp.status_code == 200
    assert resp.json()[0]["result"] == "passed"


@pytest.mark.asyncio
async def test_auditor_cannot_submit_declaration(test_engine, requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as ac:
        resp = await ac.post(
            f"/api/v1/visits/{visit['id']}/health-declaration",
            json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cannot_submit_for_other_users_visit_returns_404(test_engine):
    user_a = await make_user(test_engine, role="requester")
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_a.id, "requester")) as ca, \
               authed_client(make_token(user_b.id, "requester")) as cb:
        visit = await _create_gmp_visit(ca, str(user_a.id))
        resp = await cb.post(
            f"/api/v1/visits/{visit['id']}/health-declaration",
            json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
        )
    assert resp.status_code == 404


# ── Status gate ────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_cannot_declare_for_checked_in_visit(requester):
    """After check-in, the declaration window has closed."""
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    # First pass + print so the visit checks in.
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
    )
    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    # Now try to re-declare — should 409.
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
    )
    assert resp.status_code == 409


# ── Audit log ──────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_submission_writes_audit_event(test_engine, requester):
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == uuid.UUID(visit["id"]))
                .where(AuditLog.action_type == "health_decl.submit")
                .order_by(AuditLog.id.desc()).limit(1)
            )
        ).scalar_one()
    assert row.user_id == user.id
    assert row.notes == "result=passed"
    assert (row.new_value or {}).get("health_decl_status") == "passed"


# ── Tamper-proof history (PRD VMS-AU-013) ──────────────────────────────────-

@pytest.mark.asyncio
async def test_declaration_bakes_question_text_into_each_answer(test_engine, requester):
    """Question text is snapshotted into each answer so historical records
    stay valid after Admin edits the template."""
    user, client = requester
    visit = await _create_gmp_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/health-declaration",
        json={"answers": _PASS_ANSWERS, "safety_training_confirmed": True},
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (
            await db.execute(
                select(HealthDeclaration).where(
                    HealthDeclaration.visit_id == uuid.UUID(visit["id"])
                )
            )
        ).scalar_one()
    answers = row.questionnaire_data["answers"]
    assert len(answers) == 4
    for a in answers:
        assert "text" in a and a["text"]   # question text was baked in
        assert "id" in a
        assert "answer" in a
