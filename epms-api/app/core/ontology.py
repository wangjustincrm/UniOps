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

import yaml
from sqlalchemy.sql import Select

from app.models.gr import GoodsReceipt
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
}


# ── Row scope ─────────────────────────────────────────────────────────────────
# A None subquery means unrestricted (this user sees everything of this kind);
# that is build_scope's contract, not an absent value. Each helper mirrors one
# line that used to live inline in the matching crud.get_all().


def scope_pr(q: Select, scope: dict) -> Select:
    subq = scope.get("pr_subq")
    return q if subq is None else q.where(PurchaseRequest.id.in_(subq))


def scope_po(q: Select, scope: dict) -> Select:
    subq = scope.get("po_subq")
    return q if subq is None else q.where(PurchaseOrder.id.in_(subq))


def scope_gr(q: Select, scope: dict) -> Select:
    # Scoped through the PO, not by a GR subquery of its own.
    subq = scope.get("po_subq")
    return q if subq is None else q.where(GoodsReceipt.po_id.in_(subq))


_SCOPES: dict[str, Callable[[Select, dict], Select]] = {
    "pr": scope_pr,
    "po": scope_po,
    "gr": scope_gr,
}


# ── Shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Field:
    kind: str
    label: str
    values: tuple[str, ...] = ()


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
    apply_scope: Callable[[Select, dict], Select]
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
        fields[fname] = Field(kind=kind, label=fspec["label"],
                              values=tuple(fspec.get("values") or ()))
    _require(bool(fields), f"{where}: needs at least one field")

    date_field = spec.get("date_field")
    _require(date_field in fields,
             f"{where}: date_field {date_field!r} is not a declared field")

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
