"""JV Detail (voucher lines, auxiliaries one column each) and the named
auxiliaries on the voucher detail — user, 2026-10-29.

NC's voucher screen prints 【部门：0106/Engineering】; the sync stores only the
code, so names come from the local masters. A code with no master shows alone.
"""
import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.coa import ChartOfAccount
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine, JvLineDimension
from app.models.mirrors import BudgetAccount, Department, Material

BASE = "/finance/v1/journal-vouchers"


def _h():
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": "finance_manager",
                      "exp": datetime.now(timezone.utc).timestamp() + 3600},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.clear()


async def _jv(db, num, period, d, *, nc=True):
    jv = JournalVoucher(
        jv_number=f"JV-{period.replace('-', '')}-{num:04d}", voucher_word="JV",
        voucher_date=d, fiscal_period=period, summary=f"voucher {num}", status="posted",
        nc_source_pk=f"NCPK{num}" if nc else None, nc_num=num, nc_attachment_count=0,
        nc_voucher_type_name="记账凭证",
        total_debit=Decimal("100"), total_credit=Decimal("100"),
        total_local_debit=Decimal("100"), total_local_credit=Decimal("100"))
    db.add(jv)
    await db.flush()
    return jv


async def _line(db, jv, no, account, *, summary=None, debit="0", credit="0",
                partner=None, dims=()):
    ln = JournalVoucherLine(
        jv_id=jv.id, line_no=no, account_code=account, summary=summary,
        orig_debit=Decimal(debit), orig_credit=Decimal(credit),
        local_debit=Decimal(debit), local_credit=Decimal(credit),
        currency="CAD", fx_rate=Decimal("1"), partner_name=partner)
    db.add(ln)
    await db.flush()
    for code, text in dims:
        db.add(JvLineDimension(jv_line_id=ln.id, dim_code=code, value_text=text))
    await db.flush()
    return ln


@pytest_asyncio.fixture
async def book(db_session):
    db = db_session
    db.add_all([
        Department(code="0106", name="Engineering", is_active=True),
        BudgetAccount(code="CRM004", name="折旧费", is_active=True),
        Material(code="CR0017", name="Whole milk powder"),
        ChartOfAccount(code="510101", name="制造费用(fix)", account_type="expense",
                       normal_balance="debit"),
    ])
    a = await _jv(db, 1, "2026-10", date(2026, 10, 4))
    # dims inserted out of NC order on purpose: cost centre before department
    await _line(db, a, 1, "510101", summary="Adjustment for grant depreciation",
                debit="181783.06",
                dims=[("cost_center", "E04"), ("income_expense_item", "CRM004"),
                      ("department", "0106")])
    await _line(db, a, 2, "220201", summary="=SUM(A1)", credit="181783.06",
                partner="Process and Steam", dims=[("supplier", "0000119")])
    b = await _jv(db, 2, "2026-09", date(2026, 9, 30))
    await _line(db, b, 1, "140301", summary="materials", debit="5",
                dims=[("item", "CR0017"), ("partner", "1001A1100000003CGZNE")],
                partner="Lactalis")
    # an internal draft — never in NC, so never listed (same rule as the voucher list)
    c = await _jv(db, 3, "2026-10", date(2026, 10, 5), nc=False)
    await _line(db, c, 1, "510101", debit="1", dims=[("department", "0106")])
    return a


async def test_detail_names_auxiliaries_in_nc_order(client, book):
    r = await client.get(f"{BASE}/{book.id}", headers=_h())
    assert r.status_code == 200, r.text
    lines = {ln["line_no"]: ln for ln in r.json()["lines"]}
    assert [(d["label"], d["display"]) for d in lines[1]["dims"]] == [
        ("Department", "0106/Engineering"),
        ("Income/Expense Item", "CRM004/折旧费"),
        ("Cost Center", "E04"),                      # no local master: code alone
    ]
    assert [d["display"] for d in lines[2]["dims"]] == ["0000119/Process and Steam"]


async def test_lines_one_column_per_auxiliary(client, book):
    r = await client.get(f"{BASE}/lines", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3                         # the internal draft is not listed
    assert [c["code"] for c in body["dim_columns"]] == [
        "department", "income_expense_item", "cost_center", "supplier", "partner", "item"]
    rows = [(i["jv_number"], i["line_no"]) for i in body["items"]]
    assert rows == [("JV-202610-0001", 1), ("JV-202610-0001", 2), ("JV-202609-0002", 1)]
    first = body["items"][0]
    assert first["account_name"] == "制造费用(fix)"
    assert first["aux"] == {"department": "0106/Engineering",
                            "income_expense_item": "CRM004/折旧费", "cost_center": "E04"}
    # 客商 carries NC's raw value pk as its code — the name alone is shown
    assert body["items"][2]["aux"] == {"item": "CR0017/Whole milk powder",
                                       "partner": "Lactalis"}


async def test_lines_filters_apply_to_the_line(client, book):
    r = await client.get(f"{BASE}/lines", params={"account_code": "5101"}, headers=_h())
    assert [(i["jv_number"], i["line_no"]) for i in r.json()["items"]] == [("JV-202610-0001", 1)]
    r = await client.get(f"{BASE}/lines", params={"aux_code": "item", "aux_value": "CR0017"},
                         headers=_h())
    assert [i["jv_number"] for i in r.json()["items"]] == ["JV-202609-0002"]
    r = await client.get(f"{BASE}/lines", params={"period_from": "2026-10"}, headers=_h())
    assert r.json()["total"] == 2
    r = await client.get(f"{BASE}/lines", params={"limit": 1, "offset": 1}, headers=_h())
    assert r.json()["total"] == 3 and len(r.json()["items"]) == 1


async def test_export_csv_expands_auxiliaries(client, book):
    r = await client.get(f"{BASE}/lines/export", params={"period_from": "2026-10"},
                         headers=_h())
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="jv-detail-2026-10_end.csv"' in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")      # BOM: Excel reads the Chinese
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    head, data = rows[0], rows[1:]
    assert head[-6:] == ["Department", "Income/Expense Item", "Cost Center",
                         "Supplier", "Partner", "Item"]
    assert len(data) == 2
    rec = [dict(zip(head, row)) for row in data]
    assert rec[0]["Department"] == "0106/Engineering"
    assert rec[0]["Cost Center"] == "E04" and rec[0]["Supplier"] == ""
    assert rec[0]["Debit (CAD)"] == "181783.06" and rec[0]["Account Name"] == "制造费用(fix)"
    assert rec[1]["Supplier"] == "0000119/Process and Steam"
    assert rec[1]["Summary"] == "'=SUM(A1)"            # never a formula in Excel
