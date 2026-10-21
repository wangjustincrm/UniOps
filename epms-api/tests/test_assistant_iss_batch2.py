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


# ── Decisions of 2026-10-21: amount defaults, and no AP copy in EPMS ────────

async def test_amounts_leave_out_cancelled_rejected_and_draft_unless_asked(
        test_engine, admin_client):
    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        db.add_all([_po(v, uid, f"PO-EXC-{tag}-{st}", status=st, total=Decimal(amt))
                    for st, amt in (("issued", "100"), ("closed", "50"), ("cancelled", "7"),
                                    ("rejected", "8"), ("draft", "9"))])
        await db.commit()
    mine = {"field": "number", "op": "like", "value": f"PO-EXC-{tag}"}

    body = await _query(admin_client, entity="purchase_order", metrics=["amount"], where=[mine])
    assert body["rows"] == [{"currency": "CAD", "amount": "150.00"}]
    assert "cancelled, rejected and draft" in body["assumption"]

    # Asked about a status: answered as asked, no default.
    body = await _query(admin_client, entity="purchase_order", metrics=["amount"],
                        where=[mine, {"field": "status", "op": "eq", "value": "cancelled"}])
    assert body["rows"] == [{"currency": "CAD", "amount": "7.00"}]
    assert "assumption" not in body

    # A count alone is not an amount: every PO is counted.
    body = await _query(admin_client, entity="purchase_order", metrics=["count"], where=[mine])
    assert body["rows"] == [{"count": 5}]


