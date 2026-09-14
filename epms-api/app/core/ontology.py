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

import sqlalchemy as sa
import yaml
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import column_property, declarative_base
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.models.user import User
from app.models.department import Department
from app.models.cost_center import CostCenter
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest

_ONTOLOGY_DIR = Path(__file__).resolve().parent.parent / "ontology"
ONTOLOGY_PATH = _ONTOLOGY_DIR / "epms.yaml"

# One file per owning service rather than one big file. The ontology is meant to
# grow past this codebase — the same registry will eventually carry WMS and MES —
# and a fragment per service keeps each one reviewable by the people who know
# that data, with its own traps documented next to its own tables.
ONTOLOGY_FILES = (
    ONTOLOGY_PATH,
    _ONTOLOGY_DIR / "finance.yaml",
    _ONTOLOGY_DIR / "mrp.yaml",
    _ONTOLOGY_DIR / "mdm.yaml",
    _ONTOLOGY_DIR / "settings.yaml",
    _ONTOLOGY_DIR / "budget.yaml",
)

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

# Tables owned by other services are mapped here, on their own metadata — see
# _model_for_table. Never the application Base.
_ExternalBase = declarative_base()
_AGG_FNS = frozenset({"sum", "count", "avg", "min", "max"})
_CARDINALITIES = frozenset({"one_to_many", "many_to_one"})

# YAML names a model; only these are addressable. Adding an entity means adding
# its class here as well, which keeps "what the ontology can reach" explicit.
#
# An entity may instead name a `table`. Finance and MRP keep their tables in this
# same database but their ORM classes live in other services' code, and copying
# 76 model definitions over here would be a second description of those tables
# that starts drifting the moment either side changes — a mistake this codebase
# has made three times already. For those, the YAML field list IS the model: the
# columns declared there are built into a read-only mapped class below, and
# test_ontology_matches_the_database checks every one against information_schema
# so a drift fails a test instead of a user's question.
_MODELS: dict[str, type] = {
    "PurchaseRequest": PurchaseRequest,
    "PurchaseOrder": PurchaseOrder,
    "GoodsReceipt": GoodsReceipt,
    "Invoice": Invoice,
    "PaymentApplication": PaymentApplication,
    "User": User,
    "Department": Department,
    "CostCenter": CostCenter,
}


_SA_TYPES = {
    TEXT: sa.String, ENUM: sa.String, MONEY: sa.Numeric, DATE: sa.Date,
    DATETIME: sa.DateTime(timezone=True), BOOL: sa.Boolean, INT: sa.Integer,
    # Only reachable from an `internal: true` field — deliberately absent from
    # _KINDS, so a planner-visible column can never be one. A join key mapped as
    # text would compare a str against a Postgres uuid and fail in the database
    # rather than here.
    "uuid": PGUUID(as_uuid=True),
}

# Built classes are cached: load() runs once at import, but the tests call it
# again with other files, and re-declaring a table on the same metadata raises.
_TABLE_MODELS: dict[str, type] = {}


def _model_for_table(table: str, fields: dict, where: str) -> type:
    """Map a table this service does not own, from the ontology's own field list.

    Deliberately on a metadata of its own rather than the application Base: these
    tables belong to finance-api and mrp-api, who own their migrations. Putting
    them on the shared Base would put them in create_all, and this service would
    start creating another service's tables in any fresh database.
    """
    if table in _TABLE_MODELS:
        return _TABLE_MODELS[table]

    cols: dict = {
        "__tablename__": table,
        # Every table reached this way is keyed on id. SQLAlchemy needs a primary
        # key to map at all, and the row scopes all filter on it.
        "id": sa.Column(PGUUID(as_uuid=True), primary_key=True),
    }
    for fname, fspec in fields.items():
        if fname == "id":
            continue
        kind = fspec.get("kind")
        _require(kind in _SA_TYPES, f"{where}.{fname}: unknown kind {kind!r}")
        expr = fspec.get("expr")
        if expr:
            # A column the owning service should have but does not. The BOM
            # default flag is the case this exists for: it lives on the NC
            # mirror and the sync never carried it into `boms`, so without this
            # there is no way to ask which version is the real one — and no way
            # to ask about the others either, which is worse.
            #
            # The SQL comes from the ontology file, never from a request. A
            # request cannot reach this: the planner chooses field NAMES from a
            # fixed list and never writes SQL.
            cols[fname] = column_property(
                sa.literal_column(f"({expr})", type_=_SA_TYPES[kind]))
        else:
            cols[fname] = sa.Column(_SA_TYPES[kind], nullable=True)

    model = type(f"Ext_{table}", (_ExternalBase,), cols)
    _TABLE_MODELS[table] = model
    return model


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


