"""Assistant, round 2 follow-up (asst2/h, 11-11): the data points left over.

Every chat test stubs the planner to pick the wrong thing and the narrator to
write what production wrote (a wrong total, "数据中并未显示有贷项", "下单方式…
没有记录"), and checks the reply is right AND does not carry the wrong
sentence — the reply is written by code, or the wrong sentence is taken out.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.approval import ApprovalEvent
from app.models.freight import FreightCarrier
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.user import User
from app.models.vendor import Vendor
from app.services import assistant_llm, fact_replies
from tests.test_assistant_iss_batch2 import _uid

pytestmark = pytest.mark.asyncio
CHAT = "/api/v1/assistant/chat"


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _tag() -> str:
    return uuid.uuid4().hex[:6].upper()


def _stub(monkeypatch, plans, narration="ok") -> list:
    """The planner returns `plans` in turn (the last one repeats); the
    narrator writes `narration`."""
    plans = plans if isinstance(plans, list) else [plans]
    asked: list = []
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        asked.append(message)
        plan = plans[min(len(asked) - 1, len(plans) - 1)]
        return plan if "kind" in plan else {"kind": "query", "query": plan}

    async def fake_narrate(message, q, result):
        seen.append({"message": message, "query": q, "result": result})
        return {"text": narration(result) if callable(narration) else narration, "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    return seen


async def _ask(client, message: str, history=None) -> dict:
    r = await client.post(CHAT, json={"message": message, "history": history or []})
    assert r.status_code == 200, r.text
    return r.json()


async def _vendor(db, name=None, **kw) -> Vendor:
    v = Vendor(code=f"V{_tag()}", name=name or f"Vendor {_tag()}", category="Services",
               contact_name="A", contact_email="a@t.test", **kw)
    db.add(v)
    await db.flush()
    return v


async def _credits(test_engine, vendor: str, specs) -> None:
    """specs: (status, remaining, currency[, credit_date])."""
    async with test_engine.begin() as conn:
        for n, spec in enumerate(specs):
            status, remaining, cur = spec[:3]
            when = spec[3] if len(spec) > 3 else date(2026, 10, 1 + n % 20)
            await conn.execute(sa.text(
                "INSERT INTO vendor_credits (id, credit_number, vendor_name, vendor_credit_number,"
                " credit_date, currency, amount, tax_amount, total_amount, applied_amount,"
                " remaining_amount, status, source, uploaded_at) VALUES (:id, :num, :v, :ref,"
                " :d, :cur, :t, 0, :t, 0, :t, :st, 'manual', now())"),
                {"id": uuid.uuid4(), "num": f"VC-{uuid.uuid4().hex[:8]}-{n:02d}", "v": vendor,
                 "ref": f"CA{n:04d}", "d": when, "cur": cur, "t": Decimal(remaining),
                 "st": status})


# Amazon on 23/10 (dev4 snapshot, before VC-20261026-0001): 14 Available CAD
# credits, Remaining CAD 570.11 on Vendor Credits → Available.
_AMAZON_14 = ["0.03"] * 7 + ["0.04", "0.04", "0.02", "0.01", "4.35", "38.41", "527.03"]


# ── ISS-0021 ④: a vendor's available credits, summed by the query ────────────

async def test_a_vendors_available_credits_are_counted_and_summed_by_code(
        test_engine, admin_client, monkeypatch):
    name = f"Amazon {_tag()}"
    specs = [("available", r, "CAD") for r in _AMAZON_14]
    await _credits(test_engine, name, specs + [("void", "99.00", "CAD")])
    expected = Decimal("570.11")
    assert len(specs) == 14 and sum(Decimal(r) for r in _AMAZON_14) == expected
    # The planner goes to agreements (19/10) and the narrator adds up wrongly (23/10).
    seen = _stub(monkeypatch, {"entity": "purchase_agreement", "select": ["number"],
                               "where": [{"field": "vendor_name", "op": "eq", "value": name,
                                          "because": "v"}]},
                 narration="共 14 笔可用贷项，合计 CAD 600.20。")
    for _ in range(3):
        answer = (await _ask(admin_client, f"供应商 {name} 还有多少可用贷项？"))["answer"]
        assert f"{name} 当前可用（Available）的供应商贷项共 14 张" in answer
        assert f"CAD {expected:,.2f}" in answer
        assert "600.20" not in answer and "已作废（Void） 1 张" in answer
    assert not seen, "written by code, the narrator is never asked"
    english = (await _ask(admin_client, f"How much available vendor credit does {name} have?"))
    assert f"{name} has 14 Available vendor credits, Remaining in total CAD {expected:,.2f}" \
        in english["answer"]


async def test_a_vendor_whose_credits_are_all_void_has_none_available(
        test_engine, admin_client, monkeypatch):
    name = f"Dairy Farmers {_tag()}"
    await _credits(test_engine, name, [("void", "10", "CAD")] * 3)
    _stub(monkeypatch, {"kind": "cannot", "reason": "no_data", "explanation": "no agreement"},
          narration="没有找到该供应商的采购协议。")
    answer = (await _ask(admin_client, f"供应商 {name} 还有多少可用贷项？"))["answer"]
    assert f"{name} 当前没有可用（Available）的供应商贷项，可抵扣金额为 0" in answer
    assert "已作废（Void） 3 张" in answer and "采购协议" not in answer


async def test_a_rule_question_about_credits_is_not_taken_for_figures(admin_client, monkeypatch):
    # "录入手工贷项需要什么？" stays with its rule card; the netting rule question
    # names no vendor and no PA.
    async def nothing(*a, **k):
        raise AssertionError("not a figures question")

    monkeypatch.setattr(fact_replies, "_credit_figures", nothing)
    answer = (await _ask(admin_client, "录入手工贷项需要什么？"))["answer"]
    assert "备注" in answer
    assert await fact_replies.answer(None, {}, "付款时贷项是怎么抵扣的？", None) is None


# ── ISS-0056: an unpaid PA after credits ─────────────────────────────────────

async def _unpaid_pa(test_engine, client, vendor_name: str) -> str:
    uid = _uid(client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db, name=vendor_name)
        po = PurchaseOrder(number=f"PO-P{_tag()}-2610-01", title="t", type=2, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("18.14"),
                           total=Decimal("18.14"), status="issued", created_by=uid)
        db.add(po)
        await db.flush()
        number = f"PA-2026{uuid.uuid4().int % 10**4:04d}-{uuid.uuid4().int % 10**4:04d}"
        db.add(PaymentApplication(pa_number=number, title="t", vendor_id=v.id, po_id=po.id,
                                  po_number=po.number,
                                  vendor_name=v.name, subtotal=Decimal("18.14"),
                                  payment_amount=Decimal("18.14"), currency="CAD",
                                  created_by=uid, status="approved"))
        await db.commit()
    return number


async def test_an_unpaid_pa_after_credits_says_netted_at_payment_and_lists_them(
        test_engine, admin_client, monkeypatch):
    name = f"Amazon {_tag()}"
    pa = await _unpaid_pa(test_engine, admin_client, name)
    await _credits(test_engine, name, [("available", "527.03", "CAD"),
                                       ("available", "38.41", "CAD"),
                                       ("available", "5.00", "USD"),
                                       ("pending_review", "1.00", "CAD")])
    # What production did: payments looked up, "no credits in the data".
    seen = _stub(monkeypatch, {"entity": "payment_record", "select": ["amount"],
                               "where": [{"field": "pa_number", "op": "eq", "value": pa,
                                          "because": "pa"}]},
                 narration="18.14 CAD，数据中并未显示有贷项。")
    answer = (await _ask(admin_client, f"{pa} 扣掉贷项后实际付多少？"))["answer"]
    assert f"{pa}（{name}）金额 CAD 18.14，状态 Approved，尚未付款。贷项不在 PA 上扣" in answer
    assert "贷项不录在付款申请（PA）上，而是在执行付款时抵扣" in answer, "the VC-005 rule"
    assert f"{name} 当前可用（Available）的 CAD 供应商贷项共 2 张" in answer
    assert "CAD 565.44" in answer and "USD" not in answer, "same currency only"
    assert "并未显示有贷项" not in answer and "没有任何可用" not in answer
    assert not seen
    # The follow-up of conversation 1, by name and without one.
    history = [{"role": "user", "text": f"{pa} 扣掉贷项后实际付多少？"},
               {"role": "assistant", "text": answer}]
    # By name: the vendor's credits in every currency, each summed apart.
    again = (await _ask(admin_client, f"那 {name} 有可用的贷项吗？", history))["answer"]
    assert "供应商贷项共 3 张，可抵扣余额（Remaining）合计 CAD 565.44；USD 5.00" in again
    # Named by nothing: the PA's vendor, in the PA's currency.
    again = (await _ask(admin_client, "那有可用的贷项吗？", history))["answer"]
    assert "的 CAD 供应商贷项共 2 张" in again and "CAD 565.44" in again
    assert "没有任何可用" not in again
    english = (await _ask(admin_client, f"How much is actually paid on {pa} after credits?"))
    assert "not paid yet. Credits are not deducted on the PA" in english["answer"]


async def test_a_pa_looked_for_among_payments_is_said_to_exist(test_engine, admin_client,
                                                                monkeypatch):
    """code_answers.found_elsewhere: a payments query for an unpaid PA finds no
    row. The reply says the PA exists — and the narrator's "未找到" goes."""
    pa = await _unpaid_pa(test_engine, admin_client, f"Vendor {_tag()}")
    _stub(monkeypatch, {"entity": "payment_record", "select": ["amount", "payment_date"],
                        "where": [{"field": "pa_number", "op": "eq", "value": pa,
                                   "because": "pa"}]},
          narration=f"未找到付款申请 {pa} 的付款记录。请联系财务。")
    answer = (await _ask(admin_client, f"{pa} 的付款记录是什么？"))["answer"]
    assert f"{pa} 是存在的：付款申请，状态 Approved，已批准、尚未付款" in answer
    assert "未找到" not in answer and "请联系财务" in answer


