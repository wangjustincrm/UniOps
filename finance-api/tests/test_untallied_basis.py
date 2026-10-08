""""Include unposted vouchers" on the Budget Dashboard and Budget Actual pages.

NC counts a voucher once it is tallied (记账). A month still being closed reads
nearly empty on the posted basis — 2026-09 on 10-08 had 0 of 446 AP vouchers
tallied — so both budget pages got the Account Balance page's toggle.

What has to hold:

  1. One rule. status_filter (ORM) and status_sql (the roll-up's raw SQL) pick
     the same vouchers, on both bases.
  2. Unposted means a voucher NC still holds as normal. The mirror also keeps
     drafts NC has discarded or deleted since import; counting those would
     report spend that will never be booked.
  3. Off by default, and the PR over-budget criterion never takes it.
  4. Every read behind one page moves together, so a figure and its
     composition still add up on the unposted basis.
"""
import uuid
from decimal import Decimal

import pytest_asyncio
import sqlalchemy as sa
from datetime import datetime, timedelta, timezone
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.crud import account_balance as crud
from app.crud import budget_rollup
from app.crud.fiscal import status_filter, status_sql
from app.db.base import get_db
from app.main import app
from app.models.journal_voucher import JournalVoucher


async def _nc_voucher(db, *, cc, item, amount, status, state, period="2026-06",
                      nc_pk=None):
    """One NC-mirrored voucher with a single 5101 expense line."""
    jv = uuid.uuid4()
    await db.execute(sa.text(
        "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
        "fiscal_period, summary, status, total_debit, total_credit, total_local_debit, "
        "total_local_credit, nc_source_pk, nc_voucher_state, created_at, updated_at) "
        "values (:id, :num, 'JV', :d, :p, 'nc', :st, 0, 0, 0, 0, :pk, :state, now(), now())"),
        {"id": jv, "num": f"JV-NC-{str(jv)[:6]}", "d": datetime(int(period[:4]),
         int(period[5:7]), 20).date(), "p": period, "st": status,
         "pk": nc_pk or f"NC{str(jv)[:16]}", "state": state})
    await db.execute(sa.text(
        "insert into journal_voucher_lines (id, jv_id, line_no, account_code, summary, "
        "orig_debit, orig_credit, local_debit, local_credit, currency, fx_rate, "
        "cost_center_id, income_expense_item_id, created_at, updated_at) "
        "values (:id, :jv, 1, '5101', 'x', :a, 0, :a, 0, 'CAD', 1, :cc, :item, now(), now())"),
        {"id": uuid.uuid4(), "jv": jv, "a": Decimal(amount), "cc": cc, "item": item})
    await db.flush()
    return jv


@pytest_asyncio.fixture
async def mixed(db_session, seed_posted_jv_two_cc):
    """cc_a: 100 posted (fixture) + one voucher of every not-posted kind."""
    s = seed_posted_jv_two_cc
    cc, item = s["cc_a"], s["income_expense_item_id"]
    ids = {
        "draft_normal": await _nc_voucher(db_session, cc=cc, item=item, amount="7",
                                          status="draft", state="normal"),
        "draft_discarded": await _nc_voucher(db_session, cc=cc, item=item, amount="1000",
                                             status="draft", state="discarded"),
        "draft_deleted": await _nc_voucher(db_session, cc=cc, item=item, amount="2000",
                                           status="draft", state="deleted"),
        "draft_tempsave": await _nc_voucher(db_session, cc=cc, item=item, amount="4000",
                                            status="draft", state="tempsave"),
        "reversed": await _nc_voucher(db_session, cc=cc, item=item, amount="8000",
                                      status="reversed", state="normal"),
    }
    return {**s, **ids}


# ── 1 + 2: one rule, and what it lets in ─────────────────────────────────────

async def test_orm_and_sql_forms_select_the_same_vouchers(db_session, mixed):
    for include in (False, True):
        orm = set((await db_session.execute(
            select(JournalVoucher.id).where(status_filter(include)))).scalars())
        raw = set((await db_session.execute(sa.text(
            f"select v.id from journal_vouchers v where {status_sql('v', include)}"))).scalars())
        assert orm == raw, f"include_unposted={include}"
        # positive: the live draft is in exactly when asked for
        assert (mixed["draft_normal"] in orm) is include
    # never, on either basis
    for k in ("draft_discarded", "draft_deleted", "draft_tempsave", "reversed"):
        assert mixed[k] not in orm, k


async def test_uniops_own_drafts_keep_their_old_reading(db_session, mixed):
    """UniOps JVs carry no NC state; the tightening must not drop them from
    the Account Balance toggle that already counted them."""
    jv = uuid.uuid4()
    await db_session.execute(sa.text(
        "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
        "fiscal_period, status, total_debit, total_credit, total_local_debit, "
        "total_local_credit, created_at, updated_at) values "
        "(:id, 'JV-U-1', 'JV', '2026-06-01', '2026-06', 'draft', 0,0,0,0, now(), now())"),
        {"id": jv})
    got = set((await db_session.execute(
        select(JournalVoucher.id).where(status_filter(True)))).scalars())
    assert jv in got
    assert jv not in set((await db_session.execute(
        select(JournalVoucher.id).where(status_filter(False)))).scalars())


