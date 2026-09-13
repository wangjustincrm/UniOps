"""The chat loop, with the model stubbed out.

What is worth testing here is not whether the model writes good queries — that is
prompt work, and it changes. It is that the code around it holds when the model
behaves badly. So the stubs below deliberately misbehave: one asks for a column
that is not in the schema, one tries to read another user's rows, one refuses to
answer. None of them should be able to produce a wrong-but-confident reply, and
none should be able to reach data the caller cannot see.

The load-bearing test is test_a_greedy_plan_still_cannot_escape_the_row_scope:
it hands the endpoint a plan that asks for everything, from a requester, and
asserts the answer is built from that requester's rows only. If the model were
compromised outright, that is the property that still has to hold.
"""
import uuid
from decimal import Decimal

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.services import assistant_llm

pytestmark = pytest.mark.asyncio

CHAT = "/api/v1/assistant/chat"


def _user_id(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


async def _seed_vendor(test_engine) -> Vendor:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Chat Test Vendor",
                   category="Services", contact_name="C", contact_email="c@t.test")
        db.add(v)
        await db.commit()
        await db.refresh(v)
        return v


async def _seed_po(test_engine, vendor, creator, *, number, total="100.00"):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(PurchaseOrder(
            number=number, title=number, type=1, status="approved",
            subtotal=Decimal(total), total=Decimal(total),
            vendor_id=vendor.id, vendor_name=vendor.name, created_by=creator))
        await db.commit()


def _stub(monkeypatch, *, plans, narration="Here is the answer."):
    """Feed a scripted sequence of plans, and record what each narrator was given."""
    calls = {"plan": [], "narrate": [], "preflight": []}
    queue = list(plans)

    async def fake_plan(schema, message, context=None, retry_error=None):
        calls["plan"].append({"schema": schema, "message": message,
                              "context": context, "retry_error": retry_error})
        return queue.pop(0)

    async def fake_narrate(message, query, result):
        calls["narrate"].append({"message": message, "query": query, "result": result})
        return {"text": narration, "usage": {}}

    async def fake_narrate_preflight(message, doc_number, preflight):
        calls["preflight"].append({"message": message, "doc_number": doc_number,
                                   "preflight": preflight})
        return {"text": narration, "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    monkeypatch.setattr(assistant_llm, "narrate_preflight", fake_narrate_preflight)
    return calls


# ── the property that must hold no matter what the model does ─────────────────


async def test_a_greedy_plan_still_cannot_escape_the_row_scope(
    test_engine, admin_client, requester_client, monkeypatch
):
    """A plan asking for everything, run as a requester, sees only their rows."""
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    mine = f"PO-CHAT-{tag}-mine"
    theirs = f"PO-CHAT-{tag}-theirs"
    await _seed_po(test_engine, vendor, _user_id(requester_client), number=mine)
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=theirs)

    greedy = {"kind": "query", "query": {
        "entity": "purchase_order", "select": ["number"], "limit": 100,
        "where": [{"field": "number", "op": "like", "value": f"PO-CHAT-{tag}"}]}}

    calls = _stub(monkeypatch, plans=[greedy])
    r = await requester_client.post(CHAT, json={"message": "show me every PO"})
    assert r.status_code == 200

    rows = calls["narrate"][0]["result"]["rows"]
    numbers = {row["number"] for row in rows}
    assert mine in numbers, "the requester's own PO should be there"
    assert theirs not in numbers, "row scope must still hold against a greedy plan"


async def test_the_planner_is_only_shown_entities_the_caller_may_view(
    admin_client, monkeypatch
):
    """Filtering happens before the model sees anything: an entity out of reach
    is never named to it, so it cannot propose a query that would be refused."""
    calls = _stub(monkeypatch, plans=[{"kind": "cannot", "reason": "too_ambiguous",
                                       "explanation": "Need more detail."}])
    await admin_client.post(CHAT, json={"message": "anything"})

    schema = calls["plan"][0]["schema"]
    assert schema, "admin should see entities"
    for entity in schema:
        assert "fields" in entity and entity["fields"]
        # Columns outside the whitelist must not leak into the prompt either.
        if entity["name"] == "purchase_order":
            assert "created_by" not in entity["fields"]
            assert "nc_source_pk" not in entity["fields"]


# ── refusals are answers, not errors ──────────────────────────────────────────


