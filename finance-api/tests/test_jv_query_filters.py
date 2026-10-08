"""The voucher query: ranges, the auxiliary picker, and the four voucher states.

Why this file exists: the list used to offer ONE exact fiscal_period, so listing
a quarter meant three requests and listing a year was impossible (user,
2026-09-22). NC's own 凭证查询 has ranges on period/date/number plus filters for
制单/审核/记账, the account, 对方科目 and the auxiliaries.

Every filter gets BOTH a case it admits and a case it excludes: a filter that
returns everything and a filter that returns nothing both satisfy a
"the match is in the result" assertion, and only the pair separates them.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.journal_voucher import (JournalVoucher, JournalVoucherLine,
                                        JvLineDimension)

BASE = "/finance/v1/journal-vouchers"


def _h():
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": "finance_manager",
                      "exp": datetime.now(timezone.utc).timestamp() + 3600},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    """Entered once for the whole test. A fixture that yields an UNentered
    client forces every call site into its own `async with`, and the second one
    then hits "Cannot reopen a client instance" — which reads like an API bug."""
    app.dependency_overrides[get_db] = lambda: db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seeded(db_session):
    """Three vouchers spread across periods, preparers and states, each with
    lines carrying different accounts and auxiliaries."""
    async def _jv(num, period, d, *, state="normal", kind=0, prepared="SUNQI",
                  checked="LIUYUHONG", manager="LIUYUHONG", debit="100.00"):
        jv = JournalVoucher(
            jv_number=f"JV-{period.replace('-', '')}-{num:04d}", voucher_word="JV",
            voucher_date=d, fiscal_period=period, summary=f"voucher {num}",
            status="posted" if state == "normal" else "draft",
            nc_source_pk=f"NCPK{num}", nc_num=num, nc_voucher_state=state,
            nc_voucher_kind=kind, nc_prepared_name=prepared, nc_checked_name=checked,
            nc_manager_name=manager, nc_attachment_count=0,
            nc_voucher_type_name="记账凭证",
            total_debit=Decimal(debit), total_credit=Decimal(debit),
            total_local_debit=Decimal(debit), total_local_credit=Decimal(debit))
        db_session.add(jv)
        await db_session.flush()
        return jv

    async def _line(jv, no, account, *, opp=None, currency="CAD", dims=()):
        ln = JournalVoucherLine(
            jv_id=jv.id, line_no=no, account_code=account, opposite_subject=opp,
            orig_debit=Decimal("100.00"), local_debit=Decimal("100.00"),
            currency=currency, fx_rate=Decimal("1"))
        db_session.add(ln)
        await db_session.flush()
        for code, text in dims:
            db_session.add(JvLineDimension(jv_line_id=ln.id, dim_code=code,
                                           value_text=text))
        await db_session.flush()
        return ln

    a = await _jv(1, "2026-06", date(2026, 6, 15), debit="100.00")
    await _line(a, 1, "510102", opp="2202 应付账款",
                dims=[("item", "CR0017"), ("department", "0104")])
    # two lines on the SAME voucher both matching the account filter — the
    # voucher must still come back once (this is why the filters are EXISTS
    # subqueries and not joins, which would also inflate `total`).
    await _line(a, 2, "510103", opp="2202 应付账款", dims=[("item", "CR0017")])

    b = await _jv(2, "2026-07", date(2026, 7, 20), prepared="ZHANGMENGQI",
                  checked="Monga Raghav", manager="Monga Raghav", debit="5000.00")
    await _line(b, 1, "660101", opp="100201 Checking", currency="USD",
                dims=[("item", "CR0071"), ("bank_account", "1033760")])

    c = await _jv(3, "2026-08", date(2026, 8, 4), state="discarded", kind=1,
                  debit="42.00")
    await _line(c, 1, "220201", dims=[("department", "0105")])
    return {"a": a, "b": b, "c": c}


async def _numbers(client, **params):
    r = await client.get(BASE, params=params, headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    nums = [i["nc_num"] for i in body["items"]]
    assert body["total"] == len(nums), f"total {body['total']} != {len(nums)} rows"
    return sorted(n for n in nums if n is not None)


# ── ranges ───────────────────────────────────────────────────────────────────

async def test_period_range_spans_months_and_excludes_the_ones_outside(client, seeded):
    """The headline gap: one exact period could not express "June through July"."""
    assert await _numbers(client, period_from="2026-06", period_to="2026-07") == [1, 2]
    assert await _numbers(client, period_from="2026-08") == [3]
    assert await _numbers(client, period_to="2026-06") == [1]


async def test_exact_period_still_works_for_existing_callers(client, seeded):
    assert await _numbers(client, period="2026-07") == [2]


async def test_date_range_is_independent_of_the_period(client, seeded):
    assert await _numbers(client, date_from="2026-07-01", date_to="2026-07-31") == [2]
    assert await _numbers(client, date_from="2026-08-01") == [3]


async def test_voucher_number_range(client, seeded):
    assert await _numbers(client, num_from=2) == [2, 3]
    assert await _numbers(client, num_from=2, num_to=2) == [2]


async def test_amount_range_reads_the_cad_total_the_list_shows(client, seeded):
    assert await _numbers(client, amount_min="1000") == [2]
    assert await _numbers(client, amount_max="99") == [3]


# ── the four voucher states, and the kinds ───────────────────────────────────

async def test_voucher_state_filter_can_find_the_discarded_one(client, seeded):
    """A discarded voucher used to be dropped at sync time, so nothing could
    report that it existed. It is imported and marked now — and never posted."""
    assert await _numbers(client, voucher_state="discarded") == [3]
    assert await _numbers(client, voucher_state="normal") == [1, 2]
    assert await _numbers(client, voucher_state="tempsave") == []


async def test_voucher_kind_separates_closing_entries(client, seeded):
    assert await _numbers(client, voucher_kind=1) == [3]
    assert await _numbers(client, voucher_kind=0) == [1, 2]


# ── people ───────────────────────────────────────────────────────────────────

async def test_preparer_checker_and_manager_filter_separately(client, seeded):
    assert await _numbers(client, prepared_by="ZHANGMENGQI") == [2]
    assert await _numbers(client, checked_by="LIUYUHONG") == [1, 3]
    assert await _numbers(client, manager="Monga Raghav") == [2]
    assert await _numbers(client, prepared_by="NOBODY") == []


# ── line-level predicates ────────────────────────────────────────────────────

async def test_account_filter_is_a_prefix_and_never_duplicates_the_voucher(client, seeded):
    """Voucher 1 has TWO lines under 5101*. A join would return it twice and
    report total=2; the EXISTS subquery must return it once."""
    assert await _numbers(client, account_code="5101") == [1]
    assert await _numbers(client, account_code="6601") == [2]
    assert await _numbers(client, account_code="9999") == []


async def test_opposite_subject_and_currency_filters(client, seeded):
    assert await _numbers(client, opposite_subject="应付账款") == [1]
    assert await _numbers(client, currency="USD") == [2]
    assert await _numbers(client, currency="CAD") == [1, 3]


# ── the auxiliary picker ─────────────────────────────────────────────────────

async def test_auxiliary_filter_by_code_and_by_value(client, seeded):
    """物料基本信息 is on 113,866 of the live book's 323,740 lines and was never
    stored at all before 0038 — this filter is the reason it now is."""
    assert await _numbers(client, aux_code="item") == [1, 2]
    assert await _numbers(client, aux_code="item", aux_value="CR0017") == [1]
    assert await _numbers(client, aux_code="bank_account", aux_value="1033760") == [2]
    assert await _numbers(client, aux_code="department", aux_value="0105") == [3]
    # a value that exists under a DIFFERENT dimension must not match
    assert await _numbers(client, aux_code="department", aux_value="CR0017") == []


async def test_filters_compose_rather_than_replace_each_other(client, seeded):
    assert await _numbers(client, period_from="2026-06", period_to="2026-08",
                          aux_code="item", prepared_by="SUNQI") == [1]
    # same window, a preparer who has no 物料 line -> empty, not "ignored"
    assert await _numbers(client, period_from="2026-06", period_to="2026-08",
                          aux_code="item", prepared_by="NOBODY") == []


# ── the form's option endpoints ──────────────────────────────────────────────

async def test_filter_options_are_read_from_the_data(client, seeded):
    r = await client.get(f"{BASE}/filter-options", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["prepared_by"]) == {"SUNQI", "ZHANGMENGQI"}
    assert set(body["manager"]) == {"LIUYUHONG", "Monga Raghav"}
    assert set(body["aux_codes"]) == {"item", "department", "bank_account"}
    assert body["voucher_states"] == ["normal", "error", "discarded", "tempsave"]


async def test_aux_values_typeahead_scopes_to_one_dimension(client, seeded):
    r = await client.get(f"{BASE}/aux-values", params={"dim_code": "item"}, headers=_h())
    assert r.json()["items"] == ["CR0017", "CR0071"]
    r = await client.get(f"{BASE}/aux-values",
                         params={"dim_code": "item", "q": "0071"}, headers=_h())
    assert r.json()["items"] == ["CR0071"]
    # scoped: a department value must not surface under `item`
    r = await client.get(f"{BASE}/aux-values",
                         params={"dim_code": "item", "q": "0104"}, headers=_h())
    assert r.json()["items"] == []


async def test_option_routes_are_not_swallowed_by_the_id_route(client, seeded):
    """/filter-options and /aux-values sit under the same prefix as /{jv_id};
    declared after it they would parse as a voucher id and 422."""
    for path in ("/filter-options", "/aux-values?dim_code=item"):
        r = await client.get(f"{BASE}{path}", headers=_h())
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text}"