# ── ISS-0045: how a PO was placed ────────────────────────────────────────────

async def _placed_po(test_engine, client, method: str, **kw) -> tuple[str, str]:
    tag = _tag()
    who = f"Forough {tag}"
    async with _factory(test_engine)() as db:
        user = User(email=f"f{tag.lower()}@t.test", hashed_password=hash_password("x"),
                    full_name=who, role="procurement_officer")
        db.add(user)
        v = await _vendor(db, name=f"Hewitt {tag}")
        po = PurchaseOrder(number=f"PO-H{tag}-2610-01", title="t", type=2, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("1"), total=Decimal("1"),
                           status="issued", created_by=_uid(client), place_order_method=method,
                           placed_at=datetime(2026, 10, 19, 14, 52, tzinfo=timezone.utc), **kw)
        db.add(po)
        await db.flush()
        db.add(ApprovalEvent(document_type="po", document_id=po.id, document_number=po.number,
                             action="issue", actor_id=user.id, actor_role="procurement_officer",
                             comment="Order placed online"))
        await db.commit()
    return po.number, who


async def test_how_a_po_was_placed_is_answered_whole_from_its_record(
        test_engine, admin_client, monkeypatch):
    number, who = await _placed_po(test_engine, admin_client, "online")
    seen = _stub(monkeypatch, {"entity": "purchase_order", "select": ["vendor_name"],
                               "where": [{"field": "number", "op": "eq", "value": number,
                                          "because": "po"}]},
                 narration="下单方式（邮件或在线）在系统中没有记录。发给了 Hewitt。")
    for _ in range(2):
        out = await _ask(admin_client, f"{number} 是邮件下单还是在线下单？发给了谁？什么时候发的？")
        answer = out["answer"]
        assert out["kind"] != "parts", "one answer, not three parts"
        # 14:52 UTC is 10:52 in Toronto, the day the History tab shows.
        assert f"{number} 是在线下单：19/10/2026 10:52 由 {who}（Procurement Officer）在 EPMS 确认下单" \
            in answer
        assert "没有发送邮件，所以没有收件人和发送时间" in answer
        assert "没有记录" not in answer and "Hewitt" not in answer
    assert not seen
    english = await _ask(admin_client, f"Was {number} placed by email or online? Sent to whom?")
    assert f"{number} was placed online by {who} (Procurement Officer) on 19/10/2026 10:52" \
        in english["answer"]