async def test_the_epms_assistant_does_not_see_finance_ap_copy(admin_client, monkeypatch):
    from app.services import assistant_llm

    shown = {}

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        shown.setdefault("names", {e["name"] for e in schema})
        shown.setdefault("retry", retry_error)
        if retry_error:
            return {"kind": "cannot", "reason": "x", "explanation": "n/a", "usage": {}}
        return {"kind": "query", "usage": {}, "query": {"entity": "ap_invoice", "metrics": ["total"]}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    r = await admin_client.post("/api/v1/assistant/chat", json={
        "message": "unpaid invoices?", "context": {"app": "epms"}})
    assert r.status_code == 200, r.text
    assert "invoice" in shown["names"] and "ap_invoice" not in shown["names"]

    # Elsewhere (Finance) it is still there.
    shown.clear()
    await admin_client.post("/api/v1/assistant/chat", json={
        "message": "AP posting?", "context": {"app": "finance"}})
    assert "ap_invoice" in shown["names"]


# ── ISS-0031 / 0044 / 0045 / 0054 / 0016: data that was there but not modelled ──

async def test_receipt_quantities_are_queryable(test_engine, admin_client):
    from app.models.gr import GoodsReceipt, GrLineItem

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = _po(v, uid, f"PO-GRL-{tag}")
        db.add(po)
        await db.flush()
        gr = GoodsReceipt(number=f"GR-GRL-{tag}", title="g", po_id=po.id, po_number=po.number,
                          vendor_id=v.id, vendor_name=v.name, gr_type="physical",
                          procurement_type=2, status="collected", created_by=uid)
        db.add(gr)
        await db.flush()
        db.add_all([
            GrLineItem(gr_id=gr.id, description="a", qty_ordered=Decimal("10"),
                       qty_received=Decimal("4"), unit="ea", unit_price=Decimal("1"),
                       line_total=Decimal("4")),
            GrLineItem(gr_id=gr.id, description="b", qty_ordered=Decimal("10"),
                       qty_received=Decimal("9"), actual_qty=Decimal("8"), unit="ea",
                       unit_price=Decimal("1"), line_total=Decimal("8")),
        ])
        await db.commit()
    body = await _query(admin_client, entity="gr_line", metrics=["qty"],
                        where=[{"field": "receipt.number", "op": "eq", "value": f"GR-GRL-{tag}"}])
    assert Decimal(body["rows"][0]["qty"]) == Decimal("12"), "4 + corrected 8, not 9"


async def test_vendors_and_parts_are_visible_to_anyone_signed_in(
        test_engine, admin_client, requester_client):
    from app.models.part import Part

    tag = uuid.uuid4().hex[:6]
    async with _factory(test_engine)() as db:
        await _vendor(db, name=f"Vend {tag}")
        customer = Vendor(code=f"C-{tag}", name=f"Cust {tag}", category="x", contact_name="",
                          contact_email="", is_supplier=False, is_customer=True)
        db.add(customer)
        db.add(Part(code=f"M{tag}", category="c", name=f"Spring {tag}", supplier="S",
                    supplier_part_no="1", unit_price=Decimal("3.50"), unit="ea"))
        await db.commit()
    for client in (admin_client, requester_client):
        body = await _query(client, entity="vendor", select=["name"],
                            where=[{"field": "name", "op": "like", "value": tag}])
        assert [r["name"] for r in body["rows"]] == [f"Vend {tag}"], "suppliers only"
        body = await _query(client, entity="part", select=["code", "unit_price"],
                            where=[{"field": "code", "op": "eq", "value": f"M{tag}"}])
        assert body["rows"] == [{"code": f"M{tag}", "unit_price": "3.50"}]


async def test_order_placement_and_match_results_are_queryable(test_engine, admin_client):
    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = _po(v, uid, f"PO-PLC-{tag}", place_order_method="email",
                 place_order_email_to="buy@vendor.test")
        db.add(po)
        await db.flush()
        db.add(_inv(v, uid, f"INV-PLC-{tag}", po_id=po.id, po_number=po.number,
                    matched_reference_total=Decimal("100"), variance=Decimal("4.50")))
        await db.commit()
    body = await _query(admin_client, entity="purchase_order",
                        select=["place_order_method", "place_order_email_to"],
                        where=[{"field": "number", "op": "eq", "value": f"PO-PLC-{tag}"}])
    assert body["rows"] == [{"place_order_method": "email",
                             "place_order_email_to": "buy@vendor.test"}]
    assert body["value_labels"]["place_order_method"]["email"].startswith("Emailed")
    body = await _query(admin_client, entity="invoice", select=["variance"],
                        where=[{"field": "po_number", "op": "eq", "value": f"PO-PLC-{tag}"}])
    assert body["rows"] == [{"variance": "4.50"}]


def test_the_prepayment_cap_rule_is_stated():
    from app.services import doc_type_guide

    rules = {r["id"]: r["answer"] for r in doc_type_guide.build("pa")["rules"]}
    assert "Max Prepayment %" in rules["prepayment_cap"]
    assert "never more than that system cap" in rules["prepayment_cap"]


# ── ISS-0021: vendor credits, agreement receipts, couriers, projects ─────────

async def test_vendor_credits_are_queryable_per_currency(test_engine, admin_client):
    import sqlalchemy as sa

    tag = uuid.uuid4().hex[:6]
    async with test_engine.begin() as conn:
        for n, (status, total, cur) in enumerate([("available", "527.03", "CAD"),
                                                  ("available", "38.41", "CAD"),
                                                  ("void", "99.00", "CAD"),
                                                  ("pending_review", "10.00", "USD")]):
            await conn.execute(sa.text(
                "INSERT INTO vendor_credits (id, credit_number, vendor_name, vendor_credit_number,"
                " credit_date, currency, amount, tax_amount, total_amount, applied_amount,"
                " remaining_amount, status, source, uploaded_at) VALUES (:id, :num, :v, :ref,"
                " :d, :cur, :t, 0, :t, 0, :t, :st, 'manual', now())"),
                {"id": uuid.uuid4(), "num": f"VC-{tag}-{n}", "v": f"Amazon {tag}",
                 "ref": f"R{n}", "d": date(2026, 10, 9), "cur": cur, "t": Decimal(total),
                 "st": status})
    body = await _query(admin_client, entity="vendor_credit", metrics=["remaining"],
                        where=[{"field": "vendor_name", "op": "like", "value": tag}])
    got = {r["currency"]: Decimal(r["remaining"]) for r in body["rows"]}
    assert got == {"CAD": Decimal("565.44"), "USD": Decimal("10.00")}, "void left out"
    body = await _query(admin_client, entity="vendor_credit", select=["credit_number"],
                        where=[{"field": "vendor_name", "op": "like", "value": tag},
                               {"field": "status", "op": "eq", "value": "pending_review"}])
    assert [r["credit_number"] for r in body["rows"]] == [f"VC-{tag}-3"]


async def test_agreement_receipts_projects_and_carriers_are_entities(admin_client):
    for entity in ("agreement_receipt", "project", "freight_carrier"):
        r = await admin_client.post(QUERY, json={"entity": entity, "metrics": ["count"]})
        assert r.status_code == 200, (entity, r.text)


async def test_courier_shipments_follow_the_freight_page_scope():
    from sqlalchemy import select

    from app.core.ontology import scope_freight_shipment
    from app.models.freight import FreightShipment

    me = uuid.uuid4()
    registrar = await scope_freight_shipment(select(FreightShipment.id),
                                             {"perms": {"epms.freight.register": True},
                                              "user_id": me}, None)
    assert "created_by" in str(registrar), "a registrar sees only their own"
    manager = await scope_freight_shipment(select(FreightShipment.id),
                                           {"perms": {"epms.freight.manage": True},
                                            "user_id": me}, None)
    assert "created_by" not in str(manager), "a freight manager sees all"


def test_who_records_agreement_receipts_is_a_rule():
    from app.services import doc_type_guide

    rules = {r["id"]: r["answer"] for r in doc_type_guide.build("agreement")["rules"]}
    assert "Record Agreement Receipts" in rules["who_records_agreement_receipts"]


def test_the_planner_is_told_not_to_substitute_a_look_alike_entity():
    from app.services import assistant_llm

    assert "Never query a different entity because its name looks similar" in \
        assistant_llm._PLAN_SYSTEM
    assert "`vendor_credit`" in assistant_llm._PLAN_SYSTEM


# ── ISS-0060 / 0058 / 0059 / 0036: facts the code decides, as fields ────────

async def test_po_facts_three_way_invoiced_and_pa(test_engine, admin_client):
    from app.models.pa import PaymentApplication
    from app.models.pa_po_link import PaPoLink
    from app.models.po import PoLineItem

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        pos = {k: _po(v, uid, f"PO-FCT-{tag}-{k}") for k in ("recv", "norecv", "bare", "second")}
        db.add_all(pos.values())
        await db.flush()
        db.add(PoLineItem(po_id=pos["recv"].id, description="x", qty=Decimal("1"), unit="ea",
                          unit_price=Decimal("10"), line_total=Decimal("10"),
                          received_qty=Decimal("1")))
        db.add_all([_inv(v, uid, f"INV-FCT-{tag}-{k}", po_id=pos[k].id, po_number=pos[k].number)
                    for k in ("recv", "norecv")])
        # One PA paying two POs: "recv" is the primary, "second" only linked.
        pa = PaymentApplication(pa_number=f"PA-FCT-{tag}", title="p", po_id=pos["recv"].id,
                                po_number=pos["recv"].number, vendor_id=v.id,
                                vendor_name=v.name, invoice_ids=[], gr_ids=[],
                                subtotal=Decimal("1"), payment_amount=Decimal("1"),
                                status="approved", created_by=uid)
        db.add(pa)
        await db.flush()
        db.add_all([PaPoLink(pa_id=pa.id, po_id=pos[k].id, po_number=pos[k].number)
                    for k in ("recv", "second")])
        await db.commit()
    body = await _query(admin_client, entity="purchase_order",
                        select=["number", "three_way_matched", "invoiced", "has_pa"],
                        where=[{"field": "number", "op": "like", "value": f"PO-FCT-{tag}"}])
    facts = {r["number"].rsplit("-", 1)[1]: (r["three_way_matched"], r["invoiced"], r["has_pa"])
             for r in body["rows"]}
    assert facts == {"recv": (True, True, True), "norecv": (False, True, False),
                     "bare": (False, False, False), "second": (False, False, True)}

    # The PA receipt gate reads the same definition.
    from app.crud.po import po_has_three_way_matched_invoice
    async with _factory(test_engine)() as db:
        assert await po_has_three_way_matched_invoice(db, pos["recv"].id) is True
        assert await po_has_three_way_matched_invoice(db, pos["norecv"].id) is False


async def test_goods_received_long_ago_without_an_invoice(test_engine, admin_client):
    from datetime import datetime, timedelta, timezone

    from app.models.gr import GoodsReceipt

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    old = datetime.now(timezone.utc) - timedelta(days=40)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        billed, unbilled = _po(v, uid, f"PO-OLD-{tag}-b"), _po(v, uid, f"PO-OLD-{tag}-u")
        db.add_all([billed, unbilled])
        await db.flush()
        db.add(_inv(v, uid, f"INV-OLD-{tag}", po_id=billed.id, po_number=billed.number))
        for po in (billed, unbilled):
            db.add(GoodsReceipt(number=f"GR-OLD-{tag}-{po.number[-1]}", title="g", po_id=po.id,
                                po_number=po.number, vendor_id=v.id, vendor_name=v.name,
                                gr_type="physical", procurement_type=2, status="collected",
                                received_at=old, created_by=uid))
        await db.commit()
    body = await _query(admin_client, entity="goods_receipt", select=["number"], where=[
        {"field": "number", "op": "like", "value": f"GR-OLD-{tag}"},
        {"field": "gr_type", "op": "eq", "value": "physical"},
        {"field": "days_since_received", "op": "gt", "value": 30},
        {"field": "po_invoiced", "op": "eq", "value": False}])
    assert [r["number"] for r in body["rows"]] == [f"GR-OLD-{tag}-u"]


async def test_average_days_to_approve_a_pr(test_engine, admin_client):
    from datetime import datetime, timedelta, timezone

    from app.models.approval import ApprovalEvent
    from app.models.pr import PurchaseRequest

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    t0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    async with _factory(test_engine)() as db:
        for n, days in enumerate((2, 4)):
            pr = PurchaseRequest(number=f"PR-AVG-{tag}-{n}", title="a", type=2,
                                 status="approved", amount=Decimal("1"), created_by=uid,
                                 submitted_at=t0)
            db.add(pr)
            await db.flush()
            db.add(ApprovalEvent(document_type="pr", document_id=pr.id,
                                 document_number=pr.number, step_idx=0, action="approve",
                                 actor_id=uid, actor_role="dept_manager",
                                 created_at=t0 + timedelta(days=days)))
        await db.commit()
    body = await _query(admin_client, entity="purchase_request",
                        metrics=["avg_days_to_approve"],
                        where=[{"field": "number", "op": "like", "value": f"PR-AVG-{tag}"}])
    assert Decimal(body["rows"][0]["avg_days_to_approve"]) == Decimal("3")


async def test_an_invoice_knows_its_payment_application(test_engine, admin_client):
    from app.models.pa import PaymentApplication

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = _po(v, uid, f"PO-INVPA-{tag}")
        db.add(po)
        await db.flush()
        inv = _inv(v, uid, f"INV-INVPA-{tag}", po_id=po.id, po_number=po.number)
        db.add(inv)
        await db.flush()
        db.add(PaymentApplication(pa_number=f"PA-INVPA-{tag}", title="p", po_id=po.id,
                                  po_number=po.number, vendor_id=v.id, vendor_name=v.name,
                                  invoice_ids=[str(inv.id)], gr_ids=[], subtotal=Decimal("1"),
                                  payment_amount=Decimal("1"), status="processed",
                                  created_by=uid))
        await db.commit()
    body = await _query(admin_client, entity="invoice", select=["pa_number", "pa_status", "status"],
                        where=[{"field": "internal_ref", "op": "eq", "value": f"INV-INVPA-{tag}"}])
    # Matched, on a processed (paid) PA: exactly the mismatch ISS-0059 asked to find.
    assert body["rows"] == [{"pa_number": f"PA-INVPA-{tag}", "pa_status": "processed",
                             "status": "matched"}]


# ── ISS-0056: how credits are netted ─────────────────────────────────────────

def test_the_credit_netting_rule_is_stated():
    from app.services import doc_type_guide

    rules = {r["id"]: r["answer"] for r in doc_type_guide.build("pa")["rules"]}
    rule = rules["vendor_credit_netting"]
    assert "oldest credit date first" in rule and "same currency" in rule
    assert "not entered on the payment application" in rule


# ── ISS-0043 / ISS-0056: when, from which account, how much after credits ──

async def test_a_pas_payment_record_gives_date_account_and_net(test_engine, admin_client):
    import sqlalchemy as sa

    tag = uuid.uuid4().hex[:6]
    bank_id = uuid.uuid4()
    async with test_engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO bank_accounts (id, name, bank_name, currency, is_active, created_at)"
            " VALUES (:id, :n, 'RBC', 'CAD', true, now())"), {"id": bank_id, "n": f"Ops {tag}"})
        await conn.execute(sa.text(
            "INSERT INTO payment_records (id, pa_number, vendor_name, payment_date,"
            " payment_method, amount, credit_applied, currency, status, bank_account_id)"
            " VALUES (:id, :pa, 'V', :d, 'eft', 6648.14, 527.03, 'CAD', 'completed', :b)"),
            {"id": uuid.uuid4(), "pa": f"PA-PAY-{tag}", "d": date(2026, 10, 19), "b": bank_id})
    body = await _query(admin_client, entity="payment_record",
                        select=["payment_date", "bank_account.name", "amount", "credit_applied"],
                        where=[{"field": "pa_number", "op": "eq", "value": f"PA-PAY-{tag}"}])
    assert body["rows"] == [{"payment_date": "2026-10-19", "bank_account.name": f"Ops {tag}",
                             "amount": "6648.14", "credit_applied": "527.03"}]


