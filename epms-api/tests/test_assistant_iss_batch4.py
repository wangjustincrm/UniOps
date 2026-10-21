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
