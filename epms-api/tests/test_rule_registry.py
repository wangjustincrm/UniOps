"""The rule registry (app/knowledge/rules, app/services/rule_registry.py) —
every rule the Assistant states, held to the code that does it (ISS-0022 and
the issues grouped under it: 0017 0018 0019 0025 0026 0027 0028 0034 0039 0040
0041 0046 0049 0050 0061 0063 0064 0065 0066).

The first block holds the registry itself: a card without a test, or naming a
test that does not exist, fails here — so a rule cannot be added on someone's
say-so. The rest are the pins the cards name.

Pins on files outside epms-api (the React pages, identity-api, budget-api,
finance-api, portal) read the source and skip when that tree is not mounted;
run with the whole repository mounted to exercise them.
"""
import ast
import inspect
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import rule_facts, rule_registry

EPMS_API = Path(__file__).resolve().parents[1]
REPO = EPMS_API.parent
CHAT = "/api/v1/assistant/chat"


def _src(rel: str) -> str:
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} is not mounted in this container")
    return path.read_text(encoding="utf-8")


def _rules(area: str) -> dict[str, str]:
    return {r["id"]: r["answer"] for r in rule_registry.static_rules(area)}


class _FakeDb:
    """Answers `select(CompanyConfig)` with the given config row."""

    def __init__(self, cfg):
        self.cfg = cfg

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.cfg)


# ── The registry itself ──────────────────────────────────────────────────────

def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_every_rule_card_is_derived_or_pinned_by_a_test_that_exists():
    unchecked = []
    for area, card in rule_registry.all_cards():
        where = f"{area}.{card.get('id')}"
        assert card.get("id") and card.get("answer"), where
        assert card.get("asked_as"), f"{where}: no asked_as"
        assert card.get("provenance") in rule_registry.PROVENANCES, where
        assert (card.get("enforced_in") or "").strip(), f"{where}: no enforced_in"
        tests = card.get("test") or []
        assert tests, f"{where}: a rule with no test behind it is a description"
        if card["provenance"] == "derived":
            assert callable(getattr(rule_facts, card.get("derive") or "", None)), where
        else:
            assert "{" not in card["answer"], f"{where}: placeholder in a non-derived rule"
        for area_ref in card.get("also_in") or []:
            assert area_ref in rule_registry.area_keys(), f"{where}: also_in {area_ref}"
        for ref in tests:
            file, _, name = ref.partition("::")
            if file.startswith("tests/"):
                path = EPMS_API / file
                assert path.exists(), f"{where}: {file} does not exist"
                assert name in _defined_tests(path), f"{where}: no {name} in {file}"
                continue
            path = REPO / file
            if not path.exists():
                unchecked.append(ref)   # another service's tree is not mounted
                continue
            if name:
                assert name in _defined_tests(path), f"{where}: no {name} in {file}"
    # Every card was looked at; only the files of unmounted trees were not.
    assert all("/tests/" in u or u.endswith(".test.ts") for u in unchecked)


def test_rule_ids_are_unique_and_areas_match_their_files():
    ids = [c["id"] for _, c in rule_registry.all_cards()]
    assert len(ids) == len(set(ids))
    for key, area in rule_registry.areas().items():
        assert area.label and area.covers, key
    assert {"pr", "po", "gr", "pa", "invoice", "agreement", "approval_engine",
            "delegation", "budget", "nc_sync", "notifications", "tasks",
            "dashboard", "reports", "vendor", "account"} == set(rule_registry.area_keys())


async def test_derived_rules_fill_every_placeholder():
    cfg = SimpleNamespace(nc_purchase_sync_interval_minutes=None, service_gr_sla={},
                          notification_settings={}, invoice_match_tolerance_pct=Decimal("5.00"))
    rules = [r for _, c in rule_registry.all_cards() if c["provenance"] == "derived"
             for r in [{"id": c["id"], "answer": " ".join(c["answer"].split())}]]
    filled, missing = await rule_registry.resolve(rules, _FakeDb(cfg))
    assert not missing
    for r in filled:
        assert "{" not in r["answer"] and "}" not in r["answer"], r["id"]


def test_the_planner_is_offered_every_area():
    from app.services import assistant_llm

    tool = assistant_llm._rules_tool()
    assert tool["input_schema"]["properties"]["areas"]["items"]["enum"] == list(
        rule_registry.area_keys())
    plan = assistant_llm._PLAN_SYSTEM
    assert "explain_rules" in plan and "is never one of these" in plan
    # The instruction that produced "that document type is not covered yet".
    assert "not covered yet" not in plan
    assert "never suggests\n  whom to ask" in plan or "never suggests whom to ask" in plan


def test_the_guide_rules_come_from_the_registry():
    from app.services import doc_type_guide, guide_view

    assert doc_type_guide.build("pa")["rules"] == rule_registry.static_rules("pa")
    assert [r["id"] for r in guide_view.approval_rules()] == ["approve_only_in_the_system"]
    over = {r["id"] for r in guide_view.over_budget_rules()}
    assert {"negative_total_is_never_over_budget", "budget_service_unreachable"} <= over


# ── Routing: rule questions get rules, not refusals ──────────────────────────

