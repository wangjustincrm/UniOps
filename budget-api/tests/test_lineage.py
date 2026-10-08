"""What we say "actual" means, and what the aggregation counts, must match.

The Budget Dashboard stacks three figures in every cell and nothing could say
where any of them came from. Two of the three are computed here, so this service
now states its own rule — and a statement that drifted from the aggregation
would be worse than the silence it replaced.

Every claim in the descriptor is therefore put to the code that would have to
honour it: one ledger row per operation, and then the figures are asked which
ones they counted.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud import balance as balance_crud
from app.crud import ledger as ledger_crud
from app.models.catalog import BudgetAccount
from app.models.ledger import LEDGER_OPS, BudgetLedger
from app.services import lineage

pytestmark = pytest.mark.asyncio

_AMOUNT = Decimal("100")


async def _one_row_per_operation(db_session, seed_two_cc_plans) -> uuid.UUID:
    """The whole vocabulary, once each, same cost centre, same month."""
    acct_id = (await db_session.execute(
        sa.select(BudgetAccount.id).where(BudgetAccount.code == "SCOPE-ACC")
    )).scalar_one()
    db_session.add_all([
        BudgetLedger(
            source_service="test", source_doc_type="test_doc",
            source_doc_id=uuid.uuid4(), operation=op,
            cost_center_id=seed_two_cc_plans["cc_a"], account_id=acct_id,
            fiscal_year=2026, month=1, amount=_AMOUNT,
        )
        for op in sorted(LEDGER_OPS)
    ])
    await db_session.commit()
    return acct_id


async def test_the_operations_it_calls_actual_are_the_ones_counted(
        db_session, seed_two_cc_plans):
    acct_id = await _one_row_per_operation(db_session, seed_two_cc_plans)
    described = await lineage.describe(db_session)
    actual = next(f for f in described["figures"] if f["figure"] == "actual_docs")
    named = {o["operation"] for o in actual["operations_that_count"]}

    spent = await ledger_crud.get_actual_spent(
        db_session, seed_two_cc_plans["cc_a"], acct_id, 2026)
    assert spent == _AMOUNT * len(named), (
        f"actual counts something other than {sorted(named)}")

    # And the same answer through the dashboard's own path, which is a second
    # aggregation of the same rule and has been known to disagree.
    monthly = await balance_crud.get_monthly_actuals_summary(
        db_session, cost_center_id=seed_two_cc_plans["cc_a"], fiscal_year=2026)
    row = next(a for a in monthly.accounts if a.account_id == acct_id)
    assert row.actual_by_month[1] == _AMOUNT * len(named)


async def test_committed_is_not_part_of_actual(db_session, seed_two_cc_plans):
    """The descriptor says committed is money promised and separate. With one
    row of every operation, commit is cancelled out by release and actualize —
    so a committed figure that leaked into actual would show up as too much."""
    acct_id = await _one_row_per_operation(db_session, seed_two_cc_plans)
    committed = await ledger_crud.get_committed(
        db_session, seed_two_cc_plans["cc_a"], acct_id, 2026)
    assert committed == Decimal("0")            # 1 commit − (release + actualize)

    described = await lineage.describe(db_session)
    actual = next(f for f in described["figures"] if f["figure"] == "actual_docs")
    assert "commit" not in {o["operation"] for o in actual["operations_that_count"]}


async def test_every_operation_is_explained(db_session, seed_two_cc_plans):
    """A vocabulary word with no meaning attached would reach a reader as a
    column name, which is how "opening" got read as spending."""
    await _one_row_per_operation(db_session, seed_two_cc_plans)
    described = await lineage.describe(db_session)
    actual = next(f for f in described["figures"] if f["figure"] == "actual_docs")
    mix = {r["operation"]: r for r in actual["what_is_in_it_now"]}
    assert set(mix) == set(LEDGER_OPS)
    for op, row in mix.items():
        assert row["entries"] == 1
        assert row["means"] and row["means"] != op


async def test_it_reports_what_the_ledger_holds_not_what_it_could_hold(
        db_session, seed_two_cc_plans):
    """The fact that made this worth building: in production every row is
    `opening`, so "actual" is what finance imported rather than what documents
    have consumed. Describing the design without that reads as the opposite."""
    acct_id = (await db_session.execute(
        sa.select(BudgetAccount.id).where(BudgetAccount.code == "SCOPE-ACC")
    )).scalar_one()
    db_session.add(BudgetLedger(
        source_service="manual", source_doc_type="opening_balance",
        source_doc_id=uuid.uuid4(), operation="opening",
        cost_center_id=seed_two_cc_plans["cc_a"], account_id=acct_id,
        fiscal_year=2026, month=1, amount=_AMOUNT))
    await db_session.commit()

    described = await lineage.describe(db_session)
    actual = next(f for f in described["figures"] if f["figure"] == "actual_docs")
    assert [r["operation"] for r in actual["what_is_in_it_now"]] == ["opening"]
    assert "opening (1)" in actual["read_it_this_way"]


async def test_an_empty_ledger_says_so(db_session, seed_two_cc_plans):
    described = await lineage.describe(db_session)
    actual = next(f for f in described["figures"] if f["figure"] == "actual_docs")
    assert actual["what_is_in_it_now"] == []
    assert "nothing has been recorded" in actual["read_it_this_way"]


async def test_the_plan_figure_names_the_filter_the_query_applies(
        db_session, seed_two_cc_plans):
    """`which_plan` claims only an approved, current plan counts. Superseding
    the plan must therefore empty the figure."""
    described = await lineage.describe(db_session)
    plan = next(f for f in described["figures"] if f["figure"] == "plan")
    assert "approved" in plan["which_plan"].lower()

    before = await balance_crud.get_monthly_actuals_summary(
        db_session, cost_center_id=seed_two_cc_plans["cc_a"], fiscal_year=2026)
    assert sum(a.plan_year for a in before.accounts) == Decimal("1000")

    await db_session.execute(sa.text(
        "UPDATE budget_plans SET is_current = false "
        "WHERE cost_center_id = :cc"), {"cc": seed_two_cc_plans["cc_a"]})
    await db_session.commit()

    after = await balance_crud.get_monthly_actuals_summary(
        db_session, cost_center_id=seed_two_cc_plans["cc_a"], fiscal_year=2026)
    assert sum(a.plan_year for a in after.accounts) == Decimal("0")


async def test_the_endpoint_serves_it(client, admin_token):
    r = await client.get("/api/v1/actuals/lineage",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    assert {f["figure"] for f in r.json()["figures"]} == {"plan", "actual_docs"}


async def test_no_amounts_leave_this_endpoint(db_session, seed_two_cc_plans):
    """Counts only. It explains the pipeline; it is not a back door to spending
    that the scoped endpoints would not have shown this caller."""
    await _one_row_per_operation(db_session, seed_two_cc_plans)
    described = await lineage.describe(db_session)
    assert str(_AMOUNT) not in str(described)
