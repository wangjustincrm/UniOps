"""Assistant round 2 (2026-11-10), group A: which route a question takes is
decided by code, not by the model choosing a tool.

Every chat test here scripts a planner and narrators that do NOT cooperate —
a planner that never splits, takes a follow-up for a question about the
conversation, or picks a route with no rule on it; narrators that leave the
rule out or state its opposite — and checks the answer is still right.
ISS-0037, ISS-0069, ISS-0067, ISS-0018, ISS-0022, ISS-0034, ISS-0019, ISS-0050,
ISS-0056 (the "PA not found" point).
"""
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import assistant_llm, question_routes, rule_registry
from tests.test_assistant_iss_batch4 import _conversation, _stub, _uid

CHAT = "/api/v1/assistant/chat"
REPO = Path(__file__).resolve().parents[2]

# What a narrator that does not cooperate writes.
WRONG = "会从第一步重新审批。当前等待审批的审批人可以编辑。未找到这份付款申请。"


def _never_planned(monkeypatch):
    """A planner that must not be asked at all."""
    calls = _stub(monkeypatch, plans=[], narration=WRONG)

    async def boom(*a, **kw):
        raise AssertionError("the planner was asked")
    monkeypatch.setattr(assistant_llm, "plan", boom)
    return calls


# ── Splitting (ISS-0037, ISS-0069, ISS-0019, ISS-0050) ───────────────────────

@pytest.mark.parametrize("message, parts", [
    # ISS-0037
    ("PO-576-2610-01 的状态、PO-535-2609-02 的金额、供应商 Raise The Standard 的联系人分别是什么？",
     ["PO-576-2610-01 的状态是什么？", "PO-535-2609-02 的金额是什么？",
      "供应商 Raise The Standard 的联系人是什么？"]),
    ("总用户数、活跃用户数、部门数、各角色的用户数分别是多少？",
     ["总用户数是多少？", "活跃用户数是多少？", "部门数是多少？", "各角色的用户数是多少？"]),
    # ISS-0069
    ("PO-079-2605-03 是什么状态，INV-2026-0123 金额多少，怎么催审批人，再帮我写一首诗",
     ["PO-079-2605-03 是什么状态", "INV-2026-0123 金额多少", "怎么催审批人", "再帮我写一首诗"]),
    ("PR-20261015-0006 和 PA-20260923-0017 现在是什么状态？",
     ["PR-20261015-0006 现在是什么状态？", "PA-20260923-0017 现在是什么状态？"]),
    ("GR-20261019-0001 是哪天几点收货的？10 月 18 日收了哪些货？",
     ["GR-20261019-0001 是哪天几点收货的？", "10 月 18 日收了哪些货？"]),
    # ISS-0019
    ("怎么在 Teams 里审批？怎么回复邮件来审批？GR 确认超时会升级给谁？",
     ["怎么在 Teams 里审批？", "怎么回复邮件来审批？", "GR 确认超时会升级给谁？"]),
    # ISS-0050
    ("有哪些供应商已经停用？停用后还能下单吗？", ["有哪些供应商已经停用？", "停用后还能下单吗？"]),
    ("1. PO-576-2610-01 的状态 2. 谁在审批", ["PO-576-2610-01 的状态", "谁在审批"]),
    ("PO-576-2610-01 是什么状态；INV-2026-0123 金额多少",
     ["PO-576-2610-01 是什么状态；", "INV-2026-0123 金额多少"]),
])
def test_a_message_that_asks_several_things_is_split_by_code(message, parts):
    assert question_routes.split(message) == parts


@pytest.mark.parametrize("message", [
    "NC 里刚批准的采购订单，多久能在 EPMS 里看到？",
    "假如今天是 10 月 1 号，昨天收了哪些货？",
    "PO-576-2610-01、PO-535-2609-02 的金额分别是多少？",   # one query with `in`
    "PO-535-2609-02 和 PO-576-2610-01 的状态",              # the same kind
    "版本 1.5 的说明是什么",
    "What does the PO number PO-ABC-2604-03 mean?",
    "上周一共给供应商付了多少，按币种分",
    "PR-20261015-0006 was returned; if I resubmit it, does it start again?",
])
def test_one_question_is_not_split(message):
    assert question_routes.split(message) == [message]