# ── Scopes for tables this service reads but does not own ─────────────────────


async def scope_permission_only(q: Select, scope: dict, db: AsyncSession) -> Select:
    """No row filter: perm_key is the whole access decision for this entity.

    Used by the finance and MRP entities. Their rows have no per-person
    dimension to filter on — a voucher is not "yours" the way a requisition is —
    so the question is whether someone may see the books, or the plan, at all.

    Named for what it means rather than "unrestricted", because the thing to
    notice when reading it is that widening the permission widens the data with
    nothing else in the way.
    """
    return q


# The three below are not access control. They are correctness: these tables
# accumulate one set of rows per planning run, and a query that does not pick a
# run sums every run that ever happened. Two released MPS runs hold 96 and 92
# lines; asked for planned quantity, the honest answer comes from one of them
# and the arithmetic mean of that mistake is a near-exact doubling — the kind of
# wrong number that looks entirely reasonable.
#
# They live in the scope slot deliberately: a scope is stapled into the WHERE
# and cannot be switched off by a planner that did not think to filter.


def _latest(table: str, where: str = "", order: str = "created_at DESC"):
    return sa.text(f"SELECT id FROM {table} {where} ORDER BY {order} LIMIT 1")


async def scope_mps_in_force(q: Select, scope: dict, db: AsyncSession) -> Select:
    """Only the plan in force.

    is_default is mrp-api's own marker for THE plan currently in force (see its
    mps.py and purchase.py, which select on exactly this); following it keeps the
    assistant's answer and the MRP screens describing the same plan.
    """
    model = _TABLE_MODELS["mrp_mps_lines"]
    return q.where(model.run_id.in_(
        _latest("mrp_mps_runs", "WHERE is_default IS TRUE")))


async def scope_forecast_current(q: Select, scope: dict, db: AsyncSession) -> Select:
    """Only the newest confirmed forecast version.

    Four versions exist and they are not increments of each other — the newest
    holds 100 lines where an older one holds 1. There is no is_default here, so
    newest-confirmed is the closest thing to "the forecast" this table offers.
    """
    model = _TABLE_MODELS["mrp_forecast_lines"]
    return q.where(model.version_id.in_(
        _latest("mrp_forecast_versions", "WHERE status = 'confirmed'")))


async def _own_department_cost_centres(scope: dict):
    """Cost centres of the asker's own department, as a subquery.

    Their department comes off the users row rather than the token: a token is
    minted at sign-in and a transfer since then would leave it stale, and this
    decides what someone may see. 20 of 112 users have no department at all —
    for them this is empty, which correctly shows them nothing rather than
    everything.
    """
    dept = (sa.select(User.department_id)
            .where(User.id == scope.get("user_id")).scalar_subquery())
    return sa.select(CostCenter.id).where(CostCenter.department_id == dept)


async def scope_budget_by_cost_centre(q: Select, scope: dict, db: AsyncSession) -> Select:
    """Whole company, or just your own department's cost centres.

    The matrix already draws this line and nothing was reading it:
    finance.budget.view_all goes to finance and the GM; finance.budget.view_dept
    goes to nearly everyone else — requesters, department managers, procurement.
    A department manager may see their own department's budget and not the
    company's, which is the distinction those two keys exist to make.
    """
    if (scope.get("perms") or {}).get("finance.budget.view_all"):
        return q
    model = _TABLE_MODELS["budget_plans"]
    return q.where(model.cost_center_id.in_(await _own_department_cost_centres(scope)))


async def scope_budget_ledger_by_cost_centre(
        q: Select, scope: dict, db: AsyncSession) -> Select:
    """Same rule, on the ledger's own cost-centre column."""
    if (scope.get("perms") or {}).get("finance.budget.view_all"):
        return q
    model = _TABLE_MODELS["budget_ledger"]
    return q.where(model.cost_center_id.in_(await _own_department_cost_centres(scope)))


