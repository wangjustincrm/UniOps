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


# ── ISS-0070: the HISTORY block — no import marker, the role actually held ──

from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.services import workflow_view  # noqa: E402

_T0 = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


def _ev(minutes, action, name, role, step, comment=None):
    return SimpleNamespace(step_idx=step, action=action, actor_name=name, actor_role=role,
                           comment=comment, created_at=_T0 + timedelta(minutes=minutes))


def test_the_import_marker_is_not_shown_as_a_comment():
    """PO-079-2605-03: every line read "[reconstructed]"."""
    got = workflow_view.history_entries([
        _ev(0, "submit", "Kris Enriquez", "requester", 0, "[reconstructed]"),
        _ev(1, "approve", "Balakrishnan Raghul", "dept_manager", 0, "[reconstructed]"),
        _ev(2, "approve", "Kris Enriquez", "director", 1,
            "[reconstructed] Auto-skipped (director not in legacy system)"),
        _ev(3, "approve", "Farshid Mohammadi", "procurement_manager", 2, "Looks fine"),
    ], [])
    assert [e["comment"] for e in got] == [
        None, None, "Auto-skipped (director not in legacy system)", "Looks fine"]
    assert [e["imported"] for e in got] == [True, True, True, False]
    assert not any("[reconstructed]" in (e["comment"] or "") for e in got)


def test_the_marker_is_the_one_the_pms_import_writes():
    script = Path(workflow_view.__file__).resolve().parents[2] / "scripts" / "import_pms" / "reconstruct.py"
    assert f'RECON_TAG = "{workflow_view.IMPORTED_MARKER}"' in script.read_text(encoding="utf-8")


def test_a_return_carries_the_role_of_the_step_it_was_decided_at():
    """PA-20261013-0003: Yuping Huang approved as Finance BP and her return
    was listed as "Requester" — her primary role, which the engine records
    for return/reject."""
    steps = [{"role": "dept_manager", "label": "Dept Manager"},
             {"role": "finance_bp", "label": "Finance BP"},
             {"role": "finance_director", "label": "Finance Director of Oversea Department"}]
    got = workflow_view.history_entries([
        _ev(0, "submit", "Kris Enriquez", "requester", 0),
        _ev(1, "approve", "A Manager", "dept_manager", 0),
        _ev(2, "return", "Yuping Huang", "requester", 1, "Wrong amount"),
        _ev(3, "submit", "Kris Enriquez", "requester", 0),
        _ev(4, "approve", "A Manager", "dept_manager", 0),
        _ev(5, "approve", "Yuping Huang", "finance_bp", 1),
        _ev(6, "reject", "Yuping Huang", "requester", 2, "No budget"),
    ], steps)
    roles = [(e["action"], e["actor_name"], e["actor_role"]) for e in got]
    assert roles == [
        ("submit", "Kris Enriquez", "Requester"),
        ("approve", "A Manager", "Dept Manager"),
        # the step's role as recorded on this document's own approve at step 1
        ("return", "Yuping Huang", "Finance BP"),
        ("submit", "Kris Enriquez", "Requester"),
        ("approve", "A Manager", "Dept Manager"),
        ("approve", "Yuping Huang", "Finance BP"),
        # no approve at step 2 yet: the engine's chain names the step
        ("reject", "Yuping Huang", "Finance Director of Oversea Department"),
    ]


def test_without_any_source_for_the_step_the_recorded_role_stays():
    got = workflow_view.history_entries([_ev(0, "return", "X", "gm", 4)], [])
    # Unchanged, not dropped: no step to read a better one from.
    assert got[0]["actor_role"] == workflow_view.role_label("gm")