async def test_the_planner_need_not_split(admin_client, monkeypatch):
    """A planner that never calls split_question: the three questions of the
    ISS-0037 message are still planned one by one."""
    message = ("PO-576-2610-01 的状态、PO-535-2609-02 的金额、供应商 Raise The Standard "
               "的联系人分别是什么？")
    calls = _stub(monkeypatch, plans=[
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": f"part {i}"}
        for i in (1, 2, 3)])
    body = (await admin_client.post(CHAT, json={"message": message})).json()
    assert body["kind"] == "parts" and len(body["parts"]) == 3
    assert [c["message"] for c in calls["plan"]] == question_routes.split(message)
    assert all(c["context"]["question_part"]["whole"] == message for c in calls["plan"])
    assert not any(c["message"] == message for c in calls["plan"]), "never planned whole"


# ── The conversation itself (ISS-0067) ───────────────────────────────────────

@pytest.mark.parametrize("message, position", [
    ("我在这次对话里问的第一个问题是什么？你当时怎么回答的？", 1),
    ("我问的第 50 个问题是什么？", 50),
    ("我上一个问题是什么？", -1),
    ("我刚才问了什么", -1),
    ("第二个问题你怎么回答的", 2),
    ("What was my first question?", 1),
    ("what did I just ask?", -1),
    # Follow-ups about an answer's data — Cassie's three failures and one pass.
    ("上面这 23 份是按哪个日期筛选的？它们付款了吗？", None),
    ("这里的'上周'是哪几天？是按什么日期算的？", None),
    ("上面这些是按什么日期算的？它们都已经付款了吗？", None),
    ("上面这份采购订单是哪天创建的？", None),
    ("这是我第一个问题，PO-535-2609-02 状态是什么", None),
])
def test_only_explicit_wording_is_about_the_conversation(message, position):
    assert question_routes.recall_position(message) == position


async def test_a_follow_up_after_a_one_line_answer_is_not_recall(admin_client, monkeypatch):
    """The planner takes the follow-up for a question about the conversation
    (as on production, 3 times) — the code does not answer it as one."""
    history = [{"role": "user", "text": "10月16日处理了哪些付款申请？"},
               {"role": "assistant", "text": "10月16日没有处理任何付款申请。"}]
    calls = _stub(monkeypatch, plans=[
        {"kind": "recall", "position": 1},                    # the model's misroute
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "按付款日期。"}])
    body = (await admin_client.post(CHAT, json={
        "message": "上面这些是按什么日期算的？", "history": history})).json()
    assert body["kind"] != "recall"
    assert "你在这次对话里问的" not in body["answer"]
    assert "recall_conversation is not available" in calls["plan"][1]["retry_error"]


async def test_the_conversation_question_never_reaches_the_planner(admin_client, monkeypatch):
    _never_planned(monkeypatch)
    body = (await admin_client.post(CHAT, json={
        "message": "我在这次对话里问的第一个问题是什么？你当时怎么回答的？",
        "history": _conversation(20)})).json()
    assert body["kind"] == "recall" and "「question 1」" in body["answer"]


# ── Rules the code answers (ISS-0018, ISS-0069, ISS-0019, ISS-0050, ISS-0022) ─

def test_the_resubmit_answer_is_the_engines_rule():
    """The code-written answer (approval_engine.resubmitted_after_return) says
    what approval-api's own rule says — and that rule is held by the engine
    tests the card names."""
    card = rule_registry.card("resubmitted_after_return")
    en = " ".join(card["says"]["en"].split())
    assert "at the step that returned it" in en and "does not start again" in en
    assert "从退回它的那一步继续审批" in card["says"]["zh"].replace("\n", "").replace(" ", "")
    engine = REPO / "approval-api" / "app" / "crud" / "engine_rules.py"
    if not engine.exists():
        pytest.skip("approval-api is not mounted")
    src = engine.read_text(encoding="utf-8")
    flat = " ".join(src.replace('"\n', " ").replace('            "', "").split())
    assert "returned_document_resumes_at_the_returning_step" in src
    assert "approval resumes at the step that returned it" in flat
    assert "A recalled document is a draft again and always starts from the first step" in flat


