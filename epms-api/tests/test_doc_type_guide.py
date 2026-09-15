"""The guide's claims have to be the system's behaviour, for every document.

The reason to derive instead of writing down is that four descriptions of the
PR types already existed here — PRD §2, PRD §3.2.1, the training deck, and the
code — and no two agreed. These tests are what stops the guide becoming a fifth,
and they have to hold as the guide grows past PR.

Two derivations are checked:

  required_to_create_any   the Pydantic model the create endpoint validates
                           against. Universal — every document kind has one.
  required_before_submit   the doc_preflight gates. Only PR has any, and the
                           tests below pin that asymmetry rather than papering
                           over it, because "no extra gates" and "no rules" are
                           different claims and the narrator is told which.
"""
import pytest

from app.schemas.pr import PrCreate
from app.services import doc_type_guide
from app.services.doc_preflight import SUBMIT_CHECKS, field_checks_for

PR_TYPES = (1, 2, 3, 4, 5, 6)
ALL_KINDS = doc_type_guide.SUPPORTED


def test_every_declared_kind_has_a_knowledge_file_that_loads():
    for kind in ALL_KINDS:
        built = doc_type_guide.build(kind)
        assert built["types"], f"{kind} declares no values"
        assert built["label"]
        assert built["what_it_is"], f"{kind} does not say what the document is"


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_value_says_what_it_is_for(kind):
    """Prose is the half no code supplies; an empty entry is a silent gap."""
    for entry in doc_type_guide.build(kind)["types"]:
        assert entry["label"], entry
        assert entry["covers"], f"{kind} {entry['value']} has no 'covers'"
        assert entry["when_to_pick"], f"{kind} {entry['value']} has no 'when'"


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_create_requirements_come_from_the_create_schema(kind):
    """Not "match" — are. Both sides read the same Pydantic model."""
    schema = doc_type_guide._TOPICS[kind].create_schema
    expected = [n for n, f in schema.model_fields.items() if f.is_required()]
    assert doc_type_guide.required_at_create(kind) == expected
    assert expected, f"{kind} create schema requires nothing at all — suspicious"


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_only_kinds_with_gates_report_gates(kind):
    """The asymmetry, asserted rather than assumed.

    PR is the only kind doc_preflight owns field gates for. If that changes —
    a gate added for PO, say — this fails, and whoever added it is told that
    the guide has a claim to update. That is the whole point of the rule.
    """
    reported = [r for t in doc_type_guide.build(kind)["types"]
                for r in t["required_before_submit"]]
    if doc_type_guide._TOPICS[kind].has_submit_gates:
        assert reported, f"{kind} claims submit gates but reports none"
        assert kind in SUBMIT_CHECKS
    else:
        assert reported == [], (
            f"{kind} reports submit gates but is not registered as having them")


# ── PR, where both derivations apply ─────────────────────────────────────────

@pytest.mark.parametrize("pr_type", PR_TYPES)
def test_pr_reported_gates_are_the_enforced_gates(pr_type):
    reported = [r["id"] for r in doc_type_guide.requirements_for("pr", pr_type)]
    enforced = [c.id for c in field_checks_for("pr", doc_type_guide._BlankDoc("pr", pr_type))]
    assert reported == enforced


@pytest.mark.parametrize("pr_type", PR_TYPES)
def test_every_gate_is_put_into_words(pr_type):
    """A gate with no label reaches a requester as "fixed_asset_id_required"."""
    for req in doc_type_guide.requirements_for("pr", pr_type):
        assert req["requirement"] != req["id"], (
            f"gate {req['id']} has no entry in _GATE_LABELS")


def test_the_blank_document_satisfies_every_gate_function():
    """A gate added later that reads a field _BlankDoc lacks raises here,
    rather than in front of someone asking a question."""
    for doc_type in SUBMIT_CHECKS:
        if doc_type not in ALL_KINDS:
            continue
        for pr_type in PR_TYPES:
            field_checks_for(doc_type, doc_type_guide._BlankDoc(doc_type, pr_type))


def test_type_1_is_the_only_one_without_a_budget_account():
    def needs_budget(t: int) -> bool:
        return any(r["id"] == "budget_account_required"
                   for r in doc_type_guide.requirements_for("pr", t))

    assert not needs_budget(1)
    assert all(needs_budget(t) for t in PR_TYPES if t != 1)


def test_pr_create_still_accepts_exactly_these_types():
    field = PrCreate.model_fields["type"]
    bounds = {m.__class__.__name__: getattr(m, "ge", getattr(m, "le", None))
              for m in field.metadata}
    assert bounds.get("Ge") == min(PR_TYPES)
    assert bounds.get("Le") == max(PR_TYPES)


# ── Receipt flow, which only procurement types drive ─────────────────────────

def test_only_procurement_documents_report_a_receipt_flow():
    """PR and PO carry a procurement type, so who receives follows from it. A
    PA type or an invoice status says nothing about receiving, and claiming one
    would be inventing a fact."""
    for kind in ALL_KINDS:
        has_receipt = any("receipt" in t for t in doc_type_guide.build(kind)["types"])
        assert has_receipt == (kind in ("pr", "po")), kind


@pytest.mark.parametrize("kind", ("pr", "po"))
def test_service_types_are_confirmed_by_the_requester(kind):
    receipts = {t["value"]: t["receipt"] for t in doc_type_guide.build(kind)["types"]}
    assert "requester" in receipts[4].lower()
    assert "requester" in receipts[6].lower()
    assert "warehouse" in receipts[2].lower()


def test_pr_and_po_describe_the_same_six_types():
    """A PO inherits its type from its PR. If the two lists ever diverge, one
    of them is describing types that cannot exist."""
    pr = {t["value"] for t in doc_type_guide.build("pr")["types"]}
    po = {t["value"] for t in doc_type_guide.build("po")["types"]}
    assert pr == po == set(PR_TYPES)


# ── Refusals ─────────────────────────────────────────────────────────────────

def test_unknown_doc_type_is_refused_not_guessed():
    for fn in (doc_type_guide.build, doc_type_guide.required_at_create):
        with pytest.raises(LookupError):
            fn("expense")