def _stub_llm(monkeypatch, *, plan, areas=()):
    from app.services import assistant_llm

    seen = {"guide": [], "preflight": [], "picked": 0}

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return dict(plan)

    async def fake_pick(message, history=None):
        seen["picked"] += 1
        return {"areas": list(areas), "usage": {}}

    async def fake_guide(message, payload):
        seen["guide"].append(payload)
        return {"text": "answer", "usage": {}}

    async def fake_preflight(message, doc_number, preflight):
        seen["preflight"].append({"doc": doc_number, **preflight})
        return {"text": "answer", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "pick_rule_areas", fake_pick)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    monkeypatch.setattr(assistant_llm, "narrate_preflight", fake_preflight)
    return seen


async def test_a_rule_question_refused_as_not_data_gets_the_rules(admin_client, monkeypatch):
    """"NC 订单多久同步一次？" was answered "not in my data, ask the admin"."""
    seen = _stub_llm(monkeypatch, areas=["nc_sync"], plan={
        "kind": "cannot", "reason": "not_a_data_question",
        "explanation": "这是系统配置问题，请联系系统管理员。"})
    r = await admin_client.post(CHAT, json={"message": "NC 订单多久同步一次？"})
    body = r.json()
    assert body["kind"] == "rules", body
    payload = seen["guide"][0]
    assert payload["kind"] == "rules"
    rules = {x["id"]: x["answer"] for a in payload["areas"] for x in a["rules"]}
    assert "every 60 minutes" in rules["nc_sync_schedule"]
    assert "系统管理员" not in body["answer"]


async def test_no_rule_reply_names_nobody_to_ask(admin_client, monkeypatch):
    _stub_llm(monkeypatch, areas=[], plan={
        "kind": "cannot", "reason": "not_a_data_question",
        "explanation": "Ask the Vendor Manager."})
    body = (await admin_client.post(CHAT, json={"message": "what is the weather"})).json()
    assert body["kind"] == "cannot_answer" and body["reason"] == "no_rule"
    assert "Vendor Manager" not in body["answer"]
    assert "Purchase orders" in body["answer"]


async def test_explain_rules_brings_the_engines_rules(admin_client, monkeypatch):
    from app.services import approval_client

    async def fake_forward(method, path, token, json=None):
        assert path == "/workflows/rules"
        return 200, {"rules": [
            {"id": "returned_document_resumes_at_the_returning_step",
             "area": "approval_engine", "answer": "resumes at the step"},
            {"id": "delegation_is_one_level", "area": "delegation", "answer": "one level"}]}

    monkeypatch.setattr(approval_client, "forward", fake_forward)
    seen = _stub_llm(monkeypatch, plan={"kind": "rules", "areas": ["approval_engine"]})
    await admin_client.post(CHAT, json={"message": "请购单被退回后重新提交，会从第一步重新审批吗？"})
    areas = seen["guide"][0]["areas"]
    ids = [x["id"] for x in areas[0]["rules"]]
    assert ids[0] == "returned_document_resumes_at_the_returning_step"
    assert "delegation_is_one_level" not in ids          # another area's
    assert "approval_buttons" in ids                      # the local card too


async def test_an_unreachable_engine_is_named_not_filled_in(admin_client, monkeypatch):
    from app.services import approval_client

    async def down(*a, **kw):
        raise ConnectionError("down")

    monkeypatch.setattr(approval_client, "forward", down)
    seen = _stub_llm(monkeypatch, plan={"kind": "rules", "areas": ["delegation"]})
    body = (await admin_client.post(CHAT, json={"message": "代理人能再委托吗？"})).json()
    assert body["sources"]["complete"] is False
    assert seen["guide"][0]["unavailable"]


async def test_a_why_blocked_question_on_a_pa_is_not_a_fixed_reply(admin_client, monkeypatch):
    seen = _stub_llm(monkeypatch, areas=["pa"], plan={
        "kind": "check", "doc_type": "pa", "action": "submit"})
    body = (await admin_client.post(CHAT, json={"message": "为什么不能提交这张 PA？"})).json()
    assert "only check purchase requests" not in body["answer"]
    assert body["kind"] == "rules" and seen["picked"] == 1


async def test_why_no_gr_runs_the_gr_routes_own_gates(admin_client, monkeypatch):
    """"为什么 PO-800-2609-01 不能建收货单？" — a fully received PO (ISS-0017)."""
    from app.services import po_action_check, workflow_view

    po = SimpleNamespace(id=uuid.uuid4(), number="PO-800-2609-01", type=1,
                         status="fully_received", pr_id=None, is_prepaid=False,
                         signoff_status="draft")

    async def fake_resolve(db, scope, doc_type, number, doc_id):
        return po

    async def fake_perm(db, uid, role, key):
        return True

    monkeypatch.setattr(workflow_view, "resolve", fake_resolve)
    monkeypatch.setattr(po_action_check, "has_permission", fake_perm)
    seen = _stub_llm(monkeypatch, plan={
        "kind": "check", "doc_type": "po", "doc_number": "PO-800-2609-01",
        "action": "create_gr"})
    body = (await admin_client.post(CHAT, json={"message": "为什么 PO-800-2609-01 不能建收货单？"})).json()
    assert body["kind"] == "preflight"
    result = seen["preflight"][0]
    assert result["doc"] == "PO-800-2609-01" and result["allowed"] is False
    failed = {c["id"]: c["message"] for c in result["checks"] if not c["passed"]}
    assert "issued / partially_received" in failed["po_status_allows_a_receipt"]