async def test_the_workflow_view_of_a_pa_carries_its_payment_date(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.services import workflow_view

    async def no_steps(*a, **k):
        return []

    async def no_events(db, doc_id):
        return []

    async def no_step(db, doc_type, items):
        for it in items:
            it.current_step = None

    monkeypatch.setattr(workflow_view.approval_client, "get_workflow_steps", no_steps)
    monkeypatch.setattr(workflow_view._SPEC["pa"]["crud"], "get_approval_events", no_events)
    monkeypatch.setattr(workflow_view, "enrich_current_step", no_step)
    pa = SimpleNamespace(id=uuid.uuid4(), pa_number="PA-1", status="processed",
                         submitted_at=None, approved_at=None,
                         paid_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
    view = await workflow_view.build(None, "pa", pa, "t")
    assert view["milestones"]["paid_on"] == date(2026, 10, 19)


# ── ISS-0014: one cost centre × account; current month; cut answers ─────────

async def test_an_account_question_gets_the_pr_widget_figure(test_engine, admin_client, monkeypatch):
    import sqlalchemy as sa

    from app.models.cost_center import CostCenter
    from app.models.department import Department
    from app.services import assistant_llm, budget_variance

    tag = uuid.uuid4().hex[:6]
    async with _factory(test_engine)() as db:
        dept = Department(code=f"D{tag}", name=f"Dept {tag}", is_active=True)
        db.add(dept)
        await db.flush()
        db.add(CostCenter(code=f"MOH-{tag}-E01", name=f"ENG-{tag}", department_id=dept.id,
                          is_active=True))
        await db.commit()

    asked = {}

    async def balance(token, cc_id, fiscal_year, account_id=None, account_code=None):
        asked.update(fiscal_year=fiscal_year, account_code=account_code)
        return {"annual_budget": "950000.00", "committed": "0", "actual_spent": "1004769.43",
                "available": "-54769.43"}

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "variance", "fiscal_year": 2026, "cost_center": f"eng-{tag}",
                "account_code": "CRM0090202", "usage": {}}

    seen = {}

    async def fake_guide(message, payload):
        seen.update(payload)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(budget_variance.budget_client, "get_balance", balance)
    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    r = await admin_client.post("/api/v1/assistant/chat",
                                json={"message": "MOH-ENG 科目 CRM0090202 今年还有多少预算？"})
    assert r.status_code == 200, r.text
    assert asked == {"fiscal_year": 2026, "account_code": "CRM0090202"}
    assert seen["kind"] == "budget_account_balance"
    assert seen["cost_center_code"] == f"MOH-{tag}-E01", "found by its name, case-insensitive"
    assert seen["over_budget_by"] == "54769.43" and seen["currency"] == "CAD"


async def test_an_unasked_month_is_this_month_not_december(admin_client, monkeypatch):
    from app.core.delegation import local_today
    from app.services import assistant_llm, budget_variance

    got = {}

    async def fake_build(db, scope, *, fiscal_year, through_month, token):
        got.update(year=fiscal_year, through=through_month)
        return {"allowed": True, "rows": []}

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "variance", "fiscal_year": local_today().year, "usage": {}}

    async def fake_guide(message, payload):
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(budget_variance, "build", fake_build)
    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    r = await admin_client.post("/api/v1/assistant/chat", json={"message": "本财年预算用了多少？"})
    assert r.status_code == 200, r.text
    assert got == {"year": local_today().year, "through": local_today().month}