async def scope_budget_line_by_plan(q: Select, scope: dict, db: AsyncSession) -> Select:
    """Budget lines carry no cost centre; they inherit their plan's.

    Reached through plan_id rather than by joining, so the restriction holds
    whatever else the query does with its joins.
    """
    if (scope.get("perms") or {}).get("finance.budget.view_all"):
        return q
    model = _TABLE_MODELS["budget_plan_lines"]
    plans = _TABLE_MODELS["budget_plans"]
    visible = sa.select(plans.id).where(
        plans.cost_center_id.in_(await _own_department_cost_centres(scope)))
    return q.where(model.plan_id.in_(visible))


async def scope_bom_default(q: Select, scope: dict, db: AsyncSession) -> Select:
    """Only the default BOM for each product.

    A product carries several BOMs — six for CF0063, versions 1.0 to 1.5, five of
    them approved — and exactly one is marked default in NC. They are not
    variants of a common quantity: v1.2 is built in batches of 1000 and v1.4 in
    batches of 660, so adding their lines together produces a number with no
    meaning at all.

    The marker lives on the NC mirror as hbdefault, not on `boms` — the sync
    never carried the column across — so this reaches back through nc_source_pk,
    which is populated on all 286 rows. If `boms` ever gains its own flag, this
    is the one place that has to change.

    Not universally unique: 159 of 164 (product, bom_type) pairs have exactly one
    default, five have more. Those return more than one BOM rather than an
    arbitrary pick, because choosing silently is how the wrong recipe gets
    reported as the recipe.
    """
    model = _TABLE_MODELS["boms"]
    return q.where(model.nc_source_pk.in_(
        sa.text("SELECT nc_source_pk FROM nc_bom WHERE hbdefault = 'Y'")))


async def scope_bom_line_default(q: Select, scope: dict, db: AsyncSession) -> Select:
    """Lines of the default BOMs only — same reasoning, one level down.

    Without this, "what goes into CF0063" answers with all six versions' lines
    interleaved: the same component appearing repeatedly at quantities that
    belong to different batch sizes.
    """
    model = _TABLE_MODELS["bom_lines"]
    return q.where(model.bom_id.in_(sa.text(
        "SELECT b.id FROM boms b JOIN nc_bom n ON n.nc_source_pk = b.nc_source_pk "
        "WHERE n.hbdefault = 'Y'")))


async def scope_purchase_latest_run(q: Select, scope: dict, db: AsyncSession) -> Select:
    """Only the newest purchase-suggestion run.

    This table has neither a status nor an is_default, so newest is all there is
    to go on. A run does carry source_plan_run_id back to the MPS run it came
    from; if suggestions ever need to track the plan in force rather than the
    latest run, that is the column to follow.
    """
    model = _TABLE_MODELS["mrp_purchase_lines"]
    return q.where(model.run_id.in_(_latest("mrp_purchase_runs")))


