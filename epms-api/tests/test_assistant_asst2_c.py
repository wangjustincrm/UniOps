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
from app.services import assistant_llm, controlled_query as cq, next_step, period_words
from tests.test_assistant_iss_batch4 import _seed_chain, _stub, _uid
from app.core.plant_time import fmt_date

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
    assert answer.rstrip().endswith(f"查询期间：{fmt_date(frm)} – {fmt_date(to)}。")
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
    assert r.json()["answer"].endswith(f"Period used: {fmt_date(lf)} – {fmt_date(lt)}.")


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
    assert f"{fmt_date(first)} – {fmt_date(last)}" in body["answer"]
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
    # The on-screen format, copied from a page or an answer (UI-DATE-002).
    assert period_words.explicit_days("2026-NOV-01 到 2026-NOV-11 付了多少", TODAY) == [
        ("2026-NOV-01", date(2026, 11, 1)), ("2026-NOV-11", date(2026, 11, 11))]
    assert period_words.explicit_days("paid on 2026-Nov-01?", TODAY) == [("2026-Nov-01", date(2026, 11, 1))]
    assert period_words.explicit_days("2026-FEB-30", TODAY) == []
    assert period_words._WRITTEN_DATE.search("2026-NOV-01")
    # Typed without spaces, as Chinese questions usually are.
    assert period_words.explicit_days("从2026-NOV-01到2026-NOV-11付了多少", TODAY) == [
        ("2026-NOV-01", date(2026, 11, 1)), ("2026-NOV-11", date(2026, 11, 11))]
    for q in ("从2026-11-01到2026-11-11付了多少", "Q3付了多少", "nov的发票", "2026-NOV-01的付款"):
        assert period_words._WRITTEN_DATE.search(q), q
    assert not period_words._WRITTEN_DATE.search("novel decimal")
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
        assert f"{local_today().year}-NOV-10 – {local_today().year}-NOV-10" in body["answer"], app


# ── Next steps: the flow (ISS-0247, ISS-0248, ISS-0250) ──────────────────────

