import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from jose import jwt as _jwt

from app.core.config import settings
from app.crud import balance as balance_crud
from app.models.catalog import BudgetAccount
from app.models.ledger import BudgetLedger


def _token(sub: uuid.UUID, role: str) -> str:
    return _jwt.encode(
        {"sub": str(sub), "role": role, "type": "access"},
        settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )


async def _seed_ledger_rows(
    db_session, seed_two_cc_plans, operation: str,
    *, source_service: str = "test", source_doc_type: str = "test_doc",
) -> None:
    """seed_two_cc_plans only creates BudgetPlan/BudgetPlanLine rows — the
    /actuals and /actuals/opening endpoints read BudgetLedger instead, which
    is otherwise empty. Without this, "cc_b not in result" would hold
    vacuously (no rows at all) and the scoping tests wouldn't actually
    exercise the leak they're meant to catch.

    source_service/source_doc_type default to arbitrary test values for the
    /actuals endpoint (list_monthly_actuals doesn't filter on them); the
    opening-balance test overrides them to match list_opening_balances'
    hardcoded ("manual", "opening_balance") filter.
    """
    acct_id = (await db_session.execute(
        sa.select(BudgetAccount.id).where(BudgetAccount.code == "SCOPE-ACC")
    )).scalar_one()
    db_session.add_all([
        BudgetLedger(
            source_service=source_service, source_doc_type=source_doc_type,
            source_doc_id=uuid.uuid4(), operation=operation,
            cost_center_id=seed_two_cc_plans["cc_a"], account_id=acct_id,
            fiscal_year=2026, month=1, amount=Decimal("100"),
        ),
        BudgetLedger(
            source_service=source_service, source_doc_type=source_doc_type,
            source_doc_id=uuid.uuid4(), operation=operation,
            cost_center_id=seed_two_cc_plans["cc_b"], account_id=acct_id,
            fiscal_year=2026, month=1, amount=Decimal("200"),
        ),
    ])
    await db_session.commit()


@pytest.mark.asyncio
async def test_summary_empty_cc_ids_returns_no_accounts(db_session, seed_two_cc_plans):
    # seed_two_cc_plans: fixture creating approved plans in CC-A and CC-B, FY 2026.
    res = await balance_crud.get_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[])
    assert res.accounts == []


@pytest.mark.asyncio
async def test_summary_cc_ids_restricts_aggregate(db_session, seed_two_cc_plans):
    cc_a = seed_two_cc_plans["cc_a"]
    only_a = await balance_crud.get_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[cc_a])
    all_cc = await balance_crud.get_actuals_summary(db_session, fiscal_year=2026)
    a_total = sum(x.annual_budget for x in only_a.accounts)
    all_total = sum(x.annual_budget for x in all_cc.accounts)
    assert a_total < all_total  # A-only excludes CC-B's plan


@pytest.mark.asyncio
async def test_monthly_summary_empty_cc_ids_returns_no_accounts(db_session, seed_two_cc_plans):
    res = await balance_crud.get_monthly_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[])
    assert res.accounts == []


@pytest.mark.asyncio
async def test_monthly_summary_cc_ids_restricts_aggregate(db_session, seed_two_cc_plans):
    cc_a = seed_two_cc_plans["cc_a"]
    only_a = await balance_crud.get_monthly_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[cc_a])
    all_cc = await balance_crud.get_monthly_actuals_summary(db_session, fiscal_year=2026)
    a_total = sum(x.plan_year for x in only_a.accounts)
    all_total = sum(x.plan_year for x in all_cc.accounts)
    assert a_total < all_total  # A-only excludes CC-B's plan


