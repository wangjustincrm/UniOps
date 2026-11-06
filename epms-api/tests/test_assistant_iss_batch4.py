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


# ── ISS-0038 / ISS-0067: what the planner knows about the conversation ──────

def _conversation(n):
    turns = []
    for i in range(1, n + 1):
        turns += [{"role": "user", "text": f"question {i}"},
                  {"role": "assistant", "text": f"answer {i}"}]
    return turns


async def test_a_new_conversation_says_nothing_came_before(admin_client, monkeypatch):
    """"刚才那张单呢？" in a new conversation was answered with 390 open tasks."""
    calls = _stub(monkeypatch, plans=[{"kind": "cannot", "reason": "too_ambiguous",
                                       "explanation": "您指的是哪张单？请给出单号。"}])
    r = await admin_client.post(CHAT, json={"message": "刚才那张单呢？"})
    assert r.json()["kind"] == "cannot_answer"
    ctx = calls["plan"][0]["context"]
    assert ctx["conversation"] == {"earlier_questions": 0, "shown_questions": 0}
    note = assistant_llm._context_note(ctx)
    assert "first message of this conversation" in note
    assert "conversation=" not in note, "not listed as something on screen"
    rules = assistant_llm._PLAN_SESSION_RULES
    assert "刚才那张单" in rules and "too_ambiguous" in rules and "whats_next" in rules


async def test_a_long_conversation_says_it_was_cut(admin_client, monkeypatch):
    calls = _stub(monkeypatch, plans=[{"kind": "cannot", "reason": "too_ambiguous",
                                       "explanation": "n/a"}])
    await admin_client.post(CHAT, json={"message": "and the PO?", "history": _conversation(20)})
    ctx = calls["plan"][0]["context"]
    assert ctx["conversation"] == {"earlier_questions": 20, "shown_questions": 3}
    assert len(calls["plan"][0]["history"]) == 6
    assert "only the last 3" in assistant_llm._context_note(ctx)


async def test_the_first_question_is_read_from_the_whole_conversation(admin_client, monkeypatch):
    """After 20 questions the 18th was named as the first."""
    _stub(monkeypatch, plans=[{"kind": "recall", "position": 1}])
    r = await admin_client.post(CHAT, json={
        "message": "我在这次对话里问的第一个问题是什么？你当时怎么回答的？",
        "history": _conversation(20)})
    body = r.json()
    assert body["kind"] == "recall"
    assert "「question 1」" in body["answer"] and "answer 1" in body["answer"]
    assert "question 18" not in body["answer"]


async def test_the_previous_question_and_out_of_range(admin_client, monkeypatch):
    calls = _stub(monkeypatch, plans=[{"kind": "recall", "position": -1},
                                      {"kind": "recall", "position": 30},
                                      {"kind": "recall", "position": 1}])
    r = await admin_client.post(CHAT, json={"message": "what did I just ask?",
                                            "history": _conversation(4)})
    assert "previous question" in r.json()["answer"] and "“question 4”" in r.json()["answer"]
    r = await admin_client.post(CHAT, json={"message": "what was my 30th question?",
                                            "history": _conversation(4)})
    assert r.json()["kind"] == "cannot_answer"
    assert "only 4 earlier questions" in r.json()["answer"]
    r = await admin_client.post(CHAT, json={"message": "what was my first question?"})
    assert r.json()["kind"] == "cannot_answer" and "first question" in r.json()["answer"]
    assert not calls["fixed"], "English replies are not re-rendered"


def test_a_long_earlier_answer_is_quoted_in_part():
    from app.api.v1.assistant_chat import _excerpt
    long = "\n".join(f"| row {i} | {'x' * 40} |" for i in range(40))
    got = _excerpt(long)
    assert len(got) < 700 and got.endswith("…")
    assert all(line.startswith("| row") for line in got.split("\n\n")[0].splitlines()), \
        "cut at a line break, not mid-row"


def test_the_planner_is_offered_recall_conversation():
    assert assistant_llm._RECALL_TOOL in assistant_llm._session_tools(None)
    assert assistant_llm._SESSION_TOOL_KINDS["recall_conversation"] == "recall"


# ── ISS-0073: who is asking ──────────────────────────────────────────────────

from sqlalchemy import text as _sql  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402