def test_a_reply_cut_at_its_limit_says_so_and_drops_the_half_row():
    from types import SimpleNamespace

    from app.services import assistant_llm

    resp = SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(
        type="text", text="| 成本中心 | 预算 |\n|---|---|\n| SELL-0112 | 1,000 |\n| SELL-0113 | 754")])
    text = assistant_llm._reply_text(resp, "按成本中心列出本财年的预算")
    assert "SELL-0113" not in text and "SELL-0112" in text
    assert "被截断" in text
    whole = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="done")])
    assert assistant_llm._reply_text(whole, "x") == "done"


# ── ISS-0016: the invoice against the PO, line by line ──────────────────────

async def test_an_invoice_is_compared_with_its_po_line_by_line(test_engine, admin_client):
    from app.models.invoice_allocation import InvoicePoAllocation
    from app.models.po import PoLineItem

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = _po(v, uid, f"PO-LBL-{tag}")
        db.add(po)
        await db.flush()
        goods = PoLineItem(po_id=po.id, description="Gloves", qty=Decimal("1"), unit="ea",
                           unit_price=Decimal("100"), line_total=Decimal("100"))
        freight = PoLineItem(po_id=po.id, description="Transport", qty=Decimal("1"), unit="ea",
                             unit_price=Decimal("20"), line_total=Decimal("20"))
        db.add_all([goods, freight])
        await db.flush()
        inv = _inv(v, uid, f"INV-LBL-{tag}", amount=Decimal("100"), po_id=po.id,
                   po_number=po.number)
        db.add(inv)
        await db.flush()
        db.add(InvoicePoAllocation(invoice_id=inv.id, invoice_line_id=uuid.uuid4(), po_id=po.id,
                                   po_line_id=goods.id, allocated_amount=Decimal("100"),
                                   allocated_tax=Decimal("13"), allocated_total=Decimal("113"),
                                   variance=Decimal("0"), variance_pct=Decimal("0")))
        await db.commit()
    body = await _query(admin_client, entity="invoice_allocation",
                        select=["po_line.description", "allocated_amount", "variance"],
                        where=[{"field": "order.number", "op": "eq", "value": f"PO-LBL-{tag}"}])
    assert body["rows"] == [{"po_line.description": "Gloves", "allocated_amount": "100.00",
                             "variance": "0.00"}]
    # The transport line was never invoiced: it has no allocation, and the PO
    # line itself still shows it outstanding.


