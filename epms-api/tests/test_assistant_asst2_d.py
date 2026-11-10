"""Assistant round 2, group D — a rule question's conclusion is the rule's, by
code (app/services/rule_verdicts.py): ISS-0249, ISS-0026, ISS-0025, ISS-0027,
ISS-0029, ISS-0055, ISS-0017, ISS-0040, ISS-0065; and the totals of a cut
list are labelled by code (ISS-0032).

Most tests here make the model uncooperative on purpose — the planner picks
the wrong tool, the narrator opens with the opposite conclusion or leaves the
sentence out, or the model is unreachable — and check that the reply still
says what the rule says.
"""
import ast
import inspect
import re
from pathlib import Path

import pytest

from app.services import assistant_llm, rule_facts, rule_registry, rule_verdicts

CHAT = "/api/v1/assistant/chat"
EPMS_API = Path(__file__).resolve().parents[1]
REPO = EPMS_API.parent


def _src(rel: str) -> str:
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} is not mounted in this container")
    return path.read_text(encoding="utf-8")


def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _stub(monkeypatch, *, plan=None, narration="", unavailable=False):
    """A planner that answers `plan` (or the wrong tool), a narrator that
    writes `narration` and records what it was given."""
    seen = {"plan": 0, "guide": [], "narrate": []}

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        seen["plan"] += 1
        # Left to itself, the planner sent these to the PO approval chain.
        return dict(plan or {"kind": "process", "doc_type": "po"})

    async def fake_guide(message, payload):
        seen["guide"].append(payload)
        if unavailable:
            raise assistant_llm.LlmUnavailable("down")
        return {"text": narration, "usage": {}}

    async def fake_narrate(message, query, result):
        seen["narrate"].append(result)
        return {"text": narration, "usage": {}}

    async def fake_pick(message, history=None):
        return {"areas": [], "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate_guide", fake_guide)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    monkeypatch.setattr(assistant_llm, "pick_rule_areas", fake_pick)
    return seen


# ── The verdicts themselves ──────────────────────────────────────────────────

