"""Assistant fixes, second round, group C: dates and periods, and "what happens
next", decided in code (ISS-0020, ISS-0035, ISS-0048, ISS-0030, ISS-0247,
ISS-0248, ISS-0250).

Every test here stubs the model and makes it NOT cooperate — the planner picks
the wrong route or writes its own dates, the narrator leaves the key sentence
out or says the opposite — and checks the answer is still right.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.delegation import local_today
from app.core.ontology import REGISTRY
from app.services import assistant_llm, controlled_query as cq, period_words
from tests.test_assistant_iss_batch4 import _stub, _uid

CHAT = "/api/v1/assistant/chat"
EPMS_API = Path(__file__).resolve().parents[1]
REPO = EPMS_API.parent

PR = REGISTRY["purchase_request"]
INVOICE = REGISTRY["invoice"]
TODAY = date(2026, 10, 23)  # a Friday, the day of Cassie's retest


def _enforce(query, message, entity=PR, carried=None, today=TODAY):
    return period_words.enforce(query, message, entity, today, carried=carried)[0]


# ── ISS-0020: the same calendar word, the same dates ─────────────────────────

def test_this_month_is_the_calendar_month_whatever_the_planner_wrote():
    """"本月一共提交了多少张 PR？" gave 6 (status = submitted) and then 153
    (submitted this month). Each way the planner wrote it, the same query runs."""
    message = "本月一共提交了多少张 PR？"
    wanted = {"calendar": "this_month", "field": "submitted_at"}
    for planned in (
        {"entity": "purchase_request", "metrics": ["count"],
         "where": [{"field": "status", "op": "eq", "value": "submitted", "because": "提交了"}]},
        {"entity": "purchase_request", "metrics": ["count"], "period": {"last_n_months": 1}},
        {"entity": "purchase_request", "metrics": ["count"],
         "period": {"from": "2026-09-23", "to": "2026-10-23"}},
        {"entity": "purchase_request", "metrics": ["count"],
         "period": {"calendar": "this_month"}},
        {"entity": "purchase_request", "metrics": ["count"],
         "where": [{"field": "submitted_at", "op": "gte", "value": "2026-10-01", "because": "本月"}]},
    ):
        out = _enforce(planned, message)
        assert out["period"] == wanted, planned
        assert not out.get("where"), planned
    # The same for every calendar word, Chinese or English.
    for message, name in (("上个月一共下了多少张 PO？", "last_month"), ("本季度的采购额是多少？", "this_quarter"),
                          ("今年所有采购订单的金额合计是多少？", "this_year"),
                          ("How much was paid last week?", "last_week")):
        out = _enforce({"entity": "purchase_order", "metrics": ["amount"],
                        "period": {"last_n_months": 12}}, message, REGISTRY["purchase_order"])
        assert out["period"]["calendar"] == name, message


def test_a_rolling_window_or_two_periods_are_left_as_planned():
    planned = {"entity": "purchase_order", "metrics": ["count"], "period": {"last_n_months": 3}}
    assert _enforce(planned, "最近 3 个月下了多少张 PO？", REGISTRY["purchase_order"]) == planned
    planned = {"entity": "purchase_order", "metrics": ["count"], "group_by": ["created_at:month"]}
    assert _enforce(planned, "本月和上个月各下了多少张 PO？", REGISTRY["purchase_order"]) == planned
    # Dates of the person's own next to the calendar word are theirs to keep.
    planned = {"entity": "invoice", "select": ["internal_ref"],
               "where": [{"field": "due_date", "op": "lt", "value": "2026-12-01", "because": "x"}],
               "period": {"field": "created_at", "calendar": "this_month"}}
    assert _enforce(planned, "本月上传的、到期日在 12 月之前的发票有哪些？", INVOICE) == planned


def test_same_period_last_year_is_one_year_earlier():
    """Asked alone, "去年同期的采购额是多少？" returned this year's total."""
    po = REGISTRY["purchase_order"]
    alone = _enforce({"entity": "purchase_order", "metrics": ["amount"],
                      "period": {"calendar": "this_year"}}, "去年同期的采购额是多少？", po)
    assert alone["period"] == {"calendar": "this_year", "shift_years": -1}
    assert cq.calendar_range("this_year", -1, today=TODAY) == (date(2025, 1, 1), date(2025, 10, 23))
    # After "本季度的采购额是多少？" it is the same quarter last year.
    follow = _enforce({"entity": "purchase_order", "metrics": ["amount"],
                       "period": {"last_n_months": 12}}, "去年同期的呢？", po, carried="this_quarter")
    assert follow["period"] == {"calendar": "this_quarter", "shift_years": -1}
    # A comparison runs both periods instead.
    both = _enforce({"entity": "purchase_order", "metrics": ["amount"],
                     "period": {"calendar": "this_quarter", "shift_years": -1}},
                    "本季度的采购额比去年同期增长了多少？", po)
    assert both["period"] == {"calendar": "this_quarter", "compare_previous_year": True}


