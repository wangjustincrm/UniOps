"""Assistant fixes, batch 6 — what the tester found still failing on
ISS-0008, ISS-0010, ISS-0011, ISS-0013 and ISS-0059.

Each test pins a statement the Assistant makes to the code that makes it true,
or pins the code path that guarantees the statement is made at all.
"""
import json
import sys
import types

import pytest

from app.core.ontology import REGISTRY
from app.services import assistant_llm, rule_registry

pytestmark = pytest.mark.asyncio
CHAT = "/api/v1/assistant/chat"


def _plan_query(monkeypatch, query: dict, narration: str = "ok") -> list:
    """The planner returns `query`; the narrator returns `narration` and
    records the result it was given."""
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "query", "query": query}

    async def fake_narrate(message, q, result):
        seen.append(result)
        return {"text": narration, "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    return seen


# ── ISS-0008: a list of procurement types always says Type 1 is not selectable ─

_BY_TYPE = {"entity": "purchase_request", "metrics": ["count"], "group_by": ["type"]}


async def test_a_list_of_procurement_types_always_says_type_1_is_not_selectable(
        admin_client, monkeypatch):
    """The 1-in-7 failure: "系统里有哪几种采购类型？" planned as a count per type,
    narrated as six lines and nothing about Type 1. The sentence is now added
    in code, under whatever the narrator wrote."""
    seen = _plan_query(monkeypatch, _BY_TYPE, narration="系统里有 6 种采购类型：……")
    r = await admin_client.post(CHAT, json={"message": "系统里有哪几种采购类型？"})
    assert r.status_code == 200, r.text
    answer = r.json()["answer"]
    assert answer.startswith("系统里有 6 种采购类型")
    assert "不能选择" in answer and "NC" in answer
    assert seen[0]["said_with_this_list"][0][0] == "type_1_not_selectable"

    _plan_query(monkeypatch, _BY_TYPE, narration="There are six types.")
    r = await admin_client.post(CHAT, json={"message": "What procurement types are there?"})
    assert "cannot be chosen when creating a PR in EPMS" in r.json()["answer"]

    # The same field on purchase orders carries the same sentence.
    _plan_query(monkeypatch, {"entity": "purchase_order", "metrics": ["count"],
                              "group_by": ["type"]}, narration="Six.")
    r = await admin_client.post(CHAT, json={"message": "How many POs per type?"})
    assert "cannot be chosen" in r.json()["answer"]


async def test_the_sentence_is_not_added_when_types_are_not_listed(admin_client, monkeypatch):
    seen = _plan_query(monkeypatch, {"entity": "purchase_request", "select": ["number", "type"],
                                     "limit": 5}, narration="Five PRs.")
    r = await admin_client.post(CHAT, json={"message": "List five PRs"})
    assert r.json()["answer"] == "Five PRs."
    assert "said_with_this_list" not in seen[0]


async def test_the_listing_sentence_names_a_pinned_rule():
    for entity in ("purchase_request", "purchase_order"):
        rule, english, chinese = REGISTRY[entity].fields["type"].when_listed
        card = rule_registry.card(rule)
        assert card["provenance"] == "pinned" and card["test"]
        assert "cannot be chosen" in english and "cannot be chosen" in card["answer"]
        assert "NC" in chinese


async def test_the_narrator_is_told_not_to_repeat_it(monkeypatch):
    mod = types.ModuleType("anthropic")
    for name in ("APIConnectionError", "RateLimitError", "APIStatusError"):
        setattr(mod, name, type(name, (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    sent: list = []

    class _Client:
        def __init__(self):
            self.messages = self

        async def create(self, **kw):
            sent.append(kw)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="ok")], usage=None)

    monkeypatch.setattr(assistant_llm, "_client", _Client)
    note = REGISTRY["purchase_request"].fields["type"].when_listed
    await assistant_llm.narrate("q", _BY_TYPE, {"rows": [], "said_with_this_list": [note]})
    payload = json.loads(sent[0]["messages"][0]["content"])
    assert payload["added_below_your_reply"] == [note[1]]
    assert "added_below_your_reply" in sent[0]["system"]

