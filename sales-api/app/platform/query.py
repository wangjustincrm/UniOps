"""Query specification for list endpoints (design §13.3).

The UniOps list contract is `{items, total}`. Sort and filter keys are names a
resource declares; the repository maps them to columns, so nothing a client
sends reaches SQL as an identifier.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

from app.platform.errors import InvalidInput

T = TypeVar("T")

FilterOp = Literal["eq", "in", "contains", "range"]
MAX_PAGE_SIZE = 200
# Bounds on what a client can ask for: deep offsets, huge IN lists and long
# search strings are refused (422), not run.
MAX_PAGE = 10_000
MAX_IN_VALUES = 100
MAX_SEARCH = 100
MAX_FILTERS = 20
MAX_VALUE = 200


class SortKey(BaseModel):
    field: str
    desc: bool = False


class Filter(BaseModel):
    field: str
    op: FilterOp = "eq"
    # eq/contains: one value; in: a list; range: [low, high] with None for open.
    value: Any = None


class QuerySpec(BaseModel):
    page: int = Field(1, ge=1, le=MAX_PAGE)
    page_size: int = Field(50, ge=1, le=MAX_PAGE_SIZE)
    sort: list[SortKey] = Field(default_factory=list, max_length=10)
    search: str | None = Field(None, max_length=MAX_SEARCH)
    filters: list[Filter] = Field(default_factory=list, max_length=MAX_FILTERS)
    include_historical: bool = False


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


@dataclass(frozen=True)
class FilterRule:
    """What a filterable key allows. `cast` converts each incoming value
    (query strings arrive as text) and raises ValueError on bad input; left
    out, it follows the column's Python type, so "abc" for an integer column
    is a 422 here and never a database error."""

    column: Any
    ops: frozenset[str] = frozenset({"eq", "in"})
    cast: Callable[[Any], Any] | None = None

    def convert(self, value: Any) -> Any:
        return (self.cast or cast_for(self.column))(value)


def parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    raise ValueError(f"not a boolean: {v!r}")


def cast_for(column: Any) -> Callable[[Any], Any]:
    try:
        py = column.type.python_type
    except (AttributeError, NotImplementedError):
        return lambda v: v
    if py is bool:
        return parse_bool
    if py in (datetime, date):
        return lambda v: v if isinstance(v, py) else py.fromisoformat(str(v))
    if py is Decimal:
        return lambda v: Decimal(str(v))
    if py is uuid.UUID:
        return lambda v: v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    return py


def parse_sort(raw: str | None) -> list[SortKey]:
    """`code,-created_at` → [code asc, created_at desc]."""
    keys = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            keys.append(SortKey(field=part.lstrip("-"), desc=part.startswith("-")))
    return keys


def parse_filter(raw: str) -> Filter:
    """`field:op:value`; `in` takes `a|b|c`, `range` takes `low..high`
    (either side may be empty)."""
    field, sep, rest = raw.partition(":")
    op, sep2, value = rest.partition(":")
    if not sep or not sep2 or not field:
        raise InvalidInput("query.bad_filter", f"filter {raw!r} is not field:op:value")
    if op == "in":
        values = [v for v in value.split("|") if v != ""]
        if len(values) > MAX_IN_VALUES:
            raise InvalidInput("query.too_many_values", f"at most {MAX_IN_VALUES} values in one 'in' filter",
                               {"field": field, "max": MAX_IN_VALUES})
        if any(len(v) > MAX_VALUE for v in values):
            raise InvalidInput("query.value_too_long", f"a filter value is longer than {MAX_VALUE}", {"field": field})
        return Filter(field=field, op="in", value=values)
    if len(value) > MAX_VALUE:
        raise InvalidInput("query.value_too_long", f"a filter value is longer than {MAX_VALUE}", {"field": field})
    if op == "range":
        low, dots, high = value.partition("..")
        if not dots:
            raise InvalidInput("query.bad_filter", f"range filter {raw!r} needs low..high")
        return Filter(field=field, op="range", value=[low or None, high or None])
    if op in ("eq", "contains"):
        return Filter(field=field, op=op, value=value)
    raise InvalidInput("query.unsupported_operator", f"unknown filter operator {op!r}", {"op": op})


def query_spec(
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    sort: Annotated[str | None, Query(max_length=500)] = None,
    search: Annotated[str | None, Query(max_length=MAX_SEARCH)] = None,
    filter: Annotated[list[str] | None, Query(max_length=MAX_FILTERS)] = None,  # noqa: A002 — the parameter's name
    include_historical: bool = False,
) -> QuerySpec:
    """FastAPI dependency: `?page=&page_size=&sort=a,-b&search=&filter=f:op:v&filter=…`."""
    return QuerySpec(
        page=page,
        page_size=page_size,
        sort=parse_sort(sort),
        search=search,
        filters=[parse_filter(f) for f in (filter or [])],
        include_historical=include_historical,
    )