@pytest.mark.parametrize("message, must, must_not", [
    ("请购单被退回后重新提交，会从第一步重新审批吗？", "从退回它的那一步继续审批", "会从第一步重新审批"),
    ("When a returned PR is resubmitted, does approval start again from the first step?",
     "at the step that returned it", "会从第一步"),
    ("已经提交的请购单想修改怎么办？", "审批人不能编辑", "审批人可以编辑"),
    ("录入手工贷项需要什么？", "贷项单参考号", "未找到"),
])
async def test_a_rule_question_is_answered_by_code(admin_client, monkeypatch, message, must,
                                                   must_not):
    _never_planned(monkeypatch)
    body = (await admin_client.post(CHAT, json={"message": message})).json()
    assert body["kind"] == "rules" and body["pinned_rules"]
    assert must in body["answer"] and must_not not in body["answer"]


async def test_how_to_chase_an_approver_is_the_same_every_time(admin_client, monkeypatch):
    """ISS-0069: "怎么催审批人" inside the four-part message, 1 answer in 4 right."""
    message = "PO-079-2605-03 是什么状态，INV-2026-0123 金额多少，怎么催审批人，再帮我写一首诗"
    calls = _stub(monkeypatch, narration="使用系统内的提醒功能（如果有），或直接联系审批人。", plans=[
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "PO"},
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "INV"}])
    body = (await admin_client.post(CHAT, json={"message": message})).json()
    assert len(calls["plan"]) == 2, "the reminder part is not planned"
    third = body["parts"][2]
    assert third["question"] == "怎么催审批人"
    assert "Send reminder" in third["answer"] and "24 小时" in third["answer"]
    assert "当前步骤下方" in third["answer"] and "付款申请（PA）没有" in third["answer"]
    assert "如果有" not in third["answer"]
    assert body["not_looked_up"] == ["再帮我写一首诗"]


async def test_two_questions_one_rule_is_one_answer(admin_client, monkeypatch):
    _never_planned(monkeypatch)
    body = (await admin_client.post(CHAT, json={
        "message": "我能连续催审批人两次吗？PA 能催办吗？"})).json()
    assert body["kind"] == "rules" and body["answer"].count("Send reminder（发送提醒）") == 1


async def test_gr_escalation_asked_with_other_questions(admin_client, monkeypatch):
    """ISS-0019: alone it was answered, together with Teams and email it was
    "no information"."""
    calls = _stub(monkeypatch, narration="我没有关于这部分的信息。", plans=[
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "teams"},
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "email"}])
    body = (await admin_client.post(CHAT, json={
        "message": "怎么在 Teams 里审批？怎么回复邮件来审批？GR 确认超时会升级给谁？"})).json()
    assert len(calls["plan"]) == 2
    gr = body["parts"][2]["answer"]
    assert "GR Notification SLA" in gr and "Service GR SLA" in gr and "部门经理" in gr
    assert "没有关于" not in gr and "{" not in gr


async def test_inactive_vendor_rule_asked_with_the_list(admin_client, monkeypatch):
    """ISS-0050: the list was right, the rule part "not covered"."""
    calls = _stub(monkeypatch, narration="查询结果中没有包含这方面的信息。", plans=[
        {"kind": "query", "query": {"entity": "vendor", "select": ["name"], "where": []}}])
    body = (await admin_client.post(CHAT, json={
        "message": "有哪些供应商已经停用？停用后还能下单吗？"})).json()
    assert [c["message"] for c in calls["plan"]] == ["有哪些供应商已经停用？"]
    rule = body["parts"][1]["answer"]
    assert "选不到" in rule and "没有包含" not in rule


async def test_the_edit_rule_card_no_longer_reads_as_approvers_editing():
    card = rule_registry.card("who_can_edit_a_pr")
    answer = " ".join(card["answer"].split())
    assert "Approvers cannot edit a PR" in answer
    assert "unlike a returned one, which resumes at the step that returned it" in answer


async def test_a_named_document_keeps_its_own_answer_under_the_rule(admin_client, monkeypatch):
    calls = _stub(monkeypatch, narration="It is waiting on step 2.", plans=[
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "which?"}])
    body = (await admin_client.post(CHAT, json={
        "message": "PR-20261015-0006 was returned; if I resubmit it, does it start again?"})).json()
    assert len(calls["plan"]) == 1
    assert body["answer"].startswith("A returned document")
    assert "which?" in body["answer"]


# ── One named document, from its data (ISS-0022, ISS-0034) ───────────────────

def _digits() -> str:
    return str(uuid.uuid4().int)[:8]


