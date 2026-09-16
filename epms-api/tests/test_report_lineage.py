"""Explaining a report must report, not describe.

Two questions landed on the same afternoon and both were answered badly: "在这个
Monthly Plan vs Actual 的表里你是怎么获取 Actual 数据,并按照什么原则把它分配到
对应的成本中心的", answered with what the Budget module is for, and "SELL-0107-S03
对应 NC JV 里是哪个成本中心或部门", answered with "that mapping is not in my data
structure" — about a table twenty rows long that sits in the same database.

The route that replaces those answers is only worth having if it cannot start
inventing. So what is asserted here is where each sentence came from:

  * the figures are whatever the owning service said, verbatim and in order;
  * a service that could not be reached is named in `unavailable`, and its
    half is absent rather than remembered;
  * the mapping rules are the rows in the table, gated by the ontology's own
    keys rather than by a second opinion held here.
"""
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ontology import get_entity
from app.services import report_lineage

pytestmark = pytest.mark.asyncio

# Every row this file writes is marked, and only marked rows are removed on the
# way out. The database is shared for the whole session, and a tidy-up that
# deleted the table would be deleting someone else's fixture.
_MARK = "TEST-CC-"

FINANCE_HALF = {"figure": "nc_posted", "label": "NC posted",
                "cost_centre_rule": {"precedence": [{"rank": 1, "matches": "exact"}]}}
BUDGET_HALF = {"figures": [{"figure": "plan", "label": "plan"},
                           {"figure": "actual_docs", "label": "actual (docs)"}]}

FULL_PERMS = {"finance.budget.view_all": True}
NO_PERMS: dict = {}


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db(test_engine):
    """A session, and the removal of exactly the rows this file added."""
    model = get_entity("nc_cost_center_map").model
    async with _factory(test_engine)() as session:
        yield session
    async with _factory(test_engine)() as cleanup:
        await cleanup.execute(
            sa.delete(model).where(model.uniops_cc_code.like(f"{_MARK}%")))
        await cleanup.commit()


@pytest.fixture
def services(monkeypatch):
    """Both owning services reachable, each returning its own half."""
    async def fin(*, bearer_token=None):
        return FINANCE_HALF

    async def bud(token=None):
        return BUDGET_HALF

    monkeypatch.setattr(report_lineage.finance_client, "budget_actual_lineage", fin)
    monkeypatch.setattr(report_lineage.budget_client, "get_actuals_lineage", bud)


async def _rows(db, rows):
    model = get_entity("nc_cost_center_map").model
    for account, dept, cc, uni in rows:
        await db.execute(sa.insert(model).values(
            id=uuid.uuid4(), account_code=account, dept_code=dept,
            nc_cc_code=cc, uniops_cc_code=f"{_MARK}{uni}"))
    await db.commit()


async def test_the_figures_are_the_owning_services_words(db, services):
    out = await report_lineage.build(db, "budget_plan_vs_actual",
                                     FULL_PERMS, "tok")
    # Plan and actual (docs) first, NC last — the order of the stacked cell.
    assert [f["label"] for f in out["figures"]] == ["plan", "actual (docs)", "NC posted"]
    # Verbatim: nothing in this service paraphrases a rule it does not own.
    assert out["figures"][2] == FINANCE_HALF
    assert not out["unavailable"]


async def test_an_unreachable_service_is_named_not_remembered(db, monkeypatch):
    """The failure mode worth guarding: a half-answer that reads as a whole one."""
    async def gone_fin(*, bearer_token=None):
        return None

    async def gone_bud(token=None):
        return None

    monkeypatch.setattr(report_lineage.finance_client, "budget_actual_lineage", gone_fin)
    monkeypatch.setattr(report_lineage.budget_client, "get_actuals_lineage", gone_bud)

    out = await report_lineage.build(db, "budget_plan_vs_actual",
                                     FULL_PERMS, "tok")
    assert out["figures"] == []
    assert len(out["unavailable"]) == 2
    # The prose half still answers "what is this report" — that part is ours.
    assert out["what_it_is"] and out["why_three_figures"]


async def test_the_rules_are_the_rows(db, services):
    await _rows(db, [("6601", "0107", "S03", "SELL-0107-S03"),
                             ("6602", "0100", "ALL", "GA-0100")])
    out = await report_lineage.build(db, "budget_plan_vs_actual",
                                     FULL_PERMS, "tok")
    mapping = out["cost_centre_mapping"]
    # count is every rule in the table, not only the two planted here.
    assert mapping["available"] and mapping["count"] >= 2
    # The row that answers the question that started this.
    assert {"expense_account": "6601", "nc_department": "0107",
            "nc_cost_centre": "S03",
            "uniops_cost_centre": f"{_MARK}SELL-0107-S03"} in mapping["rules"]
    assert "ALL" in mapping["wildcard"]


async def test_without_budget_access_the_rules_are_withheld_with_a_reason(
        db, services):
    await _rows(db, [("6601", "0107", "S03", "SELL-0107-S03")])
    out = await report_lineage.build(db, "budget_plan_vs_actual",
                                     NO_PERMS, "tok")
    mapping = out["cost_centre_mapping"]
    assert mapping["available"] is False
    assert "rules" not in mapping
    assert mapping["why"]
    # How the mapping works is not the same secret as which code maps where:
    # the explanation still arrives, which is the whole point of splitting them.
    assert out["figures"] and out["notes"]


async def test_an_unknown_report_is_refused_rather_than_guessed():
    with pytest.raises(LookupError):
        report_lineage._load("some_other_report")


async def test_the_prose_carries_only_what_no_code_can_state():
    """A guard on the division of labour, not on wording.

    The failure this is aimed at is someone answering a future "why is my
    number wrong" by adding the account list or the ledger operations to the
    YAML. Both are reported by the services that enforce them; a second copy
    here would be the fifth conflicting description of the same rule, which is
    the exact history the guide layer exists because of.
    """
    raw = report_lineage._load("budget_plan_vs_actual")
    text = str(raw).lower()
    for leaked in ("5101", "6601", "6602", "book_expense", "actualize"):
        assert leaked not in text, (
            f"{leaked!r} is written into the prose; it belongs to the service "
            f"that enforces it and is already reported from there")
