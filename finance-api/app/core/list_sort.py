"""Click-to-sort for finance list endpoints — the one `sort` + `dir` contract
every UniOps list shares (see packages/shell/src/ui/sortable.tsx).

Each endpoint keeps a module-level whitelist `{key: column/expression}`. The
key is the only thing taken from the request; it is looked up in that dict and
never reaches SQL as text, so the whitelist is the SQL-injection boundary. An
unknown key is a 422, never a silent fallback — a typo would otherwise look
like "sorting does nothing".

NULLs go last in BOTH directions: Postgres puts them first under DESC, which
would bury a date column's real values under a block of "—".
"""
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import func

SortDir = Literal["asc", "desc"]


def lower(col: Any) -> Any:
    """Case-insensitive text sort, so 'amazon' and 'Amazon' sit together. A
    blank string counts as empty (NULL), so it sorts last like the "—" cells."""
    return func.lower(func.nullif(col, ""))


def order_by(sort: str | None, dir: SortDir, whitelist: Mapping[str, Any],
             tiebreak: Sequence[Any] = ()) -> list | None:
    """ORDER BY clauses for `sort`, or None when no sort was asked for (the
    caller then keeps its own default order). A whitelist value may be a single
    expression or a tuple of them (all ordered in `dir`). `tiebreak` columns
    follow, ascending, so paging never repeats or skips a row on ties."""
    if sort is None:
        return None
    if sort not in whitelist:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}; "
                            f"expected one of {sorted(whitelist)}")
    exprs = whitelist[sort]
    if not isinstance(exprs, (tuple, list)):
        exprs = (exprs,)
    out = [(e.desc() if dir == "desc" else e.asc()).nulls_last() for e in exprs]
    return out + [t.asc() if hasattr(t, "asc") else t for t in tiebreak]


def order_sql(sort: str | None, dir: SortDir, whitelist: Mapping[str, str],
              tiebreak: str) -> str | None:
    """Raw-SQL twin of `order_by` for services that build text() queries.
    Whitelist values are SQL fragments written in this codebase, never request
    text; `dir` is a Literal already validated by FastAPI."""
    if sort is None:
        return None
    if sort not in whitelist:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}; "
                            f"expected one of {sorted(whitelist)}")
    d = "desc" if dir == "desc" else "asc"
    return f"{whitelist[sort]} {d} nulls last, {tiebreak}"



def sort_rows(rows: list, sort: str | None, dir: SortDir,
              getters: Mapping[str, Any], tiebreak: Any) -> list:
    """The same contract for a list computed in Python (groups, aggregates)
    before it is sliced into a page: whitelisted key (unknown → 422), empty
    values (None / '') last in BOTH directions, ties broken by `tiebreak(row)`
    ascending so page N+1 continues page N. `sort=None` returns `rows`
    untouched (the caller's own default order). Getters return comparable
    values; text getters should already be lower-cased."""
    if sort is None:
        return rows
    if sort not in getters:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}; "
                            f"expected one of {sorted(getters)}")
    get = getters[sort]
    base = sorted(rows, key=tiebreak)
    filled = [r for r in base if get(r) not in (None, "")]
    empty = [r for r in base if get(r) in (None, "")]
    # list.sort is stable, also with reverse=True, so equal values keep the
    # tiebreak order in both directions.
    filled.sort(key=get, reverse=(dir == "desc"))
    return filled + empty