def test_this_week_counts_prs_submitted_and_approved_since():
    """"本周提交了哪些请购单？" listed 6 PRs still in approval and left out
    PR-20261021-0001…0007, submitted that week and approved since."""
    planned = {"entity": "purchase_request", "select": ["number", "status"],
               "where": [{"field": "status", "op": "in", "value": ["submitted", "in_review"],
                          "because": "本周提交了哪些请购单"}]}
    out = _enforce(planned, "本周提交了哪些请购单？")
    assert out["period"] == {"calendar": "this_week", "field": "submitted_at"}
    assert not out.get("where")
    # Asked about the state as well, the status filter stays.
    kept = _enforce(planned, "本周提交的、还在审批中的请购单有哪些？")
    assert kept["where"] == planned["where"]
    # Monday to today, and last week is the Monday-to-Sunday before it.
    assert cq.calendar_range("this_week", today=TODAY) == (date(2026, 10, 19), TODAY)
    assert cq.calendar_range("last_week", today=TODAY) == (date(2026, 10, 12), date(2026, 10, 18))


async def test_prs_submitted_this_month_are_counted_by_submission(test_engine, admin_client,
                                                                 monkeypatch):
    """The whole loop with a planner that filters on status = submitted."""
    from app.models.pr import PurchaseRequest
    uid = _uid(admin_client)
    tag = uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        for i, (status, when) in enumerate((("approved", now), ("submitted", now),
                                            ("approved", now - timedelta(days=70)))):
            db.add(PurchaseRequest(number=f"PR-C{tag}-{i}", title="t", type=2, status=status,
                                   amount=Decimal("1"), created_by=uid, submitted_at=when))
        await db.commit()
    _stub(monkeypatch, plans=[{"kind": "query", "query": {
        "entity": "purchase_request", "metrics": ["count"],
        "where": [{"field": "number", "op": "like", "value": f"PR-C{tag}", "because": "test"},
                  {"field": "status", "op": "eq", "value": "submitted", "because": "提交了"}]}}])
    r = await admin_client.post(CHAT, json={"message": "本月一共提交了多少张 PR？"})
    body = r.json()
    assert body["query"]["period"] == {"calendar": "this_month", "field": "submitted_at"}
    assert [c["field"] for c in body["query"]["where"]] == ["number"]


# ── ISS-0020 #1: every answer over a period says which dates ─────────────────

