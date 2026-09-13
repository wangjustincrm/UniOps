"""Validate and execute the assistant's controlled queries.

The planner (an LLM) never writes SQL. It emits a structured description naming
an entity, some whitelisted fields, and a handful of operators; this module
checks every one of those names against app/ontology/epms.yaml and then
builds the statement itself. A name the registry does not know is rejected, so
the planner cannot reach a column we did not deliberately expose.

Two gates run on every query, in this order:

  1. ``perms[entity.perm_key]`` — the Access Control Matrix gate. Cheap, decided
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
)

# Row caps. A planner asking for "all of them" gets this many, and the response
# says so — silent truncation would read as a complete answer when it is not.
MAX_ROWS_DETAIL = 100
MAX_ROWS_GROUPED = 200
STATEMENT_TIMEOUT_MS = 5_000

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


def _apply_where(stmt, entity: Entity, clauses: list[dict]):
    for clause in clauses:
        name = clause.get("field")
        op = (clause.get("op") or "eq").lower()
        value = clause.get("value")

        col = _column(entity, name)
        kind = entity.fields[name].kind
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


def _apply_period(stmt, entity: Entity, period: dict | None):
    """Relative time windows, resolved server-side.

    The planner says "the last three months"; it does not get to say what today
    is. Anchoring here keeps the answer stable regardless of what the model
    believes the date to be.
    """
    if not period:
        return stmt
    name = period.get("field") or entity.date_field
    col = _column(entity, name)

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
        kind = entity.fields[name].kind
        if frm:
            stmt = stmt.where(col >= _coerce(kind, frm))
        if to:
            stmt = stmt.where(col <= _coerce(kind, to))
        return stmt

    if entity.fields[name].kind == DATE:
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


async def execute(db: AsyncSession, request: dict, scope: dict) -> dict:
    """Run one controlled query. Raises QueryRejected on anything unrecognised.

    `scope` is build_scope()'s output; both gates are read from it and neither is
    optional.
    """
    entity = get_entity(request.get("entity") or "")
    if entity is None:
        _reject(f"Unknown entity. Available: {', '.join(sorted(REGISTRY))}")

    # Gate 1 — may this user see this kind of document at all.
    if not scope.get("perms", {}).get(entity.perm_key, False):
        return {"entity": entity.name, "rows": [], "row_count": 0,
                "truncated": False, "denied": True}

    if entity.apply_scope is None:  # pragma: no cover — registry invariant
        raise RuntimeError(f"Entity {entity.name} has no scope function")

    group_by = request.get("group_by") or []
    metrics = request.get("metrics") or []
    grouped = bool(group_by or metrics)

    if grouped:
        selected, labels = [], []
        for name in group_by:
            selected.append(_column(entity, name))
            labels.append(name)
        for key in metrics:
            metric = entity.metrics.get(key)
            if metric is None:
                _reject(
                    f"Unknown metric '{key}' on {entity.name}. "
                    f"Available: {', '.join(sorted(entity.metrics))}"
                )
            agg = _AGG_FN[metric.fn]
            target = sa.literal_column("*") if metric.field == "*" else _column(entity, metric.field)
            selected.append(agg(target).label(key))
            labels.append(key)
        stmt = select(*selected)
    else:
        fields = request.get("select") or list(entity.fields)
        labels = list(fields)
        stmt = select(*[_column(entity, f) for f in fields])

    # Gate 2 — the row filter. Unconditional, and applied before any caller
    # supplied predicate so nothing can be OR-ed around it.
    stmt = entity.apply_scope(stmt, scope)
    stmt = _apply_where(stmt, entity, request.get("where") or [])
    stmt = _apply_period(stmt, entity, request.get("period"))

    if grouped and group_by:
        stmt = stmt.group_by(*[_column(entity, n) for n in group_by])

    order = request.get("order_by")
    if order:
        name = order.get("field")
        if name in entity.metrics and grouped:
            col = sa.literal_column(f'"{name}"')
        else:
            col = _column(entity, name)
        stmt = stmt.order_by(col.desc() if order.get("desc") else col.asc())

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
    return {"entity": entity.name, "rows": rows, "row_count": len(rows),
            "truncated": truncated, "denied": False}