async def test_an_email_placed_po_names_its_recipient_and_time(test_engine, admin_client,
                                                               monkeypatch):
    number, who = await _placed_po(test_engine, admin_client, "email",
                                   place_order_email_to="orders@hewitt.test")
    _stub(monkeypatch, {"kind": "cannot", "reason": "no_data", "explanation": "x"},
          narration="没有记录。")
    answer = (await _ask(admin_client, f"{number} 是邮件下单还是在线下单？发给了谁？什么时候发的？"))
    assert (f"{number} 是邮件下单：19/10/2026 10:52 由 {who}（Procurement Officer）在 EPMS 下单，"
            f"订单邮件当时（19/10/2026 10:52，工厂本地时间）发送到 orders@hewitt.test") \
        in answer["answer"]
    assert "也不保存邮件主题" in answer["answer"]


# ── ISS-0021 ③: shipments and carriers, asked together ──────────────────────

async def test_shipments_and_carriers_asked_together_are_both_answered(
        test_engine, admin_client, monkeypatch):
    tag = _tag()
    async with _factory(test_engine)() as db:
        for code in ("UPS", "FEDEX", "PURO"):
            v = await _vendor(db, name=f"{code} {tag}")
            db.add(FreightCarrier(vendor_id=v.id, code=f"{code}{tag}", name=f"{code} Canada {tag}"))
        await db.commit()
        total = (await db.execute(sa.text("SELECT count(*) FROM freight_carriers"))).scalar()
    # Part 2, were it planned, would go to invoices (20/10); it never is.
    seen = _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref"]},
                 narration="最近没有快递单的记录，无法统计承运商数量。")
    out = await _ask(admin_client, "最近有哪些快递单？有几个承运商？")
    assert out["kind"] == "parts" and len(out["parts"]) == 2
    first, second = out["parts"]
    assert first["question"] == "最近有哪些快递单？" and seen[0]["message"] == "最近有哪些快递单？"
    assert second["question"] == "有几个承运商？"
    assert f"Freight → Carriers 里共有 {total} 个承运商" in second["answer"]
    assert f"UPS Canada {tag}" in second["answer"] and "无法统计" not in second["answer"]
    assert len(seen) == 1, "the carrier count is not narrated"