async def test_why_no_pa_names_the_missing_invoice_and_receipt(monkeypatch):
    """"为什么我不能为 PO-576-2610-01 创建付款申请？" — issued, nothing received,
    no invoice (ISS-0017)."""
    from app.api.v1 import pa as pa_api
    from app.crud import po as po_crud
    from app.services import po_action_check

    async def no(*a, **kw):
        return False

    async def nothing(*a, **kw):
        return set()

    async def none(*a, **kw):
        return None

    async def yes(*a, **kw):
        return True

    monkeypatch.setattr(po_crud, "has_any_invoice", no)
    monkeypatch.setattr(po_crud, "payable_invoice_po_ids", nothing)
    monkeypatch.setattr(po_crud, "po_has_three_way_matched_invoice", no)
    monkeypatch.setattr(pa_api, "open_prepayment_pa_number", none)
    monkeypatch.setattr(po_action_check, "has_permission", yes)
    po = SimpleNamespace(id=uuid.uuid4(), number="PO-576-2610-01", type=4,
                         status="issued", pr_id=None, is_prepaid=False)
    out = await po_action_check.check(None, {"sub": str(uuid.uuid4()), "role": "ap_clerk"},
                                      po, "create_pa", "t")
    failed = {c["id"] for c in out["checks"] if not c["passed"]}
    assert failed == {"an_invoice_to_pay", "three_way_matched_invoice"}
    assert out["not_checked"] and out["complete"]


def test_the_pa_check_uses_create_pas_own_queries():
    from app.api.v1 import pa as pa_api

    create = inspect.getsource(pa_api.create_pa)
    assert "open_prepayment_pa_number(db" in create
    assert "po_has_three_way_matched_invoice" in create
    assert pa_api.PREPAYMENT_CLOSED_STATUSES == ("cancelled", "rejected", "processed")


# ── ISS-0049: overdue is counted, never inferred from urgent ────────────────

def test_an_urgent_task_without_a_due_date_is_not_overdue():
    from app.services import guide_view

    s = guide_view.task_summary([
        {"type": "resolve_ap_duplicate_invoice", "priority": "urgent", "due_date": None,
         "created_at": "2026-10-01", "assigned_to_me_personally": False},
        {"type": "approve_pr", "priority": "normal", "due_date": None,
         "created_at": "2026-10-02", "assigned_to_me_personally": True}])
    assert s["urgent_count"] == 1
    assert s["with_due_date_count"] == 0 and s["overdue_count"] == 0
    assert "tasks_have_no_due_date" in {r["id"] for r in s["rules"]}


def test_no_task_is_created_with_a_due_date():
    """Every Task(...) in epms-api leaves due_date unset, and no raw insert
    names it — so nothing can be overdue."""
    offenders = []
    for path in (EPMS_API / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "Task(" not in text and "insert into tasks" not in text.lower():
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Task"
                    and any(k.arg == "due_date" for k in node.keywords)):
                offenders.append(f"{path.name}:{node.lineno}")
        for m in re.finditer(r"insert into tasks\s*\(([^)]*)\)", text, re.I):
            if "due_date" in m.group(1):
                offenders.append(f"{path.name}: raw insert")
    assert not offenders, offenders
    from app.crud import dashboard
    assert "Task.due_date < _today()" in inspect.getsource(dashboard.build_requester)


def test_the_task_inbox_has_no_mark_done():
    tasks_ts = _src("epms/src/services/tasks.ts")
    assert "No `complete()` wrapper on purpose" in tasks_ts
    assert "/complete" not in tasks_ts


# ── Purchase requests ────────────────────────────────────────────────────────

def test_budget_gate_applies_to_every_type_but_1():
    from app.services import doc_type_guide

    for t in range(2, 7):
        assert "budget_account_required" in {
            r["id"] for r in doc_type_guide.requirements_for("pr", t)}, t
    assert "budget_account_required" not in {
        r["id"] for r in doc_type_guide.requirements_for("pr", 1)}


def test_only_the_requester_edits_a_draft_or_returned_pr():
    from app.api.v1 import pr as pr_api

    src = inspect.getsource(pr_api.update_pr)
    assert 'pr.status not in ("draft", "returned")' in src
    assert 'user.get("role") != "system_admin"' in src
    assert "Only the requester who raised this PR can edit it" in src


def test_the_pr_page_offers_withdraw_on_in_review():
    """Known defect stated by withdraw_a_pr: shown on in_review, refused there.
    When the page stops offering it, this fails and the rule is updated."""
    page = _src("epms/src/pages/pr/PrDetailPage.tsx")
    assert "['draft', 'returned', 'submitted', 'in_review'].includes(pr.status) && !canApprove" in page
    assert "action: 'cancel'" in page


