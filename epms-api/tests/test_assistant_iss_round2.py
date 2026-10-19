"""Assistant fixes, second round (ISS-0004 remainder, ISS-0008 … ISS-0011).

The rules the guide now states are pinned to the behaviour they describe: a
rule whose code changes must fail here, not be read out wrong.
"""
import json
import re
import sys
import types
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as sm
from app.core.config import settings
from app.crud import agreement as agreement_crud
from app.crud import gr as gr_crud
from app.crud import invoice as invoice_crud
from app.crud import pa as pa_crud
from app.crud import po as po_crud
from app.crud import pr as pr_crud
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.schemas.invoice import AllocationInput, InvoiceMatchRequest
from app.services import assistant_llm, doc_type_guide, guide_view

pytestmark = pytest.mark.asyncio
CHAT = "/api/v1/assistant/chat"


def _user_id(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


@pytest.fixture
def fake_anthropic(monkeypatch):
    mod = types.ModuleType("anthropic")
    for name in ("APIConnectionError", "RateLimitError", "APIStatusError"):
        setattr(mod, name, type(name, (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", mod)


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Client:
    """Returns the given content blocks for every call."""

    def __init__(self, blocks):
        self.messages = self
        self._blocks = blocks

    async def create(self, **kw):
        return _Block(content=self._blocks, usage=None)


# ── ISS-0009: tool-call markup never reaches the person ──────────────────────

async def test_strip_markup_removes_tool_tags_and_keeps_real_angle_brackets():
    leaked = "不在我能查询的数据范围内。\n</explanation>\n</invoke>"
    assert assistant_llm.strip_markup(leaked) == "不在我能查询的数据范围内。"
    assert assistant_llm.strip_markup("qty < 10 and > 2") == "qty < 10 and > 2"


async def test_narrator_output_is_cleaned(monkeypatch, fake_anthropic):
    monkeypatch.setattr(assistant_llm, "_client", lambda: _Client(
        [_Block(type="text", text="答案。\n</explanation>\n</invoke>")]))
    told = await assistant_llm.narrate_guide("问题？", {"question": "问题？"})
    assert told["text"] == "答案。"


async def test_planner_refusal_text_is_cleaned(monkeypatch, fake_anthropic):
    monkeypatch.setattr(assistant_llm, "_client", lambda: _Client([_Block(
        type="tool_use", name="cannot_answer",
        input={"reason": "not_a_data_question",
               "explanation": "没有这个数据。\n</explanation>\n</invoke>"})]))
    planned = await assistant_llm.plan([], "能在 Teams 里审批吗？")
    assert planned["kind"] == "cannot" and planned["explanation"] == "没有这个数据。"


async def test_chat_exit_cleans_any_answer(admin_client, monkeypatch):
    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "cannot", "reason": "x", "explanation": "不行。</invoke>"}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    r = await admin_client.post(CHAT, json={"message": "能在 Teams 里审批吗？"})
    assert r.json()["answer"] == "不行。"


# ── ISS-0011: task counts are counted in code, grouped like the Task Inbox ────

def _task(t, priority="normal", n=0):
    return {"type": t, "priority": priority, "document_number": f"D-{t}-{n}",
            "due_date": None, "created_at": f"2026-10-{1 + n % 28:02d}"}


async def test_task_summary_counts_add_up_and_use_inbox_labels():
    tasks = ([_task("confirm_receipt", n=i) for i in range(54)]
             + [_task("approve_pr", n=i) for i in range(5)]
             + [_task("process_pa", n=i) for i in range(303)]
             + [_task("resolve_ap_duplicate_invoice", "urgent")]
             + [_task("brand_new_type")])
    s = guide_view.task_summary(tasks)
    assert s["open_task_count"] == 364 and s["urgent_count"] == 1
    assert s["groups_add_up_to"] == 364
    groups = {g["label"]: g["count"] for g in s["groups"]}
    assert groups["Confirm Goods Receipt"] == 54 and groups["Approve Purchase Request"] == 5
    assert groups["brand_new_type"] == 1                    # inbox falls back to the key too
    assert s["examples"][0]["type"] == "resolve_ap_duplicate_invoice"   # urgent first
    assert s["examples_shown"] == 12 and s["examples_left_out"] == 352
    assert s["assigned_to_me_personally_count"] + s["reached_me_through_a_role_count"] == 364


async def test_whats_next_payload_carries_counts_not_the_whole_list(admin_client, monkeypatch):
    async def fake_tasks(role, user_id):
        return [_task("confirm_receipt", n=i) for i in range(40)]

    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "next"}

    async def fake_guide(message, payload):
        seen.append(payload)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(guide_view, "my_tasks", fake_tasks)
    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    await admin_client.post(CHAT, json={"message": "我现在有几条待办？"})
    p = seen[0]
    assert p["open_task_count"] == 40 and "tasks" not in p
    assert p["groups"] == [{"type": "confirm_receipt", "label": "Confirm Goods Receipt", "count": 40}]
    assert len(p["examples"]) == 12


_TS = Path(__file__).resolve().parents[2] / "epms" / "src" / "lib" / "taskTypes.ts"


@pytest.mark.skipif(not _TS.exists(), reason="epms frontend not mounted in this container")
async def test_task_labels_match_the_task_inbox():
    block = _TS.read_text(encoding="utf-8").split("export const TASK_TYPE_LABELS")[1].split("\n}")[0]
    frontend = dict(re.findall(r"^\s+([a-z_]+): '([^']+)',", block, re.M))
    assert frontend == guide_view.TASK_TYPE_LABELS


# ── ISS-0008 / ISS-0010: the rules the guide states, and the code behind them ─

def _rules(doc_type):
    return {r["id"]: r["answer"] for r in doc_type_guide.build(doc_type)["rules"]}


async def test_guide_carries_the_rules():
    assert "Cancelled" in _rules("pa")["rejected_pa_is_cancelled"]
    assert "cannot be chosen" in _rules("pr")["type_1_not_selectable"]
    assert "PR-YYYYMMDD-NNNN" in _rules("pr")["pr_numbering"]
    m = _rules("invoice")["matching_compares_amounts_not_received_quantity"]
    assert m.startswith("No.") and "not compared" in m
    assert "payment application" in m and "Override Receipt on PA" in m   # PA-013 gate
    # The sentence that told the model the receipt is part of the match is gone.
    assert "receipt proving" not in doc_type_guide.build("invoice")["what_it_is"]


async def test_process_payload_says_approvals_happen_only_in_the_system(monkeypatch):
    async def fake_forward(method, path, token, **kw):
        return 200, {"doc_type": "pa", "steps": []}

    monkeypatch.setattr(guide_view.approval_client, "forward", fake_forward)
    got = await guide_view.process_steps("pa", "t")
    [rule] = got["how_approvals_are_given"]
    assert rule["id"] == "approve_only_in_the_system" and "Teams" in rule["answer"]


async def test_teams_card_offers_no_way_to_approve(monkeypatch):
    """Pins approve_only_in_the_system: the card's only action opens the page."""
    from app.services import teams
    sent: list = []

    class _Resp:
        def raise_for_status(self):
            return None

    class _Http:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            sent.append(json)
            return _Resp()

    monkeypatch.setattr(teams.httpx, "AsyncClient", _Http)
    await teams.send_teams_card("https://hook", "t", "b", link="https://epms/pr/1")
    actions = re.findall(r'"type":\s*"(Action\.[A-Za-z]+)"', json.dumps(sent))
    assert actions and set(actions) == {"Action.OpenUrl"}


async def test_numbers_have_the_formats_the_guide_states():
    async with sm.AsyncSessionLocal() as db:
        assert re.fullmatch(r"PR-\d{8}-\d{4}", await pr_crud._next_number(db))
        assert re.fullmatch(r"PA-\d{8}-\d{4}", await pa_crud._next_number(db))
        assert re.fullmatch(r"GR-\d{8}-\d{4}", await gr_crud._next_number(db))
        assert re.fullmatch(r"AGR-\d{6}-\d{4}", await agreement_crud._next_number(db))
        assert re.fullmatch(r"PO-332-\d{4}-\d{2}", await po_crud._next_number(db, "332"))
        assert re.fullmatch(r"INV-\d{4}-\d+", await invoice_crud._next_ref(db))


async def test_invoice_matches_with_nothing_received():
    """Pins matching_compares_amounts_not_received_quantity (PRD INV-3WM-006)."""
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"r2-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="AP", role="ap_clerk"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Messer",
                        category="supplier", contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-R2-{uuid.uuid4().hex[:6]}", title="T", type=2,
                           vendor_id=vendor.id, vendor_name="Messer", status="issued",
                           subtotal=Decimal("100"), created_by=user.id)
        db.add(po)
        await db.flush()
        line = PoLineItem(po_id=po.id, description="gas", qty=Decimal("10"), unit="ea",
                          unit_price=Decimal("10"), line_total=Decimal("100"),
                          received_qty=Decimal("0"))
        db.add(line)
        await db.flush()

        async def _match(amount):
            ref = f"INV-R2-{uuid.uuid4().hex[:6]}"
            inv = Invoice(internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor.id,
                          vendor_name="Messer", amount=Decimal(amount), tax_amount=Decimal("0"),
                          total_amount=Decimal(amount), invoice_date=date(2026, 10, 1),
                          due_date=date(2026, 11, 1), status="unmatched", line_items=[],
                          uploaded_by=user.id)
            db.add(inv)
            await db.flush()
            await invoice_crud.match(db, inv, InvoiceMatchRequest(allocations=[AllocationInput(
                invoice_line_id=uuid.uuid4(), po_id=po.id, po_line_id=line.id,
                allocated_amount=Decimal(amount))]), matched_by=user.id)
            await db.refresh(inv)
            return inv

        first = await _match("60")      # nothing received, under the line value
        assert first.status == "matched" and first.gr_id is None
        second = await _match("50")     # cumulative 110 > 100, zero tolerance
        assert second.status == "exception"
        await db.rollback()


# ── ISS-0004 remainder: service completion date and owner ────────────────────

async def test_service_fields_are_queryable(admin_client, requester_client, test_engine,
                                            monkeypatch):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    admin, owner = _user_id(admin_client), _user_id(requester_client)
    async with factory() as db:
        service = PurchaseRequest(number=f"PR-R2S-{uuid.uuid4().hex[:6]}", title="Service",
                                  type=4, status="approved", amount=Decimal("3250"),
                                  created_by=admin, owner_id=owner,
                                  service_completion_date=date(2026, 10, 22))
        # A stale owner on a PHYSICAL type is ignored, as pr_owner.py ignores it.
        physical = PurchaseRequest(number=f"PR-R2P-{uuid.uuid4().hex[:6]}", title="Goods",
                                   type=2, status="approved", amount=Decimal("10"),
                                   created_by=admin, owner_id=owner)
        db.add_all([service, physical])
        await db.commit()

    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "query", "query": {
            "entity": "purchase_request",
            "select": ["number", "service_completion_date", "service_owner_name",
                       "requester_full_name"],
            "where": [{"field": "number", "op": "in", "value": [service.number, physical.number]}]}}

    async def fake_narrate(message, q, result):
        seen.append(result)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    r = await admin_client.post(CHAT, json={"message": "服务完成日期是哪天？负责人是谁？"})
    assert r.status_code == 200, r.text
    rows = {row["number"]: row for row in seen[0]["rows"]}
    assert str(rows[service.number]["service_completion_date"]) == "2026-10-22"
    assert rows[service.number]["service_owner_name"] == "Test requester"
    assert rows[service.number]["requester_full_name"] == "Test system_admin"
    assert rows[physical.number]["service_owner_name"] == "Test system_admin"
