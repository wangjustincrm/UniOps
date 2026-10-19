"""Click-to-sort list headers — the server half (shared UniOps list contract).

Every paged list takes ``?sort=<key>&dir=asc|desc``. ``sort`` goes straight
into ORDER BY, so each endpoint keeps a module-level whitelist
``{key: SQLAlchemy expression}`` and an unknown key is a 422 — the whitelist
is the SQL-injection boundary. No ``sort`` keeps the endpoint's own default
order unchanged.
"""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import case


def check_sort(sort: str | None, whitelist: Mapping[str, Any]) -> None:
    """Raise 422 for a key the list does not offer. Call before any query runs."""
    if sort is not None and sort not in whitelist:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}")


def apply_sort(stmt, sort: str | None, dir: str, whitelist: Mapping[str, Any],
               tiebreak, default=None):
    """ORDER BY the whitelisted column, NULLs last in BOTH directions (Postgres
    puts them first on DESC), then ``tiebreak`` (a unique column) so page 2
    continues page 1. ``sort=None`` → ``default`` (a sequence of ORDER BY
    clauses), or ``stmt`` untouched when it already carries its order."""
    check_sort(sort, whitelist)
    if sort is None:
        return stmt.order_by(*default) if default is not None else stmt
    expr = whitelist[sort]
    expr = expr.asc() if dir == "asc" else expr.desc()
    return stmt.order_by(expr.nulls_last(), tiebreak)


def label_case(col, labels: Mapping[Any, str], else_=None):
    """Sort a coded column by the label the page shows for it (lower-cased).
    ``else_`` orders codes the map does not name (default: NULL, i.e. last)."""
    return case({k: v.lower() for k, v in labels.items()}, value=col, else_=else_)
