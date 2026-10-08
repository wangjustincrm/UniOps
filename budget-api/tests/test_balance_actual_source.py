"""`/balance`'s actual_spent is NC's posted figure, not this service's ledger.

The ledger was built to carry the purchase chain (commit / release /
actualize) but nothing ever writes to it — in production it holds a single
2026-07-07 opening import. Serving that back as "actual spent" made a PR's
over-budget test measure this year's request against last year's opening
balance, so `/balance` now asks finance-api for the same NC figure the Budget
Dashboard displays.

What these tests pin down is the pair: the NC figure is used when it can be
had (so the numbers actually changed source), AND an unreachable finance-api
falls back to the ledger rather than to zero. The second half matters more
than it looks: `actual_spent = 0` reads as "nothing has been spent", which
makes `available` the full annual budget and passes every PR ever tested. A
fail-closed report would become a fail-open control.
"""
import uuid
from decimal import Decimal

import pytest

from app.crud import balance as balance_crud
from app.models.catalog import BudgetAccount, BudgetL1
from app.models.ledger import BudgetLedger
from app.models.plan import BudgetPlan, BudgetPlanLine

pytestmark = pytest.mark.asyncio

CC = uuid.uuid4()
FY = 2026


async def _seed(db, *, annual: Decimal, ledger_opening: Decimal):
    l1 = BudgetL1(code="L1", name="IT", sort_order=0)
    db.add(l1)
    await db.flush()
    acct = BudgetAccount(code="CRM00301", name="General IT Expense", l1_id=l1.id, sort_order=0)
    db.add(acct)
    await db.flush()
    plan = BudgetPlan(cost_center_id=CC, fiscal_year=FY, status="approved",
                      is_current=True, version=1, created_by=uuid.uuid4())
    db.add(plan)
    await db.flush()
    db.add(BudgetPlanLine(plan_id=plan.id, account_id=acct.id, month=1, amount=annual))
    # The production shape: one opening import, nothing else, ever.
    db.add(BudgetLedger(
        source_service="manual", source_doc_type="opening_balance",
        source_doc_id=uuid.uuid4(), operation="opening", cost_center_id=CC,
        account_id=acct.id, fiscal_year=FY, month=1, amount=ledger_opening,
    ))
    await db.flush()
    return acct


def _finance_returns(monkeypatch, value):
    async def _stub(**_kwargs):
        return value
    monkeypatch.setattr(balance_crud.finance_client,
                        "nc_actual_for_budget_check", _stub)


async def test_actual_spent_is_the_nc_figure_not_the_ledger(db_session, monkeypatch):
    """The whole point: NC says 5,000 and the ledger's opening says 39,942.65 —
    the balance must report 5,000, and `available` must follow it."""
    db = db_session
    acct = await _seed(db, annual=Decimal("38080"), ledger_opening=Decimal("39942.65"))
    _finance_returns(monkeypatch, Decimal("5000"))

    res = await balance_crud.get_balance(db, CC, acct.id, FY, bearer_token="t")

    assert res.actual_spent == Decimal("5000")
    assert res.available == Decimal("33080")   # 38,080 - 0 committed - 5,000


async def test_nc_zero_is_an_amount_not_a_missing_answer(db_session, monkeypatch):
    """NC genuinely having posted nothing is a real zero and must be taken as
    one — the ledger's opening must not leak back in as a floor, a max, or a
    second addend. Without this the previous test alone would still pass if the
    two sources were being summed."""
    db = db_session
    acct = await _seed(db, annual=Decimal("38080"), ledger_opening=Decimal("39942.65"))
    _finance_returns(monkeypatch, Decimal("0"))

    res = await balance_crud.get_balance(db, CC, acct.id, FY, bearer_token="t")

    assert res.actual_spent == Decimal("0")
    assert res.available == Decimal("38080")


