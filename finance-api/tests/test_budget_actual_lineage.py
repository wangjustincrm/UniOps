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


# ── per-cost-centre actuals (the half a budget comparison was missing) ────────

async def test_actuals_by_cost_centre_stop_at_the_month_asked_for(db_session):
    """The window is inclusive, and a later month must not leak into it."""
    cc = await _cc(db_session, "MOH-0106-E01", "ENG")
    ba = uuid.uuid4()
    db_session.add(BudgetAccount(id=ba, code="CRM003", name="IT", is_active=True))
    await db_session.flush()
    account = DESC["categories"][0]["account_code"]
    for period, amount in (("2026-06", "60.00"), ("2026-08", "80.00"),
                           ("2026-09", "900.00")):
        await _posted_dim_event(db_session, account, amount, cc_id=cc, ba_id=ba,
                                period=period)
    await jv_crud.backfill_posted_jvs(db_session)

    to_august = await ab.nc_actuals_by_cost_center(db_session, 2026, through_month=8)
    assert [(r["cost_center_code"], r["actual"]) for r in to_august["cost_centers"]] \
        == [("MOH-0106-E01", "140.00")]

    whole_year = await ab.nc_actuals_by_cost_center(db_session, 2026)
    assert whole_year["cost_centers"][0]["actual"] == "1040.00"


async def test_actuals_by_cost_centre_uses_the_dashboards_own_basis(db_session):
    """Same exclusions, so the total is comparable with the plan beside it.

    Asserted against the dashboard's own aggregate rather than against a list of
    rules: if the two ever disagree, one of them is lying to somebody.
    """
    cc = await _cc(db_session, "MOH-0106-E01", "ENG")
    kept, excluded = uuid.uuid4(), uuid.uuid4()
    prefix = DESC["excluded_from_the_dashboard"][0]["budget_account_prefix"]
    db_session.add_all([
        BudgetAccount(id=kept, code="CRM003", name="IT", is_active=True),
        BudgetAccount(id=excluded, code=f"{prefix}99", name="Left out", is_active=True),
    ])
    await db_session.flush()
    account = DESC["categories"][0]["account_code"]
    await _posted_dim_event(db_session, account, "10.00", cc_id=cc, ba_id=kept,
                            period="2026-06")
    await _posted_dim_event(db_session, account, "90.00", cc_id=cc, ba_id=excluded,
                            period="2026-06")
    await jv_crud.backfill_posted_jvs(db_session)

    by_cc = await ab.nc_actuals_by_cost_center(db_session, 2026, through_month=6)
    per_account = await ab.nc_actuals_monthly(db_session, 2026, cc)
    from decimal import Decimal
    dashboard_total = sum(
        (Decimal(v) for months in per_account["accounts"].values()
         for v in months.values()), Decimal("0"))
    assert Decimal(by_cc["cost_centers"][0]["actual"]) == dashboard_total == Decimal("10.00")
    # And the 90 is reported as left out rather than simply gone.
    assert by_cc["dropped"]["excluded_items"]["amount"] == "90.00"


async def test_it_says_what_it_could_not_place(db_session):
    """Money in no cell of the report, split by why it is in none.

    Both buckets, because they mean different things to whoever has to fix
    them: a posting with a budget account and no cost centre is an unmapped
    combination, and one with neither key is a voucher nobody can attribute at
    all. Either way it is spending that a plan-vs-actual comparison would
    otherwise quietly leave out, which reads as money saved.
    """
    ba = uuid.uuid4()
    db_session.add(BudgetAccount(id=ba, code="CRM003", name="IT", is_active=True))
    await db_session.flush()
    # keyed by income-expense item, but the map resolved no cost centre
    await _posted_dim_event(db_session, DESC["categories"][0]["account_code"],
                            "77.00", ba_id=ba, period="2026-06")
    # inside the by-account-code family, and no budget account carries that code
    await _posted_dim_event(db_session, DESC["categories"][-1]["account_code"],
                            "13.00", period="2026-06")
    await jv_crud.backfill_posted_jvs(db_session)

    out = await ab.nc_actuals_by_cost_center(db_session, 2026, through_month=12)
    assert out["cost_centers"] == []
    assert out["dropped"]["no_cost_center"] == {"lines": 1, "amount": "77.00"}
    assert out["dropped"]["no_budget_account"] == {"lines": 1, "amount": "13.00"}
    assert out["dropped_means"]


async def test_the_dropped_buckets_do_not_overlap(db_session):
    """They are published under one heading, so they will be added together.

    A payroll line with no cost centre satisfies two of the three descriptions.
    Counting it twice inflated the "missing" figure by 906,456.65 against real
    data, which is the kind of number someone takes to a meeting.

    The identity asserted here is the one that makes the set trustworthy: what
    the dashboard counts, plus what has no cost centre to hang it on, is what
    this aggregate totals.
    """
    from decimal import Decimal
    cc = await _cc(db_session, "MOH-0106-E01", "ENG")
    kept, excluded = uuid.uuid4(), uuid.uuid4()
    prefix = DESC["excluded_from_the_dashboard"][0]["budget_account_prefix"]
    db_session.add_all([
        BudgetAccount(id=kept, code="CRM003", name="IT", is_active=True),
        BudgetAccount(id=excluded, code=f"{prefix}99", name="Left out", is_active=True),
    ])
    await db_session.flush()
    account = DESC["categories"][0]["account_code"]
    await _posted_dim_event(db_session, account, "10.00", cc_id=cc, ba_id=kept,
                            period="2026-03")
    # excluded AND without a cost centre — the line that used to land in two
    # buckets at once
    await _posted_dim_event(db_session, account, "90.00", ba_id=excluded,
                            period="2026-03")
    await _posted_dim_event(db_session, account, "7.00", ba_id=kept, period="2026-03")
    await jv_crud.backfill_posted_jvs(db_session)

    out = await ab.nc_actuals_by_cost_center(db_session, 2026, through_month=12)
    d = out["dropped"]
    assert d["excluded_items"]["amount"] == "90.00"
    assert d["no_cost_center"] == {"lines": 1, "amount": "7.00"}
    assert d["no_budget_account"] == {"lines": 0, "amount": "0.00"}

    per_cc = sum((Decimal(r["actual"]) for r in out["cost_centers"]), Decimal("0"))
    dashboard = sum(
        (Decimal(v) for months in (await ab.nc_actuals_monthly(
            db_session, 2026))["accounts"].values() for v in months.values()),
        Decimal("0"))
    assert per_cc + Decimal(d["no_cost_center"]["amount"]) == dashboard


async def test_an_empty_scope_is_not_the_whole_company(db_session):
    """The fail-closed case. cc_ids=[] must mean nothing, never everything."""
    assert (await ab.nc_actuals_by_cost_center(
        db_session, 2026, cc_ids=[]))["cost_centers"] == []


async def test_the_by_cost_centre_endpoint_serves_it(client):
    r = await client.get(
        "/finance/v1/gl/nc-actuals-by-cost-center?fiscal_year=2026&through_month=8",
        headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["through_month"] == 8 and "cost_centers" in body and "dropped" in body