def _engine_rule_ids() -> set[str] | None:
    path = REPO / "approval-api/app/crud/engine_rules.py"
    if not path.exists():
        return None
    return set(re.findall(r'"id":\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))


async def test_every_verdict_rests_on_a_rule_and_names_tests_that_exist():
    local = {c["id"] for _, c in rule_registry.all_cards()}
    engine = _engine_rule_ids()
    ids = [v.id for v in rule_verdicts.verdicts()]
    assert len(ids) == len(set(ids)) and len(ids) >= 14
    for v in rule_verdicts.verdicts():
        assert v.rule in local or engine is None or v.rule in engine, f"{v.id}: no rule {v.rule}"
        assert v.says_en and v.says_zh, v.id
        assert v.asks and all(v.asks), v.id
        assert v.place in rule_verdicts.PLACES and v.route in rule_verdicts.ROUTES, v.id
        if v.derive:
            assert callable(getattr(rule_facts, v.derive, None)), v.id
        else:
            assert "{" not in v.says_en + v.says_zh, f"{v.id}: placeholder without derive"
        assert v.test, f"{v.id}: a verdict with no test behind it is a guess"
        for ref in v.test:
            file, _, name = ref.partition("::")
            path = (EPMS_API if file.startswith("tests/") else REPO) / file
            if not path.exists():
                assert not file.startswith("tests/"), f"{v.id}: {file} does not exist"
                continue        # another service's tree is not mounted
            assert name in _defined_tests(path), f"{v.id}: no {name} in {file}"
        said = await rule_verdicts.sentences(v, None)
        assert "{" not in said["en"] + said["zh"], v.id


# Every failing question from the tracker, word for word, and the verdict it gets.
TRACKER_QUESTIONS = [
    ("货还没收到，发票可以先匹配吗？", "invoice_can_be_matched_before_receipt"),            # 0249
    ("我能把不同供应商的几张发票放进同一张付款申请吗？", "one_pa_one_vendor"),                # 0249
    ("不同供应商的发票能放进同一张 PA 吗？", "one_pa_one_vendor"),
    ("我自己就是第一审批人，提交后还需要自己再批吗？", "requester_who_is_first_approver_still_approves"),  # 0026
    ("超预算模式改了以后，已经提交、正在审批中的请购单会受影响吗？", "mode_change_reaches_in_flight_prs"),  # 0025
    ("超预算模式改了以后，审批中的请购单会受影响吗？", "mode_change_reaches_in_flight_prs"),
    ("改成 Allow 以后，已经在审批中的请购单还要走超预算审批步骤吗？",
     "allow_mode_skips_pending_over_budget_steps"),
    ("我要休假，怎么让同事帮我审批？", "delegation_is_set_up_in_portal"),                  # 0027
    ("Absence Cover 怎么设置？", "delegation_is_set_up_in_portal"),
    ("实物收货的流程是怎样的？", "the_requester_acknowledges_and_collects"),                # 0029
    ("PO 已经下单了，货到了以后谁要做什么？我是申请人。", "the_requester_acknowledges_and_collects"),
    ("服务类 PO 谁来确认？怎么确认？", "service_receipt_is_the_owners_or_the_requesters"),
    ("服务类采购由谁确认收货？", "service_receipt_is_the_owners_or_the_requesters"),         # 0055
    ("项目类请购单提交前必须填哪些内容？", "service_and_project_prs_name_an_owner"),
    ("Service/Project Owner 可以不填吗？可以选其他部门的人吗？", "owner_is_required_and_anyone"),
    ("服务做完了，我是申请人但不是服务负责人，我可以确认收货吗？", "the_requester_can_confirm_a_service"),
    ("已经全部收货的采购订单还能再建收货单吗？", "fully_received_po_takes_no_receipt"),          # 0017
    ("为什么这几张发票不能放在同一张 PA 里？", "why_invoices_cannot_share_a_pa"),              # 0040
    ("Milk / NC 状态是什么意思？", "what_milk_and_nc_pending_mean"),                       # 0065 #1
    ("直接付款申请的审批流程是怎样的？现在还能建吗？", "direct_pa_can_be_created"),              # 0065 #8
    ("Can an invoice be matched before the goods are received?",
     "invoice_can_be_matched_before_receipt"),
]


@pytest.mark.parametrize("question,expected", TRACKER_QUESTIONS)
async def test_each_tracker_question_gets_its_verdict(question, expected):
    v = rule_verdicts.match(question)
    assert v is not None and v.id == expected, (question, v and v.id)


@pytest.mark.parametrize("question", [
    "为什么 PO-800-2609-01 不能建收货单？",            # a document: its own checks answer
    "为什么我不能为 PO-576-2610-01 创建付款申请？",
    "没收到货能建 PA 吗？",                          # the PA's receipt gate, not matching
    "设置代理后，代理人能处理我哪些任务？",              # what a delegate may do
    "代理人能再委托给第三个人吗？",
    "Approval Routing 里的 GM backup 有什么用？",
    "服务类 PO 谁来审批？",                          # the approval chain IS the answer
    "PO 没有关联请购单的话，收货单谁来确认？",            # gr_without_pr
    "协议负责人必填吗？",                            # an agreement's owner
    "allow 模式下超预算还要写理由吗？",
    "这个月有多少张请购单？",
    "有哪些发票已经匹配了但货还没收到？",               # a data question, not the rule
])
async def test_other_questions_get_no_verdict(question):
    v = rule_verdicts.match(question)
    assert v is None, (question, v.id)


# ── The model does not cooperate ─────────────────────────────────────────────

async def test_a_verdict_opens_the_reply_whatever_the_narrator_writes(admin_client, monkeypatch):
    """ISS-0249: the narrator opens "不可以" over a body that says it can; the
    planner would have sent it elsewhere. The reply starts with the rule's
    conclusion and the wrong opening is gone."""
    seen = _stub(monkeypatch, narration="不可以。收货单完全不参与匹配的比较，所以发票可以在没有收到任何货物的情况下被匹配。")
    r = await admin_client.post(CHAT, json={"message": "货还没收到，发票可以先匹配吗？"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "rules" and body["verdict"] == "invoice_can_be_matched_before_receipt"
    assert seen["plan"] == 0, "answered from the rules without asking the planner"
    assert body["answer"].startswith("可以。匹配只比较金额")
    assert "不可以" not in body["answer"]
    assert "收货单完全不参与匹配" in body["answer"], "the narrator's body is kept"
    payload = seen["guide"][0]
    assert payload["kind"] == "rules" and payload["areas"][0]["area"] == "invoice"
    assert "Matching compares amounts only" in payload["placed_before_your_reply"]


@pytest.mark.parametrize("question,wrong,starts", [
    ("我能把不同供应商的几张发票放进同一张付款申请吗？", "可以。但是发票必须同一供应商。", "不能。"),
    ("我自己就是第一审批人，提交后还需要自己再批吗？",
     "不需要，系统会自动批准第一步。提交时不会自动批准。", "需要。提交时不会做"),
    ("超预算模式改了以后，审批中的请购单会受影响吗？", "不会。后来改变的模式不会应用到它身上。", "会，但只会变轻"),
    ("服务做完了，我是申请人但不是服务负责人，我可以确认收货吗？",
     "不可以，只有那位负责人才能确认。", "可以。服务/项目类的收货"),
    ("已经全部收货的采购订单还能再建收货单吗？", "可以。", "不能。实物（货物）PO 只有在 Issued 或 Partial Receipt"),
])
async def test_the_opposite_opening_is_replaced(admin_client, monkeypatch, question, wrong, starts):
    _stub(monkeypatch, narration=wrong)
    body = (await admin_client.post(CHAT, json={"message": question})).json()
    assert body["answer"].startswith(starts), body["answer"]
    assert wrong.split("。")[0] + "。" not in body["answer"]


@pytest.mark.parametrize("question,must", [
    ("服务类 PO 谁来确认？怎么确认？", ["Service/Project Owner", "申请人也随时可以确认", "不是 PO 的审批链"]),
    ("实物收货的流程是怎样的？", ["Acknowledge Goods Receipt", "Collect Goods"]),
    ("我要休假，怎么让同事帮我审批？", ["Portal → Admin", "More admin pages", "不在 EPMS"]),
    ("为什么这几张发票不能放在同一张 PA 里？", ["7 天", "供应商或币种不同", "把发票号或 PO 号发给我"]),
    ("Milk / NC 状态是什么意思？", ["不能建收货单", "可以在 EPMS 匹配", "在 NC 批准前不能收货、不能匹配发票、也不能付款"]),
    ("Service/Project Owner 可以不填吗？可以选其他部门的人吗？", ["必填", "不能为空", "其他部门"]),
])
async def test_what_must_be_said_is_said_when_the_narrator_says_nothing(
        admin_client, monkeypatch, question, must):
    """The narrator leaves everything out (an empty reply); the code-written
    sentence still carries the answer."""
    _stub(monkeypatch, narration="")
    body = (await admin_client.post(CHAT, json={"message": question})).json()
    for words in must:
        assert words in body["answer"], (words, body["answer"])


async def test_the_verdict_stands_when_the_model_is_unreachable(admin_client, monkeypatch):
    _stub(monkeypatch, unavailable=True)
    r = await admin_client.post(CHAT, json={"message": "我自己就是第一审批人，提交后还需要自己再批吗？"})
    assert r.status_code == 200, r.text
    assert r.json()["answer"].startswith("需要。")


async def test_an_english_question_gets_the_english_verdict(admin_client, monkeypatch):
    _stub(monkeypatch, narration="No, it must be received first.")
    body = (await admin_client.post(CHAT, json={
        "message": "Can an invoice be matched before the goods are received?"})).json()
    assert body["answer"].startswith("Yes. Matching compares amounts only")
    assert "No, it must" not in body["answer"]


async def test_a_kept_route_gets_the_sentence_under_its_reply(admin_client, monkeypatch):
    """ISS-0029 point 3: the document guide answers "what must a project PR
    have" and leaves the Owner out; the sentence is added under it."""
    seen = _stub(monkeypatch, plan={"kind": "doc_types", "doc_type": "pr"},
                 narration="项目类请购单需要：供应商、成本中心、预算科目、完成日期、项目号。")
    body = (await admin_client.post(CHAT, json={"message": "项目类请购单提交前必须填哪些内容？"})).json()
    assert body["kind"] == "document_types" and seen["plan"] == 1
    assert body["answer"].startswith("项目类请购单需要")
    assert body["answer"].rstrip().endswith("可以改成任何在职用户，不能留空。")


async def test_direct_pa_is_said_to_be_created_in_epms(admin_client, monkeypatch):
    """ISS-0065 point 8: the planner keeps its route (the chain); the narrator
    says it cannot be created; the reply opens with where it is created."""
    _stub(monkeypatch, plan={"kind": "cannot", "reason": "not_in_schema",
                             "explanation": "不能新建直接付款申请。"})
    body = (await admin_client.post(CHAT, json={
        "message": "直接付款申请的审批流程是怎样的？现在还能建吗？"})).json()
    assert body["answer"].startswith("可以，在 EPMS 里建")
    assert "不能新建" not in body["answer"]


async def test_a_question_naming_a_document_keeps_the_planners_route(admin_client, monkeypatch):
    seen = _stub(monkeypatch, plan={"kind": "cannot", "reason": "too_ambiguous",
                                    "explanation": "x"})
    body = (await admin_client.post(CHAT, json={
        "message": "PO-800-2609-01 已经全部收货了，还能再建收货单吗？"})).json()
    assert seen["plan"] == 1 and "verdict" not in body


def test_an_opening_word_inside_a_phrase_is_not_taken_for_a_verdict():
    keep = ["能否匹配取决于金额。", "是否需要看情况。", "对于这个问题，规则如下。", "会计期间无关。"]
    for text in keep:
        assert rule_verdicts.without_opening_verdict(text) == text
    assert rule_verdicts.without_opening_verdict("**不可以**，因为……。后面是说明。") == "后面是说明。"
    assert rule_verdicts.without_opening_verdict("No. Because x.") == "Because x."


# ── The facts the new cards and verdicts state ───────────────────────────────

def test_the_owner_is_any_active_user_and_never_empty():
    """ISS-0055: required on the form, defaults to the requester, any active
    user from any department; NULL means the requester, so never empty."""
    from app.api.v1 import pr as pr_api
    from app.crud import pr_owner

    page = _src("epms/src/pages/pr/PrCreatePage.tsx")
    assert 'Service/Project Owner <span className="text-danger-600">*</span>' in page
    assert "Defaults to you" in page
    assert "userService.directory({ search: ownerQuery || undefined })" in page, \
        "the picker searches everyone — no department filter"
    check = inspect.getsource(pr_api._check_owner)
    assert "is_active" in check and "department" not in check
    assert pr_owner._resolve(4, None, "requester") == "requester"
    assert pr_owner._resolve(6, "owner", "requester") == "owner"
    assert pr_owner._resolve(2, "owner", "requester") == "requester"


def test_the_receipt_steps_are_the_codes():
    from app.crud import gr as gr_crud
    from app.services.guide_view import TASK_TYPE_LABELS

    steps = rule_facts.gr_steps(None)
    assert steps == {"ack_task": TASK_TYPE_LABELS["acknowledge_gr"],
                     "collect_task": TASK_TYPE_LABELS["collect_goods"]}
    action = inspect.getsource(gr_crud.action)
    ack = action.split('if act == "acknowledge"')[1].split('elif act == "collect"')[0]
    assert 'gr.status = "collection_pending"' in ack and "_create_collect_task" in ack
    collect = action.split('elif act == "collect"')[1].split("elif act ==")[0]
    assert 'gr.status = "collected"' in collect and "_update_po_received_qty" in collect


def test_the_service_confirmer_is_the_owner_or_the_requester():
    """ISS-0029, ISS-0055: owner on the PR, OR its requester — not the PO."""
    from app.api.v1 import gr as gr_api

    src = inspect.getsource(gr_api.create_gr)
    assert "may_confirm = actor_id in {i for i in (pr_requester_id, pr_owner_id) if i}" in src
    page = _src("epms/src/pages/po/PoDetailPage.tsx")
    assert "po.pr_owner_id === user?.id" in page and "po.pr_requester_id === user?.id" in page


def test_nc_pending_is_not_received_matched_or_paid():
    from app.api.v1.gr import PHYSICAL_GR_PO_STATUSES, SERVICE_GR_PO_STATUSES
    from app.api.v1.invoices import _MATCHABLE_PO_STATUSES
    from app.services.po_action_check import PA_PAGE_PO_STATUSES

    for statuses in (PHYSICAL_GR_PO_STATUSES, SERVICE_GR_PO_STATUSES,
                     _MATCHABLE_PO_STATUSES, PA_PAGE_PO_STATUSES):
        assert "nc_pending" not in statuses


def test_the_delegation_card_and_verdict_say_portal_not_epms():
    rules = {r["id"]: r["answer"] for r in rule_registry.static_rules("delegation")}
    assert "in Portal, not in EPMS" in rules["where_delegation_is_set_up"]
    assert "left sidebar under ADMIN" not in rules["where_delegation_is_set_up"].split("Portal's")[0]


def test_the_mode_change_card_is_given_with_the_over_budget_rules():
    from app.services import guide_view

    ids = {r["id"] for r in guide_view.over_budget_rules()}
    assert "over_budget_mode_change_in_flight" in ids
    assert "over_budget_mode_change_in_flight" in {
        r["id"] for r in rule_registry.static_rules("budget")}


# ── ISS-0032: the totals of a cut list, labelled by code ─────────────────────

async def test_top_n_totals_are_both_said_and_labelled(test_engine, admin_client, monkeypatch):
    from tests.test_assistant_iss_batch2 import _two_currency_pos

    tag = await _two_currency_pos(test_engine, admin_client)
    query = {"entity": "purchase_order", "group_by": ["vendor_name"], "metrics": ["amount"],
             "where": [{"field": "number", "op": "like", "value": f"PO-CUR-{tag}"}],
             "order_by": {"field": "amount", "desc": True}, "limit": 2}
    # The narrator writes a "total" of its own after the list.
    seen = _stub(monkeypatch, plan={"kind": "query", "query": query},
                 narration="前 2 名：Big CAD 500.00，Small CAD 100.00。")
    body = (await admin_client.post(CHAT, json={"message": "今年采购金额前 2 的供应商是哪些？"})).json()
    result = seen["narrate"][0]
    assert "totals" not in result and "totals_shown" not in result, \
        "the narrator gets no total it could mislabel"
    answer = body["answer"]
    assert "以上 2 行合计：CAD 600.00" in answer
    assert "查询匹配到的全部结果合计（不只是以上 2 行）：CAD 600.00；USD 50.00" in answer


async def test_a_ranking_across_currencies_says_no_rate_is_applied(test_engine, admin_client, monkeypatch):
    from tests.test_assistant_iss_batch2 import _two_currency_pos

    tag = await _two_currency_pos(test_engine, admin_client)
    query = {"entity": "purchase_order", "group_by": ["vendor_name"], "metrics": ["amount"],
             "where": [{"field": "number", "op": "like", "value": f"PO-CUR-{tag}"}],
             "order_by": {"field": "amount", "desc": True}, "limit": 10}
    _stub(monkeypatch, plan={"kind": "query", "query": query}, narration="三行。")
    body = (await admin_client.post(CHAT, json={"message": "采购金额最多的供应商排名"})).json()
    assert "没有做汇率换算" in body["answer"]
    # One currency, no note.
    query["where"].append({"field": "currency", "op": "eq", "value": "CAD"})
    _stub(monkeypatch, plan={"kind": "query", "query": query}, narration="两行。")
    body = (await admin_client.post(CHAT, json={"message": "采购金额最多的供应商排名"})).json()
    assert "汇率" not in body["answer"]
