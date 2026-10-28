"""Query specification for list endpoints (design §13.3).

The UniOps list contract is `{items, total}`. Sort and filter keys are names a
resource declares; the repository maps them to columns, so nothing a client
sends reaches SQL as an identifier.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

from app.platform.errors import InvalidInput

T = TypeVar("T")

FilterOp = Literal["eq", "in", "contains", "range"]
MAX_PAGE_SIZE = 200


class SortKey(BaseModel):
    field: str
    desc: bool = False


class Filter(BaseModel):
    field: str
    op: FilterOp = "eq"
    # eq/contains: one value; in: a list; range: [low, high] with None for open.
    value: Any = None


class QuerySpec(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=MAX_PAGE_SIZE)
    sort: list[SortKey] = []
    search: str | None = None
    filters: list[Filter] = []
    include_historical: bool = False


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


@dataclass(frozen=True)
class FilterRule:
    """What a filterable key allows. `cast` converts each incoming value
    (query strings arrive as text) and raises ValueError on bad input."""

    column: Any
    ops: frozenset[str] = frozenset({"eq", "in"})
    cast: Callable[[Any], Any] = lambda v: v


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
        return Filter(field=field, op="in", value=[v for v in value.split("|") if v != ""])
    if op == "range":
        low, dots, high = value.partition("..")
        if not dots:
            raise InvalidInput("query.bad_filter", f"range filter {raw!r} needs low..high")
        return Filter(field=field, op="range", value=[low or None, high or None])
    if op in ("eq", "contains"):
        return Filter(field=field, op=op, value=value)
    raise InvalidInput("query.unsupported_operator", f"unknown filter operator {op!r}", {"op": op})


def query_spec(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    sort: str | None = None,
    search: str | None = None,
    filter: Annotated[list[str] | None, Query()] = None,  # noqa: A002 — the query parameter's name
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