_SCOPES: dict[str, Callable] = {
    "pr": scope_pr,
    "po": scope_po,
    "gr": scope_gr,
    "invoice": scope_invoice,
    "pa": scope_pa,
    "permission_only": scope_permission_only,
    "mps_in_force": scope_mps_in_force,
    "forecast_current": scope_forecast_current,
    "purchase_latest_run": scope_purchase_latest_run,
    "bom_default": scope_bom_default,
    "bom_line_default": scope_bom_line_default,
    "budget_by_cost_centre": scope_budget_by_cost_centre,
    "budget_ledger_by_cost_centre": scope_budget_ledger_by_cost_centre,
    "budget_line_by_plan": scope_budget_line_by_plan,
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
    # Computed here rather than stored by the owning service — so it has no row
    # in information_schema and the drift checks must skip it.
    is_expression: bool = False
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
    # One key, or several of which ANY grants access. Budget is the reason for
    # the plural: the matrix splits it into finance.budget.view_all and
    # finance.budget.view_dept, held by disjoint sets of roles — finance holds
    # the first, everyone else the second, and system_admin neither. A single
    # key would have locked out whichever half it did not name, which is how
    # the budget entities first shipped invisible to the admin asking about them.
    perm_key: tuple[str, ...]
    apply_scope: Callable  # async (Select, scope, AsyncSession) -> Select
    date_field: str
    fields: dict[str, Field] = dc_field(default_factory=dict)
    metrics: dict[str, Metric] = dc_field(default_factory=dict)
    links: dict[str, Link] = dc_field(default_factory=dict)
    # The yaml acknowledged that date_field can be NULL. Kept on the entity so
    # the check against real data can tell "we looked and accepted this" from
    # "nobody has looked yet".
    nullable_axis_ok: bool = False
    # A condition applied only when the caller said nothing about this field.
    #
    # Different from a scope, and the difference is the whole point. A scope is a
    # boundary and cannot be opted out of. This is a safe DEFAULT: without it,
    # "what is CF0063 made of" silently merges six versions of the recipe; with
    # it as a scope, "how many versions does CS0026 have" answers 1 when the
    # truth is 7. Neither is acceptable, so the rule is: assume the default
    # version unless the question is about versions, in which case get out of
    # the way.
    default_filter: tuple[str, object] | None = None
    default_filter_note: str = ""
    # Fields whose mention means the question is ABOUT this dimension, so the
    # default must not be applied. Declared rather than inferred: "version" is
    # such a field and shares no prefix with "is_default".
    default_filter_stand_down: tuple[str, ...] = ()


# Filled during _build_entity, drained by load() once every entity exists: a
# date_field that reaches through a link cannot be checked until its target has
# been built.
_deferred_axis_checks: list[tuple[str, str, str, str]] = []


def may_view(entity: "Entity", perms: dict) -> bool:
    """Does this caller hold any of the entity's keys?

    ANY rather than ALL: the keys on an entity are alternative routes to the
    same data, not a set of requirements.
    """
    return any(perms.get(k, False) for k in entity.perm_key)


class OntologyError(Exception):
    """The ontology file disagrees with the code. Raised at import time."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise OntologyError(msg)


def _build_entity(name: str, spec: dict) -> Entity:
    where = f"entity '{name}'"

    model_name = spec.get("model")
    table_name = spec.get("table")
    _require(bool(model_name) != bool(table_name),
             f"{where}: name exactly one of model (a class this service owns) "
             f"or table (one it reads but does not own)")
    if model_name:
        _require(model_name in _MODELS,
                 f"{where}: unknown model {model_name!r}; known: {sorted(_MODELS)}")
        model = _MODELS[model_name]
    else:
        model = _model_for_table(table_name, spec.get("fields") or {}, where)
        model_name = table_name

    scope_name = spec.get("scope")
    _require(scope_name in _SCOPES,
             f"{where}: unknown scope {scope_name!r}; known: {sorted(_SCOPES)}")

    raw_perm = spec.get("perm_key")
    _require(bool(raw_perm), f"{where}: perm_key is required")
    perm_keys = ((raw_perm,) if isinstance(raw_perm, str) else tuple(raw_perm))
    _require(all(isinstance(k, str) and k for k in perm_keys),
             f"{where}: perm_key must be a name or a list of names")
    _require(bool(spec.get("label")), f"{where}: label is required")

    fields: dict[str, Field] = {}
    for fname, fspec in (spec.get("fields") or {}).items():
        # Built into the mapped class (a scope needs the column) but never
        # offered to the planner and never returned. These are join keys —
        # `run_id` on a planning table — which mean nothing to whoever is asking
        # and would only spend tokens in the schema block.
        if fspec.get("internal"):
            continue
        kind = fspec.get("kind")
        _require(kind in _KINDS, f"{where}.{fname}: unknown kind {kind!r}")
        _require(bool(fspec.get("label")), f"{where}.{fname}: label is required")
        # The check that matters: a registered column the model does not have
        # would otherwise fail at query time, for whoever happened to ask first.
        # For a `table` entity this is trivially true — the class was built from
        # this very list — so the equivalent guarantee comes from
        # test_ontology_matches_the_database, which compares it to the real one.
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
            is_expression=bool(fspec.get("expr")),
        )
    _require(bool(fields), f"{where}: needs at least one field")

    links_spec = spec.get("links") or {}

    date_field = spec.get("date_field")
    # May reach through one link: "voucher.voucher_date". A line item usually
    # carries no date of its own that means anything — journal_voucher_lines has
    # only its row-insert stamp, which on migrated data is the day of the
    # migration and nothing else. Every row of it reads 2026, so every question
    # about 2020-2025 came back empty, and the reply said there were no postings.
    # _apply_period already resolves dotted names and joins; this only had to
    # stop refusing them.
    if date_field and "." in str(date_field):
        link_name, _, far_field = str(date_field).partition(".")
        _require(link_name in links_spec,
                 f"{where}: date_field {date_field!r} goes through link "
                 f"{link_name!r}, which is not declared")
        _require(links_spec[link_name].get("cardinality") == "many_to_one",
                 f"{where}: date_field {date_field!r} goes through a "
                 f"one_to_many link, which would multiply rows")
        _deferred_axis_checks.append((name, links_spec[link_name]["target"],
                                      far_field, date_field))
    else:
        _require(date_field in fields,
                 f"{where}: date_field {date_field!r} is not a declared field")
    # The default time axis must be a column every row actually has. A nullable
    # one silently drops rows from every "last N months" question, and the
    # answer still looks plausible — PO shipped with date_field: placed_at,
    # which is NULL on 97% of rows because orders mirrored from NC were never
    # placed through EPMS. Nobody would have noticed from the replies.
    _col = getattr(model, date_field) if "." not in str(date_field) else None
    _column_obj = (getattr(getattr(_col, "property", None), "columns", [None])[0]
                   if _col is not None else None)
    # Only meaningful for a class this service owns. A `table` entity's columns
    # are all declared nullable because the ontology does not know the real
    # constraint — asking for an acknowledgement on every one of them would turn
    # a real warning into boilerplate. test_ontology_matches_the_database reads
    # the actual is_nullable instead.
    if spec.get("table"):
        _column_obj = None
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

    df = spec.get("default_filter")
    _default_filter: tuple[str, object] | None = None
    _df_note = ""
    _df_stand_down: tuple[str, ...] = ()
    if df:
        _require(isinstance(df, dict) and "field" in df and "value" in df,
                 f"{where}.default_filter: needs field and value")
        _require(bool(df.get("note")),
                 f"{where}.default_filter: needs a note — it changes what a "
                 f"query means, so the reply has to be able to say so")
        _default_filter = (df["field"], df["value"])
        _df_note = df["note"]
        _df_stand_down = tuple(df.get("stand_down_on") or ())

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
        name=name, model=model, label=spec["label"], perm_key=perm_keys,
        apply_scope=_SCOPES[scope_name], date_field=date_field,
        fields=fields, metrics=metrics, links=links,
        nullable_axis_ok=spec.get("date_field_nullable_ok") is True,
        default_filter=_default_filter, default_filter_note=_df_note,
        default_filter_stand_down=_df_stand_down,
    )


def load(paths: Path | tuple[Path, ...] = ONTOLOGY_FILES) -> dict[str, Entity]:
    if isinstance(paths, Path):
        paths = (paths,)

    specs: dict[str, dict] = {}
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _require(raw.get("version") == 1,
                 f"{path.name}: unsupported version {raw.get('version')!r}")
        part = raw.get("entities") or {}
        _require(bool(part), f"{path.name}: no entities declared")
        # A name defined twice would have one silently win depending on file
        # order, and the loser's traps would go with it.
        clash = set(part) & set(specs)
        _require(not clash, f"{path.name}: redefines {sorted(clash)}")
        specs.update(part)

    entities = {name: _build_entity(name, spec) for name, spec in specs.items()}

    # Cross-entity checks, once every entity exists.
    while _deferred_axis_checks:
        owner, target, far_field, spelled = _deferred_axis_checks.pop()
        _require(target in entities,
                 f"entity '{owner}': date_field {spelled!r} targets unknown "
                 f"entity {target!r}")
        far = entities[target].fields.get(far_field)
        _require(far is not None,
                 f"entity '{owner}': date_field {spelled!r} — {target} has no "
                 f"field {far_field!r}")
        _require(far.kind in (DATE, DATETIME),
                 f"entity '{owner}': date_field {spelled!r} is {far.kind!r}, "
                 f"not a date")

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
