"""Assistant fixes from the 2026-10-20 issue review, batch 2: queries that
return the right rows and the right numbers.
"""
import uuid
from datetime import date
from decimal import Decimal

from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor

QUERY = "/api/v1/assistant/query"


def _uid(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _vendor(db, **kw) -> Vendor:
    v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name=kw.pop("name", "Batch2 Vendor"),
               category="Services", contact_name="A", contact_email="a@t.test", **kw)
    db.add(v)
    await db.flush()
    return v


def _po(v, uid, number, **kw) -> PurchaseOrder:
    kw.setdefault("status", "issued")
    kw.setdefault("total", Decimal("100"))
    return PurchaseOrder(number=number, title="t", type=2, vendor_id=v.id,
                         vendor_name=v.name, subtotal=kw["total"], created_by=uid, **kw)


def _inv(v, uid, ref, **kw) -> Invoice:
    amount = kw.pop("amount", Decimal("10"))
    kw.setdefault("status", "matched")
    return Invoice(internal_ref=ref, vendor_invoice_number=ref, vendor_id=v.id,
                   vendor_name=v.name, amount=amount, tax_amount=Decimal("0"),
                   total_amount=amount, invoice_date=date(2026, 10, 1),
                   due_date=date(2026, 11, 1), line_items=[], uploaded_by=uid, **kw)


# ── ISS-0060 / ISS-0058: a one-to-many link never multiplies the parent ──────

async def _po_with_invoices(test_engine, admin_client):
    """PO-A has three matched invoices, PO-B one exception invoice, PO-C none."""
    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        a, b, c = (_po(v, uid, f"PO-MANY-{tag}-{x}") for x in "ABC")
        db.add_all([a, b, c])
        await db.flush()
        db.add_all([_inv(v, uid, f"INV-MANY-{tag}-A{i}", po_id=a.id, po_number=a.number)
                    for i in range(3)])
        db.add(_inv(v, uid, f"INV-MANY-{tag}-B", po_id=b.id, po_number=b.number,
                    status="exception"))
        await db.commit()
    return tag


