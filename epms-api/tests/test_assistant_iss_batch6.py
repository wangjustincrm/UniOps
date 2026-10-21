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


# ── ISS-0010: what a PA's receipt check really accepts, and the override ─────

async def _po_with_matched_invoice(client, test_engine, received_qty="0"):
    """A PO (via the API) with one line, a header-matched invoice and no GR.
    `received_qty` is written on the line the way a PMS-migrated order has it."""
    import uuid
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.invoice import Invoice
    from app.models.po import PoLineItem
    from tests.test_assistant_iss_round3 import _user_id
    from tests.test_pa_multi_po import _make_po, _make_vendor

    v = await _make_vendor(client)
    po = await _make_po(client, v["id"])
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(update(PoLineItem).where(PoLineItem.po_id == uuid.UUID(po["id"]))
                         .values(received_qty=Decimal(received_qty)))
        ref = f"I-B6-{uuid.uuid4().hex[:6]}"
        inv = Invoice(internal_ref=ref, vendor_invoice_number=ref,
                      vendor_id=uuid.UUID(v["id"]), vendor_name=v["name"],
                      amount=Decimal("400"), tax_amount=Decimal("52"),
                      total_amount=Decimal("452"), invoice_date=date(2026, 1, 1),
                      due_date=date(2026, 2, 1), status="matched", line_items=[],
                      po_id=uuid.UUID(po["id"]), uploaded_by=_user_id(client))
        db.add(inv)
        await db.commit()
        return po, str(inv.id)


def _pa(po, inv, **extra):
    from tests.test_pa_multi_po import _payload
    return _payload([po["id"]], invoice_ids=[inv], subtotal="400.00",
                    tax_amount="52.00", **extra)


async def test_pa_receipt_evidence_is_a_gr_or_a_received_quantity(admin_client, test_engine):
    """The rule says a goods receipt OR a received quantity on a PO line counts
    (PRD INV-3WM-006 names only a linked goods receipt — reported as a PRD
    conflict, the Assistant states what the code does)."""
    import uuid

    import app.db.session as sm
    from app.crud import po as po_crud

    received, inv1 = await _po_with_matched_invoice(admin_client, test_engine, "2")
    nothing, _ = await _po_with_matched_invoice(admin_client, test_engine, "0")
    async with sm.AsyncSessionLocal() as db:
        assert await po_crud.po_has_three_way_matched_invoice(db, uuid.UUID(received["id"]))
        assert not await po_crud.po_has_three_way_matched_invoice(db, uuid.UUID(nothing["id"]))
    # No GR anywhere, and the PA goes through without an override.
    r = await admin_client.post("/api/v1/pa", json=_pa(received, inv1))
    assert r.status_code == 201, r.text
    assert r.json()["receipt_override"] is False


async def test_pa_without_receipt_needs_the_override_and_a_reason(admin_client, test_engine):
    po, inv = await _po_with_matched_invoice(admin_client, test_engine, "0")
    r = await admin_client.post("/api/v1/pa", json=_pa(po, inv))
    assert r.status_code == 422 and "override with a reason" in r.json()["detail"]
    r = await admin_client.post("/api/v1/pa", json=_pa(po, inv, receipt_override=True,
                                                     receipt_override_reason="  "))
    assert r.status_code == 422 and "reason is required" in r.json()["detail"]
    r = await admin_client.post("/api/v1/pa", json=_pa(po, inv, receipt_override=True,
                                                     receipt_override_reason="arrived, GR late"))
    assert r.status_code == 201, r.text


async def test_the_receipt_rule_keeps_the_override_with_the_check():
    """Four answers in five dropped the override: it is now in the same
    sentence as the check, in every text that states the check, and the
    narrator is told a rule's exceptions are part of it."""
    from app.services import doc_type_guide

    for text in (rule_registry.card("matching_compares_amounts_not_received_quantity")["answer"],
                 next(n for t in doc_type_guide.build("pa")["types"] if t["value"] == "regular"
                      for n in t["notes"] if "receipt evidence" in n)):
        sentence = next(s for s in " ".join(text.split()).split(". ")
                        if "Override Receipt on PA" in s)
        assert "received quantity" in sentence and "reason" in sentence
    assert "never shorten it to its first half" in assistant_llm._GUIDE_SYSTEM