def test_pr_line_prices_may_be_zero_or_negative():
    from pydantic import ValidationError

    from app.schemas.pr import PrLineItemIn

    fields = PrLineItemIn.model_fields
    base = {k: "x" for k, f in fields.items() if f.is_required() and k not in ("qty", "unit_price")}
    PrLineItemIn(**base, qty=Decimal("1"), unit_price=Decimal("-5"))
    PrLineItemIn(**base, qty=Decimal("1"), unit_price=Decimal("0"))
    with pytest.raises(ValidationError):
        PrLineItemIn(**base, qty=Decimal("0"), unit_price=Decimal("1"))
    page = _src("epms/src/pages/pr/PrCreatePage.tsx")
    assert "estimatedAmount === 0" in page and "At least one line must have a price" in page


def test_changing_the_department_clears_the_budget_fields():
    for page in ("PrCreatePage.tsx", "PrEditPage.tsx"):
        src = _src(f"epms/src/pages/pr/{page}")
        block = src.split("department changed → clear cost-center cascade")[1][:400]
        for setter in ("setSelectedCostCenter('')", "setSelectedL2('')", "setFactorCombo({})"):
            assert setter in block, (page, setter)


def test_decomposition_factors_are_required_on_submit_only():
    factor = _src("budget-api/app/crud/factor.py")
    assert "MAX_FACTORS_PER_ACCOUNT = 3" in factor
    page = _src("epms/src/pages/pr/PrCreatePage.tsx")
    assert "accountFactors.filter((f) => !factorCombo[f.factor_code])" in page
    assert "Select a value for:" in page


def test_pr_list_filters():
    page = _src("epms/src/pages/pr/PrListPage.tsx")
    for text in ("Search by PR#, title, vendor, budget code", "All Departments",
                 "disabled={isRequesterOnly}", "My Purchase Requisitions", "prepaidFilter"):
        assert text in page, text


def test_a_pr_is_copied_from_the_list():
    assert "/pr/new?copyFrom=" in _src("epms/src/pages/pr/PrListPage.tsx")
    assert "Copy of " in _src("epms/src/pages/pr/PrCreatePage.tsx")


def test_the_pr_pdf_is_made_on_approval():
    from app.api.v1 import pr as pr_api
    from app.api.v1 import pr_attachments

    assert pr_attachments._PDF_STATUSES == {"approved"}
    src = inspect.getsource(pr_api)
    assert 'if pr.status == "approved":' in src and "_generate_pr_pdf_background(pr_id" in src


async def test_a_zero_or_negative_pr_is_never_over_budget():
    from app.crud.pr import compute_budget_check

    for amount in (Decimal("0"), Decimal("-250")):
        assert await compute_budget_check("CRM0090202", uuid.uuid4(), amount) == (False, None)


async def test_an_unreachable_budget_service_skips_the_catalogue_check(monkeypatch):
    from fastapi import HTTPException

    from app.services import budget_client

    async def unreachable(token, code):
        return None

    async def unknown(token, code):
        return False

    monkeypatch.setattr(budget_client, "account_code_is_known", unreachable)
    await budget_client.ensure_known_budget_code("t", "ANY-CODE")      # accepted
    monkeypatch.setattr(budget_client, "account_code_is_known", unknown)
    with pytest.raises(HTTPException):
        await budget_client.ensure_known_budget_code("t", "BAD-CODE")


# ── Purchase orders and NC ───────────────────────────────────────────────────

def test_a_local_po_is_edited_only_in_draft_or_returned():
    from app.api.v1 import po as po_api

    assert 'po.status not in ("draft", "returned")' in inspect.getsource(po_api.update_po)
    imported = [r for r in po_api.router.routes if r.path.endswith("/imported-details")]
    assert imported and "Only NC-imported POs can be edited here" in inspect.getsource(po_api)


def test_nc_po_edit_fields_come_from_the_schema():
    from app.schemas.po import PoImportedDetailsUpdate, PoImportedLineUpdate

    header = set(PoImportedDetailsUpdate.model_fields) - {"lines", "manual_lines"}
    assert header == set(rule_facts.IMPORTED_HEADER_WORDS)
    assert set(PoImportedLineUpdate.model_fields) - {"id"} == set(rule_facts.IMPORTED_LINE_WORDS)
    # Read-only means absent from the schema.
    for absent in ("vendor_id", "currency", "budget_code"):
        assert absent not in PoImportedDetailsUpdate.model_fields
    words = rule_facts.po_imported_fields(None)
    assert "Expected Delivery" in words["header_fields"] and "Sample" in words["line_fields"]
    seed = _src("identity-api/scripts/seed_phase2_keys.py")
    assert '"epms.po.edit_imported": ("system_admin", "erp_pa_officer")' in seed


def test_place_order_needs_po_write_on_an_approved_po():
    from app.api.v1 import po as po_api
    from app.crud import po as po_crud

    crud = inspect.getsource(po_crud.place_order)
    assert 'po.status != "approved"' in crud and '"issued"' in crud
    route = next(r for r in po_api.router.routes if r.path.endswith("/place-order"))
    assert "PoWriteDep" in inspect.getsource(route.endpoint)
    page = _src("epms/src/pages/po/PoDetailPage.tsx")
    assert "const canPlaceOrder = isProcurementOfficer && po?.status === 'approved'" in page