# ── HTTP endpoint tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scope_endpoint_dept_user(client, dept_manager_token, seed_two_cc_plans):
    r = await client.get("/api/v1/actuals/scope",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["full_access"] is False
    ids = {c["id"] for c in body["cost_centers"]}
    assert str(seed_two_cc_plans["cc_a"]) in ids  # dept-A manager sees CC-A
    assert str(seed_two_cc_plans["cc_b"]) not in ids


@pytest.mark.asyncio
async def test_scope_endpoint_admin_full_access(client, admin_token, seed_two_cc_plans):
    r = await client.get("/api/v1/actuals/scope",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["full_access"] is True
    ids = {c["id"] for c in body["cost_centers"]}
    assert str(seed_two_cc_plans["cc_a"]) in ids
    assert str(seed_two_cc_plans["cc_b"]) in ids


@pytest.mark.asyncio
async def test_summary_scopes_to_department(client, dept_manager_token, admin_token, seed_two_cc_plans):
    r = await client.get("/api/v1/actuals/summary?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200  # never 403
    scoped_total = sum(float(a["annual_budget"]) for a in r.json()["accounts"])
    r_all = await client.get("/api/v1/actuals/summary?fiscal_year=2026",
                             headers={"Authorization": f"Bearer {admin_token}"})
    all_total = sum(float(a["annual_budget"]) for a in r_all.json()["accounts"])
    assert scoped_total < all_total


@pytest.mark.asyncio
async def test_monthly_summary_scopes_to_department(client, dept_manager_token, admin_token, seed_two_cc_plans):
    r = await client.get("/api/v1/actuals/monthly-summary?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200  # never 403
    scoped_total = sum(float(a["plan_year"]) for a in r.json()["accounts"])
    r_all = await client.get("/api/v1/actuals/monthly-summary?fiscal_year=2026",
                             headers={"Authorization": f"Bearer {admin_token}"})
    all_total = sum(float(a["plan_year"]) for a in r_all.json()["accounts"])
    assert scoped_total < all_total


@pytest.mark.asyncio
async def test_no_auth_header_rejected(client):
    r = await client.get("/api/v1/actuals/scope")
    assert r.status_code == 403  # HTTPBearer with no credentials


@pytest.mark.asyncio
async def test_dept_user_with_no_department_gets_empty_scope(client, db_session):
    # A user row exists but has no department_id -> fail-closed empty scope,
    # never full_access, never a 403/500.
    uid = uuid.uuid4()
    await db_session.execute(
        sa.text(
            "INSERT INTO users (id, department_id, role, is_active) "
            "VALUES (CAST(:id AS uuid), NULL, 'requester', true)"
        ),
        {"id": str(uid)},
    )
    await db_session.commit()
    token = _token(uid, "requester")
    r = await client.get("/api/v1/actuals/scope",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["full_access"] is False
    assert body["cost_centers"] == []


@pytest.mark.asyncio
async def test_list_actuals_scopes_to_department(client, dept_manager_token, admin_token, seed_two_cc_plans, db_session):
    await _seed_ledger_rows(db_session, seed_two_cc_plans, "book_expense")

    r = await client.get("/api/v1/actuals?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200
    ccs = {i["cost_center_id"] for i in r.json()["items"]}
    assert str(seed_two_cc_plans["cc_a"]) in ccs       # own department's data survives
    assert str(seed_two_cc_plans["cc_b"]) not in ccs   # other department must not leak

    r_all = await client.get("/api/v1/actuals?fiscal_year=2026",
                             headers={"Authorization": f"Bearer {admin_token}"})
    ccs_all = {i["cost_center_id"] for i in r_all.json()["items"]}
    assert str(seed_two_cc_plans["cc_b"]) in ccs_all   # admin/full-access still sees it
    assert ccs <= ccs_all


@pytest.mark.asyncio
async def test_list_actuals_out_of_scope_cc_is_clamped(client, dept_manager_token, seed_two_cc_plans, db_session):
    await _seed_ledger_rows(db_session, seed_two_cc_plans, "book_expense")

    r = await client.get(f"/api/v1/actuals?fiscal_year=2026&cost_center_id={seed_two_cc_plans['cc_b']}",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200                        # never 403
    ccs = {i["cost_center_id"] for i in r.json()["items"]}
    assert str(seed_two_cc_plans["cc_a"]) in ccs       # clamped to own dept, not emptied
    assert str(seed_two_cc_plans["cc_b"]) not in ccs   # clamped, not honoured


@pytest.mark.asyncio
async def test_list_opening_scopes_to_department(client, dept_manager_token, seed_two_cc_plans, db_session):
    await _seed_ledger_rows(
        db_session, seed_two_cc_plans, "opening",
        source_service="manual", source_doc_type="opening_balance",
    )

    r = await client.get("/api/v1/actuals/opening?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200
    ccs = {i["cost_center_id"] for i in r.json()["items"]}
    assert str(seed_two_cc_plans["cc_b"]) not in ccs
