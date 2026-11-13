"""Assistant, round 2 follow-up (asst2/g): what the code worked out overrules
what the narrator wrote.

Round 2 put the code's sentence under the reply and left the narrator's
sentence above it, so a reply could say "未匹配" and then "三单匹配：已通过".
Here every narrator is stubbed to say the OPPOSITE of the facts, word for word
as the testers saw it on production, and each test checks the reply opens with
the code's conclusion and no longer contains the wrong sentence.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.gr import GoodsReceipt, GrLineItem
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.part import Part
from app.models.po import PurchaseOrder
from app.services import answer_facts, assistant_llm, rule_verdicts, workflow_view
from tests.test_assistant_asst2_b import (_ask, _factory, _paid_pa, _po_535, _stub, _tag,
                                          _vendor)
from tests.test_assistant_iss_batch2 import _uid

# No module-wide asyncio mark: the sync tests here would take a loop of their
# own, and the session engine's connections are bound to the session loop
# (conftest marks every coroutine test; asyncio_mode is auto).
CHAT = "/api/v1/assistant/chat"


# ── The mechanism ────────────────────────────────────────────────────────────

def test_a_contradicting_sentence_is_taken_out_and_the_rest_kept():
    text = ("PO-1 的发票是 INV-1。3-way match 结果：未匹配 (Not Matched)。原因是差 22.60。\n"
            "- 结果：未匹配。其余正常\n| 三方匹配 | 未通过 |")
    out = rule_verdicts.without_sentences(text, answer_facts._NOT_MATCHED)
    assert out == "PO-1 的发票是 INV-1。\n- 其余正常"
    # A decimal point is not the end of a sentence.
    assert rule_verdicts.without_sentences(
        "Paid CAD 7,175.17 on 19/10. It was not matched.", answer_facts._NOT_MATCHED) == \
        "Paid CAD 7,175.17 on 19/10."
    assert rule_verdicts.without_sentences("无关的话。", []) == "无关的话。"


# ── ISS-0013 ①: "已经发给供应商了吗？" ──────────────────────────────────────────

async def _issued_po(test_engine, client, **kw) -> PurchaseOrder:
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = PurchaseOrder(number=f"PO-G{_tag()}-2605-03", title="t", type=2, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("1"), total=Decimal("1"),
                           status="issued", created_by=_uid(client), **kw)
        db.add(po)
        await db.commit()
        return po


async def test_issued_is_said_first_and_sent_to_the_vendor_is_taken_out(
        test_engine, admin_client, monkeypatch):
    po = await _issued_po(test_engine, admin_client, place_order_method="nc")
    _stub(monkeypatch, {"entity": "purchase_order", "select": ["number", "status"],
                        "where": [{"field": "number", "op": "eq", "value": po.number,
                                   "because": "po"}]},
          narration="是的，已经发给供应商了。供应商已收到订单。状态为已下单（Issued）。")
    answer = (await _ask(admin_client, f"{po.number} 已经发给供应商了吗？"))["answer"]
    assert answer.startswith(f"{po.number}：状态为已下单（Issued）只表示订单已下达（在 NC 下单）")
    assert "是的" not in answer and "已经发给供应商了" not in answer
    assert "供应商已收到订单" not in answer
    assert "状态为已下单（Issued）。" in answer, "the narrator's other sentence is kept"

    _stub(monkeypatch, {"entity": "purchase_order", "select": ["number", "status"],
                        "where": [{"field": "number", "op": "eq", "value": po.number,
                                   "because": "po"}]},
          narration="Yes, it has been sent to the vendor. The vendor has received it.")
    answer = (await _ask(admin_client, f"Has {po.number} been sent to the vendor?"))["answer"]
    # Since the merge with ISS-0045 (fact_replies._placement) the English wording
    # is answered whole from the PO's placing record; either way the reply opens
    # with how it was placed and never says the vendor got it.
    assert answer.startswith(po.number) and "NC" in answer.split(".")[0]
    assert "has been sent to the vendor" not in answer and "has received" not in answer


async def test_issued_from_the_approval_route_is_not_sent_either(
        test_engine, admin_client, monkeypatch):
    """The planner picks the approval history instead of a query: the same."""
    po = await _issued_po(test_engine, admin_client, place_order_method="email",
                          place_order_email_to="orders@vendor.test",
                          placed_at=datetime(2026, 5, 20, 15, tzinfo=timezone.utc))

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "workflow", "doc_type": "po", "doc_number": po.number}

    async def fake_build(db, doc_type, doc, token):
        return {"document_number": doc.number, "status": doc.status, "steps": [], "history": [],
                "steps_available": True}

    async def fake_narrate(message, view):
        return {"text": "是的，已经发给供应商了，审批已全部完成。", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(workflow_view, "build", fake_build)
    monkeypatch.setattr(assistant_llm, "narrate_workflow", fake_narrate)
    answer = (await _ask(admin_client, f"{po.number} 已经发给供应商了吗？"))["answer"]
    assert answer.startswith(f"{po.number}：EPMS 记录的是订单邮件已于2026-MAY-20 发送到 orders@vendor.test")
    assert "已经发给供应商" not in answer


# ── ISS-0059: "没有标记为部分已付，可能数据不一致" ────────────────────────────────

async def test_partially_paid_is_not_blamed_when_none_is_a_prepayment(
        test_engine, admin_client, monkeypatch):
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        inv = Invoice(internal_ref=f"INV-G{_tag()}", vendor_invoice_number="1", vendor_id=v.id,
                      vendor_name=v.name, amount=Decimal("1"), tax_amount=Decimal("0"),
                      total_amount=Decimal("1"), invoice_date=date(2026, 9, 1),
                      due_date=date(2026, 10, 1), status="matched", line_items=[],
                      uploaded_by=uid)
        db.add(inv)
        await db.flush()
        db.add(PaymentApplication(pa_number=f"PA-G{_tag()}", title="t", vendor_id=v.id,
                                  vendor_name=v.name, subtotal=Decimal("1"),
                                  payment_amount=Decimal("1"), created_by=uid,
                                  status="processed", pa_type="regular",
                                  invoice_ids=[str(inv.id)]))
        await db.commit()
    _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref", "status", "pa_type"],
                        "where": [{"field": "pa_payment_shown", "op": "eq", "value": False,
                                   "because": "mismatch"},
                                  {"field": "vendor_name", "op": "eq", "value": v.name,
                                   "because": "v"}]},
          narration="这 1 张都不是预付款 PA 的发票。系统中没有将它们标记为部分已付，这可能是数据不一致。")
    answer = (await _ask(admin_client, "这些发票里有预付款 PA 的吗？"))["answer"]
    assert "部分已付" not in answer and "可能是数据不一致" not in answer
    assert "这 1 张都不是预付款 PA 的发票。" in answer
    # Counted over the invoices this query matched, not the whole system.
    assert "1 张在普通 PA 上（应为 Paid），0 张在预付款 PA 上" in answer


# ── ISS-0036 / ISS-0016: match result and difference ─────────────────────────

@pytest.mark.parametrize("wrong", [
    "3-way match 结果：未匹配 (Not Matched)。原因是 PO 与发票差 22.60，且 PO 部分收货。",
    "三方匹配未成功，系统显示三方匹配结果为否定。",
    "3-way match 结果是未完成匹配（three_way_matched: false），因为 PO 部分收货所以不通过。",
])
async def test_three_way_matched_is_the_conclusion_and_not_matched_goes(
        test_engine, admin_client, monkeypatch, wrong):
    d = await _po_535(test_engine, admin_client)
    _stub(monkeypatch, {"entity": "purchase_order", "select": ["number", "three_way_matched"],
                        "where": [{"field": "number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]}, narration=wrong)
    answer = (await _ask(admin_client, f"{d['po']} 的 3-way match 结果怎样？"))["answer"]
    assert answer.startswith(f"系统核对 {d['po']}")
    assert "三单匹配：已通过（INV-3WM-006" in answer.split("\n\n")[0]
    for words in ("未匹配", "未成功", "否定", "未完成匹配", "不通过", "false"):
        assert words not in answer, (words, answer)


async def test_not_matched_in_english_goes_too(test_engine, admin_client, monkeypatch):
    d = await _po_535(test_engine, admin_client)
    _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref", "status"],
                        "where": [{"field": "po_number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]},
          narration="The PO is not three-way matched. Matching failed because of the 22.60 gap.")
    answer = (await _ask(admin_client, f"What is the 3-way match result of {d['po']}?"))["answer"]
    assert answer.startswith(f"Code check of {d['po']}") and "Three-way matched (INV-3WM-006" in answer
    assert "not three-way matched" not in answer and "failed" not in answer


async def test_no_difference_goes_when_code_finds_one(test_engine, admin_client, monkeypatch):
    d = await _po_535(test_engine, admin_client)
    _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref", "status", "variance"],
                        "where": [{"field": "po_number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]},
          narration="没有超过。差异为 0.00，与采购订单完全匹配。发票状态为已支付。")
    answer = (await _ask(admin_client, f"{d['po']} 的发票金额有没有超过 PO？差异多少？"))["answer"]
    # Two questions, answered as two parts: in each, the code's check comes first.
    for part in answer.split("**")[2::2]:
        assert part.strip().startswith(f"系统核对 {d['po']}"), part
    assert "税前比 PO 少 CAD 20.00（含税少 CAD 22.60）" in answer
    assert "未开票的行：第 3 行 Transport fee CMB（CAD 20.00）" in answer
    assert "0.00，" not in answer and "完全匹配" not in answer and "没有超过。" not in answer
    _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref"],
                        "where": [{"field": "po_number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]},
          narration="The invoice matches exactly, no difference. Difference of 0.00.")
    answer = (await _ask(admin_client, f"Is the invoice for {d['po']} over the PO?"))["answer"]
    assert "no difference" not in answer and "matches exactly" not in answer
    assert "Difference of 0.00" not in answer and "CAD 20.00 below the PO" in answer


# ── ISS-0031 / ISS-0068: "结果中未包含每次收货的数量" ──────────────────────────────

async def test_no_quantities_goes_when_code_lists_the_lines(test_engine, admin_client, monkeypatch):
    d = await _po_535(test_engine, admin_client)
    _stub(monkeypatch, {"entity": "goods_receipt", "select": ["number", "received_at"],
                        "where": [{"field": "po_number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]},
          narration=f"共收货 1 次：{d['gr']}。结果中未包含每次收货的数量明细。"
                    "如需了解具体收货数量，请查看收货单的详情。")
    answer = (await _ask(admin_client, f"{d['po']} 收过几次货？每次收了多少？"))["answer"]
    assert answer.startswith(f"{d['gr']}（{d['po']}）收货 3 行，金额 CAD 6,349.71")
    assert "未包含" not in answer and "如需了解" not in answer
    assert f"共收货 1 次：{d['gr']}。" in answer


async def _service_gr(test_engine, client) -> GoodsReceipt:
    uid = _uid(client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = PurchaseOrder(number=f"PO-H{_tag()}-2609-04", title="svc", type=2, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("15562"), total=Decimal("15562"),
                           status="issued", created_by=uid, currency="CAD")
        db.add(po)
        await db.flush()
        gr = GoodsReceipt(number=f"GR-H{_tag()}-0003", title="r", po_id=po.id,
                          po_number=po.number, vendor_id=v.id, vendor_name=v.name,
                          gr_type="service", procurement_type=2, status="confirmed",
                          created_by=uid, received_at=datetime(2026, 10, 22, 14, tzinfo=timezone.utc))
        db.add(gr)
        await db.flush()
        db.add(GrLineItem(gr_id=gr.id, description="Boiler service", qty_ordered=Decimal(1),
                          qty_received=Decimal(1), unit="job", unit_price=Decimal("15562"),
                          line_total=Decimal("15562"), sort_order=0))
        await db.commit()
        return gr


@pytest.mark.parametrize("entity", ["goods_receipt", "gr_line"])
async def test_on_a_gr_page_the_lines_and_value_come_first(
        test_engine, admin_client, monkeypatch, entity):
    """GR-20261022-0003 page, "收了多少？": the planner builds the query from the
    page's document (doc_type=gr); the narrator says nothing was returned."""
    gr = await _service_gr(test_engine, admin_client)
    seen = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        seen.append(context)
        field = "number" if entity == "goods_receipt" else "receipt.number"
        select = ["number", "status"] if entity == "goods_receipt" else ["description"]
        return {"kind": "query", "query": {
            "entity": entity, "select": select,
            "where": [{"field": field, "op": "eq", "value": context["doc_number"],
                       "because": "the page"}]}}

    async def fake_narrate(message, q, result):
        return {"text": "系统中没有返回收货数量的信息。金额是 15,562.00。", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    r = await admin_client.post(CHAT, json={
        "message": "收了多少？", "context": {"app": "epms", "doc_type": "gr", "doc_id": str(gr.id)}})
    answer = r.json()["answer"]
    assert seen[0]["doc_number"] == gr.number
    assert answer.startswith(f"{gr.number}（{gr.po_number}）收货 1 行，金额 CAD 15,562.00：Boiler service — 1 job")
    assert "没有返回收货数量" not in answer


# ── ISS-0043: "数据中没有账户" ───────────────────────────────────────────────

async def test_no_account_goes_when_the_payment_record_names_one(
        test_engine, admin_client, monkeypatch):
    number = await _paid_pa(test_engine, admin_client, remittance=False)
    _stub(monkeypatch, {"entity": "payment_application",
                        "select": ["pa_number", "status", "created_at"],
                        "where": [{"field": "pa_number", "op": "eq", "value": number,
                                   "because": "PA"}]},
          narration="状态为已处理（Processed）。查询结果中没有包含付款时间和账户信息。数据中没有账户。")
    answer = (await _ask(admin_client, f"{number} 什么时候付的？用的哪个账户？"))["answer"]
    for part in answer.split("**")[2::2]:      # asked as two questions
        assert part.strip().startswith(f"{number} 已于 2026-OCT-19 付款。付款账户：RBC"), part
    assert "没有包含付款时间" not in answer and "数据中没有账户" not in answer
    assert "状态为已处理（Processed）。" in answer


# ── ISS-0066: prepayment rule question; remittance ───────────────────────────

async def test_a_vendor_without_a_cap_can_be_prepaid_up_to_the_system_cap(
        admin_client, monkeypatch):
    """"没有设置 Max Prepayment % 的供应商，能不能建预付款 PA？" said "不能", then the
    system cap, then "需要先在供应商记录上设置" — the narrator says exactly that."""
    from app.schemas.config import PrepaymentConfig
    from tests.test_assistant_asst2_d import _stub as rules_stub

    question = "没有设置 Max Prepayment % 的供应商，能不能建预付款 PA？"
    v = rule_verdicts.match(question)
    assert v is not None and v.id == "no_vendor_cap_takes_the_system_cap"
    rules_stub(monkeypatch, narration="不能。系统会使用 Prepayment Config 的上限。"
                                      "但需要先在供应商记录上设置 Max Prepayment %。")
    body = (await admin_client.post(CHAT, json={"message": question})).json()
    pct = f"{Decimal(str(PrepaymentConfig().max_prepayment_pct)).normalize():f}"
    assert body["verdict"] == "no_vendor_cap_takes_the_system_cap"
    assert body["answer"].startswith(
        f"能。供应商没有设置自己的 Max Prepayment % 时，适用 Admin Panel → Prepayment Config "
        f"的系统上限，目前为 {pct}%")
    assert "不能。" not in body["answer"] and "需要先在供应商记录上设置" not in body["answer"]
    assert "系统会使用 Prepayment Config 的上限。" in body["answer"]


async def test_a_zero_system_cap_turns_the_verdict_round(monkeypatch):
    from app.services import rule_facts

    class Cfg:
        max_prepayment_pct = 0

    async def zero(db):
        return Cfg()

    import app.api.v1.pa as pa_api
    monkeypatch.setattr(pa_api, "_get_prepayment_config", zero)
    v = rule_verdicts.match("没有设置 Max Prepayment % 的供应商，能不能建预付款 PA？")
    said = await rule_verdicts.sentences(v, object())
    assert said["zh"].startswith("不能。") and "目前为 0%" in said["zh"]
    assert said["drops"] == list(rule_facts._CAN_PREPAY)
    assert rule_verdicts.place("可以预付。别的。", said["zh"], "first", said["drops"]) == \
        said["zh"] + "\n\n别的。"


async def test_a_vendor_answer_loses_no_policy_and_must_set_first(
        test_engine, admin_client, monkeypatch):
    async with _factory(test_engine)() as db:
        v = await _vendor(db, name=f"Raise {_tag()}")
        await db.commit()
    _stub(monkeypatch, {"entity": "vendor", "select": ["name", "max_prepayment_pct"],
                        "where": [{"field": "name", "op": "eq", "value": v.name, "because": "v"}]},
          narration="不可以。最大预付款百分比为空，这表示没有建立预付款政策。需要先在供应商记录上设置。")
    answer = (await _ask(admin_client, f"供应商 {v.name} 可以预付吗？"))["answer"]
    assert answer.startswith(f"{v.name} 没有设置自己的 Max Prepayment %，因此适用系统上限")
    for words in ("不可以", "没有建立预付款政策", "需要先"):
        assert words not in answer, words


async def test_remittance_is_not_said_sent_without_a_send_record(
        test_engine, admin_client, monkeypatch):
    number = await _paid_pa(test_engine, admin_client, remittance=False)
    _stub(monkeypatch, {"entity": "payment_application", "select": ["pa_number", "status"],
                        "where": [{"field": "pa_number", "op": "eq", "value": number,
                                   "because": "PA"}]},
          narration="是的，汇款通知已经发给供应商了，已于 2026-10-19 支付。")
    answer = (await _ask(admin_client, f"{number} 的汇款通知发给供应商了吗？"))["answer"]
    assert answer.startswith(f"{number} 已于 2026-OCT-19 付款。")
    assert "这笔付款还没有发送汇款通知" in answer
    assert "已经发给供应商" not in answer


# ── ISS-0057: opinions in the vendor summary ─────────────────────────────────

def test_opinions_are_taken_out_of_a_vendor_summary():
    text = ("PO：111 张，CAD 325,033.75。总体合作稳定，完成率很高。\n"
            "PA：112 张，全部已支付，付款情况良好。\n"
            "Distribution Qualtech 是一个活跃的供应商。\n"
            "Overall, a stable relationship. Invoices: 112, 2 unpaid.")
    out = answer_facts.without_opinions(text)
    assert out == ("PO：111 张，CAD 325,033.75。\nPA：112 张，全部已支付。\n"
                   "Invoices: 112, 2 unpaid.")


async def test_the_vendor_summary_reply_carries_no_opinion(test_engine, admin_client, monkeypatch):
    async with _factory(test_engine)() as db:
        v = await _vendor(db, name=f"Opinion {_tag()}")
        await db.commit()

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "vendor_summary", "vendor": v.name}

    async def fake_guide(message, payload):
        return {"text": "采购订单：0 张。是一个活跃的供应商，付款情况良好。", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    answer = (await _ask(admin_client, f"供应商 {v.name} 和我们合作的情况总结一下。"))["answer"]
    assert answer.startswith("采购订单：0 张。")
    assert "活跃" not in answer and "良好" not in answer


# ── ISS-0044: a catalog price without its currency ───────────────────────────

async def test_a_catalog_price_gets_its_currency(test_engine, admin_client, monkeypatch):
    code = f"M{_tag()}"
    async with _factory(test_engine)() as db:
        db.add(Part(code=code, category="Maintenance", name="Stainless Steel Spring",
                    supplier="Realgoal", supplier_part_no="071320-0001",
                    unit_price=Decimal("1362.56"), unit="Each"))
        await db.commit()
    _stub(monkeypatch, {"entity": "part", "select": ["code", "name", "supplier", "unit_price", "unit"],
                        "where": [{"field": "code", "op": "eq", "value": code, "because": "part"}]},
          narration=f"{code}：Stainless Steel Spring，供应商 Realgoal，单价 1,362.56，单位 Each"
                    "（即 $1362.56；已含 CAD 1,362.56 的说法不重复加）。")
    answer = (await _ask(admin_client, f"零件 {code} 的名称、供应商、单价、单位是什么？"))["answer"]
    assert "单价 CAD 1,362.56，" in answer and "即 CAD 1362.56" in answer
    assert "CAD CAD" not in answer and answer.count("CAD 1,362.56") == 2