def test_there_is_no_close_po_route():
    from app.api.v1 import po as po_api

    assert not [r.path for r in po_api.router.routes if "close" in r.path.lower()]


def test_signoff_offers_sign_and_return_but_no_reject():
    from app.api.v1 import po_signoff

    paths = {r.path.rsplit("/", 1)[-1] for r in po_signoff.router.routes}
    assert {"submit", "sign", "return"} <= paths
    assert not {p for p in paths if "reject" in p or "cancel" in p}


def test_signoff_statuses():
    from app.crud import po_signoff

    assert po_signoff.SIGNOFF_SOURCES == ("nc",)
    assert po_signoff._SUBMITTABLE_PO_STATUSES == ("nc_pending", "issued", "nc_milk")


async def test_nc_schedule_is_read_from_config():
    for raw, said in ((None, "every 60 minutes"), (30, "every 30 minutes"),
                      (0, "only when someone presses Sync NC")):
        cfg = SimpleNamespace(nc_purchase_sync_interval_minutes=raw)
        assert said in (await rule_facts.nc_sync_schedule(_FakeDb(cfg)))["schedule"]
    from app.api.v1.nc_purchase_sync import _SYNC_ROLES
    assert _SYNC_ROLES == frozenset(("system_admin", "procurement_officer", "erp_pa_officer"))


def test_withdrawn_pending_orders_are_tagged():
    from app.services.nc_purchase_sync import writer

    assert writer._WITHDRAWN_TAG == "[NC Pending Withdrawn]"
    src = inspect.getsource(writer.reconcile_pending)
    assert "status='cancelled'" in src.replace(" ", "") or "status = 'cancelled'" in src


def test_number_suffix_limit():
    from app.services.nc_purchase_sync import writer

    assert writer._MAX_NUMBER_SUFFIX == 50


def test_nc_create_pa_tasks_go_to_the_erp_pa_officer():
    from app.api.v1 import invoices
    from app.crud import task

    assert '"erp_pa_officer", None' in inspect.getsource(invoices._create_or_renotify_create_pa)
    assert '"erp_pa_officer"' in inspect.getsource(task._backfill_create_pa_tasks)


def test_milk_orders_are_kept_out_of_receipts_and_matching():
    from app.api.v1.gr import PHYSICAL_GR_PO_STATUSES, SERVICE_GR_PO_STATUSES
    from app.api.v1.invoices import _MATCHABLE_PO_STATUSES
    from app.services.po_action_check import PA_PAGE_PO_STATUSES

    for statuses in (PHYSICAL_GR_PO_STATUSES, SERVICE_GR_PO_STATUSES,
                     _MATCHABLE_PO_STATUSES, PA_PAGE_PO_STATUSES):
        assert "nc_milk" not in statuses
    labels = rule_facts.nc_statuses(None)
    assert "Milk order" in labels["nc_milk"] and "Not yet approved in NC" in labels["nc_pending"]
    page = _src("epms/src/pages/po/PoDetailPage.tsx")
    assert "['issued', 'partially_received', 'fully_received'].includes(po.status)" in page


# ── Goods receipts ───────────────────────────────────────────────────────────

def test_gr_statuses_are_the_routes():
    from app.api.v1 import gr as gr_api

    src = inspect.getsource(gr_api.create_gr)
    assert "SERVICE_GR_PO_STATUSES" in src and "PHYSICAL_GR_PO_STATUSES" in src
    words = rule_facts.gr_po_statuses(None)
    assert words == {"physical": "issued and partially_received",
                     "service": "approved, issued and partially_received"}


def test_a_damaged_line_raises_an_urgent_task_for_procurement():
    from app.crud import gr as gr_crud

    src = inspect.getsource(gr_crud._create_damage_report_task)
    assert '"gr_damage_report"' in src and '"urgent"' in src and '"procurement_officer"' in src
    assert 'item.condition not in ("good", None)' in inspect.getsource(gr_crud.create)


def test_nothing_reads_the_gr_notification_sla():
    """Stored and editable, acted on by nothing (no_escalation_for_goods_receipts)."""
    allowed = {"models/config.py", "crud/config.py", "schemas/config.py"}
    readers = sorted(
        str(p.relative_to(EPMS_API / "app")) for p in (EPMS_API / "app").rglob("*.py")
        if "gr_notification_sla" in p.read_text(encoding="utf-8"))
    assert set(readers) <= allowed, readers


async def test_service_gr_reminder_facts_are_read_from_config():
    from app.tasks import service_gr_due

    off = await rule_facts.service_gr_reminders(_FakeDb(SimpleNamespace(
        service_gr_sla={}, notification_settings={})))
    assert (off["reminder_days"], off["escalation_days"]) == (1, 3)
    assert off["state"].startswith("switched off")
    dry = await rule_facts.service_gr_reminders(_FakeDb(SimpleNamespace(
        service_gr_sla={"reminder_days": 2, "manager_escalation_days": 5},
        notification_settings={"service_gr_due_enabled": True})))
    assert (dry["reminder_days"], dry["escalation_days"]) == (2, 5)
    assert "dry run" in dry["state"]
    live = await rule_facts.service_gr_reminders(_FakeDb(SimpleNamespace(
        service_gr_sla={}, notification_settings={"service_gr_due_enabled": True,
                                                   "service_gr_due_dry_run": False,
                                                   "followup_time": "07:30"})))
    assert "live" in live["state"] and live["time"] == "07:30"
    # The sweep reads the same keys with the same fallbacks.
    src = inspect.getsource(service_gr_due)
    assert 'get("service_gr_due_enabled", False)' in src
    assert 'get("service_gr_due_dry_run", True)' in src
    assert "reminded_before" in src


