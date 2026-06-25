"""Admin endpoints (W11 / S2-C).

Quality Manager roster tests live in test_approval_integration.py — those
landed in W10. This file covers the W11 additions:
  - GET / PUT /admin/notification-contacts
  - GET / PUT /admin/health-questions

The badge-templates endpoints (GET /badge/templates, PUT /badge/templates/{name})
are tested in test_badge.py — they shipped in W5.
"""
import pytest

from tests.conftest import authed_client, make_token, make_user


# ── Notification contacts ───────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_notification_contacts_get_initial_is_empty(admin):
    _, client = admin
    # Clear first (other tests may have populated).
    await client.put(
        "/api/v1/admin/notification-contacts",
        json={"training_email": None, "ppe_email": None},
    )
    resp = await client.get("/api/v1/admin/notification-contacts")
    assert resp.status_code == 200
    assert resp.json() == {"training_email": None, "ppe_email": None}


@pytest.mark.asyncio
async def test_notification_contacts_put_replaces_both(admin):
    _, client = admin
    resp = await client.put(
        "/api/v1/admin/notification-contacts",
        json={
            "training_email": "hr-training@royalmilk.com",
            "ppe_email": "janitor@royalmilk.com",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["training_email"] == "hr-training@royalmilk.com"
    assert body["ppe_email"] == "janitor@royalmilk.com"

    # GET returns what we set.
    got = await client.get("/api/v1/admin/notification-contacts")
    assert got.json() == body


@pytest.mark.asyncio
async def test_notification_contacts_put_null_clears_a_field(admin):
    _, client = admin
    await client.put(
        "/api/v1/admin/notification-contacts",
        json={"training_email": "t@x.com", "ppe_email": "p@x.com"},
    )
    # Clear training, keep PPE.
    resp = await client.put(
        "/api/v1/admin/notification-contacts",
        json={"training_email": None, "ppe_email": "p@x.com"},
    )
    body = resp.json()
    assert body["training_email"] is None
    assert body["ppe_email"] == "p@x.com"


@pytest.mark.asyncio
async def test_notification_contacts_rejects_invalid_email(admin):
    _, client = admin
    resp = await client.put(
        "/api/v1/admin/notification-contacts",
        json={"training_email": "not-an-email", "ppe_email": None},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_notification_contacts_requires_admin(requester):
    _, client = requester
    resp = await client.get("/api/v1/admin/notification-contacts")
    assert resp.status_code == 403

    resp = await client.put(
        "/api/v1/admin/notification-contacts",
        json={"training_email": "x@y.com", "ppe_email": None},
    )
    assert resp.status_code == 403


# ── Health questions ────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_health_questions_get_returns_default_when_unset(admin):
    _, client = admin
    # Make sure the singleton is back at default.
    await client.put(
        "/api/v1/admin/health-questions",
        json={"version": 1, "questions": []},
    )
    # When questions list is empty, server falls back to DEFAULT_HEALTH_QUESTIONS.
    resp = await client.get("/api/v1/admin/health-questions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert len(body["questions"]) >= 4   # default has 4
    assert all("id" in q and "text" in q for q in body["questions"])


@pytest.mark.asyncio
async def test_health_questions_put_replaces_template(admin):
    _, client = admin
    new_template = {
        "version": 7,
        "questions": [
            {"id": "covid_test",     "text": "COVID test in past 48h?", "fail_on": "no"},
            {"id": "international",  "text": "International travel in past 14 days?", "fail_on": "yes"},
        ],
    }
    resp = await client.put("/api/v1/admin/health-questions", json=new_template)
    assert resp.status_code == 200, resp.text
    assert resp.json() == new_template

    # GET returns what we set.
    got = await client.get("/api/v1/admin/health-questions")
    body = got.json()
    assert body["version"] == 7
    ids = [q["id"] for q in body["questions"]]
    assert ids == ["covid_test", "international"]


@pytest.mark.asyncio
async def test_health_questions_rejects_duplicate_ids(admin):
    _, client = admin
    resp = await client.put(
        "/api/v1/admin/health-questions",
        json={
            "version": 1,
            "questions": [
                {"id": "dup", "text": "Q1", "fail_on": "yes"},
                {"id": "dup", "text": "Q2", "fail_on": "no"},
            ],
        },
    )
    assert resp.status_code == 422
    assert "duplicate" in resp.text.lower()


@pytest.mark.asyncio
async def test_health_questions_template_drives_public_questionnaire(admin, requester):
    """When Admin updates the template, /api/v1/health-questions (read by
    HealthDeclForm) reflects the change immediately."""
    _, admin_client = admin
    custom = {
        "version": 99,
        "questions": [
            {"id": "x", "text": "Test question", "fail_on": "yes"},
        ],
    }
    await admin_client.put("/api/v1/admin/health-questions", json=custom)

    _, req_client = requester
    public = await req_client.get("/api/v1/health-questions")
    assert public.status_code == 200
    assert public.json()["version"] == 99
    assert public.json()["questions"][0]["id"] == "x"

    # Reset to default for other tests.
    await admin_client.put(
        "/api/v1/admin/health-questions",
        json={"version": 1, "questions": []},
    )


@pytest.mark.asyncio
async def test_health_questions_requires_admin(requester):
    _, client = requester
    resp = await client.get("/api/v1/admin/health-questions")
    assert resp.status_code == 403

    resp = await client.put(
        "/api/v1/admin/health-questions",
        json={"version": 1, "questions": []},
    )
    assert resp.status_code == 403


# ── Audit log captures admin changes ────────────────────────────────────────-

@pytest.mark.asyncio
async def test_notification_contacts_change_writes_audit_event(test_engine, admin):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.audit_log import AuditLog

    _, client = admin
    await client.put(
        "/api/v1/admin/notification-contacts",
        json={"training_email": "audit-test@x.com", "ppe_email": None},
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(
            select(AuditLog)
            .where(AuditLog.action_type == "admin.notification_contacts.update")
            .order_by(AuditLog.id.desc()).limit(1)
        )).scalar_one()
    assert (row.new_value or {}).get("notification_contacts", {}).get("training_email") == "audit-test@x.com"


@pytest.mark.asyncio
async def test_health_questions_change_writes_audit_event(test_engine, admin):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.audit_log import AuditLog

    _, client = admin
    await client.put(
        "/api/v1/admin/health-questions",
        json={
            "version": 42,
            "questions": [{"id": "q1", "text": "Test", "fail_on": "yes"}],
        },
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(
            select(AuditLog)
            .where(AuditLog.action_type == "admin.health_questions.update")
            .order_by(AuditLog.id.desc()).limit(1)
        )).scalar_one()
    assert (row.new_value or {}).get("health_questions", {}).get("version") == 42
    assert row.notes == "version=42, questions=1"

    # Reset for other tests.
    await client.put("/api/v1/admin/health-questions", json={"version": 1, "questions": []})
