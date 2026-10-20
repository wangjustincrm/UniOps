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
