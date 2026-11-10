"""OA EXP-002: a general expense claim has up to 20 lines (ISS-0199).

The New General Expense page stops at 20, but POST /api/v1/expenses took 21
lines with a 201 (EXP-20261103-0003). The cap is enforced on create and on
edit, for EXP only: the PRD sets no line cap for TRV.
"""
import uuid
from datetime import date
from decimal import Decimal

from app.crud import expense as expense_crud
from app.schemas.expense import ExpenseClaimCreate, ExpenseClaimUpdate, LineItemCreate
from tests.conftest import _client, _make_token


def _line_json(n):
    return {
        "line_number": n, "expense_date": "2026-11-03", "description": f"Item {n}",
        "budget_account_id": str(uuid.uuid4()), "budget_account_code": "CRM00201",
        "budget_account_name": "Training & Development",
        "net_amount": "10.00", "tax_amount": "1.30", "total_amount": "11.30",
    }


def _body(n_lines, claim_type="EXP"):
    return {"claim_type": claim_type, "submission_date": "2026-11-03", "currency": "CAD",
            "line_items": [_line_json(i + 1) for i in range(n_lines)], "trip_items": []}


def _line(n):
    return LineItemCreate(**_line_json(n))


async def test_create_refuses_21_lines():
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        r = await c.post("/api/v1/expenses", json=_body(21))
    assert r.status_code == 422, r.text
    assert "at most 20 lines" in r.json()["detail"]


async def test_create_accepts_20_lines():
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        r = await c.post("/api/v1/expenses", json=_body(20))
    assert r.status_code == 201, r.text
    assert len(r.json()["line_items"]) == 20


async def test_edit_refuses_21_lines():
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        r = await c.post("/api/v1/expenses", json=_body(1))
        assert r.status_code == 201, r.text
        claim_id = r.json()["id"]
        r = await c.patch(f"/api/v1/expenses/{claim_id}",
                          json={"line_items": _body(21)["line_items"]})
        assert r.status_code == 409, r.text
        assert "at most 20 lines" in r.json()["detail"]
        got = await c.get(f"/api/v1/expenses/{claim_id}")
    assert len(got.json()["line_items"]) == 1


async def test_trv_lines_are_not_capped(db_session):
    """No line cap for TRV in the PRD — the check must not reach it. Exercised
    through crud's own gate (the TRV endpoint additionally needs an approved
    Travel Application, irrelevant here)."""
    expense_crud._check_exp_line_count("TRV", [_line(i + 1) for i in range(25)])
    data = ExpenseClaimCreate(claim_type="EXP", submission_date=date(2026, 11, 3),
                              line_items=[_line(1)])
    claim = await expense_crud.create_claim(db_session, data, user_id=uuid.uuid4(),
                                            user_name="Emp", department_id=None,
                                            department_name="GMO")
    claim.claim_type = "TRV"  # same crud path a TRV edit takes
    claim.travel_country = "CA"
    await expense_crud.update_claim(db_session, claim, ExpenseClaimUpdate(
        line_items=[_line(i + 1) for i in range(25)]))
    assert len(claim.line_items) == 25
    assert claim.total_amount == Decimal("282.50")