async def test_unreachable_finance_falls_back_to_the_ledger_never_to_zero(
        db_session, monkeypatch):
    """finance-api down: keep the old (wrong, but non-zero) ledger number.

    Reporting 0 here would hand back the full annual budget as available and
    wave through every PR for as long as the outage lasted — silently, since
    nothing about an over-budget PR that passes looks like an error."""
    db = db_session
    acct = await _seed(db, annual=Decimal("38080"), ledger_opening=Decimal("39942.65"))
    _finance_returns(monkeypatch, None)

    res = await balance_crud.get_balance(db, CC, acct.id, FY, bearer_token="t")

    assert res.actual_spent == Decimal("39942.65")
    assert res.available == Decimal("-1862.65")


async def test_client_reports_unreachable_as_none_not_zero(monkeypatch):
    """The fallback above is only safe because the client says None — not 0 —
    when finance-api refuses. Pin that at the source too, so a well-meaning
    `return Decimal(0)` in the client can't quietly disarm it."""
    import httpx

    from app.services import finance_client

    class _Boom:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            return False
        async def get(self, *_a, **_kw):
            raise httpx.ConnectError("finance-api is down")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Boom())
    got = await finance_client.nc_actual_for_budget_check(
        bearer_token="t", cost_center_id=CC, account_id=uuid.uuid4(), fiscal_year=FY)
    assert got is None


async def test_http_error_status_is_also_unknown_not_zero(monkeypatch):
    """A 403/500 from finance-api is 'unknown', same as a dead socket. This is
    the live one: the endpoint is authenticated, so a token that finance-api
    rejects is a far more likely failure than the service being down."""
    import httpx

    from app.services import finance_client

    class _Resp:
        status_code = 403
        text = "Forbidden"

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            return False
        async def get(self, *_a, **_kw):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())
    got = await finance_client.nc_actual_for_budget_check(
        bearer_token="t", cost_center_id=CC, account_id=uuid.uuid4(), fiscal_year=FY)
    assert got is None


# ── An account with no budget at all ─────────────────────────────────────────

async def test_an_account_with_no_plan_leaves_nothing_available(db_session, monkeypatch):
    """A budget account nobody budgeted for must not quietly fund anything.

    `available` is the whole input to the over-budget test — epms-api's
    compute_budget_check is `amount > available` and nothing else — so an
    account with no approved plan line has to come back at zero or below, and
    then every PR against it is over budget and picks up the two extra
    approvals. Pinned because the three terms are computed separately and a
    future change to any of them (a default, a coalesce, an outer join that
    invents a row) could make "no budget" read as "budget not yet used".
    """
    db = db_session
    l1 = BudgetL1(code="L1", name="IT", sort_order=0)
    db.add(l1)
    await db.flush()
    acct = BudgetAccount(code="CRM00399", name="Unbudgeted", l1_id=l1.id, sort_order=0)
    db.add(acct)
    await db.flush()
    _finance_returns(monkeypatch, Decimal("0"))   # NC has posted nothing either

    res = await balance_crud.get_balance(db, CC, acct.id, FY, bearer_token="t")

    assert res.annual_budget == Decimal("0")
    assert res.available <= Decimal("0")


async def test_no_plan_but_nc_has_spent_goes_negative(db_session, monkeypatch):
    """Same account, money already posted against it — available must go
    negative rather than floor at zero, so the overage a PR reports is the real
    one and not understated."""
    db = db_session
    l1 = BudgetL1(code="L1", name="IT", sort_order=0)
    db.add(l1)
    await db.flush()
    acct = BudgetAccount(code="CRM00399", name="Unbudgeted", l1_id=l1.id, sort_order=0)
    db.add(acct)
    await db.flush()
    _finance_returns(monkeypatch, Decimal("500"))

    res = await balance_crud.get_balance(db, CC, acct.id, FY, bearer_token="t")

    assert res.available == Decimal("-500")


