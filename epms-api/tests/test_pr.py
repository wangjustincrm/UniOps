"""Purchase Request endpoint tests."""
import pytest
import sqlalchemy as sa

URL = "/api/v1/pr"

_LINE = {
    "description": "Deep Groove Bearing 6205",
    "material_id": "PART-0001",
    "qty": "2",
    "unit": "EA",
    "unit_price": "12.50",
}


def _payload(**overrides):
    base = {
        "title": "Monthly Spare Parts Replenishment",
        "type": 3,
        "currency": "CAD",
        "notes": "Urgent restock",
        "line_items": [_LINE],
    }
    base.update(overrides)
    return base


async def _create(client, **overrides):
    resp = await client.post(URL, json=_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Basic CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pr(admin_client):
    pr = await _create(admin_client)
    assert pr["number"].startswith("PR-")
    assert pr["status"] == "draft"
    assert len(pr["line_items"]) == 1
    assert float(pr["amount"]) == 25.00
    assert pr["approval_step_idx"] == 0


@pytest.mark.asyncio
async def test_list_prs(admin_client):
    await _create(admin_client)
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_list_prs_carries_requester_name(admin_client):
    """The list shows a Requester column, so every row must name its creator.

    get_by_id resolved created_by_name via a join; the list left it null.
    """
    created = await _create(admin_client)
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    row = next(r for r in resp.json()["items"] if r["id"] == created["id"])
    assert row["created_by_name"] == "Test system_admin"


@pytest.mark.asyncio
async def test_get_pr(admin_client):
    pr = await _create(admin_client)
    resp = await admin_client.get(f"{URL}/{pr['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pr["id"]


@pytest.mark.asyncio
async def test_get_pr_not_found(admin_client):
    resp = await admin_client.get(f"{URL}/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_pr_draft(admin_client):
    pr = await _create(admin_client, title="Old Title")
    resp = await admin_client.patch(f"{URL}/{pr['id']}", json={"title": "New Title"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"


@pytest.mark.asyncio
async def test_update_pr_line_items(admin_client):
    pr = await _create(admin_client)
    new_lines = [
        {**_LINE, "qty": "5", "description": "Updated Bearing"},
        {**_LINE, "description": "Oil Seal 40x60", "qty": "3", "unit_price": "8.00"},
    ]
    resp = await admin_client.patch(f"{URL}/{pr['id']}", json={"line_items": new_lines})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["line_items"]) == 2
    assert float(data["amount"]) == pytest.approx(5 * 12.50 + 3 * 8.00)


@pytest.mark.asyncio
async def test_create_pr_unauthenticated(client):
    resp = await client.post(URL, json=_payload())
    assert resp.status_code == 403


# ── Workflow ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_pr(admin_client):
    pr = await _create(admin_client)
    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "submitted"
    assert data["submitted_at"] is not None


@pytest.mark.asyncio
async def test_cannot_edit_submitted_pr(admin_client):
    pr = await _create(admin_client)
    await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})
    resp = await admin_client.patch(f"{URL}/{pr['id']}", json={"title": "Hacked"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_full_approval_flow(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]

    # submit
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    assert r.json()["status"] == "submitted"

    # approve step 0 → in_review (step 1)
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve", "comment": "LGTM"})
    assert r.json()["status"] == "in_review"
    assert r.json()["approval_step_idx"] == 1

    # approve step 1 → in_review (step 2)
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "in_review"
    assert r.json()["approval_step_idx"] == 2

    # approve step 2 → approved
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_return_pr(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "return", "comment": "Needs clarification"})
    assert r.json()["status"] == "returned"
    assert r.json()["approval_step_idx"] == 0


@pytest.mark.asyncio
async def test_resubmit_after_return(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "return"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    assert r.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_reject_pr(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "reject"})
    assert r.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_cancel_draft_pr(admin_client):
    pr = await _create(admin_client)
    r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "cancel"})
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_invalid_action(admin_client):
    pr = await _create(admin_client)
    r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "fly"})
    assert r.status_code == 409


