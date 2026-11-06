"""Click-to-sort ordering for paged list endpoints (invoices, parts, vendors).

Contract shared by every UniOps list endpoint: `sort=<key>&dir=asc|desc`.
`sort` goes straight into ORDER BY, so only keys in the endpoint's whitelist
are accepted — the whitelist is the SQL-injection boundary; anything else is
a 422. Empty values sort last in BOTH directions (Postgres would put NULLs
first on DESC), and a unique tiebreak keeps page 2 continuing page 1.
"""
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func


def blank_as_null(col: Any) -> Any:
    """Text column where '' / whitespace means "no value": NULL so it sorts last."""
    return func.nullif(func.trim(col), "")


def lower_text(col: Any) -> Any:
    """Case-insensitive text key ("amazon" with "Amazon"), blanks last."""
    return func.lower(blank_as_null(col))


def sort_clauses(
    sort: str | None,
    dir: str,
    whitelist: Mapping[str, Any | Sequence[Any]],
    tiebreak: Sequence[Any],
) -> list[Any] | None:
    """ORDER BY clauses for `sort`/`dir`, or None when no sort was asked for
    (the caller then keeps its own default order). A whitelist value may be a
    single expression or a sequence of them (e.g. "Net 30" = text, then days)."""
    if sort is None:
        return None
    if sort not in whitelist:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}")
    exprs = whitelist[sort]
    if not isinstance(exprs, (list, tuple)):
        exprs = [exprs]
    ordered = [(e.desc() if dir == "desc" else e.asc()).nulls_last() for e in exprs]
    return [*ordered, *tiebreak]
