"""Click-to-sort for mrp-api's paged lists (the UniOps list contract).

Every list takes `sort` (a whitelisted key) and `dir` (asc | desc). The key
is looked up in a per-endpoint dict of SQLAlchemy expressions and never
reaches SQL itself — the whitelist is the injection boundary; an unknown key
is a 422. `sort` absent = the endpoint's existing default order.

Empty values sort last in BOTH directions (Postgres puts NULLs first on DESC
by default), and the ORDER BY ends in a unique tiebreak so a row can never
appear on two pages.
"""
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal, TypeVar

from fastapi import HTTPException

SortDir = Literal["asc", "desc"]
T = TypeVar("T")


def resolve(sort: str | None, dir_: SortDir | None, keys: Iterable[str],
            default_dir: SortDir = "asc") -> tuple[str, SortDir] | None:
    """(key, direction), or None for the default order. 422 on an unknown key."""
    if sort is None:
        return None
    if sort not in set(keys):
        raise HTTPException(status_code=422, detail=f"Cannot sort by {sort!r}")
    return sort, dir_ or default_dir


def order_by(columns: Mapping[str, Any], key: str, dir_: SortDir, *tiebreak) -> list:
    """ORDER BY terms: the whitelisted column, nulls last either way, then the tiebreak."""
    col = columns[key]
    return [(col.desc() if dir_ == "desc" else col.asc()).nulls_last(), *tiebreak]


def sort_rows(rows: list[T], get: Callable[[T], Any], dir_: SortDir) -> list[T]:
    """In-memory equivalent for lists built in Python: empties (None / '')
    last in both directions; ties keep the incoming (default) order."""
    present = [r for r in rows if get(r) not in (None, "")]
    empty = [r for r in rows if get(r) in (None, "")]
    present.sort(key=get, reverse=dir_ == "desc")
    return present + empty
