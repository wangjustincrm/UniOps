"""The rule we tell people must be the rule we run.

This module's whole reason to exist is that a manager can now ask how the NC
figure on the Budget Dashboard was arrived at and get an answer. An answer that
described a policy the code had stopped following would be worse than the
"ask Finance" it replaces — it would be wrong and confident.

So nothing here checks wording. Each test takes a claim the descriptor makes and
puts it to the code that would have to honour it: the grid, the monthly actuals,
and the resolver itself.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.crud import account_balance as ab
from app.crud import journal_voucher as jv_crud
from app.models.mirrors import BudgetAccount
from app.services import budget_actual_lineage as lineage
from app.services.cc_map_import import resolve_uniops_cc
from app.services.posting import emit_event

# The seeding helpers and the authenticated client already exist next door;
# a second copy of them would be a second thing to keep true.
from tests.test_account_balance import _cc, _h, _posted_dim_event, client  # noqa: F401

DESC = lineage.describe()


async def test_every_category_it_names_appears_on_the_grid(db_session):
    grid = await ab.budget_actual_grid(db_session, "2026-06", {})
    assert ({c["account_code"] for c in DESC["categories"]}
            == {c["account_code"] for c in grid["categories"]})
    for named in DESC["categories"]:
        on_grid = next(c for c in grid["categories"]
                       if c["account_code"] == named["account_code"])
        assert on_grid["category"] == named["category"]


async def test_what_it_says_is_excluded_is_excluded(db_session):
    """Everything the descriptor names as left out is actually left out — it
    iterates the descriptor, so an exclusion added to EXCLUDED_IO_LABELS is
    covered here the moment it is declared."""
    cc = await _cc(db_session, "MOH-0106-E01", "ENG")
    kept, excluded_ids = uuid.uuid4(), []
    db_session.add(BudgetAccount(id=kept, code="CRM003", name="IT", is_active=True))
    for entry in DESC["excluded_from_the_dashboard"]:
        aid = uuid.uuid4()
        excluded_ids.append(aid)
        db_session.add(BudgetAccount(id=aid, code=f"{entry['budget_account_prefix']}99",
                                     name=entry["what"], is_active=True))
    await db_session.flush()

    account = DESC["categories"][0]["account_code"]
    await _posted_dim_event(db_session, account, "10.00", cc_id=cc, ba_id=kept,
                            period="2026-06")
    for aid in excluded_ids:
        await _posted_dim_event(db_session, account, "90.00", cc_id=cc, ba_id=aid,
                                period="2026-06")
    await jv_crud.backfill_posted_jvs(db_session)

    res = await ab.nc_actuals_monthly(db_session, 2026, cc)
    assert res["accounts"][str(kept)] == {6: "10.00"}
    for aid in excluded_ids:
        assert str(aid) not in res["accounts"], (
            "the descriptor says this prefix is left out of the dashboard")


async def test_only_posted_vouchers_count(db_session):
    """`posted_only` in words; a draft voucher colouring nothing in fact."""
    cc = await _cc(db_session, "MOH-0106-E01", "ENG")
    ba = uuid.uuid4()
    db_session.add(BudgetAccount(id=ba, code="CRM003", name="IT", is_active=True))
    await db_session.flush()
    await _posted_dim_event(db_session, DESC["categories"][0]["account_code"],
                            "55.00", cc_id=cc, ba_id=ba, period="2026-06")
    # deliberately NOT backfilled -> the voucher stays draft
    res = await ab.nc_actuals_monthly(db_session, 2026, cc)
    assert res["accounts"] == {}
    # The claim exists and the behaviour behind it holds. What the sentence says
    # is not asserted — this file checks rules, not wording.
    assert DESC["posted_only"]


def test_the_precedence_is_the_resolvers_own_order():
    """Three rules, all matching, one winner — and then the same test again with
    the winner removed. The descriptor reports the order it observed; this
    asserts the order it observed is the one a real map produces."""
    ranks = [step["rank"] for step in DESC["cost_centre_rule"]["precedence"]]
    assert ranks == [1, 2, 3]

    rules = [
        {"account_code": "6601", "dept_code": "0107", "nc_cc_code": "S03",
         "uniops_cc_code": "exact"},
        {"account_code": "6601", "dept_code": "0107", "nc_cc_code": "ALL",
         "uniops_cc_code": "dept"},
        {"account_code": "6601", "dept_code": "ALL", "nc_cc_code": "ALL",
         "uniops_cc_code": "account"},
    ]
    assert resolve_uniops_cc(rules, "6601", "0107", "S03") == "exact"
    assert resolve_uniops_cc(rules[1:], "6601", "0107", "S03") == "dept"
    assert resolve_uniops_cc(rules[2:], "6601", "0107", "S03") == "account"

    order = [s["matches"] for s in DESC["cost_centre_rule"]["precedence"]]
    assert "AND" in order[0]        # the most specific rule is described first
    assert "nc_cc_code = ALL" in order[1]
    assert "both ALL" in order[2]


def test_an_unmatched_combination_gets_no_cost_centre():
    assert lineage.unmatched_result() is None
    assert DESC["cost_centre_rule"]["unmatched_returns"] is None


async def test_an_unmatched_line_surfaces_as_an_exception(db_session):
    """The claim that a miss is visible rather than silent, put to the grid."""
    from sqlalchemy import text
    ba = uuid.uuid4()
    db_session.add(BudgetAccount(id=ba, code="CRM003", name="IT", is_active=True))
    await db_session.flush()
    account = DESC["categories"][-1]["account_code"]
    await _posted_dim_event(db_session, account, "77.00", ba_id=ba, period="2026-06")
    await jv_crud.backfill_posted_jvs(db_session)
    await db_session.execute(text(
        "update journal_voucher_lines set nc_cc_code = 'ZZ9' "
        f"where account_code = '{account}' and cost_center_id is null"))
    await db_session.flush()

    grid = await ab.budget_actual_grid(db_session, "2026-06", {})
    assert [r["nc_cc_code"] for r in grid["unmapped"]] == ["ZZ9"]


async def test_the_endpoint_serves_it(client):
    r = await client.get("/finance/v1/gl/budget-actual/lineage", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["figure"] == "nc_posted"
    assert body["cost_centre_rule"]["rules_live_in"] == "budget_actual_cc_map"
    # Rules, never rows: the map itself is read from the table by whoever asks,
    # under their own permissions, not handed out here.
    assert "rules" not in body["cost_centre_rule"]


def test_it_does_not_promise_to_explain_other_accounts():
    """Most of the ledger is mapped by an older, account-blind rule. Saying so
    is the difference between an answer and a misleading one."""
    assert DESC["cost_centre_rule"]["other_accounts"]
