"""Load and validate the ontology fragment this service owns.

The definitions live in app/ontology/epms.yaml rather than in Python so they are
data, not logic: portable to another execution engine later, readable by someone
who is not going to open a .py file, and diffable as a thing in its own right.
What stays in Python is everything YAML cannot safely hold — the ORM classes and
the row-scope callables — which this module resolves by name.

Resolution is strict on purpose. A field naming a column the model does not
have, a metric over an undeclared field, a link pointing at nothing: all of them
raise at import time. The alternative is a confusing 422 the first time someone
happens to ask a question that touches the mistake, which could be months later.
"""
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Callable

import uuid

import yaml
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest

ONTOLOGY_PATH = Path(__file__).resolve().parent.parent / "ontology" / "epms.yaml"

# Field kinds drive both output formatting and which operators the validator
# accepts (see controlled_query._OPS_BY_KIND).
TEXT = "text"
ENUM = "enum"
MONEY = "money"
DATE = "date"
DATETIME = "datetime"
BOOL = "bool"
INT = "int"

_KINDS = frozenset({TEXT, ENUM, MONEY, DATE, DATETIME, BOOL, INT})
_AGG_FNS = frozenset({"sum", "count", "avg", "min", "max"})
_CARDINALITIES = frozenset({"one_to_many", "many_to_one"})

# YAML names a model; only these are addressable. Adding an entity means adding
# its class here as well, which keeps "what the ontology can reach" explicit.
_MODELS: dict[str, type] = {
    "PurchaseRequest": PurchaseRequest,
    "PurchaseOrder": PurchaseOrder,
    "GoodsReceipt": GoodsReceipt,
    "Invoice": Invoice,
    "PaymentApplication": PaymentApplication,
}


# ── Row scope ─────────────────────────────────────────────────────────────────
# A None subquery means unrestricted (this user sees everything of this kind);
# that is build_scope's contract, not an absent value. Each helper mirrors one
# line that used to live inline in the matching crud.get_all().


# All five are async and take a session even though three of them need neither:
# invoice and pa genuinely have to await, and one uniform signature is worth more
# than saving an await on the simple ones.


async def scope_pr(q: Select, scope: dict, db: AsyncSession) -> Select:
    subq = scope.get("pr_subq")
    return q if subq is None else q.where(PurchaseRequest.id.in_(subq))


async def scope_po(q: Select, scope: dict, db: AsyncSession) -> Select:
    subq = scope.get("po_subq")
    return q if subq is None else q.where(PurchaseOrder.id.in_(subq))


async def scope_gr(q: Select, scope: dict, db: AsyncSession) -> Select:
    # Scoped through the PO, not by a GR subquery of its own.
    subq = scope.get("po_subq")
    return q if subq is None else q.where(GoodsReceipt.po_id.in_(subq))


async def invoice_scope_conditions(db: AsyncSession, po_ids_subq,
                                   own_uploads_user_id: uuid.UUID | None,
                                   task_user_id: uuid.UUID | None) -> list:
    """The OR-ed reasons a restricted caller may see an invoice.

    Five of them, and the breadth is the point: an invoice reaches people
    through more routes than the PO it hangs off. Shared with
    crud/invoice.get_all so the list endpoint and the assistant admit exactly
    the same rows.
    """
    from app.core.access_scope import _open_task_doc_ids
    from app.core.delegation import active_delegator_ids

    conds: list = []
    if po_ids_subq is not None:
        # Both routes: the invoice's own po_id, and any allocation line pointing
        # at a visible PO — one invoice can be split across several POs, and
        # matching po_id alone would hide it from the owner of the second one.
        conds.append(Invoice.po_id.in_(po_ids_subq))
        conds.append(Invoice.id.in_(select(InvoicePoAllocation.invoice_id).where(
            InvoicePoAllocation.po_id.in_(po_ids_subq))))
    if own_uploads_user_id is not None:
        conds.append(Invoice.uploaded_by == own_uploads_user_id)
    if task_user_id is not None:
        # Widen with anyone currently delegating approvals to this user: a
        # delegate who can approve a PA must be able to open its invoice.
        task_user_ids = {task_user_id} | await active_delegator_ids(db, task_user_id)
        conds.append(Invoice.id.in_(
            await _open_task_doc_ids(db, task_user_id, task_user_ids, "invoice")))
        # Matcher retention: invoices this user matched stay visible.
        conds.append(Invoice.matched_by == task_user_id)
    return conds