# ── Payment applications ─────────────────────────────────────────────────────

def test_pa_window_is_the_routes():
    from app.api.v1.pa import PA_INVOICE_DUE_WINDOW_DAYS

    assert rule_facts.pa_invoice_window(None) == {"window": PA_INVOICE_DUE_WINDOW_DAYS}


def test_an_open_prepayment_blocks_a_regular_pa():
    from app.api.v1 import pa as pa_api

    create = inspect.getsource(pa_api.create_pa)
    assert "is still open. Settle or cancel it before creating a regular PA" in create
    assert "A prepayment PA already exists for this PO" in create


def test_prepayment_cap_reads_the_vendor_then_the_system():
    from app.api.v1 import pa as pa_api

    create = inspect.getsource(pa_api.create_pa)
    assert "vendor.max_prepayment_pct" in create
    assert "This vendor does not allow prepayment" in create
    # ISS-0066: the Prepaid flag on PRs/POs plays no part in creating a PA.
    assert "is_prepaid" not in create


def test_pa_tax_is_on_the_lines_only():
    from app.admin import recompute

    row = SimpleNamespace(tax_rate=Decimal("0.13"), shipping_amount=Decimal("50"),
                          other_charges=Decimal("20"), prepayment_applied=Decimal("0"))
    lines = [{"line_total": Decimal("100")}]
    out = recompute._pa(row, lines)
    assert out["tax_amount"] == Decimal("13.00")          # 13% of the lines only
    assert out["payment_amount"] == Decimal("183.00")     # + freight + other, untaxed
    assert "never taxed" in " ".join(recompute.recompute_pa_header_only.__doc__.split())
    page = _src("epms/src/pages/pa/PaCreatePage.tsx")
    assert "setTaxAmount(invoiceTax.toFixed(2))" in page
    assert "formCurrency === 'CAD' && subtotalNum > 0" in page
    assert "(agreement?.tax_rate != null ? Number(agreement.tax_rate) : 0.13)" in page


def test_remittance_is_sent_by_hand_and_not_modelled():
    from app.core.ontology import REGISTRY

    assert not [e for e in REGISTRY.values()
                if e.model.__tablename__ == "payment_remittance_notifications"]
    # Paying sends nothing to the vendor; sending is its own route.
    execute = _src("finance-api/app/crud/payment_execute.py")
    assert "send_email" not in execute and "send_remittance" not in execute
    routes = _src("finance-api/app/api/v1/remittance.py")
    assert "/remittance/send" in routes


def test_claimed_invoices_are_hidden_by_the_form_only():
    from app.api.v1 import pa as pa_api

    page = _src("epms/src/pages/pa/PaCreatePage.tsx")
    assert "if (['cancelled', 'rejected'].includes(pa.status)) continue" in page
    assert "lockedInvoiceIds" in page
    # The server does not check claims on create (one_invoice_one_pa says so).
    assert "payable_invoice_po_ids" not in inspect.getsource(pa_api.create_pa)


# ── Invoices ─────────────────────────────────────────────────────────────────

async def test_match_tolerance_is_read_and_not_editable():
    from app.models.config import CompanyConfig
    from app.schemas.config import ConfigUpdate

    assert "invoice_match_tolerance_pct" not in ConfigUpdate.model_fields
    assert CompanyConfig.__table__.c.invoice_match_tolerance_pct.server_default.arg == "5"
    got = await rule_facts.invoice_match_tolerance(_FakeDb(SimpleNamespace(
        invoice_match_tolerance_pct=Decimal("5.00"))))
    assert got == {"pct": "5"}


def test_duplicate_invoice_numbers_are_caught_by_the_form_only():
    from app.api.v1 import invoices
    from app.models.invoice import Invoice

    page = _src("epms/src/pages/invoices/InvoiceListPage.tsx")
    assert "i.vendor_invoice_number.toLowerCase() === vendorInvoiceNumber.trim().toLowerCase()" in page
    assert not Invoice.__table__.c.vendor_invoice_number.unique
    unique_cols = {tuple(c.name for c in con.columns)
                   for con in Invoice.__table__.constraints if hasattr(con, "columns")}
    assert not [cols for cols in unique_cols if "vendor_invoice_number" in cols]
    assert "Duplicate" not in inspect.getsource(invoices.upload_invoice)


def test_invoice_due_date_rules_are_the_forms():
    from app.models.invoice import Invoice

    page = _src("epms/src/pages/invoices/InvoiceListPage.tsx")
    assert "if (fields.dueDate)" in page and "fields.paymentTermsNetDays" in page
    assert "30 * 86400000" in page
    # Nothing records which of the three set it.
    assert not [c for c in Invoice.__table__.c.keys() if "due_date" in c and c != "due_date"]


