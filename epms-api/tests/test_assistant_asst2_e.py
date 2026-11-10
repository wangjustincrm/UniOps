"""Assistant, second round — ISS-0102: "which questions have I marked?" is
answered from the Marked list, in code, whatever the planner picks; and no raw
task-type key (approve_exp) reaches the reply.

Every chat test stubs the model to NOT cooperate: the planner picks the task
inbox (what it did three times on production), a query, a refusal, or is
unreachable, and the narrator prints the raw type key.
"""
import datetime as dt

import pytest

from app.services import assistant_llm, guide_view, marked_view, rule_registry

pytestmark = pytest.mark.asyncio
CHAT = "/api/v1/assistant/chat"
SAVED = "/api/v1/assistant/saved"

_ASKED = [
    "你可以查询到我mark问题有哪些吗？",          # the tester's question, verbatim
    "我收藏了哪些问题？",
    "我标记的问题有哪些",
    "Which questions have I marked?",
    "show my saved questions",
    "What's in my Marked list?",
]
_NOT_ASKED = [
    "how do I mark a task as done",
    "Mark PO-079-2605-03 as received",
    "PR-2026-0012 有什么问题？",
    "我现在有几条待办？",
    "any marketing questions this month?",
    "10 月 16 日处理了哪些付款申请？",
]


async def test_the_question_is_recognised_in_code():
    for q in _ASKED:
        assert marked_view.asks_for_marked(q), q
    for q in _NOT_ASKED:
        assert not marked_view.asks_for_marked(q), q


def _uncooperative(monkeypatch, kind: str = "next"):
    """The planner picks `kind`; any narrator records that it was called."""
    called: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        if kind == "down":
            raise assistant_llm.LlmUnavailable("quota")
        if kind == "query":
            return {"kind": "query", "query": {"entity": "purchase_order", "metrics": ["count"]}}
        if kind == "cannot":
            return {"kind": "cannot", "reason": "not_in_schema",
                    "explanation": "我看不到收藏的问题。"}
        return {"kind": kind}

    async def fake_narrator(*a, **k):
        called.append(a)
        return {"text": "您在 EPMS 任务收件箱中有 406 个待处理任务", "usage": {}}

    async def fake_tasks(role, user_id):
        return []

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_narrator)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrator)
    monkeypatch.setattr(guide_view, "my_tasks", fake_tasks)
    return called


async def test_marked_questions_are_listed_whatever_the_planner_picks(admin_client, monkeypatch):
    for q in ("Type 1 POs not fully received", "上个月每个供应商的发票总额"):
        assert (await admin_client.post(SAVED, json={"question": q})).status_code == 200

    for kind in ("next", "query", "cannot", "dashboard", "down"):
        called = _uncooperative(monkeypatch, kind)
        r = await admin_client.post(CHAT, json={"message": "你可以查询到我mark问题有哪些吗？"})
        assert r.status_code == 200, (kind, r.text)
        body = r.json()
        answer = body["answer"]
        assert body["kind"] == "marked", kind
        # Newest first, in the words they were saved in.
        assert body["marked"] == ["上个月每个供应商的发票总额", "Type 1 POs not fully received"]
        assert "1. 上个月每个供应商的发票总额" in answer
        assert "2. Type 1 POs not fully received" in answer
        assert answer.startswith("您收藏了 2 个问题") and "Marked" in answer
        assert "406" not in answer and "待处理任务" not in answer, kind
        assert called == [], kind          # no narrator wrote any of it

    # English, and none marked.
    _uncooperative(monkeypatch, "next")
    r = await admin_client.post(CHAT, json={"message": "Which questions have I marked?"})
    assert r.json()["answer"].startswith("You have marked 2 question(s)")


async def test_none_marked_says_how_to_mark(requester_client, monkeypatch):
    _uncooperative(monkeypatch, "next")
    r = await requester_client.post(CHAT, json={"message": "我收藏了哪些问题？"})
    body = r.json()
    assert body["kind"] == "marked" and body["marked"] == []
    assert body["answer"].startswith("您还没有收藏任何问题") and "星标" in body["answer"]


