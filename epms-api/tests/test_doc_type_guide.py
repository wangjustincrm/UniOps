"""The guide's claims have to be the system's behaviour.

The reason to derive the requirements instead of writing them down is that four
descriptions of the PR types already exist in this repository — PRD §2, PRD
§3.2.1, the training deck generator, and the code — and they disagree. These
tests are what stops the guide becoming the fifth.

Three things are checked:

  * the gate list the guide reports is the gate list the submit route enforces,
    for every type — not a copy of it, the same call;
  * the blank document the guide asks the gates about carries every attribute
    the gates read, so a new gate cannot silently vanish from the answer;
  * the prose covers every type the system accepts, and claims nothing about a
    type that does not exist.
"""
import pytest

from app.schemas.pr import PrCreate
from app.services import doc_type_guide
from app.services.doc_preflight import SUBMIT_CHECKS, field_checks_for

PR_TYPES = (1, 2, 3, 4, 5, 6)


def test_the_guide_covers_every_type_the_form_accepts():
    """PrCreate accepts 1..6; the prose must describe all six and no others."""
    described = {t["value"] for t in doc_type_guide.build("pr")["types"]}
    assert described == set(PR_TYPES)


@pytest.mark.parametrize("pr_type", PR_TYPES)
def test_reported_requirements_are_the_enforced_gates(pr_type):
    """Not "match" — are. Both sides call the same function.

    The assertion worth having is that the guide does not filter, reorder into
    something lossy, or drop a gate it has no label for.
    """
    reported = [r["id"] for r in doc_type_guide.requirements_for("pr", pr_type)]
    enforced = [c.id for c in field_checks_for("pr", doc_type_guide._BlankDoc("pr", pr_type))]
    assert reported == enforced


@pytest.mark.parametrize("pr_type", PR_TYPES)
def test_every_gate_is_put_into_words(pr_type):
    """A gate with no label falls back to its id, which would reach a requester
    as "fixed_asset_id_required". Catch it here instead."""
    for req in doc_type_guide.requirements_for("pr", pr_type):
        assert req["requirement"] != req["id"], (
            f"gate {req['id']} has no entry in _GATE_LABELS")


def test_the_blank_document_satisfies_every_gate_function():
    """The gates read attributes off the document; _BlankDoc has to carry them.

    A gate added later that reads a field _BlankDoc lacks would raise
    AttributeError here rather than in front of someone asking a question.
    """
    for doc_type in SUBMIT_CHECKS:
        if doc_type not in doc_type_guide.SUPPORTED:
            continue
        for pr_type in PR_TYPES:
            field_checks_for(doc_type, doc_type_guide._BlankDoc(doc_type, pr_type))


def test_type_1_is_the_only_one_without_a_budget_account():
    """The one difference every requester already half-knows, pinned.

    Pinned against the gates rather than against the YAML: if the budget rule
    is ever widened to type 1 or narrowed off another type, this fails and the
    prose gets revisited, which is the point.
    """
    def needs_budget(t: int) -> bool:
        return any(r["id"] == "budget_account_required"
                   for r in doc_type_guide.requirements_for("pr", t))

    assert not needs_budget(1)
    assert all(needs_budget(t) for t in PR_TYPES if t != 1)


def test_service_types_are_confirmed_by_the_requester():
    """Types 4 and 6 have nothing for the warehouse to receive."""
    receipts = {t["value"]: t["receipt"] for t in doc_type_guide.build("pr")["types"]}
    assert "requester" in receipts[4].lower()
    assert "requester" in receipts[6].lower()
    assert "warehouse" in receipts[2].lower()


def test_every_type_says_what_it_is_for():
    """Prose is the half no code supplies; an empty entry is a silent gap."""
    for entry in doc_type_guide.build("pr")["types"]:
        assert entry["label"], entry
        assert entry["covers"], f"type {entry['value']} has no 'covers'"
        assert entry["when_to_pick"], f"type {entry['value']} has no 'when'"


def test_unknown_doc_type_is_refused_not_guessed():
    with pytest.raises(LookupError):
        doc_type_guide.build("invoice")


def test_pr_create_still_accepts_exactly_these_types():
    """The guide's coverage is only complete while the form's range is 1..6."""
    field = PrCreate.model_fields["type"]
    bounds = {m.__class__.__name__: getattr(m, "ge", getattr(m, "le", None))
              for m in field.metadata}
    assert bounds.get("Ge") == min(PR_TYPES)
    assert bounds.get("Le") == max(PR_TYPES)