# ── ISS-0021 #4 #7 #5: the Receiving Report's totals; year on year ──────────

async def test_receiving_report_totals_come_from_the_report(test_engine, admin_client, monkeypatch):
    from datetime import datetime, timezone

    from app.models.gr import GoodsReceipt, GrLineItem
    from app.services import assistant_llm

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = _po(v, uid, f"PO-RCV-{tag}", placed_at=datetime(2099, 1, 5, 15, tzinfo=timezone.utc))
        db.add(po)
        await db.flush()
        gr = GoodsReceipt(number=f"GR-RCV-{tag}", title="g", po_id=po.id, po_number=po.number,
                          vendor_id=v.id, vendor_name=v.name, gr_type="physical",
                          procurement_type=2, status="collected", created_by=uid,
                          received_at=datetime(2099, 1, 15, 15, tzinfo=timezone.utc))
        db.add(gr)
        await db.flush()
        db.add_all([GrLineItem(gr_id=gr.id, description=d, qty_ordered=Decimal("1"),
                               qty_received=Decimal("1"), unit="ea", unit_price=Decimal("1"),
                               line_total=Decimal("1")) for d in ("a", "b")])
        await db.commit()

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "receiving", "from": "2099-01-01", "to": "2099-01-31", "usage": {}}

    seen = {}

    async def fake_guide(message, payload):
        seen.update(payload)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    r = await admin_client.post("/api/v1/assistant/chat", json={"message": "收货报表本月收了多少"})
    assert r.status_code == 200, r.text
    assert (seen["lines"], seen["receipts"], seen["orders"], seen["avg_lead_days"]) == (2, 1, 1, 10)