async def _make_me_a_director_and_finance_bp(test_engine, uid):
    from app.models.department import Department
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(name=f"Oversea {uuid.uuid4().hex[:4]}", code=f"OV{uuid.uuid4().hex[:4]}")
        db.add(dept)
        await db.flush()
        await db.execute(_sql("UPDATE users SET department_id = :d WHERE id = :u"),
                         {"d": dept.id, "u": uid})
        await db.execute(_sql("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_bp')"),
                         {"u": uid})
        await db.execute(_sql(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, 'gm', :u, false)"), {"d": str(dept.id), "u": str(uid)})
        await db.commit()
        return dept


async def _undo(test_engine, dept):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(_sql("DELETE FROM approval_dept_routing WHERE dept_id = :d"),
                         {"d": str(dept.id)})
        await db.commit()


async def test_who_am_i_names_the_person_and_every_role(test_engine, requester_client, monkeypatch):
    """Answered "the system cannot identify you", then roles guessed from tasks."""
    dept = await _make_me_a_director_and_finance_bp(test_engine, _uid(requester_client))
    try:
        calls = _stub(monkeypatch, plans=[{"kind": "me"}, {"kind": "me"}])
        r = await requester_client.post(CHAT, json={"message": "who am I? what roles do I have?"})
        body = r.json()
        assert body["kind"] == "me"
        text = body["answer"]
        assert "Test requester" in text and dept.name in text
        assert "Primary role: Requester" in text
        assert "Finance BP" in text
        assert "Director (set as a department's Director in Approval Routing)" in text
        assert not calls["fixed"], "an English question is answered as written"

        r = await requester_client.post(CHAT, json={"message": "我是谁？我有哪些角色？"})
        assert calls["fixed"] and "Primary role: Requester" in calls["fixed"][0]["text"], \
            "a Chinese question gets the same facts, rendered by the fixed-reply step"
    finally:
        await _undo(test_engine, dept)


async def test_the_planner_knows_who_is_asking(test_engine, requester_client, monkeypatch):
    dept = await _make_me_a_director_and_finance_bp(test_engine, _uid(requester_client))
    try:
        calls = _stub(monkeypatch, plans=[{"kind": "cannot", "reason": "too_ambiguous",
                                           "explanation": "n/a"}])
        await requester_client.post(CHAT, json={"message": "my open PRs"})
        caller = calls["plan"][0]["context"]["caller"]
        assert caller["name"] == "Test requester" and caller["primary_role"] == "Requester"
        assert {r["label"] for r in caller["additional_roles"]} == {"Finance BP", "Director"}
        note = assistant_llm._context_note(calls["plan"][0]["context"])
        assert "The person asking is Test requester" in note and "Finance BP" in note
        assert "who_am_i" in assistant_llm._PLAN_SESSION_RULES
    finally:
        await _undo(test_engine, dept)


async def test_the_task_list_is_given_the_real_roles(admin_client, monkeypatch):
    calls = _stub(monkeypatch, plans=[{"kind": "next"}])

    async def no_tasks(role, user_id):
        return []
    from app.services import guide_view
    monkeypatch.setattr(guide_view, "my_tasks", no_tasks)
    await admin_client.post(CHAT, json={"message": "我是谁？我有哪些角色？"})
    me = calls["guide"][0]["payload"]["me"]
    assert me["primary_role"] == "System Admin"
    assert "email" not in me and "never name a role from a task" in me["roles_are"]


# ── ISS-0068: the document on screen, on every detail page ───────────────────

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402


async def _seed_chain(test_engine, uid, tag=None):
    """PR → PO → GR → invoice → PA, plus an agreement with one receipt.
    The shape of PR-20260928-0001's chain in ISS-0030."""
    from app.models.agreement import PurchaseAgreement
    from app.models.agreement_receipt import AgreementReceipt
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.models.pa import PaymentApplication
    from app.models.pa_po_link import PaPoLink
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    from app.models.vendor import Vendor

    tag = tag or uuid.uuid4().hex[:6]
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = Vendor(code=f"V-{tag}", name=f"Chain Vendor {tag}", category="Services",
                   contact_name="C", contact_email="c@t.test")
        db.add(v)
        await db.flush()
        pr = PurchaseRequest(number=f"PR-CH-{tag}", title="chain", type=2, status="approved",
                             amount=Decimal("7175.17"), vendor_name=v.name, created_by=uid)
        db.add(pr)
        await db.flush()
        po = PurchaseOrder(number=f"PO-CH-{tag}", title="chain", type=2, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("7175.17"),
                           total=Decimal("7175.17"), status="partially_received",
                           created_by=uid, pr_id=pr.id, pr_number=pr.number)
        db.add(po)
        await db.flush()
        pr.po_id, pr.po_number = po.id, po.number
        gr = GoodsReceipt(number=f"GR-CH-{tag}", title="g", po_id=po.id, po_number=po.number,
                          vendor_id=v.id, vendor_name=v.name, gr_type="physical",
                          procurement_type=2, status="collected", created_by=uid)
        cancelled_gr = GoodsReceipt(number=f"GR-CHX-{tag}", title="g", po_id=po.id,
                                    po_number=po.number, vendor_id=v.id, vendor_name=v.name,
                                    gr_type="physical", procurement_type=2,
                                    status="cancelled", created_by=uid)
        db.add_all([gr, cancelled_gr])
        await db.flush()
        inv = Invoice(internal_ref=f"INV-CH-{tag}", vendor_invoice_number=f"V{tag}",
                      vendor_id=v.id, vendor_name=v.name, amount=Decimal("7175.17"),
                      tax_amount=Decimal("0"), total_amount=Decimal("7175.17"),
                      invoice_date=date(2026, 10, 1), due_date=date(2026, 11, 1),
                      status="matched", line_items=[], uploaded_by=uid, po_id=po.id,
                      po_number=po.number, gr_id=gr.id, gr_ids=[str(gr.id)])
        db.add(inv)
        await db.flush()
        pa = PaymentApplication(pa_number=f"PA-CH-{tag}", title="p", po_id=po.id,
                                po_number=po.number, vendor_id=v.id, vendor_name=v.name,
                                invoice_ids=[str(inv.id)], gr_ids=[str(gr.id)],
                                subtotal=Decimal("7175.17"),
                                payment_amount=Decimal("7175.17"), status="approved",
                                created_by=uid)
        db.add(pa)
        await db.flush()
        db.add(PaPoLink(pa_id=pa.id, po_id=po.id, po_number=po.number))
        agr = PurchaseAgreement(number=f"AGR-CH-{tag}", title="a", agreement_type="house_account",
                                vendor_id=v.id, vendor_name=v.name, valid_from=date(2026, 1, 1),
                                valid_to=date(2026, 12, 31), status="active", created_by=uid)
        db.add(agr)
        await db.flush()
        receipt = AgreementReceipt(agreement_id=agr.id, receipt_date=date(2026, 8, 4),
                                   receipt_ref=f"R{tag}", amount=Decimal("200"),
                                   tax_amount=Decimal("8.48"), total_amount=Decimal("208.48"),
                                   received_by=uid, created_by=uid, status="open")
        db.add(receipt)
        await db.commit()
        return SimpleNamespace(tag=tag, vendor=v, pr=pr, po=po, gr=gr, cancelled_gr=cancelled_gr,
                               inv=inv, pa=pa, agr=agr, receipt=receipt)


def _capture_context(monkeypatch) -> list:
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        seen.append(context)
        return {"kind": "cannot", "reason": "x", "explanation": "n/a"}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    return seen


async def test_a_gr_page_is_recognised(test_engine, admin_client, monkeypatch):
    """GR-20261020-0001: "这张收货单是哪个 PO 的？收了多少？" was refused twice."""
    c = await _seed_chain(test_engine, _uid(admin_client))
    seen = _capture_context(monkeypatch)
    await admin_client.post(CHAT, json={
        "message": "这张收货单是哪个 PO 的？收了多少？",
        "context": {"app": "epms", "doc_type": "gr", "doc_id": str(c.gr.id)}})
    ctx = seen[0]
    assert ctx["doc_number"] == c.gr.number and ctx["doc_entity"] == "goods_receipt"
    assert ctx["doc_lines"] == f"gr_line filtered on receipt.number = {c.gr.number}"
    note = assistant_llm._context_note(ctx)
    assert f"query on goods_receipt filtered on number = {c.gr.number}" in note
    assert "explain_workflow does not apply" in note


async def test_invoice_agreement_and_receipt_pages_are_recognised(
        test_engine, admin_client, monkeypatch):
    """The invoice page said it had no invoice data, the agreement page asked
    for the agreement to be opened, the receipt page was read as a GR."""
    from tests.test_assistant_iss_fixes import _grant_agreement_read
    await _grant_agreement_read(test_engine, "system_admin")   # as in production
    c = await _seed_chain(test_engine, _uid(admin_client))
    seen = _capture_context(monkeypatch)
    for doc_type, doc_id in (("invoice", c.inv.id), ("agreement", c.agr.id),
                             ("agreement_receipt", c.receipt.id)):
        await admin_client.post(CHAT, json={
            "message": "这张单是什么情况？",
            "context": {"app": "epms", "doc_type": doc_type, "doc_id": str(doc_id)}})
    inv, agr, rcpt = seen
    assert (inv["doc_entity"], inv["doc_number_field"], inv["doc_number"]) == (
        "invoice", "internal_ref", c.inv.internal_ref)
    assert (agr["doc_entity"], agr["doc_number"]) == ("purchase_agreement", c.agr.number)
    assert rcpt["doc_entity"] == "agreement_receipt"
    assert rcpt["doc_filters"] == [{"field": "receipt_ref", "value": c.receipt.receipt_ref},
                                   {"field": "agreement.number", "value": c.agr.number}]
    note = assistant_llm._context_note(rcpt)
    assert (f"query on agreement_receipt filtered on receipt_ref = {c.receipt.receipt_ref} "
            f"and agreement.number = {c.agr.number}") in note


async def test_a_page_document_the_caller_cannot_see_adds_nothing(
        test_engine, admin_client, requester_client, monkeypatch):
    c = await _seed_chain(test_engine, _uid(admin_client))   # someone else's
    seen = _capture_context(monkeypatch)
    for doc_type, doc_id in (("gr", c.gr.id), ("invoice", c.inv.id),
                             ("agreement", c.agr.id), ("agreement_receipt", c.receipt.id)):
        await requester_client.post(CHAT, json={
            "message": "这张单是什么情况？",
            "context": {"app": "epms", "doc_type": doc_type, "doc_id": str(doc_id)}})
    for ctx in seen:
        assert "doc_number" not in ctx and "doc_filters" not in ctx


async def test_the_planner_can_query_what_the_note_names(test_engine, admin_client):
    """The filters handed to the planner are valid queries returning the document."""
    from tests.test_assistant_iss_fixes import _grant_agreement_read
    await _grant_agreement_read(test_engine, "system_admin")
    c = await _seed_chain(test_engine, _uid(admin_client))
    for entity, where in (
        ("goods_receipt", [{"field": "number", "op": "eq", "value": c.gr.number}]),
        ("gr_line", [{"field": "receipt.number", "op": "eq", "value": c.gr.number}]),
        ("invoice", [{"field": "internal_ref", "op": "eq", "value": c.inv.internal_ref}]),
        ("purchase_agreement", [{"field": "number", "op": "eq", "value": c.agr.number}]),
        ("agreement_receipt", [{"field": "receipt_ref", "op": "eq", "value": c.receipt.receipt_ref},
                               {"field": "agreement.number", "op": "eq", "value": c.agr.number}]),
    ):
        r = await admin_client.post("/api/v1/assistant/query", json={
            "entity": entity, "metrics": ["count"], "where": where})
        assert r.status_code == 200, (entity, r.text)
        expected = 0 if entity == "gr_line" else 1
        assert r.json()["matched_rows"] == expected, entity


# ── ISS-0030: from requisition to payment ─────────────────────────────────────

import re  # noqa: E402

from app.services import document_chain  # noqa: E402


async def _chain_for(test_engine, uid, role, doc_type, number, *, perms=None):
    from app.core.access_scope import build_scope
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        scope = await build_scope(db, {"sub": str(uid), "role": role})
        if perms:
            scope = {**scope, "perms": {**scope["perms"], **perms}}
        return await document_chain.build(db, scope, doc_type, number)


async def test_the_whole_chain_from_the_pr(test_engine, admin_client):
    """PR-20260928-0001: only the PR's approval was told, then "it has entered
    the payment stage". Every step with its status."""
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    chain = await _chain_for(test_engine, uid, "system_admin", "pr", c.pr.number)
    assert chain["found"] and chain["purchase_request"]["number"] == c.pr.number
    (order,) = chain["orders"]
    po = order["purchase_order"]
    assert (po["number"], po["status_label"]) == (c.po.number, "Partially Received")
    assert po["facts"] == {"anything_received": True, "invoiced": True, "pa_raised": True,
                           "three_way_matched": True}
    assert [g["number"] for g in order["goods_receipts"]["items"]] == [c.gr.number], \
        "the cancelled receipt is left out, as on the Document Chain"
    (inv,) = order["invoices"]["items"]
    assert (inv["number"], inv["status_label"], inv["total"], inv["on_payment_applications"]) == (
        c.inv.internal_ref, "Matched", "7175.17", [c.pa.pa_number])
    (pa,) = order["payment_applications"]["items"]
    assert (pa["number"], pa["status_label"], pa["payment"], pa["pays_invoices"]) == (
        c.pa.pa_number, "Approved", "awaiting payment", [c.inv.internal_ref])
    assert document_chain.count_documents(chain) == 5


async def test_any_document_in_the_chain_leads_to_the_same_chain(test_engine, admin_client):
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    for doc_type, number in (("po", c.po.number), ("gr", c.gr.number),
                             ("invoice", c.inv.internal_ref), ("pa", c.pa.pa_number)):
        chain = await _chain_for(test_engine, uid, "system_admin", doc_type, number)
        assert chain["asked_about"] == {"doc_type": doc_type, "number": number}
        assert chain["purchase_request"]["number"] == c.pr.number, doc_type
        assert chain["orders"][0]["purchase_order"]["number"] == c.po.number


async def test_a_paid_pa_says_paid_and_when(test_engine, admin_client):
    from app.models.pa import PaymentApplication
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pa = await db.get(PaymentApplication, c.pa.id)
        pa.status, pa.paid_at = "processed", datetime(2026, 10, 19, tzinfo=timezone.utc)
        await db.commit()
    chain = await _chain_for(test_engine, uid, "system_admin", "pa", c.pa.pa_number)
    (pa,) = chain["orders"][0]["payment_applications"]["items"]
    assert (pa["payment"], pa["paid_on"], pa["status_label"]) == ("paid", "2026-10-19", "Processed")


async def test_each_document_stays_inside_its_own_scope(
        test_engine, admin_client, requester_client):
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    # Someone else's purchase: not found, the same as absent.
    other = await _chain_for(test_engine, _uid(requester_client), "requester", "po", c.po.number)
    assert other == {"found": False, "reason": "not_found", "asked": c.po.number}
    # A kind of document the matrix keeps them out of is "not visible", not "none".
    chain = await _chain_for(test_engine, uid, "system_admin", "po", c.po.number,
                             perms={"view_invoice": False, "view_pa": False})
    order = chain["orders"][0]
    assert order["invoices"] == {"not_visible": True}
    assert order["payment_applications"] == {"not_visible": True}
    assert order["goods_receipts"]["count"] == 1
    # Asked from the receipt by someone who may not see orders: the PO is
    # "not visible", never "there is no PO".
    chain = await _chain_for(test_engine, uid, "system_admin", "gr", c.gr.number,
                             perms={"view_po": False})
    assert chain["orders"][0]["purchase_order"] == {"not_visible": True}


async def test_an_agreement_purchase_follows_the_agreement(test_engine, admin_client):
    from app.models.invoice import Invoice
    from app.models.pa import PaymentApplication
    from tests.test_assistant_iss_fixes import _grant_agreement_read
    await _grant_agreement_read(test_engine, "system_admin")
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        inv = Invoice(internal_ref=f"INV-AG-{c.tag}", vendor_invoice_number=f"A{c.tag}",
                      vendor_id=c.vendor.id, vendor_name=c.vendor.name, amount=Decimal("208.48"),
                      tax_amount=Decimal("0"), total_amount=Decimal("208.48"),
                      invoice_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
                      status="matched", line_items=[], uploaded_by=uid,
                      agreement_id=c.agr.id, agreement_number=c.agr.number)
        db.add(inv)
        await db.flush()
        db.add(PaymentApplication(pa_number=f"PA-AG-{c.tag}", title="p", agreement_id=c.agr.id,
                                  agreement_number=c.agr.number, vendor_id=c.vendor.id,
                                  vendor_name=c.vendor.name, invoice_ids=[str(inv.id)],
                                  gr_ids=[], subtotal=Decimal("208.48"),
                                  payment_amount=Decimal("208.48"), status="in_review",
                                  created_by=uid))
        await db.commit()
    chain = await _chain_for(test_engine, uid, "system_admin", "pa", f"PA-AG-{c.tag}")
    assert chain["orders"] == []
    (agr,) = chain["agreements"]
    assert agr["agreement"]["number"] == c.agr.number
    assert [i["number"] for i in agr["invoices"]["items"]] == [f"INV-AG-{c.tag}"]
    (pa,) = agr["payment_applications"]["items"]
    assert pa["payment"] == "not yet — still in approval"


async def test_the_chat_route_and_the_page_document(test_engine, admin_client, monkeypatch):
    uid = _uid(admin_client)
    c = await _seed_chain(test_engine, uid)
    calls = _stub(monkeypatch, plans=[
        {"kind": "trace", "doc_type": "pr", "doc_number": c.pr.number},
        {"kind": "trace", "doc_type": "gr"},
        {"kind": "trace", "doc_type": "po", "doc_number": "PO-NOPE-1"}])
    r = await admin_client.post(CHAT, json={"message": f"{c.pr.number} 从请购到付款的完整进度是怎样的？"})
    body = r.json()
    assert body["kind"] == "document_chain" and body["sources"]["documents"] == 5
    assert calls["chain"][0]["chain"]["orders"][0]["purchase_order"]["number"] == c.po.number
    # On the GR's own page, no number given.
    r = await admin_client.post(CHAT, json={
        "message": "这张收货单后面开票付款了吗？",
        "context": {"app": "epms", "doc_type": "gr", "doc_id": str(c.gr.id)}})
    assert calls["chain"][1]["chain"]["asked_about"]["number"] == c.gr.number
    r = await admin_client.post(CHAT, json={"message": "PO-NOPE-1 到哪一步了？"})
    assert r.json()["kind"] == "cannot_answer" and "PO-NOPE-1" in calls["fixed"][-1]["text"]
    assert "trace_document" in assistant_llm._PLAN_CHAIN_RULES
    assert assistant_llm._TRACE_TOOL in assistant_llm._session_tools(None)


_TREE = (Path(document_chain.__file__).resolve().parents[3]
         / "epms" / "src" / "components" / "shared" / "DocumentChainTree.tsx")


def test_status_names_match_the_document_chain():
    import pytest
    if not _TREE.exists():
        pytest.skip("epms frontend not mounted in this container")
    text = _TREE.read_text(encoding="utf-8")
    for const, kind in (("GR_STATUS_LABELS", "gr"), ("INV_STATUS_LABELS", "invoice"),
                        ("PA_STATUS_LABELS", "pa"), ("PO_STATUS_LABELS", "po")):
        block = text.split(f"const {const}")[1].split("\n}")[0]
        assert dict(re.findall(r"^\s+([a-z_]+):\s*'([^']+)',", block, re.M)) == \
            document_chain.STATUS_LABELS[kind], const


# ── ISS-0037 / ISS-0069: one message, several questions ──────────────────────

_FOUR = "PO 是什么状态，INV 金额多少，再帮我写一首诗，最后供应商联系人是谁"


def _q(entity, field, value, select):
    return {"kind": "query", "query": {"entity": entity, "select": select, "where": [
        {"field": field, "op": "eq", "value": value, "because": "asked"}]}}


async def test_each_part_is_planned_and_answered_on_its_own(test_engine, admin_client, monkeypatch):
    """Only the PO was queried; the invoice amount was "not in the query
    result" although, asked alone, it is answered."""
    c = await _seed_chain(test_engine, _uid(admin_client))
    parts = [f"{c.po.number} 是什么状态？", f"{c.inv.internal_ref} 金额多少？", "帮我写一首诗",
             "供应商联系人是谁？"]
    calls = _stub(monkeypatch, plans=[
        {"kind": "split", "parts": parts},
        _q("purchase_order", "number", c.po.number, ["number", "status"]),
        _q("invoice", "internal_ref", c.inv.internal_ref, ["internal_ref", "total_amount"]),
        {"kind": "cannot", "reason": "not_a_data_question", "explanation": "写诗不在我能做的范围内。"},
    ])
    r = await admin_client.post(CHAT, json={"message": _FOUR})
    body = r.json()
    assert body["kind"] == "parts" and len(body["parts"]) == assistant_llm.MAX_PARTS == 3
    # Each part was planned as itself, knowing it is one of several.
    assert [p["message"] for p in calls["plan"][1:]] == parts[:3]
    assert calls["plan"][2]["context"]["question_part"] == {"index": 2, "of": 4, "whole": _FOUR}
    # ...and narrated with its own question, so nothing reads as "missing".
    assert [n["message"] for n in calls["narrate"]] == parts[:2]
    assert calls["narrate"][1]["result"]["rows"][0]["internal_ref"] == c.inv.internal_ref
    assert [p["kind"] for p in body["parts"]] == ["answer", "answer", "cannot_answer"]
    assert body["parts"][1]["query"]["entity"] == "invoice", "its own receipt for export"
    text = body["answer"]
    assert f"**1. {parts[0]}**" in text and f"**3. {parts[2]}**" in text
    # A part that is no data question gets the code-written refusal naming what
    # can be answered (ISS-0022), never the model's own "ask someone" text.
    assert "写诗不在我能做的范围内" not in text
    assert body["parts"][2]["reason"] == "no_rule"
    assert "I have no rule or data that answers that" in body["parts"][2]["answer"]
    # The fourth part is named as not looked up — never as having no data.
    assert body["not_looked_up"] == [parts[3]]
    assert "这次没有去查" in text and parts[3] in text


async def test_a_part_cannot_reach_what_the_person_cannot(
        test_engine, admin_client, requester_client, monkeypatch):
    c = await _seed_chain(test_engine, _uid(admin_client))   # someone else's
    calls = _stub(monkeypatch, plans=[
        {"kind": "split", "parts": ["a?", "b?"]},
        _q("purchase_order", "number", c.po.number, ["number", "status"]),
        _q("invoice", "internal_ref", c.inv.internal_ref, ["internal_ref", "total_amount"]),
    ])
    await requester_client.post(CHAT, json={"message": "a? and b?"})
    assert [n["result"]["rows"] for n in calls["narrate"]] == [[], []]


async def test_a_part_is_never_split_again_and_one_part_is_just_the_question(
        admin_client, monkeypatch):
    assert assistant_llm._SPLIT_TOOL in assistant_llm._session_tools({})
    assert assistant_llm._SPLIT_TOOL not in assistant_llm._session_tools(
        {"question_part": {"index": 1, "of": 2, "whole": "x"}})
    note = assistant_llm._context_note({"question_part": {"index": 2, "of": 3, "whole": "x y z"}})
    assert "part 2 of 3" in note and "«x y z»" in note

    calls = _stub(monkeypatch, plans=[
        {"kind": "split", "parts": ["only one?"]},
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "which one?"},
        {"kind": "split", "parts": ["x?", "y?"]},
        {"kind": "split", "parts": ["x1?", "x2?"]},       # a part asking to split again
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "y?"},
    ])
    r = await admin_client.post(CHAT, json={"message": "only one?"})
    assert r.json()["kind"] == "cannot_answer" and r.json()["answer"] == "which one?"
    assert calls["plan"][1]["context"]["question_part"]["of"] == 1
    r = await admin_client.post(CHAT, json={"message": "x? y?"})
    assert [p["kind"] for p in r.json()["parts"]] == ["cannot_answer", "cannot_answer"]
    assert len(calls["plan"]) == 5, "no recursion"


async def test_one_part_failing_keeps_the_others(admin_client, monkeypatch):
    calls = _stub(monkeypatch, plans=[
        {"kind": "split", "parts": ["what is waiting for me?", "who am I?"]},
        {"kind": "next"}, {"kind": "me"}])

    async def down(message, payload):
        raise assistant_llm.LlmUnavailable("Could not reach the model")
    monkeypatch.setattr(assistant_llm, "narrate_guide", down)
    r = await admin_client.post(CHAT, json={"message": "what is waiting for me, and who am I?"})
    body = r.json()
    assert body["parts"][0]["kind"] == "cannot_answer"
    assert "could not be answered just now" in body["parts"][0]["answer"]
    assert body["parts"][1]["kind"] == "me" and "System Admin" in body["parts"][1]["answer"]
    assert len(calls["plan"]) == 3


def test_an_unplanned_part_is_said_to_be_not_looked_up():
    rules = assistant_llm._NARRATE_SYSTEM
    assert "was not looked up" in rules and "Never say it does not exist" in rules
    assert "split_question" in assistant_llm._PLAN_PARTS_RULES