# ── 3 + 4: the dashboard reads ───────────────────────────────────────────────

async def test_dashboard_monthly_adds_only_the_live_draft(db_session, mixed):
    item = str(mixed["income_expense_item_id"])
    posted = await crud.nc_actuals_monthly(db_session, 2026, mixed["cc_a"])
    both = await crud.nc_actuals_monthly(db_session, 2026, mixed["cc_a"],
                                         include_unposted=True)
    assert Decimal(posted["accounts"][item][6]) == Decimal("100.00")
    assert Decimal(both["accounts"][item][6]) == Decimal("107.00")
    assert both["include_unposted"] is True and posted["include_unposted"] is False


async def test_vendor_drill_and_vouchers_follow_the_cell(db_session, mixed):
    item = mixed["income_expense_item_id"]
    pm = await crud.nc_partner_monthly(db_session, item, 2026, mixed["cc_a"],
                                       include_unposted=True)
    total = sum(Decimal(p["by_month"].get(6, "0")) for p in pm["partners"])
    assert total == Decimal("107.00")
    pv = await crud.nc_partner_vouchers(db_session, item, 2026, 6, mixed["cc_a"],
                                        include_unposted=True)
    flags = sorted((Decimal(r["local_debit"]), r["posted"]) for r in pv["rows"])
    assert flags == [(Decimal("7.00"), False), (Decimal("100.00"), True)]
    pv0 = await crud.nc_partner_vouchers(db_session, item, 2026, 6, mixed["cc_a"])
    assert [r["posted"] for r in pv0["rows"]] == [True]


# ── 4: the finance roll-up still adds up on the unposted basis ───────────────

async def test_rollup_and_its_composition_agree_on_both_bases(db_session, mixed):
    for include, want in ((False, "100.00"), (True, "107.00")):
        r = await budget_rollup.rollup(db_session, fiscal_year=2026, month_from=1,
                                       month_to=12, include_unposted=include)
        leaf = next(c for n in r["by_expense_centre"] for c in n["children"]
                    if c["cost_center_code"] == "SCOPE-CC-A")
        assert Decimal(leaf["actual_period"]) == Decimal(want), include
        b = await budget_rollup.breakdown(
            db_session, fiscal_year=2026, month_from=1, month_to=12,
            scope_kind="cost_centre", scope_key="SCOPE-CC-A", include_unposted=include)
        assert b["totals"]["actual_period"] == leaf["actual_period"], include
        # placed + unallocated == total, on either basis
        rec = r["reconciliation"]
        assert Decimal(rec["placed_actual_period"]) + \
               Decimal(rec["unallocated_actual_period"]) == Decimal(rec["total_actual_period"])


async def test_month_grid_follows_the_basis(db_session, mixed):
    for include, want in ((False, Decimal("100.00")), (True, Decimal("107.00"))):
        g = await crud.budget_actual_grid(db_session, "2026-06", {},
                                          include_unposted=include)
        rows = [d for c in g["categories"] for d in c["detail"]
                if d["cost_center_code"] == "SCOPE-CC-A"]
        assert sum(Decimal(d["actual"]) for d in rows) == want, include


# ── 3: the PR criterion never takes it ───────────────────────────────────────

@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _token(role: str, sub=None) -> str:
    return jwt.encode(
        {"sub": str(sub or uuid.uuid4()), "role": role,
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


async def test_budget_check_criterion_ignores_unposted(client, mixed):
    params = {"fiscal_year": 2026, "cost_center_id": str(mixed["cc_a"]),
              "account_id": str(mixed["income_expense_item_id"])}
    h = {"Authorization": f"Bearer {_token('admin')}"}
    plain = await client.get("/finance/v1/gl/nc-actual-for-budget-check",
                             params=params, headers=h)
    asked = await client.get("/finance/v1/gl/nc-actual-for-budget-check",
                             params={**params, "include_unposted": "true"}, headers=h)
    assert plain.status_code == asked.status_code == 200
    assert Decimal(plain.json()["actual"]) == Decimal(asked.json()["actual"]) == Decimal("100.00")


async def test_dashboard_endpoint_passes_the_flag_through(client, mixed):
    """The positive half of the test above: the dashboard's endpoint does see
    the live draft when asked — through the department-scoped path an ordinary
    manager takes (their department owns cc_a)."""
    h = {"Authorization": f"Bearer {_token('dept_manager', mixed['mgr_uid'])}"}
    item = str(mixed["income_expense_item_id"])
    r = await client.get("/finance/v1/gl/nc-actuals-monthly", headers=h, params={
        "fiscal_year": 2026, "cost_center_id": str(mixed["cc_a"]), "include_unposted": "true"})
    assert r.status_code == 200
    assert Decimal(r.json()["accounts"][item]["6"]) == Decimal("107.00")