# ── /actuals/summary — the OA expense-claim account picker ──────────────────
#
# Same bug, second consumer: `/balance` was moved to NC but the summary kept
# reading the ledger, so the OA picker showed CRM00301 at -1,862.65 (38,080
# budget, 39,942.65 of opening import) while the dashboard showed it in budget.

def _finance_map_returns(monkeypatch, value):
    seen: dict = {}

    async def _stub(**kwargs):
        seen.update(kwargs)
        return value
    monkeypatch.setattr(balance_crud.finance_client, "nc_actuals_by_account", _stub)
    return seen


@pytest.mark.parametrize("cc", [CC, None], ids=["one-cc", "aggregate"])
async def test_summary_actual_is_the_nc_figure_not_the_ledger(db_session, monkeypatch, cc):
    db = db_session
    acct = await _seed(db, annual=Decimal("38080"), ledger_opening=Decimal("39942.65"))
    seen = _finance_map_returns(monkeypatch, {str(acct.id): Decimal("5000")})

    res = await balance_crud.get_actuals_summary(
        db, cost_center_id=cc, fiscal_year=FY, bearer_token="t")

    row = next(a for a in res.accounts if a.account_id == acct.id)
    assert row.actual_spent == Decimal("5000")
    assert row.available == Decimal("33080")
    assert seen["bearer_token"] == "t" and seen["cost_center_id"] == cc


async def test_summary_account_nc_has_not_touched_is_a_real_zero(db_session, monkeypatch):
    """Absent from NC's map = nothing posted. The ledger's opening must not
    leak back in for that account just because others did have NC rows."""
    db = db_session
    acct = await _seed(db, annual=Decimal("38080"), ledger_opening=Decimal("39942.65"))
    _finance_map_returns(monkeypatch, {str(uuid.uuid4()): Decimal("1")})

    res = await balance_crud.get_actuals_summary(
        db, cost_center_id=CC, fiscal_year=FY, bearer_token="t")

    row = next(a for a in res.accounts if a.account_id == acct.id)
    assert row.actual_spent == Decimal("0")
    assert row.available == Decimal("38080")


@pytest.mark.parametrize("cc", [CC, None], ids=["one-cc", "aggregate"])
async def test_summary_unreachable_finance_falls_back_to_the_ledger(db_session, monkeypatch, cc):
    db = db_session
    acct = await _seed(db, annual=Decimal("38080"), ledger_opening=Decimal("39942.65"))
    _finance_map_returns(monkeypatch, None)

    res = await balance_crud.get_actuals_summary(
        db, cost_center_id=cc, fiscal_year=FY, bearer_token="t")

    row = next(a for a in res.accounts if a.account_id == acct.id)
    assert row.actual_spent == Decimal("39942.65")
    assert row.available == Decimal("-1862.65")


async def test_by_account_client_sums_the_months(monkeypatch):
    """The dashboard endpoint answers per month; the summary wants the year."""
    import httpx

    from app.services import finance_client

    aid = str(uuid.uuid4())

    class _Resp:
        status_code = 200
        def json(self):
            return {"fiscal_year": FY, "accounts": {aid: {"1": "100.50", "9": "39.50"}}}

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            return False
        async def get(self, *_a, **_kw):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())
    got = await finance_client.nc_actuals_by_account(
        bearer_token="t", fiscal_year=FY, cost_center_id=None)
    assert got == {aid: Decimal("140.00")}


@pytest.mark.parametrize("status_code", [403, 500])
async def test_by_account_client_error_is_unknown_not_empty(monkeypatch, status_code):
    """An empty map would read as 'nothing spent anywhere' — every account at
    its full budget. A refusal has to come back as None."""
    import httpx

    from app.services import finance_client

    class _Resp:
        text = "nope"
    _Resp.status_code = status_code

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            return False
        async def get(self, *_a, **_kw):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())
    got = await finance_client.nc_actuals_by_account(
        bearer_token="t", fiscal_year=FY, cost_center_id=CC)
    assert got is None