async def test_cannot_answer_comes_back_as_an_answer(admin_client, monkeypatch):
    _stub(monkeypatch, plans=[{
        "kind": "cannot", "reason": "not_a_data_question",
        "explanation": "That is about why something is blocked, not a data question.",
    }])
    r = await admin_client.post(CHAT, json={"message": "why can't I submit PR-1?"})

    assert r.status_code == 200, "a refusal is not a server error"
    body = r.json()
    assert body["kind"] == "cannot_answer"
    assert body["reason"] == "not_a_data_question"
    assert body["query"] is None
    assert "blocked" in body["answer"]


# ── recovery ──────────────────────────────────────────────────────────────────


async def test_a_rejected_plan_is_fed_back_once_and_can_succeed(
    test_engine, admin_client, monkeypatch
):
    """The validator's message names what IS available, so one retry usually
    lands. The retry must carry that message, or it is just a second guess."""
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=f"PO-RT-{tag}")

    bad = {"kind": "query", "query": {"entity": "purchase_order",
                                      "select": ["not_a_column"]}}
    good = {"kind": "query", "query": {
        "entity": "purchase_order", "select": ["number"],
        "where": [{"field": "number", "op": "like", "value": f"PO-RT-{tag}"}]}}

    calls = _stub(monkeypatch, plans=[bad, good])
    r = await admin_client.post(CHAT, json={"message": "list my POs"})

    assert r.status_code == 200
    assert r.json()["kind"] == "answer"
    assert len(calls["plan"]) == 2, "should have retried exactly once"
    retry_error = calls["plan"][1]["retry_error"]
    assert retry_error and "not_a_column" in retry_error
    assert "number" in retry_error, "the rejection should list what IS available"


async def test_two_bad_plans_give_up_honestly(admin_client, monkeypatch):
    bad = {"kind": "query", "query": {"entity": "purchase_order",
                                      "select": ["still_not_a_column"]}}
    calls = _stub(monkeypatch, plans=[bad, bad])
    r = await admin_client.post(CHAT, json={"message": "list POs"})

    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "cannot_answer"
    assert body["reason"] == "invalid_plan"
    assert len(calls["plan"]) == 2, "must not retry forever"


# ── the receipt ───────────────────────────────────────────────────────────────


async def test_the_answer_carries_the_query_that_produced_it(
    test_engine, admin_client, monkeypatch
):
    """An answer nobody can check is not worth much in a finance system."""
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=f"PO-RC-{tag}")

    plan = {"kind": "query", "query": {
        "entity": "purchase_order", "metrics": ["count"],
        "where": [{"field": "number", "op": "like", "value": f"PO-RC-{tag}"}]}}
    _stub(monkeypatch, plans=[plan])

    body = (await admin_client.post(CHAT, json={"message": "how many?"})).json()

    assert body["query"]["entity"] == "purchase_order"
    assert body["sources"]["entity"] == "purchase_order"
    assert body["sources"]["row_count"] == 1
    assert body["sources"]["truncated"] is False


async def test_page_context_reaches_the_planner(admin_client, monkeypatch):
    """Without it, "this PO" has no referent and the model has to guess."""
    calls = _stub(monkeypatch, plans=[{"kind": "cannot", "reason": "too_ambiguous",
                                       "explanation": "n/a"}])
    await admin_client.post(CHAT, json={
        "message": "what is the status of this one?",
        "context": {"app": "epms", "doc_type": "po", "doc_id": "abc-123"},
    })

    ctx = calls["plan"][0]["context"]
    assert ctx["doc_type"] == "po"
    assert ctx["doc_id"] == "abc-123"


# ── degradation ───────────────────────────────────────────────────────────────


