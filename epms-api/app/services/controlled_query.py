"""Validate and execute the assistant's controlled queries.

The planner (an LLM) never writes SQL. It emits a structured description naming
an entity, some whitelisted fields, and a handful of operators; this module
checks every one of those names against app/ontology/epms.yaml and then
builds the statement itself. A name the registry does not know is rejected, so
the planner cannot reach a column we did not deliberately expose.

Two gates run on every query, in this order:

  1. ``may_view(entity, perms)`` — the Access Control Matrix gate. Cheap, decided
     before any row is touched, and the same key the list endpoints check.
  2. ``entity.apply_scope(stmt, scope)`` — the row filter. Applied
     unconditionally: there is no request field that can switch it off, and an
     entity whose scope callable is missing raises rather than running
     unfiltered. Fail closed, never open.

The whole statement additionally runs inside a READ ONLY transaction, so a
future bug here cannot write even if it tried to.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ontology import (
    BOOL, DATE, DATETIME, ENUM, INT, MONEY, REGISTRY, TEXT, Entity, get_entity,
    may_view,
)

# Row caps. A planner asking for "all of them" gets this many, and the response
# says so — silent truncation would read as a complete answer when it is not.
MAX_ROWS_DETAIL = 100
MAX_ROWS_GROUPED = 200
# An export is a file someone will work from, so the on-screen caps would make
# it quietly incomplete. Still bounded — a request for everything should not be
# able to exhaust memory — and the result says when the bound bit.
MAX_ROWS_EXPORT = 50_000
STATEMENT_TIMEOUT_MS = 5_000

# Internal label for the row count attached to a bare aggregate; never a field
# name, so it cannot collide with anything the ontology declares.
_MATCHED = "__matched_rows"

# Operators permitted per field kind. Restricting by kind keeps the planner from
# building queries that are technically valid SQL but meaningless (LIKE against
# a money column) or expensive (LIKE against an unindexed date).
_OPS_BY_KIND: dict[str, frozenset[str]] = {
    TEXT: frozenset({"eq", "ne", "in", "not_in", "like"}),
    ENUM: frozenset({"eq", "ne", "in", "not_in"}),
    MONEY: frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "between"}),
    INT: frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "between"}),
    DATE: frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "between"}),
    DATETIME: frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "between"}),
    BOOL: frozenset({"eq"}),
}

_AGG_FN = {"sum": func.sum, "count": func.count, "avg": func.avg,
           "min": func.min, "max": func.max}


class QueryRejected(Exception):
    """The request named something the registry does not expose, or is malformed.

    Carries a message meant to be read by the planner on a retry, so it says
    what was wrong and what is available instead.
    """


def _reject(msg: str) -> None:
    raise QueryRejected(msg)


MAX_HOPS = 3

def _resolve(entity: Entity, name: str):
    """Resolve a field reference to (column, path).

    Accepts `field` on this entity, or a dotted path along declared links —
    `originating_pr.department_name`, or two hops for an invoice:
    `order.originating_pr.department_name`. purchase_orders has no department
    column at all; it lives on the requisition, so the only honest way to group
    orders by department is to walk there.

    Only declared links, and at most MAX_HOPS of them. Without this the planner
    reached for whatever column looked close enough — it grouped POs by
    budget_code and headed the column "department", which is not an
    approximation, it is a different fact.

    Returns `path` as the list of (link, target_entity) hops, empty for a local
    field.
    """
    parts = name.split(".")
    if len(parts) == 1:
        return _column(entity, name), []
    if len(parts) - 1 > MAX_HOPS:
        _reject(f"'{name}' crosses more than {MAX_HOPS} relationships")

    path = []
    current = entity
    for i, step in enumerate(parts[:-1]):
        link = current.links.get(step)
        if link is None:
            _reject(
                f"Unknown link '{step}' on {current.name}. "
                f"Available: {', '.join(sorted(current.links)) or '(none)'}"
            )
        target = REGISTRY.get(link.target)
        if target is None:  # pragma: no cover — validated at ontology load
            _reject(f"Link '{step}' points at unknown entity '{link.target}'")
        path.append((current, link, target))
        current = target

    field_name = parts[-1]
    if field_name not in current.fields:
        _reject(
            f"Unknown field '{field_name}' on {current.name} (via "
            f"{'.'.join(parts[:-1])}). Available: {', '.join(sorted(current.fields))}"
        )
    return getattr(current.model, field_name), path


def _kind_of(entity: Entity, name: str) -> str:
    """Field kind, following the same path _resolve does."""
    parts = name.split(".")
    current = entity
    for step in parts[:-1]:
        link = current.links.get(step)
        if link is None:
            _reject(f"Unknown link '{step}' on {current.name}")
        current = REGISTRY[link.target]
    return current.fields[parts[-1]].kind


def _field_of(entity: Entity, name: str):
    """The Field behind a result column, or None if the column is a metric."""
    parts = name.split(".")
    current = entity
    for step in parts[:-1]:
        link = current.links.get(step)
        if link is None:
            return None
        current = REGISTRY[link.target]
    return current.fields.get(parts[-1])


def _column(entity: Entity, name: str):
    if name not in entity.fields:
        _reject(
            f"Unknown field '{name}' on {entity.name}. "
            f"Available: {', '.join(sorted(entity.fields))}"
        )
    col = getattr(entity.model, name, None)
    if col is None:
        # Registry lists a field the model does not have — a registry bug, not a
        # planner mistake. Surfacing it as a rejection keeps the request safe.
        _reject(f"Field '{name}' is registered but missing on {entity.name}")
    return col


def _coerce(kind: str, raw: Any) -> Any:
    """Turn a JSON scalar into the type the column expects.

    Dates arrive as ISO strings; money as numbers or numeric strings. A value
    that will not convert is a rejection, never a silent None — filtering on
    None would quietly widen the result set.
    """
    if raw is None:
        return None
    try:
        if kind in (DATE, DATETIME):
            if isinstance(raw, (date, datetime)):
                return raw
            text = str(raw).strip()
            if kind == DATE:
                return date.fromisoformat(text[:10])
            # Accept a trailing Z, which json encoders commonly emit.
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        if kind == MONEY:
            return Decimal(str(raw))
        if kind == INT:
            return int(raw)
        if kind == BOOL:
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("true", "1", "yes")
        return str(raw)
    except (ValueError, ArithmeticError, TypeError):
        _reject(f"Value {raw!r} is not a valid {kind}")


def _apply_default_filter(stmt, entity: Entity, clauses: list[dict], hops: list,
                          request: dict) -> tuple:
    """Apply the entity's safe default, unless the caller is asking about it.

    The BOM case is the one this exists for, and it has a failure on each side.
    With no default, "what is CF0063 made of" merges six versions of the recipe
    into one list of components. With the default forced — which is what a scope
    would do — "how many BOM versions does CS0026 have" answers 1 where the
    answer is 7, and that is the worse of the two: a confident wrong number
    rather than a muddled right one.

    So it assumes the default version and steps aside as soon as the question is
    about versions. What counts as "about versions" is declared in the ontology
    (`stand_down_on`) rather than guessed from the shape of the field name —
    asking for `version` is plainly such a question and shares no prefix with
    `is_default`, so no amount of string matching would have found it.
    """
    df = entity.default_filter
    if not df:
        return stmt, False
    field, value = df

    referenced = {c.get("field") for c in clauses if c.get("field")}
    referenced |= set(request.get("group_by") or [])
    referenced |= set(request.get("select") or [])
    order = request.get("order_by") or {}
    if order.get("field"):
        referenced.add(order["field"])

    if referenced & ({field} | set(entity.default_filter_stand_down)):
        return stmt, False

    col, path = _resolve(entity, field)
    hops.extend(path)
    return stmt.where(col.is_(True) if value is True else col == value), True


def _apply_where(stmt, entity: Entity, clauses: list[dict], hops: list):
    for clause in clauses:
        name = clause.get("field")
        op = (clause.get("op") or "eq").lower()
        value = clause.get("value")

        col, path = _resolve(entity, name)
        hops.extend(path)
        kind = _kind_of(entity, name)
        allowed = _OPS_BY_KIND.get(kind, frozenset())
        if op not in allowed:
            _reject(
                f"Operator '{op}' is not allowed on {entity.name}.{name} "
                f"({kind}). Allowed: {', '.join(sorted(allowed))}"
            )

        if op in ("in", "not_in"):
            if not isinstance(value, list) or not value:
                _reject(f"Operator '{op}' needs a non-empty list for '{name}'")
            if len(value) > 100:
                _reject(f"Too many values for '{name}' (max 100)")
            items = [_coerce(kind, v) for v in value]
            stmt = stmt.where(col.in_(items) if op == "in" else col.notin_(items))
            continue

        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                _reject(f"Operator 'between' needs exactly two values for '{name}'")
            lo, hi = (_coerce(kind, value[0]), _coerce(kind, value[1]))
            stmt = stmt.where(col.between(lo, hi))
            continue

        if op == "like":
            text = _coerce(TEXT, value)
            # The planner supplies a bare term; the wildcards are ours so it
            # cannot inject a leading % that forces a full scan on every column.
            safe = re.sub(r"[%_\\]", lambda m: "\\" + m.group(0), text)
            stmt = stmt.where(col.ilike(f"%{safe}%"))
            continue

        operand = _coerce(kind, value)
        ops = {"eq": col.__eq__, "ne": col.__ne__, "gt": col.__gt__,
               "gte": col.__ge__, "lt": col.__lt__, "lte": col.__le__}
        stmt = stmt.where(ops[op](operand))
    return stmt


def _apply_period(stmt, entity: Entity, period: dict | None, hops: list):
    """Relative time windows, resolved server-side.

    The planner says "the last three months"; it does not get to say what today
    is. Anchoring here keeps the answer stable regardless of what the model
    believes the date to be.
    """
    if not period:
        return stmt
    name = period.get("field") or entity.date_field
    col, path = _resolve(entity, name)
    hops.extend(path)

    months = period.get("last_n_months")
    days = period.get("last_n_days")
    if months is not None:
        months = int(months)
        if not 1 <= months <= 60:
            _reject("last_n_months must be between 1 and 60")
        start = datetime.now(timezone.utc) - timedelta(days=31 * months)
    elif days is not None:
        days = int(days)
        if not 1 <= days <= 1830:
            _reject("last_n_days must be between 1 and 1830")
        start = datetime.now(timezone.utc) - timedelta(days=days)
    else:
        frm, to = period.get("from"), period.get("to")
        kind = _kind_of(entity, name)
        if frm:
            stmt = stmt.where(col >= _coerce(kind, frm))
        if to:
            stmt = stmt.where(col <= _coerce(kind, to))
        return stmt

    if _kind_of(entity, name) == DATE:
        return stmt.where(col >= start.date())
    return stmt.where(col >= start)


def _serialise(value: Any) -> Any:
    """Shape a cell for JSON.

    Decimals go out as strings, matching what every other UniOps endpoint does —
    the consumer already knows to widen them, and a float here would introduce a
    second convention for money in the same system.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