def test_non_po_fee_lines_are_paid_but_not_matched():
    from app.crud import pa_lines
    from app.schemas.invoice import InvoiceLineItem

    assert "non_po_fee" in InvoiceLineItem.model_fields
    assert 'li.get("non_po_fee")' in inspect.getsource(pa_lines)


# ── Agreements ───────────────────────────────────────────────────────────────

def test_nothing_moves_an_agreement_to_expired_or_closed():
    writes = [f"{p.name}" for p in (EPMS_API / "app").rglob("*.py")
              if re.search(r"status\s*=\s*['\"]expired['\"]", p.read_text(encoding="utf-8"))]
    assert not writes, writes


async def test_agreement_periods_are_queryable(test_engine, admin_client):
    """"协议 AGR-… 这个月的服务确认了吗？" is a row with who and when (ISS-0022 #1)."""
    from jose import jwt
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import settings
    from app.models.agreement_schedule import AgreementPaymentSchedule
    from app.models.user import User
    from app.models.vendor import Vendor
    from tests.test_agreement_invoice_match import _make_active_agreement

    token = admin_client.headers["Authorization"].split()[1]
    uid = uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                               algorithms=[settings.JWT_ALGORITHM])["sub"])
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Period Vendor", category="Services",
                   contact_name="A", contact_email="a@t.test")
        db.add(v)
        await db.commit()
        name = (await db.get(User, uid)).full_name
    agr = await _make_active_agreement(test_engine, v.id, uid, agreement_type="recurring")
    async with factory() as db:
        from datetime import datetime, timezone
        db.add_all([
            AgreementPaymentSchedule(agreement_id=agr.id, schedule_type="period", sequence=1,
                                     period_label="2026-09", expected_date=date(2026, 9, 30),
                                     status="pending"),
            AgreementPaymentSchedule(agreement_id=agr.id, schedule_type="period", sequence=2,
                                     period_label="2026-10", expected_date=date(2026, 10, 31),
                                     status="received", accepted_by=uid,
                                     accepted_at=datetime(2026, 10, 4, 20, 20, tzinfo=timezone.utc)),
        ])
        await db.commit()
    from app.services import controlled_query as cq

    query = {
        "entity": "agreement_period",
        "select": ["period_label", "status", "confirmed_by_name", "accepted_at"],
        "where": [{"field": "agreement.number", "op": "eq", "value": agr.number}],
        "order_by": {"field": "sequence"}}
    # The agreement scope itself is the ontology's (scope_agreement_period);
    # an unrestricted reader with the agreement permission.
    scope = {"perms": {"epms.agreement.read": True}, "agr_subq": None,
             "user_id": uid, "role": "system_admin"}
    async with factory() as db:
        result = await cq.execute(db, query, scope)

    class _R:
        @staticmethod
        def json():
            return result
    r = _R()
    rows = r.json()["rows"]
    assert [x["period_label"] for x in rows] == ["2026-09", "2026-10"], r.json()
    assert rows[0]["confirmed_by_name"] is None
    assert rows[1]["confirmed_by_name"] == name and rows[1]["accepted_at"]


# ── Approval and delegation (the EPMS side) ──────────────────────────────────

def test_approval_buttons_follow_the_open_task():
    po = _src("epms/src/pages/po/PoDetailPage.tsx")
    assert "t.document_id === po.id && t.type === 'approve_po'" in po
    pa = _src("epms/src/pages/pa/PaDetailPage.tsx")
    assert "hasApproveTask" in pa and "'approve_pa'" in pa
    pr = _src("epms/src/pages/pr/PrDetailPage.tsx")
    assert "(hasApproveTask || isAdmin || (!tasksLoaded && roleMatchesStep))" in pr


def test_the_delegation_page_is_in_the_admin_sidebar():
    nav = _src("portal/src/components/layout/navConfig.tsx")
    admin = nav.split("title: 'ADMIN'")[1]
    assert re.search(r"label: 'Approval Delegation'.*adminOnly: true", admin)


def test_a_delegate_sees_approval_tasks_only():
    from app.crud import task

    src = inspect.getsource(task.get_for_role)
    assert 'Task.type.like("approve%")' in src
    assert not "sign_po".startswith("approve")


# ── Notifications ────────────────────────────────────────────────────────────

def test_payment_applications_have_no_reminder():
    from app.api.v1 import pa as pa_api
    from app.api.v1 import po as po_api
    from app.api.v1 import pr as pr_api
    from app.services.manual_reminder import APPROVAL_TASK_TYPES, COOLDOWN_HOURS

    def remind(router):
        return [r.path for r in router.routes if r.path.endswith("/remind")]

    assert remind(pr_api.router) and remind(po_api.router) and not remind(pa_api.router)
    assert set(APPROVAL_TASK_TYPES) == {"pr", "po"}
    assert rule_facts.reminder_rules(None) == {"hours": COOLDOWN_HOURS,
                                               "documents": "purchase requests and purchase orders"}