async def test_every_period_answer_ends_with_its_dates(admin_client, monkeypatch):
    """The narrator was never given resolved_period; it wrote no dates. It still
    writes none here — the sentence is the code's."""
    calls = _stub(monkeypatch, plans=[{"kind": "query", "query": {
        "entity": "purchase_order", "metrics": ["count"], "period": {"calendar": "this_month"}}}],
        narration="本月共有 12 张采购订单。")
    r = await admin_client.post(CHAT, json={"message": "本月一共下了多少张 PO？"})
    answer = r.json()["answer"]
    frm, to = cq.calendar_range("this_month")
    assert answer.startswith("本月共有 12 张采购订单。")
    assert answer.rstrip().endswith(f"查询期间：{frm:%d/%m/%Y} – {to:%d/%m/%Y}。")
    # The narrator had the dates, and was told the line is added for it.
    sent = calls["narrate"][0]["result"]
    assert sent["resolved_period"]["from"] == frm.isoformat()
    assert sent["said_with_this_list"][-1][0] == "period_used"
    # In English, the English sentence.
    _stub(monkeypatch, plans=[{"kind": "query", "query": {
        "entity": "purchase_order", "metrics": ["count"], "period": {"last_n_months": 1}}}],
        narration="12 orders.")
    r = await admin_client.post(CHAT, json={"message": "How many POs were placed last month?"})
    lf, lt = cq.calendar_range("last_month")
    assert r.json()["answer"].endswith(f"Period used: {lf:%d/%m/%Y} – {lt:%d/%m/%Y}.")


def test_the_narrator_payload_carries_the_period():
    src = Path(assistant_llm.__file__).read_text(encoding="utf-8")
    narrate = src[src.index("async def narrate(message"):src.index("_PREFLIGHT_SYSTEM =")]
    assert '"resolved_period"' in narrate and '"comparison"' in narrate


# ── ISS-0035: "due this month", the conditions, one name for the yuan ────────

def test_due_this_month_is_the_whole_month():
    """"本月到期待付款的发票总额" ran as due_date ≤ today with no start (592
    invoices); INV-2026-0617, due 30/10, was left out on 22/10."""
    planned = {"entity": "invoice", "metrics": ["total"],
               "where": [{"field": "status", "op": "not_in", "value": ["paid", "cancelled"],
                          "because": "待付款"},
                         {"field": "due_date", "op": "lte", "value": "2026-10-22",
                          "because": "本月到期"}]}
    out = _enforce(planned, "本月到期待付款的发票总额是多少？", INVOICE)
    assert out["period"] == {"calendar": "this_month", "field": "due_date"}
    assert [c["field"] for c in out["where"]] == ["status"]
    assert cq.period_dates(out["period"], INVOICE) == cq.calendar_range("this_month", whole=True)
    assert INVOICE.fields["due_date"].scheduled
    assert cq.calendar_range("this_month", today=date(2026, 10, 22), whole=True) == (
        date(2026, 10, 1), date(2026, 10, 31))
    # A past event is still the month to today.
    assert not INVOICE.fields["invoice_date"].scheduled


async def test_due_this_month_finds_the_invoice_due_at_month_end(test_engine, admin_client):
    from app.models.invoice import Invoice
    from app.models.vendor import Vendor
    tag = uuid.uuid4().hex[:6]
    first, last = cq.calendar_range("this_month", whole=True)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = Vendor(code=f"V-{tag}", name=f"Due Vendor {tag}", category="Services",
                   contact_name="C", contact_email="c@t.test")
        db.add(v)
        await db.flush()
        for ref, due in ((f"INV-D{tag}-end", last), (f"INV-D{tag}-before", first - timedelta(days=1))):
            db.add(Invoice(internal_ref=ref, vendor_invoice_number=ref, vendor_id=v.id,
                           vendor_name=v.name, amount=Decimal("10"), tax_amount=Decimal("0"),
                           total_amount=Decimal("10"), invoice_date=first, due_date=due,
                           status="matched", line_items=[], uploaded_by=_uid(admin_client)))
        await db.commit()
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "invoice", "select": ["internal_ref"],
        "where": [{"field": "vendor_name", "op": "eq", "value": v.name}],
        "period": {"field": "due_date", "calendar": "this_month"}})
    body = r.json()
    assert [row["internal_ref"] for row in body["rows"]] == [f"INV-D{tag}-end"]
    assert body["resolved_period"]["to"] == last.isoformat()