async def test_only_the_askers_own_marked_questions(admin_client, requester_client, monkeypatch):
    await admin_client.post(SAVED, json={"question": "admin's weekly report"})
    await requester_client.post(SAVED, json={"question": "requester's report"})
    _uncooperative(monkeypatch, "next")
    r = await requester_client.post(CHAT, json={"message": "show my saved questions"})
    assert r.json()["marked"] == ["requester's report"]
    assert "admin's weekly report" not in r.json()["answer"]


async def test_a_split_message_answers_both_parts(admin_client, monkeypatch):
    """The planner still runs first, so "my marked questions, and my tasks"
    is split and each part answered."""
    await admin_client.post(SAVED, json={"question": "PO 未收货清单"})

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        if (context or {}).get("question_part"):
            return {"kind": "next"}
        return {"kind": "split", "parts": ["我收藏了哪些问题？", "我现在有几条待办？"]}

    async def fake_guide(message, payload):
        return {"text": f"您有 {payload['open_task_count']} 个待办。", "usage": {}}

    async def fake_tasks(role, user_id):
        return []

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    monkeypatch.setattr(guide_view, "my_tasks", fake_tasks)
    r = await admin_client.post(CHAT, json={"message": "我收藏了哪些问题？另外我现在有几条待办？"})
    parts = r.json()["parts"]
    assert [p["kind"] for p in parts] == ["marked", "tasks"]
    assert "PO 未收货清单" in parts[0]["answer"] and parts[1]["answer"] == "您有 0 个待办。"


# ── approve_exp in the reply (ISS-0102) ──────────────────────────────────────

def _task(type_, doc_type, n=0):
    return {"type": type_, "document_type": doc_type, "document_number": f"X-{n}",
            "title": "t", "description": None, "priority": "normal", "due_date": None,
            "amount": None, "vendor": None, "assigned_to_me_personally": True,
            "assigned_role": None, "created_at": dt.datetime(2026, 10, 1)}


async def test_every_task_type_has_a_readable_label():
    assert guide_view.task_type_label("confirm_receipt") == "Confirm Goods Receipt"
    assert guide_view.task_type_label("approve_exp") == "Approve Expense Claim"
    assert guide_view.task_type_label("approve_trv") == "Approve Travel Expense"
    assert guide_view.task_type_label("approve_mil") == "Approve Mileage Claim"
    assert guide_view.task_type_label("vms_confirm_training") == "Vms Confirm Training"
    s = guide_view.task_summary([], [_task("approve_exp", "exp")])
    assert s["not_in_this_inbox"][0]["label"] == "Approve Expense Claim"


async def test_no_raw_task_type_reaches_the_reply(admin_client, monkeypatch):
    async def fake_tasks(role, user_id):
        return [_task("confirm_receipt", "gr", n=i) for i in range(3)] + [
            _task("approve_exp", "exp", n=i) for i in range(2)]

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "next"}

    async def fake_guide(message, payload):
        # The narrator printed the keys, not the labels.
        return {"text": "| 类型 | 数量 |\n|---|---|\n| confirm_receipt | 3 |\n\n"
                        "另有 OA 的 `approve_exp` 2 个。", "usage": {}}

    monkeypatch.setattr(guide_view, "my_tasks", fake_tasks)
    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    r = await admin_client.post(CHAT, json={"message": "我现在有几条待办？"})
    answer = r.json()["answer"]
    assert "approve_exp" not in answer and "confirm_receipt" not in answer
    assert "Approve Expense Claim" in answer and "| Confirm Goods Receipt | 3 |" in answer


async def test_the_rule_cards_are_in_the_registry():
    assert "marked_questions_are_listed" in {
        r["id"] for r in rule_registry.static_rules("assistant")}
    assert rule_registry.card("answer_tables_sort_and_fold")["provenance"] == "pinned"