async def scope_invoice(q: Select, scope: dict, db: AsyncSession) -> Select:
    po_subq = scope.get("po_subq")
    restricted = bool(scope.get("restrict"))
    # These two derivations used to live in the list endpoint. They belong with
    # the conditions they feed, or the assistant and the list would each decide
    # for themselves who counts as an uploader.
    own_uploads = scope.get("user_id") if (restricted and scope.get("role") == "requester") else None
    task_uid = scope.get("user_id") if restricted else None
    if po_subq is None and own_uploads is None and task_uid is None:
        return q  # unrestricted
    conds = await invoice_scope_conditions(db, po_subq, own_uploads, task_uid)
    return q.where(or_(*conds)) if conds else q


def pa_scope_conditions(po_ids_subq, agr_ids_subq, created_by) -> list:
    """The OR-ed reasons a restricted caller may see a payment application.

    Each branch stands on its own rather than leaning on po/agr subqueries being
    set together, in case that coupling ever changes.
    """
    from app.crud.pa_links import pa_ids_for_pos

    conds = [
        # ANY of the PA's POs inside the caller's scope admits it — matching
        # po_id alone would hide a multi-PO PA from the owner of its second PO,
        # and they are paying for it.
        PaymentApplication.id.in_(pa_ids_for_pos(po_ids_subq))
        if po_ids_subq is not None else PaymentApplication.po_id.is_not(None),
        PaymentApplication.agreement_id.in_(agr_ids_subq)
        if agr_ids_subq is not None else PaymentApplication.agreement_id.is_not(None),
    ]
    if created_by:
        conds.append(PaymentApplication.created_by == created_by)
    return conds


async def scope_pa(q: Select, scope: dict, db: AsyncSession) -> Select:
    # Ownership filter first: OA's Direct PAs have both columns NULL and are not
    # EPMS's rows at all. This is separate from visibility and applies to
    # everyone, unrestricted callers included.
    q = q.where(or_(PaymentApplication.po_id.is_not(None),
                    PaymentApplication.agreement_id.is_not(None)))

    po_subq, agr_subq = scope.get("po_subq"), scope.get("agr_subq")
    restricted = bool(scope.get("restrict"))
    created_by = scope.get("user_id") if (restricted and scope.get("role") == "requester") else None
    if po_subq is None and agr_subq is None:
        return q.where(PaymentApplication.created_by == created_by) if created_by else q
    return q.where(or_(*pa_scope_conditions(po_subq, agr_subq, created_by)))


_SCOPES: dict[str, Callable] = {
    "pr": scope_pr,
    "po": scope_po,
    "gr": scope_gr,
    "invoice": scope_invoice,
    "pa": scope_pa,
}


# ── Shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Field:
    kind: str
    label: str
    values: tuple[str, ...] = ()
    # Ask the schema endpoint to list this column's actual values. For a field
    # with few distinct values — departments, cost centres — the names matter as
    # much as the column does: someone asking in Chinese about 工程部 means the
    # row recorded as "Engineering", and a planner that has only seen the field
    # name will filter on the words it was given and find nothing.
    enumerate_values: bool = False
    # What a coded value MEANS. A stored 4 is "Service"; without the mapping the
    # column is unanswerable in both directions — the planner cannot turn a
    # question about services into type=4, and nothing can report a 4 back as
    # anything but 4. Asked what procurement types exist, the assistant said the
    # system has none, because all it could see was an integer column.
    value_labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Metric:
    fn: str
    field: str  # a declared field name, or "*" for count
    label: str


@dataclass(frozen=True)
class Link:
    """A traversable relationship. Declared now, joined later.

    Nothing executes links yet — the query layer is still single-entity. They are
    declared and validated ahead of that so the shape of the chain is written
    down once, while the people who know it are still looking at this code.
    """
    name: str
    target: str
    cardinality: str
    local: str   # column on THIS entity's model
    remote: str  # column on the target's model
    label: str


@dataclass(frozen=True)
class Entity:
    name: str
    model: type
    label: str
    perm_key: str
    apply_scope: Callable  # async (Select, scope, AsyncSession) -> Select
    date_field: str
    fields: dict[str, Field] = dc_field(default_factory=dict)
    metrics: dict[str, Metric] = dc_field(default_factory=dict)
    links: dict[str, Link] = dc_field(default_factory=dict)


