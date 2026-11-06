"""Assistant fixes from the issue tracker (ISS-0001 … ISS-0007, 2026-10-16).

Model calls are stubbed; what is pinned is everything AROUND the model: which
language each narrator is told to write in, what it is handed, which document
the planner is told is on screen, and what the ontology lets it reach.
"""
import json
import sys
import types
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.delegation import local_today
from app.crud.current_step import ROLE_LABELS
from app.models.agreement import PurchaseAgreement
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.services import assistant_llm, guide_view, reply_language

pytestmark = pytest.mark.asyncio
CHAT = "/api/v1/assistant/chat"
ZH = "Chinese (Simplified)"


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _user_id(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


# ── ISS-0003 / ISS-0001: the reply language is decided in code ───────────────

@pytest.mark.parametrize("message,expected", [
    ("采购协议的审批流程是怎样的？", ZH),
    ("用中文回答", ZH),
    ("AP Clerk 能执行付款吗？", ZH),             # English role name inside Chinese
    ("用英文回答", "English"),                  # Chinese sentence asking for English
    ("Answer in English please", "English"),
    ("responde en español", "Spanish"),
    ("How does PA approval work?", None),       # Latin script: left to the model
    ("¿Cuál es el proceso de aprobación?", None),
])
async def test_detect(message, expected):
    assert reply_language.detect(message) == expected


@pytest.mark.parametrize("message,expected", [
    ("用中文回答", True), ("请用中文回答", True), ("Answer in English please", True),
    ("用中文回答这张单的供应商是谁", False),      # a real question, in Chinese
    ("采购协议的审批流程是怎样的？", False),
])
async def test_language_only_request(message, expected):
    assert reply_language.is_language_only_request(message) is expected


async def test_chinese_payload_carries_the_fixed_terms():
    got = reply_language.instructions("这张单的审批流程是什么？")
    assert got["reply_in"] == ZH and got["terms"]["Director"] == "总监"
    assert "terms" not in reply_language.instructions("How does this work?")


# ── ISS-0007: every role and process label has one fixed Chinese name ────────

# Labels in production's workflow_defs on 2026-10-16 (read-only query). The
# narrator sees these exact strings; one missing here gets translated freely.
_PROD_WORKFLOW_LABELS = {
    "Department Manager", "Finance Manager", "Procurement Manager", "GM/OPM",
    "Finance Director", "AP Review", "GM", "Director",
    "Finance Director of Oversea Department", "Operations Manager",
    "Purchasing Manager", "General Manager", "Quality Manager",
}


async def test_every_known_label_has_a_chinese_term():
    known = set(ROLE_LABELS.values()) | _PROD_WORKFLOW_LABELS | set(
        guide_view.PROCESS_LABELS.values()) | {
        "Over-Budget — Finance Manager", "Over-Budget — GM / OPM"}
    missing = sorted(k for k in known if k not in reply_language.TERMS_ZH)
    assert not missing, f"no fixed Chinese term for {missing}"


async def test_the_three_mistranslations_from_the_report_are_pinned():
    t = reply_language.TERMS_ZH
    assert t["Director"] == "总监" and t["Supervisor"] == "主管"      # not 主管 / 董事
    assert t["AP Review"] == "应付审核"                               # not 账应收审查
    assert t["MIL"] == "里程报销单" and t["GM / OPM"] == "总经理/运营经理"


# ── every narrator gets the language rules and the decision ──────────────────

@pytest.fixture
def fake_anthropic(monkeypatch):
    """The narrators import anthropic for its exception types; the test image
    does not install it."""
    mod = types.ModuleType("anthropic")
    for name in ("APIConnectionError", "RateLimitError", "APIStatusError"):
        setattr(mod, name, type(name, (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", mod)


class _FakeResp:
    def __init__(self):
        self.content = [type("B", (), {"type": "text", "text": "ok"})()]
        self.usage = None


class _FakeClient:
    def __init__(self, calls):
        self.messages = self
        self._calls = calls

    async def create(self, **kw):
        self._calls.append(kw)
        return _FakeResp()


async def test_every_narrator_is_told_the_language(monkeypatch, fake_anthropic):
    calls: list[dict] = []
    monkeypatch.setattr(assistant_llm, "_client", lambda: _FakeClient(calls))
    q = "这张单的审批到哪一步了？"
    await assistant_llm.narrate(q, {"entity": "purchase_order"}, {"rows": []})
    await assistant_llm.narrate_preflight(q, "PR-1", {"checks": []})
    await assistant_llm.narrate_workflow(q, {"document_number": "PR-1", "steps": []})
    await assistant_llm.narrate_guide(q, {"question": q, "kind": "process"})
    await assistant_llm.narrate_plan(q, [])
    await assistant_llm.render_fixed_reply("I could not find PR-1.", q, ZH)
    assert len(calls) == 6
    for kw in calls:
        system = kw["system"] if isinstance(kw["system"], str) else kw["system"][0]["text"]
        assert system.startswith(reply_language.LANGUAGE_RULES)
        payload = json.loads(kw["messages"][-1]["content"])
        assert payload["reply_in"] == ZH and payload["terms"]["Director"] == "总监"


async def test_workflow_narrator_now_receives_the_question(monkeypatch, fake_anthropic):
    """ISS-0004: only the chain was sent, so every reply was the same English
    status recap whatever was asked."""
    calls: list[dict] = []
    monkeypatch.setattr(assistant_llm, "_client", lambda: _FakeClient(calls))
    await assistant_llm.narrate_workflow("谁批的？", {"document_number": "PR-1"})
    payload = json.loads(calls[0]["messages"][0]["content"])
    assert payload["question"] == "谁批的？" and payload["document_number"] == "PR-1"


async def test_planner_is_told_a_language_only_message_repeats_the_last_question(
        monkeypatch, fake_anthropic):
    calls: list[dict] = []

    class _PlanResp:
        content = []
        usage = None

    class _PlanClient:
        messages = None

        def __init__(self):
            self.messages = self

        async def create(self, **kw):
            calls.append(kw)
            return _PlanResp()

    monkeypatch.setattr(assistant_llm, "_client", lambda: _PlanClient())
    await assistant_llm.plan([], "用中文回答")
    text = calls[0]["messages"][-1]["content"]
    assert ZH in text and "PREVIOUS question" in text


# ── ISS-0001: code-written replies reach the asker in their language ─────────

def _stub_plan(monkeypatch, planned: dict, rendered: list):
    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return planned

    async def fake_render(text, question, language):
        rendered.append({"text": text, "question": question, "language": language})
        return {"text": "【已翻译】" + text, "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "render_fixed_reply", fake_render)


async def test_unsupported_process_reply_is_rendered_and_lists_po_signoff(admin_client, monkeypatch):
    rendered: list = []
    _stub_plan(monkeypatch, {"kind": "process", "doc_type": "nonsense"}, rendered)
    r = await admin_client.post(CHAT, json={"message": "这个流程是怎样的？"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("【已翻译】") and "_fixed_text" not in body
    assert rendered[0]["language"] == ZH
    assert "PO sign-off" in rendered[0]["text"] and "Purchase Agreement" in rendered[0]["text"]


async def test_english_question_keeps_the_english_reply_without_a_model_call(admin_client, monkeypatch):
    rendered: list = []
    _stub_plan(monkeypatch, {"kind": "process", "doc_type": "nonsense"}, rendered)
    r = await admin_client.post(CHAT, json={"message": "how does this process work?"})
    assert r.status_code == 200
    assert rendered == [] and r.json()["answer"].startswith("I can describe")


async def test_model_written_refusal_is_not_rendered_again(admin_client, monkeypatch):
    rendered: list = []
    _stub_plan(monkeypatch, {"kind": "cannot", "reason": "x",
                             "explanation": "我无法回答这个问题。"}, rendered)
    r = await admin_client.post(CHAT, json={"message": "这个是什么？"})
    assert rendered == [] and r.json()["answer"] == "我无法回答这个问题。"


# ── ISS-0004: the on-screen document's number reaches the planner ────────────

async def _seed_pr(test_engine, creator: uuid.UUID) -> PurchaseRequest:
    async with _factory(test_engine)() as db:
        pr = PurchaseRequest(number=f"PR-ISS4-{uuid.uuid4().hex[:6]}", title="ISS-0004",
                             type=2, status="approved", amount=Decimal("3250.00"),
                             vendor_name="Raise The Standard", created_by=creator)
        db.add(pr)
        await db.commit()
        await db.refresh(pr)
        return pr


def _capture_context(monkeypatch) -> list:
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        seen.append(context)
        return {"kind": "cannot", "reason": "x", "explanation": "n/a"}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    return seen


async def test_on_screen_pr_number_is_added_to_the_context(admin_client, test_engine, monkeypatch):
    pr = await _seed_pr(test_engine, _user_id(admin_client))
    seen = _capture_context(monkeypatch)
    await admin_client.post(CHAT, json={
        "message": "这张单的供应商是谁？金额多少？",
        "context": {"app": "epms", "doc_type": "pr", "doc_id": str(pr.id)}})
    assert seen[0]["doc_number"] == pr.number and seen[0]["doc_number_field"] == "number"


async def test_a_pr_the_caller_cannot_see_adds_no_number(requester_client, admin_client,
                                                         test_engine, monkeypatch):
    pr = await _seed_pr(test_engine, _user_id(admin_client))   # someone else's
    seen = _capture_context(monkeypatch)
    await requester_client.post(CHAT, json={
        "message": "这张单的供应商是谁？",
        "context": {"app": "epms", "doc_type": "pr", "doc_id": str(pr.id)}})
    assert "doc_number" not in seen[0]


async def test_context_note_tells_the_planner_to_query_by_number():
    note = assistant_llm._context_note({"doc_type": "pr", "doc_id": "x",
                                        "doc_number": "PR-1", "doc_number_field": "number"})
    assert "number = PR-1" in note and "explain_workflow is only for" in note


# ── ISS-0005: the process route can name the over-budget mode ───────────────

async def test_process_payload_carries_the_over_budget_rule(monkeypatch):
    async def fake_forward(method, path, token, **kw):
        return 200, {"doc_type": "pr", "steps": [
            {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}],
            "over_budget": {"mode": "allow", "mode_label": "Allow without extra approval",
                            "added_steps": [], "blocks_submission": False, "note": "n"}}

    monkeypatch.setattr(guide_view.approval_client, "forward", fake_forward)
    got = await guide_view.process_steps("pr", "t")
    assert got["over_budget"]["mode_label"] == "Allow without extra approval"
    assert got["over_budget"]["added_steps"] == []


async def test_process_tool_offers_every_wired_chain():
    enum = assistant_llm._PROCESS_TOOL["input_schema"]["properties"]["doc_type"]["enum"]
    assert set(enum) == set(guide_view.SUPPORTED_PROCESSES)
    assert {"agr", "posign", "exp", "mil", "trv", "tra"} <= set(enum)


# ── ISS-0002: agreements are queryable, inside the same row scope ────────────

async def _grant_agreement_read(test_engine, role: str) -> None:
    async with _factory(test_engine)() as db:
        await db.execute(sa.text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.agreement.read','epms','View Agreements',104) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(sa.text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES (:role,'epms.agreement.read') ON CONFLICT DO NOTHING"), {"role": role})
        await db.commit()


async def _seed_agreement(test_engine, creator: uuid.UUID, *, nte=None, consumed="0",
                          valid_to=None) -> PurchaseAgreement:
    async with _factory(test_engine)() as db:
        v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Mister Safety Shoes",
                   category="Services", contact_name="C", contact_email="c@t.test")
        db.add(v)
        await db.flush()
        a = PurchaseAgreement(
            number=f"AGR-ISS2-{uuid.uuid4().hex[:6]}", title="Safety shoes",
            agreement_type="house_account", vendor_id=v.id, vendor_name=v.name,
            valid_from=date(2026, 1, 1), valid_to=valid_to or date(2026, 12, 31),
            not_to_exceed=Decimal(nte) if nte is not None else None,
            consumed_amount=Decimal(consumed), status="active", created_by=creator)
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return a


def _query_plan(monkeypatch, query: dict) -> list:
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        seen.append({"schema": schema})
        return {"kind": "query", "query": query}

    async def fake_narrate(message, q, result):
        seen.append({"result": result})
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    return seen


async def test_agreement_validity_nte_and_remaining_are_queryable(admin_client, test_engine,
                                                                  monkeypatch):
    await _grant_agreement_read(test_engine, "system_admin")
    capped = await _seed_agreement(test_engine, _user_id(admin_client), nte="1000.00",
                                   consumed="250.00",
                                   valid_to=local_today() + timedelta(days=20))
    open_ended = await _seed_agreement(test_engine, _user_id(admin_client))
    seen = _query_plan(monkeypatch, {
        "entity": "purchase_agreement",
        "select": ["number", "valid_to", "days_until_expiry", "not_to_exceed",
                   "consumed_amount", "remaining_amount"],
        "where": [{"field": "number", "op": "in", "value": [capped.number, open_ended.number]}]})
    r = await admin_client.post(CHAT, json={"message": "协议的 NTE 还剩多少？"})
    assert r.status_code == 200, r.text
    assert any(e["name"] == "purchase_agreement" for e in seen[0]["schema"])
    rows = {row["number"]: row for row in seen[1]["result"]["rows"]}
    assert Decimal(str(rows[capped.number]["remaining_amount"])) == Decimal("750.00")
    assert rows[capped.number]["days_until_expiry"] == 20
    assert rows[open_ended.number]["not_to_exceed"] is None          # no ceiling, not 0
    assert rows[open_ended.number]["remaining_amount"] is None


async def test_expiring_within_30_days_is_one_filter(admin_client, test_engine, monkeypatch):
    await _grant_agreement_read(test_engine, "system_admin")
    soon = await _seed_agreement(test_engine, _user_id(admin_client),
                                 valid_to=local_today() + timedelta(days=10))
    later = await _seed_agreement(test_engine, _user_id(admin_client),
                                  valid_to=local_today() + timedelta(days=90))
    seen = _query_plan(monkeypatch, {
        "entity": "purchase_agreement", "select": ["number"],
        "where": [{"field": "days_until_expiry", "op": "between", "value": [0, 30]},
                  {"field": "number", "op": "in", "value": [soon.number, later.number]}]})
    await admin_client.post(CHAT, json={"message": "哪些协议在 30 天内到期？"})
    assert [row["number"] for row in seen[1]["result"]["rows"]] == [soon.number]


async def test_requester_sees_own_agreement_and_not_someone_elses(requester_client, admin_client,
                                                                   test_engine, monkeypatch):
    """Row scope: the denial is paired with an admission, so "nobody sees
    anything" cannot pass it."""
    await _grant_agreement_read(test_engine, "requester")
    mine = await _seed_agreement(test_engine, _user_id(requester_client))
    theirs = await _seed_agreement(test_engine, _user_id(admin_client))
    seen = _query_plan(monkeypatch, {
        "entity": "purchase_agreement", "select": ["number"],
        "where": [{"field": "number", "op": "in", "value": [mine.number, theirs.number]}]})
    r = await requester_client.post(CHAT, json={"message": "my agreements"})
    assert r.status_code == 200, r.text
    assert [row["number"] for row in seen[1]["result"]["rows"]] == [mine.number]


async def test_the_permission_decides_whether_agreements_are_in_the_schema(
        requester_client, test_engine, monkeypatch):
    """Revoked → absent; granted → present. Both halves in one test so neither
    can pass on its own (the grant is shared state across this suite)."""
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        seen.append({e["name"] for e in schema})
        return {"kind": "cannot", "reason": "x", "explanation": "n/a"}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    async with _factory(test_engine)() as db:
        await db.execute(sa.text(
            "DELETE FROM role_permissions WHERE role_code='requester' "
            "AND permission_key='epms.agreement.read'"))
        await db.commit()
    await requester_client.post(CHAT, json={"message": "agreements"})
    await _grant_agreement_read(test_engine, "requester")
    await requester_client.post(CHAT, json={"message": "agreements"})
    assert "purchase_agreement" not in seen[0]
    assert "purchase_agreement" in seen[1]