def _defined(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_step_is_a_task_the_system_raises():
    from app.crud.task import _TASK_DEDICATED_HANDLING, _TASK_NO_STATUS_INVARIANT, TASK_LIVENESS
    from app.services.guide_view import TASK_TYPE_LABELS
    known = set(TASK_LIVENESS) | set(_TASK_DEDICATED_HANDLING) | set(_TASK_NO_STATUS_INVARIANT)
    for step in next_step.FLOW:
        for task_type in step.task_types:
            assert task_type in known and task_type in TASK_TYPE_LABELS, (step.key, task_type)
    keys = [s.key for s in next_step.FLOW]
    assert keys.index("create_pa") < keys.index("pa_approval") < keys.index("process_pa") \
        < keys.index("remittance")
    # Who gets them, as the code assigns them (PRD §3.7.1).
    invoices = _defined(EPMS_API / "app/api/v1/invoices.py")
    assert 'assigned_role, assigned_user_id = "erp_pa_officer", None' in invoices
    assert 'assigned_role, assigned_user_id = "requester", pr.created_by' in invoices
    engine = REPO / "approval-api/app/crud/engine.py"
    if engine.exists():
        src = _defined(engine)
        pr_part = src[src.index("async def _post_approve_pr"):][:600]
        pa_part = src[src.index("async def _post_approve_pa("):][:900]
        assert 'type="create_po"' in pr_part and 'assigned_role="procurement_officer"' in pr_part
        assert 'type="process_pa"' in pa_part and 'assigned_role="payment_officer"' in pa_part


async def test_after_matching_the_requester_creates_the_pa(admin_client, monkeypatch):
    """Twice: "the PA goes into approval", with no one creating it. The planner
    here picks the PA approval chain; the answer is still the flow."""
    calls = _stub(monkeypatch, plans=[{"kind": "process", "doc_type": "pa"}])
    r = await admin_client.post(CHAT, json={"message": "发票已经匹配好了，下一步是什么？谁来做？"})
    body = r.json()
    assert body["kind"] == "next_steps" and not calls["guide"]
    assert body["steps"][:2] == ["create_pa", "pa_approval"]
    answer = body["answer"]
    assert "PR 申请人" in answer and "ERP PA Officer" in answer
    assert answer.index("建付款申请") < answer.index("审批付款申请")


async def test_after_pr_approval_no_task_table_and_no_more_approval(admin_client, monkeypatch):
    """One answer was a table of 379 tasks, another "your PR now needs these
    approvals". The planner here reaches for the task list."""
    _stub(monkeypatch, plans=[{"kind": "next"}])
    r = await admin_client.post(CHAT, json={"message": "我的请购单批准了，接下来会发生什么？我还要做什么？"})
    body = r.json()
    assert body["kind"] == "next_steps" and "tasks" not in body
    answer = body["answer"]
    assert answer.startswith("PR 的审批已经结束，不需要再审批")
    assert body["steps"][0] == "create_po" and "采购员" in answer
    assert "货到之前，申请人不需要做什么" in answer
    assert "（你）：收货" in answer and "（你）：建付款申请" in answer


async def test_the_flow_after_payment_is_the_remittance_advice(admin_client, monkeypatch):
    """Called "not part of the system", or nothing at all after payment."""
    _stub(monkeypatch, plans=[{"kind": "rules", "areas": ["pa"]}])
    r = await admin_client.post(CHAT, json={
        "message": "付款申请已经批完了，什么时候付款？谁来付？付完以后还有什么步骤？"})
    body = r.json()
    assert body["steps"] == ["process_pa", "remittance"]
    assert "Payment Officer" in body["answer"] and "在系统里给供应商发汇款通知邮件" in body["answer"]
    assert "系统不设定付款日期" in body["answer"]
    # English, the same steps.
    _stub(monkeypatch, plans=[{"kind": "next"}])
    r = await admin_client.post(CHAT, json={"message": "The invoice is matched. What happens next?"})
    assert r.json()["steps"][0] == "create_pa"
    # The rule card says the same: sent from the system, not "by hand".
    from app.services import rule_registry
    card = next(c for a, c in rule_registry.all_cards() if c["id"] == "remittance_advice")
    assert "from the system" in card["answer"] and "by hand" not in card["answer"]


def test_what_is_not_a_next_step_question_is_left_alone():
    for message in ("我的请购单提交不了，下一步怎么办？", "PR-20261023-0002 是谁批的？",
                    "PA 批准以后平均多久付款？", "这个月有多少张发票？"):
        assert next_step.route(message, None) is None, message
    assert next_step.route("这个月的进度怎么样？", ("pr", "PR-1")) is None
    assert next_step.route("这张单下一步谁做？", ("po", "PO-1")) == ("chain", ("po", "PO-1"))


# ── Next steps: one document (ISS-0030) ──────────────────────────────────────

async def _add_task(test_engine, *, type_, doc_type, doc_id, number, role):
    from app.models.task import Task
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(Task(type=type_, priority="normal", document_type=doc_type, document_id=doc_id,
                    document_number=number, assigned_role=role, title="t"))
        await db.commit()


async def test_where_a_pr_stands_follows_the_chain_to_the_pa(test_engine, admin_client,
                                                            monkeypatch):
    """"PR-20261023-0002 现在到哪一步了？下一步该谁做什么？": "已获得所有必需批准，
    无需进一步操作" — its PA was approved and waiting for the Payment Officer.
    The planner here picks the PR's approval history, and the narrator says
    nothing more is needed."""
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    await _add_task(test_engine, type_="process_pa", doc_type="pa", doc_id=c.pa.id,
                    number=c.pa.pa_number, role="payment_officer")
    calls = _stub(monkeypatch, plans=[{"kind": "workflow", "doc_type": "pr",
                                       "doc_number": c.pr.number}],
                  narration="该请购单已获得所有必需批准，无需进一步操作。")
    r = await admin_client.post(CHAT, json={"message": f"{c.pr.number} 现在到哪一步了？下一步该谁做什么？"})
    body = r.json()
    assert body["kind"] == "document_chain" and not calls["workflow"]
    answer = body["answer"]
    assert (f"下一步：{c.pa.pa_number} 的「Process Payment Application」——由 Payment Officer"
            in answer)
    assert "汇款通知" in answer
    assert calls["chain"][0]["chain"]["next_step_added_below"].startswith("Where it stands")
    assert [t["type"] for t in body["next_tasks"]] == ["process_pa"]


async def test_with_no_open_task_the_statuses_say_what_comes_next(test_engine, admin_client,
                                                                  monkeypatch):
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    # The planner ran a status query on the PR.
    _stub(monkeypatch, plans=[{"kind": "query", "query": {
        "entity": "purchase_request", "select": ["status"],
        "where": [{"field": "number", "op": "eq", "value": c.pr.number, "because": "x"}]}}])
    r = await admin_client.post(CHAT, json={"message": f"{c.pr.number} 下一步该谁做什么？"})
    answer = r.json()["answer"]
    assert f"{c.pa.pa_number} 已批准。下一步：付款专员（Payment Officer" in answer
    # The PO is partly received, so more receipts may follow.
    assert f"{c.po.number} 只收了部分货" in answer
    # In English.
    _stub(monkeypatch, plans=[{"kind": "workflow", "doc_type": "pa", "doc_number": c.pa.pa_number}])
    r = await admin_client.post(CHAT, json={"message": f"What is the next step for {c.pa.pa_number}?"})
    assert f"{c.pa.pa_number} is approved. Next: the Payment Officer pool" in r.json()["answer"]


async def test_a_new_pr_in_approval_is_with_its_approver(test_engine, admin_client, monkeypatch):
    from app.models.pr import PurchaseRequest
    uid = _uid(admin_client)
    tag = uuid.uuid4().hex[:6]
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pr = PurchaseRequest(number=f"PR-N{tag}", title="t", type=2, status="approved",
                             amount=Decimal("1"), created_by=uid)
        db.add(pr)
        await db.commit()
    _stub(monkeypatch, plans=[{"kind": "next"}])
    r = await admin_client.post(CHAT, json={"message": f"PR-N{tag} 现在到哪一步了？"})
    answer = r.json()["answer"]
    assert f"PR-N{tag} 已批准，还没有采购订单。下一步：采购员（Procurement Officer）" in answer