async def _seed(test_engine, uid, *, invoice_status="paid", pa_status="approved",
                invoice_date=date(2026, 9, 15), due_date=date(2026, 10, 15), with_pa=True):
    from app.models.invoice import Invoice
    from app.models.pa import PaymentApplication
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    from app.models.vendor import Vendor

    d = _digits()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = Vendor(code=f"V{d}", name=f"Route Vendor {d}", category="Services",
                   contact_name="C", contact_email="c@t.test")
        db.add(v)
        await db.flush()
        pr = PurchaseRequest(number=f"PR-2099{d[:4]}-{d[4:8]}", title="r", type=2,
                             status="approved", amount=Decimal("18.14"), vendor_name=v.name,
                             created_by=uid)
        db.add(pr)
        await db.flush()
        po = PurchaseOrder(number=f"PO-{d[:3]}-2610-{d[3:5]}", title="r", type=2, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("18.14"), total=Decimal("18.14"),
                           status="issued", created_by=uid, pr_id=pr.id, pr_number=pr.number)
        db.add(po)
        await db.flush()
        inv = Invoice(internal_ref=f"INV-2099-{d[:5]}", vendor_invoice_number=f"V{d}",
                      vendor_id=v.id, vendor_name=v.name, amount=Decimal("18.14"),
                      tax_amount=Decimal("0"), total_amount=Decimal("18.14"),
                      invoice_date=invoice_date, due_date=due_date, status=invoice_status,
                      line_items=[], uploaded_by=uid, po_id=po.id, po_number=po.number,
                      match_route="po")
        db.add(inv)
        await db.flush()
        pa = None
        if with_pa:
            pa = PaymentApplication(pa_number=f"PA-2099{d[4:8]}-{d[:4]}", title="p",
                                    po_id=po.id, po_number=po.number, vendor_id=v.id,
                                    vendor_name=v.name, invoice_ids=[str(inv.id)],
                                    subtotal=Decimal("18.14"), payment_amount=Decimal("18.14"),
                                    status=pa_status, created_by=uid)
            db.add(pa)
        await db.commit()
        return SimpleNamespace(pr=pr, po=po, inv=inv, pa=pa)


async def test_can_this_invoice_be_unmatched(test_engine, admin_client, monkeypatch):
    """ISS-0022 #1: INV-2026-0631, Paid, on PA-20261013-0003."""
    s = await _seed(test_engine, _uid(admin_client))
    _never_planned(monkeypatch)
    body = (await admin_client.post(CHAT, json={
        "message": f"发票 {s.inv.internal_ref} 能取消匹配吗？"})).json()
    assert body["answer"].startswith(f"不能。{s.inv.internal_ref} 的状态是 Paid")
    assert s.pa.pa_number in body["answer"] and "规则：" in body["answer"]
    # On the invoice's own page, without its number.
    body = (await admin_client.post(CHAT, json={
        "message": "这张发票能取消匹配吗？",
        "context": {"app": "epms", "doc_type": "invoice", "doc_id": str(s.inv.id)}})).json()
    assert body["answer"].startswith("不能。") and s.pa.pa_number in body["answer"]
    # English.
    body = (await admin_client.post(CHAT, json={
        "message": f"Can {s.inv.internal_ref} be unmatched?"})).json()
    assert body["answer"].startswith("No.") and s.pa.pa_number in body["answer"]


async def test_an_invoice_not_in_payment_can_be_unmatched(test_engine, admin_client, monkeypatch):
    s = await _seed(test_engine, _uid(admin_client), invoice_status="matched", with_pa=False)
    _never_planned(monkeypatch)
    body = (await admin_client.post(CHAT, json={
        "message": f"{s.inv.internal_ref} 能取消匹配吗？"})).json()
    assert body["answer"].startswith("可以。")
    s2 = await _seed(test_engine, _uid(admin_client), invoice_status="matched",
                     pa_status="submitted")
    body = (await admin_client.post(CHAT, json={
        "message": f"{s2.inv.internal_ref} 能取消匹配吗？"})).json()
    assert body["answer"].startswith("不能。") and s2.pa.pa_number in body["answer"]


async def test_someone_who_cannot_see_the_invoice_is_told_nothing(
        test_engine, admin_client, requester_client, monkeypatch):
    s = await _seed(test_engine, _uid(admin_client))
    _never_planned(monkeypatch)
    body = (await requester_client.post(CHAT, json={
        "message": f"{s.inv.internal_ref} 能取消匹配吗？"})).json()
    assert "找不到" in body["answer"] and s.pa.pa_number not in body["answer"]


