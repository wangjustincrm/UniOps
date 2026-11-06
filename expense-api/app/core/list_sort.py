"""Click-to-sort ordering for the paged OA list endpoints.

Contract shared by every UniOps list endpoint: `sort=<key>&dir=asc|desc`.
Only whitelisted keys are accepted (the key reaches ORDER BY, so the whitelist
is the SQL-injection boundary); anything else is a 422. Empty values sort last
in BOTH directions, and a unique tiebreak keeps page 2 continuing page 1.
"""
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func


def lower_text(col: Any) -> Any:
    """Case-insensitive text key; '' / whitespace counts as empty (sorts last)."""
    return func.lower(func.nullif(func.trim(col), ""))


def check_sort_key(sort: str | None, allowed: Mapping[str, Any]) -> None:
    if sort is not None and sort not in allowed:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}")


def sort_clauses(
    sort: str | None, dir: str, whitelist: Mapping[str, Any], tiebreak: Sequence[Any],
) -> list[Any] | None:
    """ORDER BY clauses, or None when no sort was asked for (caller keeps its default)."""
    if sort is None:
        return None
    check_sort_key(sort, whitelist)
    col = whitelist[sort]
    return [(col.desc() if dir == "desc" else col.asc()).nulls_last(), *tiebreak]


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def sort_in_memory(
    rows: list, sort: str, dir: str, getters: Mapping[str, Callable[[Any], Any]],
    tiebreak: Callable[[Any], Any],
) -> list:
    """Same rules for a list the endpoint has to assemble in Python (a union of
    two sources): empties last either way, text case-insensitive, stable on
    `tiebreak` (ascending regardless of `dir`)."""
    check_sort_key(sort, getters)
    get = getters[sort]

    def norm(v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v

    present = [r for r in rows if not _empty(get(r))]
    empty = [r for r in rows if _empty(get(r))]
    present.sort(key=tiebreak)
    present.sort(key=lambda r: norm(get(r)), reverse=(dir == "desc"))  # stable
    empty.sort(key=tiebreak)
    return present + empty