# ── Approval events ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approval_events(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve", "comment": "OK"})

    resp = await admin_client.get(f"{URL}/{pid}/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 2
    actions = [e["action"] for e in events]
    assert "submit" in actions
    assert "approve" in actions


# ── Approval auto-skip (crud-level) ─────────────────────────────────────────────
# The HTTP /action endpoint forwards approve/submit/etc to approval-api's engine
# (which owns the live workflow — already migrated off role_management in Task
# 5). epms-api/crud/pr.py keeps its own copy of the auto-skip logic; this test
# exercises it directly to prove it now resolves role holders from identity's
# user_roles ∪ users.role, not the retired company_config.role_management
# single *_user_id fields (phase 3).

@pytest.mark.asyncio
async def test_pr_approve_auto_skips_step_held_by_same_actor(test_engine):
    import uuid
    from sqlalchemy import select as _select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import pr as pr_crud
    from app.crud import user as user_crud
    from app.models.approval import ApprovalEvent
    from app.models.config import CompanyConfig
    from app.models.department import Department
    from app.models.pr import PurchaseRequest
    from app.schemas.auth import RegisterRequest
    from app.schemas.pr import PrActionRequest

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        # Pin a 2-step "pr" workflow (dept_manager, then finance_manager) on
        # the shared singleton config row — don't rely on whatever an earlier
        # test (e.g. test_config.py) may have left there.
        cfg = (await db.execute(_select(CompanyConfig).limit(1))).scalar_one_or_none()
        pr_workflow_defs = [
            {"id": "pr-step-0", "role": "dept_manager", "label": "Department Manager"},
            {"id": "pr-step-1", "role": "finance_manager", "label": "Finance Manager"},
        ]
        if cfg is None:
            db.add(CompanyConfig(role_permissions={}, custom_roles=[], workflow_defs={"pr": pr_workflow_defs}))
        else:
            cfg.workflow_defs = {**(cfg.workflow_defs or {}), "pr": pr_workflow_defs}
        await db.commit()

        dept = Department(code=f"D{uuid.uuid4().hex[:4].upper()}", name="Auto Skip Dept", is_active=True)
        db.add(dept)
        await db.commit()
        await db.refresh(dept)

        requester = await user_crud.create(db, RegisterRequest(
            email=f"pr-autoskip-req-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="Auto Skip Requester", role="requester"))
        requester.department_id = dept.id

        # Actor is the dept_manager of the requester's department (PRIMARY
        # role, resolved via _get_dept_manager_id — unrelated to
        # role_management). They ALSO hold "finance_manager" as an ADDITIONAL
        # role (identity's user_roles) — proving the union covers this branch
        # too, not just the PRIMARY-role case.
        actor = await user_crud.create(db, RegisterRequest(
            email=f"pr-autoskip-dm-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="Auto Skip Dept Manager", role="dept_manager"))
        actor.department_id = dept.id
        await db.commit()

        await db.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_manager')"),
            {"u": str(actor.id)})
        await db.commit()

        pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:6]}", title="Auto Skip PR", type=3,
                             created_by=requester.id, status="submitted", approval_step_idx=0)
        db.add(pr)
        await db.commit()
        await db.refresh(pr)

        result = await pr_crud.action(
            db, pr, PrActionRequest(action="approve"), actor_id=actor.id, actor_role=actor.role)
        await db.commit()

        # step 0 (dept_manager) is the explicit approve; step 1
        # (finance_manager) auto-skips because the actor also holds it.
        assert result.status == "approved"

        events = (await db.execute(
            sa.select(ApprovalEvent).where(ApprovalEvent.document_id == pr.id).order_by(ApprovalEvent.step_idx)
        )).scalars().all()
        assert [e.action for e in events] == ["approve", "approve"]
        assert events[1].comment == "Auto-approved (same approver holds both roles)"
        assert events[1].actor_role == "finance_manager"
