"""Assistant fixes from the 2026-10-20 issue review, batch 4: the conversation
and the page — what the panel knows about who is asking, what is on screen,
what was said before, and a message that asks several things at once.
"""
import uuid

from jose import jwt

from app.core.config import settings
from app.services import assistant_llm, reply_language

CHAT = "/api/v1/assistant/chat"


def _uid(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


def _stub(monkeypatch, *, plans, narration="Here is the answer."):
    """Scripted plans; every narrator records what it was given."""
    calls = {"plan": [], "narrate": [], "workflow": [], "guide": [], "chain": [],
             "fixed": []}
    queue = list(plans)

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        calls["plan"].append({"schema": schema, "message": message, "context": context,
                              "retry_error": retry_error, "history": history})
        return queue.pop(0)

    async def fake_narrate(message, query, result):
        calls["narrate"].append({"message": message, "query": query, "result": result})
        return {"text": narration, "usage": {}}

    async def fake_narrate_guide(message, payload):
        calls["guide"].append({"message": message, "payload": payload})
        return {"text": narration, "usage": {}}

    async def fake_narrate_workflow(message, view):
        calls["workflow"].append({"message": message, "view": view})
        return {"text": narration, "usage": {}}

    async def fake_narrate_chain(message, chain):
        calls["chain"].append({"message": message, "chain": chain})
        return {"text": narration, "usage": {}}

    async def fake_render_fixed_reply(text, question, language):
        calls["fixed"].append({"text": text, "question": question})
        return {"text": text, "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_narrate_guide)
    monkeypatch.setattr(assistant_llm, "narrate_workflow", fake_narrate_workflow)
    if hasattr(assistant_llm, "narrate_chain"):
        monkeypatch.setattr(assistant_llm, "narrate_chain", fake_narrate_chain)
    monkeypatch.setattr(assistant_llm, "render_fixed_reply", fake_render_fixed_reply)
    return calls


# ── ISS-0051: NC is the ERP, never North Carolina ────────────────────────────

def test_nc_written_as_a_place_comes_back_as_nc():
    said = {
        "北卡州有 238.48 加元仍未支付的重复发票副本": "NC有 238.48 加元仍未支付的重复发票副本",
        "需要解决北卡州重复发票问题（238.48 加元仍需支付）": "需要解决NC重复发票问题（238.48 加元仍需支付）",
        "北卡罗来纳州的重复发票": "NC的重复发票",
        "北卡羅來納的重複發票": "NC的重複發票",
        "在北卡发现重复发票": "在NC发现重复发票",
    }
    for before, after in said.items():
        assert reply_language.keep_system_names(before) == after


def test_text_that_only_contains_the_characters_is_left_alone():
    for text in ("NC 系统中发现重复发票", "NC中的重复发票问题", "东北卡车运输",
                 "Raise The Standard", ""):
        assert reply_language.keep_system_names(text) == text


def test_every_narrator_is_told_system_names_are_not_words():
    got = reply_language.instructions("我有哪些待办已经超时了？")
    assert "NC" in got["system_names"]
    assert "system_names" in reply_language.LANGUAGE_RULES
    assert "北卡" in reply_language.LANGUAGE_RULES


async def test_the_reply_leaves_with_nc_whatever_the_model_wrote(admin_client, monkeypatch):
    _stub(monkeypatch, plans=[{"kind": "next"}],
          narration="紧急任务：需要解决北卡州重复发票问题（238.48 加元仍需支付）")

    async def no_tasks(role, user_id):
        return []
    from app.services import guide_view
    monkeypatch.setattr(guide_view, "my_tasks", no_tasks)
    r = await admin_client.post(CHAT, json={"message": "我有哪些待办已经超时了？"})
    assert r.status_code == 200, r.text
    assert "北卡" not in r.json()["answer"]
    assert "NC重复发票" in r.json()["answer"]
