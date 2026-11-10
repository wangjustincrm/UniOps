"""Assistant, second round (asst2/b): answers that need data looked up or
worked out are looked up and worked out by code.

Every chat test here stubs the narrator to write the WRONG thing — leave the
figure out, or say the opposite — and checks the reply still carries what the
code decided. That is the bar for these fixes: they must hold when Haiku does
not cooperate.
"""
import json
import sys
import types
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement import PurchaseAgreement
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.models.part import Part
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.services import answer_facts, assistant_llm, workflow_view
from tests.test_assistant_iss_batch2 import _uid

pytestmark = pytest.mark.asyncio
CHAT = "/api/v1/assistant/chat"


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _tag() -> str:
    return uuid.uuid4().hex[:6].upper()


def _stub(monkeypatch, plan: dict, narration: str = "ok") -> list:
    """The planner returns `plan`; the narrator writes `narration` (usually
    wrong on purpose) and records what it was given."""
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return plan if "kind" in plan else {"kind": "query", "query": plan}

    async def fake_narrate(message, q, result):
        seen.append({"query": q, "result": result})
        return {"text": narration, "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    return seen


async def _ask(client, message: str) -> dict:
    r = await client.post(CHAT, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


async def _vendor(db, name=None, **kw) -> Vendor:
    v = Vendor(code=f"V{_tag()}", name=name or f"Vendor {_tag()}", category="Services",
               contact_name="A", contact_email="a@t.test", **kw)
    db.add(v)
    await db.flush()
    return v


async def _po_535(test_engine, client):
    """PO-535-2609-02 as it is: four lines (one a 20.00 transport fee that was
    neither received nor invoiced), one GR receiving the other three, one
    invoice allocated over those three — matched, then paid."""
    tag = _tag()
    uid = _uid(client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db, name=f"Qualtech {tag}")
        po = PurchaseOrder(number=f"PO-T{tag}-2609-02", title="seals", type=3, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("6369.71"),
                           total=Decimal("7197.77"), status="partially_received",
                           created_by=uid, currency="CAD")
        db.add(po)
        await db.flush()
        specs = [("SHAFT SEAL FPM", 3, 3, "2624.64"), ("SHAFT SEAL FEP", 3, 3, "1523.97"),
                 ("Transport fee CMB", 1, 0, "20.00"), ("SHAFT SEAL EPDM", 6, 6, "2201.10")]
        lines = []
        for i, (desc, qty, got, total) in enumerate(specs):
            line = PoLineItem(po_id=po.id, description=desc, qty=Decimal(qty), unit="pcs",
                              unit_price=Decimal(total) / qty, line_total=Decimal(total),
                              received_qty=Decimal(got), sort_order=i)
            db.add(line)
            lines.append(line)
        await db.flush()
        gr = GoodsReceipt(number=f"GR-T{tag}-0024", title="r", po_id=po.id,
                          po_number=po.number, vendor_id=v.id, vendor_name=v.name,
                          gr_type="physical", procurement_type=3, status="collected",
                          created_by=uid, received_at=datetime(2026, 10, 9, 14, tzinfo=timezone.utc))
        db.add(gr)
        await db.flush()
        for i, line in enumerate(lines):
            if line.received_qty:
                db.add(GrLineItem(gr_id=gr.id, po_line_id=line.id, description=line.description,
                                  qty_ordered=line.qty, qty_received=line.received_qty,
                                  unit="pcs", unit_price=line.unit_price,
                                  line_total=line.line_total, sort_order=i))
        inv = Invoice(internal_ref=f"INV-T{tag}", vendor_invoice_number="197736",
                      vendor_id=v.id, vendor_name=v.name, amount=Decimal("6349.71"),
                      tax_amount=Decimal("825.46"), total_amount=Decimal("7175.17"),
                      invoice_date=date(2026, 10, 7), due_date=date(2026, 11, 6),
                      status="paid", line_items=[], uploaded_by=uid, po_id=po.id,
                      po_number=po.number, gr_id=gr.id, gr_number=gr.number,
                      matched_at=datetime(2026, 10, 9, 15, tzinfo=timezone.utc))
        db.add(inv)
        await db.flush()
        for line in lines:
            if line.received_qty:
                db.add(InvoicePoAllocation(invoice_id=inv.id, invoice_line_id=uuid.uuid4(),
                                           po_id=po.id, po_line_id=line.id,
                                           allocated_amount=line.line_total,
                                           allocated_total=line.line_total,
                                           variance=Decimal("0")))
        await db.commit()
        return {"po": po.number, "gr": gr.number, "inv": inv.internal_ref, "po_id": po.id,
                "vendor": v.name}


# ── ISS-0031 / ISS-0068: what a receipt received ─────────────────────────────

async def test_a_receipt_answer_always_carries_its_lines(test_engine, admin_client, monkeypatch):
    """Only goods_receipt was queried, and not even its number; the narrator
    says there are no quantities. The lines are still said, by code."""
    d = await _po_535(test_engine, admin_client)
    seen = _stub(monkeypatch, {"entity": "goods_receipt", "select": ["received_at", "status"],
                               "where": [{"field": "po_number", "op": "eq", "value": d["po"],
                                          "because": "PO"}]},
                 narration="结果中未包含每次收货的数量明细。")
    body = await _ask(admin_client, f"{d['po']} 收过几次货？每次收了多少？")
    answer = body["answer"]
    assert answer.startswith("结果中未包含")
    assert f"{d['gr']}（{d['po']}）收货 3 行，金额 CAD 6,349.71" in answer
    for part in ("SHAFT SEAL FPM — 3 pcs", "SHAFT SEAL FEP — 3 pcs", "SHAFT SEAL EPDM — 6 pcs"):
        assert part in answer
    assert "Transport" not in answer
    # The narrator was told the lines are added, and was given them.
    given = seen[0]["result"]
    assert given["from_code"]["receipt_lines"]
    assert any(n[0] == "receipt_lines" for n in given["said_with_this_list"])


# ── ISS-0016 / ISS-0036: the PO against its invoices; three-way match ───────

async def test_invoice_against_po_is_compared_line_by_line(test_engine, admin_client, monkeypatch):
    d = await _po_535(test_engine, admin_client)
    _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref", "status", "variance"],
                        "where": [{"field": "po_number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]},
          narration="差异为 0.00，与采购订单完全匹配。")
    answer = (await _ask(admin_client, f"{d['po']} 的发票金额有没有超过 PO？差异多少？"))["answer"]
    assert "税前比 PO 少 CAD 20.00（含税少 CAD 22.60），没有超过 PO" in answer
    assert "未开票的行：第 3 行 Transport fee CMB（CAD 20.00）" in answer
    assert f"{d['inv']} 已付款" in answer and "09/10/2026 匹配" in answer

    _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref"],
                        "where": [{"field": "po_number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]}, narration="Matched, no difference.")
    answer = (await _ask(admin_client, f"Is the invoice for {d['po']} over the PO?"))["answer"]
    assert "CAD 20.00 below the PO before tax (CAD 22.60 including tax) — not over the PO" in answer
    assert "Not invoiced: line 3 Transport fee CMB (CAD 20.00)" in answer


async def test_a_paid_matched_received_po_is_three_way_matched(test_engine, admin_client,
                                                                 monkeypatch):
    import app.db.session as sm
    from app.crud import po as po_crud

    d = await _po_535(test_engine, admin_client)
    _stub(monkeypatch, {"entity": "purchase_order", "select": ["number", "three_way_matched"],
                        "where": [{"field": "number", "op": "eq", "value": d["po"],
                                   "because": "PO"}]},
          narration="3-way match 结果：未匹配，因为差 22.60 且部分收货。")
    body = await _ask(admin_client, f"{d['po']} 的 3-way match 结果怎样？")
    assert body["answer"].count("三单匹配：已通过（INV-3WM-006") == 1
    assert d["gr"] in body["answer"]
    # The field itself: matched and since paid still counts.
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["three_way_matched"],
        "where": [{"field": "number", "op": "eq", "value": d["po"]}]})
    assert r.json()["rows"] == [{"three_way_matched": True}]
    # The PA gate is unchanged: a paid invoice cannot go on another PA.
    async with sm.AsyncSessionLocal() as db:
        assert not await po_crud.po_has_three_way_matched_invoice(db, d["po_id"])


# ── ISS-0068: no ceiling; status names ───────────────────────────────────────

async def test_an_agreement_with_no_nte_has_no_ceiling(test_engine, admin_client):
    tag = _tag()
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        db.add(PurchaseAgreement(number=f"AGR-T{tag}", title="t", agreement_type="house_account",
                                 vendor_id=v.id, vendor_name=v.name, valid_from=date(2026, 9, 1),
                                 valid_to=date(2027, 8, 31), consumed_amount=Decimal("1845.98"),
                                 created_by=_uid(admin_client), status="in_review"))
        await db.commit()
    import app.db.session as sm
    from app.core.access_scope import build_scope

    # The test matrix gives no role epms.agreement.read; grant it on the scope.
    async with sm.AsyncSessionLocal() as db:
        scope = await build_scope(db, {"sub": str(_uid(admin_client)), "role": "system_admin"})
        scope = {**scope, "perms": {**scope["perms"], "epms.agreement.read": True}}
        query = {"entity": "purchase_agreement", "select": ["number", "status", "remaining_amount"],
                 "where": [{"field": "number", "op": "eq", "value": f"AGR-T{tag}"}]}
        from app.services import controlled_query as cq
        result = await cq.execute(db, query, scope)
        assert await answer_facts.supplement(db, scope, "这个协议还剩多少额度？", query, result) is None
    zh = dict((k, z) for k, _e, z in result["said_with_this_list"])["agreement_ceiling"]
    assert f"AGR-T{tag} 没有设置 Not to Exceed 上限（No ceiling），所以剩余额度不受限制" in zh
    assert "CAD 1,845.98" in zh
    assert result["value_labels"]["status"]["in_review"] == "In Review"


# ── ISS-0021 #5: year on year ────────────────────────────────────────────────

async def test_year_on_year_is_worked_out_and_said(test_engine, admin_client, monkeypatch):
    tag = _tag()
    uid = _uid(admin_client)
    now = datetime.now(timezone.utc)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        for when, total in ((now, "300"), (now - timedelta(days=365), "200")):
            db.add(PurchaseOrder(number=f"PO-Y{_tag()}-2610-01", title="t", type=2,
                                 vendor_id=v.id, vendor_name=v.name, subtotal=Decimal(total),
                                 total=Decimal(total), status="issued", created_by=uid,
                                 currency="CAD", created_at=when))
        await db.commit()
    # The planner leaves compare_previous_year off; the narrator says it
    # cannot be worked out.
    seen = _stub(monkeypatch, {"entity": "purchase_order", "metrics": ["amount"],
                               "where": [{"field": "vendor_name", "op": "eq", "value": v.name,
                                          "because": "v"}],
                               "period": {"calendar": "this_month"}},
                 narration="无法计算增长幅度。")
    answer = (await _ask(admin_client, "本月采购额和去年同期相比增长了多少？"))["answer"]
    assert "本期" in answer and "CAD 300.00" in answer and "去年同期" in answer
    assert "CAD 200.00" in answer and "变化 +CAD 100.00（+50.0%）" in answer
    given = seen[0]
    assert given["query"]["period"]["compare_previous_year"] is True
    assert given["result"]["comparison"][0]["change"] == "100.00"


async def test_the_narrator_is_given_every_key_its_rules_name(monkeypatch):
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
    result = {"rows": [], "comparison": [1], "previous_year": {}, "near_misses": {"n": ["x"]},
              "numbers_read_as": [1], "resolved_period": {}, "totals_shown": {},
              "currency_split": "currency", "from_code": {"k": 1}}
    await assistant_llm.narrate("q", {}, result)
    payload = json.loads(sent[0]["messages"][0]["content"])
    for key in assistant_llm._NARRATED_KEYS:
        assert key in payload, key
        # ... and the rules mention each one, or passing it means nothing.
        assert f"`{key}`" in assistant_llm._NARRATE_SYSTEM or key in assistant_llm._NARRATE_SYSTEM


# ── ISS-0021 #6 / ISS-0066: the prepayment cap that applies ──────────────────

async def test_a_vendor_with_no_cap_of_its_own_gets_the_system_cap(test_engine, admin_client,
                                                                    monkeypatch):
    from app.core.prepayment_cap import effective_cap

    assert effective_cap(None, 100) == (Decimal(100), "system")
    assert effective_cap(Decimal("30"), 100) == (Decimal(30), "vendor")
    assert effective_cap(Decimal("80"), 50) == (Decimal(50), "vendor")   # PP-003
    async with _factory(test_engine)() as db:
        v = await _vendor(db, name=f"Boot-Boy {_tag()}")
        await db.commit()
    _stub(monkeypatch, {"entity": "vendor", "select": ["name", "max_prepayment_pct"],
                        "where": [{"field": "name", "op": "eq", "value": v.name, "because": "v"}]},
          narration="最大预付款百分比为空，这表示没有建立预付款政策。")
    answer = (await _ask(admin_client, f"供应商 {v.name} 最多能预付百分之几？"))["answer"]
    # Prepayment Config default (no row in the test database): its model's default.
    from app.schemas.config import PrepaymentConfig
    system = Decimal(str(PrepaymentConfig().max_prepayment_pct)).normalize()
    assert f"{v.name} 没有设置自己的 Max Prepayment %，因此适用系统上限" in answer
    assert f"{system:f}%" in answer


# ── ISS-0043 / ISS-0066: payment account, remittance ─────────────────────────

_REMITTANCE_TABLE = (
    "CREATE TABLE IF NOT EXISTS payment_remittance_notifications (id uuid PRIMARY KEY,"
    " scope_kind varchar(10) NOT NULL, scope_id uuid NOT NULL, recipient_kind varchar(10) NOT NULL,"
    " party_id uuid NOT NULL, party_name varchar(255) NOT NULL, email varchar(255) NOT NULL,"
    " payment_record_ids jsonb NOT NULL DEFAULT '[]', amount numeric(15,2) NOT NULL,"
    " currency varchar(10) NOT NULL, status varchar(10) NOT NULL, error varchar(500),"
    " attempts integer NOT NULL DEFAULT 0, sent_at timestamptz, created_by uuid NOT NULL,"
    " created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())")


async def _paid_pa(test_engine, client, remittance: bool = True) -> str:
    tag = _tag()
    uid = _uid(client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        number = f"PA-T{tag}"
        db.add(PaymentApplication(pa_number=number, title="t", vendor_id=v.id, vendor_name=v.name,
                                  subtotal=Decimal("6349.71"), payment_amount=Decimal("7175.17"),
                                  created_by=uid, status="processed",
                                  paid_at=datetime(2026, 10, 19, tzinfo=timezone.utc)))
        await db.commit()
    bank, record = uuid.uuid4(), uuid.uuid4()
    async with test_engine.begin() as conn:
        await conn.execute(sa.text(_REMITTANCE_TABLE))
        await conn.execute(sa.text(
            "INSERT INTO bank_accounts (id, name, bank_name, currency, is_active, created_at)"
            " VALUES (:id, :n, 'RBC', 'CAD', true, now())"), {"id": bank, "n": f"RBC {tag}"})
        await conn.execute(sa.text(
            "INSERT INTO payment_records (id, pa_number, vendor_name, payment_date,"
            " payment_method, amount, credit_applied, currency, status, bank_account_id)"
            " VALUES (:id, :pa, 'V', :d, 'bank_transfer', 7175.17, 0, 'CAD', 'completed', :b)"),
            {"id": record, "pa": number, "d": date(2026, 10, 19), "b": bank})
        if remittance:
            await conn.execute(sa.text(
                "INSERT INTO payment_remittance_notifications (id, scope_kind, scope_id,"
                " recipient_kind, party_id, party_name, email, payment_record_ids, amount,"
                " currency, status, sent_at, created_by) VALUES (:id, 'batch', :s, 'vendor', :p,"
                " 'V', 'craig@vendor.test', CAST(:ids AS jsonb), 7175.17, 'CAD', 'sent', :at, :u)"),
                {"id": uuid.uuid4(), "s": uuid.uuid4(), "p": uuid.uuid4(),
                 "ids": json.dumps([str(record)]), "u": uid,
                 "at": datetime(2026, 10, 19, 18, 42, tzinfo=timezone.utc)})
    return number


async def test_a_paid_pa_says_account_and_remittance_from_finance_records(
        test_engine, admin_client, monkeypatch):
    number = await _paid_pa(test_engine, admin_client)
    _stub(monkeypatch, {"entity": "payment_application", "select": ["pa_number", "status"],
                        "where": [{"field": "pa_number", "op": "eq", "value": number,
                                   "because": "PA"}]},
          narration="是的，汇款通知已经发给供应商了。数据中没有账户。")
    answer = (await _ask(admin_client, f"{number} 什么时候付的？用的哪个账户？汇款通知发了吗？"))["answer"]
    assert f"{number} 已于 19/10/2026 付款。" in answer
    assert "付款账户：RBC" in answer and "CAD 7,175.17" in answer
    # The send record, in plant time (18:42 UTC is 14:42 in Toronto).
    assert "汇款通知已于 19/10/2026 14:42 发送到 craig@vendor.test" in answer


async def test_without_finance_access_the_account_is_said_to_need_it(test_engine, admin_client):
    import app.db.session as sm
    from app.core.access_scope import build_scope

    number = await _paid_pa(test_engine, admin_client, remittance=False)
    async with sm.AsyncSessionLocal() as db:
        scope = await build_scope(db, {"sub": str(_uid(admin_client)), "role": "system_admin"})
        scope = {**scope, "perms": {**scope["perms"], "view_finance": False}}
        facts = await answer_facts.payment_facts(db, scope, number)
    en, zh = answer_facts.payment_sentences(facts)
    assert "needs View Finance access" in en and "需要 View Finance 权限" in zh
    assert "Paid from" not in en


async def test_no_remittance_record_is_said_as_none_sent(test_engine, admin_client):
    import app.db.session as sm
    from app.core.access_scope import build_scope

    number = await _paid_pa(test_engine, admin_client, remittance=False)
    async with sm.AsyncSessionLocal() as db:
        scope = await build_scope(db, {"sub": str(_uid(admin_client)), "role": "system_admin"})
        facts = await answer_facts.payment_facts(db, scope, number)
    en, _ = answer_facts.payment_sentences(facts)
    assert "No remittance advice has been sent" in en


# ── ISS-0043 / ISS-0047 / ISS-0070: the HISTORY block ────────────────────────

_T0 = datetime(2026, 10, 15, 12, 41, tzinfo=timezone.utc)


def _ev(minutes, action, name, role, step, comment=None):
    return SimpleNamespace(step_idx=step, action=action, actor_name=name, actor_role=role,
                           comment=comment, created_at=_T0 + timedelta(minutes=minutes))


async def test_history_is_in_plant_time_and_a_skipped_step_has_no_approver():
    got = workflow_view.history_entries([
        _ev(0, "submit", "Kris Enriquez", "requester", 0),
        _ev(1, "approve", "Farshid Mohammadi", "director", 1,
            "Auto-skipped (department has no Director)"),
        _ev(2, "approve", "Kris Enriquez", "finance_manager", 2,
            "Re-synced (config change): Auto-skipped (over-budget mode Allow)"),
        _ev(3, "approve", "Yuping Huang", "finance_bp", 3, "ok"),
    ], [])
    # 12:41 UTC is 08:41 in Toronto (EDT), as the PA page shows it.
    assert got[0]["at"] == "2026-10-15T08:41-04:00"
    assert [(e["action"], e["actor_name"], e["comment"]) for e in got] == [
        ("submit", "Kris Enriquez", None),
        ("skipped", None, "department has no Director"),
        ("skipped", None, "over-budget mode Allow — Re-synced (config change)"),
        ("approve", "Yuping Huang", "ok"),
    ]
    assert got[2]["actor_role"] == workflow_view.role_label("finance_manager")


async def test_a_paid_pa_history_ends_with_payment_processed(monkeypatch):
    async def no_steps(*a, **k):
        return []

    async def events(db, doc_id):
        return [_ev(0, "submit", "Kris", "requester", 0)]

    async def step(db, doc_type, items, token=None):
        for it in items:
            it.current_step = None

    monkeypatch.setattr(workflow_view.approval_client, "get_workflow_steps", no_steps)
    monkeypatch.setattr(workflow_view._SPEC["pa"]["crud"], "get_approval_events", events)
    monkeypatch.setattr(workflow_view, "enrich_current_step", step)
    pa = SimpleNamespace(id=uuid.uuid4(), pa_number="PA-1", status="processed",
                         submitted_at=_T0, approved_at=_T0 + timedelta(days=1),
                         paid_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
    view = await workflow_view.build(None, "pa", pa, "t")
    assert view["history"][-1]["action"] == "payment processed"
    assert view["history"][-1]["at"] == "2026-10-19"
    assert view["milestones"]["submitted_at"] == "2026-10-15T08:41-04:00"
    json.dumps(view, default=str)


async def test_a_waiting_pr_has_local_submit_time_and_days_waiting(monkeypatch):
    from app.core.delegation import local_today

    since = datetime.now(timezone.utc) - timedelta(days=3)

    async def no_steps(*a, **k):
        return []

    async def events(db, doc_id):
        return [SimpleNamespace(step_idx=0, action="submit", actor_name="Frank", actor_role="requester",
                                comment=None, created_at=datetime(2026, 10, 22, 19, 16, tzinfo=timezone.utc))]

    async def step(db, doc_type, items, token=None):
        for it in items:
            it.current_step = {"role": "dept_manager", "label": "Dept Manager",
                               "approver_name": "Farshid", "since": since}

    monkeypatch.setattr(workflow_view.approval_client, "get_workflow_steps", no_steps)
    monkeypatch.setattr(workflow_view._SPEC["pr"]["crud"], "get_approval_events", events)
    monkeypatch.setattr(workflow_view, "enrich_current_step", step)
    pr = SimpleNamespace(id=uuid.uuid4(), number="PR-1", status="submitted",
                         submitted_at=datetime(2026, 10, 22, 19, 16, tzinfo=timezone.utc))
    view = await workflow_view.build(None, "pr", pr, "t")
    # 19:16 UTC was 15:16 at the plant.
    assert view["history"][0]["at"] == "2026-10-22T15:16-04:00"
    assert view["milestones"]["submitted_at"] == "2026-10-22T15:16-04:00"
    from zoneinfo import ZoneInfo
    expected = (local_today() - since.astimezone(ZoneInfo("America/Toronto")).date()).days
    assert view["current_step"]["days_waiting"] == expected


# ── ISS-0024: one digit wrong ────────────────────────────────────────────────

async def test_closest_numbers_are_one_slip_away():
    from app.core import doc_numbers

    pool = ["PR-20260928-0001", "PR-20260928-0002", "PR-20260928-0141", "PR-20260929-0091"]
    assert doc_numbers.closest("PR-20260928-0091", pool) == ["PR-20260928-0001",
                                                              "PR-20260929-0091"]
    assert doc_numbers.closest("PR-20260928-0001", pool) == ["PR-20260928-0002"]


async def test_a_number_one_digit_wrong_gets_did_you_mean(test_engine, admin_client, monkeypatch):
    day = f"2099{uuid.uuid4().int % 10000:04d}"
    real = f"PR-{day}-0001"
    async with _factory(test_engine)() as db:
        db.add(PurchaseRequest(number=real, title="t", type=2, created_by=_uid(admin_client),
                               status="approved"))
        await db.commit()
    # The workflow route ("现在是什么状态") used to answer only "not found".
    _stub(monkeypatch, {"kind": "workflow", "doc_type": "pr", "doc_number": f"PR-{day}-0091"})
    body = await _ask(admin_client, f"What is the status of PR-{day}-0091?")
    assert body["answer"] == f"I could not find PR-{day}-0091. Did you mean {real}?"
    # The query route says it in code too, whatever the narrator wrote.
    _stub(monkeypatch, {"entity": "purchase_request", "select": ["number", "status"],
                        "where": [{"field": "number", "op": "eq", "value": f"PR-{day}-0091",
                                   "because": "n"}]}, narration="找不到。")
    answer = (await _ask(admin_client, f"PR-{day}-0091 现在是什么状态？"))["answer"]
    assert f"您是不是指 {real}？" in answer


# ── ISS-0015: the requester is the PR's Requester; no AP copy offered ────────

async def test_the_requester_behind_an_invoice_is_the_prs(test_engine, admin_client, monkeypatch):
    d = await _po_535(test_engine, admin_client)
    pr_number = f"PR-2098{uuid.uuid4().int % 10000:04d}-0001"
    async with _factory(test_engine)() as db:
        pr = PurchaseRequest(number=pr_number, title="Seals for Kristupas", type=3,
                             created_by=_uid(admin_client), status="approved")
        db.add(pr)
        await db.flush()
        await db.execute(sa.update(PurchaseOrder).where(PurchaseOrder.id == d["po_id"])
                         .values(pr_id=pr.id, pr_number=pr_number))
        await db.commit()
    _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref", "po_number"],
                        "where": [{"field": "internal_ref", "op": "eq", "value": d["inv"],
                                   "because": "inv"}]},
          narration="是 Kristupas 申请的。请查看 NC 系统。")
    answer = (await _ask(admin_client, f"发票 {d['inv']} 最早是谁申请采购的？"))["answer"]
    assert f"{pr_number} 的申请人（Requester）是 Test system_admin" in answer

    # The chain route carries the Requester field, and says it.
    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "trace", "doc_type": "invoice", "doc_number": d["inv"]}

    async def fake_chain(message, chain):
        assert chain["purchase_request"]["requester"] == "Test system_admin"
        return {"text": "Kristupas.", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_chain", fake_chain)
    answer = (await _ask(admin_client, f"Who requested the purchase for {d['inv']}?"))["answer"]
    assert f"The requester on {pr_number} is Test system_admin." in answer


async def test_the_epms_panel_does_not_offer_the_ap_copy(admin_client):
    everything = (await admin_client.get("/api/v1/assistant/schema")).json()["entities"]
    epms = (await admin_client.get("/api/v1/assistant/schema?app=epms")).json()["entities"]
    assert "ap_invoice" in {e["name"] for e in everything}
    assert "ap_invoice" not in {e["name"] for e in epms}
    assert not [e for e in epms if "AP ledger copy" in e["label"]]


# ── ISS-0044: a part with no PO lines ────────────────────────────────────────

async def test_a_part_with_no_po_lines_is_not_said_to_be_missing(test_engine, admin_client,
                                                                  monkeypatch):
    code = f"M{_tag()}"
    async with _factory(test_engine)() as db:
        db.add(Part(code=code, category="Maintenance", name="Stainless Steel Spring",
                    supplier="Realgoal", supplier_part_no="071320-0001",
                    unit_price=Decimal("362.56"), unit="Each"))
        await db.commit()
    _stub(monkeypatch, {"entity": "po_line", "select": ["unit_price", "order.number"],
                        "where": [{"field": "material_id", "op": "eq", "value": code,
                                   "because": "part"}]},
          narration="系统中不存在零件代码的采购订单行数据。请确认零件代码是否正确。")
    answer = (await _ask(admin_client, f"零件 {code} 最近三次的采购价格是多少？"))["answer"]
    assert "请确认" not in answer
    assert f"{code}（Stainless Steel Spring）在零件目录" in answer
    assert "CAD 362.56/Each" in answer and "没有任何采购订单行" in answer


# ── ISS-0057: what the vendor totals leave out ───────────────────────────────

async def test_a_vendor_summary_says_what_its_totals_leave_out(test_engine, admin_client,
                                                               monkeypatch):
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db, name=f"Summary {_tag()}")
        pos = [PurchaseOrder(number=f"PO-S{_tag()}-2610-01", title="t", type=2,
                             vendor_id=v.id, vendor_name=v.name, subtotal=Decimal("10"),
                             total=Decimal("10"), status=status, created_by=uid)
               for status in ("closed", "cancelled", "cancelled")]
        db.add_all(pos)
        await db.flush()
        db.add(PaymentApplication(pa_number=f"PA-S{_tag()}", title="t", vendor_id=v.id,
                                  po_id=pos[0].id,
                                  vendor_name=v.name, subtotal=Decimal("1"),
                                  payment_amount=Decimal("1"), created_by=uid,
                                  status="cancelled"))
        await db.commit()

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "vendor_summary", "vendor": v.name}

    given = []

    async def fake_guide(message, payload):
        given.append(payload)
        return {"text": "合作稳定。", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    answer = (await _ask(admin_client, f"供应商 {v.name} 和我们合作的情况总结一下。"))["answer"]
    assert "以上合计不含已取消、已驳回和草稿单据：已取消 2 张 PO、已取消 1 张付款申请" in answer, given[0].get("sections", {}).get("payment_applications")
    assert given[0]["added_below_your_reply"], given[0]


# ── ISS-0059: invoices of paid PAs ───────────────────────────────────────────

async def test_paid_pa_invoices_always_carry_the_partially_paid_rule(test_engine, admin_client,
                                                                     monkeypatch):
    seen = _stub(monkeypatch, {"entity": "invoice", "select": ["internal_ref", "pa_number"],
                               "where": [{"field": "pa_payment_shown", "op": "eq",
                                          "value": False, "because": "mismatch"}]},
                 narration="没有标记为部分已付，可能数据不一致。")
    answer = (await _ask(admin_client, "已付款 PA 对应的发票是否都显示 paid？"))["answer"]
    assert "预付款 PA 的发票应为 Partially Paid（部分付款）" in answer
    assert "张在普通 PA 上（应为 Paid）" in answer
    assert "invoices_by_pa_kind" in seen[0]["result"]["from_code"]


# ── ISS-0013: issued is not delivered; a cut list; a draft is not late ───────

async def test_issued_is_never_said_to_have_reached_the_vendor(test_engine, admin_client,
                                                               monkeypatch):
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        po = PurchaseOrder(number=f"PO-I{_tag()}-2605-03", title="t", type=2, vendor_id=v.id,
                           vendor_name=v.name, subtotal=Decimal("1"), total=Decimal("1"),
                           status="issued", created_by=uid, place_order_method="email",
                           place_order_email_to="orders@vendor.test",
                           placed_at=datetime(2026, 5, 20, 15, tzinfo=timezone.utc))
        db.add(po)
        await db.commit()
    _stub(monkeypatch, {"entity": "purchase_order", "select": ["number", "status"],
                        "where": [{"field": "number", "op": "eq", "value": po.number,
                                   "because": "po"}]},
          narration="是的，已经发给供应商了。")
    answer = (await _ask(admin_client, f"{po.number} 已经发给供应商了吗？"))["answer"]
    assert ("EPMS 记录的是订单邮件已于20/05/2026 发送到 orders@vendor.test，并不记录供应商是否收到或已读"
            in answer)


async def test_a_cut_list_states_the_total_and_what_is_shown(test_engine, admin_client,
                                                             monkeypatch):
    uid = _uid(admin_client)
    tag = _tag()
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        for i in range(12):
            db.add(PurchaseOrder(number=f"PO-L{tag}-2610-{i:02d}", title="t", type=2,
                                 vendor_id=v.id, vendor_name=v.name, subtotal=Decimal("1"),
                                 total=Decimal("1"), status="issued", created_by=uid,
                                 expected_delivery=date(2026, 11, 1)))
        await db.commit()
    plan = {"entity": "purchase_order", "select": ["number", "status", "expected_delivery"],
            "where": [{"field": "vendor_name", "op": "eq", "value": v.name, "because": "v"}]}
    # The query limit (100 in production), made small enough to bite here.
    from app.services import controlled_query as cq
    monkeypatch.setattr(cq, "MAX_ROWS_DETAIL", 5)
    # A reply listing none of the rows, and naming a field: the rows are listed
    # by code, the field gets its name.
    seen = _stub(monkeypatch, plan, narration="Here are the first 100 (by expected_delivery).")
    answer = (await _ask(admin_client, "List all issued purchase orders"))["answer"]
    assert "expected_delivery" not in answer and "Expected delivery date" in answer
    assert f"1. PO-L{tag}-2610-" in answer and "Issued · 01/11/2026" in answer
    assert "There are 12 in total; 5 are listed here — click Export to Excel below" in answer
    assert any(n[0] == "list_cut" for n in seen[0]["result"]["said_with_this_list"])
    # A reply that lists two of them: it says two.
    first_two = None

    async def fake_narrate(message, q, result):
        nonlocal first_two
        first_two = [r["number"] for r in result["rows"][:2]]
        return {"text": "共 12 张：" + "、".join(first_two), "usage": {}}

    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    answer = (await _ask(admin_client, "列出所有 issued 状态的采购订单"))["answer"]
    assert "一共 12 条，这里列出了 2 条，完整列表请点击下方 Export to Excel 下载查看。" in answer
    assert "1. " not in answer


async def test_a_draft_po_is_not_late_for_delivery(test_engine, admin_client, monkeypatch):
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        for status in ("draft", "issued", "partially_received", "submitted"):
            db.add(PurchaseOrder(number=f"PO-D{_tag()}-2607-01", title=status, type=2,
                                 vendor_id=v.id, vendor_name=v.name, subtotal=Decimal("1"),
                                 total=Decimal("1"), status=status, created_by=uid,
                                 expected_delivery=date(2026, 7, 24)))
        await db.commit()
    seen = _stub(monkeypatch, {"entity": "purchase_order", "select": ["number", "status"],
                               "where": [{"field": "vendor_name", "op": "eq", "value": v.name,
                                          "because": "v"},
                                         {"field": "expected_delivery", "op": "lt",
                                          "value": "2026-11-10", "because": "past due"},
                                         {"field": "status", "op": "not_in",
                                          "value": ["fully_received", "closed", "cancelled"],
                                          "because": "not fully received"}]})
    answer = (await _ask(admin_client, "哪些 PO 已经过了预计交期还没有全部收货？"))["answer"]
    statuses = sorted(r["status"] for r in seen[0]["result"]["rows"])
    assert statuses == ["issued", "partially_received"]
    assert "草稿或尚未审批的 PO 还没有下单，不算逾期未收货" in answer