# ── ISS-0050: as many rows listed as were found ──────────────────────────────

async def test_a_list_the_narrator_cut_short_is_completed_by_code(test_engine, admin_client,
                                                                 monkeypatch):
    tag = _tag()
    async with _factory(test_engine)() as db:
        names = [f"Inactive {tag} {c}" for c in "ABC"]
        for n in names:
            await _vendor(db, name=n, is_active=False)
        await db.commit()
    plan = {"entity": "vendor", "select": ["name", "is_active"],
            "where": [{"field": "is_active", "op": "eq", "value": False, "because": "inactive"},
                      {"field": "name", "op": "like", "value": f"Inactive {tag}", "because": "t"}],
            "order_by": {"field": "name"}}
    _stub(monkeypatch, plan, narration=f"共 3 家：\n| 供应商 |\n|---|\n| {names[0]} |\n| {names[1]} |")
    answer = (await _ask(admin_client, "有哪些供应商已经停用？"))["answer"]
    assert "上面漏列了 1 条，补在这里（共 3 条）：" in answer
    assert answer.count(names[2]) == 1 and answer.count(names[0]) == 1
    # Listed in full: nothing added.
    _stub(monkeypatch, plan, narration="、".join(names))
    answer = (await _ask(admin_client, "有哪些供应商已经停用？"))["answer"]
    assert "漏列" not in answer
    # Only the count: left as it is.
    _stub(monkeypatch, plan, narration="共 3 家。")
    assert "漏列" not in (await _ask(admin_client, "有几家供应商已经停用？"))["answer"]


async def test_not_found_sentences_go_only_for_the_found_documents():
    pa = [{"doc_type": "pa", "number": "PA-20261016-0005"}]
    text = "未找到付款申请 PA-20261016-0005。PO-1 未找到。金额 18.14."
    assert fact_replies.without_not_found(text, pa) == "PO-1 未找到。金额 18.14."
    en = "No payment was found for PA-20261016-0005. Ask Finance."
    assert fact_replies.without_not_found(en, pa) == "Ask Finance."
    # By its kind, with no number; a true "no payment record" stays.
    assert fact_replies.without_not_found("未找到这份付款申请。没有付款记录。", pa) == "没有付款记录。"


async def test_placing_words_in_other_questions_do_not_take_the_open_po():
    """Round 2 misroute check: on a PO page, questions that only share a word
    with placing ("什么时候发", "Place Order") are not this PO's placing record."""
    from app.services import fact_replies as f
    for q in ["每日摘要邮件什么时候发？", "谁可以对 PO 执行 Place Order？",
              "Who can place an order?", "怎么在线下单？"]:
        assert f._RULE_ASKED.search(q) or not f._PLACED_HERE.search(q), q
    for q in ["这张 PO 是邮件下单还是在线下单？", "发给了谁？", "Who was this order sent to?"]:
        assert f._PLACED_HERE.search(q) and not f._RULE_ASKED.search(q), q