async def _apply_hops(db: AsyncSession, stmt, hops: list, scope: dict):
    """LEFT JOIN each relationship, and scope the far side as its own entity.

    The scope matters more than it looks. A purchase order can be visible for
    reasons that have nothing to do with its requisition — you created it, or a
    task was assigned to you — so "can see the PO" does not imply "can see the
    PR". Joining without the target's own filter would hand over fields from a
    requisition this person cannot open.

    LEFT rather than INNER so a row whose far side is out of scope still counts:
    dropping it would quietly change every total, which is the failure this
    layer exists to avoid. Such a row reports the linked field as null — unknown,
    not absent.
    """
    seen: set[tuple[str, str]] = set()
    for source, link, target in hops:
        key = (source.name, link.name)
        if key in seen:
            continue
        seen.add(key)

        visible = await target.apply_scope(select(target.model.id), scope, db)
        onclause = sa.and_(
            getattr(source.model, link.local) == getattr(target.model, link.remote),
            target.model.id.in_(visible),
        )
        stmt = stmt.join(target.model, onclause, isouter=True)
    return stmt


async def execute(db: AsyncSession, request: dict, scope: dict,
                  for_export: bool = False) -> dict:
    """Run one controlled query. Raises QueryRejected on anything unrecognised.

    `scope` is build_scope()'s output; both gates are read from it and neither is
    optional.
    """
    entity = get_entity(request.get("entity") or "")
    if entity is None:
        _reject(f"Unknown entity. Available: {', '.join(sorted(REGISTRY))}")

    # Gate 1 — may this user see this kind of document at all.
    if not may_view(entity, scope.get("perms") or {}):
        return {"entity": entity.name, "rows": [], "row_count": 0,
                "truncated": False, "denied": True}

    if entity.apply_scope is None:  # pragma: no cover — registry invariant
        raise RuntimeError(f"Entity {entity.name} has no scope function")

    group_by = request.get("group_by") or []
    metrics = request.get("metrics") or []
    grouped = bool(group_by or metrics)

    # Hops needed by any part of the request, collected before the statement is
    # built so each relationship is joined exactly once.
    hops: list = []

    if grouped:
        selected, labels = [], []
        for name in group_by:
            col, path = _resolve(entity, name)
            hops.extend(path)
            selected.append(col)
            labels.append(name)
        for key in metrics:
            metric = entity.metrics.get(key)
            if metric is None:
                _reject(
                    f"Unknown metric '{key}' on {entity.name}. "
                    f"Available: {', '.join(sorted(entity.metrics))}"
                )
            agg = _AGG_FN[metric.fn]
            if metric.field == "*":
                target = sa.literal_column("*")
            else:
                target, path = _resolve(entity, metric.field)
                hops.extend(path)
            selected.append(agg(target).label(key))
            labels.append(key)
        # select_from is not optional here. count(*) uses a literal_column, which
        # is bound to no table, so a request for count ALONE leaves SQLAlchemy
        # with nothing to infer a FROM from — it emits a table-less SELECT
        # count(*), which returns 1 no matter how many rows exist. Asking for
        # count alongside any real column hid this; asking for it by itself did
        # not.
        stmt = select(*selected).select_from(entity.model)
        if not group_by:
            # A bare aggregate over zero rows returns NULL, which reads exactly
            # like "there were rows but the value was empty" — the caller cannot
            # tell "no draft PAs exist" from "drafts exist with no amount", and a
            # model asked to narrate that will hedge across both. Count the rows
            # alongside so the difference is a fact rather than a guess. Stripped
            # out of the row before it is returned; surfaced as matched_rows.
            stmt = stmt.add_columns(func.count().label(_MATCHED))
            labels.append(_MATCHED)
    else:
        fields = request.get("select") or list(entity.fields)
        labels = list(fields)
        cols = []
        for f in fields:
            col, path = _resolve(entity, f)
            hops.extend(path)
            cols.append(col)
        stmt = select(*cols).select_from(entity.model)

    # Gate 2 — the row filter. Unconditional, and applied before any caller
    # supplied predicate so nothing can be OR-ed around it.
    stmt = await entity.apply_scope(stmt, scope, db)
    clauses = list(request.get("where") or [])
    stmt, applied_default = _apply_default_filter(
        stmt, entity, clauses, hops, request)
    stmt = _apply_where(stmt, entity, clauses, hops)
    stmt = _apply_period(stmt, entity, request.get("period"), hops)

    # Resolve grouping and ordering BEFORE the joins are built, because
    # resolving is what discovers which joins are needed. Ordering used to go
    # through _column, which cannot cross a link at all — "compare versions 1.5
    # and 1.6, ordered by version" was rejected with "Unknown field
    # 'bom.version'" while the identical name worked in select and where, and
    # the planner was told its query was invalid when it was not. Grouping did
    # use _resolve but dropped the path it returned, so a group_by across a link
    # only worked when select happened to mention the same side.
    group_cols = []
    if grouped and group_by:
        for n in group_by:
            col, path = _resolve(entity, n)
            hops.extend(path)
            group_cols.append(col)

    order = request.get("order_by")
    order_col = None
    if order:
        name = order.get("field")
        if name in entity.metrics and grouped:
            order_col = sa.literal_column(f'"{name}"')
        else:
            order_col, path = _resolve(entity, name)
            hops.extend(path)

    stmt = await _apply_hops(db, stmt, hops, scope)

    if group_cols:
        stmt = stmt.group_by(*group_cols)
    if order_col is not None:
        stmt = stmt.order_by(
            order_col.desc() if order.get("desc") else order_col.asc())

    # Totals for a grouped query, computed in SQL.
    #
    # Not a nicety. Given nine department subtotals and asked for the total, the
    # model added them itself and came out 2,000 over — every subtotal correct,
    # the sum wrong, and nothing in the reply to show it. Telling it not to do
    # arithmetic does not stop it; having the answer already there does. Run over
    # the whole result, not the returned page, or a capped list would total only
    # what happened to fit.
    totals: dict | None = None
    if grouped and group_by:
        total_cols = []
        total_labels = []
        for key in metrics:
            metric = entity.metrics[key]
            agg = _AGG_FN[metric.fn]
            if metric.field == "*":
                target = sa.literal_column("*")
            else:
                target = _resolve(entity, metric.field)[0]
            total_cols.append(agg(target).label(key))
            total_labels.append(key)
        if total_cols:
            t_stmt = select(*total_cols).select_from(entity.model)
            t_stmt = await entity.apply_scope(t_stmt, scope, db)
            t_stmt = _apply_where(t_stmt, entity, request.get("where") or [], [])
            t_stmt = _apply_period(t_stmt, entity, request.get("period"), [])
            t_stmt = await _apply_hops(db, t_stmt, hops, scope)
            t_row = (await db.execute(t_stmt)).first()
            if t_row is not None:
                totals = {k: _serialise(v) for k, v in zip(total_labels, t_row)}

    if for_export:
        # The planner's own limit is ignored here: it was chosen for a chat
        # reply ("top 5 vendors"), and exporting the top 5 of a report someone
        # asked to have in full is the wrong file.
        cap = limit = MAX_ROWS_EXPORT
    else:
        cap = MAX_ROWS_GROUPED if grouped else MAX_ROWS_DETAIL
        limit = int(request.get("limit") or cap)
        limit = max(1, min(limit, cap))
    # One extra row tells us whether the cap actually bit, so the caller can say
    # "showing the first N" instead of implying it saw everything.
    stmt = stmt.limit(limit + 1)

    await db.execute(sa.text("SET LOCAL TRANSACTION READ ONLY"))
    await db.execute(sa.text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
    result = await db.execute(stmt)
    raw = result.all()

    truncated = len(raw) > limit
    rows = [dict(zip(labels, (_serialise(v) for v in row))) for row in raw[:limit]]

    matched = None
    if grouped and not group_by:
        matched = rows[0].pop(_MATCHED, None) if rows else 0
        matched = int(matched) if matched is not None else 0

    out = {"entity": entity.name, "rows": rows, "row_count": len(rows),
           "truncated": truncated, "denied": False,
           # What the columns are called and what they mean, so an export can
           # head them with something a person recognises.
           "labels": labels, "row_cap": limit}

    # Say so when a default narrowed the question. A result that quietly shows
    # one version of six is not wrong, but a reply that does not mention it is.
    if applied_default and entity.default_filter_note:
        out["assumption"] = entity.default_filter_note

    # Codes that stand for something, for the columns actually returned. The
    # schema block carries these too, but only the planner sees that; whatever
    # writes the reply sees this result and nothing else, and without the
    # mapping it reports "type 2" — a number the reader cannot act on.
    coded = {}
    for label in labels:
        f = _field_of(entity, label)
        if f is not None and f.value_labels:
            coded[label] = {k: v for k, v in f.value_labels}
    if coded:
        out["value_labels"] = coded
    if matched is not None:
        out["matched_rows"] = matched
    if totals is not None:
        out["totals"] = totals
    return out
