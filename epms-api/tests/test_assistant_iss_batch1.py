"""Assistant fixes from the 2026-10-20 issue review, batch 1.

Each rule the guide states is pinned to the code that enforces it, so the guide
fails here rather than drifting when the behaviour changes.
"""
import inspect

from app.api.v1 import pa as pa_api
from app.crud import agreement as agreement_crud
from app.models.agreement import PurchaseAgreement
from app.services import doc_type_guide, reply_language


def _rules(doc_type):
    return {r["id"]: r["answer"] for r in doc_type_guide.build(doc_type)["rules"]}


def _type(doc_type, value):
    return next(t for t in doc_type_guide.build(doc_type)["types"]
                if t["value"] == value)


# ── ISS-0023: agreements ─────────────────────────────────────────────────────

def test_house_account_is_not_described_as_an_open_po():
    """The guide said purchases are "charged against an open purchase order";
    the agreement route raises no PO at all."""
    notes = " ".join(_type("agreement", "house_account")["notes"]).lower()
    assert "open purchase order" not in notes
    assert "no pr or po is raised" in notes
    assert "no PO, no GR" in inspect.getsource(pa_api)


def test_grace_window_rule_matches_the_admission_predicate():
    answer = _rules("agreement")["grace_window"]
    default = PurchaseAgreement.__table__.c.grace_days.server_default.arg
    assert default == "30" and "30" in answer
    predicate = inspect.getsource(agreement_crud._admissible_predicate)
    # "Valid From is not checked" — the predicate must not read it.
    assert "valid_from)" not in predicate.split('"""')[-1]
    assert "Valid From is not checked" in answer


def test_nte_rule_matches_the_payment_route():
    """Not to Exceed blocks nothing: the PA route never reads it."""
    assert "warning only" in _rules("agreement")["nte_is_warning_only"]
    assert "not_to_exceed" not in inspect.getsource(pa_api)


def test_house_account_has_a_fixed_chinese_name():
    """It was translated as 房间账户 (a hotel room account)."""
    assert reply_language.TERMS_ZH["House Account"] == "挂账账户"


# ── ISS-0025: over-budget ────────────────────────────────────────────────────

async def test_process_payload_carries_justification_and_over_budget_rules(monkeypatch):
    from app.services import guide_view

    async def fake_forward(method, path, token, **kw):
        return 200, {"doc_type": "pr", "steps": [],
                     "over_budget": {"mode": "allow",
                                     "mode_label": "Allow without extra approval",
                                     "added_steps": [], "blocks_submission": False,
                                     "justification_required": True, "note": "n"}}

    monkeypatch.setattr(guide_view.approval_client, "forward", fake_forward)
    got = await guide_view.process_steps("pr", "t")
    assert got["over_budget"]["justification_required"] is True
    ids = {r["id"] for r in got["rules"]}
    assert {"budget_check_applies_to_types_2_to_6", "over_budget_checked_on_save",
            "budget_not_reserved"} <= ids
    # The same rules the document-type route carries, not a copy.
    assert got["rules"] == [r for r in doc_type_guide.build("pr")["rules"]
                            if r.get("topic") == "over_budget"]
    # Other processes carry no PR rules.
    po = await guide_view.process_steps("po", "t")
    assert "rules" not in po


def test_narrator_is_told_to_give_the_whole_frozen_mode_note():
    """The narrator was told to say only "fixed when the PR is submitted",
    dropping that a looser mode skips steps a PR has not yet passed."""
    from app.services import assistant_llm

    guide = assistant_llm._GUIDE_SYSTEM
    assert "justification_required" in guide
    assert "looser one skips over-budget steps" in guide
    assert "is fixed when the PR is submitted" not in guide


def test_over_budget_is_decided_on_save_not_on_submit():
    """over_budget_checked_on_save says submit does not re-check. Pin it: the
    only callers of the computation are create and update."""
    from app.crud import pr as pr_crud

    src = inspect.getsource(pr_crud)
    assert src.count("await _compute_over_budget(") == 2
    assert "_compute_over_budget" in inspect.getsource(pr_crud.create)
    assert "_compute_over_budget" in inspect.getsource(pr_crud.update)


def test_budget_unreachable_is_treated_as_within_budget():
    from app.crud import pr as pr_crud

    assert "return False, None" in inspect.getsource(pr_crud.compute_budget_check)
    assert "treated as within budget" in _rules("pr")["budget_not_reserved"]


# ── ISS-0065: the retired Direct PA is not a process to describe ─────────────

def test_direct_pa_is_not_offered_as_a_process():
    from app.services import assistant_llm, guide_view

    assert "pa_dir" not in guide_view.SUPPORTED_PROCESSES
    enum = assistant_llm._PROCESS_TOOL["input_schema"]["properties"]["doc_type"]["enum"]
    assert "pa_dir" not in enum
    assert "discontinued" in _rules("pa")["direct_pa_retired"]
