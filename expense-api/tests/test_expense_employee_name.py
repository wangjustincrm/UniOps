"""Regression: employee_name/department_name must be resolved from the shared
users/departments tables at claim creation.

The JWT access token issued by identity-api (`create_access_token`) carries only
`{sub, role, type}` — never `full_name` or `department_name`. Reading those keys
off the token therefore always yields "" and every claim was persisted with an
empty employee_name/department_name (prod claims EXP-20260729-0001/0002,
employee Etienne Clement, 2026-08-03). The Employee column in the OA Expense
Claims list was blank as a result. The fix resolves both from the DB by
employee_id.
"""
import uuid

import pytest
from sqlalchemy import text

from tests.conftest import IDENTITY_SHADOW_DDL, _client, _make_token


async def _ensure_identity_tables(db_session):
    # `users`/`departments` are identity-owned (same physical DB in prod, no ORM
    # model here); conftest shadows both for the whole session. Only clear them
    # here — dropping and recreating with a narrower column set used to leave
    # later tests querying a table that no longer had the columns they need.
    await db_session.execute(text("DELETE FROM users"))
    await db_session.execute(text("DELETE FROM departments"))
    await db_session.commit()


@pytest.fixture
async def identity_user(db_session):
    await _ensure_identity_tables(db_session)
    uid, dept_id = uuid.uuid4(), uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO departments (id, name) VALUES (:d, 'Quality Assurance')"),
        {"d": dept_id})
    await db_session.execute(
        text("INSERT INTO users (id, full_name, department_id) VALUES (:u, 'Etienne Clement', :d)"),
        {"u": uid, "d": dept_id})
    await db_session.commit()
    yield str(uid), str(dept_id)
    await db_session.execute(text("DELETE FROM users"))
    await db_session.execute(text("DELETE FROM departments"))
    await db_session.commit()


def _exp_body():
    return {
        "claim_type": "EXP",
        "submission_date": "2026-07-29",
        "currency": "CAD",
        "line_items": [{
            "line_number": 1,
            "expense_date": "2026-07-29",
            "description": "Taxi",
            "budget_account_id": str(uuid.uuid4()),
            "budget_account_code": "6000",
            "budget_account_name": "Travel",
            "total_amount": "113.00",
            "tax_amount": "13.00",
            "net_amount": "100.00",
        }],
    }


async def test_employee_and_department_resolved_from_db_when_token_lacks_them(identity_user):
    uid, dept_id = identity_user
    # token mirrors prod: sub/role/exp only — no full_name, no department_name
    token = _make_token("requester", user_id=uid)
    async with _client(token) as c:
        r = await c.post("/api/v1/expenses", json=_exp_body())
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["employee_name"] == "Etienne Clement"
    assert data["department_name"] == "Quality Assurance"
    assert data["department_id"] == dept_id


async def test_create_still_succeeds_when_identity_tables_absent(db_session):
    # No users/departments tables — the SAVEPOINT-guarded lookup must fail
    # cleanly and creation must still succeed (never 500 / poisoned transaction).
    await db_session.execute(text("DROP TABLE IF EXISTS users CASCADE"))
    await db_session.execute(text("DROP TABLE IF EXISTS departments CASCADE"))
    await db_session.commit()
    try:
        token = _make_token("requester", user_id=str(uuid.uuid4()))
        async with _client(token) as c:
            r = await c.post("/api/v1/expenses", json=_exp_body())
        assert r.status_code == 201, r.text
    finally:
        # Put the session-wide shadows back — this is the one test that removes
        # them on purpose, and everything after it needs them.
        for ddl in IDENTITY_SHADOW_DDL:
            if ddl.split()[2] in ("users", "departments"):
                await db_session.execute(text(ddl))
        await db_session.commit()
