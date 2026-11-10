"""OA questions are answered from OA's rules, in code (ISS-0213).

Three blocks:

  * routing — every question the tester asked (description + the 04/11 and
    05/11 comments) is recognised as OA and lands on the card that answers it;
    Procurement questions are left to the planner;
  * the chat — with the models stubbed to do the wrong thing (a planner that
    would answer from the PR rules or refuse, a narrator that says "not part of
    EPMS"), the reply is still the OA rule;
  * the pins the cards of app/knowledge/rules/oa.yaml name — each reads the
    code that does what the card says (expense-api, approval-api, the OA
    pages) and skips when that tree is not mounted.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import assistant_llm, guide_view, oa_guide, rule_facts, rule_registry

EPMS_API = Path(__file__).resolve().parents[1]
REPO = EPMS_API.parent
CHAT = "/api/v1/assistant/chat"


def _src(rel: str) -> str:
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} is not mounted in this container")
    return path.read_text(encoding="utf-8")


# ── Routing ──────────────────────────────────────────────────────────────────

# (app the question was asked in, question, the card(s) that must answer it).
# D = the issue's description, C1 = comment 04/11 08:42, C2 = comment 04/11
# 10:13, C3 = comment 05/11.
TESTER_QUESTIONS = [
    # D
    ("portal", "Do I need a receipt for a mileage claim?", ["oa_mileage_needs_no_receipt"]),
    ("portal", "Can I claim travel expenses without a Travel Application?",
     ["oa_travel_expense_needs_an_approved_travel_application"]),
    ("portal", "My claim was returned. What should I do?", ["oa_returned_claim"]),
    ("portal", "Can I delete a draft expense claim?", ["oa_only_travel_applications_are_deleted"]),
    ("portal", "What happens if my meal is over the limit?", ["oa_meal_limits"]),
    ("oa", "Why is the Submit button grey?", ["oa_submit_needs_an_attachment"]),
    ("portal", "Who can pay a claim?", ["oa_who_pays_a_claim"]),
    ("portal", "How do I send a reminder for my claim?", ["oa_send_reminder"]),
    ("portal", "How do I submit a General Expense claim?", ["oa_how_to_submit_general_expense"]),
    ("oa", "What is the difference between Return and Reject?", ["oa_return_versus_reject"]),
    # C1
    ("oa", "Can I submit a claim if it is over the budget?", ["oa_over_budget_is_a_warning"]),
    ("oa", "Can I claim in USD?", ["oa_currency"]),
    ("oa", "Can I approve my own claim?", ["oa_claimant_never_approves"]),
    # C2
    ("oa", "I forgot a receipt after submitting. What can I do?",
     ["oa_attachments_only_while_editable"]),
    ("oa", "I paid in USD on a trip. How do I claim?",
     ["oa_travel_expense_needs_an_approved_travel_application", "oa_currency"]),
    ("oa", "Why can't I see my Travel Application when I create a Travel Expense?",
     ["oa_travel_application_picker"]),
    ("oa", "Can I claim a refund or a negative amount?", ["oa_negative_lines"]),
    ("oa", "Who is approving my claim now?", ["oa_approval_status"]),
    ("oa", "When will I get paid? How do I know?", ["oa_when_am_i_paid"]),
    ("oa", "What is the difference between General Expense and Travel Expense?",
     ["oa_general_versus_travel_expense"]),
    ("oa", "Can I use one Travel Application for several claims?",
     ["oa_one_travel_application_several_claims"]),
    ("oa", "Who can see my claim?", ["oa_who_sees_a_claim"]),
    ("oa", "I did not get an e-mail for my approval task.", ["oa_task_emails"]),
    ("oa", "Can I use OA on my phone?", ["oa_on_a_phone"]),
    ("oa", "Can I take back a claim I have submitted?", ["oa_no_recall"]),
    ("oa", "My colleagues and I are travelling together. Who claims the expenses?",
     ["oa_travelling_together"]),
    ("oa", "Scan filled the wrong amount. What do I do?", ["oa_scan_is_a_prefill"]),
    ("oa", "My claim was rejected. What now?", ["oa_rejected_claim"]),
    ("oa", "Can I edit my claim after I submit it?", ["oa_edit_only_draft_or_returned"]),
    ("oa", "What file types can I upload and what is the size limit?",
     ["oa_attachment_files"]),
    # C3
    ("oa", "I am the department manager. If I submit a travel expense, do I approve it myself?",
     ["oa_claimant_never_approves"]),
    ("oa", "If the Department Manager is also the GM, do they approve a general expense twice?",
     ["oa_same_approver_next_step"]),
    ("oa", "Can the applicant recall a Travel Application?", ["oa_no_recall"]),
    # The same, in Chinese
    ("oa", "没有出差申请能报差旅费吗？", ["oa_travel_expense_needs_an_approved_travel_application"]),
    ("portal", "报里程需要收据吗？", ["oa_mileage_needs_no_receipt"]),
    ("oa", "提交后能撤回吗？", ["oa_no_recall"]),
]


@pytest.mark.parametrize("app,question,expected", TESTER_QUESTIONS)
def test_every_tester_question_is_oa_and_reaches_its_rule(app, question, expected):
    assert oa_guide.is_oa_question(question, app), question
    picked = oa_guide.pick(question)
    for card in expected:
        assert card in picked, (question, picked)


@pytest.mark.parametrize("question,types", [
    ("What is the approval process for a Travel Expense?", ["trv"]),
    ("What is the approval chain for a Travel Application?", ["tra"]),
    ("What is the approval process for a mileage claim?", ["mil"]),
    ("Who approves my claim?", ["exp", "mil", "trv", "tra"]),
])
def test_an_oa_chain_question_names_its_chains(question, types):
    assert oa_guide.is_oa_question(question, "oa")
    assert oa_guide.process_types(question) == types


@pytest.mark.parametrize("app,question", [
    ("portal", "How many POs did I raise this month?"),
    ("oa", "How many POs did I raise this month?"),
    ("portal", "What is the difference between OA and Procurement?"),
    ("portal", "Which invoices are claimed against agreement AGR-0001?"),
    ("portal", "Can I submit a PR over budget?"),
])
def test_procurement_questions_stay_with_the_planner(app, question):
    assert not oa_guide.is_oa_question(question, app)


def test_a_claim_number_is_oa_anywhere():
    assert oa_guide.is_oa_question("where is exp20261016-1?", "epms")
    assert oa_guide.numbers("where is exp20261016-1?") == ["EXP-20261016-0001"]


def test_every_oa_card_has_a_title_and_match_patterns():
    for card in rule_registry.areas()["oa"].cards:
        assert card.get("title") and card.get("match"), card["id"]
        if card["provenance"] == "derived":
            assert card.get("fallback") and "{" not in card["fallback"], card["id"]


def test_a_refusal_or_a_procurement_answer_is_never_passed_on():
    for bad in ("Expense claims are handled in the OA module, not in EPMS purchasing, "
                "so I have no data about them.",
                "No. A purchase request of types 2 to 6 is checked against its budget.",
                "No — a payment application must be in the same currency as its POs."):
        said = oa_guide.guarded(bad)
        assert said.startswith("I have no OA rule") and "Receipts on a Mileage Claim" in said
    assert oa_guide.guarded("Yes, you can.") == "Yes, you can."


# ── The chat, with the models doing the wrong thing ─────────────────────────

def _uncooperative(monkeypatch, narration="Expense claims are handled in the OA module, "
                                          "not in EPMS purchasing, so I have no data."):
    """A planner that must not be asked, a rule picker that would pick the PR
    rules, a narrator that refuses."""
    calls = {"plan": 0, "narrate_guide": 0}

    async def plan(*a, **k):
        calls["plan"] += 1
        return {"kind": "rules", "areas": ["pr"]}

    async def pick(*a, **k):
        return {"areas": ["pr"], "usage": {}}

    async def guide(message, payload):
        calls["narrate_guide"] += 1
        return {"text": narration, "usage": {}}

    async def fixed(text, question, language):
        return {"text": text, "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", plan)
    monkeypatch.setattr(assistant_llm, "pick_rule_areas", pick)
    monkeypatch.setattr(assistant_llm, "narrate_guide", guide)
    monkeypatch.setattr(assistant_llm, "render_fixed_reply", fixed)
    return calls


async def test_an_oa_question_is_answered_from_the_oa_rule_not_the_planner(
        admin_client, monkeypatch):
    calls = _uncooperative(monkeypatch)
    r = await admin_client.post(CHAT, json={
        "message": "Can I claim travel expenses without a Travel Application?",
        "context": {"app": "portal", "route": "/"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"].startswith("No. A Travel Expense claim must be raised against "
                                     "a Travel Application that is approved")
    assert "not in EPMS" not in body["answer"] and "purchase request" not in body["answer"]
    assert calls == {"plan": 0, "narrate_guide": 0}
    assert body["sources"]["oa_rules"] == [
        "oa_travel_expense_needs_an_approved_travel_application"]


async def test_over_budget_and_usd_are_not_answered_from_pr_or_pa_rules(
        admin_client, monkeypatch):
    _uncooperative(monkeypatch, narration="No. A purchase request over budget …")
    r = await admin_client.post(CHAT, json={
        "message": "Can I submit a claim if it is over the budget?", "context": {"app": "oa"}})
    assert r.json()["answer"].startswith("Yes. On a General Expense claim")
    r = await admin_client.post(CHAT, json={
        "message": "Can I claim in USD?", "context": {"app": "oa"}})
    assert r.json()["answer"].startswith("A General Expense claim can be in CAD or USD")


async def test_an_oa_question_no_card_answers_is_guarded(admin_client, monkeypatch):
    calls = _uncooperative(monkeypatch)
    r = await admin_client.post(CHAT, json={
        "message": "Is there a dress code for business trips abroad?",
        "context": {"app": "oa"}})
    body = r.json()
    assert calls["narrate_guide"] == 1 and calls["plan"] == 0
    assert body["answer"].startswith("I have no OA rule that answers that exactly")
    assert "EPMS" not in body["answer"]


async def test_a_claim_number_is_said_to_be_unreadable_with_the_rule(admin_client, monkeypatch):
    _uncooperative(monkeypatch)
    r = await admin_client.post(CHAT, json={
        "message": "Who is approving EXP-20261016-0001 now?", "context": {"app": "oa"}})
    answer = r.json()["answer"]
    assert answer.startswith("I cannot read OA claims themselves, so I cannot say where "
                             "EXP-20261016-0001 stands")
    assert "Approval Status (right-hand column)" in answer


def _process(doc_type, steps):
    return {"doc_type": doc_type, "label": guide_view.PROCESS_LABELS[doc_type],
            "steps": [{"index": i + 1, "role": r, "label": label, "conditional": False,
                       "condition": None} for i, (r, label) in enumerate(steps)],
            "steps_available": True,
            # What the engine also serves, and must not reach an OA answer.
            "engine_rules": [{"id": "same_approver_on_consecutive_steps",
                              "answer": "a requester who is also the first approver "
                                        "must still approve after submitting"}]}


async def test_an_oa_chain_carries_the_claimant_skip_written_by_code(admin_client, monkeypatch):
    """05/11: asked about the Travel Expense / Travel Application chains, the
    answers said a claimant who is the first approver approves it themselves,
    that every step always runs, and that a TRA can be recalled."""
    calls = _uncooperative(monkeypatch, narration="Every step always runs; you can recall it.")

    async def steps(doc_type, token):
        return _process(doc_type, [("dept_manager", "Department Manager"),
                                   ("finance_bp", "Finance BP")])
    monkeypatch.setattr(guide_view, "process_steps", steps)

    r = await admin_client.post(CHAT, json={
        "message": "What is the approval process for a Travel Expense?",
        "context": {"app": "oa"}})
    answer = r.json()["answer"]
    assert answer.startswith("Travel Expense approval chain, as configured now: "
                             "1. Department Manager; 2. Finance BP.")
    assert "that step is skipped and the claim goes straight on to the next step" in answer
    assert "Auto-approved (same approver holds both roles)" in answer
    assert "OA has no Recall or Cancel button" in answer
    assert "must still approve" not in answer and "always runs;" not in answer
    assert calls == {"plan": 0, "narrate_guide": 0}


async def test_the_planners_oa_process_route_gets_the_same_answer(admin_client, monkeypatch):
    """When the planner is asked (an EPMS-worded question it routes to an OA
    chain) the OA chain answer is the same code-written one."""
    from app.api.v1 import assistant_chat

    _uncooperative(monkeypatch)

    async def steps(doc_type, token):
        return _process(doc_type, [("dept_manager", "Department Manager"),
                                   ("finance_manager", "Finance Manager"),
                                   ("gm", "General Manager")])
    monkeypatch.setattr(guide_view, "process_steps", steps)
    out = await assistant_chat._answer_process(
        None, {}, "t", SimpleNamespace(message="TRA flow?"), {"doc_type": "tra"}, None)
    assert out["answer"].startswith("Travel Application approval chain")
    assert "nobody approves their own claim or Travel Application" in out["answer"]
    assert "OA has no Recall" in out["answer"]


async def test_the_oa_rules_area_is_said_by_code(admin_client, monkeypatch):
    from app.api.v1 import assistant_chat

    calls = _uncooperative(monkeypatch)
    out = await assistant_chat._answer_rules(
        None, "t", SimpleNamespace(message="Can I delete a draft expense claim?"), ["oa"], None)
    assert out["answer"].startswith("Only a Travel Application can be deleted")
    assert calls["narrate_guide"] == 0


# ── Derived facts ────────────────────────────────────────────────────────────

class _Db:
    def __init__(self, value):
        self.value = value

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.value)


async def test_oa_policy_is_read_from_the_policy_row():
    facts = await rule_facts.oa_policy(_Db({
        "meal_breakfast_limit": "10", "meal_lunch_limit": 20, "meal_dinner_limit": "35.00",
        "meal_incidental_limit": "17.3", "mileage_rate_per_km": "0.5500",
        "max_km_per_claim": 5000}))
    assert facts == {"breakfast": "10.00", "lunch": "20.00", "dinner": "35.00",
                     "incidental": "17.30", "rate": "0.55", "max_km": "5,000"}
    with pytest.raises(LookupError):
        await rule_facts.oa_policy(_Db(None))
    # expense-api reads the same row and columns.
    src = _src("expense-api/app/models/policy.py")
    for col in ("meal_breakfast_limit", "meal_lunch_limit", "meal_dinner_limit",
                "meal_incidental_limit", "mileage_rate_per_km", "max_km_per_claim"):
        assert col in src
    assert '__tablename__ = "expense_policy_config"' in src


async def test_an_unreadable_policy_says_the_fallback_not_a_number():
    cards = await oa_guide.cards(_Db(None), ["oa_meal_limits"])
    assert "could not be read just now" in cards[0]["answer"]
    assert "{" not in cards[0]["answer"]


async def test_oa_task_emails_follow_the_policy():
    from app.services.task_notify_policy import DEFAULT_IMMEDIATE

    assert not any(t.startswith(("approve_exp", "approve_mil", "approve_trv", "approve_tra"))
                   for t in DEFAULT_IMMEDIATE)
    cfg = SimpleNamespace(notification_settings={"task_notifications": {
        "approve_exp": {"email": True, "mode": "digest"}}})
    facts = await rule_facts.oa_email_policy(_Db(cfg))
    assert facts["approve_exp"] == "a line in the daily digest email"
    assert facts["approve_tra"] == "no email"


def test_oa_reminder_uses_the_shared_cooldown():
    from app.services.manual_reminder import COOLDOWN_HOURS

    assert rule_facts.oa_reminder(None) == {"hours": COOLDOWN_HOURS}
    src = (EPMS_API / "app/api/v1/expense_reminders.py").read_text(encoding="utf-8")
    assert "remind_document" in src
    assert "/remind" in _src("expense-api/app/api/v1/expenses.py")


# ── Pins: the code that does what each card says ─────────────────────────────

def test_only_general_and_travel_expense_need_an_attachment():
    src = _src("expense-api/app/api/v1/expenses.py")
    assert 'claim.claim_type in ("EXP", "TRV") and not trv_all_period' in src
    page = _src("oa/src/pages/expenses/ExpenseDetailPage.tsx")
    assert "disabled={missingAttachment}" in page
    assert "At least one receipt attachment is required before this claim can be submitted." in page


def test_a_travel_expense_needs_an_approved_travel_application_listing_the_claimant():
    src = _src("expense-api/app/api/v1/expenses.py")
    body = src.split("async def _require_trv_application", 1)[1].split("\nasync def ", 1)[0]
    assert 'tra.status != "approved"' in body
    assert "claimant_id not in {t.user_id for t in tra.travelers}" in body
    assert src.count("_require_trv_application(") >= 3   # def, create, update


def test_the_travel_application_picker_offers_approved_ones_the_user_is_on():
    src = _src("expense-api/app/crud/expense.py")
    body = src.split("async def list_eligible_travel_apps", 1)[1]
    assert 'ExpenseClaim.status == "approved"' in body
    assert "ExpenseClaim.employee_id == user_id" in body and "traveler_claim_ids(user_id)" in body


def test_a_returned_claim_offers_edit_and_submit():
    page = _src("oa/src/pages/expenses/ExpenseDetailPage.tsx")
    assert page.count("isEditable(claim.status) && canEdit && (") >= 2
    status = _src("oa/src/lib/status.ts")
    assert "STATUS.RETURNED" in status.split("EDITABLE_STATUSES", 1)[1].split("\n", 1)[0]


def test_a_rejected_claim_cannot_be_edited_or_submitted():
    crud = _src("expense-api/app/crud/expense.py")
    assert "Only draft or returned claims can be edited" in crud
    engine = _src("approval-api/app/crud/engine.py")
    meta = engine.split("def _expense_meta", 1)[1].split("\n\n\n", 1)[0]
    assert '"valid_submit":  ("draft", "returned")' in meta


def test_only_an_unapproved_travel_application_can_be_deleted():
    src = _src("expense-api/app/api/v1/expenses.py")
    assert '_DELETABLE_STATUSES = ("draft", "returned", "submitted", "in_review")' in src
    assert 'if claim.claim_type != "TRA":\n        return False' in src


def test_oa_pages_offer_no_recall_or_cancel():
    """OA.md OI-08: the engine supports recall and cancel, OA offers neither.
    Only the retired Direct PA pages ever did."""
    pages = REPO / "oa" / "src"
    if not pages.exists():
        pytest.skip("oa is not mounted in this container")
    for path in pages.rglob("*.tsx"):
        if "_retired" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "ACTION.RECALL" not in text and "'recall'" not in text, path
        assert "ACTION.CANCEL" not in text and "action: 'cancel'" not in text, path


def test_meal_limits_flag_single_day_lines_and_refuse_date_range_lines():
    src = _src("expense-api/app/crud/expense.py")
    assert "claim.is_over_budget = await _trv_over_meal_limit" in src
    check = src.split("async def _check_trv_lines", 1)[1].split("\nasync def ", 1)[0]
    assert "if li.period_from is not None:" in check and "exceeds the" in check


def test_over_budget_steps_are_for_purchase_requests_only():
    engine = _src("approval-api/app/crud/engine.py")
    assert 'cfg_ob_mode = config_ob_mode(cfg) if doc_type == "pr" else None' in engine
    assert 'if doc_type == "pr" and cfg_ob_mode is not None:' in engine


def test_who_may_pay_a_claim():
    src = _src("expense-api/app/api/v1/expenses.py")
    assert ('_PAY_PRIMARY = {"payment_officer", "finance_manager", "finance_bp", '
            '"system_admin"}') in src
    assert '_PAY_ASSIGNED = _PAY_PRIMARY - {"system_admin"}' in src
    assert 'claim.status == "approved" and claim.claim_type != "TRA"' in src


def test_approval_raises_the_reimbursement_task_and_payment_marks_paid():
    engine = _src("approval-api/app/crud/engine.py")
    assert 'title=f"Process Reimbursement: {claim.claim_number}' in engine
    assert '"tra":    _post_approve_tra' in engine


def test_general_expense_currency_lines_and_totals():
    page = _src("oa/src/pages/expenses/ExpenseCreatePage.tsx")
    assert "<option>CAD</option>" in page and "<option>USD</option>" in page
    assert "lines.length < 20" in page
    crud = _src("expense-api/app/crud/expense.py")
    assert 'if currency != "CAD":' in crud
    assert "currency: 'CAD'" in _src("oa/src/pages/expenses/MilCreatePage.tsx")
    schema = _src("expense-api/app/schemas/expense.py")
    assert "Deliberately NOT a non-negative check" in schema
    assert "daily rate cannot be negative" in schema


def test_the_claimant_never_approves_and_their_only_step_is_skipped():
    engine = _src("approval-api/app/crud/engine.py")
    assert 'CLAIMANT_SKIP_REASON = "Auto-skipped (claimant holds this step' in engine
    assert "return CLAIMANT_SKIP_REASON if holders == {claimant} else None" in engine
    # At submit (submission_landing) and when a later step is reached.
    assert engine.count("_claimant_step_skip(db, doc_type,") >= 2
    assert "raise ValueError(MSG_CLAIMANT_HOLDS_EVERY_STEP)" in engine
    perms = _src("expense-api/app/api/v1/expenses.py")
    assert 'if claim.status in ("submitted", "in_review") and not is_owner:' in perms


def test_the_same_approver_is_auto_approved_on_the_next_step():
    engine = _src("approval-api/app/crud/engine.py")
    assert 'comment="Auto-approved (same approver holds both roles)"' in engine


def test_attachments_only_while_draft_or_returned_and_up_to_25_mb():
    src = _src("expense-api/app/api/v1/expense_attachments.py")
    assert 'if claim.status not in ("draft", "returned"):' in src
    assert "MAX_FILE_BYTES = 25 * 1024 * 1024" in src
    assert "content_type not in" not in src      # any type


def test_scan_accepts_images_and_pdf_up_to_25_mb():
    src = _src("expense-api/app/api/v1/ocr.py")
    assert "MAX_FILE_BYTES = 25 * 1024 * 1024" in src
    assert "Use JPEG, PNG, WebP, GIF or PDF." in src
    assert "onScanned" in _src("oa/src/components/ReceiptScanButton.tsx")


def test_approval_status_lists_each_step():
    src = _src("expense-api/app/api/v1/expenses.py")
    assert '@router.get("/{claim_id}/approval-status"' in src


def test_who_sees_a_claim_is_the_list_rule():
    src = _src("expense-api/app/api/v1/expenses.py")
    for clause in ("conditions = [EC.employee_id == user_id]",
                   'conditions.append((EC.claim_type == "TRV") & (EC.created_by == user_id))',
                   "conditions.append(EC.id.in_(acted_on_documents(user_id)))",
                   'conditions.append((EC.status == "approved") & (EC.claim_type != "TRA"))'):
        assert clause in src, clause


def test_oa_is_a_responsive_web_app():
    layout = _src("oa/src/components/layout/AppLayout.tsx")
    assert "mobileOpen" in layout and "onMobileMenuToggle" in layout