async def test_model_unavailable_is_a_503_not_a_made_up_answer(
    admin_client, monkeypatch
):
    async def boom(*a, **kw):
        raise assistant_llm.LlmUnavailable("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr(assistant_llm, "plan", boom)
    r = await admin_client.post(CHAT, json={"message": "anything"})

    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


async def test_empty_message_is_refused_before_any_model_call(admin_client, monkeypatch):
    called = {"n": 0}

    async def counting_plan(*a, **kw):
        called["n"] += 1
        raise AssertionError("should not be reached")

    monkeypatch.setattr(assistant_llm, "plan", counting_plan)
    r = await admin_client.post(CHAT, json={"message": ""})

    assert r.status_code == 422
    assert called["n"] == 0, "do not spend a call on an empty question"


# ── gate questions route to the real gates, never to a guess ──────────────────


async def _seed_pr(test_engine, creator, *, number, pr_type=2):
    from app.models.pr import PurchaseRequest
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pr = PurchaseRequest(number=number, title="Gate question test", type=pr_type,
                             status="draft", amount=Decimal("100.00"), created_by=creator)
        db.add(pr)
        await db.commit()
        return pr.id


async def test_a_why_blocked_question_runs_the_real_gates(
    test_engine, admin_client, monkeypatch
):
    """The whole point: the reason comes from the gates, not from the model.

    Before this, the planner refused these questions — correctly, since guessing
    a cause sends someone to fix the wrong thing. Now it routes them.
    """
    number = f"PR-GATE-{uuid.uuid4().hex[:6]}"
    await _seed_pr(test_engine, _user_id(admin_client), number=number)

    calls = _stub(monkeypatch, plans=[{
        "kind": "check", "doc_type": "pr", "doc_number": number, "action": "submit"}])

    r = await admin_client.post(CHAT, json={"message": f"why can't I submit {number}?"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "preflight"

    # The narrator was handed real check results, not a free-form prompt.
    handed = calls["preflight"][0]["preflight"]
    ids = {c["id"] for c in handed["checks"]}
    assert "vendor_required" in ids, "field gates should be there"
    assert handed["allowed"] is False
    # And the response carries them, so the answer can be checked.
    assert body["preflight"]["checks"]
    assert body["sources"]["document"] == number


async def test_the_gate_result_distinguishes_fixable_from_not(
    test_engine, admin_client, monkeypatch
):
    """fixable_by_user is the field the reply is built from — it decides between
    "go add a vendor" and "this isn't yours to fix"."""
    number = f"PR-FIX-{uuid.uuid4().hex[:6]}"
    await _seed_pr(test_engine, _user_id(admin_client), number=number)
    calls = _stub(monkeypatch, plans=[{
        "kind": "check", "doc_type": "pr", "doc_number": number, "action": "submit"}])

    await admin_client.post(CHAT, json={"message": f"why is {number} stuck?"})

    checks = calls["preflight"][0]["preflight"]["checks"]
    failed = [c for c in checks if not c["passed"]]
    assert failed, "a draft PR with no vendor should fail something"
    assert all("fixable_by_user" in c for c in failed)


async def test_a_document_the_caller_cannot_see_is_not_found(
    test_engine, admin_client, requester_client, monkeypatch
):
    """Same answer as a document that does not exist. Saying "it's blocked on a
    missing vendor" would leak that it exists and what state it is in."""
    number = f"PR-HID-{uuid.uuid4().hex[:6]}"
    await _seed_pr(test_engine, _user_id(admin_client), number=number)

    _stub(monkeypatch, plans=[{
        "kind": "check", "doc_type": "pr", "doc_number": number, "action": "submit"}])

    r = await requester_client.post(CHAT, json={"message": f"why can't I submit {number}?"})
    body = r.json()
    assert body["kind"] == "cannot_answer"
    assert body["reason"] == "document_not_found"


async def test_the_document_being_viewed_is_used_when_no_number_is_given(
    test_engine, admin_client, monkeypatch
):
    """"why can't I submit this?" has to resolve against the page context."""
    number = f"PR-CTX-{uuid.uuid4().hex[:6]}"
    pr_id = await _seed_pr(test_engine, _user_id(admin_client), number=number)

    calls = _stub(monkeypatch, plans=[{
        "kind": "check", "doc_type": "pr", "action": "submit"}])  # no doc_number

    r = await admin_client.post(CHAT, json={
        "message": "why can't I submit this?",
        "context": {"app": "epms", "doc_type": "pr", "doc_id": str(pr_id)},
    })
    assert r.json()["kind"] == "preflight"
    assert calls["preflight"][0]["doc_number"] == number


async def test_no_number_and_no_context_asks_instead_of_guessing(
    admin_client, monkeypatch
):
    _stub(monkeypatch, plans=[{"kind": "check", "doc_type": "pr", "action": "submit"}])
    body = (await admin_client.post(CHAT, json={"message": "why is it blocked?"})).json()

    assert body["kind"] == "cannot_answer"
    assert body["reason"] == "document_not_found"
    assert "which document" in body["answer"].lower()


async def test_an_unsupported_doc_type_says_so_plainly(admin_client, monkeypatch):
    _stub(monkeypatch, plans=[{
        "kind": "check", "doc_type": "po", "doc_number": "PO-1", "action": "submit"}])
    body = (await admin_client.post(CHAT, json={"message": "why can't I submit PO-1?"})).json()

    assert body["kind"] == "cannot_answer"
    assert body["reason"] == "not_supported"