async def test_how_was_this_invoices_due_date_set(test_engine, admin_client, monkeypatch):
    """ISS-0034: "INV-2026-0658 的到期日是怎么算的？" — only "+30 days"."""
    s = await _seed(test_engine, _uid(admin_client), invoice_status="matched", with_pa=False)
    _never_planned(monkeypatch)
    body = (await admin_client.post(CHAT, json={
        "message": f"发票 {s.inv.internal_ref} 的到期日是怎么算的？"})).json()
    text = body["answer"]
    assert "发票日期 2026-09-15，到期日 2026-10-15，相差 30 天" in text
    assert "NET n" in text and "默认发票日期加 30 天" in text
    assert "系统不记录这张发票的到期日是按哪一种方式得出的" in text
    s45 = await _seed(test_engine, _uid(admin_client), invoice_status="matched", with_pa=False,
                      invoice_date=date(2026, 9, 1), due_date=date(2026, 10, 16))
    body = (await admin_client.post(CHAT, json={
        "message": f"How is the due date of {s45.inv.internal_ref} calculated?"})).json()
    assert "45 days apart is not the 30-day default" in body["answer"]


@pytest.mark.parametrize("message, must", [
    ("What does the PO number PO-ABC-2604-03 mean?",
     ["ABC is the vendor's code", "April 2026", "3rd PO raised for that vendor"]),
    ("PO-ABC-2604-03 这个编号是什么意思？", ["ABC 是供应商代码", "2026 年 4 月", "第 3 张"]),
    ("What does PR-20261015-0003 mean?", ["15 October 2026", "3rd of that day"]),
    ("PO-535-2609-02-2 是什么意思？", ["末尾的 -2", "NC"]),
])
async def test_what_a_number_means(admin_client, monkeypatch, message, must):
    """ISS-0022 #2: the English question got the fixed list twice."""
    _never_planned(monkeypatch)
    body = (await admin_client.post(CHAT, json={"message": message})).json()
    for m in must:
        assert m in body["answer"], m


def test_a_status_question_is_not_a_number_question():
    from app.services import code_answers
    assert code_answers.asks_number_meaning("PO-079-2605-03 的状态 Issued 是什么意思？") is None


# ── A named document the query did not return (ISS-0069, ISS-0056) ──────────

async def test_a_pa_looked_up_in_the_wrong_place_is_not_called_missing(
        test_engine, admin_client, monkeypatch):
    """PA-20260923-0017 asked with a PR: looked up among purchase requests,
    "未找到"; PA-20261016-0005 "after credits": looked up among payments made."""
    s = await _seed(test_engine, _uid(admin_client), invoice_status="matched")
    _stub(monkeypatch, narration="未找到这份付款申请。", plans=[{"kind": "query", "query": {
        "entity": "purchase_request", "select": ["number", "status"],
        "where": [{"field": "number", "op": "in", "value": [s.pr.number, s.pa.pa_number],
                   "because": "asked"}]}}])
    body = (await admin_client.post(CHAT, json={
        "message": f"{s.pa.pa_number} 现在是什么状态"})).json()
    assert body["answer"].startswith("未找到这份付款申请。")
    assert f"{s.pa.pa_number} 是存在的：付款申请，状态 Approved，已批准、尚未付款，金额 18.14" \
        in body["answer"]
    # A number asked about at its own home that finds nothing stays unanswered
    # here (not found, or not visible) — nothing is added.
    _stub(monkeypatch, narration="Not found.", plans=[{"kind": "query", "query": {
        "entity": "payment_application", "select": ["pa_number"],
        "where": [{"field": "pa_number", "op": "eq", "value": "PA-20990101-0001",
                   "because": "asked"}]}}])
    body = (await admin_client.post(CHAT, json={"message": "PA-20990101-0001 status?"})).json()
    assert body["answer"] == "Not found."


async def test_a_pa_someone_cannot_see_is_not_revealed(
        test_engine, admin_client, requester_client, monkeypatch):
    s = await _seed(test_engine, _uid(admin_client))
    _stub(monkeypatch, narration="Not found.", plans=[{"kind": "query", "query": {
        "entity": "purchase_request", "select": ["number"],
        "where": [{"field": "number", "op": "eq", "value": s.pa.pa_number, "because": "x"}]}}])
    body = (await requester_client.post(CHAT, json={"message": f"{s.pa.pa_number} 状态？"})).json()
    assert "是存在的" not in body["answer"]