async def _query(client, **body):
    r = await client.post(QUERY, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_filter_on_child_rows_counts_each_parent_once(test_engine, admin_client):
    tag = await _po_with_invoices(test_engine, admin_client)
    mine = {"field": "number", "op": "like", "value": f"PO-MANY-{tag}"}

    body = await _query(admin_client, entity="purchase_order", select=["number"],
                        where=[mine, {"field": "invoices.status", "op": "eq",
                                      "value": "matched"}])
    assert [r["number"][-1] for r in body["rows"]] == ["A"], "listed once, not three times"

    body = await _query(admin_client, entity="purchase_order", metrics=["count"],
                        where=[mine, {"field": "invoices.status", "op": "in",
                                      "value": ["matched", "exception"]}])
    assert body["matched_rows"] == 2


async def test_conditions_on_one_link_describe_the_same_child(test_engine, admin_client):
    """matched AND exception on the same invoice is impossible; as separate
    joins it would have matched PO-A and PO-B through different invoices."""
    tag = await _po_with_invoices(test_engine, admin_client)
    body = await _query(admin_client, entity="purchase_order", select=["number"], where=[
        {"field": "number", "op": "like", "value": f"PO-MANY-{tag}"},
        {"field": "invoices.status", "op": "eq", "value": "matched"},
        {"field": "invoices.internal_ref", "op": "like", "value": f"{tag}-B"},
    ])
    assert body["rows"] == []


async def test_an_empty_child_field_means_no_child(test_engine, admin_client):
    tag = await _po_with_invoices(test_engine, admin_client)
    body = await _query(admin_client, entity="purchase_order", select=["number"], where=[
        {"field": "number", "op": "like", "value": f"PO-MANY-{tag}"},
        {"field": "invoices.internal_ref", "op": "eq", "value": None},
    ])
    assert [r["number"][-1] for r in body["rows"]] == ["C"]


async def test_listing_or_grouping_across_a_child_link_is_refused(admin_client):
    for body in (
        {"entity": "purchase_order", "select": ["number", "invoices.internal_ref"]},
        {"entity": "purchase_order", "group_by": ["invoices.status"], "metrics": ["count"]},
        {"entity": "purchase_order", "select": ["number"],
         "order_by": {"field": "invoices.total_amount"}},
    ):
        r = await admin_client.post(QUERY, json=body)
        assert r.status_code == 422, body
        assert "one purchase_order has many invoice rows" in r.json()["detail"]
    # The other direction is still a plain join.
    r = await admin_client.post(QUERY, json={
        "entity": "invoice", "group_by": ["order.status"], "metrics": ["count"]})
    assert r.status_code == 200, r.text


# ── ISS-0035 / ISS-0032: amounts per currency; the total of what is listed ──

async def _two_currency_pos(test_engine, admin_client):
    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        big = await _vendor(db, name=f"Big {tag}")
        small = await _vendor(db, name=f"Small {tag}")
        db.add_all([
            _po(big, uid, f"PO-CUR-{tag}-1", total=Decimal("300"), currency="CAD"),
            _po(big, uid, f"PO-CUR-{tag}-2", total=Decimal("200"), currency="CAD"),
            _po(big, uid, f"PO-CUR-{tag}-3", total=Decimal("50"), currency="USD"),
            _po(small, uid, f"PO-CUR-{tag}-4", total=Decimal("100"), currency="CAD"),
        ])
        await db.commit()
    return tag


async def test_a_money_total_is_never_added_across_currencies(test_engine, admin_client):
    tag = await _two_currency_pos(test_engine, admin_client)
    body = await _query(admin_client, entity="purchase_order", metrics=["amount", "count"],
                        where=[{"field": "number", "op": "like", "value": f"PO-CUR-{tag}"}])
    assert body["currency_split"] == "currency"
    by_cur = {r["currency"]: r for r in body["rows"]}
    assert Decimal(by_cur["CAD"]["amount"]) == Decimal("600")
    assert Decimal(by_cur["USD"]["amount"]) == Decimal("50")
    assert body["matched_rows"] == 4, "the row count is still the whole match"


async def test_grouped_totals_are_per_currency_and_say_what_was_listed(test_engine, admin_client):
    tag = await _two_currency_pos(test_engine, admin_client)
    body = await _query(admin_client, entity="purchase_order",
                        group_by=["vendor_name"], metrics=["amount"],
                        where=[{"field": "number", "op": "like", "value": f"PO-CUR-{tag}"}],
                        order_by={"field": "amount", "desc": True}, limit=1)
    # Three (vendor, currency) groups; the top one is listed.
    assert body["truncated"] and body["total_rows"] == 3
    assert body["rows"] == [{"vendor_name": f"Big {tag}", "currency": "CAD", "amount": "500.00"}]
    totals = {t["currency"]: Decimal(t["amount"]) for t in body["totals"]}
    assert totals == {"CAD": Decimal("600"), "USD": Decimal("50")}, "every group"
    shown = {t["currency"]: Decimal(t["amount"]) for t in body["totals_shown"]}
    assert shown == {"CAD": Decimal("500")}, "only the row listed"


async def test_quantities_are_not_split_by_currency(test_engine, admin_client):
    """qty is stored with the money kind; only declared amounts are split."""
    r = await admin_client.post(QUERY, json={"entity": "po_line", "metrics": ["qty"]})
    assert r.status_code == 200, r.text
    assert "currency_split" not in r.json()


def test_every_declared_currency_resolves():
    from app.core.ontology import REGISTRY
    from app.services.controlled_query import _resolve

    declared = [(e, m) for e in REGISTRY.values() for m in e.metrics.values() if m.currency]
    assert len(declared) >= 15
    for entity, metric in declared:
        _resolve(entity, metric.currency)   # raises if the path does not exist


async def test_the_export_has_one_total_row_per_currency(test_engine, admin_client):
    from io import BytesIO

    from openpyxl import load_workbook

    tag = await _two_currency_pos(test_engine, admin_client)
    r = await admin_client.post("/api/v1/assistant/export", json={
        "entity": "purchase_order", "group_by": ["vendor_name"], "metrics": ["amount"],
        "where": [{"field": "number", "op": "like", "value": f"PO-CUR-{tag}"}]})
    assert r.status_code == 200, r.text
    rows = [[c.value for c in row] for row in load_workbook(BytesIO(r.content)).active.iter_rows()]
    totals = {row[0]: row for row in rows if str(row[0] or "").startswith("Total (")}
    assert set(totals) == {"Total (CAD)", "Total (USD)"}
    assert 600 in totals["Total (CAD)"] and 50 in totals["Total (USD)"]


# ── ISS-0033 / ISS-0054: per month, and distinct counts ─────────────────────

async def test_amounts_group_by_calendar_month_in_plant_time(test_engine, admin_client):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Toronto")
    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        db.add_all([
            _po(v, uid, f"PO-MON-{tag}-1", total=Decimal("10"),
                created_at=datetime(2026, 9, 30, 22, 0, tzinfo=tz)),   # 02:00 UTC 1 Oct
            _po(v, uid, f"PO-MON-{tag}-2", total=Decimal("20"),
                created_at=datetime(2026, 10, 2, 9, 0, tzinfo=tz)),
            _po(v, uid, f"PO-MON-{tag}-3", total=Decimal("30"),
                created_at=datetime(2026, 10, 20, 9, 0, tzinfo=tz)),
        ])
        await db.commit()
    body = await _query(admin_client, entity="purchase_order",
                        group_by=["created_at:month"], metrics=["amount", "count"],
                        where=[{"field": "number", "op": "like", "value": f"PO-MON-{tag}"}],
                        order_by={"field": "created_at:month"})
    got = [(r["created_at:month"], Decimal(r["amount"]), r["count"]) for r in body["rows"]]
    assert got == [("2026-09-01", Decimal("10"), 1), ("2026-10-01", Decimal("50"), 2)]


async def test_grouping_by_a_raw_timestamp_is_refused_with_the_fix(admin_client):
    r = await admin_client.post(QUERY, json={
        "entity": "purchase_order", "group_by": ["created_at"], "metrics": ["count"]})
    assert r.status_code == 422
    assert "created_at:month" in r.json()["detail"]
    r = await admin_client.post(QUERY, json={
        "entity": "purchase_order", "group_by": ["vendor_name:month"], "metrics": ["count"]})
    assert r.status_code == 422


async def test_vendor_count_is_a_distinct_count(test_engine, admin_client):
    tag = await _two_currency_pos(test_engine, admin_client)
    body = await _query(admin_client, entity="purchase_order",
                        metrics=["vendor_count", "count"],
                        where=[{"field": "number", "op": "like", "value": f"PO-CUR-{tag}"}])
    assert body["rows"] == [{"vendor_count": 2, "count": 4}]


# ── ISS-0020: a calendar word always means the same calendar dates ──────────

def test_calendar_periods_resolve_to_the_dates_the_dashboard_uses():
    from app.services.controlled_query import calendar_range

    today = date(2026, 10, 20)   # a Tuesday
    expect = {
        "today": ("2026-10-20", "2026-10-20"),
        "yesterday": ("2026-10-19", "2026-10-19"),
        "this_week": ("2026-10-19", "2026-10-20"),
        "last_week": ("2026-10-12", "2026-10-18"),   # not 13-19 (ISS-0020 follow-up)
        "this_month": ("2026-10-01", "2026-10-20"),
        "last_month": ("2026-09-01", "2026-09-30"),
        "this_quarter": ("2026-10-01", "2026-10-20"),  # not 1 Jul-30 Sep
        "last_quarter": ("2026-07-01", "2026-09-30"),
        "this_year": ("2026-01-01", "2026-10-20"),     # not the last 12 months
        "last_year": ("2025-01-01", "2025-12-31"),
    }
    for name, (frm, to) in expect.items():
        got = calendar_range(name, today=today)
        assert (got[0].isoformat(), got[1].isoformat()) == (frm, to), name
    # 去年同期 for this quarter to date.
    got = calendar_range("this_quarter", -1, today=today)
    assert (got[0].isoformat(), got[1].isoformat()) == ("2025-10-01", "2025-10-20")
    # Across a year boundary.
    got = calendar_range("last_quarter", today=date(2026, 1, 5))
    assert (got[0].isoformat(), got[1].isoformat()) == ("2025-10-01", "2025-12-31")


async def test_a_calendar_period_filters_and_reports_its_dates(test_engine, admin_client):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.services.controlled_query import calendar_range

    tz = ZoneInfo("America/Toronto")
    frm, to = calendar_range("this_month")
    month_start = datetime.combine(frm, datetime.min.time(), tz)
    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        db.add_all([
            _po(v, uid, f"PO-CAL-{tag}-in", created_at=month_start),
            # One minute before the month began, plant time.
            _po(v, uid, f"PO-CAL-{tag}-out", created_at=month_start - timedelta(minutes=1)),
        ])
        await db.commit()
    body = await _query(admin_client, entity="purchase_order", select=["number"],
                        where=[{"field": "number", "op": "like", "value": f"PO-CAL-{tag}"}],
                        period={"calendar": "this_month"})
    assert [r["number"].rsplit("-", 1)[1] for r in body["rows"]] == ["in"]
    assert body["resolved_period"] == {"from": frm.isoformat(), "to": to.isoformat(),
                                       "calendar": "this_month"}


async def test_shift_years_needs_a_calendar_period(admin_client):
    r = await admin_client.post(QUERY, json={
        "entity": "purchase_order", "metrics": ["count"],
        "period": {"last_n_months": 3, "shift_years": -1}})
    assert r.status_code == 422


def test_the_planner_is_told_calendar_words_are_calendar_periods():
    from app.services import assistant_llm

    assert "period.calendar, never last_n_months" in assistant_llm._PLAN_SYSTEM
    enum = assistant_llm._QUERY_TOOL["input_schema"]["properties"]["period"]["properties"]["calendar"]["enum"]
    from app.services.controlled_query import CALENDAR_PERIODS
    assert tuple(enum) == CALENDAR_PERIODS


# ── ISS-0024 / ISS-0015: numbers as people type them ─────────────────────────

def test_typed_numbers_are_read_in_their_stored_form():
    from app.core.doc_numbers import normalize

    assert normalize("PR", "pr20260928-0001") == "PR-20260928-0001"
    assert normalize("PR", "20260928-0001") == "PR-20260928-0001"
    assert normalize("PR", " PR 202609280001 ") == "PR-20260928-0001"
    assert normalize("PA", "pa-20261013-0003") == "PA-20261013-0003"
    assert normalize("INV", "inv20260631") == "INV-2026-0631"
    assert normalize("AGR", "agr2026080007") == "AGR-202608-0007"
    assert normalize("PO", "po332-2610-01") == "PO-332-2610-01"
    assert normalize("PO", "PO-ABC-2604-03-2") == "PO-ABC-2604-03-2"
    # Not a format the system writes: compared exactly as typed.
    assert normalize("INV", " inv-106 ") == "inv-106"
    assert normalize("PO", "PO-SCOPE-abc123") == "PO-SCOPE-abc123"
    assert normalize("PR", "GR-20260928-0001") == "GR-20260928-0001"


async def test_a_number_typed_loosely_finds_the_document(test_engine, admin_client):
    from app.models.pr import PurchaseRequest

    day = "209901" + uuid.uuid4().hex[:2].translate(str.maketrans("abcdef", "123456"))
    number = f"PR-{day}-0001"
    async with _factory(test_engine)() as db:
        db.add(PurchaseRequest(number=number, title="loose", type=2, status="draft",
                               amount=Decimal("1"), created_by=_uid(admin_client)))
        await db.commit()
    for typed in (number.lower(), number.replace("PR-", "pr"), number[3:]):
        body = await _query(admin_client, entity="purchase_request", select=["number"],
                            where=[{"field": "number", "op": "eq", "value": typed}])
        assert [r["number"] for r in body["rows"]] == [number], typed
        assert body["numbers_read_as"][0]["used"] == number

    # A slip in the digits: not found, but the real number is offered.
    body = await _query(admin_client, entity="purchase_request", select=["number"],
                        where=[{"field": "number", "op": "eq", "value": f"PR-{day}-001"}])
    assert body["rows"] == []
    assert number in body["near_misses"]["number"]


def test_invoice_numbers_point_at_the_epms_invoice():
    from app.core.ontology import REGISTRY

    assert "INV-" in REGISTRY["invoice"].fields["internal_ref"].label
    assert REGISTRY["invoice"].fields["internal_ref"].doc_number == "INV"
    assert "use `invoice`" in REGISTRY["ap_invoice"].label