async def test_the_conditions_of_the_previous_answer_are_read_back(admin_client, monkeypatch):
    """"这个结果用的到期日范围和发票状态条件是什么？" was planned again and the
    previous answer repeated. The planner here re-plans it again; the reply is
    still the conditions."""
    previous = {"entity": "invoice", "metrics": ["total"],
                "where": [{"field": "status", "op": "not_in", "value": ["paid", "cancelled"],
                           "because": "待付款"}],
                "period": {"field": "due_date", "calendar": "this_month"}}
    calls = _stub(monkeypatch, plans=[{"kind": "query", "query": previous}])
    r = await admin_client.post(CHAT, json={
        "message": "这个结果用的到期日范围和发票状态条件是什么？",
        "history": [{"role": "user", "text": "本月到期待付款的发票总额是多少？"},
                    {"role": "assistant", "text": "CAD 1,000.00", "query": previous}]})
    body = r.json()
    first, last = cq.calendar_range("this_month", whole=True)
    assert body["kind"] == "conditions" and not calls["narrate"]
    assert "Payment due date" in body["answer"]
    assert f"{first:%d/%m/%Y} – {last:%d/%m/%Y}" in body["answer"]
    assert "不属于 Paid (paid), Cancelled (cancelled)" in body["answer"] or \
        "不属于 paid, cancelled" in body["answer"]
    # A follow-up that changes the conditions is a new query, not this.
    from app.services import query_conditions
    assert not query_conditions.asks_for_conditions("把条件改成只要已匹配的")


def test_yuan_is_written_one_way():
    from app.api.v1.assistant_chat import one_code_for_yuan
    assert one_code_for_yuan("RMB 1,000.00；CNY 2.00；RMB.") == "CNY 1,000.00；CNY 2.00；CNY."
    assert one_code_for_yuan("BRMBX") == "BRMBX"


async def test_the_reply_writes_cny_whatever_the_narrator_wrote(admin_client, monkeypatch):
    _stub(monkeypatch, plans=[{"kind": "query", "query": {
        "entity": "invoice", "metrics": ["total"]}}], narration="合计：RMB 500.00，CAD 10.00")
    r = await admin_client.post(CHAT, json={"message": "发票总额是多少？"})
    assert "RMB" not in r.json()["answer"] and "CNY 500.00" in r.json()["answer"]


# ── ISS-0048: one reading of "10/11" on every page ───────────────────────────

def test_a_day_without_a_year_is_this_year_on_every_page():
    days = period_words.explicit_days("10/11 那天收了哪些货？", TODAY)
    assert days == [("10/11", date(2026, 11, 10))]
    assert period_words.explicit_days("11月10日那天收了哪些货？", TODAY) == [("11月10日", date(2026, 11, 10))]
    assert period_words.explicit_days("2025年11月10日", TODAY) == [("2025年11月10日", date(2025, 11, 10))]
    assert period_words.explicit_days("10/11/2025", TODAY) == [("10/11/2025", date(2025, 11, 10))]
    assert period_words.explicit_days("2026-11-10", TODAY) == []
    # The planner on Portal wrote 2025: the query runs on the day as read.
    gr = REGISTRY["goods_receipt"]
    out = _enforce({"entity": "goods_receipt", "select": ["number"],
                    "period": {"from": "2025-11-10", "to": "2025-11-10"}},
                   "10/11 那天收了哪些货？", gr)
    assert out["period"] == {"from": "2026-11-10", "to": "2026-11-10"}
    # And the planner is told the day, the same on every page.
    note = assistant_llm._today_note("10/11 那天收了哪些货？")
    assert f'"10/11" is {local_today().year}-11-10' in note


async def test_the_day_is_the_same_from_portal_and_epms(admin_client, monkeypatch):
    for app in ("portal", "epms"):
        _stub(monkeypatch, plans=[{"kind": "query", "query": {
            "entity": "goods_receipt", "select": ["number"],
            "period": {"from": f"{local_today().year - 1}-11-10",
                       "to": f"{local_today().year - 1}-11-10"}}}], narration="没有收货记录。")
        r = await admin_client.post(CHAT, json={"message": "10/11 那天收了哪些货？",
                                                "context": {"app": app}})
        body = r.json()
        day = f"{local_today().year}-11-10"
        assert body["query"]["period"] == {"from": day, "to": day}, app
        assert f"10/11/{local_today().year} – 10/11/{local_today().year}" in body["answer"], app