async def test_year_on_year_is_computed_for_the_narrator(test_engine, admin_client, monkeypatch):
    from datetime import datetime, timezone

    from app.services import assistant_llm

    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        db.add_all([
            _po(v, uid, f"PO-YOY-{tag}-1", total=Decimal("100"),
                created_at=datetime(2098, 3, 10, 15, tzinfo=timezone.utc)),
            _po(v, uid, f"PO-YOY-{tag}-2", total=Decimal("250"),
                created_at=datetime(2099, 3, 10, 15, tzinfo=timezone.utc)),
        ])
        await db.commit()

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "query", "usage": {}, "query": {
            "entity": "purchase_order", "metrics": ["amount"],
            "where": [{"field": "number", "op": "like", "value": f"PO-YOY-{tag}"}],
            "period": {"from": "2099-03-01", "to": "2099-03-31",
                       "compare_previous_year": True}}}

    seen = {}

    async def fake_narrate(message, query, result):
        seen.update(result)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    r = await admin_client.post("/api/v1/assistant/chat", json={"message": "同比增长了多少？"})
    assert r.status_code == 200, r.text
    assert seen["comparison"] == [{"currency": "CAD", "metric": "amount", "current": "250.00",
                                   "previous_year": "100.00", "change": "150.00",
                                   "change_pct": "150.0"}]
    assert seen["previous_year"]["resolved_period"] == {"from": "2098-03-01", "to": "2098-03-31"}
