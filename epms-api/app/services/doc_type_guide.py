"""What kinds of a document exist, and what differs when you create one.

The fifth question in a family the assistant kept getting wrong: "what PR types
are there and what changes between them" is neither a data question nor a
question about the approval chain, so the router sent it to the nearest thing it
had — explain_process — which answered with who signs off. Twice.

The answer is assembled the way a person would write a training page: what the
type is for, and what the system will actually make you do. Those two halves
come from different places on purpose.

  prose   — app/knowledge/pr_types.yaml. What a type covers and when to pick
            it. Nothing in the code states this, and nothing can derive it.

  fields  — services/doc_preflight.field_checks_for, called with a blank
            document of that type. Not a description of the gates: the gates
            themselves, the same function the submit route runs. A gate added,
            removed or re-scoped changes this answer in the same commit.

  receipt — schemas/gr.is_service, the set every "is this a service?" branch in
            the codebase already reads.

Deriving rather than describing is the whole point. There are four descriptions
of the PR types in this repository — PRD §2, PRD §3.2.1, the training deck
generator, and the code — and they disagree with each other. A fifth written by
hand would have joined them; one computed from the gates cannot.
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

from app.schemas.gr import is_service
from app.services.doc_preflight import field_checks_for

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"

# doc_type -> knowledge file. One entry today; the shape is the point.
_TOPICS = {"pr": "pr_types.yaml"}
SUPPORTED = tuple(_TOPICS)

# A gate id says what it checks; this says it in words a requester would use,
# and names where the field is. Gates without an entry still appear — falling
# back to the id is ugly and honest, and beats dropping a requirement silently.
_GATE_LABELS = {
    "vendor_required": "A vendor",
    "budget_account_required": "A cost center and budget account",
    "service_completion_date_required": "An expected completion date",
    "fixed_asset_id_required": "A Fixed Asset ID",
    "project_code_required": "A Project No.",
}


class _BlankDoc:
    """An empty document of a given type, for asking the gates what applies.

    Every gated field is None, so each gate reports itself as failing — which is
    exactly the question being asked. "What must a type 5 PR have?" is the same
    question as "what does a type 5 PR with nothing filled in fail on?".

    The attribute list has to cover every field the gate functions read. A
    missing one raises AttributeError rather than quietly dropping a gate, and
    test_doc_type_guide.py holds the two in step.
    """

    def __init__(self, doc_type: str, value: int):
        self.id = "00000000-0000-0000-0000-000000000000"
        self.type = value
        self.vendor_id = None
        self.budget_code = None
        self.cost_center_id = None
        self.service_completion_date = None
        self.project_code = None
        self.fixed_asset_id = None


@functools.lru_cache(maxsize=None)
def _load(doc_type: str) -> dict:
    name = _TOPICS.get(doc_type)
    if name is None:
        raise LookupError(doc_type)
    with (_KNOWLEDGE / name).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def requirements_for(doc_type: str, value: int) -> list[dict]:
    """What a document of this type must have before it can be submitted.

    Read off the gates rather than written down, so this cannot describe a rule
    that is not enforced — nor miss one that is.
    """
    return [
        {"id": c.id, "requirement": _GATE_LABELS.get(c.id, c.id)}
        for c in field_checks_for(doc_type, _BlankDoc(doc_type, value))
    ]


def build(doc_type: str) -> dict:
    """The types of this document, each with its prose and its real rules."""
    doc = _load(doc_type)
    out = []
    for entry in doc.get("values", []):
        value = entry["value"]
        requirements = requirements_for(doc_type, value)
        out.append({
            "value": value,
            "label": entry.get("label"),
            "covers": (entry.get("covers") or "").strip(),
            "when_to_pick": (entry.get("when") or "").strip(),
            "notes": entry.get("notes") or [],
            # Derived, both of them.
            "required_before_submit": requirements,
            "receipt": ("The requester confirms the work is complete"
                        if is_service(value)
                        else "The warehouse receives the goods"),
        })
    return {
        "doc_type": doc_type,
        "label": doc.get("label", doc_type.upper()),
        "types": out,
        # Said out loud so the narrator can pass it on: these are the gates that
        # stop a submit, not every difference between the types. Line-item
        # behaviour lives in the frontend and is described in the prose.
        "requirements_are": "the field gates enforced when the document is submitted",
    }