async def test_approval_email_policy_is_read_from_config():
    default = await rule_facts.approval_email_policy(_FakeDb(SimpleNamespace(
        notification_settings={})))
    assert default["approve_pa"] == "no email" and default["approve_pa_default"] == "off"
    assert default["approve_pr"] == "an email at once"
    set_ = await rule_facts.approval_email_policy(_FakeDb(SimpleNamespace(
        notification_settings={"task_notifications": {
            "approve_pa": {"email": True, "mode": "digest"}}})))
    assert set_["approve_pa"] == "a line in the daily digest email"


async def test_digest_time_is_read_from_config():
    from app.tasks import daily_followup

    assert (await rule_facts.digest_time(_FakeDb(SimpleNamespace(
        notification_settings={"followup_time": "09:30"}))))["time"] == "09:30"
    assert (await rule_facts.digest_time(_FakeDb(None)))["time"] == "08:00"
    assert 'get("followup_time")' in inspect.getsource(daily_followup._load_schedule)


def test_no_screen_edits_email_templates():
    panel = _src("epms/src/pages/admin/AdminPanel.tsx")
    assert re.search(r"case 'notifications':\s*return <MovedToPortal", panel)
    portal = REPO / "portal" / "src"
    if not portal.exists():
        pytest.skip("portal not mounted")
    hits = [p.name for p in portal.rglob("*.tsx")
            if "email_templates" in p.read_text(encoding="utf-8")]
    assert not hits, hits


# ── Dashboards and reports ───────────────────────────────────────────────────

def test_system_health_is_a_constant():
    from app.crud import dashboard

    assert 'KpiCard(title="System Health", value="Healthy")' in inspect.getsource(
        dashboard.build_system_admin)


def test_the_three_committed_figures():
    from app.crud import dashboard

    for fn in (dashboard.build_approver, dashboard.build_procurement):
        assert '"Total Committed (FY)"' in inspect.getsource(fn)
    src = inspect.getsource(dashboard)
    assert 'PurchaseOrder.status.notin_(["cancelled", "closed", "draft"])' in src
    assert "total_committed=_ZERO" in inspect.getsource(dashboard._budget_overview)
    assert "_budget_overview" in inspect.getsource(dashboard.build_cfo)


def test_report_types_are_the_routers():
    from app.api.v1.reports import router

    got = rule_facts.report_types(None)
    assert got["count"] == len(router.routes) == 7
    assert got["reports"].startswith("Purchase Requisitions")


# ── Vendors and the account ──────────────────────────────────────────────────

def test_vendor_pickers_list_active_vendors_only():
    for page in ("pr/PrCreatePage.tsx", "po/PoCreatePage.tsx", "agreements/AgreementCreatePage.tsx"):
        assert "active_only: true" in _src(f"epms/src/pages/{page}"), page


def test_vendor_edits_leave_no_history():
    from app.admin.registry import REGISTRY
    from app.api.v1 import vendors

    assert "vendor" not in REGISTRY
    assert "audit" not in inspect.getsource(vendors.update_vendor).lower()


def test_sign_in_codes_last_ten_minutes():
    auth = _src("identity-api/app/api/v1/auth.py")
    assert "setex(otp_redis_key(str(user.id)), 600, otp)" in auth
    assert auth.count(", 600, otp)") == 2      # sign-in and resend


def test_idle_sign_out_after_an_hour():
    assert "const IDLE_MS = 60 * 60 * 1000" in _src("epms/src/hooks/useIdleTimeout.ts")


def test_notification_channel_is_a_personal_setting():
    page = _src("epms/src/pages/profile/ProfilePage.tsx")
    for key in ("email_only:", "teams_only:", "both:", "none:"):
        assert key in page.split("NOTIFICATION_CHANNEL_LABELS")[1][:300], key
    user = _src("identity-api/app/models/user.py")
    assert re.search(r"notification_channel.*email_only", user)


def test_the_signature_is_set_in_my_profile():
    page = _src("epms/src/pages/profile/ProfilePage.tsx")
    assert 'SectionCard title="My Signature"' in page


def test_a_manual_credit_needs_its_email_on_screen():
    modal = _src("epms/src/pages/vendorcredits/ManualCreditModal.tsx")
    assert "notes: !notes.trim()," in modal and "files: files.length === 0," in modal
    drawer = _src("epms/src/pages/vendorcredits/CreditDetailDrawer.tsx")
    assert "credit.source === 'manual' && atts.isSuccess && files.length === 0" in drawer


async def test_a_process_answer_carries_the_engines_rules(monkeypatch):
    """"请购单被退回后重新提交，会从第一步重新审批吗？" often lands on
    explain_process(pr); it now has the resume rule there too (ISS-0018)."""
    from app.services import approval_client, guide_view

    async def fake_forward(method, path, token, json=None):
        if path == "/workflows/rules":
            return 200, {"rules": [
                {"id": "returned_document_resumes_at_the_returning_step",
                 "area": "approval_engine", "answer": "resumes"},
                {"id": "delegation_is_one_level", "area": "delegation", "answer": "x"}]}
        return 200, {"doc_type": "pr", "steps": [{"role": "dept_manager"}]}

    monkeypatch.setattr(approval_client, "forward", fake_forward)
    got = await guide_view.process_steps("pr", "t")
    assert [r["id"] for r in got["engine_rules"]] == [
        "returned_document_resumes_at_the_returning_step"]
