"""Click-to-sort for inventory-api's paged lists (the UniOps list contract).

Every list takes `sort` (a whitelisted key) and `dir` (asc | desc). The key
goes into ORDER BY, so it is looked up in a per-endpoint whitelist of FIXED
SQL fragments and never interpolated itself: the whitelist is the SQL
injection boundary. An unknown key is a 422.

These endpoints already had a `sort` with one fixed direction per key
(qty / amount largest first, codes ascending). A caller that sends one of
those old keys without `dir` keeps exactly that direction.

Empty values sort last in BOTH directions (Postgres puts NULLs first on DESC
by default), and every ORDER BY ends in a unique tiebreak so a row can never
appear on two pages.
"""
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal, TypeVar

from fastapi import HTTPException

SortDir = Literal["asc", "desc"]
T = TypeVar("T")


def resolve(sort: str | None, dir_: SortDir | None, keys: Iterable[str],
            legacy_dir: Mapping[str, SortDir] | None = None) -> tuple[str, SortDir] | None:
    """(key, direction) for a request, or None for the endpoint's default order.

    `dir_` None means the caller did not send one: an old fixed-direction key
    keeps its old direction, anything else is ascending.
    """
    if sort is None:
        return None
    if sort not in set(keys):
        raise HTTPException(status_code=422, detail=f"Cannot sort by {sort!r}")
    if dir_ is None:
        dir_ = (legacy_dir or {}).get(sort, "asc")
    return sort, dir_


def order_sql(expr: str, dir_: SortDir, tiebreak: str) -> str:
    """ORDER BY body for a whitelisted SQL fragment. `expr` and `tiebreak`
    must come from the code, never from the request."""
    return f"{expr} {'desc' if dir_ == 'desc' else 'asc'} nulls last, {tiebreak}"


def sort_rows(rows: list[T], get: Callable[[T], Any], dir_: SortDir,
              tiebreak: Callable[[T], Any]) -> list[T]:
    """In-memory equivalent of order_sql, for lists built in Python:
    empties (None / '') last in both directions, then the unique tiebreak."""
    present = [r for r in rows if get(r) not in (None, "")]
    empty = [r for r in rows if get(r) in (None, "")]
    present.sort(key=tiebreak)
    present.sort(key=get, reverse=dir_ == "desc")    # stable: ties keep tiebreak order
    empty.sort(key=tiebreak)
    return present + empty
