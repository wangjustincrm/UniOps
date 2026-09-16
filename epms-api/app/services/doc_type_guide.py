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
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.schemas.agreement import AgreementCreate
from app.schemas.gr import GrCreate, is_service
from app.schemas.invoice import InvoiceCreate
from app.schemas.pa import PaCreate
from app.schemas.po import PoCreate
from app.schemas.pr import PrCreate
from app.services import report_lineage
from app.services.doc_preflight import SUBMIT_CHECKS, field_checks_for

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"


@dataclass(frozen=True)
class _Topic:
    """Where a document kind's answer comes from.

    `create_schema` is the Pydantic model the create endpoint validates against,
    and it is the second derivation in this module: what a document must carry
    to be created at all is `model_fields` with `is_required()`, read off the
    class the endpoint actually uses. It generalises where the submit gates do
    not — only PR has those — so every document kind here can still answer "what
    do I have to fill in" from enforcement rather than from prose.
    """
    file: str
    create_schema: type
    # True when doc_preflight owns submit gates for this kind. Today only PR
    # does; the others are validated by their create schema and their crud.
    has_submit_gates: bool = False


MODULES_FILE = "modules.yaml"

_TOPICS: dict[str, _Topic] = {
    "pr": _Topic("pr_types.yaml", PrCreate, has_submit_gates=True),
    "po": _Topic("po_types.yaml", PoCreate),
    "gr": _Topic("gr_types.yaml", GrCreate),
    "pa": _Topic("pa_types.yaml", PaCreate),
    "invoice": _Topic("invoice_types.yaml", InvoiceCreate),
    "agreement": _Topic("agreement_types.yaml", AgreementCreate),
}
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
    topic = _TOPICS.get(doc_type)
    if topic is None:
        raise LookupError(doc_type)
    with (_KNOWLEDGE / topic.file).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def required_at_create(doc_type: str) -> list[str]:
    """The fields the create endpoint will not accept the document without.

    Straight off the Pydantic model the endpoint validates against — a field
    made optional, or a new one made required, changes this answer without
    anyone remembering to. Type-independent: it is one schema per document kind.
    """
    topic = _TOPICS.get(doc_type)
    if topic is None:
        raise LookupError(doc_type)
    return [name for name, f in topic.create_schema.model_fields.items()
            if f.is_required()]


def requirements_for(doc_type: str, value) -> list[dict]:
    """What a document of this type must have before it can be SUBMITTED.

    Read off the gates rather than written down, so this cannot describe a rule
    that is not enforced — nor miss one that is.

    Empty for every kind but PR, and that emptiness is honest rather than a gap:
    doc_preflight owns field gates for PR alone. The others are held to their
    create schema and their crud, which is what required_at_create reports. The
    narrator is told which of the two it is looking at so it cannot present "no
    submit gates" as "no requirements".
    """
    topic = _TOPICS.get(doc_type)
    if topic is None:
        raise LookupError(doc_type)
    if not topic.has_submit_gates or doc_type not in SUBMIT_CHECKS:
        return []
    return [
        {"id": c.id, "requirement": _GATE_LABELS.get(c.id, c.id)}
        for c in field_checks_for(doc_type, _BlankDoc(doc_type, value))
    ]


def _receipt(doc_type: str, value) -> str | None:
    """Who confirms receipt — only meaningful where a procurement type drives it."""
    if doc_type not in ("pr", "po") or not isinstance(value, int):
        return None
    return ("The requester confirms the work is complete" if is_service(value)
            else "The warehouse receives the goods")


def build(doc_type: str) -> dict:
    """The types of this document, each with its prose and its real rules."""
    doc = _load(doc_type)
    out = []
    for entry in doc.get("values", []):
        value = entry["value"]
        item = {
            "value": value,
            "label": entry.get("label"),
            "covers": (entry.get("covers") or "").strip(),
            "when_to_pick": (entry.get("when") or "").strip(),
            "notes": entry.get("notes") or [],
            # Derived.
            "required_before_submit": requirements_for(doc_type, value),
        }
        receipt = _receipt(doc_type, value)
        if receipt:
            item["receipt"] = receipt
        out.append(item)
    return {
        "doc_type": doc_type,
        "label": doc.get("label", doc_type.upper()),
        "what_it_is": (doc.get("what_it_is") or "").strip(),
        "types": out,
        # Derived: one schema for the whole document kind, so it sits beside the
        # per-type list rather than inside it.
        "required_to_create_any": required_at_create(doc_type),
        # Said out loud so the narrator can pass it on rather than guessing at
        # the scope of what it was handed.
        "requirements_are": (
            "required_to_create_any is what the create form will not submit "
            "without, for every type. required_before_submit is the extra "
            "field gates checked when the document leaves draft, and is empty "
            "for document kinds that have none — which means no EXTRA gates, "
            "not no requirements."
        ),
        "how_types_are_set": (doc.get("how_types_are_set") or "").strip() or None,
    }


# ── Modules ──────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _load_modules() -> dict:
    with (_KNOWLEDGE / MODULES_FILE).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def modules() -> dict:
    """What each part of UniOps is for, and which of them go deeper.

    The layer above the document kinds. Prose, unavoidably: what a module is
    FOR is not something any code states, and the modules that are not EPMS run
    in their own services, which this one cannot import.

    The one thing here that CAN be checked is checked. `can_explain_further`
    is filtered against the kinds this module actually has knowledge for, and
    `can_explain_reports` against the reports whose workings the guide holds, so
    the assistant never offers to go deeper on something it would then have to
    refuse. test_module_guide.py fails if a module names a kind or a report that
    does not exist, or if a kind exists that no module claims.
    """
    doc = _load_modules()
    out = []
    for entry in doc.get("modules", []):
        declared = entry.get("documents") or []
        out.append({
            "key": entry["key"],
            "label": entry.get("label"),
            "covers": (entry.get("covers") or "").strip(),
            "who_uses_it": (entry.get("who") or "").strip(),
            "typical_things_you_do": entry.get("typical") or [],
            "notes": entry.get("notes") or [],
            # Derived: only kinds the guide can really answer on.
            "can_explain_further": [d for d in declared if d in _TOPICS],
            # Same rule for reports: a module may only offer to explain the
            # workings of a report the guide actually holds the workings of.
            "can_explain_reports": [r for r in (entry.get("reports") or [])
                                    if r in report_lineage.SUPPORTED],
        })
    return {
        "label": doc.get("label", "UniOps modules"),
        "modules": out,
        "deeper_available_for": list(_TOPICS),
        "what_this_is": (
            "An overview of what each module is for. For any kind listed in "
            "can_explain_further, ask again about that document and you get "
            "its types and what has to be filled in."
        ),
    }