class OntologyError(Exception):
    """The ontology file disagrees with the code. Raised at import time."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise OntologyError(msg)


def _build_entity(name: str, spec: dict) -> Entity:
    where = f"entity '{name}'"

    model_name = spec.get("model")
    _require(model_name in _MODELS,
             f"{where}: unknown model {model_name!r}; known: {sorted(_MODELS)}")
    model = _MODELS[model_name]

    scope_name = spec.get("scope")
    _require(scope_name in _SCOPES,
             f"{where}: unknown scope {scope_name!r}; known: {sorted(_SCOPES)}")

    _require(bool(spec.get("perm_key")), f"{where}: perm_key is required")
    _require(bool(spec.get("label")), f"{where}: label is required")

    fields: dict[str, Field] = {}
    for fname, fspec in (spec.get("fields") or {}).items():
        kind = fspec.get("kind")
        _require(kind in _KINDS, f"{where}.{fname}: unknown kind {kind!r}")
        _require(bool(fspec.get("label")), f"{where}.{fname}: label is required")
        # The check that matters: a registered column the model does not have
        # would otherwise fail at query time, for whoever happened to ask first.
        _require(hasattr(model, fname),
                 f"{where}.{fname}: {model_name} has no such column")
        if kind == ENUM:
            _require(bool(fspec.get("values")),
                     f"{where}.{fname}: enum needs values")
        raw_labels = fspec.get("value_labels") or {}
        _require(isinstance(raw_labels, dict),
                 f"{where}.{fname}: value_labels must be a mapping")
        fields[fname] = Field(
            kind=kind, label=fspec["label"],
            values=tuple(fspec.get("values") or ()),
            enumerate_values=bool(fspec.get("enumerate")),
            value_labels=tuple((str(k), str(v)) for k, v in raw_labels.items()),
        )
    _require(bool(fields), f"{where}: needs at least one field")

    date_field = spec.get("date_field")
    _require(date_field in fields,
             f"{where}: date_field {date_field!r} is not a declared field")
    # The default time axis must be a column every row actually has. A nullable
    # one silently drops rows from every "last N months" question, and the
    # answer still looks plausible — PO shipped with date_field: placed_at,
    # which is NULL on 97% of rows because orders mirrored from NC were never
    # placed through EPMS. Nobody would have noticed from the replies.
    _col = getattr(model, date_field)
    _column_obj = getattr(getattr(_col, "property", None), "columns", [None])[0]
    if _column_obj is not None and _column_obj.nullable:
        # Not forbidden outright — sometimes the nullable column is genuinely the
        # better axis (a receipt date beats a row-creation date) and is populated
        # in practice. But it has to be acknowledged in writing, with the numbers
        # that justify it, rather than chosen by accident.
        _require(
            spec.get("date_field_nullable_ok") is True,
            f"{where}: date_field {date_field!r} is nullable, so rows without it "
            f"vanish from every period query. If it is populated in practice, set "
            f"date_field_nullable_ok: true and record the coverage next to it; "
            f"otherwise pick a column that is always set."
        )

    metrics: dict[str, Metric] = {}
    for mname, mspec in (spec.get("metrics") or {}).items():
        fn = mspec.get("fn")
        _require(fn in _AGG_FNS, f"{where}.metrics.{mname}: unknown fn {fn!r}")
        target = mspec.get("field")
        _require(target == "*" or target in fields,
                 f"{where}.metrics.{mname}: field {target!r} is not declared")
        metrics[mname] = Metric(fn=fn, field=target, label=mspec.get("label", mname))

    links: dict[str, Link] = {}
    for lname, lspec in (spec.get("links") or {}).items():
        card = lspec.get("cardinality")
        _require(card in _CARDINALITIES,
                 f"{where}.links.{lname}: unknown cardinality {card!r}")
        _require(hasattr(model, lspec.get("local", "")),
                 f"{where}.links.{lname}: {model_name} has no column "
                 f"{lspec.get('local')!r}")
        links[lname] = Link(name=lname, target=lspec["target"], cardinality=card,
                            local=lspec["local"], remote=lspec["remote"],
                            label=lspec.get("label", lname))

    return Entity(
        name=name, model=model, label=spec["label"], perm_key=spec["perm_key"],
        apply_scope=_SCOPES[scope_name], date_field=date_field,
        fields=fields, metrics=metrics, links=links,
    )


def load(path: Path = ONTOLOGY_PATH) -> dict[str, Entity]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _require(raw.get("version") == 1,
             f"ontology: unsupported version {raw.get('version')!r}")
    specs = raw.get("entities") or {}
    _require(bool(specs), "ontology: no entities declared")

    entities = {name: _build_entity(name, spec) for name, spec in specs.items()}

    # Cross-entity checks, once every entity exists.
    for entity in entities.values():
        for link in entity.links.values():
            _require(link.target in entities,
                     f"entity '{entity.name}'.links.{link.name}: unknown target "
                     f"{link.target!r}")
            target_model = entities[link.target].model
            _require(hasattr(target_model, link.remote),
                     f"entity '{entity.name}'.links.{link.name}: "
                     f"{target_model.__name__} has no column {link.remote!r}")
    return entities


REGISTRY: dict[str, Entity] = load()


def get_entity(name: str) -> Entity | None:
    return REGISTRY.get(name)