async def test_no_rule_says_agreement_payments_are_unchecked():
    """"采购协议的付款不需要收货检查" contradicted PRD AGR-061. Every text that
    says an agreement payment has no goods-receipt check also says what is
    checked instead, and the code still checks it."""
    import inspect
    from app.api.v1 import pa as pa_api
    from app.services import doc_type_guide

    texts = [c["answer"] for _, c in rule_registry.all_cards()]
    texts += [n for d in ("pa", "agreement", "invoice") for t in doc_type_guide.build(d)["types"]
              for n in t["notes"]]
    said = [" ".join(t.split()) for t in texts if "no goods-receipt check" in " ".join(t.split())]
    assert len(said) >= 2
    for t in said:
        assert "house account" in t.lower() and "recurring" in t.lower()
        assert "confirmed" in t and "without receipt evidence" in t
    card = " ".join(rule_registry.card("agreement_pa_gates")["answer"].split())
    assert "with a reason" in card and "Milestone: no confirmation check" in card
    src = inspect.getsource(pa_api._validate_agreement_pa_invoices)
    assert "accepted_at.is_(None)" in src and "legacy_settlement" in src
    assert "_validate_agreement_pa_invoices" in inspect.getsource(pa_api.create_pa)


# ── ISS-0011: the task counts are the EPMS Task Inbox's ──────────────────────

def _task(t, doc_type="po", priority="normal", n=0):
    return {"type": t, "document_type": doc_type, "priority": priority,
            "document_number": f"D-{t}-{n}", "due_date": None,
            "created_at": f"2026-10-{1 + n % 28:02d}"}


async def test_task_counts_leave_out_what_the_epms_inbox_leaves_out(admin_client, monkeypatch):
    """403 against the inbox's 397: the six were vms_confirm_training."""
    from app.services import guide_view

    async def fake_tasks(role, user_id):
        return ([_task("confirm_receipt", n=i) for i in range(5)]
                + [_task("approve_exp", "exp", n=i) for i in range(2)]
                + [_task("vms_confirm_training", "vms_train", n=i) for i in range(6)]
                + [_task("check_out_visitor", "vms_visit")])

    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "next"}

    async def fake_guide(message, payload):
        seen.append(payload)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(guide_view, "my_tasks", fake_tasks)
    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    r = await admin_client.post(CHAT, json={"message": "我现在有几条待办？"})
    p = seen[0]
    assert p["open_task_count"] == 7 == p["groups_add_up_to"]
    assert {g["type"] for g in p["groups"]} == {"confirm_receipt", "approve_exp"}
    assert {(g["type"], g["count"], g["module"]) for g in p["not_in_this_inbox"]} == {
        ("vms_confirm_training", 6, "VMS"), ("check_out_visitor", 1, "VMS")}
    assert r.json()["sources"]["open_tasks"] == 7


async def test_groups_add_up_to_the_total_with_an_other_row():
    """The narrator once merged seven small groups into "其他（7 项）| 4" and
    the table added up to 402 of 403. The merge is done here, counted."""
    from app.services import guide_view

    tasks = []
    for i, t in enumerate(sorted(guide_view.TASK_TYPE_LABELS)[:19]):
        tasks += [_task(t, n=j) for j in range(i % 4 + 1)]
    tasks += [_task("resolve_ap_duplicate_invoice", priority="urgent")]
    s = guide_view.task_summary(tasks)
    assert len(s["groups"]) == 12 and s["groups"][-1]["type"] == "other"
    other = s["groups"][-1]
    assert other["count"] == sum(m["count"] for m in other["made_of"])
    assert other["label"] == f"Other ({len(other['made_of'])} types)"
    assert sum(g["count"] for g in s["groups"]) == s["groups_add_up_to"] == s["open_task_count"]
    assert s["open_task_count"] == len(tasks)
    # Few types: every group on its own row, still adding up.
    few = guide_view.task_summary(tasks[:6])
    assert all(g["type"] != "other" for g in few["groups"])
    assert sum(g["count"] for g in few["groups"]) == few["open_task_count"] == 6


async def test_the_inbox_filter_is_the_frontends():
    import re
    from pathlib import Path

    from app.services import guide_view

    hook = Path(__file__).resolve().parents[2] / "epms" / "src" / "hooks" / "useTasks.ts"
    if not hook.exists():
        pytest.skip("epms frontend not mounted in this container")
    line = re.search(r"NON_EPMS_DOC_TYPES = new Set\(\[([^\]]*)\]\)", hook.read_text()).group(1)
    assert set(re.findall(r"'([a-z_]+)'", line)) == set(guide_view.NOT_IN_EPMS_INBOX)
    assert "not_in_this_inbox" in assistant_llm._GUIDE_SYSTEM
    assert "never merge rows" in " ".join(assistant_llm._GUIDE_SYSTEM.split()).lower() or \
        "Never merge rows" in " ".join(assistant_llm._GUIDE_SYSTEM.split())
