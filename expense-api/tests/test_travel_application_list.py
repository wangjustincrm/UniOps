"""GET /api/v1/expenses?type=TRA — Task 12b remediation on the Travel
Applications list page.

Gap 1: ExpenseClaimListItem dropped `travel_destination`, so the list page's
Destination column was always blank even though the ORM row has the value.

Gap 2: the approver-visibility loop in list_expenses() (the tuple mapping
claim_type -> workflow_defs action key) omitted ("TRA", "tra"), so an approver
role configured on workflow_defs["tra"] (e.g. dept_manager) could not see a
co-worker's submitted TRA claim in the list — only their own + ones they
personally acted on.

NOTE: like test_travel_application_create.py / test_trv_travel_application_gate.py,
this repo's tests/conftest.py has no `client` + `auth_headers` fixtures — uses
the `_client_for(role, user_id)` pattern from test_pa_permissions.py /
test_pa_list_visibility.py, and seeds company_config.workflow_defs the way
test_pa_permissions.py's `_set_role_management` does (upsert onto the single
EpmsCompanyConfig row, restoring workflow_defs afterwards since the row is
shared session-scoped state across test modules).
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

import app.db.base as db_module
from app.core.config import settings
from app.main import create_app
from app.models.company_config_mirror import EpmsCompanyConfig
from app.models.expense import ExpenseClaim


def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _set_workflow_defs(tra_steps: list[dict]) -> dict:
    """Upsert workflow_defs["tra"] onto the shared company_config row, returning
    the previous workflow_defs dict so the caller can restore it (this row is
    shared, session-scoped state read by every other test module too)."""
    async with db_module.AsyncSessionLocal() as db:
        existing = (await db.execute(select(EpmsCompanyConfig))).scalars().all()
        if existing:
            cfg = existing[0]
            previous = dict(cfg.workflow_defs or {})
            cfg.workflow_defs = {**previous, "tra": tra_steps}
        else:
            previous = {}
            db.add(EpmsCompanyConfig(
                id=uuid.uuid4(), dept_gm_opm_mapping={}, role_management={},
                workflow_defs={"tra": tra_steps},
            ))
        await db.commit()
    return previous


async def _restore_workflow_defs(previous: dict) -> None:
    async with db_module.AsyncSessionLocal() as db:
        existing = (await db.execute(select(EpmsCompanyConfig))).scalars().all()
        if existing:
            existing[0].workflow_defs = previous
            await db.commit()


def _ids(body: dict) -> set[str]:
    return {item["id"] for item in body["items"]}


# ── Gap 1: Destination column ───────────────────────────────────────────────

async def test_list_item_includes_travel_destination(admin_client, db_session):
    body = {
        "claim_type": "TRA", "submission_date": "2026-08-03",
        "purpose": "Supplier audit", "travel_destination": "Montreal",
        "travel_from_date": "2026-08-04", "travel_to_date": "2026-08-06",
        "transport_modes": ["airplane"], "travelers": [],
    }
    created = (await admin_client.post("/api/v1/expenses", json=body)).json()
    assert created["travel_destination"] == "Montreal"

    resp = await admin_client.get("/api/v1/expenses", params={"type": "TRA"})
    assert resp.status_code == 200
    items = {item["id"]: item for item in resp.json()["items"]}
    assert created["id"] in items
    assert items[created["id"]]["travel_destination"] == "Montreal", (
        "ExpenseClaimListItem dropped travel_destination — Destination column "
        "on the Travel Applications list would render blank"
    )

    # Cleanup: free the TRA-<today>-0001 numbering slot for other test modules
    # (same reasoning as test_travel_application_create.py).
    claim = await db_session.get(ExpenseClaim, uuid.UUID(created["id"]))
    await db_session.delete(claim)
    await db_session.commit()


# ── Gap 2: approver visibility for TRA ──────────────────────────────────────

@pytest.mark.asyncio
async def test_dept_manager_sees_others_submitted_tra():
    """A dept_manager configured on workflow_defs["tra"] must see a co-worker's
    submitted TRA in the list, mirroring EXP/MIL/TRV — even though they never
    acted on it and it isn't their own submission."""
    previous = await _set_workflow_defs([{"id": 0, "role": "dept_manager", "label": "Manager"}])
    try:
        submitter_id = str(uuid.uuid4())
        approver_id = str(uuid.uuid4())

        async with db_module.AsyncSessionLocal() as db:
            tra = ExpenseClaim(
                claim_number=f"TRA-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRA",
                employee_id=uuid.UUID(submitter_id), employee_name="Submitter",
                department_name="Ops", submission_date=date(2026, 8, 3),
                status="submitted", created_by=uuid.UUID(submitter_id),
            )
            db.add(tra)
            await db.commit()
            tra_id = str(tra.id)

        async with _client_for("dept_manager", approver_id) as approver:
            resp = await approver.get("/api/v1/expenses", params={"type": "TRA"})
        assert resp.status_code == 200
        assert tra_id in _ids(resp.json()), (
            "dept_manager did not see a co-worker's submitted TRA — the "
            "approver-visibility loop is missing (\"TRA\", \"tra\")"
        )
    finally:
        await _restore_workflow_defs(previous)


@pytest.mark.asyncio
async def test_dept_manager_does_not_see_others_draft_tra():
    """Sanity check on the same rule EXP/MIL/TRV already enforce: a draft claim
    (not yet submitted) stays invisible to approvers, even ones on the workflow."""
    previous = await _set_workflow_defs([{"id": 0, "role": "dept_manager", "label": "Manager"}])
    try:
        submitter_id = str(uuid.uuid4())
        approver_id = str(uuid.uuid4())

        async with db_module.AsyncSessionLocal() as db:
            tra = ExpenseClaim(
                claim_number=f"TRA-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRA",
                employee_id=uuid.UUID(submitter_id), employee_name="Submitter",
                department_name="Ops", submission_date=date(2026, 8, 3),
                status="draft", created_by=uuid.UUID(submitter_id),
            )
            db.add(tra)
            await db.commit()
            tra_id = str(tra.id)

        async with _client_for("dept_manager", approver_id) as approver:
            resp = await approver.get("/api/v1/expenses", params={"type": "TRA"})
        assert resp.status_code == 200
        assert tra_id not in _ids(resp.json())
    finally:
        await _restore_workflow_defs(previous)
